"""Tests for Goal.status field + GoalRegistry.mark_done lifecycle.

Adds completion-marking to typed Goals so registered goals can actually
close. agent.run(prompt) registers a Goal at start; on clean completion,
_mark_goal_done appends a status='done' line to the journal.
"""
from __future__ import annotations

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_state_machine import Goal
from src.agent_types import (
    AgentPermissions, AgentRuntimeConfig, AgentRunResult, ModelConfig, ModelPricing,
)
from src.state_machine_goals import GoalRegistry


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


# ---- Goal dataclass status field ------------------------------------------

def test_goal_status_default_is_active():
    g = Goal.new(title='something to do')
    assert g.status == 'active'
    assert g.completed_at is None


def test_goal_status_serializes_in_to_dict():
    g = Goal.new(title='x')
    d = g.to_dict()
    assert d['status'] == 'active'
    assert d['completed_at'] is None


# ---- GoalRegistry.mark_done semantics --------------------------------------

def test_mark_done_appends_status_line(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = reg.register(Goal.new(title='build typed loop'))
    updated = reg.mark_done(g.id)

    assert updated is not None
    assert updated.status == 'done'
    assert updated.completed_at is not None

    # Two lines on disk now: register + done
    lines = reg.goals_path.read_text().splitlines()
    assert len(lines) == 2


def test_list_all_returns_latest_status_after_mark_done(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = reg.register(Goal.new(title='will be done'))
    reg.mark_done(g.id)

    fresh = reg.list_all()
    assert len(fresh) == 1
    assert fresh[0].status == 'done'


def test_mark_done_unknown_id_returns_none(tmp_path):
    reg = GoalRegistry(tmp_path)
    assert reg.mark_done('goal_nonexistent') is None


def test_mark_abandoned_sets_status(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = reg.register(Goal.new(title='dropping this'))
    updated = reg.mark_abandoned(g.id)
    assert updated.status == 'abandoned'
    # abandoned doesn't auto-set completed_at
    assert updated.completed_at is None


def test_history_returns_all_status_transitions(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = reg.register(Goal.new(title='trace me'))
    reg.mark_done(g.id)
    reg.mark_abandoned(g.id)  # weird transition but valid as audit history

    history = reg.history(g.id)
    statuses = [h.status for h in history]
    assert statuses == ['active', 'done', 'abandoned']


def test_list_active_excludes_done_and_abandoned(tmp_path):
    reg = GoalRegistry(tmp_path)
    g1 = reg.register(Goal.new(title='active one'))
    g2 = reg.register(Goal.new(title='will be done'))
    g3 = reg.register(Goal.new(title='will be abandoned'))
    reg.mark_done(g2.id)
    reg.mark_abandoned(g3.id)

    active = reg.list_active()
    active_titles = {g.title for g in active}
    assert active_titles == {'active one'}


# ---- agent.run end-to-end Goal completion ----------------------------------

def test_run_marks_registered_goal_as_done_on_clean_completion(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        return AgentRunResult(
            final_output='ok', turns=0, tool_calls=0, transcript=(),
            stop_reason='end_turn',  # not 'error'
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )
    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)

    agent.run('Test prompt for goal lifecycle')

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].status == 'done'
    assert goals[0].completed_at is not None


def test_run_does_not_mark_done_if_stop_reason_is_error(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        return AgentRunResult(
            final_output='', turns=0, tool_calls=0, transcript=(),
            stop_reason='error',  # error → goal stays active
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )
    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)

    agent.run('Erroring prompt')

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].status == 'active'  # NOT marked done because stop_reason='error'


@pytest.mark.parametrize('bad_stop', ['error', 'backend_error', 'budget_exceeded',
                                       'max_turns', 'max_tool_calls', 'max_model_calls'])
def test_run_does_not_mark_done_on_failure_class_stop_reasons(tmp_path, monkeypatch, bad_stop):
    """A run that exits via budget/timeout/backend failure must NOT close the
    Goal as done — the work didn't actually finish."""
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        return AgentRunResult(
            final_output='', turns=0, tool_calls=0, transcript=(),
            stop_reason=bad_stop,
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )
    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    goals_dir = tmp_path / 'goals'
    agent._sm_goals = GoalRegistry(goals_dir)

    agent.run(f'Run that will exit via {bad_stop}')
    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].status == 'active', (
        f'stop_reason={bad_stop!r} should NOT mark goal done'
    )


def test_run_marks_done_on_stop_class_clean_outcomes(tmp_path, monkeypatch):
    """Verify the positive side of the exclusion: end_turn / stop / tool_calls
    are clean outcomes that DO close the Goal."""
    for clean_stop in ('end_turn', 'stop', 'tool_calls'):
        agent = _make_agent(tmp_path)
        monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)
        monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
        monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

        def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history, _stop=clean_stop):
            return AgentRunResult(
                final_output='ok', turns=1, tool_calls=0, transcript=(),
                stop_reason=_stop, session_id=session_id,
                scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
            )
        monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

        goals_dir = tmp_path / f'goals_{clean_stop}'
        agent._sm_goals = GoalRegistry(goals_dir)
        agent.run(f'Clean run with {clean_stop}')

        goals = agent._sm_goals.list_all()
        assert len(goals) == 1
        assert goals[0].status == 'done', f'stop_reason={clean_stop!r} should mark goal done'


def test_resume_registers_goal_with_prompt_title(tmp_path, monkeypatch):
    """Symmetric with agent.run: agent.resume(prompt, stored) also registers
    a Goal whose title is the prompt's first 80 chars."""
    from src.session_store import StoredAgentSession
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)
    monkeypatch.setattr(agent, '_run_prompt', lambda *a, **kw: AgentRunResult(
        final_output='ok', turns=0, tool_calls=0, transcript=(),
        stop_reason='end_turn', session_id=kw['session_id'],
        scratchpad_directory=str(kw['scratchpad_directory']) if kw['scratchpad_directory'] else None,
    ))

    goals_dir = tmp_path / 'goals_resume'
    agent._sm_goals = GoalRegistry(goals_dir)

    stored = StoredAgentSession(
        session_id='resumed_sess_42', model_config={}, runtime_config={},
        system_prompt_parts=('system',), user_context={}, system_context={},
        messages=(), turns=0, tool_calls=0, usage={}, total_cost_usd=0.0,
        file_history=(), budget_state={}, plugin_state={}, scratchpad_directory=None,
    )

    agent.resume('Continue the typed loop work', stored)

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].title == 'Continue the typed loop work'
    assert goals[0].status == 'done'  # clean stop_reason → done


def test_resume_does_not_mark_done_on_failure_class_stop(tmp_path, monkeypatch):
    from src.session_store import StoredAgentSession
    agent = _make_agent(tmp_path)
    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)
    monkeypatch.setattr(agent, '_run_prompt', lambda *a, **kw: AgentRunResult(
        final_output='', turns=0, tool_calls=0, transcript=(),
        stop_reason='budget_exceeded', session_id=kw['session_id'],
        scratchpad_directory=None,
    ))

    goals_dir = tmp_path / 'goals_resume_fail'
    agent._sm_goals = GoalRegistry(goals_dir)
    stored = StoredAgentSession(
        session_id='resumed_fail', model_config={}, runtime_config={},
        system_prompt_parts=('system',), user_context={}, system_context={},
        messages=(), turns=0, tool_calls=0, usage={}, total_cost_usd=0.0,
        file_history=(), budget_state={}, plugin_state={}, scratchpad_directory=None,
    )
    agent.resume('Resume that will exceed budget', stored)

    goals = agent._sm_goals.list_all()
    assert len(goals) == 1
    assert goals[0].status == 'active'  # budget_exceeded must NOT close


def test_mark_goal_done_silent_on_registry_failure(tmp_path):
    """If the goal registry raises, _mark_goal_done must not propagate."""
    agent = _make_agent(tmp_path)

    class BoomRegistry:
        def mark_done(self, goal_id, completed_at=None):
            raise RuntimeError('disk full')
    agent._sm_goals = BoomRegistry()

    g = Goal.new(title='boom test')
    # Should not raise
    agent._mark_goal_done(g)
