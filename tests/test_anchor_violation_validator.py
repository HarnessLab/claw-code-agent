"""Summary→active-constraint: validator surfaces anchor violations.

Anchored MISSION/CORRECTION/NEVER messages survive compaction (commits
459cd14 + 048309b + 59318ff). They are visible to the LLM as context.
But they are PASSIVE — the LLM can ignore them and the State layer
doesn't know it happened.

This validator turns one class of anchor — NEVER: constraints — into
an ACTIVE constraint. When a bash tool action is dispatched, the
validator inspects the session's anchored messages, extracts NEVER:
constraints, and compares each constraint's token set against the
bash command. If overlap exceeds a threshold, the validator returns
severity='warn' and surfaces the matched constraint in its evidence.

This is the smallest meaningful first cut at the user's framing:
"summary as active constraint, not passive history." Future expansion:
block-severity for hard walls (rm -rf /, force-push main), LLM-judge
for fuzzy matching, OR-of-anchors instead of AND-of-tokens.
"""
from __future__ import annotations

import unittest

from src.agent_state_machine import Action, Observation
from src.state_machine_validators import AnchorViolationValidator


class TestAnchorViolationValidator(unittest.TestCase):
    def _bash_action(self, command: str) -> Action:
        return Action(
            kind='tool_call',
            payload={'tool_name': 'bash', 'arguments': {'command': command}},
        )

    def _success_obs(self, action: Action) -> Observation:
        return Observation(
            action_id=action.id, kind='success',
            payload={'tool_name': 'bash', 'ok': True, 'content': '...'},
        )

    def test_no_anchors_passes(self) -> None:
        v = AnchorViolationValidator(anchors_provider=lambda: [])
        action = self._bash_action('rm -rf /tmp/test')
        result = v.validate(action, self._success_obs(action))
        self.assertTrue(result.passed)
        self.assertEqual(result.severity, 'info')

    def test_unrelated_anchor_passes(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: commit secrets'],
        )
        action = self._bash_action('ls -la')
        result = v.validate(action, self._success_obs(action))
        self.assertTrue(result.passed)

    def test_anchor_violation_warns(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: rm -rf production data'],
        )
        action = self._bash_action('rm -rf /var/lib/production/data')
        result = v.validate(action, self._success_obs(action))
        self.assertFalse(result.passed)
        self.assertEqual(result.severity, 'warn')
        all_evidence = ' '.join(c.evidence for c in result.checks)
        self.assertIn('rm', all_evidence)

    def test_non_never_anchor_not_enforced(self) -> None:
        # Only NEVER: prefixes are enforced. MISSION/IMPORTANT etc. are
        # advisory — they shape the LLM's context but don't generate
        # validator warnings on tool calls.
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['MISSION: rm -rf the build artifacts'],
        )
        action = self._bash_action('rm -rf /var/log/old')
        result = v.validate(action, self._success_obs(action))
        self.assertTrue(result.passed)

    def test_multiple_anchors_one_matches(self) -> None:
        v = AnchorViolationValidator(
            anchors_provider=lambda: [
                'MISSION: build the long-context layer',
                'NEVER: force push to main branch',
                'IMPORTANT: write tests first',
            ],
        )
        action = self._bash_action('git push --force origin main')
        result = v.validate(action, self._success_obs(action))
        self.assertEqual(result.severity, 'warn')
        all_evidence = ' '.join(c.evidence for c in result.checks)
        self.assertIn('force', all_evidence)

    def test_only_applies_to_bash_tool_calls(self) -> None:
        # Other tool kinds (read_file, write_file) are not bash; skip.
        v = AnchorViolationValidator(
            anchors_provider=lambda: ['NEVER: read secret files'],
        )
        non_bash = Action(
            kind='tool_call',
            payload={'tool_name': 'read_file', 'arguments': {'path': '/tmp/secret'}},
        )
        self.assertFalse(v.applies_to(non_bash))

    def test_anchor_provider_failure_does_not_crash(self) -> None:
        def boom():
            raise RuntimeError('anchors backing store unavailable')
        v = AnchorViolationValidator(anchors_provider=boom)
        action = self._bash_action('ls')
        # Validator must not raise; degrades to pass.
        result = v.validate(action, self._success_obs(action))
        self.assertTrue(result.passed)


if __name__ == '__main__':
    unittest.main()
