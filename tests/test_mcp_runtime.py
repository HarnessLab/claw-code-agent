from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import AgentRuntimeConfig, ModelConfig
from src.mcp_runtime import MCPRuntime


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode('utf-8')

    def __enter__(self) -> 'FakeHTTPResponse':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def make_urlopen_side_effect(responses: list[dict[str, object]]):
    queued = [FakeHTTPResponse(payload) for payload in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


class MCPRuntimeTests(unittest.TestCase):
    def test_runtime_discovers_and_reads_local_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'notes.txt').write_text('mcp notes\n', encoding='utf-8')
            (workspace / '.claw-mcp.json').write_text(
                (
                    '{"servers":[{"name":"workspace","resources":['
                    '{"uri":"mcp://workspace/notes","name":"Notes","path":"notes.txt"},'
                    '{"uri":"mcp://workspace/inline","name":"Inline","text":"inline body"}'
                    ']}]}'
                ),
                encoding='utf-8',
            )
            runtime = MCPRuntime.from_workspace(workspace)
            self.assertEqual(len(runtime.resources), 2)
            self.assertIn('Local MCP resources: 2', runtime.render_summary())
            self.assertEqual(runtime.read_resource('mcp://workspace/inline'), 'inline body')
            self.assertIn('mcp notes', runtime.read_resource('mcp://workspace/notes'))

    def test_mcp_tools_execute_against_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'notes.txt').write_text('mcp notes\n', encoding='utf-8')
            (workspace / '.claw-mcp.json').write_text(
                (
                    '{"servers":[{"name":"workspace","resources":['
                    '{"uri":"mcp://workspace/notes","name":"Notes","path":"notes.txt"}'
                    ']}]}'
                ),
                encoding='utf-8',
            )
            runtime = MCPRuntime.from_workspace(workspace)
            context = build_tool_context(
                AgentRuntimeConfig(cwd=workspace),
                mcp_runtime=runtime,
            )
            list_result = execute_tool(
                default_tool_registry(),
                'mcp_list_resources',
                {},
                context,
            )
            read_result = execute_tool(
                default_tool_registry(),
                'mcp_read_resource',
                {'uri': 'mcp://workspace/notes'},
                context,
            )

        self.assertTrue(list_result.ok)
        self.assertIn('mcp://workspace/notes', list_result.content)
        self.assertTrue(read_result.ok)
        self.assertIn('mcp notes', read_result.content)

    def test_agent_can_use_mcp_tools_in_model_loop(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will inspect the MCP resource.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'mcp_read_resource',
                                        'arguments': '{"uri": "mcp://workspace/notes"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'The MCP resource says mcp notes.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'notes.txt').write_text('mcp notes\n', encoding='utf-8')
            (workspace / '.claw-mcp.json').write_text(
                (
                    '{"servers":[{"name":"workspace","resources":['
                    '{"uri":"mcp://workspace/notes","name":"Notes","path":"notes.txt"}'
                    ']}]}'
                ),
                encoding='utf-8',
            )
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Read the MCP notes resource')

        self.assertEqual(result.final_output, 'The MCP resource says mcp notes.')
        self.assertEqual(result.tool_calls, 1)
        tool_message = next(
            message
            for message in result.transcript
            if message.get('role') == 'tool'
        )
        self.assertIn('mcp notes', tool_message.get('content', ''))
