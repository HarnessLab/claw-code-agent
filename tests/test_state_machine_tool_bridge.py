"""Tests for the bridge between StateMachineRunner and the real tool registry.

Step 2a of the runway in ``~/.latti/STATE_MACHINE.md``: prove a real tool
(read_file, write_file) flows through the typed loop end-to-end against the
actual claw-code-agent tool registry. This is the prerequisite for step 2b
(the flag-gated branch in agent_runtime.py).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.agent_state_machine import Action, State
from src.agent_tools import build_tool_context, default_tool_registry
from src.agent_types import AgentRuntimeConfig, AgentPermissions
from src.state_machine_operators import ToolCallOperator
from src.state_machine_runner import StateMachineRunner


@pytest.fixture
def real_runner(tmp_path):
    registry = default_tool_registry()
    config = AgentRuntimeConfig(
        cwd=tmp_path,
        permissions=AgentPermissions(allow_file_write=True, allow_shell_commands=False),
    )
    context = build_tool_context(config, tool_registry=registry)
    log_path = tmp_path / 'policy_decisions.jsonl'
    runner = StateMachineRunner(
        operators=[ToolCallOperator(registry, context)],
        decision_log_path=log_path,
    )
    state = State.fresh(session_id='bridge_test', budget_usd=1.0,
                        available_tools=tuple(registry.keys()))
    return runner, state, log_path, tmp_path


def test_real_read_file_via_bridge(real_runner):
    runner, state, _, tmp_path = real_runner
    target = tmp_path / 'note.txt'
    target.write_text('bridge works', encoding='utf-8')

    action = Action(kind='tool_call', payload={
        'tool_name': 'read_file',
        'arguments': {'path': 'note.txt'},
    })
    obs, new_state = runner.run_one_step(state, action, rationale='real read_file')

    assert obs.kind == 'success'
    assert obs.payload['ok'] is True
    assert 'bridge works' in obs.payload['content']
    assert obs.payload['tool_name'] == 'read_file'
    assert new_state.turn_id != state.turn_id


def test_real_write_file_via_bridge(real_runner):
    runner, state, _, tmp_path = real_runner
    action = Action(kind='tool_call', payload={
        'tool_name': 'write_file',
        'arguments': {'path': 'created.txt', 'content': 'made via bridge\n'},
    })
    obs, _ = runner.run_one_step(state, action)

    assert obs.kind == 'success'
    written = (tmp_path / 'created.txt').read_text()
    assert written == 'made via bridge\n'


def test_real_unknown_tool_returns_error(real_runner):
    runner, state, _, _ = real_runner
    action = Action(kind='tool_call', payload={
        'tool_name': 'this_tool_does_not_exist',
        'arguments': {},
    })
    obs, new_state = runner.run_one_step(state, action)

    assert obs.kind == 'error'
    # State machine still walks
    assert new_state.turn_id != state.turn_id


def test_can_handle_only_matches_known_registry_entries(real_runner):
    runner, _, _, _ = real_runner
    op = runner.operators[0]
    assert op.can_handle(Action(kind='tool_call', payload={'tool_name': 'read_file'}))
    assert not op.can_handle(Action(kind='tool_call', payload={'tool_name': 'nope'}))
    assert not op.can_handle(Action(kind='llm_call', payload={'tool_name': 'read_file'}))


def test_decision_log_records_tool_dispatch(real_runner):
    runner, state, log_path, tmp_path = real_runner
    target = tmp_path / 'logged.txt'
    target.write_text('x', encoding='utf-8')
    action = Action(kind='tool_call', payload={
        'tool_name': 'read_file',
        'arguments': {'path': 'logged.txt'},
    })
    runner.run_one_step(state, action, rationale='log this dispatch')
    line = log_path.read_text().strip()
    rec = json.loads(line)
    assert rec['decision']['rationale'] == 'log this dispatch'
    assert rec['decision']['chose']['payload']['tool_name'] == 'read_file'
    assert rec['observation_kind'] == 'success'


def test_read_missing_file_returns_error_observation(real_runner):
    runner, state, _, _ = real_runner
    action = Action(kind='tool_call', payload={
        'tool_name': 'read_file',
        'arguments': {'path': 'does_not_exist.txt'},
    })
    obs, _ = runner.run_one_step(state, action)
    # Whatever the underlying tool's error mode, the bridge must surface it
    # as kind='error' — the runner still walks.
    assert obs.kind == 'error'
    assert obs.payload['ok'] is False
