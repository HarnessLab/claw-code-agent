"""Tests for the state-machine runner + operator dispatch.

Backs the design in ``~/.latti/STATE_MACHINE.md`` step 1 (thin runtime slice).
Verifies real Operators move typed Actions through the runner end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent_state_machine import Action, Observation, State
from src.state_machine_operators import (
    EchoLLMOperator,
    JSONSchemaValidator,
    ReadFileOperator,
)
from src.state_machine_runner import (
    DEFAULT_DECISION_LOG,
    NoOperatorError,
    StateMachineRunner,
)


@pytest.fixture
def fresh_state():
    return State.fresh(session_id='test_sess', budget_usd=1.0,
                       available_tools=('read_file', 'llm_call'))


@pytest.fixture
def runner_no_log(tmp_path):
    """Runner that writes decision log to a temp file, never to ~/.latti."""
    log_path = tmp_path / 'policy_decisions.jsonl'
    return StateMachineRunner(
        operators=[ReadFileOperator(), JSONSchemaValidator(), EchoLLMOperator()],
        decision_log_path=log_path,
    ), log_path


def test_read_file_operator_returns_success_for_existing_file(runner_no_log, fresh_state, tmp_path):
    runner, _ = runner_no_log
    target = tmp_path / 'hello.txt'
    target.write_text('hi from latti', encoding='utf-8')

    action = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(target)})
    obs, new_state = runner.run_one_step(fresh_state, action)

    assert obs.kind == 'success'
    assert obs.payload['content'] == 'hi from latti'
    assert obs.payload['truncated'] is False
    assert new_state.turn_id != fresh_state.turn_id
    assert new_state.last_observation is obs


def test_read_file_operator_returns_error_for_missing_file(runner_no_log, fresh_state, tmp_path):
    runner, _ = runner_no_log
    missing = tmp_path / 'nope.txt'
    action = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(missing)})
    obs, new_state = runner.run_one_step(fresh_state, action)

    # State machine still walks — error observation, never raises
    assert obs.kind == 'error'
    assert 'file not found' in obs.payload['error']
    assert new_state.turn_id != fresh_state.turn_id


def test_runner_returns_error_observation_for_unhandleable_action(runner_no_log, fresh_state):
    runner, _ = runner_no_log
    # 'wait' action — no registered operator handles it
    action = Action(kind='wait', payload={'duration_s': 3})
    obs, new_state = runner.run_one_step(fresh_state, action)

    assert obs.kind == 'error'
    assert 'no operator' in obs.payload['error']
    assert obs.payload['unhandled_action_kind'] == 'wait'
    # State still advances — loop never crashes on unknown action
    assert new_state.turn_id != fresh_state.turn_id


def test_decision_log_appends_one_line_per_call(runner_no_log, fresh_state, tmp_path):
    runner, log_path = runner_no_log
    target = tmp_path / 'a.txt'
    target.write_text('A')
    a1 = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(target)})
    a2 = Action(kind='llm_call', payload={'prompt': 'hello'})

    runner.run_one_step(fresh_state, a1, rationale='read first')
    runner.run_one_step(fresh_state, a2, rationale='echo second')

    lines = log_path.read_text().strip().split('\n')
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1['decision']['rationale'] == 'read first'
    assert rec2['decision']['rationale'] == 'echo second'
    assert rec1['session_id'] == 'test_sess'
    assert rec1['observation_kind'] == 'success'
    assert rec1['decision']['chose']['kind'] == 'tool_call'
    assert rec2['decision']['chose']['kind'] == 'llm_call'


def test_state_turn_id_advances_and_budget_decrements(runner_no_log, fresh_state, tmp_path):
    runner, _ = runner_no_log
    target = tmp_path / 'b.txt'
    target.write_text('B')
    action = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(target)})

    obs, s1 = runner.run_one_step(fresh_state, action)
    assert s1.turn_id != fresh_state.turn_id
    # ReadFileOperator returns cost_usd=0.0 by default, so budget unchanged
    assert s1.budget_remaining_usd == fresh_state.budget_remaining_usd

    # Same fresh state again, but feed an Observation with cost_usd > 0 manually
    obs_with_cost = Observation(action_id=action.id, kind='success', payload={}, cost_usd=0.25)
    s2 = fresh_state.next_turn(obs_with_cost, budget_decrement_usd=0.25)
    assert abs(s2.budget_remaining_usd - 0.75) < 1e-9


def test_dispatch_picks_correct_operator_among_multiple(runner_no_log, fresh_state, tmp_path):
    runner, _ = runner_no_log
    # tool_call goes to ReadFileOperator
    target = tmp_path / 'c.txt'
    target.write_text('C')
    a_tool = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(target)})
    obs_tool, _ = runner.run_one_step(fresh_state, a_tool)
    assert obs_tool.kind == 'success'
    assert obs_tool.payload['content'] == 'C'

    # llm_call goes to EchoLLMOperator
    a_llm = Action(kind='llm_call', payload={'prompt': 'ping'})
    obs_llm, _ = runner.run_one_step(fresh_state, a_llm)
    assert obs_llm.kind == 'success'
    assert obs_llm.payload['completion'] == 'echo: ping'
    assert obs_llm.payload['is_stub'] is True

    # validation goes to JSONSchemaValidator
    a_val = Action(kind='validation', payload={
        'value': {'name': 'x'}, 'required_keys': ['name'],
    })
    obs_val, _ = runner.run_one_step(fresh_state, a_val)
    assert obs_val.kind == 'success'
    assert obs_val.payload['validation']['passed'] is True


def test_validator_blocks_on_missing_required_key(runner_no_log, fresh_state):
    runner, _ = runner_no_log
    a = Action(kind='validation', payload={
        'value': {'foo': 1},
        'required_keys': ['name', 'id'],
    })
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert obs.payload['validation']['severity'] == 'block'
    assert obs.payload['validation']['passed'] is False
    failing = [c for c in obs.payload['validation']['checks'] if not c['passed']]
    assert any('required:name' in c['name'] for c in failing)


def test_runner_requires_at_least_one_operator():
    with pytest.raises(ValueError, match='at least one Operator'):
        StateMachineRunner(operators=[])


def test_default_decision_log_path_is_under_latti_memory():
    # Sanity: the default points at the latti substrate, not somewhere else.
    assert DEFAULT_DECISION_LOG == Path.home() / '.latti' / 'memory' / 'policy_decisions.jsonl'


def test_pick_raises_no_operator_error_directly():
    runner = StateMachineRunner(operators=[ReadFileOperator()], decision_log_path=None)
    a = Action(kind='ask_user', payload={'q': 'really?'})
    with pytest.raises(NoOperatorError):
        runner.pick(a)
