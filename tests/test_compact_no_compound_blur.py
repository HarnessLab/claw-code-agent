"""Multi-tier protection: compact summaries don't compound-blur.

Today (after commits 459cd14 + 53049c6 + this) the compact_boundary +
compact_summary messages from a prior compaction get re-summarized when
the next compaction fires, because they're not in the prefix range and
they're not anchored. Result: lossy compounding — content originally
summarized at depth 1 gets summarized again at depth 2, then 3, …

Fix: extend the prefix detection in compact_conversation to count BOTH
'compact_boundary' AND 'compact_summary' messages as the protected
prefix, so prior compaction artifacts pass through subsequent
compactions verbatim.

The user-visible win: after N compactions you have a chronological
stack of summaries (oldest first, newest last) plus the verbatim tail,
instead of a single increasingly-blurry summary. This is the simple
analog of DeepSeek's HCA layers — heavy compression of distant past,
preserved (not re-compressed) when the model revisits.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.agent_runtime import LocalCodingAgent
from src.agent_session import AgentMessage, AgentSessionState
from src.agent_types import AgentRuntimeConfig, ModelConfig, UsageStats
from src.compact import compact_conversation
from src.openai_compat import AssistantTurn


def _summary_turn(text: str) -> AssistantTurn:
    return AssistantTurn(
        content=f'<summary>{text}</summary>',
        tool_calls=(),
        finish_reason='stop',
        raw_message={},
        usage=UsageStats(),
    )


def _user(content: str, mid: str) -> AgentMessage:
    return AgentMessage(role='user', content=content, message_id=mid)


class TestNoCompoundBlur(unittest.TestCase):
    def test_first_summary_survives_second_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='test-model'),
                runtime_config=AgentRuntimeConfig(
                    cwd=Path(tmp), compact_preserve_messages=2,
                ),
            )
            # First conversation: 8 messages
            agent.last_session = AgentSessionState(
                system_prompt_parts=('hi',),
                messages=[_user(f'first round msg {i}', f'a{i}') for i in range(8)],
            )
            agent.client = MagicMock()

            # First compaction
            agent.client.complete.return_value = _summary_turn('FIRST_ROUND_DETAILS')
            r1 = compact_conversation(agent)
            self.assertIsNone(r1.error, f'first compaction failed: {r1.error}')

            # Add more messages and compact again
            for i in range(6):
                agent.last_session.append_user(f'second round msg {i}')

            agent.client.complete.return_value = _summary_turn('SECOND_ROUND_DETAILS')
            r2 = compact_conversation(agent)
            self.assertIsNone(r2.error, f'second compaction failed: {r2.error}')

            # The FIRST round's summary content must still be present
            # verbatim — not re-summarized into a single blurrier summary.
            all_content = '\n'.join(m.content for m in agent.last_session.messages)
            self.assertIn(
                'FIRST_ROUND_DETAILS', all_content,
                f'first compaction content was re-summarized into oblivion. '
                f'Session contents: {all_content[:500]}',
            )
            self.assertIn(
                'SECOND_ROUND_DETAILS', all_content,
                'second compaction content missing',
            )

    def test_chronological_order_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='test-model'),
                runtime_config=AgentRuntimeConfig(
                    cwd=Path(tmp), compact_preserve_messages=2,
                ),
            )
            agent.last_session = AgentSessionState(
                system_prompt_parts=('hi',),
                messages=[_user(f'r1 {i}', f'a{i}') for i in range(8)],
            )
            agent.client = MagicMock()

            agent.client.complete.return_value = _summary_turn('FIRST')
            compact_conversation(agent)

            for i in range(6):
                agent.last_session.append_user(f'r2 {i}')

            agent.client.complete.return_value = _summary_turn('SECOND')
            compact_conversation(agent)

            # Find positions of 'FIRST' and 'SECOND' in the session
            contents = [m.content for m in agent.last_session.messages]
            first_idx = next(
                i for i, c in enumerate(contents) if 'FIRST' in c
            )
            second_idx = next(
                i for i, c in enumerate(contents) if 'SECOND' in c
            )
            self.assertLess(
                first_idx, second_idx,
                f'oldest summary should appear before newest; '
                f'got FIRST@{first_idx}, SECOND@{second_idx} in {contents}',
            )


if __name__ == '__main__':
    unittest.main()
