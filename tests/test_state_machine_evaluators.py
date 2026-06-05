"""Tests for the post-step Evaluator pipeline.

Step 4 of the runway in ``~/.latti/STATE_MACHINE.md``: evaluators score progress
and emit a verdict; the runner uses verdict precedence to decide whether to
continue, replan, escalate, or terminate.
"""
from __future__ import annotations

import pytest

from src.agent_state_machine import (
    Action,
    EvaluationResult,
    Evaluator,
    Goal,
    Observation,
    State,
    Task,
    combine_verdicts,
)
from src.state_machine_evaluators import (
    BudgetExhaustionEvaluator,
    ConsecutiveErrorEvaluator,
    TaskCompletionEvaluator,
)
from src.state_machine_operators import EchoLLMOperator, ReadFileOperator
from src.state_machine_runner import StateMachineRunner


# ---- Verdict precedence ----------------------------------------------------

def test_combine_verdicts_picks_most_severe():
    assert combine_verdicts(()) == 'continue'
    assert combine_verdicts(('continue',)) == 'continue'
    assert combine_verdicts(('replan',)) == 'replan'
    assert combine_verdicts(('replan', 'done')) == 'done'
    assert combine_verdicts(('done', 'escalate')) == 'escalate'
    assert combine_verdicts(('escalate', 'timeout')) == 'timeout'
    assert combine_verdicts(('continue', 'replan', 'done', 'escalate', 'timeout')) == 'timeout'


# ---- Evaluator protocol satisfaction --------------------------------------

def test_budget_exhaustion_evaluator_satisfies_protocol():
    e = BudgetExhaustionEvaluator()
    assert isinstance(e, Evaluator)


def test_task_completion_evaluator_satisfies_protocol():
    assert isinstance(TaskCompletionEvaluator(), Evaluator)


def test_consecutive_error_evaluator_satisfies_protocol():
    assert isinstance(ConsecutiveErrorEvaluator(), Evaluator)


# ---- BudgetExhaustionEvaluator semantics ----------------------------------

def test_budget_exhaustion_returns_continue_when_funded():
    s = State.fresh(session_id='s1', budget_usd=1.0)
    r = BudgetExhaustionEvaluator().evaluate(s)
    assert r.verdict == 'continue'


def test_budget_exhaustion_returns_timeout_when_drained():
    s = State.fresh(session_id='s1', budget_usd=0.0)
    r = BudgetExhaustionEvaluator().evaluate(s)
    assert r.verdict == 'timeout'


# ---- TaskCompletionEvaluator semantics ------------------------------------

def test_task_completion_returns_done_when_no_active_tasks():
    s = State.fresh(session_id='s1')
    r = TaskCompletionEvaluator().evaluate(s)
    assert r.verdict == 'done'


def test_task_completion_returns_continue_with_pending_task():
    t = Task.new(goal_id='g1', description='do thing')
    s = State(turn_id='turn_1', session_id='s1', open_tasks=(t,))
    r = TaskCompletionEvaluator().evaluate(s)
    assert r.verdict == 'continue'


# ---- ConsecutiveErrorEvaluator semantics ----------------------------------

def test_consecutive_error_replan_on_error_observation():
    obs = Observation(action_id='a1', kind='error', payload={'error': 'x'})
    s = State.fresh(session_id='s1')
    s = s.next_turn(obs)
    r = ConsecutiveErrorEvaluator().evaluate(s)
    assert r.verdict == 'replan'


def test_consecutive_error_continue_on_success_observation():
    obs = Observation(action_id='a1', kind='success', payload={})
    s = State.fresh(session_id='s1')
    s = s.next_turn(obs)
    r = ConsecutiveErrorEvaluator().evaluate(s)
    assert r.verdict == 'continue'


# ---- run_until_done loop --------------------------------------------------

def test_run_until_done_exits_when_action_supplier_returns_none(tmp_path):
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=tmp_path / 'log.jsonl',
        evaluators=[BudgetExhaustionEvaluator()],
    )
    s = State.fresh(session_id='s1', budget_usd=1.0)

    calls = []
    def supplier(_state):
        if not calls:
            calls.append(1)
            return Action(kind='llm_call', payload={'prompt': 'hi'})
        return None  # halt

    final_state, result = runner.run_until_done(s, supplier, max_turns=10)
    assert result.verdict == 'done'
    assert result.note == 'action_supplier returned None'


def test_run_until_done_terminates_on_budget_exhaustion(tmp_path):
    """Construct a runner with an expensive operator + budget validator;
    after one step the budget is gone, evaluator returns timeout."""

    class ExpensiveOp:
        @property
        def kind(self):
            return 'llm_call'

        def can_handle(self, action):
            return action.kind == 'llm_call'

        def execute(self, action, state):
            return Observation(action_id=action.id, kind='success',
                               payload={'completion': 'ok'}, cost_usd=0.50)

    runner = StateMachineRunner(
        operators=[ExpensiveOp()],
        decision_log_path=tmp_path / 'log.jsonl',
        evaluators=[BudgetExhaustionEvaluator()],
    )
    s = State.fresh(session_id='s1', budget_usd=0.50)

    def supplier(_state):
        return Action(kind='llm_call', payload={'prompt': 'expensive'})

    _, result = runner.run_until_done(s, supplier, max_turns=10)
    assert result.verdict == 'timeout'


def test_run_until_done_hits_max_turns(tmp_path):
    """No terminal evaluator → loop hits max_turns and returns timeout."""
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=tmp_path / 'log.jsonl',
        evaluators=[],  # no terminal verdicts will fire
    )
    s = State.fresh(session_id='s1', budget_usd=1.0)

    def supplier(_state):
        return Action(kind='llm_call', payload={'prompt': 'forever'})

    _, result = runner.run_until_done(s, supplier, max_turns=3)
    assert result.verdict == 'timeout'
    assert 'max_turns=3' in result.note


def test_run_until_done_replan_does_not_terminate(tmp_path):
    """A 'replan' verdict should NOT exit the loop. The supplier eventually
    halts via None, then we get done."""
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=tmp_path / 'log.jsonl',
        evaluators=[ConsecutiveErrorEvaluator()],  # may emit replan but not terminal
    )
    s = State.fresh(session_id='s1', budget_usd=1.0)

    counter = {'i': 0}
    def supplier(_state):
        counter['i'] += 1
        if counter['i'] > 2:
            return None
        return Action(kind='llm_call', payload={'prompt': f'turn {counter["i"]}'})

    _, result = runner.run_until_done(s, supplier, max_turns=10)
    # EchoLLMOperator returns 'success' so evaluator says continue;
    # supplier eventually returns None → done.
    assert result.verdict == 'done'


def test_runner_evaluate_returns_one_result_per_evaluator():
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=None,
        evaluators=[BudgetExhaustionEvaluator(), TaskCompletionEvaluator()],
    )
    s = State.fresh(session_id='s1', budget_usd=1.0)
    results = runner.evaluate(s)
    assert len(results) == 2
    names = {type(e).__name__ for e in [BudgetExhaustionEvaluator(), TaskCompletionEvaluator()]}
    assert all(isinstance(r, EvaluationResult) for r in results)


def test_runner_combined_verdict_uses_precedence():
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=None,
        evaluators=[],
    )
    # Synthesize results manually to exercise the helper
    rs = (
        EvaluationResult(task_id='t', score=1.0, verdict='continue'),
        EvaluationResult(task_id='t', score=0.0, verdict='timeout'),
        EvaluationResult(task_id='t', score=0.5, verdict='replan'),
    )
    assert runner.combined_verdict(rs) == 'timeout'
