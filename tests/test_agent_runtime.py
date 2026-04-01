from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import AgentRuntimeConfig, ModelConfig
from src.openai_compat import OpenAICompatClient
from src.session_store import load_agent_session


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


def make_recording_urlopen_side_effect(
    responses: list[dict[str, object]],
    recorded_payloads: list[dict[str, object]],
):
    queued = [FakeHTTPResponse(payload) for payload in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        body = request_obj.data.decode('utf-8')
        recorded_payloads.append(json.loads(body))
        return queued.pop(0)

    return _fake_urlopen


class AgentRuntimeTests(unittest.TestCase):
    def test_openai_client_parses_tool_calls(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Inspecting the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "hello.txt"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ]
            }
        ]
        with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
            client = OpenAICompatClient(
                ModelConfig(
                    model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                    base_url='http://127.0.0.1:8000/v1',
                )
            )
            turn = client.complete(
                messages=[{'role': 'user', 'content': 'read hello.txt'}],
                tools=[],
            )
        self.assertEqual(turn.content, 'Inspecting the file.')
        self.assertEqual(len(turn.tool_calls), 1)
        self.assertEqual(turn.tool_calls[0].name, 'read_file')
        self.assertEqual(turn.tool_calls[0].arguments['path'], 'hello.txt')

    def test_agent_executes_tool_calls_against_fake_backend(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will inspect the file first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "hello.txt"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ]
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'The file contains hello world.',
                        },
                        'finish_reason': 'stop',
                    }
                ]
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'hello.txt').write_text('hello world\n', encoding='utf-8')
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Inspect hello.txt')

        self.assertEqual(result.final_output, 'The file contains hello world.')
        self.assertEqual(result.tool_calls, 1)
        self.assertGreaterEqual(len(result.transcript), 5)

    def test_write_tool_is_blocked_without_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = AgentRuntimeConfig(cwd=Path(tmp_dir))
            context = build_tool_context(config)
            result = execute_tool(
                default_tool_registry(),
                'write_file',
                {'path': 'blocked.txt', 'content': 'data'},
                context,
            )
        self.assertFalse(result.ok)
        self.assertIn('--allow-write', result.content)

    def test_local_slash_command_returns_without_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            result = agent.run('/permissions')
        self.assertEqual(result.turns, 0)
        self.assertEqual(result.tool_calls, 0)
        self.assertIn('# Permissions', result.final_output)

    def test_agent_persists_session_and_can_resume(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Initial answer.',
                        },
                        'finish_reason': 'stop',
                    }
                ]
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Continued answer.',
                        },
                        'finish_reason': 'stop',
                    }
                ]
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            runtime_config = AgentRuntimeConfig(
                cwd=workspace,
                session_directory=session_dir,
            )
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=runtime_config,
                )
                first_result = agent.run('Start task')
                self.assertIsNotNone(first_result.session_id)
                stored = load_agent_session(first_result.session_id or '', directory=session_dir)

                resumed_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=runtime_config,
                )
                second_result = resumed_agent.resume('Continue the task', stored)

                self.assertTrue((session_dir / f'{first_result.session_id}.json').exists())

        self.assertEqual(first_result.final_output, 'Initial answer.')
        self.assertEqual(second_result.final_output, 'Continued answer.')
        self.assertEqual(second_result.session_id, first_result.session_id)
        self.assertEqual(len(recorded_payloads), 2)
        resumed_messages = recorded_payloads[1]['messages']
        assert isinstance(resumed_messages, list)
        contents = [message.get('content') for message in resumed_messages if isinstance(message, dict)]
        self.assertIn('Start task', contents)
        self.assertIn('Initial answer.', contents)
        self.assertIn('Continue the task', contents)
