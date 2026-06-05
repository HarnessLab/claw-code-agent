"""Tests for typed Controllers + run_until_done(controller=...) integration.

Step 5 of the runway in ``~/.latti/STATE_MACHINE.md``: Controllers replace
the bare action_supplier callable with a typed Protocol that returns a
PolicyDecision (rationale + decided_by metadata propagated to the log).
"""
from __future__ import annotations

import json

import pytest

from src.agent_state_machine import (
    Action,
    Controller,
    Goal,
    Observation,
    PolicyDecision,
    State,
    Task,
)
from src.state_machine_controllers import (
    FallbackController,
    FixedActionController,
    HaltController,
    RuleBasedController,
)
from src.state_machine_evaluators import BudgetExhaustionEvaluator
from src.state_machine_operators import EchoLLMOperator
from src.state_machine_runner import StateMachineRunner


# ---- Protocol satisfaction -------------------------------------------------

def test_rule_based_controller_satisfies_protocol():
    c = RuleBasedController(rules=[])
    assert isinstance(c, Controller)
    assert c.name == 'rule_based'


def test_fixed_action_controller_satisfies_protocol():
    a = Action(kind='llm_call', payload={'prompt': 'hi'})
    assert isinstance(FixedActionController(a), Controller)


def test_halt_controller_satisfies_protocol():
    assert isinstance(HaltController(), Controller)


def test_fallback_controller_satisfies_protocol():
    primary = HaltController()
    fallback = HaltController()
    assert isinstance(FallbackController(primary, fallback), Controller)


# ---- RuleBasedController semantics ----------------------------------------

def test_rule_based_picks_first_matching_rule():
    state = State.fresh(session_id='s')
    rules = [
        (lambda s, g: False, lambda s, g: Action(kind='llm_call', payload={}), 'rule_a'),
        (lambda s, g: True,  lambda s, g: Action(kind='llm_call', payload={'prompt': 'B'}), 'rule_b'),
        (lambda s, g: True,  lambda s, g: Action(kind='llm_call', payload={'prompt': 'C'}), 'rule_c'),
    ]
    decision = RuleBasedController(rules).pick(state)
    assert decision is not None
    assert decision.chose.payload['prompt'] == 'B'
    assert decision.rationale == 'rule_fired: rule_b'
    assert decision.decided_by == 'rule'


def test_rule_based_returns_none_when_no_rule_matches():
    state = State.fresh(session_id='s')
    rules = [
        (lambda s, g: False, lambda s, g: Action(kind='llm_call', payload={}), 'never'),
    ]
    assert RuleBasedController(rules).pick(state) is None


def test_rule_based_skips_rule_whose_predicate_raises():
    state = State.fresh(session_id='s')
    def boom(s, g): raise RuntimeError('oops')
    rules = [
        (boom, lambda s, g: Action(kind='llm_call', payload={}), 'broken'),
        (lambda s, g: True, lambda s, g: Action(kind='llm_call', payload={'prompt': 'OK'}), 'good'),
    ]
    decision = RuleBasedController(rules).pick(state)
    assert decision is not None
    assert decision.rationale == 'rule_fired: good'


def test_rule_based_skips_rule_whose_factory_returns_none():
    state = State.fresh(session_id='s')
    rules = [
        (lambda s, g: True, lambda s, g: None, 'returns_none'),
        (lambda s, g: True, lambda s, g: Action(kind='llm_call', payload={'prompt': 'X'}), 'second'),
    ]
    decision = RuleBasedController(rules).pick(state)
    assert decision is not None
    assert decision.rationale == 'rule_fired: second'


# ---- FallbackController composition ---------------------------------------

def test_fallback_uses_primary_when_primary_fires():
    primary_action = Action(kind='llm_call', payload={'prompt': 'primary'})
    fallback_action = Action(kind='llm_call', payload={'prompt': 'fallback'})
    fc = FallbackController(
        primary=FixedActionController(primary_action),
        fallback=FixedActionController(fallback_action),
    )
    decision = fc.pick(State.fresh(session_id='s'))
    assert decision.chose.payload['prompt'] == 'primary'


def test_fallback_uses_fallback_when_primary_returns_none():
    fallback_action = Action(kind='llm_call', payload={'prompt': 'rescue'})
    fc = FallbackController(
        primary=HaltController(),  # always None
        fallback=FixedActionController(fallback_action),
    )
    decision = fc.pick(State.fresh(session_id='s'))
    assert decision is not None
    assert decision.chose.payload['prompt'] == 'rescue'


def test_fallback_returns_none_when_both_return_none():
    fc = FallbackController(primary=HaltController(), fallback=HaltController())
    assert fc.pick(State.fresh(session_id='s')) is None


# ---- run_until_done(controller=) integration ------------------------------

def test_run_until_done_with_controller_logs_rationale_and_decided_by(tmp_path):
    log_path = tmp_path / 'log.jsonl'
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=log_path,
        evaluators=[BudgetExhaustionEvaluator()],
    )
    s = State.fresh(session_id='s', budget_usd=1.0)
    rules = [
        (lambda s, g: True,
         lambda s, g: Action(kind='llm_call', payload={'prompt': 'hi'}),
         'always_say_hi'),
    ]
    primary = RuleBasedController(rules)
    fallback = HaltController()
    controller = FallbackController(primary, fallback)

    # Cap to 1 turn via supplier-style halt: after first turn, primary will
    # still fire but we want to ensure the log carries the rule's rationale.
    final_state, result = runner.run_until_done(
        s, controller=controller, max_turns=1,
    )
    # max_turns=1 means we ran exactly one step then hit timeout
    assert result.verdict == 'timeout'
    line = log_path.read_text().strip()
    rec = json.loads(line)
    assert rec['decision']['rationale'] == 'rule_fired: always_say_hi'
    assert rec['decision']['decided_by'] == 'rule'


def test_run_until_done_requires_exactly_one_of_controller_or_supplier(tmp_path):
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=tmp_path / 'log.jsonl',
    )
    s = State.fresh(session_id='s', budget_usd=1.0)
    # Both provided → error
    with pytest.raises(ValueError, match='exactly one'):
        runner.run_until_done(
            s,
            action_supplier=lambda _state: None,
            controller=HaltController(),
        )
    # Neither provided → error
    with pytest.raises(ValueError, match='exactly one'):
        runner.run_until_done(s)


def test_halt_controller_emits_done_verdict_immediately(tmp_path):
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=tmp_path / 'log.jsonl',
    )
    s = State.fresh(session_id='s', budget_usd=1.0)
    _, result = runner.run_until_done(s, controller=HaltController(), max_turns=10)
    assert result.verdict == 'done'
    assert "controller 'halt' returned None" in result.note


def test_decided_by_propagates_through_fallback_chain(tmp_path):
    """When the fallback fires, its decided_by label should be in the log."""

    class LLMStubController:
        @property
        def name(self):
            return 'llm_stub'

        def pick(self, state, goal=None):
            return PolicyDecision(
                at_state_turn_id=state.turn_id,
                chose=Action(kind='llm_call', payload={'prompt': 'from-llm'}),
                rationale='LLM picked this',
                decided_by='llm',
                confidence=0.5,
            )

    log_path = tmp_path / 'log.jsonl'
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=log_path,
    )
    s = State.fresh(session_id='s', budget_usd=1.0)
    fc = FallbackController(primary=HaltController(), fallback=LLMStubController())
    runner.run_until_done(s, controller=fc, max_turns=1)
    rec = json.loads(log_path.read_text().strip().splitlines()[0])
    assert rec['decision']['decided_by'] == 'llm'
    assert rec['decision']['rationale'] == 'LLM picked this'
