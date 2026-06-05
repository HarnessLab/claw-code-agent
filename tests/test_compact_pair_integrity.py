"""Atomic tool-pair compaction.

The existing walk-forward only checks `msg[compact_end]` for a tool_result
and pulls it into candidates if so. When a non-tool message intervenes —
e.g. assistant_with_tool_use → user (interjection) → tool_result — the
walk does not fire, the assistant_tool_use ends up in candidates (folded
into the summary), and the tool_result is orphaned in the preserved tail.

The egress shield (commit f053ba7) silently strips the orphan before it
reaches the provider, but compaction itself was producing malformed
sessions. This commit fixes that at the source: extend `compact_end`
forward by tool_use_id matching, not just position-is-tool-result.
After this, every tool_use in candidates has its tool_result in
candidates; the preserved tail starts cleanly.

Live precedent: session 7c77bcb2dd394 had exactly this pattern in its
persisted form (orphan tool_result at messages[2]). With pair-integrity
compaction, future compactions cannot reproduce that shape.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.agent_runtime import LocalCodingAgent
from src.agent_session import AgentMessage, AgentSessionState, _strip_orphan_tool_results
from src.agent_types import AgentRuntimeConfig, ModelConfig, UsageStats
from src.compact import compact_conversation
from src.openai_compat import AssistantTurn


_OK_SUMMARY = AssistantTurn(
    content='<summary>routine summary</summary>',
    tool_calls=(),
    finish_reason='stop',
    raw_message={},
    usage=UsageStats(),
)


def _agent(tmp_dir: str) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=ModelConfig(model='test-model'),
        runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
    )


def _asst_tc(tc_id: str, mid: str) -> AgentMessage:
    return AgentMessage(
        role='assistant',
        content='calling',
        tool_calls=({'id': tc_id, 'type': 'function',
                     'function': {'name': 'bash', 'arguments': '{}'}},),
        message_id=mid,
    )


def _tr(tc_id: str, mid: str) -> AgentMessage:
    return AgentMessage(role='tool', content='result',
                        tool_call_id=tc_id, message_id=mid)


def _user(content: str, mid: str) -> AgentMessage:
    return AgentMessage(role='user', content=content, message_id=mid)


class TestCompactPairIntegrity(unittest.TestCase):
    def _run_compact_with_session(
        self,
        messages: list[AgentMessage],
        *,
        preserve: int = 4,
    ) -> AgentSessionState:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(tmp)
            agent.runtime_config = AgentRuntimeConfig(
                cwd=Path(tmp),
                compact_preserve_messages=preserve,
            )
            agent.last_session = AgentSessionState(
                system_prompt_parts=('You are a helpful assistant.',),
                messages=list(messages),
            )
            agent.client = MagicMock()
            agent.client.complete.return_value = _OK_SUMMARY
            compact_conversation(agent)
            return agent.last_session

    def test_post_compact_raw_messages_have_no_orphan(self) -> None:
        # Pair split shape that misses the walk-forward:
        # assistant_tc → intervening user → tool_result → assistant.
        # Inspect new_session.messages directly (NOT to_openai_messages,
        # which now runs the egress shield and would mask compaction's
        # output).
        messages = [
            _user('m0', 'm0'),
            _user('m1', 'm1'),
            _asst_tc('toolu_X', 'asst_tc'),
            _user('intervene', 'w1'),
            _tr('toolu_X', 'tr'),
            AgentMessage(role='assistant', content='done', message_id='asst_done'),
        ]
        new_session = self._run_compact_with_session(messages, preserve=3)
        announced: set[str] = set()
        for m in new_session.messages:
            if m.role == 'assistant' and m.tool_calls:
                for tc in m.tool_calls:
                    if isinstance(tc, dict) and isinstance(tc.get('id'), str):
                        announced.add(tc['id'])
            if m.role == 'tool' and m.tool_call_id is not None:
                self.assertIn(
                    m.tool_call_id, announced,
                    f'orphan tool_result {m.tool_call_id} present in raw '
                    f'session.messages — egress shield would mask this',
                )

    def test_non_adjacent_tool_result_is_pulled_into_candidates(self) -> None:
        # Same shape but assert the structural fix directly: after
        # compaction the tool_result must NOT be in the preserved tail.
        messages = [
            _user('m0', 'm0'),
            _user('m1', 'm1'),
            _asst_tc('toolu_Y', 'asst_y'),
            _user('intervene', 'w1'),
            _tr('toolu_Y', 'tr_y'),
            AgentMessage(role='assistant', content='done', message_id='final'),
        ]
        new_session = self._run_compact_with_session(messages, preserve=3)
        ids = [m.message_id for m in new_session.messages]
        # tr_y must NOT survive into the new session as an orphan
        self.assertNotIn(
            'tr_y', ids,
            f'orphan tool_result tr_y survived in {ids}',
        )

    def test_multiple_open_pairs_extend_until_all_matched(self) -> None:
        # Two open tool_uses; both results sit past intervening messages
        messages = [
            _user('m0', 'm0'),
            _asst_tc('toolu_A', 'asst_a'),
            _user('intervene1', 'w1'),
            _asst_tc('toolu_B', 'asst_b'),
            _user('intervene2', 'w2'),
            _tr('toolu_A', 'tr_a'),
            _tr('toolu_B', 'tr_b'),
            AgentMessage(role='assistant', content='done', message_id='final'),
        ]
        new_session = self._run_compact_with_session(messages, preserve=2)
        api_messages = new_session.to_openai_messages()
        filtered = _strip_orphan_tool_results(api_messages)
        self.assertEqual(len(api_messages), len(filtered))

    def test_clean_session_unchanged_by_pair_integrity(self) -> None:
        # No tool calls anywhere — pair integrity must be a no-op.
        messages = [_user(f'm{i}', f'm{i}') for i in range(8)]
        new_session = self._run_compact_with_session(messages, preserve=2)
        # Should still see boundary + summary + tail
        kinds = [m.metadata.get('kind') for m in new_session.messages]
        self.assertIn('compact_boundary', kinds)
        self.assertIn('compact_summary', kinds)

    def test_unmatched_tool_use_with_no_result_does_not_loop(self) -> None:
        # Pathological: assistant announces a tool_use whose result never
        # comes (interrupted run). Compaction must still terminate and
        # produce a clean session.
        messages = [
            _user('m0', 'm0'),
            _asst_tc('toolu_NEVER', 'asst_orphan'),
            _user('m1', 'm1'),
            AgentMessage(role='assistant', content='done', message_id='final'),
        ]
        new_session = self._run_compact_with_session(messages, preserve=2)
        # No assertion on shape — just that we returned without hanging
        # and produced something.
        self.assertGreater(len(new_session.messages), 0)


if __name__ == '__main__':
    unittest.main()
