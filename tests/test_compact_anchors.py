"""Anchor sinks: messages opted out of compaction.

Today the compaction summarizer treats every message in [prefix, compact_end)
uniformly. Mission directives, hard user corrections, and load-bearing
decisions get folded into the same 9-section summary as routine output —
and on the second compaction they get summarized again, compounding loss.

DeepSeek V4's transformer attention has explicit "sink logits" — slots
the model always attends to. The message-layer analog is an `anchor`
metadata flag: messages so marked are excluded from the summarizer
input AND survive the rebuild verbatim.

Anchors live AFTER the boundary+summary and BEFORE the preserved tail,
so they read like persistent system reminders re-injected on every turn.
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


_OK_SUMMARY = AssistantTurn(
    content=(
        '<analysis>routine</analysis>\n'
        '<summary>\n1. Primary Request and Intent: testing.\n'
        '2. Key Technical Concepts: anchors.\n'
        '3. Files and Code Sections: none.\n'
        '4. Errors and fixes: none.\n'
        '5. Problem Solving: trivial.\n'
        '6. All user messages: anchor test.\n'
        '7. Pending Tasks: none.\n'
        '8. Current Work: anchor test.\n'
        '9. Optional Next Step: ship.\n</summary>'
    ),
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


def _msg(role: str, content: str, *, anchor: bool = False, mid: str = '') -> AgentMessage:
    return AgentMessage(
        role=role,
        content=content,
        message_id=mid or f'{role}_msg',
        metadata={'anchor': True} if anchor else {},
    )


class TestAnchorSinks(unittest.TestCase):
    def test_anchored_message_survives_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(tmp)
            messages = [
                _msg('user',      f'routine {i}', mid=f'm{i}') for i in range(8)
            ]
            messages[3] = _msg(
                'user',
                'MISSION: build the long-context memory layer',
                anchor=True,
                mid='mission_anchor',
            )
            agent.last_session = AgentSessionState(
                system_prompt_parts=('You are a helpful assistant.',),
                messages=list(messages),
            )
            agent.client = MagicMock()
            agent.client.complete.return_value = _OK_SUMMARY

            result = compact_conversation(agent)

        self.assertIsNone(result.error)
        survived = [
            m for m in agent.last_session.messages
            if m.metadata.get('anchor') is True
        ]
        self.assertEqual(len(survived), 1)
        self.assertEqual(
            survived[0].content,
            'MISSION: build the long-context memory layer',
        )

    def test_anchored_messages_excluded_from_summarizer_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(tmp)
            messages = [_msg('user', f'routine {i}', mid=f'm{i}') for i in range(8)]
            messages[2] = _msg(
                'user',
                'NEVER COMPACT: this is the mission',
                anchor=True,
                mid='anchor',
            )
            agent.last_session = AgentSessionState(
                system_prompt_parts=('You are a helpful assistant.',),
                messages=list(messages),
            )
            agent.client = MagicMock()
            agent.client.complete.return_value = _OK_SUMMARY

            compact_conversation(agent)

            # Inspect what was sent to the LLM
            call_args = agent.client.complete.call_args
            api_messages = call_args[0][0] if call_args.args else call_args.kwargs['messages']
            sent_contents = [m.get('content', '') for m in api_messages]

        self.assertFalse(
            any('NEVER COMPACT' in c for c in sent_contents),
            f'anchored content leaked into summarizer input: {sent_contents}',
        )

    def test_multiple_anchors_preserved_in_original_relative_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(tmp)
            messages = [_msg('user', f'routine {i}', mid=f'm{i}') for i in range(10)]
            messages[1] = _msg('user', 'ANCHOR-A first',  anchor=True, mid='a')
            messages[4] = _msg('user', 'ANCHOR-B second', anchor=True, mid='b')
            messages[6] = _msg('user', 'ANCHOR-C third',  anchor=True, mid='c')
            agent.last_session = AgentSessionState(
                system_prompt_parts=('You are a helpful assistant.',),
                messages=list(messages),
            )
            agent.client = MagicMock()
            agent.client.complete.return_value = _OK_SUMMARY

            compact_conversation(agent)
            anchors = [
                m for m in agent.last_session.messages
                if m.metadata.get('anchor') is True
            ]

        self.assertEqual(
            [a.message_id for a in anchors],
            ['a', 'b', 'c'],
            'anchors must appear in original relative order',
        )

    def test_no_anchors_behavior_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _agent(tmp)
            messages = [_msg('user', f'routine {i}', mid=f'm{i}') for i in range(10)]
            agent.last_session = AgentSessionState(
                system_prompt_parts=('You are a helpful assistant.',),
                messages=list(messages),
            )
            agent.client = MagicMock()
            agent.client.complete.return_value = _OK_SUMMARY

            result = compact_conversation(agent)

        self.assertIsNone(result.error)
        # Same shape as the existing test_successful_compaction expects:
        boundary = [m for m in agent.last_session.messages
                    if m.metadata.get('kind') == 'compact_boundary']
        summary = [m for m in agent.last_session.messages
                   if m.metadata.get('kind') == 'compact_summary']
        self.assertEqual(len(boundary), 1)
        self.assertEqual(len(summary), 1)
        # No anchors leaked in.
        anchors = [m for m in agent.last_session.messages
                   if m.metadata.get('anchor') is True]
        self.assertEqual(anchors, [])


if __name__ == '__main__':
    unittest.main()
