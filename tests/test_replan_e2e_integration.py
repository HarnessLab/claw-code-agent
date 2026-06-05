"""(c) End-to-end: forced-error → replan threading → reminder in next LLM call.

Drives the full chain in one process:
  Turn 1: fake LLM returns a tool_call that fails
  Tool result: error observation
  Evaluator: ConsecutiveErrorEvaluator returns 'replan'
  Threading: _evaluate_state_after_step writes last_verdict='replan'
             AND last_error_text into _sm_state.runtime
  Turn 2: RuntimeLoopController reads runtime, builds payload with
          State-layer reminder appended (containing the actual error)
  Captured: turn 2's messages payload

Captures the messages passed to client.complete on each call and
asserts the State-layer reminder appeared in turn 2 — including the
specific error text from turn 1's failure.

This is the verification the curl-level tests couldn't do: the
production trigger path firing in real code, not just the synthesized
payload.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_session import AgentMessage
from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    AssistantTurn,
    ModelConfig,
    ModelPricing,
    ToolCall,
    UsageStats,
)
from src.state_machine_evaluators import (
    BudgetExhaustionEvaluator,
    ConsecutiveErrorEvaluator,
)
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


def _inject_runner_with_error_evaluator(agent: LocalCodingAgent, log_path: Path) -> None:
    """Same as production wiring (BudgetExhaustion + ConsecutiveError)
    so the 'replan' verdict will actually fire on error observations.
    """
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
        evaluators=[
            BudgetExhaustionEvaluator(),
            ConsecutiveErrorEvaluator(),
        ],
    )


def test_replan_reminder_appears_in_next_llm_call_after_tool_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    _inject_runner_with_error_evaluator(agent, tmp_path / 'replan_e2e.jsonl')
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    # Pre-existing baseline bug from commit c81dc2b: agent.run() calls
    # self._inject_next_priority() which doesn't exist on LocalCodingAgent.
    # Patch as a no-op so this test validates THIS wire, not the baseline bug.
    monkeypatch.setattr(
        agent, '_inject_next_priority',
        lambda: None, raising=False,
    )

    # Turn 1: model emits a read_file tool_call against a non-existent
    # path. ToolCallOperator will produce an error observation.
    # Turn 2: model emits a plain answer.
    turns = iter(
        [
            AssistantTurn(
                content='let me read the config',
                tool_calls=(
                    ToolCall(
                        id='call_err_1',
                        name='read_file',
                        arguments={'path': str(tmp_path / 'does-not-exist.yaml')},
                    ),
                ),
                finish_reason='tool_calls',
                usage=UsageStats(input_tokens=6, output_tokens=3),
            ),
            AssistantTurn(
                content='cannot proceed without the file',
                finish_reason='stop',
                usage=UsageStats(input_tokens=5, output_tokens=4),
            ),
        ]
    )

    captured_calls: list[list[dict]] = []

    def _capture_complete(messages, tools, *, output_schema=None, model_override=None):
        # Deep copy the messages we received — caller may mutate them
        # downstream and we want the snapshot at call time.
        captured_calls.append(list(messages))
        return next(turns)

    monkeypatch.setattr(agent.client, 'complete', _capture_complete)

    result = agent.run('load the config')

    assert result.final_output == 'cannot proceed without the file', \
        f'unexpected final_output: {result.final_output!r}'
    assert len(captured_calls) >= 2, \
        f'expected at least 2 LLM calls; got {len(captured_calls)}'

    # The second LLM call's messages must contain the State-layer reminder.
    second_call_text = '\n'.join(
        m.get('content', '') if isinstance(m.get('content'), str) else ''
        for m in captured_calls[1]
    )
    assert 'STATE-LAYER NOTICE' in second_call_text, \
        f'replan reminder missing from turn-2 LLM payload. ' \
        f'Messages: {[(m.get("role"), str(m.get("content"))[:80]) for m in captured_calls[1]]}'
    assert 'verdict=replan' in second_call_text, \
        f'replan verdict tag missing'

    # The reminder should also include some signal from the actual error
    # (file-not-found, ENOENT, missing, etc. — exact text depends on
    # the read_file tool's error format).
    error_signals = ['not found', 'enoent', 'no such file', 'does-not-exist', 'specific failure']
    has_error_signal = any(s in second_call_text.lower() for s in error_signals)
    assert has_error_signal, \
        f'reminder did not include any specific-failure signal. ' \
        f'Looked for {error_signals} in turn-2 text.'
