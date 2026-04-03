from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig
from src.plan_runtime import PlanRuntime
from src.task_runtime import TaskRuntime


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


class PlanRuntimeTests(unittest.TestCase):
    def test_runtime_persists_and_syncs_plan_to_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            task_runtime = TaskRuntime.from_workspace(workspace)
            plan_runtime = PlanRuntime.from_workspace(workspace)
            mutation = plan_runtime.update_plan(
                [
                    {
                        'step': 'Inspect the runtime loop',
                        'status': 'in_progress',
                        'description': 'Read the core agent files first.',
                    },
                    {
                        'step': 'Patch the tool registry',
                        'status': 'pending',
                    },
                ],
                explanation='Work through the runtime in two phases.',
                task_runtime=task_runtime,
            )
            rendered_plan = plan_runtime.render_plan()
            rendered_tasks = task_runtime.render_tasks()

        self.assertEqual(mutation.after_count, 2)
        self.assertEqual(mutation.synced_tasks, 2)
        self.assertIn('Inspect the runtime loop', rendered_plan)
        self.assertIn('Work through the runtime in two phases.', rendered_plan)
        self.assertIn('Inspect the runtime loop', rendered_tasks)

    def test_plan_tools_execute_against_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            task_runtime = TaskRuntime.from_workspace(workspace)
            plan_runtime = PlanRuntime.from_workspace(workspace)
            context = build_tool_context(
                AgentRuntimeConfig(
                    cwd=workspace,
                    permissions=AgentPermissions(allow_file_write=True),
                ),
                plan_runtime=plan_runtime,
                task_runtime=task_runtime,
            )
            update_result = execute_tool(
                default_tool_registry(),
                'update_plan',
                {
                    'explanation': 'Follow the current plan.',
                    'items': [
                        {'step': 'Inspect the workspace', 'status': 'completed'},
                        {'step': 'Implement the fix', 'status': 'in_progress'},
                    ],
                },
                context,
            )
            get_result = execute_tool(
                default_tool_registry(),
                'plan_get',
                {},
                context,
            )
            clear_result = execute_tool(
                default_tool_registry(),
                'plan_clear',
                {'sync_tasks': True},
                context,
            )

        self.assertTrue(update_result.ok)
        self.assertEqual(update_result.metadata.get('total_steps'), 2)
        self.assertEqual(update_result.metadata.get('synced_tasks'), 2)
        self.assertIn('# Plan', get_result.content)
        self.assertTrue(clear_result.ok)
        self.assertEqual(clear_result.metadata.get('total_steps'), 0)

    def test_agent_can_use_update_plan_tool_in_model_loop(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will store the plan first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'update_plan',
                                        'arguments': json.dumps(
                                            {
                                                'explanation': 'Start with a plan.',
                                                'items': [
                                                    {
                                                        'step': 'Inspect the current files',
                                                        'status': 'in_progress',
                                                    },
                                                    {
                                                        'step': 'Apply the code changes',
                                                        'status': 'pending',
                                                    },
                                                ],
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
                            'content': 'The plan was stored successfully.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
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
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                result = agent.run('Store the current plan')
                self.assertTrue((workspace / '.port_sessions' / 'plan_runtime.json').exists())

        self.assertEqual(result.final_output, 'The plan was stored successfully.')
        self.assertEqual(result.tool_calls, 1)
        tool_message = next(
            message for message in result.transcript if message.get('role') == 'tool'
        )
        self.assertIn('update_plan', tool_message.get('content', ''))
