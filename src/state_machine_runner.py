"""Minimum-viable state-machine runner.

Owns a list of Operators, dispatches Actions through the right one, returns
typed Observations and advances State. Logs every PolicyDecision to an
append-only JSONL file so the Controller's choices are auditable.

This runner is intentionally NOT integrated with agent_runtime.py. It is a
parallel, isolated path that proves the typed loop works on real Operators
before we migrate the real runtime to it. See ``~/.latti/STATE_MACHINE.md``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from typing import Callable

from src.agent_state_machine import (
    Action,
    Controller,
    EvaluationResult,
    Evaluator,
    Goal,
    Observation,
    Operator,
    PolicyDecision,
    State,
    Validator,
    ValidationResult,
    combine_verdicts,
    violates_constitutional_wall,
)


DEFAULT_DECISION_LOG = Path.home() / '.latti' / 'memory' / 'policy_decisions.jsonl'


class NoOperatorError(RuntimeError):
    """Raised when no registered Operator can handle the given Action."""


class StateMachineRunner:
    """Dispatches Actions through registered Operators.

    Usage:
        runner = StateMachineRunner(operators=[ReadFileOperator(), EchoLLMOperator()])
        obs, new_state = runner.run_one_step(state, action, rationale='...')

    Optionally accepts ``validators`` — Validators run AFTER the Operator
    produces an Observation. If any applicable Validator returns
    ``severity='block'``, the Observation is replaced with an error Observation
    whose payload includes the failed ValidationResults. Severity 'warn' and
    'info' do not block; results are still attached to the PolicyDecision log.

    The decision log is append-only at ``decision_log_path`` (default:
    ``~/.latti/memory/policy_decisions.jsonl``). Pass ``decision_log_path=None``
    to disable logging in tests.
    """

    def __init__(
        self,
        operators: Iterable[Operator],
        decision_log_path: Path | None = DEFAULT_DECISION_LOG,
        validators: Iterable[Validator] = (),
        evaluators: Iterable[Evaluator] = (),
    ) -> None:
        self._operators: tuple[Operator, ...] = tuple(operators)
        if not self._operators:
            raise ValueError('StateMachineRunner requires at least one Operator')
        self._decision_log_path = decision_log_path
        self._validators: tuple[Validator, ...] = tuple(validators)
        self._evaluators: tuple[Evaluator, ...] = tuple(evaluators)

    @property
    def operators(self) -> tuple[Operator, ...]:
        return self._operators

    @property
    def evaluators(self) -> tuple[Evaluator, ...]:
        """Public accessor for wired evaluators.

        Telemetry callers (agent_runtime._evaluate_state_after_step) need to
        pair evaluator names with their EvaluationResult by index, since
        evaluate() returns plain results without name. Symmetric with
        operators above.
        """
        return self._evaluators

    def pick(self, action: Action) -> Operator:
        """Return the first operator that can handle the action."""
        for op in self._operators:
            if op.can_handle(action):
                return op
        raise NoOperatorError(
            f'no operator can handle action.kind={action.kind!r} '
            f'payload-keys={sorted(action.payload.keys())}'
        )

    def run_one_step(
        self,
        state: State,
        action: Action,
        rationale: str = '',
        rejected_alternatives: tuple[Action, ...] = (),
        decided_by: str = 'rule',
    ) -> tuple[Observation, State]:
        """Pick operator, execute, log decision, advance state.

        Returns (observation, new_state). On NoOperatorError, returns an error
        Observation and an advanced state — never raises to the caller. This
        keeps the loop walking even when an action shape is unknown.
        """
        # Constitutional walls — block BEFORE operator dispatch. Walls are
        # never decided by the LLM; this is the hard-coded floor.
        wall = violates_constitutional_wall(action)
        if wall is not None:
            obs = Observation(
                action_id=action.id, kind='error',
                payload={
                    'error': f'constitutional wall violated: {wall}',
                    'wall': wall,
                    'blocked': True,
                },
            )
            self._log_decision(
                state=state, action=action, observation=obs,
                rationale=f'wall_blocked: {wall}',
                rejected_alternatives=rejected_alternatives,
                decided_by=decided_by,
            )
            return obs, state.next_turn(obs)

        try:
            op = self.pick(action)
        except NoOperatorError as exc:
            obs = Observation(
                action_id=action.id, kind='error',
                payload={'error': str(exc), 'unhandled_action_kind': action.kind},
            )
            self._log_decision(
                state=state, action=action, observation=obs,
                rationale=f'no_operator: {exc}',
                rejected_alternatives=rejected_alternatives,
                decided_by=decided_by,
            )
            new_state = state.next_turn(obs)
            return obs, new_state

        # Pre-dispatch validation (anchor-derived block-severity).
        # Validators with a pre_validate(action) method get one chance
        # to block before the operator executes. Returning a
        # ValidationResult with severity='block' substitutes an error
        # Observation and skips operator execution — for bash actions
        # this means the command NEVER runs. None means "no opinion;
        # proceed". Static walls already handled above by
        # violates_constitutional_wall; this is the session-aware tier.
        pre_block = self._run_pre_validators(action)
        if pre_block is not None:
            obs = Observation(
                action_id=action.id, kind='error',
                payload={
                    'error': 'blocked by pre-dispatch validator',
                    'blocked': True,
                    'blocking_validations': [pre_block.to_dict()],
                },
            )
            self._log_decision(
                state=state, action=action, observation=obs,
                rationale=rationale or f'pre_dispatch_block by {pre_block.checks[0].name if pre_block.checks else "validator"}',
                rejected_alternatives=rejected_alternatives,
                decided_by=decided_by,
                validation_results=(pre_block,),
            )
            return obs, state.next_turn(obs)

        obs = op.execute(action, state)

        # Run validators. Any 'block'-severity result replaces the Observation
        # with a typed error variant. 'warn'/'info' results are recorded but
        # do not interrupt the loop.
        validation_results = self._run_validators(action, obs)
        blocking = [v for v in validation_results if v.severity == 'block']
        if blocking:
            obs = Observation(
                action_id=action.id, kind='error',
                payload={
                    'error': 'blocked by validator',
                    'blocking_validations': [v.to_dict() for v in blocking],
                    'all_validations': [v.to_dict() for v in validation_results],
                    'original_observation': obs.to_dict(),
                },
                cost_usd=obs.cost_usd,
                tokens=obs.tokens,
            )

        self._log_decision(
            state=state, action=action, observation=obs,
            rationale=rationale or f'matched operator kind={op.kind}',
            rejected_alternatives=rejected_alternatives,
            decided_by=decided_by,
            validation_results=validation_results,
        )
        new_state = state.next_turn(obs, budget_decrement_usd=obs.cost_usd)
        return obs, new_state

    def evaluate(
        self, state: State, goal: Goal | None = None,
    ) -> tuple[EvaluationResult, ...]:
        """Run every registered Evaluator. Catches and surfaces raises."""
        results: list[EvaluationResult] = []
        for ev in self._evaluators:
            try:
                results.append(ev.evaluate(state, goal))
            except Exception as exc:  # pragma: no cover — defensive
                results.append(EvaluationResult(
                    task_id=goal.id if goal else 'no_goal',
                    score=0.0,
                    verdict='continue',
                    note=f'evaluator {getattr(ev, "name", type(ev).__name__)} raised: {exc!r}',
                ))
        return tuple(results)

    def combined_verdict(self, eval_results: tuple[EvaluationResult, ...]):
        """Combine multiple EvaluationResults into a single verdict via precedence."""
        return combine_verdicts(tuple(r.verdict for r in eval_results))

    def run_until_done(
        self,
        state: State,
        action_supplier: Callable[[State], Action | None] | None = None,
        max_turns: int = 50,
        goal: Goal | None = None,
        controller: Controller | None = None,
    ) -> tuple[State, EvaluationResult]:
        """Walk the loop until an Evaluator returns a terminal verdict or max_turns.

        Two ways to drive the loop:
          - ``controller`` (typed): a ``Controller`` whose ``pick(state, goal)``
            returns a ``PolicyDecision`` or ``None``. The runner uses the
            decision's rationale + decided_by when logging.
          - ``action_supplier`` (callable): legacy plain-callable form, kept
            for backward compatibility.

        Exactly one of ``controller`` or ``action_supplier`` must be provided.
        Returning ``None`` from either signals "halt"; the runner emits a
        ``done`` verdict.

        Terminal verdicts: 'done', 'escalate', 'timeout'. 'replan' and 'continue'
        keep the loop walking. Returns the final State plus a synthesized
        EvaluationResult.
        """
        if (controller is None) == (action_supplier is None):
            raise ValueError(
                'run_until_done requires exactly one of controller or action_supplier',
            )

        for _ in range(max_turns):
            if controller is not None:
                decision = controller.pick(state, goal)
                if decision is None:
                    return state, EvaluationResult(
                        task_id=goal.id if goal else 'no_goal',
                        score=1.0, verdict='done',
                        note=f'controller {controller.name!r} returned None',
                    )
                action = decision.chose
                rationale = decision.rationale
                rejected = decision.rejected_alternatives
                decided_by = decision.decided_by
            else:
                action = action_supplier(state)  # type: ignore[misc]
                if action is None:
                    return state, EvaluationResult(
                        task_id=goal.id if goal else 'no_goal',
                        score=1.0, verdict='done',
                        note='action_supplier returned None',
                    )
                rationale = ''
                rejected = ()
                decided_by = 'rule'

            _, state = self.run_one_step(
                state, action,
                rationale=rationale,
                rejected_alternatives=rejected,
                decided_by=decided_by,
            )
            eval_results = self.evaluate(state, goal)
            verdict = self.combined_verdict(eval_results)
            if verdict in ('done', 'escalate', 'timeout'):
                return state, EvaluationResult(
                    task_id=goal.id if goal else 'no_goal',
                    score=max((r.score for r in eval_results), default=0.0),
                    dimensions={'evaluator_count': len(eval_results)},
                    verdict=verdict,
                    note='terminal verdict from evaluators',
                )

        return state, EvaluationResult(
            task_id=goal.id if goal else 'no_goal',
            score=0.0, verdict='timeout',
            note=f'max_turns={max_turns} reached without terminal verdict',
        )

    def _run_pre_validators(self, action: Action) -> ValidationResult | None:
        """Invoke every validator's pre_validate (if it has one).

        Returns the FIRST block-severity result (deterministic order by
        registration). Validators without pre_validate are skipped.
        Validator raises are swallowed (defensive); the runner must
        never crash on validator implementation errors.
        """
        for v in self._validators:
            pv = getattr(v, 'pre_validate', None)
            if pv is None:
                continue
            try:
                if not v.applies_to(action):
                    continue
                result = pv(action)
            except Exception:  # pragma: no cover — defensive
                continue
            if result is None:
                continue
            if result.severity == 'block':
                return result
        return None

    def _run_validators(
        self, action: Action, observation: Observation,
    ) -> tuple[ValidationResult, ...]:
        """Invoke every applicable Validator. Catch any that raise."""
        results: list[ValidationResult] = []
        for v in self._validators:
            try:
                if not v.applies_to(action):
                    continue
                results.append(v.validate(action, observation))
            except Exception as exc:  # pragma: no cover — defensive
                from src.agent_state_machine import ValidationCheck
                results.append(ValidationResult(
                    action_id=action.id, passed=False,
                    checks=(ValidationCheck(
                        name=getattr(v, 'name', type(v).__name__),
                        passed=False,
                        evidence=f'validator raised: {exc!r}',
                    ),),
                    severity='warn',
                ))
        return tuple(results)

    # ---- internals ---------------------------------------------------------

    def _log_decision(
        self,
        state: State,
        action: Action,
        observation: Observation,
        rationale: str,
        rejected_alternatives: tuple[Action, ...],
        decided_by: str,
        validation_results: tuple[ValidationResult, ...] = (),
    ) -> None:
        if self._decision_log_path is None:
            return
        decision = PolicyDecision(
            at_state_turn_id=state.turn_id,
            chose=action,
            rejected_alternatives=rejected_alternatives,
            rationale=rationale,
            decided_by=decided_by,  # type: ignore[arg-type]
        )
        record = {
            'decision': decision.to_dict(),
            'observation_kind': observation.kind,
            'session_id': state.session_id,
            'validations': [v.to_dict() for v in validation_results],
        }
        try:
            self._decision_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._decision_log_path.open('a', encoding='utf-8') as f:
                # default=str: any non-JSON-serializable payload value (e.g.
                # OutputSchemaConfig from agent_runtime's response_schema feature)
                # is coerced to its repr instead of crashing the dispatch.
                f.write(json.dumps(record, default=str) + '\n')
        except OSError:
            # Logging must never break the loop. Silently drop on FS error.
            pass
