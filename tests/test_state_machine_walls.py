"""Tests that constitutional walls block actions BEFORE operator dispatch.

Step 5.10 of the runway in ``~/.latti/STATE_MACHINE.md``: walls are hard-coded
gates the LLM cannot decide. The runner must check them before invoking any
Operator so a blocked action has no side effect.
"""
from __future__ import annotations

import json

import pytest

from src.agent_state_machine import Action, Observation, State
from src.state_machine_runner import StateMachineRunner


class _RecordingOperator:
    """Operator that records every execute() invocation. Tests can assert it
    was NEVER called when a wall blocked the action."""

    def __init__(self, action_kind='tool_call'):
        self._kind = action_kind
        self.invocations: list[Action] = []

    @property
    def kind(self):
        return self._kind

    def can_handle(self, action):
        return action.kind == self._kind

    def execute(self, action, state):
        self.invocations.append(action)
        return Observation(action_id=action.id, kind='success',
                           payload={'tool_name': 'whatever', 'ok': True, 'content': 'ran'})


@pytest.fixture
def fresh_state():
    return State.fresh(session_id='wall_test', budget_usd=1.0)


def test_force_push_main_blocks_before_operator_executes(fresh_state, tmp_path):
    op = _RecordingOperator()
    runner = StateMachineRunner(operators=[op], decision_log_path=tmp_path / 'log.jsonl')
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'git push -f origin main'},
    })
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert obs.payload['blocked'] is True
    assert obs.payload['wall'] == 'never_force_push_main'
    # The operator was NEVER called — wall blocked dispatch.
    assert op.invocations == []


def test_secret_in_payload_blocks_before_operator_executes(fresh_state, tmp_path):
    op = _RecordingOperator(action_kind='llm_call')
    runner = StateMachineRunner(operators=[op], decision_log_path=tmp_path / 'log.jsonl')
    a = Action(kind='llm_call', payload={
        'messages': [{'role': 'user', 'content': 'leak my sk-ant-XXXXXXXXabcdefghij'}],
    })
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert obs.payload['wall'] == 'never_commit_secrets'
    assert op.invocations == []


def test_rm_rf_etc_blocks(fresh_state, tmp_path):
    op = _RecordingOperator()
    runner = StateMachineRunner(operators=[op], decision_log_path=tmp_path / 'log.jsonl')
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'rm -rf /etc/passwd'},
    })
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert obs.payload['wall'] == 'never_delete_production_data'
    assert op.invocations == []


def test_safe_action_passes_through_to_operator(fresh_state, tmp_path):
    op = _RecordingOperator()
    runner = StateMachineRunner(operators=[op], decision_log_path=tmp_path / 'log.jsonl')
    a = Action(kind='tool_call', payload={
        'tool_name': 'read_file', 'arguments': {'path': '/tmp/safe.txt'},
    })
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'success'
    assert len(op.invocations) == 1


def test_wall_block_logged_to_decision_log(fresh_state, tmp_path):
    op = _RecordingOperator()
    log_path = tmp_path / 'log.jsonl'
    runner = StateMachineRunner(operators=[op], decision_log_path=log_path)
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'rm -rf /var/log'},
    })
    runner.run_one_step(fresh_state, a)
    rec = json.loads(log_path.read_text().strip())
    assert 'wall_blocked: never_delete_production_data' in rec['decision']['rationale']
    assert rec['observation_kind'] == 'error'


def test_wall_block_advances_state(fresh_state, tmp_path):
    """Even a blocked action advances the State turn (the loop walks)."""
    op = _RecordingOperator()
    runner = StateMachineRunner(operators=[op], decision_log_path=tmp_path / 'log.jsonl')
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'git push --force main'},
    })
    _, new_state = runner.run_one_step(fresh_state, a)
    assert new_state.turn_id != fresh_state.turn_id
