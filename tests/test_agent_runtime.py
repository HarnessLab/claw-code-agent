from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    BudgetConfig,
    ModelConfig,
    OutputSchemaConfig,
)
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


class FakeStreamingHTTPResponse:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.lines: list[bytes] = []
        for payload in payloads:
            chunk = f'data: {json.dumps(payload)}\n\n'
            self.lines.extend(part.encode('utf-8') for part in chunk.splitlines(keepends=True))
        done_chunk = 'data: [DONE]\n\n'
        self.lines.extend(part.encode('utf-8') for part in done_chunk.splitlines(keepends=True))

    def readline(self) -> bytes:
        if not self.lines:
            return b''
        return self.lines.pop(0)

    def __enter__(self) -> 'FakeStreamingHTTPResponse':
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


def make_streaming_urlopen_side_effect(
    responses: list[list[dict[str, object]]],
):
    queued = [FakeStreamingHTTPResponse(payloads) for payloads in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


def make_recording_streaming_urlopen_side_effect(
    responses: list[list[dict[str, object]]],
    recorded_payloads: list[dict[str, object]],
):
    queued = [FakeStreamingHTTPResponse(payloads) for payloads in responses]

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

    def test_openai_client_streams_content_and_usage(self) -> None:
        responses = [
            [
                {'choices': [{'delta': {'content': 'Hello '}, 'finish_reason': None}]},
                {'choices': [{'delta': {'content': 'world'}, 'finish_reason': None}]},
                {
                    'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 3},
                },
            ]
        ]
        with patch(
            'src.openai_compat.request.urlopen',
            side_effect=make_streaming_urlopen_side_effect(responses),
        ):
            client = OpenAICompatClient(
                ModelConfig(
                    model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                    base_url='http://127.0.0.1:8000/v1',
                )
            )
            events = list(
                client.stream(
                    messages=[{'role': 'user', 'content': 'say hello'}],
                    tools=[],
                )
            )
        self.assertEqual(events[0].type, 'message_start')
        self.assertEqual(
            ''.join(event.delta for event in events if event.type == 'content_delta'),
            'Hello world',
        )
        usage_events = [event for event in events if event.type == 'usage']
        self.assertEqual(len(usage_events), 1)
        self.assertEqual(usage_events[0].usage.input_tokens, 10)
        self.assertEqual(usage_events[0].usage.output_tokens, 3)

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
        self.assertGreaterEqual(len(result.file_history), 0)

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

    def test_agent_streams_runtime_output_and_usage(self) -> None:
        responses = [
            [
                {'choices': [{'delta': {'content': 'Streaming '}, 'finish_reason': None}]},
                {'choices': [{'delta': {'content': 'works.'}, 'finish_reason': None}]},
                {
                    'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 14, 'completion_tokens': 5},
                },
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_streaming_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        stream_model_responses=True,
                    ),
                )
                result = agent.run('Say streaming works')
        self.assertEqual(result.final_output, 'Streaming works.')
        self.assertEqual(result.usage.input_tokens, 14)
        self.assertEqual(result.usage.output_tokens, 5)
        self.assertTrue(any(event.get('type') == 'content_delta' for event in result.events))
        self.assertIsNotNone(result.scratchpad_directory)
        assert result.scratchpad_directory is not None
        self.assertTrue(Path(result.scratchpad_directory).is_dir())
        assistant_messages = [
            message for message in result.transcript if message.get('role') == 'assistant'
        ]
        self.assertEqual(len(assistant_messages), 1)
        metadata = assistant_messages[0].get('metadata', {})
        mutation_totals = metadata.get('mutation_totals', {})
        self.assertGreaterEqual(mutation_totals.get('assistant_delta_append', 0), 2)
        self.assertEqual(mutation_totals.get('assistant_finalize', 0), 1)

    def test_agent_streams_tool_calls_and_reconstructs_arguments(self) -> None:
        responses = [
            [
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'id': 'call_1',
                                        'function': {
                                            'name': 'read_file',
                                            'arguments': '{"path": "hello',
                                        },
                                    }
                                ]
                            },
                            'finish_reason': None,
                        }
                    ]
                },
                {
                    'choices': [
                        {
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': 0,
                                        'function': {
                                            'arguments': '.txt"}',
                                        },
                                    }
                                ]
                            },
                            'finish_reason': None,
                        }
                    ]
                },
                {
                    'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
                    'usage': {'prompt_tokens': 9, 'completion_tokens': 4},
                },
            ],
            [
                {'choices': [{'delta': {'content': 'Read done.'}, 'finish_reason': None}]},
                {
                    'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 11, 'completion_tokens': 2},
                },
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'hello.txt').write_text('hello world\n', encoding='utf-8')
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_streaming_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        stream_model_responses=True,
                    ),
                )
                result = agent.run('Inspect hello.txt')
        self.assertEqual(result.final_output, 'Read done.')
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.usage.input_tokens, 20)
        self.assertEqual(result.usage.output_tokens, 6)
        assistant_messages = [message for message in result.transcript if message.get('role') == 'assistant']
        self.assertTrue(any(message.get('tool_calls') for message in assistant_messages))
        assistant_with_tool = next(
            message
            for message in assistant_messages
            if message.get('tool_calls')
        )
        metadata = assistant_with_tool.get('metadata', {})
        mutation_totals = metadata.get('mutation_totals', {})
        self.assertGreaterEqual(mutation_totals.get('assistant_tool_call_delta', 0), 2)
        self.assertEqual(mutation_totals.get('assistant_finalize', 0), 1)

    def test_transcript_entries_include_structured_blocks(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will inspect the file.',
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
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Done reading.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
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
        assistant_with_tool = next(
            message
            for message in result.transcript
            if message.get('role') == 'assistant' and message.get('tool_calls')
        )
        self.assertIn('blocks', assistant_with_tool)
        block_types = [block.get('type') for block in assistant_with_tool['blocks']]
        self.assertIn('text', block_types)
        self.assertIn('tool_call', block_types)
        tool_message = next(message for message in result.transcript if message.get('role') == 'tool')
        self.assertIn('blocks', tool_message)
        self.assertEqual(tool_message['blocks'][0]['type'], 'tool_result')

    def test_agent_inserts_compact_boundary_when_threshold_is_exceeded(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will inspect the file and then continue.',
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
                ],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 10},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Compaction test completed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 12, 'completion_tokens': 4},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'hello.txt').write_text('hello world\n', encoding='utf-8')
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        auto_compact_threshold_tokens=80,
                        compact_preserve_messages=1,
                    ),
                )
                result = agent.run(
                    'Read hello.txt and then continue with a detailed explanation that is intentionally long enough to trigger compaction.'
                )
        self.assertEqual(result.final_output, 'Compaction test completed.')
        compact_events = [event for event in result.events if event.get('type') == 'compact_boundary']
        self.assertEqual(len(compact_events), 1)
        second_request_messages = recorded_payloads[1]['messages']
        assert isinstance(second_request_messages, list)
        compact_messages = [
            message for message in second_request_messages
            if isinstance(message, dict)
            and isinstance(message.get('content'), str)
            and 'Earlier conversation history was compacted' in message['content']
        ]
        self.assertEqual(len(compact_messages), 1)
        transcript_compact_messages = [
            message for message in result.transcript
            if message.get('metadata', {}).get('kind') == 'compact_boundary'
        ]
        self.assertEqual(len(transcript_compact_messages), 1)
        compact_metadata = transcript_compact_messages[0].get('metadata', {})
        self.assertEqual(compact_metadata.get('compaction_depth'), 1)
        self.assertEqual(compact_metadata.get('nested_compaction_count'), 0)
        self.assertIn('preserved_tail_ids', compact_metadata)

    def test_agent_enforces_total_token_budget(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'This would be the answer.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': 30,
                    'completion_tokens': 12,
                },
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        budget_config=BudgetConfig(max_total_tokens=20),
                    ),
                )
                result = agent.run('Use too many tokens')
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertIn('token budget', result.final_output)
        self.assertEqual(result.usage.total_tokens, 42)

    def test_agent_continues_when_model_response_is_truncated(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Part 1 ',
                        },
                        'finish_reason': 'length',
                    }
                ],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 4},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Part 2',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 4, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Give me a long answer')
        self.assertEqual(result.final_output, 'Part 1 Part 2')
        continuation_events = [
            event for event in result.events if event.get('type') == 'continuation_request'
        ]
        self.assertEqual(len(continuation_events), 1)
        second_request_messages = recorded_payloads[1]['messages']
        assert isinstance(second_request_messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Continue exactly where you left off' in str(message.get('content', ''))
                for message in second_request_messages
            )
        )

    def test_agent_records_file_history_for_write_tool(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Creating the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'write_file',
                                        'arguments': '{"path": "out.txt", "content": "hi"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 4, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Done.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                result = agent.run('Create out.txt')
                stored = load_agent_session(result.session_id or '', directory=session_dir)
        self.assertEqual(len(result.file_history), 1)
        self.assertEqual(result.file_history[0]['path'], 'out.txt')
        self.assertEqual(result.file_history[0]['history_kind'], 'file_change')
        self.assertIn('history_entry_id', result.file_history[0])
        self.assertIn('after_snapshot_id', result.file_history[0])
        self.assertEqual(stored.file_history[0]['action'], 'write_file')
        self.assertEqual(stored.file_history[0]['after_snapshot_id'], result.file_history[0]['after_snapshot_id'])

    def test_agent_streams_write_file_tool_output(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Creating the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'write_file',
                                        'arguments': '{"path": "out.txt", "content": "hello"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 4, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Write streamed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                result = agent.run('Create out.txt')
        self.assertEqual(result.final_output, 'Write streamed.')
        tool_delta_events = [
            event for event in result.events
            if event.get('type') == 'tool_delta' and event.get('tool_name') == 'write_file'
        ]
        self.assertGreaterEqual(len(tool_delta_events), 1)
        tool_message = next(
            message for message in result.transcript
            if message.get('role') == 'tool'
        )
        metadata = tool_message.get('metadata', {})
        self.assertEqual(metadata.get('streamed'), True)
        self.assertEqual(metadata.get('action'), 'write_file')

    def test_agent_streams_bash_tool_output_and_mutates_tool_transcript(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Running a shell command.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'bash',
                                        'arguments': json.dumps(
                                            {
                                                'command': (
                                                    "printf 'alpha\\n'; "
                                                    "sleep 0.05; "
                                                    "printf 'beta\\n' >&2"
                                                )
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Shell command completed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        permissions=AgentPermissions(allow_shell_commands=True),
                    ),
                )
                result = agent.run('Run a shell command')
        self.assertEqual(result.final_output, 'Shell command completed.')
        tool_delta_events = [event for event in result.events if event.get('type') == 'tool_delta']
        self.assertGreaterEqual(len(tool_delta_events), 2)
        joined_delta = ''.join(event.get('delta', '') for event in tool_delta_events)
        self.assertIn('alpha', joined_delta)
        self.assertIn('beta', joined_delta)
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        tool_message = tool_messages[0]
        self.assertIn('exit_code=0', tool_message.get('content', ''))
        metadata = tool_message.get('metadata', {})
        self.assertIn('stream_preview', metadata)
        self.assertIn('alpha', metadata['stream_preview'])
        self.assertIn('beta', metadata['stream_preview'])

    def test_agent_streams_read_file_tool_output_in_chunks(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "large.txt"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Read finished.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'large.txt').write_text('alpha\n' * 300, encoding='utf-8')
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Read the large file')
        self.assertEqual(result.final_output, 'Read finished.')
        tool_delta_events = [
            event for event in result.events
            if event.get('type') == 'tool_delta' and event.get('tool_name') == 'read_file'
        ]
        self.assertGreaterEqual(len(tool_delta_events), 2)
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].get('metadata', {}).get('streamed'), True)
        self.assertIn('stream_preview', tool_messages[0].get('metadata', {}))

    def test_agent_records_tombstone_mutation_history_when_snipping(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading the large file first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "large.txt"}',
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
                            'content': 'Snip run completed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'large.txt').write_text(('alpha beta gamma\n' * 400), encoding='utf-8')
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        auto_snip_threshold_tokens=120,
                        compact_preserve_messages=0,
                    ),
                )
                result = agent.run('Read the large file and summarize it')
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(tool_messages[0].get('state'), 'tombstoned')
        self.assertEqual(metadata.get('kind'), 'snipped_message')
        self.assertEqual(metadata.get('last_mutation_kind'), 'snip_tombstone')
        self.assertGreaterEqual(metadata.get('mutation_count', 0), 2)
        self.assertTrue(any(entry.get('kind') == 'tool_finalize_replace' for entry in metadata.get('mutations', [])))
        self.assertTrue(any(event.get('type') == 'snip_boundary' for event in result.events))

    def test_resume_injects_file_history_replay_reminder(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Creating the file first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'write_file',
                                        'arguments': '{"path": "replay.txt", "content": "hello"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Initial write done.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Resume acknowledged.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                first_result = agent.run('Create replay.txt')
                stored = load_agent_session(first_result.session_id or '', directory=session_dir)

                resumed_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                resumed_agent.resume('Continue the work', stored)
        resumed_messages = recorded_payloads[-1]['messages']
        assert isinstance(resumed_messages, list)
        replay_messages = [
            message for message in resumed_messages
            if isinstance(message, dict)
            and isinstance(message.get('content'), str)
            and 'Recent file history from this saved session:' in message['content']
        ]
        self.assertEqual(len(replay_messages), 1)
        self.assertIn('path=replay.txt', replay_messages[0]['content'])
        self.assertIn('action=write_file', replay_messages[0]['content'])

    def test_resume_replays_file_history_snapshots(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Editing the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'edit_file',
                                        'arguments': json.dumps(
                                            {
                                                'path': 'draft.txt',
                                                'old_text': 'hello world',
                                                'new_text': 'hello mars',
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Edit completed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Resume processed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'draft.txt').write_text('hello world\n', encoding='utf-8')
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                first_result = agent.run('Edit draft.txt')
                stored = load_agent_session(first_result.session_id or '', directory=session_dir)
                resumed_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                resumed_agent.resume('Continue after the edit', stored)
        resumed_messages = recorded_payloads[-1]['messages']
        assert isinstance(resumed_messages, list)
        replay_messages = [
            message for message in resumed_messages
            if isinstance(message, dict)
            and isinstance(message.get('content'), str)
            and 'Recent file history from this saved session:' in message['content']
        ]
        self.assertEqual(len(replay_messages), 1)
        replay_content = replay_messages[0]['content']
        self.assertIn('Unique changed paths: 1', replay_content)
        self.assertIn('Snapshot ids: 2', replay_content)
        self.assertIn('before_snapshot:', replay_content)
        self.assertIn('after_snapshot:', replay_content)
        self.assertIn('before: hello world', replay_content)
        self.assertIn('after: hello mars', replay_content)
        self.assertIn('result:', replay_content)

    def test_resume_injects_compaction_replay_reminder(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading the large file first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "large.txt"}',
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
                            'content': 'Initial compaction run completed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Resume after compaction processed.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'large.txt').write_text(('alpha beta gamma\n' * 400), encoding='utf-8')
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        auto_snip_threshold_tokens=120,
                        compact_preserve_messages=0,
                    ),
                )
                first_result = agent.run('Read the large file and summarize it')
                stored = load_agent_session(first_result.session_id or '', directory=session_dir)
                resumed_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                        auto_snip_threshold_tokens=120,
                        compact_preserve_messages=0,
                    ),
                )
                resumed_agent.resume('Continue after compaction', stored)
        resumed_messages = recorded_payloads[-1]['messages']
        assert isinstance(resumed_messages, list)
        compaction_messages = [
            message for message in resumed_messages
            if isinstance(message, dict)
            and isinstance(message.get('content'), str)
            and 'This resumed session already contains compacted or snipped history.' in message['content']
        ]
        self.assertEqual(len(compaction_messages), 1)
        self.assertIn('Snipped/tombstoned messages:', compaction_messages[0]['content'])

    def test_agent_can_delegate_to_nested_agent(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Delegating this task.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'delegate_agent',
                                        'arguments': json.dumps(
                                            {
                                                'prompt': 'Summarize the delegated task.',
                                                'max_turns': 2,
                                            }
                                        ),
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
                            'content': 'Delegated summary complete.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Parent task completed after delegation.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Use a delegate agent')
        self.assertEqual(result.final_output, 'Parent task completed after delegation.')
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        self.assertIn('Delegated agent completed the subtask.', tool_messages[0]['content'])
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(metadata.get('action'), 'delegate_agent')
        self.assertIsNotNone(metadata.get('child_session_id'))

    def test_agent_can_delegate_multiple_subtasks_with_parent_context(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Delegating multiple subtasks.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'delegate_agent',
                                        'arguments': json.dumps(
                                            {
                                                'subtasks': [
                                                    {'label': 'scan', 'prompt': 'Scan the project.'},
                                                    {'label': 'summarize', 'prompt': 'Summarize the project.'},
                                                ],
                                                'max_turns': 2,
                                                'include_parent_context': True,
                                            }
                                        ),
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
                            'content': 'Child scan result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Child summary result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Parent completed after multi-delegate.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Use multiple delegated subtasks')
        self.assertEqual(result.final_output, 'Parent completed after multi-delegate.')
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(metadata.get('subtask_count'), 2)
        self.assertEqual(len(metadata.get('child_session_ids', [])), 2)
        self.assertEqual(len(metadata.get('child_results', [])), 2)
        self.assertIn('Delegated agent completed 2 sequential subtasks.', tool_messages[0].get('content', ''))
        self.assertTrue(any(event.get('type') == 'delegate_group_result' for event in result.events))
        child_events = [event for event in result.events if event.get('type') == 'delegate_subtask_result']
        self.assertEqual(len(child_events), 2)
        second_child_request = recorded_payloads[2]['messages']
        assert isinstance(second_child_request, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Prior delegated subtask summaries:' in str(message.get('content', ''))
                for message in second_child_request
            )
        )

    def test_agent_manager_tracks_delegate_group_membership(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Delegating multiple subtasks.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'delegate_agent',
                                        'arguments': json.dumps(
                                            {
                                                'subtasks': [
                                                    {'label': 'scan', 'prompt': 'Scan the project.'},
                                                    {'label': 'summarize', 'prompt': 'Summarize the project.'},
                                                ],
                                                'max_turns': 2,
                                            }
                                        ),
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
                            'content': 'Child scan result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Child summary result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Parent completed after multi-delegate.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Use multiple delegated subtasks')

        self.assertEqual(result.final_output, 'Parent completed after multi-delegate.')
        self.assertIsNotNone(agent.agent_manager)
        manager = agent.agent_manager
        assert manager is not None
        self.assertEqual(len(manager.groups), 1)
        group = next(iter(manager.groups.values()))
        self.assertEqual(group.completed_children, 2)
        child_records = sorted(
            (
                record for record in manager.completed_records()
                if record.parent_agent_id == agent.managed_agent_id
            ),
            key=lambda record: (record.child_index or 0),
        )
        self.assertEqual(len(child_records), 2)
        self.assertEqual([record.child_index for record in child_records], [1, 2])
        self.assertTrue(all(record.group_id == group.group_id for record in child_records))
        summary = '\n'.join(manager.summary_lines())
        self.assertIn(f'group={group.group_id}', summary)

    def test_agent_can_delegate_into_resumed_child_session(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Seed child result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Delegating into a resumed child session.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'delegate_agent',
                                        'arguments': '{}',
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
                            'content': 'Resumed child result.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Parent completed after resumed child.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                seed_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                    ),
                )
                seeded = seed_agent.run('Seed the delegated child')
                resumed_child_id = seeded.session_id or ''

                delegate_arguments = json.dumps(
                    {
                        'subtasks': [
                            {
                                'label': 'resume_child',
                                'prompt': 'Continue the delegated child.',
                                'resume_session_id': resumed_child_id,
                                'max_turns': 2,
                            }
                        ],
                        'max_turns': 2,
                    }
                )
                responses[1]['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = delegate_arguments

                parent_agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                    ),
                )
                result = parent_agent.run('Delegate into the resumed child')

        self.assertEqual(result.final_output, 'Parent completed after resumed child.')
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(metadata.get('resumed_children'), 1)
        child_results = metadata.get('child_results', [])
        self.assertEqual(len(child_results), 1)
        self.assertEqual(child_results[0].get('resume_used'), True)
        self.assertEqual(child_results[0].get('resumed_from_session_id'), resumed_child_id)
        self.assertTrue(
            any(
                event.get('type') == 'delegate_subtask_result'
                and event.get('resume_used')
                for event in result.events
            )
        )
        resumed_child_messages = recorded_payloads[2]['messages']
        assert isinstance(resumed_child_messages, list)
        resumed_contents = [
            message.get('content')
            for message in resumed_child_messages
            if isinstance(message, dict)
        ]
        self.assertIn('Seed the delegated child', resumed_contents)
        self.assertIn('Seed child result.', resumed_contents)
        self.assertIn('Continue the delegated child.', resumed_contents)
        manager = parent_agent.agent_manager
        assert manager is not None
        child_records = [
            record
            for record in manager.completed_records()
            if record.parent_agent_id == parent_agent.managed_agent_id
        ]
        self.assertEqual(len(child_records), 1)
        self.assertEqual(child_records[0].resumed_from_session_id, resumed_child_id)
        summary = '\n'.join(manager.summary_lines())
        self.assertIn(f'resumed_from={resumed_child_id}', summary)

    def test_agent_enforces_reasoning_token_budget(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'This uses too much reasoning.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': 8,
                    'completion_tokens': 4,
                    'completion_tokens_details': {'reasoning_tokens': 9},
                },
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        budget_config=BudgetConfig(max_reasoning_tokens=5),
                    ),
                )
                result = agent.run('Use too much reasoning')
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertIn('reasoning token budget', result.final_output)
        self.assertEqual(result.usage.reasoning_tokens, 9)

    def test_agent_enforces_tool_call_budget_before_execution(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will create the file.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'write_file',
                                        'arguments': '{"path": "blocked.txt", "content": "nope"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 4, 'completion_tokens': 2},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        permissions=AgentPermissions(allow_file_write=True),
                        budget_config=BudgetConfig(max_tool_calls=0),
                    ),
                )
                result = agent.run('Try to create a file')
                self.assertFalse((workspace / 'blocked.txt').exists())
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertIn('tool-call budget', result.final_output)
        self.assertFalse(any(message.get('role') == 'tool' for message in result.transcript))

    def test_agent_enforces_delegated_task_budget_before_child_agent_runs(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will delegate this.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'delegate_agent',
                                        'arguments': json.dumps(
                                            {
                                                'prompt': 'Do the delegated work.',
                                                'max_turns': 2,
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 4, 'completion_tokens': 2},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch('src.openai_compat.request.urlopen', side_effect=make_urlopen_side_effect(responses)):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        budget_config=BudgetConfig(max_delegated_tasks=0),
                    ),
                )
                result = agent.run('Try to delegate')
        self.assertEqual(result.stop_reason, 'budget_exceeded')
        self.assertIn('delegated-task budget', result.final_output)
        self.assertFalse(any(message.get('role') == 'tool' for message in result.transcript))

    def test_agent_sends_response_schema_when_configured(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': '{"status":"ok"}',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 4},
            }
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_recording_urlopen_side_effect(responses, recorded_payloads),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        output_schema=OutputSchemaConfig(
                            name='status_response',
                            schema={
                                'type': 'object',
                                'properties': {'status': {'type': 'string'}},
                                'required': ['status'],
                            },
                            strict=True,
                        ),
                    ),
                )
                agent.run('Return a JSON status payload')
        self.assertEqual(
            recorded_payloads[0]['response_format'],
            {
                'type': 'json_schema',
                'json_schema': {
                    'name': 'status_response',
                    'schema': {
                        'type': 'object',
                        'properties': {'status': {'type': 'string'}},
                        'required': ['status'],
                    },
                    'strict': True,
                },
            },
        )
