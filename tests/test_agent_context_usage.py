from __future__ import annotations

import unittest

from src.agent_context_usage import collect_context_usage, format_context_usage
from src.agent_session import AgentSessionState


class AgentContextUsageTests(unittest.TestCase):
    def test_collect_context_usage_formats_breakdown(self) -> None:
        session = AgentSessionState.create(
            ['# Intro\nhello', '# System\nworld'],
            'inspect repo',
            user_context={'currentDate': "Today's date is 2026-04-01."},
            system_context={'gitStatus': 'Current branch: main'},
        )
        session.append_assistant(
            'Reading files',
            (
                {
                    'id': 'call_1',
                    'type': 'function',
                    'function': {'name': 'read_file', 'arguments': '{"path":"a.py"}'},
                },
            ),
        )
        session.append_tool('read_file', 'call_1', '{"ok": true, "content": "print(1)"}')

        report = collect_context_usage(
            session=session,
            model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
            strategy='test session',
        )
        rendered = format_context_usage(report)

        self.assertGreater(report.total_tokens, 0)
        self.assertIn('## Context Usage', rendered)
        self.assertIn('### System Prompt Sections', rendered)
        self.assertIn('### Message Breakdown', rendered)
        self.assertIn('#### Top Tools', rendered)
