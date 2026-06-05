"""Tests for the post-Observation Validator pipeline.

Step 3 of the runway in ``~/.latti/STATE_MACHINE.md``: validators run after
each Observation. Block-severity results replace the Observation with an
error variant so the loop can branch on it; warn/info pass through.
"""
from __future__ import annotations

import json

import pytest

from src.agent_state_machine import (
    Action,
    Observation,
    State,
    Validator,
    ValidationCheck,
    ValidationResult,
)
from src.state_machine_operators import (
    EchoLLMOperator,
    JSONSchemaValidator,
    ReadFileOperator,
)
from src.state_machine_runner import StateMachineRunner
from src.state_machine_validators import (
    BudgetValidator,
    NonEmptyContentValidator,
    ObservationShapeValidator,
)


@pytest.fixture
def fresh_state():
    return State.fresh(session_id='val_test', budget_usd=1.0)


def _runner_with(validators, tmp_path, decision_log='log.jsonl'):
    return StateMachineRunner(
        operators=[ReadFileOperator(), EchoLLMOperator(), JSONSchemaValidator()],
        decision_log_path=tmp_path / decision_log,
        validators=validators,
    )


# ---- Protocol satisfaction -------------------------------------------------

def test_observation_shape_validator_satisfies_protocol():
    v = ObservationShapeValidator()
    assert isinstance(v, Validator)
    assert v.name == 'observation_shape'


def test_budget_validator_satisfies_protocol():
    v = BudgetValidator(max_cost_per_step_usd=0.05)
    assert isinstance(v, Validator)


def test_non_empty_content_validator_satisfies_protocol():
    v = NonEmptyContentValidator()
    assert isinstance(v, Validator)


# ---- ObservationShapeValidator semantics -----------------------------------

def test_observation_shape_validator_passes_clean_tool_call(fresh_state, tmp_path):
    runner = _runner_with([ObservationShapeValidator()], tmp_path)
    f = tmp_path / 'x.txt'
    f.write_text('hi')
    a = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(f)})
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'success'
    # No 'blocking_validations' key — passed cleanly
    assert 'blocking_validations' not in obs.payload


def test_observation_shape_validator_blocks_on_action_id_mismatch(fresh_state, tmp_path):
    """If an Operator returns an Observation referencing a different action_id,
    that's a contract violation — must block."""

    class MisidentifyingOp:
        @property
        def kind(self):
            return 'tool_call'

        def can_handle(self, action):
            return action.kind == 'tool_call'

        def execute(self, action, state):
            # WRONG: returning a different action_id than what was passed
            return Observation(action_id='wrong_id', kind='success',
                               payload={'content': 'x', 'ok': True})

    runner = StateMachineRunner(
        operators=[MisidentifyingOp()],
        decision_log_path=tmp_path / 'log.jsonl',
        validators=[ObservationShapeValidator()],
    )
    a = Action(kind='tool_call', payload={'tool_name': 'whatever'})
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert 'blocking_validations' in obs.payload
    assert any('action_id_continuity' in c['name']
               for v in obs.payload['blocking_validations']
               for c in v['checks'])


def test_observation_shape_validator_accepts_real_llm_payload_shape():
    v = ObservationShapeValidator()
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'hi'}]})
    obs = Observation(
        action_id=a.id,
        kind='success',
        payload={
            'content': 'hello',
            'tool_calls': [],
            'finish_reason': 'stop',
        },
    )

    result = v.validate(a, obs)

    assert result.passed is True
    assert result.severity == 'info'


# ---- BudgetValidator semantics ---------------------------------------------

def test_budget_validator_blocks_when_observation_exceeds_per_step_cap(fresh_state, tmp_path):
    """Stub LLM operator with elevated cost via custom op."""

    class ExpensiveOp:
        @property
        def kind(self):
            return 'llm_call'

        def can_handle(self, action):
            return action.kind == 'llm_call'

        def execute(self, action, state):
            return Observation(action_id=action.id, kind='success',
                               payload={'completion': 'ok'}, cost_usd=5.0)

    runner = StateMachineRunner(
        operators=[ExpensiveOp()],
        decision_log_path=tmp_path / 'log.jsonl',
        validators=[BudgetValidator(max_cost_per_step_usd=1.0)],
    )
    a = Action(kind='llm_call', payload={'prompt': 'hi'})
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert 'blocking_validations' in obs.payload


def test_budget_validator_passes_when_under_cap(fresh_state, tmp_path):
    runner = _runner_with([BudgetValidator(max_cost_per_step_usd=1.0)], tmp_path)
    a = Action(kind='llm_call', payload={'prompt': 'cheap'})
    obs, _ = runner.run_one_step(fresh_state, a)
    # EchoLLMOperator returns cost_usd=0.0 by default
    assert obs.kind == 'success'


# ---- NonEmptyContentValidator semantics ------------------------------------

def test_non_empty_content_passes_when_content_present(fresh_state, tmp_path):
    runner = _runner_with([NonEmptyContentValidator()], tmp_path)
    f = tmp_path / 'has_content.txt'
    f.write_text('real content here')
    a = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(f)})
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'success'


def test_non_empty_content_warns_but_does_not_block_on_empty_content(fresh_state, tmp_path):
    """warn-severity validators must NOT replace the Observation."""
    runner = _runner_with([NonEmptyContentValidator()], tmp_path)
    f = tmp_path / 'empty.txt'
    f.write_text('')  # empty file → empty content
    a = Action(kind='tool_call', payload={'tool_name': 'read_file', 'path': str(f)})
    obs, _ = runner.run_one_step(fresh_state, a)
    # Original Observation passes through (warn != block)
    assert obs.kind == 'success'
    assert 'blocking_validations' not in obs.payload


# ---- Multiple validators interaction ---------------------------------------

def test_any_blocking_validator_blocks_observation(fresh_state, tmp_path):
    """When multiple validators are registered, ANY blocker should block."""

    class AlwaysBlockValidator:
        @property
        def name(self):
            return 'always_block'

        def applies_to(self, action):
            return True

        def validate(self, action, observation):
            return ValidationResult(
                action_id=action.id, passed=False,
                checks=(ValidationCheck(name='always_block', passed=False,
                                        evidence='intentional'),),
                severity='block',
            )

    runner = _runner_with(
        [ObservationShapeValidator(), AlwaysBlockValidator()],
        tmp_path,
    )
    a = Action(kind='llm_call', payload={'prompt': 'doomed'})
    obs, _ = runner.run_one_step(fresh_state, a)
    assert obs.kind == 'error'
    assert 'blocking_validations' in obs.payload
    # Original observation is preserved in payload for debugging
    assert 'original_observation' in obs.payload


def test_validation_results_recorded_in_decision_log(fresh_state, tmp_path):
    log_path = tmp_path / 'pdlog.jsonl'
    runner = StateMachineRunner(
        operators=[EchoLLMOperator()],
        decision_log_path=log_path,
        validators=[ObservationShapeValidator()],
    )
    a = Action(kind='llm_call', payload={'prompt': 'logged'})
    runner.run_one_step(fresh_state, a)
    line = log_path.read_text().strip()
    rec = json.loads(line)
    assert 'validations' in rec
    assert len(rec['validations']) == 1
    assert rec['validations'][0]['action_id'] == a.id
