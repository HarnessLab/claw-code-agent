from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_types import AgentRuntimeConfig, ModelConfig
from src.openai_compat import OpenAICompatClient
from src.plugin_runtime import PluginRuntime
from src.query_engine import QueryEngineConfig, QueryEnginePort


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


def make_urlopen_side_effect(responses: list[dict[str, object]]):
    queued = [FakeHTTPResponse(payload) for payload in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


def make_streaming_urlopen_side_effect(
    responses: list[list[dict[str, object]]],
):
    queued = [FakeStreamingHTTPResponse(payloads) for payloads in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


class QueryEngineRuntimeTests(unittest.TestCase):
    def test_plugin_runtime_discovers_local_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'version': '0.1.0',
                        'description': 'Demo plugin',
                        'tools': ['demo_tool'],
                        'hooks': {
                            'beforePrompt': 'Run plugin hook before prompt.',
                            'afterTurn': 'Plugin after-turn hook.',
                        },
                        'toolAliases': [
                            {
                                'name': 'plugin_read',
                                'baseTool': 'read_file',
                                'description': 'Plugin read alias',
                            }
                        ],
                    }
                ),
                encoding='utf-8',
            )
            runtime = PluginRuntime.from_workspace(workspace)

        self.assertEqual(len(runtime.manifests), 1)
        self.assertEqual(runtime.manifests[0].name, 'demo-plugin')
        self.assertEqual(runtime.manifests[0].tool_names, ('demo_tool',))
        self.assertIn('beforePrompt', runtime.manifests[0].hook_names)
        self.assertIn('afterTurn', runtime.manifests[0].hook_names)
        self.assertEqual(runtime.manifests[0].tool_aliases[0].name, 'plugin_read')
        self.assertEqual(runtime.manifests[0].before_prompt, 'Run plugin hook before prompt.')
        self.assertEqual(runtime.manifests[0].after_turn, 'Plugin after-turn hook.')

    def test_query_engine_can_drive_real_runtime_agent(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Initial runtime answer.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 3},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Resumed runtime answer.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
        ]
        recorded_payloads: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            plugin_dir = workspace / '.codex-plugin'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps({'name': 'runtime-plugin', 'tools': ['runtime_tool']}),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                first = engine.submit_message('Start the task')
                second = engine.submit_message('Continue the task')
                summary = engine.render_summary()

        self.assertEqual(first.output, 'Initial runtime answer.')
        self.assertEqual(second.output, 'Resumed runtime answer.')
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(second.usage.input_tokens, 6)
        self.assertIn('Real runtime agent mode: True', summary)
        self.assertIn('## Agent Manager', summary)
        self.assertIn('runtime-plugin', summary)
        self.assertEqual(len(recorded_payloads), 2)
        resumed_messages = recorded_payloads[1]['messages']
        assert isinstance(resumed_messages, list)
        contents = [message.get('content') for message in resumed_messages if isinstance(message, dict)]
        self.assertIn('Start the task', contents)
        self.assertIn('Initial runtime answer.', contents)
        self.assertIn('Continue the task', contents)

    def test_runtime_agent_uses_plugin_aliases_and_hooks(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Using plugin alias.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'plugin_read',
                                        'arguments': '{"path": "hello.txt"}',
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
                            'content': 'Plugin alias completed.',
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
            (workspace / 'hello.txt').write_text('hello plugin\n', encoding='utf-8')
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'hooks': {
                            'beforePrompt': 'Run plugin hook before prompt.',
                            'afterTurn': 'Plugin after-turn hook.',
                        },
                        'toolAliases': [
                            {
                                'name': 'plugin_read',
                                'baseTool': 'read_file',
                                'description': 'Plugin read alias',
                            }
                        ],
                    }
                ),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Read the file through the plugin alias')

        self.assertEqual(result.final_output, 'Plugin alias completed.')
        self.assertTrue(any(event.get('type') == 'plugin_after_turn' for event in result.events))
        tool_names = [
            item['function']['name']
            for item in recorded_payloads[0]['tools']
            if isinstance(item, dict) and isinstance(item.get('function'), dict)
        ]
        self.assertIn('plugin_read', tool_names)
        messages = recorded_payloads[0]['messages']
        assert isinstance(messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Run plugin hook before prompt.' in str(message.get('content', ''))
                for message in messages
            )
        )

    def test_runtime_agent_executes_plugin_virtual_tool(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Calling the plugin virtual tool.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'demo_virtual',
                                        'arguments': '{"topic": "plugins"}',
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
                            'content': 'Plugin virtual tool completed.',
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
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'virtualTools': [
                            {
                                'name': 'demo_virtual',
                                'description': 'Return a rendered plugin response.',
                                'responseTemplate': 'Plugin says {topic}',
                                'parameters': {
                                    'type': 'object',
                                    'properties': {'topic': {'type': 'string'}},
                                    'required': ['topic'],
                                },
                            }
                        ],
                        'toolHooks': {
                            'demo_virtual': {
                                'afterResult': 'Use the virtual tool result in the next reply.',
                            }
                        },
                    }
                ),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Use the plugin virtual tool')

        self.assertEqual(result.final_output, 'Plugin virtual tool completed.')
        self.assertTrue(
            any(event.get('type') == 'plugin_virtual_tool_result' for event in result.events)
        )
        tool_names = [
            item['function']['name']
            for item in recorded_payloads[0]['tools']
            if isinstance(item, dict) and isinstance(item.get('function'), dict)
        ]
        self.assertIn('demo_virtual', tool_names)
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(metadata.get('action'), 'plugin_virtual_tool')
        self.assertEqual(metadata.get('plugin_name'), 'demo-plugin')
        self.assertEqual(metadata.get('virtual_tool'), 'demo_virtual')
        second_messages = recorded_payloads[1]['messages']
        assert isinstance(second_messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Use the virtual tool result in the next reply.' in str(message.get('content', ''))
                for message in second_messages
            )
        )

    def test_runtime_agent_injects_plugin_tool_runtime_guidance(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading through plugin guidance.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "guide.txt"}',
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
                            'content': 'Plugin runtime guidance consumed.',
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
            (workspace / 'guide.txt').write_text('plugin guidance\n', encoding='utf-8')
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'toolHooks': {
                            'read_file': {
                                'afterResult': 'Summarize the file before making edits.',
                            }
                        },
                    }
                ),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Read the file and continue')

        self.assertEqual(result.final_output, 'Plugin runtime guidance consumed.')
        self.assertTrue(any(event.get('type') == 'plugin_tool_context' for event in result.events))
        runtime_messages = [
            message for message in result.transcript
            if message.get('metadata', {}).get('kind') == 'plugin_tool_runtime'
        ]
        self.assertEqual(len(runtime_messages), 1)
        self.assertIn('Summarize the file before making edits.', runtime_messages[0].get('content', ''))
        second_messages = recorded_payloads[1]['messages']
        assert isinstance(second_messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Plugin tool runtime guidance for `read_file`:' in str(message.get('content', ''))
                and 'Summarize the file before making edits.' in str(message.get('content', ''))
                for message in second_messages
            )
        )

    def test_runtime_agent_supports_plugin_before_tool_guidance(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading through plugin guidance.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "guide.txt"}',
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
                            'content': 'Plugin before-tool guidance consumed.',
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
            (workspace / 'guide.txt').write_text('plugin before tool\n', encoding='utf-8')
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'toolHooks': {
                            'read_file': {
                                'beforeTool': 'Validate the path before reading.',
                            }
                        },
                    }
                ),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Read the file and continue')

        self.assertEqual(result.final_output, 'Plugin before-tool guidance consumed.')
        self.assertTrue(any(event.get('type') == 'plugin_tool_preflight' for event in result.events))
        runtime_messages = [
            message for message in result.transcript
            if message.get('metadata', {}).get('kind') == 'plugin_tool_runtime'
        ]
        self.assertEqual(len(runtime_messages), 1)
        self.assertIn('Before tool: demo-plugin: Validate the path before reading.', runtime_messages[0].get('content', ''))
        second_messages = recorded_payloads[1]['messages']
        assert isinstance(second_messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Before tool: demo-plugin: Validate the path before reading.' in str(message.get('content', ''))
                for message in second_messages
            )
        )

    def test_runtime_agent_blocks_tool_via_plugin_manifest(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Trying a blocked shell command.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'bash',
                                        'arguments': '{"command": "pwd"}',
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
                            'content': 'Blocked tool handled.',
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
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'blockedTools': ['bash'],
                    }
                ),
                encoding='utf-8',
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
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                result = agent.run('Try a blocked tool')

        self.assertEqual(result.final_output, 'Blocked tool handled.')
        self.assertTrue(any(event.get('type') == 'plugin_tool_block' for event in result.events))
        self.assertTrue(any(event.get('type') == 'plugin_tool_context' for event in result.events))
        tool_messages = [message for message in result.transcript if message.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        metadata = tool_messages[0].get('metadata', {})
        self.assertEqual(metadata.get('action'), 'plugin_block')
        self.assertEqual(metadata.get('plugin_blocked'), True)
        second_messages = recorded_payloads[1]['messages']
        assert isinstance(second_messages, list)
        self.assertTrue(
            any(
                isinstance(message, dict)
                and 'Plugin tool runtime guidance for `bash`:' in str(message.get('content', ''))
                and 'blocked tool bash' in str(message.get('content', '')).lower()
                for message in second_messages
            )
        )

    def test_query_engine_runtime_summary_tracks_runtime_events(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Reading through plugin guidance.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'read_file',
                                        'arguments': '{"path": "guide.txt"}',
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
                            'content': 'Summary ready.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 2},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'guide.txt').write_text('runtime summary\n', encoding='utf-8')
            plugin_dir = workspace / 'plugins' / 'demo'
            plugin_dir.mkdir(parents=True)
            (plugin_dir / 'plugin.json').write_text(
                json.dumps(
                    {
                        'name': 'demo-plugin',
                        'toolHooks': {
                            'read_file': {'afterResult': 'Summarize the file before editing it.'}
                        },
                    }
                ),
                encoding='utf-8',
            )
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Read the file and summarize it')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Summary ready.')
        self.assertIn('## Runtime Events', summary)
        self.assertIn('- plugin_tool_context=1', summary)
        self.assertIn('- tool_result=1', summary)
        self.assertIn('## Runtime Message Kinds', summary)
        self.assertIn('- plugin_tool_runtime=1', summary)
        self.assertIn('- transcript_messages=', summary)

    def test_query_engine_runtime_stream_emits_runtime_summary_event(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Streaming summary ready.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 3},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                events = list(engine.stream_submit_message('Summarize the repo'))

        runtime_summary_events = [
            event for event in events if event.get('type') == 'runtime_summary'
        ]
        self.assertEqual(len(runtime_summary_events), 1)
        summary_event = runtime_summary_events[0]
        self.assertIn('runtime_event_counts', summary_event)
        self.assertIn('transcript_store_entries', summary_event)
        self.assertIn('transcript_store_compactions', summary_event)

    def test_query_engine_compacts_transcript_store_with_summary_entry(self) -> None:
        engine = QueryEnginePort.from_workspace()
        engine.config = QueryEngineConfig(max_turns=6, compact_after_turns=2)
        engine.submit_message('first prompt')
        engine.submit_message('second prompt')
        engine.submit_message('third prompt')

        replay = engine.replay_user_messages()
        self.assertTrue(any('[transcript-compaction ' in entry for entry in replay))
        summary = engine.render_summary()
        self.assertIn('## Transcript Store', summary)
        self.assertIn('Transcript compactions:', summary)

    def test_query_engine_runtime_summary_tracks_mutation_counts(self) -> None:
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
                            'content': 'Mutation summary completed.',
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
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
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
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Read the large file and summarize it')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Mutation summary completed.')
        self.assertIn('## Runtime Mutations', summary)
        self.assertIn('- snip_tombstone=1', summary)
        self.assertIn('- tool_finalize_replace=1', summary)
        self.assertIn('## Runtime Context Reduction', summary)
        self.assertIn('- snipped_messages=1', summary)

    def test_query_engine_runtime_summary_tracks_compaction_lineage(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Read the large file first.',
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
                            'content': 'Compaction lineage summary completed.',
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
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        auto_compact_threshold_tokens=120,
                        compact_preserve_messages=0,
                    ),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Read the large file and summarize it')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Compaction lineage summary completed.')
        self.assertIn('## Runtime Context Reduction', summary)
        self.assertIn('- compact_boundaries=1', summary)
        self.assertIn('- compacted_lineages=', summary)
        self.assertIn('## Runtime Lineage', summary)
        self.assertIn('- seen_lineages=', summary)
        self.assertIn('- compacted_lineages=', summary)
        self.assertIn('- max_source_revision=', summary)

    def test_query_engine_runtime_summary_tracks_assistant_stream_mutations(self) -> None:
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
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Say streaming works')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Streaming works.')
        self.assertIn('## Runtime Mutations', summary)
        self.assertIn('- assistant_delta_append=2', summary)
        self.assertIn('- assistant_finalize=1', summary)

    def test_query_engine_runtime_summary_tracks_delegate_orchestration(self) -> None:
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
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Use multiple delegated subtasks')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Parent completed after multi-delegate.')
        self.assertIn('## Runtime Orchestration', summary)
        self.assertIn('- group_status:completed=1', summary)
        self.assertIn('- child_stop:stop=2', summary)

    def test_query_engine_runtime_summary_tracks_resumed_delegate_children(self) -> None:
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
                            'content': 'Delegating into resumed child.',
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
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
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

                agent = LocalCodingAgent(
                    model_config=ModelConfig(
                        model='Qwen/Qwen3-Coder-30B-A3B-Instruct',
                        base_url='http://127.0.0.1:8000/v1',
                    ),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        session_directory=session_dir,
                    ),
                )
                engine = QueryEnginePort.from_runtime_agent(agent)
                turn = engine.submit_message('Delegate into resumed child')
                summary = engine.render_summary()

        self.assertEqual(turn.output, 'Parent completed after resumed child.')
        self.assertIn('## Runtime Orchestration', summary)
        self.assertIn('- resumed_children=1', summary)
