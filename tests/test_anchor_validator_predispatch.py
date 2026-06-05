"""(a) Pre-dispatch block for constitution-grade NEVER violations.

The post-execution warn (commit e34a7bc) surfaces an anchor violation
AFTER the bash command has already run — for `rm -rf production-data`
that means the data is gone before the warning lands in the policy log.
This adds a pre-dispatch check that BLOCKS the action before the
operator runs, but only for high-risk command patterns AND only when
an anchored NEVER constraint mentions related concepts.

Block-severity is intentionally narrow:
  - Soft-warn surface (post-execute, severity='warn'): unchanged. Any
    NEVER anchor whose tokens overlap the command.
  - Hard-block surface (pre-dispatch, severity='block'): only fires
    when both (a) the command matches a HIGH_RISK_PATTERN and (b) a
    NEVER anchor mentions overlapping concepts. Constitution-grade
    static patterns (rm -rf /, git push --force main) remain handled
    by violates_constitutional_wall — that surface is anchor-agnostic.

The two surfaces are complementary:
  - Constitutional wall: static patterns, no session context.
  - Anchor pre-block: session-derived, fires when user-typed NEVER
    constraints intersect a high-risk pattern.
"""
from __future__ import annotations

import unittest

from src.agent_state_machine import Action, Observation
from src.state_machine_validators import AnchorViolationValidator


def _bash_action(command: str) -> Action:
    return Action(
        kind='tool_call',
        payload={'tool_name': 'bash', 'arguments': {'command': command}},
    )


class TestAnchorPreDispatchBlock(unittest.TestCase):
    def test_high_risk_command_with_never_anchor_blocks(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: delete production data'],
        )
        action = _bash_action('rm -rf /var/lib/production-data')
        result = v.pre_validate(action)
        self.assertIsNotNone(result, 'pre_validate must return a block result')
        self.assertEqual(result.severity, 'block')
        self.assertFalse(result.passed)
        evidence = ' '.join(c.evidence for c in result.checks)
        self.assertIn('production', evidence.lower())

    def test_high_risk_command_without_anchor_passes_predispatch(self) -> None:
        # No NEVER anchor → pre_validate returns None (no block).
        # Constitutional wall is a separate surface that may or may not
        # fire depending on the static pattern.
        v = AnchorViolationValidator(anchors_provider=lambda: [])
        action = _bash_action('rm -rf /var/lib/production-data')
        result = v.pre_validate(action)
        self.assertIsNone(result, 'no anchors → no pre-dispatch block')

    def test_low_risk_command_with_anchor_passes_predispatch(self) -> None:
        # Anchor matches via word-overlap but command is not high-risk.
        # Pre-dispatch returns None; post-execute warn still fires.
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: delete production data'],
        )
        action = _bash_action('echo "delete production data is dangerous"')
        self.assertIsNone(v.pre_validate(action))

    def test_force_push_to_main_with_never_anchor_blocks(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: force push to main branch'],
        )
        action = _bash_action('git push --force origin main')
        result = v.pre_validate(action)
        self.assertIsNotNone(result)
        self.assertEqual(result.severity, 'block')

    def test_force_push_to_branch_other_than_main_passes(self) -> None:
        # High-risk pattern requires main/master specifically. A force push
        # to a feature branch is not in the high-risk list.
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: force push to main branch'],
        )
        action = _bash_action('git push --force origin feature-x')
        self.assertIsNone(v.pre_validate(action))

    def test_safe_command_with_anchor_passes_predispatch(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: rm -rf production data'],
        )
        action = _bash_action('ls -la /tmp')
        self.assertIsNone(v.pre_validate(action))

    def test_pre_validate_only_applies_to_bash(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: anything'],
        )
        non_bash = Action(
            kind='tool_call',
            payload={'tool_name': 'read_file', 'arguments': {'path': '/etc/passwd'}},
        )
        self.assertIsNone(v.pre_validate(non_bash))

    def test_anchors_provider_failure_does_not_crash_pre_validate(self) -> None:
        def boom():
            raise RuntimeError('provider down')
        v = AnchorViolationValidator(anchors_provider=boom)
        action = _bash_action('rm -rf /var/lib/production-data')
        # Must not raise; degrade to None (no block).
        self.assertIsNone(v.pre_validate(action))


class TestRunnerHonorsPreDispatchBlock(unittest.TestCase):
    """Runner's run_one_step must call pre_validate before op.execute.

    On block-severity, the operator must NOT execute and the runner
    must return an error Observation referencing the violation.
    """

    def test_runner_skips_execute_on_pre_dispatch_block(self) -> None:
        from src.agent_state_machine import State, Operator
        from src.state_machine_runner import StateMachineRunner

        executed: list[str] = []

        class _RecordingBashOp:
            kind = 'tool_call'
            def can_handle(self, action: Action) -> bool:
                return action.payload.get('tool_name') == 'bash'
            def execute(self, action: Action, state: State) -> Observation:
                executed.append(action.payload.get('arguments', {}).get('command', ''))
                return Observation(
                    action_id=action.id, kind='success',
                    payload={'tool_name': 'bash', 'ok': True, 'content': 'ran'},
                )

        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: delete production data'],
        )
        runner = StateMachineRunner(
            operators=[_RecordingBashOp()],
            validators=[v],
            decision_log_path=None,
        )
        action = _bash_action('rm -rf /var/lib/production-data')
        state = State(session_id='s', turn_id='t1')
        obs, _new_state = runner.run_one_step(state, action)

        self.assertEqual(executed, [], 'operator must NOT execute on pre-dispatch block')
        self.assertEqual(obs.kind, 'error')
        self.assertIn('blocked', str(obs.payload).lower())


if __name__ == '__main__':
    unittest.main()
