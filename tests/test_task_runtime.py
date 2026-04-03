from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig
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


class TaskRuntimeTests(unittest.TestCase):
    def test_runtime_persists_and_renders_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = TaskRuntime.from_workspace(workspace)
            created = runtime.create_task(
                title='Implement task runtime',
                description='Add persistent tasks.',
                status='in_progress',
            )
            assert created.task is not None
            runtime.update_task(created.task.task_id, status='done')
            rendered_tasks = runtime.render_tasks()
            rendered_task = runtime.render_task(created.task.task_id)

        self.assertIn('Implement task runtime', rendered_tasks)
        self.assertIn('done', rendered_task)

    def test_task_tools_execute_against_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = TaskRuntime.from_workspace(workspace)
            context = build_tool_context(
                AgentRuntimeConfig(
                    cwd=workspace,
                    permissions=AgentPermissions(allow_file_write=True),
                ),
                task_runtime=runtime,
            )
            create_result = execute_tool(
                default_tool_registry(),
                'task_create',
                {'title': 'Review task tools', 'status': 'todo'},
                context,
            )
            self.assertTrue(create_result.ok)
            task_id = str(create_result.metadata.get('task_id'))
            list_result = execute_tool(
                default_tool_registry(),
                'task_list',
                {},
                context,
            )
            get_result = execute_tool(
                default_tool_registry(),
                'task_get',
                {'task_id': task_id},
                context,
            )
            update_result = execute_tool(
                default_tool_registry(),
                'task_update',
                {'task_id': task_id, 'status': 'done'},
                context,
            )
            todo_result = execute_tool(
                default_tool_registry(),
                'todo_write',
                {'items': [{'title': 'Replace with todo snapshot', 'status': 'in_progress'}]},
                context,
            )

        self.assertIn(task_id, list_result.content)
        self.assertIn('Review task tools', get_result.content)
        self.assertTrue(update_result.ok)
        self.assertEqual(update_result.metadata.get('task_status'), 'done')
        self.assertTrue(todo_result.ok)
        self.assertEqual(todo_result.metadata.get('total_tasks'), 1)

    def test_agent_can_use_task_tools_in_model_loop(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'I will create a task first.',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {
                                        'name': 'task_create',
                                        'arguments': '{"title": "Review runtime tasks", "status": "todo"}',
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
                            'content': 'The task was created successfully.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 3},
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
                result = agent.run('Create a task for the current work')
                self.assertTrue((workspace / '.port_sessions' / 'task_runtime.json').exists())

        self.assertEqual(result.final_output, 'The task was created successfully.')
        self.assertEqual(result.tool_calls, 1)
        tool_message = next(
            message
            for message in result.transcript
            if message.get('role') == 'tool'
        )
        self.assertIn('task_create', tool_message.get('content', ''))
