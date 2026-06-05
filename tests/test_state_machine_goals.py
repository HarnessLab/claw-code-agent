"""Tests for GoalRegistry + TaskTracker — typed Goal/Task lifecycle persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent_state_machine import Goal, Task
from src.state_machine_goals import GoalRegistry, TaskTracker


# ---- GoalRegistry ---------------------------------------------------------

def test_register_writes_jsonl_line(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = Goal.new(title='ship typed loop', success_criteria=('all tests pass',))
    reg.register(g)

    line = reg.goals_path.read_text().strip()
    d = json.loads(line)
    assert d['id'] == g.id
    assert d['title'] == 'ship typed loop'
    assert d['success_criteria'] == ['all tests pass']


def test_list_all_returns_goals_in_order(tmp_path):
    reg = GoalRegistry(tmp_path)
    g1 = Goal.new(title='first')
    g2 = Goal.new(title='second')
    reg.register(g1)
    reg.register(g2)

    goals = reg.list_all()
    assert len(goals) == 2
    assert goals[0].title == 'first'
    assert goals[1].title == 'second'


def test_get_returns_goal_by_id(tmp_path):
    reg = GoalRegistry(tmp_path)
    g = Goal.new(title='find me')
    reg.register(g)
    found = reg.get(g.id)
    assert found is not None
    assert found.title == 'find me'
    assert reg.get('goal_does_not_exist') is None


def test_children_of_returns_only_direct_children(tmp_path):
    reg = GoalRegistry(tmp_path)
    parent = Goal.new(title='parent')
    child_a = Goal.new(title='child A', parent_goal=parent.id)
    child_b = Goal.new(title='child B', parent_goal=parent.id)
    unrelated = Goal.new(title='unrelated')
    reg.register(parent)
    reg.register(child_a)
    reg.register(child_b)
    reg.register(unrelated)

    children = reg.children_of(parent.id)
    assert len(children) == 2
    assert {c.title for c in children} == {'child A', 'child B'}


def test_list_all_handles_missing_file(tmp_path):
    reg = GoalRegistry(tmp_path / 'never_written')
    assert reg.list_all() == []


# ---- TaskTracker ----------------------------------------------------------

def test_add_appends_task(tmp_path):
    t = TaskTracker(tmp_path)
    task = Task.new(goal_id='g1', description='do thing')
    t.add(task)
    folded = t._fold()
    assert task.id in folded
    assert folded[task.id].status == 'pending'


def test_update_status_writes_new_line_and_supersedes(tmp_path):
    t = TaskTracker(tmp_path)
    task = Task.new(goal_id='g1', description='do thing')
    t.add(task)
    t.update_status(task.id, 'in_progress')
    t.update_status(task.id, 'done', completed_at=999.0)

    current = t.get(task.id)
    assert current is not None
    assert current.status == 'done'
    assert current.completed_at == 999.0

    history = t.history(task.id)
    assert len(history) == 3
    assert [h.status for h in history] == ['pending', 'in_progress', 'done']


def test_update_status_returns_none_for_unknown_task(tmp_path):
    t = TaskTracker(tmp_path)
    assert t.update_status('task_unknown', 'done') is None


def test_list_for_goal_filters_by_goal_id(tmp_path):
    t = TaskTracker(tmp_path)
    t.add(Task.new(goal_id='g1', description='one'))
    t.add(Task.new(goal_id='g1', description='two'))
    t.add(Task.new(goal_id='g2', description='other'))

    assert len(t.list_for_goal('g1')) == 2
    assert len(t.list_for_goal('g2')) == 1


def test_list_active_excludes_done_and_abandoned(tmp_path):
    t = TaskTracker(tmp_path)
    a = t.add(Task.new(goal_id='g1', description='active pending'))
    b = t.add(Task.new(goal_id='g1', description='will finish'))
    c = t.add(Task.new(goal_id='g1', description='will abandon'))
    blocked = t.add(Task.new(goal_id='g1', description='blocked'))

    t.update_status(b.id, 'done')
    t.update_status(c.id, 'abandoned')
    t.update_status(blocked.id, 'blocked')

    active = t.list_active_for_goal('g1')
    active_ids = {x.id for x in active}
    assert a.id in active_ids
    assert blocked.id in active_ids  # 'blocked' counts as active
    assert b.id not in active_ids    # done excluded
    assert c.id not in active_ids    # abandoned excluded


def test_jsonl_files_handle_corrupt_lines_gracefully(tmp_path):
    """If a line is unparseable, it's skipped — the rest still loads."""
    reg = GoalRegistry(tmp_path)
    reg.register(Goal.new(title='good'))
    # Inject a bad line
    with reg.goals_path.open('a', encoding='utf-8') as f:
        f.write('this is not json\n')
    reg.register(Goal.new(title='also good'))

    goals = reg.list_all()
    assert len(goals) == 2
    assert {g.title for g in goals} == {'good', 'also good'}


def test_history_returns_chronological_order(tmp_path):
    t = TaskTracker(tmp_path)
    task = Task.new(goal_id='g1', description='trace me')
    t.add(task)
    t.update_status(task.id, 'in_progress')
    t.update_status(task.id, 'blocked')
    t.update_status(task.id, 'in_progress')
    t.update_status(task.id, 'done', completed_at=1.0)

    statuses = [h.status for h in t.history(task.id)]
    assert statuses == ['pending', 'in_progress', 'blocked', 'in_progress', 'done']
