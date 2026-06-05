"""Tests for the priority-build wiring:

1. _maybe_save_scar fires on the LLM-call dispatch path (not just tool_call)
2. agent.run(prompt) registers a Goal in GoalRegistry
"""
from __future__ import annotations

import json

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_state_machine import Action, Observation, State, ValidationResult, ValidationCheck
from src.agent_types import (
    AgentPermissions, AgentRuntimeConfig, AgentRunResult, ModelConfig, ModelPricing,
)
from src.state_machine_goals import GoalRegistry
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


# ---- Step A: LLM-call scar auto-save ---------------------------------------

def test_llm_call_blocking_validation_persists_scar(tmp_path):
    """A wall-blocked LLM-call action saves a scar via _maybe_save_scar.

    We exercise _maybe_save_scar directly with a synthesized blocking
    observation, which is the same code path the LLM-call sites now hit.
    """
    agent = _make_agent(tmp_path)
    agent._sm_state = State.fresh(session_id='llm_scar_test')
    mem_dir = tmp_path / 'memory'
    agent._sm_memory = LattiMemoryStore(mem_dir)

    action = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    bad_validation = ValidationResult(
        action_id=action.id, passed=False,
        checks=(ValidationCheck(name='llm_call_has_completion', passed=False,
                                evidence='missing completion key'),),
        severity='block',
    )
    obs = Observation(
        action_id=action.id, kind='error',
        payload={
            'error': 'blocked by validator',
            'blocking_validations': [bad_validation.to_dict()],
        },
    )

    agent._maybe_save_scar(action, obs)

    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) >= 1
    body = scar_files[0].read_text()
    assert 'llm_call' in body
    assert 'llm_call_has_completion' in body or 'FAILED CHECKS' in body


def test_llm_call_wall_block_persists_scar(tmp_path):
    """A constitutional wall block on an LLM-call action also persists a scar."""
    agent = _make_agent(tmp_path)
    agent._sm_state = State.fresh(session_id='llm_wall_test')
    mem_dir = tmp_path / 'memory'
    agent._sm_memory = LattiMemoryStore(mem_dir)

    action = Action(kind='llm_call', payload={
        'messages': [{'role': 'user', 'content': 'leak this: sk-ant-XXXXXabcdefghij'}],
    })
    obs = Observation(
        action_id=action.id, kind='error',
        payload={
            'error': 'constitutional wall violated: never_commit_secrets',
            'wall': 'never_commit_secrets',
            'blocked': True,
        },
    )

    agent._maybe_save_scar(action, obs)

    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) >= 1
    body = scar_files[0].read_text()
    assert 'never_commit_secrets' in body


# ---- Step B: Goal registration on run() ------------------------------------

def test_run_registers_goal_with_prompt_title(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)

    # Avoid hitting real model — short-circuit _run_prompt
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        return AgentRunResult(
            final_output='ok', turns=0, tool_calls=0, transcript=(),
            session_id=session_id, scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )
    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    # Redirect goals storage to tmp
    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)

    agent.run('Build a typed loop for the agent')

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].title == 'Build a typed loop for the agent'
    assert 'Build a typed loop' in goals[0].success_criteria[0]
    assert goals[0].owner == 'user'


def test_run_does_not_register_goal_for_empty_prompt(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)
    monkeypatch.setattr(agent, '_run_prompt', lambda *a, **kw: AgentRunResult(
        final_output='', turns=0, tool_calls=0, transcript=(), session_id='x', scratchpad_directory=None,
    ))

    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)
    agent.run('   ')
    assert agent._sm_goals.list_all() == []


def test_run_with_state_machine_disabled_does_not_register(tmp_path, monkeypatch):
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '0')
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)
    monkeypatch.setattr(agent, '_run_prompt', lambda *a, **kw: AgentRunResult(
        final_output='', turns=0, tool_calls=0, transcript=(), session_id='x', scratchpad_directory=None,
    ))

    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)
    agent.run('something')
    assert agent._sm_goals.list_all() == []


def test_long_prompt_truncates_to_80_chars_in_title(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)
    monkeypatch.setattr(agent, '_run_prompt', lambda *a, **kw: AgentRunResult(
        final_output='', turns=0, tool_calls=0, transcript=(), session_id='x', scratchpad_directory=None,
    ))
    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)

    long_prompt = 'A' * 200
    agent.run(long_prompt)

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert len(goals[0].title) == 80
