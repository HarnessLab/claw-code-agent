"""Tests that agent_runtime exposes typed memory/goals/tasks surfaces."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_state_machine import Goal, MemoryRecord, State, Task
from src.agent_types import AgentRunResult
from src.agent_types import (
    AgentPermissions, AgentRuntimeConfig, ModelConfig, ModelPricing,
)
from src.session_store import StoredAgentSession
from src.state_machine_goals import GoalRegistry, TaskTracker
from src.state_machine_memory import LattiMemoryStore


def _make_agent(tmp_path):
    return LocalCodingAgent(
        model_config=ModelConfig(
            model='unused', api_key='x', base_url='http://0/',
            pricing=ModelPricing(),
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            permissions=AgentPermissions(allow_file_write=True, allow_shell_commands=False),
        ),
    )


def test_state_machine_memory_returns_store(tmp_path):
    agent = _make_agent(tmp_path)
    store = agent.state_machine_memory()
    # Even if ~/.latti is missing, the store can be constructed (creates dir)
    assert isinstance(store, LattiMemoryStore)


def test_state_machine_memory_is_cached(tmp_path):
    agent = _make_agent(tmp_path)
    a = agent.state_machine_memory()
    b = agent.state_machine_memory()
    assert a is b


def test_state_machine_goals_returns_registry(tmp_path):
    agent = _make_agent(tmp_path)
    reg = agent.state_machine_goals()
    assert isinstance(reg, GoalRegistry)


def test_state_machine_tasks_returns_tracker(tmp_path):
    agent = _make_agent(tmp_path)
    tracker = agent.state_machine_tasks()
    assert isinstance(tracker, TaskTracker)


def test_lazy_construction_does_not_fire_at_init(tmp_path):
    agent = _make_agent(tmp_path)
    # Direct field check: nothing constructed yet
    assert agent._sm_memory is None
    assert agent._sm_goals is None
    assert agent._sm_tasks is None


def test_run_rebinds_typed_state_before_prompt_execution(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    agent._sm_state = State.fresh(session_id='stale_session', available_tools=('old_tool',))
    seen: dict[str, object] = {}

    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        seen['prompt'] = prompt
        seen['state'] = agent._sm_state
        return AgentRunResult(
            final_output='ok',
            turns=0,
            tool_calls=0,
            transcript=(),
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )

    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    result = agent.run('hello from test')

    assert result.session_id is not None
    assert seen['prompt'] == 'hello from test'
    assert isinstance(seen['state'], State)
    assert seen['state'].session_id == result.session_id
    assert seen['state'].session_id != 'stale_session'
    assert 'read_file' in seen['state'].available_tools


def test_resume_rebinds_typed_state_before_prompt_execution(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    agent._sm_state = State.fresh(session_id='stale_session', available_tools=('old_tool',))
    seen: dict[str, object] = {}

    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        seen['prompt'] = prompt
        seen['state'] = agent._sm_state
        seen['base_session'] = base_session
        return AgentRunResult(
            final_output='ok',
            turns=0,
            tool_calls=0,
            transcript=(),
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )

    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    stored = StoredAgentSession(
        session_id='stored_session_123',
        model_config={},
        runtime_config={},
        system_prompt_parts=('system',),
        user_context={},
        system_context={},
        messages=(),
        turns=0,
        tool_calls=0,
        usage={},
        total_cost_usd=0.0,
        file_history=(),
        budget_state={},
        plugin_state={},
        scratchpad_directory=None,
    )

    result = agent.resume('continue', stored)

    assert result.session_id == 'stored_session_123'
    assert seen['prompt'] == 'continue'
    assert seen['base_session'] is not None
    assert isinstance(seen['state'], State)
    assert seen['state'].session_id == 'stored_session_123'
    assert seen['state'].session_id != 'stale_session'
    assert 'read_file' in seen['state'].available_tools
