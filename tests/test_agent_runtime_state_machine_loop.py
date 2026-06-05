from __future__ import annotations

import json
from pathlib import Path

from src.agent_runtime import LocalCodingAgent
from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    AssistantTurn,
    ModelConfig,
    ModelPricing,
    ToolCall,
    UsageStats,
)
from src.state_machine_evaluators import BudgetExhaustionEvaluator
from src.state_machine_operators import (
    DelegateAgentOperator,
    RealLLMOperator,
    ToolCallOperator,
)
from src.state_machine_runner import StateMachineRunner
from src.state_machine_validators import (
    NonEmptyContentValidator,
    ObservationShapeValidator,
)


def _make_agent(tmp_path: Path) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=ModelConfig(
            model='gpt-4o-mini',
            api_key='test-key',
            base_url='http://localhost:0/unused',
            pricing=ModelPricing(),
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=False,
            ),
        ),
    )


def _inject_runner(agent: LocalCodingAgent, log_path: Path) -> None:
    agent._sm_runner = StateMachineRunner(
        operators=[
            RealLLMOperator(agent.client),
            DelegateAgentOperator(agent._execute_delegate_agent),
            ToolCallOperator(agent.tool_registry, agent.tool_context),
        ],
        decision_log_path=log_path,
        validators=[
            ObservationShapeValidator(),
            NonEmptyContentValidator(),
        ],
        evaluators=[BudgetExhaustionEvaluator()],
    )


def _read_rationales(log_path: Path) -> list[str]:
    return [
        json.loads(line)['decision']['rationale']
        for line in log_path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


def test_flag_on_outer_loop_logs_runtime_controller_rationale_for_plain_answer(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    _inject_runner(agent, tmp_path / 'loop_plain.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)

    def fake_complete(messages, tools, *, output_schema=None, model_override=None):
        return AssistantTurn(
            content='typed hello',
            finish_reason='stop',
            usage=UsageStats(input_tokens=4, output_tokens=2),
        )

    monkeypatch.setattr(agent.client, 'complete', fake_complete)

    result = agent.run('say hello')

    assert result.final_output == 'typed hello'
    assert _read_rationales(tmp_path / 'loop_plain.jsonl') == [
        'rule_fired: runtime_query_model',
    ]


def test_outer_loop_defaults_to_state_machine_controller(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv('LATTI_USE_STATE_MACHINE', raising=False)
    monkeypatch.delenv('LATTI_USE_LEGACY_LOOP', raising=False)
    agent = _make_agent(tmp_path)
    _inject_runner(agent, tmp_path / 'loop_default.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)

    def fake_complete(messages, tools, *, output_schema=None, model_override=None):
        return AssistantTurn(
            content='default typed hello',
            finish_reason='stop',
            usage=UsageStats(input_tokens=4, output_tokens=2),
        )

    monkeypatch.setattr(agent.client, 'complete', fake_complete)

    result = agent.run('say hello')

    assert result.final_output == 'default typed hello'
    assert _read_rationales(tmp_path / 'loop_default.jsonl') == [
        'rule_fired: runtime_query_model',
    ]


def test_outer_loop_emits_decision_and_checkpoint_runtime_events(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv('LATTI_USE_STATE_MACHINE', raising=False)
    monkeypatch.delenv('LATTI_USE_LEGACY_LOOP', raising=False)
    agent = _make_agent(tmp_path)
    _inject_runner(agent, tmp_path / 'loop_events.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    captured_events: list[dict[str, object]] = []
    agent.runtime_event_sink = captured_events.append

    def fake_complete(messages, tools, *, output_schema=None, model_override=None):
        return AssistantTurn(
            content='evented typed hello',
            finish_reason='stop',
            usage=UsageStats(input_tokens=4, output_tokens=2),
        )

    monkeypatch.setattr(agent.client, 'complete', fake_complete)

    result = agent.run('say hello')

    assert result.final_output == 'evented typed hello'
    assert {
        'state_machine_decision',
        'session_checkpoint',
    }.issubset({event.get('type') for event in captured_events})
    decision_event = next(
        event for event in captured_events
        if event.get('type') == 'state_machine_decision'
    )
    assert decision_event['action_kind'] == 'llm_call'
    assert decision_event['rationale'] == 'rule_fired: runtime_query_model'
    checkpoint_event = next(
        event for event in captured_events
        if event.get('type') == 'session_checkpoint'
    )
    assert checkpoint_event['session_id'] == result.session_id
    assert checkpoint_event['typed_state_checkpointed'] is True


def test_legacy_outer_loop_escape_hatch_overrides_default(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_LEGACY_LOOP', '1')
    monkeypatch.delenv('LATTI_USE_STATE_MACHINE', raising=False)
    agent = _make_agent(tmp_path)

    assert agent._should_use_state_machine_outer_loop() is False


def test_flag_on_outer_loop_logs_runtime_controller_rationale_for_tool_turn(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    _inject_runner(agent, tmp_path / 'loop_tool.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    (tmp_path / 'note.txt').write_text('tool note', encoding='utf-8')

    turns = iter(
        [
            AssistantTurn(
                content='need a tool',
                tool_calls=(
                    ToolCall(id='call_1', name='read_file', arguments={'path': 'note.txt'}),
                ),
                finish_reason='tool_calls',
                usage=UsageStats(input_tokens=6, output_tokens=3),
            ),
            AssistantTurn(
                content='done after tool',
                finish_reason='stop',
                usage=UsageStats(input_tokens=5, output_tokens=2),
            ),
        ]
    )

    monkeypatch.setattr(
        agent.client,
        'complete',
        lambda messages, tools, *, output_schema=None, model_override=None: next(turns),
    )

    result = agent.run('read the file')

    assert result.final_output == 'done after tool'
    assert _read_rationales(tmp_path / 'loop_tool.jsonl') == [
        'rule_fired: runtime_query_model',
        'rule_fired: runtime_execute_pending_tool_call',
        'rule_fired: runtime_query_model',
    ]


def test_flag_on_outer_loop_logs_runtime_controller_rationale_for_continuation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    _inject_runner(agent, tmp_path / 'loop_continue.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)

    turns = iter(
        [
            AssistantTurn(
                content='part one ',
                finish_reason='length',
                usage=UsageStats(input_tokens=6, output_tokens=3),
            ),
            AssistantTurn(
                content='part two',
                finish_reason='stop',
                usage=UsageStats(input_tokens=5, output_tokens=2),
            ),
        ]
    )

    monkeypatch.setattr(
        agent.client,
        'complete',
        lambda messages, tools, *, output_schema=None, model_override=None: next(turns),
    )

    result = agent.run('continue if needed')

    assert result.final_output == 'part one part two'
    assert _read_rationales(tmp_path / 'loop_continue.jsonl') == [
        'rule_fired: runtime_query_model',
        'rule_fired: runtime_query_model',
    ]


# ---- evaluator telemetry (added 2026-05-02) -------------------------------

def test_evaluate_state_after_step_emits_replan_on_error_observation(tmp_path):
    """ConsecutiveErrorEvaluator should be wired and produce a 'replan' verdict
    when the last observation in state was an error. Telemetry-only today."""
    from src.agent_state_machine import State, Observation, MemoryRecord

    agent = _make_agent(tmp_path)
    # Force the runner to be constructed with the production wiring (which
    # now includes ConsecutiveErrorEvaluator).
    agent._ensure_state_machine_runner()

    err_obs = Observation(
        action_id='action-x',
        kind='error',
        payload={'error': 'simulated tool error'},
    )
    agent._sm_state = State(
        turn_id='t1',
        session_id='sm-test',
        last_observation=err_obs, budget_remaining_usd=10.0,
    )

    events = agent._evaluate_state_after_step()
    verdicts = {(e['evaluator'], e['verdict']) for e in events}
    assert ('consecutive_error', 'replan') in verdicts, verdicts


def test_evaluate_state_after_step_emits_continue_on_clean_observation(tmp_path):
    """When last observation is success (not error), ConsecutiveErrorEvaluator
    returns 'continue' — verdict appears in telemetry but caller filters."""
    from src.agent_state_machine import State, Observation

    agent = _make_agent(tmp_path)
    agent._ensure_state_machine_runner()

    ok_obs = Observation(
        action_id='action-x',
        kind='success',
        payload={'tool_name': 'read_file', 'ok': True, 'content': 'x'},
    )
    agent._sm_state = State(
        turn_id='t1',
        session_id='sm-test',
        last_observation=ok_obs, budget_remaining_usd=10.0,
    )

    events = agent._evaluate_state_after_step()
    verdicts = {(e['evaluator'], e['verdict']) for e in events}
    # ConsecutiveErrorEvaluator should be present and return 'continue'.
    assert ('consecutive_error', 'continue') in verdicts, verdicts
    # Replan must NOT fire on a clean observation.
    assert not any(v == 'replan' for _, v in verdicts), verdicts


def test_evaluate_state_after_step_no_runner_returns_empty(tmp_path):
    """When _sm_state is None, helper returns [] without crashing."""
    agent = _make_agent(tmp_path)
    # Don't construct runner; _sm_state stays None.
    events = agent._evaluate_state_after_step()
    assert events == []


def test_per_tool_eval_events_stashed_for_drain(tmp_path):
    """When _dispatch_via_state_machine processes a tool that errors, its
    evaluator verdicts must accumulate in _pending_eval_events for the LLM
    hook to drain. Otherwise sequential tools clobber the 'replan' signal."""
    from src.agent_state_machine import State, Observation
    from unittest.mock import patch
    from src.agent_types import ToolCall

    agent = _make_agent(tmp_path)
    agent._ensure_state_machine_runner()

    err_obs = Observation(
        action_id='action-x', kind='error',
        payload={'error': 'sim'},
    )
    err_state = State(
        turn_id='t-err', session_id='sm-test', last_observation=err_obs, budget_remaining_usd=10.0,
    )

    # Simulate run_one_step returning the error state
    with patch.object(agent._sm_runner, 'run_one_step',
                      return_value=(err_obs, err_state)):
        # Need a real ToolCall-shaped object; minimal stub
        class _TC:
            name = 'read_file'
            arguments = {'path': '/tmp/x'}
            id = 'tc1'
        agent._dispatch_via_state_machine(_TC())

    # The 'replan' verdict from ConsecutiveErrorEvaluator should be in the
    # stash, not lost.
    verdicts = {(e['evaluator'], e['verdict']) for e in agent._pending_eval_events}
    assert ('consecutive_error', 'replan') in verdicts, verdicts


def test_runner_evaluators_accessor_returns_wired_evaluators(tmp_path):
    """Public runner.evaluators must return the wired evaluators in
    registration order — guards against silent reorder/strip during refactor."""
    from src.state_machine_evaluators import (
        BudgetExhaustionEvaluator,
        ConsecutiveErrorEvaluator,
    )

    agent = _make_agent(tmp_path)
    runner = agent._ensure_state_machine_runner()

    evaluators = runner.evaluators
    assert isinstance(evaluators, tuple), type(evaluators)
    names = [ev.name for ev in evaluators]
    # Production wiring: BudgetExhaustionEvaluator + ConsecutiveErrorEvaluator
    # in that order. If new evaluators land, this list extends — but the two
    # must remain present and named-stable.
    assert 'budget_exhaustion' in names, names
    assert 'consecutive_error' in names, names
    # Order must match registration so the helper's index-pairing stays sound.
    assert names.index('budget_exhaustion') < names.index('consecutive_error'), names


def test_persist_session_drains_pending_eval_stash(tmp_path):
    """If a tool dispatch leaves verdicts in _pending_eval_events but the run
    terminates before an LLM-call hook drains them (e.g. terminal tool that
    ends the turn directly), _persist_session must move them into the result
    events and clear the stash. Otherwise verdicts leak across sessions."""
    from src.agent_types import AgentRunResult, UsageStats
    from src.agent_session import AgentSessionState

    agent = _make_agent(tmp_path)
    # Pre-populate stash as if a tool error left a 'replan' verdict behind.
    agent._pending_eval_events.append({
        'type': 'state_machine_evaluation',
        'evaluator': 'consecutive_error',
        'verdict': 'replan',
        'score': 1.0,
        'note': 'tool errored',
        'dimensions': {},
    })

    session = AgentSessionState(system_prompt_parts=())
    result = AgentRunResult(
        final_output='ok',
        turns=1,
        tool_calls=0,
        transcript=session.transcript(),
        events=(),
        usage=UsageStats(),
        total_cost_usd=0.0,
        stop_reason='stop',
        file_history=(),
        session_id='sm-drain-test',
        scratchpad_directory=None,
    )
    persisted = agent._persist_session(session, result)

    types = [e.get('type') for e in persisted.events]
    assert 'state_machine_evaluation' in types, types
    assert agent._pending_eval_events == [], 'stash must be cleared'


def test_persist_session_clears_stash_even_when_session_id_missing(tmp_path):
    """No-session-id branch (early-return path) must also clear the stash."""
    from src.agent_types import AgentRunResult, UsageStats
    from src.agent_session import AgentSessionState

    agent = _make_agent(tmp_path)
    agent._pending_eval_events.append({
        'type': 'state_machine_evaluation',
        'evaluator': 'consecutive_error',
        'verdict': 'replan',
        'score': 1.0,
        'note': 'leaked',
        'dimensions': {},
    })

    session = AgentSessionState(system_prompt_parts=())
    result = AgentRunResult(
        final_output='no session id',
        turns=0, tool_calls=0,
        transcript=session.transcript(),
        events=(), usage=UsageStats(), total_cost_usd=0.0,
        stop_reason='stop', file_history=(),
        session_id=None, scratchpad_directory=None,
    )
    agent._persist_session(session, result)
    assert agent._pending_eval_events == [], 'stash must be cleared on no-session-id path too'


def test_evaluate_threads_replan_into_state_runtime(tmp_path):
    """When evaluator returns 'replan', the verdict must be threaded into
    _sm_state.runtime['last_verdict'] so the next controller.pick() can
    react via the existing runtime channel."""
    from src.agent_state_machine import State, Observation

    agent = _make_agent(tmp_path)
    agent._ensure_state_machine_runner()

    err_obs = Observation(
        action_id='action-x', kind='error', payload={'error': 'sim'},
    )
    agent._sm_state = State(
        turn_id='t1', session_id='sm-thread', last_observation=err_obs, budget_remaining_usd=10.0,
    )

    agent._evaluate_state_after_step()
    assert agent._sm_state.runtime.get('last_verdict') == 'replan', \
        agent._sm_state.runtime


def test_evaluate_threads_continue_for_one_shot_consumption(tmp_path):
    """Verdicts are one-shot. After a 'replan' has driven a State-layer
    response (e.g. injected reminder via RuntimeLoopController), the next
    successful step must OVERWRITE last_verdict with 'continue' so the
    turn after that does not re-inject. Pre-fix: 'continue' was filtered
    and a single 'replan' would persist forever, re-injecting every
    subsequent turn. New contract: every winning_verdict is threaded —
    including 'continue' — so verdict-driven controller behavior is
    one-shot.
    """
    from src.agent_state_machine import State, Observation

    agent = _make_agent(tmp_path)
    agent._ensure_state_machine_runner()

    ok_obs = Observation(
        action_id='action-x', kind='success',
        payload={'tool_name': 'read_file', 'ok': True, 'content': 'x'},
    )
    agent._sm_state = State(
        turn_id='t1', session_id='sm-thread', last_observation=ok_obs, budget_remaining_usd=10.0,
        runtime={'last_verdict': 'replan'},
    )

    agent._evaluate_state_after_step()
    # 'continue' overwrites the prior 'replan' — one-shot consumption.
    assert agent._sm_state.runtime.get('last_verdict') == 'continue', \
        agent._sm_state.runtime


def test_evaluate_precedence_escalate_beats_replan(tmp_path):
    """If two evaluators fire with different verdicts, the most-terminal
    verdict wins on state.runtime. Verifies precedence ordering."""
    from src.agent_state_machine import State, Observation, EvaluationResult
    from src.state_machine_evaluators import ConsecutiveErrorEvaluator

    class _AlwaysEscalate:
        @property
        def name(self) -> str: return 'always_escalate'
        def evaluate(self, state, goal=None):
            return EvaluationResult(
                task_id='no_goal', score=1.0, verdict='escalate',
                note='forced',
            )

    agent = _make_agent(tmp_path)
    runner = agent._ensure_state_machine_runner()
    # Inject a forced-escalate evaluator alongside the wired ones.
    runner._evaluators = runner._evaluators + (_AlwaysEscalate(),)

    err_obs = Observation(
        action_id='action-x', kind='error', payload={'error': 'sim'},
    )
    agent._sm_state = State(
        turn_id='t1', session_id='sm-thread', last_observation=err_obs, budget_remaining_usd=10.0,
    )

    agent._evaluate_state_after_step()
    # 'replan' from ConsecutiveErrorEvaluator + 'escalate' from injection;
    # escalate has higher precedence so it wins.
    assert agent._sm_state.runtime.get('last_verdict') == 'escalate', \
        agent._sm_state.runtime


def test_bind_state_machine_session_uses_runtime_budget_cap(tmp_path):
    """When runtime_config.budget_config.max_total_cost_usd is set, the
    fresh state should carry that cap in budget_remaining_usd — not
    hardcoded 0.0 (which would make BudgetExhaustionEvaluator falsely
    fire 'timeout' on every session start)."""
    from src.agent_types import (
        AgentPermissions, AgentRuntimeConfig, BudgetConfig,
        ModelConfig, ModelPricing,
    )

    agent = LocalCodingAgent(
        model_config=ModelConfig(
            model='gpt-4o-mini', api_key='test', base_url='http://localhost:0/unused',
            pricing=ModelPricing(),
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            permissions=AgentPermissions(allow_file_write=True, allow_shell_commands=False),
            budget_config=BudgetConfig(max_total_cost_usd=2.50),
        ),
    )
    agent._bind_state_machine_session('sm-budget-test')
    assert agent._sm_state.budget_remaining_usd == 2.50, agent._sm_state.budget_remaining_usd


def test_bind_state_machine_session_uses_inf_when_no_budget_cap(tmp_path):
    """When budget cap is None (default), fresh state should carry inf so
    BudgetExhaustionEvaluator doesn't fire 'timeout' on the first eval."""
    agent = _make_agent(tmp_path)
    agent._bind_state_machine_session('sm-inf-test')
    import math
    assert math.isinf(agent._sm_state.budget_remaining_usd), \
        agent._sm_state.budget_remaining_usd

    # Verify BudgetExhaustionEvaluator does NOT fire 'timeout' on this state.
    runner = agent._ensure_state_machine_runner()
    results = runner.evaluate(agent._sm_state, goal=None)
    budget_results = [r for r in results
                      if r.note in ('budget OK', 'budget depleted')]
    assert all(r.verdict == 'continue' for r in budget_results), \
        [(r.verdict, r.note) for r in budget_results]
