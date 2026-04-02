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
from src.query_engine import QueryEnginePort


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode('utf-8')

    def __enter__(self) -> 'FakeHTTPResponse':
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
