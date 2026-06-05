"""Concrete Evaluator implementations for the state machine.

Step 4 of the runway in ``~/.latti/STATE_MACHINE.md``: evaluators run after
each completed step (or the runner's full loop) and return a verdict the
Controller can branch on. Verdict precedence (most-severe-wins) is encoded
in ``combine_verdicts`` in ``agent_state_machine.py``.

Default evaluators here are intentionally conservative — they observe state
shape (budget, open tasks, last observation kind) without any LLM call.
Smarter LLM-driven evaluators can be added later as separate classes.
"""
from __future__ import annotations

from src.agent_state_machine import (
    EvaluationResult,
    Goal,
    State,
)


class BudgetExhaustionEvaluator:
    """Returns ``timeout`` when the State's budget is depleted.

    A safety brake — without this, a runaway loop could chew through any
    budget cap silently. Always applies; verdict is 'timeout' iff
    budget_remaining_usd <= 0, else 'continue'.
    """

    def __init__(self, threshold_usd: float = 0.0) -> None:
        self._threshold = threshold_usd

    @property
    def name(self) -> str:
        return 'budget_exhaustion'

    def evaluate(self, state: State, goal: Goal | None = None) -> EvaluationResult:
        exhausted = state.budget_remaining_usd <= self._threshold
        return EvaluationResult(
            task_id=goal.id if goal else 'no_goal',
            score=0.0 if exhausted else 1.0,
            dimensions={'budget_remaining_usd': state.budget_remaining_usd,
                        'threshold': self._threshold},
            verdict='timeout' if exhausted else 'continue',
            note='budget depleted' if exhausted else 'budget OK',
        )


class TaskCompletionEvaluator:
    """Returns ``done`` when the State has no open tasks AND last observation succeeded.

    Combined with a Goal that decomposes into Tasks, this gives the runner an
    explicit signal that the work is finished. With no open_tasks at all (or
    only completed/abandoned tasks), the verdict is 'done'.
    """

    @property
    def name(self) -> str:
        return 'task_completion'

    def evaluate(self, state: State, goal: Goal | None = None) -> EvaluationResult:
        active = [t for t in state.open_tasks if t.status in ('pending', 'in_progress', 'blocked')]
        last_kind = state.last_observation.kind if state.last_observation else None
        no_active = len(active) == 0
        last_ok = last_kind in (None, 'success', 'noop')

        if no_active and last_ok:
            verdict = 'done'
            score = 1.0
            note = 'no active tasks, last observation OK'
        else:
            verdict = 'continue'
            score = 1.0 - (len(active) / max(len(state.open_tasks), 1))
            note = f'{len(active)} active task(s) remaining'

        return EvaluationResult(
            task_id=goal.id if goal else 'no_goal',
            score=score,
            dimensions={'active_tasks': len(active),
                        'total_tasks': len(state.open_tasks),
                        'last_observation_kind': last_kind or 'none'},
            verdict=verdict,
            note=note,
        )


class ConsecutiveErrorEvaluator:
    """Triggers ``replan`` after N consecutive error observations.

    Stateless across runner instances — it inspects only the most recent
    observation and tracks a counter via a closure. For multi-error tracking
    across calls, the runner is responsible for maintaining this state in
    the State.beliefs or a separate ledger.

    This implementation is single-shot: it returns 'replan' if the last
    observation alone is an error, otherwise 'continue'. A more sophisticated
    multi-step counter belongs in a future Controller, not here.
    """

    @property
    def name(self) -> str:
        return 'consecutive_error'

    def evaluate(self, state: State, goal: Goal | None = None) -> EvaluationResult:
        last_kind = state.last_observation.kind if state.last_observation else None
        is_err = last_kind == 'error'
        return EvaluationResult(
            task_id=goal.id if goal else 'no_goal',
            score=0.5 if is_err else 1.0,
            dimensions={'last_observation_kind': last_kind or 'none'},
            verdict='replan' if is_err else 'continue',
            note='last observation was an error' if is_err else 'last observation OK',
        )
