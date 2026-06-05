"""Concrete Controller implementations for the state machine.

Step 5 of the runway in ``~/.latti/STATE_MACHINE.md``: Controllers pick the
next Action given a State. Rule-based controllers fire on known-shape
transitions (cheap, deterministic). LLM-based controllers handle ambiguity
(expensive, non-deterministic). Compose via ``FallbackController`` so the
rule path is tried first and the LLM is reached only when no rule matched.

A Controller returns a typed ``PolicyDecision`` (not a bare Action) so the
runner records rationale + decided_by metadata with every choice.
"""
from __future__ import annotations

from typing import Callable

from src.agent_state_machine import (
    Action,
    Controller,
    Goal,
    PolicyDecision,
    State,
)


# Type alias: a rule is (predicate, action_factory).
# - predicate(state, goal) → bool: should this rule fire?
# - action_factory(state, goal) → Action | None: what Action does it propose?
Predicate = Callable[[State, 'Goal | None'], bool]
ActionFactory = Callable[[State, 'Goal | None'], 'Action | None']
Rule = tuple[Predicate, ActionFactory, str]  # last element is the rule's name


_REPLAN_REMINDER_BASE = (
    'STATE-LAYER NOTICE: The state-machine evaluator flagged the previous '
    'step with verdict=replan. The last action produced an error '
    'observation. Reconsider your approach before retrying — diagnose the '
    'failure, then choose a different tool or argument shape.'
)


def _inject_replan_reminder(payload: dict, last_error_text: str = '') -> dict:
    """Return a copy of `payload` with a State-layer replan reminder
    appended to the messages list.

    The reminder includes the actual last-observation error text when
    available. Without it (e.g., older callers that don't thread it),
    the reminder degrades gracefully to its base form. One-shot
    consumption is the agent_runtime's job — see
    _evaluate_state_after_step's verdict threading.
    """
    body = _REPLAN_REMINDER_BASE
    if last_error_text:
        # Truncate aggressively — the model only needs the failure
        # signature, not a full traceback in the prompt.
        snippet = last_error_text.strip()
        if len(snippet) > 500:
            snippet = snippet[:497] + '...'
        body = (
            f'{_REPLAN_REMINDER_BASE}\n\n'
            f'Specific failure: {snippet}'
        )
    reminder = f'<system-reminder>\n{body}\n</system-reminder>'
    messages = list(payload.get('messages') or [])
    messages.append({'role': 'user', 'content': reminder})
    return {**payload, 'messages': messages}


class RuleBasedController:
    """Picks the first rule whose predicate fires.

    Rules are tuples ``(predicate, action_factory, rule_name)``. The first
    rule whose predicate returns True is used to build the Action. The
    resulting PolicyDecision carries ``decided_by='rule'`` and the rule's
    name as the rationale.

    If no predicate matches, returns ``None`` so a fallback Controller can
    take over.
    """

    def __init__(self, rules: list[Rule], name: str = 'rule_based') -> None:
        self._rules: tuple[Rule, ...] = tuple(rules)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None:
        for predicate, factory, rule_name in self._rules:
            try:
                fires = predicate(state, goal)
            except Exception:
                # A misbehaving rule should not break the controller chain.
                continue
            if not fires:
                continue
            try:
                action = factory(state, goal)
            except Exception:
                continue
            if action is None:
                continue
            return PolicyDecision(
                at_state_turn_id=state.turn_id,
                chose=action,
                rationale=f'rule_fired: {rule_name}',
                decided_by='rule',
                confidence=1.0,
            )
        return None


class FixedActionController:
    """Always emits the same Action. Useful for tests and trivial loops."""

    def __init__(self, action: Action, name: str = 'fixed_action') -> None:
        self._action = action
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None:
        return PolicyDecision(
            at_state_turn_id=state.turn_id,
            chose=self._action,
            rationale=f'fixed: {self._name}',
            decided_by='rule',
            confidence=1.0,
        )


class FallbackController:
    """Tries primary; if primary returns None, tries fallback.

    The classic "rules first, LLM second" composition: pass a
    RuleBasedController as primary and an LLM-driven Controller as fallback.
    The fallback's PolicyDecision will carry ``decided_by`` from whichever
    Controller produced it.
    """

    def __init__(
        self,
        primary: Controller,
        fallback: Controller,
        name: str = 'fallback',
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None:
        decision = self._primary.pick(state, goal)
        if decision is not None:
            return decision
        return self._fallback.pick(state, goal)


class HaltController:
    """Always returns None — signals the loop to halt.

    Useful as the terminal element of a fallback chain when the design says
    "if no rule fires AND no LLM is available, just stop."
    """

    @property
    def name(self) -> str:
        return 'halt'

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None:
        return None


class RuntimeLoopController:
    """Controller for the chat/runtime outer loop.

    Reads lightweight runtime context from ``State.runtime`` and decides the
    next concrete action for the agent loop. This is the first pass that makes
    the outer loop state-machine-driven instead of a plain Python branch nest.
    """

    def __init__(self, name: str = 'runtime_loop') -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def pick(self, state: State, goal: Goal | None = None) -> PolicyDecision | None:
        del goal
        runtime = state.runtime if isinstance(state.runtime, dict) else {}

        if runtime.get('final_output') is not None:
            return None

        pending_tool_calls = runtime.get('pending_tool_calls')
        if isinstance(pending_tool_calls, list) and pending_tool_calls:
            first = pending_tool_calls[0]
            if not isinstance(first, dict):
                return None
            tool_name = first.get('name')
            arguments = first.get('arguments')
            if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                return None
            return PolicyDecision(
                at_state_turn_id=state.turn_id,
                chose=Action(
                    kind='tool_call',
                    payload={
                        'tool_name': tool_name,
                        'arguments': arguments,
                    },
                ),
                rationale='rule_fired: runtime_execute_pending_tool_call',
                decided_by='rule',
                confidence=1.0,
            )

        if runtime.get('awaiting_model'):
            payload = runtime.get('next_llm_action')
            if not isinstance(payload, dict):
                return None

            # Verdict→action wiring (v2 close).
            # The State layer's last evaluation is in runtime['last_verdict'].
            # This is where evaluator verdicts go from passive telemetry to
            # active control:
            #   'escalate' → halt the loop (return None)
            #   'replan'   → inject a State-layer reminder into the next LLM
            #                payload so the model sees explicit governance
            #                feedback, not just the raw error in context
            #   anything else → normal pass-through
            # See state_machine_evaluators.py for what produces each verdict.
            verdict = runtime.get('last_verdict')
            if verdict == 'escalate':
                return None  # halt — outer loop produces controller_halt result

            rationale = 'rule_fired: runtime_query_model'
            if verdict == 'replan':
                last_error_text = runtime.get('last_error_text', '')
                if not isinstance(last_error_text, str):
                    last_error_text = ''
                payload = _inject_replan_reminder(payload, last_error_text)
                rationale = 'rule_fired: runtime_query_model_with_replan_reminder'

            return PolicyDecision(
                at_state_turn_id=state.turn_id,
                chose=Action(kind='llm_call', payload=payload),
                rationale=rationale,
                decided_by='rule',
                confidence=1.0,
            )

        return None
