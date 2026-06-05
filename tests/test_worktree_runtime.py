from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_tools import build_tool_context, default_tool_registry, execute_tool
from src.agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig
from src.worktree_runtime import WorktreeRuntime


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode('utf-8')

    def __enter__(self) -> '_FakeHTTPResponse':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _make_urlopen_side_effect(responses: list[dict[str, object]]):
    queued = [_FakeHTTPResponse(payload) for payload in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


def _init_git_repo(workspace: Path) -> None:
    subprocess.run(['git', 'init', '-q'], cwd=workspace, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=workspace, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=workspace, check=True)
    (workspace / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'README.md'], cwd=workspace, check=True)
    subprocess.run(['git', 'commit', '-qm', 'init'], cwd=workspace, check=True)


@unittest.skipUnless(shutil.which('git'), 'git is required for worktree tests')
class WorktreeRuntimeTests(unittest.TestCase):
    def test_worktree_runtime_enters_and_exits_managed_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _init_git_repo(workspace)
            runtime = WorktreeRuntime.from_workspace(workspace)
            enter_report = runtime.enter('feature-preview')
            worktree_path = Path(enter_report.worktree_path or '')
            exit_report = runtime.exit(action='keep')

        self.assertTrue(enter_report.active)
        self.assertTrue(worktree_path.exists())
        self.assertIn('feature-preview', enter_report.worktree_branch or '')
        self.assertFalse(exit_report.active)
        self.assertEqual(Path(exit_report.original_cwd or '').resolve(), workspace.resolve())

    def test_worktree_tools_execute_against_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _init_git_repo(workspace)
            runtime = WorktreeRuntime.from_workspace(workspace)
            context = build_tool_context(
                AgentRuntimeConfig(
                    cwd=workspace,
                    permissions=AgentPermissions(allow_file_write=True),
                ),
                worktree_runtime=runtime,
            )
            enter_result = execute_tool(
                default_tool_registry(),
                'worktree_enter',
                {'name': 'preview'},
                context,
            )
            status_result = execute_tool(
                default_tool_registry(),
                'worktree_status',
                {},
                context,
            )
            exit_result = execute_tool(
                default_tool_registry(),
                'worktree_exit',
                {'action': 'remove', 'discard_changes': True},
                context,
            )

        self.assertTrue(enter_result.ok)
        self.assertIn('preview', enter_result.content)
        self.assertEqual(enter_result.metadata.get('action'), 'worktree_enter')
        self.assertTrue(status_result.ok)
        self.assertIn('Active managed worktree: True', status_result.content)
        self.assertTrue(exit_result.ok)
        self.assertEqual(exit_result.metadata.get('action'), 'worktree_exit')

    def test_agent_switches_cwd_after_worktree_enter(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Entering worktree.',
                            'tool_calls': [
                                {
                                    'id': 'call_enter',
                                    'type': 'function',
                                    'function': {
                                        'name': 'worktree_enter',
                                        'arguments': '{"name":"preview"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Writing inside the worktree.',
                            'tool_calls': [
                                {
                                    'id': 'call_write',
                                    'type': 'function',
                                    'function': {
                                        'name': 'write_file',
                                        'arguments': '{"path":"note.txt","content":"from worktree\\n"}',
                                    },
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'done',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 1},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            _init_git_repo(workspace)
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=_make_urlopen_side_effect(responses),
            ):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                    runtime_config=AgentRuntimeConfig(
                        cwd=workspace,
                        permissions=AgentPermissions(allow_file_write=True),
                    ),
                )
                result = agent.run('Use a worktree and write a file there')
                runtime = WorktreeRuntime.from_workspace(workspace)
                assert runtime.active_session is not None
                worktree_path = Path(runtime.active_session.worktree_path)

        self.assertEqual(result.final_output, 'done')
        self.assertFalse((workspace / 'note.txt').exists())
        self.assertTrue((worktree_path / 'note.txt').exists())
        self.assertEqual(agent.runtime_config.cwd, worktree_path.resolve())
