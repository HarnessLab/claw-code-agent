"""Auto-anchor user messages on keyword triggers.

The anchor mechanism (commit 459cd14) lets messages survive compaction
verbatim, but it has no callers. This wires a heuristic into the single
chokepoint AgentSessionState.append_user(): when a user message starts
with a load-bearing prefix — MISSION:, CORRECTION:, IMPORTANT:, NEVER:,
ALWAYS: — auto-set metadata['anchor']=True. Case-insensitive, must be
at the start of a line, and only when the caller hasn't explicitly set
the anchor flag.

Falsifier: a routine message ('let me check that') is NOT anchored.
"""
from __future__ import annotations

import unittest

from src.agent_session import AgentSessionState


def _empty_session() -> AgentSessionState:
    return AgentSessionState(system_prompt_parts=())


class TestAppendUserAutoAnchor(unittest.TestCase):
    def test_mission_keyword_anchors(self) -> None:
        s = _empty_session()
        s.append_user('MISSION: ship the long-context memory layer')
        self.assertEqual(len(s.messages), 1)
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_correction_keyword_anchors_case_insensitive(self) -> None:
        s = _empty_session()
        s.append_user('Correction: stop summarizing — just answer')
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_important_keyword_anchors(self) -> None:
        s = _empty_session()
        s.append_user('IMPORTANT: every commit needs a falsifier')
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_never_keyword_anchors(self) -> None:
        s = _empty_session()
        s.append_user('NEVER: force-push to main')
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_always_keyword_anchors(self) -> None:
        s = _empty_session()
        s.append_user('ALWAYS: write a regression test before fixing a bug')
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_keyword_not_at_line_start_does_not_anchor(self) -> None:
        s = _empty_session()
        s.append_user('the user said MISSION: foo earlier in the chat')
        self.assertFalse(s.messages[0].metadata.get('anchor'))

    def test_routine_message_not_anchored(self) -> None:
        s = _empty_session()
        s.append_user('let me check the file')
        self.assertFalse(s.messages[0].metadata.get('anchor'))

    def test_explicit_anchor_true_respected(self) -> None:
        # Caller explicitly anchors a routine message — heuristic must
        # not silently override.
        s = _empty_session()
        s.append_user('routine text', metadata={'anchor': True})
        self.assertTrue(s.messages[0].metadata.get('anchor'))

    def test_explicit_anchor_false_respected(self) -> None:
        # Caller explicitly opts out even though keyword would trigger —
        # heuristic must respect.
        s = _empty_session()
        s.append_user('MISSION: foo', metadata={'anchor': False})
        self.assertFalse(s.messages[0].metadata.get('anchor'))

    def test_anchor_keyword_at_start_of_later_line_anchors(self) -> None:
        # MISSION at the start of any line in a multi-line message counts.
        s = _empty_session()
        s.append_user('hey there\nMISSION: build it')
        self.assertTrue(s.messages[0].metadata.get('anchor'))


if __name__ == '__main__':
    unittest.main()
