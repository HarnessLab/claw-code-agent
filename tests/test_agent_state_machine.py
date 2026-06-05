"""Tests for the typed state-machine objects.

Backs the design in ``~/.latti/STATE_MACHINE.md``. These verify that the
schemas round-trip cleanly, the State.next_turn transition works, and the
Operator protocol is satisfied by a minimal stub.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from agent_state_machine import (
    Action,
    BeliefState,
    CONSTITUTIONAL_WALLS,
    EvaluationResult,
    Fact,
    Goal,
    MemoryRecord,
    Observation,
    Operator,
    Plan,
    PolicyDecision,
    State,
    Step,
    Task,
    ToolCall,
    ValidationCheck,
    ValidationResult,
    violates_constitutional_wall,
)


def test_goal_constructs_with_id():
    g = Goal.new(title='ship state machine', success_criteria=('all tests green',))
    assert g.id.startswith('goal_')
    assert g.title == 'ship state machine'
    assert g.success_criteria == ('all tests green',)
    assert g.to_dict()['title'] == 'ship state machine'


def test_task_status_transitions_via_replace():
    t = Task.new(goal_id='goal_x', description='write the dataclasses')
    assert t.status == 'pending'
    # frozen dataclass: must construct a new one
    done_t = Task(id=t.id, goal_id=t.goal_id, description=t.description,
                  status='done', created_at=t.created_at, completed_at=42.0)
    assert done_t.status == 'done'
    assert done_t.completed_at == 42.0


def test_belief_state_immutable_with_helpers():
    b0 = BeliefState()
    b1 = b0.with_fact(Fact(claim='sky is blue', confidence=0.9, source='observation'))
    b2 = b1.with_question('but at night?')
    assert len(b0.facts) == 0
    assert len(b1.facts) == 1
    assert len(b2.unresolved_questions) == 1
    # original untouched
    assert len(b0.unresolved_questions) == 0


def test_state_next_turn_decrements_budget_and_advances_turn():
    s0 = State.fresh(session_id='sess_abc', budget_usd=1.0,
                     available_tools=('read_file', 'bash'))
    obs = Observation(action_id='act_1', kind='success', cost_usd=0.05)
    s1 = s0.next_turn(obs, budget_decrement_usd=0.05)
    assert s1.turn_id != s0.turn_id
    assert s1.session_id == s0.session_id
    assert s1.last_observation == obs
    assert abs(s1.budget_remaining_usd - 0.95) < 1e-9
    assert s1.available_tools == s0.available_tools


def test_state_next_turn_clamps_budget_at_zero():
    s = State.fresh(session_id='sess_x', budget_usd=0.10)
    obs = Observation(action_id='a1', kind='success')
    s2 = s.next_turn(obs, budget_decrement_usd=999.0)
    assert s2.budget_remaining_usd == 0.0


def test_plan_with_steps_round_trips():
    a = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': '/etc/hosts'})
    s1 = Step(id='step_1', plan_id='plan_x', action=a)
    p = Plan.new(task_id='task_y', steps=(s1,))
    d = p.to_dict()
    assert d['task_id'] == 'task_y'
    assert len(d['steps']) == 1
    assert d['steps'][0]['action']['kind'] == 'tool_call'


def test_validation_result_severity_blocks():
    vr = ValidationResult(
        action_id='act_42', passed=False,
        checks=(ValidationCheck(name='schema', passed=False, evidence='missing field "id"'),),
        severity='block',
    )
    assert vr.severity == 'block'
    assert not vr.passed
    assert vr.checks[0].evidence == 'missing field "id"'


def test_evaluation_result_verdict_done():
    er = EvaluationResult(task_id='t_1', score=1.0, verdict='done',
                          dimensions={'correctness': 1.0, 'cost': 0.9})
    assert er.verdict == 'done'
    assert er.dimensions['correctness'] == 1.0


def test_policy_decision_records_rejected_alternatives():
    chosen = Action(kind='tool_call', payload={'tool_name': 'read_file'})
    rejected = Action(kind='llm_call', payload={'prompt': 'guess'})
    pd = PolicyDecision(
        at_state_turn_id='turn_99',
        chose=chosen,
        rejected_alternatives=(rejected,),
        rationale='deterministic operator preferred over llm guess',
        confidence=0.95,
        decided_by='rule',
    )
    assert pd.decided_by == 'rule'
    assert len(pd.rejected_alternatives) == 1
    assert pd.rejected_alternatives[0].kind == 'llm_call'


def test_memory_record_factory():
    m = MemoryRecord.new(kind='scar', body='pi --print hangs without --base-url',
                         source_session_id='sess_42')
    assert m.id.startswith('mem_')
    assert m.kind == 'scar'
    assert m.source_session_id == 'sess_42'


def test_tool_call_serialises_with_error():
    tc = ToolCall(tool_name='bash', args={'cmd': 'ls /nope'},
                  started_at=1.0, finished_at=1.5,
                  raw_result=None, error='No such file or directory')
    d = tc.to_dict()
    assert d['error'] == 'No such file or directory'
    assert d['finished_at'] == 1.5


def test_operator_protocol_satisfied_by_stub():
    class StubOp:
        @property
        def kind(self):
            return 'tool_call'

        def can_handle(self, action):
            return action.kind == 'tool_call'

        def execute(self, action, state):
            return Observation(action_id=action.id, kind='success', payload={'echoed': action.payload})

    op = StubOp()
    assert isinstance(op, Operator)  # runtime_checkable protocol
    a = Action(kind='tool_call', payload={'msg': 'hi'})
    assert op.can_handle(a)
    obs = op.execute(a, State.fresh(session_id='s'))
    assert obs.kind == 'success'
    assert obs.payload['echoed']['msg'] == 'hi'


def test_constitutional_walls_non_empty():
    assert len(CONSTITUTIONAL_WALLS) >= 6
    assert 'never_commit_secrets' in CONSTITUTIONAL_WALLS


def test_violates_wall_returns_none_for_safe_action():
    a = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': '/tmp/x'})
    assert violates_constitutional_wall(a) is None


def test_violates_wall_blocks_force_push_main():
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'git push --force origin main'},
    })
    assert violates_constitutional_wall(a) == 'never_force_push_main'


def test_violates_wall_blocks_force_push_main_short_flag():
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'git push -f origin master'},
    })
    assert violates_constitutional_wall(a) == 'never_force_push_main'


def test_violates_wall_blocks_rm_rf_system_dir():
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'rm -rf /etc'},
    })
    assert violates_constitutional_wall(a) == 'never_delete_production_data'


def test_violates_wall_allows_rm_rf_tmp():
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash', 'arguments': {'cmd': 'rm -rf /tmp/scratch'},
    })
    assert violates_constitutional_wall(a) is None


def test_violates_wall_blocks_secret_in_payload():
    a = Action(kind='llm_call', payload={
        'messages': [{'role': 'user',
                      'content': 'my key is sk-ant-1234567890abcdefghij'}],
    })
    assert violates_constitutional_wall(a) == 'never_commit_secrets'


def test_violates_wall_blocks_github_token():
    a = Action(kind='llm_call', payload={
        'messages': [{'role': 'user',
                      'content': 'token: ghp_abcdefghij1234567890ABCDEFGHIJKLMNOPQR'}],
    })
    assert violates_constitutional_wall(a) == 'never_commit_secrets'


def test_violates_wall_blocks_credential_helper_mutation():
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash',
        'arguments': {'cmd': 'git config --global credential.helper store'},
    })
    assert violates_constitutional_wall(a) == 'never_silently_swallow_errors'


def test_violates_wall_first_match_wins_force_push_before_secret():
    """If multiple walls would match, the first-checked wins (deterministic)."""
    a = Action(kind='tool_call', payload={
        'tool_name': 'bash',
        'arguments': {'cmd': 'git push --force origin main && echo sk-ant-1234567890abcdefghij'},
    })
    # Force-push is checked first
    assert violates_constitutional_wall(a) == 'never_force_push_main'
