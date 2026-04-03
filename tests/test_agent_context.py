from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_context import (
    build_context_snapshot,
    clear_context_caches,
    set_system_prompt_injection,
)
from src.plan_runtime import PlanRuntime
from src.agent_types import AgentRuntimeConfig
from src.task_runtime import TaskRuntime


class AgentContextTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_system_prompt_injection(None)
        clear_context_caches()

    def test_user_context_loads_project_claude_md_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo' / 'nested'
            workspace.mkdir(parents=True)
            (workspace.parent / 'CLAUDE.md').write_text('root instructions\n', encoding='utf-8')
            (workspace / 'CLAUDE.local.md').write_text('local instructions\n', encoding='utf-8')

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('currentDate', snapshot.user_context)
        self.assertIn('claudeMd', snapshot.user_context)
        self.assertIn('root instructions', snapshot.user_context['claudeMd'])
        self.assertIn('local instructions', snapshot.user_context['claudeMd'])

    def test_system_context_includes_cache_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            set_system_prompt_injection('debug-token')
            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=Path(tmp_dir)))

        self.assertEqual(snapshot.system_context['cacheBreaker'], '[CACHE_BREAKER: debug-token]')

    def test_user_context_loads_plugin_cache_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo'
            workspace.mkdir(parents=True)
            plugin_cache = workspace / '.port_sessions' / 'plugin_cache.json'
            plugin_cache.parent.mkdir(parents=True, exist_ok=True)
            plugin_cache.write_text(
                '{"plugins":[{"name":"demo-plugin","version":"1.2.3","enabled":true}]}',
                encoding='utf-8',
            )

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('pluginCache', snapshot.user_context)
        self.assertIn('demo-plugin', snapshot.user_context['pluginCache'])
        self.assertIn('1.2.3', snapshot.user_context['pluginCache'])

    def test_user_context_loads_hook_policy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo'
            workspace.mkdir(parents=True)
            (workspace / '.claw-policy.json').write_text(
                (
                    '{"trusted": false, '
                    '"managedSettings": {"reviewMode": "strict"}, '
                    '"safeEnv": ["HOOK_SAFE_TOKEN"], '
                    '"hooks": {"beforePrompt": ["Respect workspace policy."]}}'
                ),
                encoding='utf-8',
            )
            with patch.dict('os.environ', {'HOOK_SAFE_TOKEN': 'demo-secret'}, clear=False):
                snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('hookPolicy', snapshot.user_context)
        self.assertIn('managedSettings', snapshot.user_context)
        self.assertIn('safeEnv', snapshot.user_context)
        self.assertIn('trustMode', snapshot.user_context)
        self.assertIn('reviewMode=strict', snapshot.user_context['managedSettings'])
        self.assertIn('HOOK_SAFE_TOKEN=demo-secret', snapshot.user_context['safeEnv'])
        self.assertIn('untrusted', snapshot.user_context['trustMode'])

    def test_user_context_loads_mcp_runtime_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo'
            workspace.mkdir(parents=True)
            (workspace / 'notes.txt').write_text('mcp notes\n', encoding='utf-8')
            (workspace / '.claw-mcp.json').write_text(
                (
                    '{"servers":[{"name":"workspace","resources":['
                    '{"uri":"mcp://workspace/notes","name":"Notes","path":"notes.txt"}'
                    ']}]}'
                ),
                encoding='utf-8',
            )

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('mcpRuntime', snapshot.user_context)
        self.assertIn('Local MCP resources: 1', snapshot.user_context['mcpRuntime'])

    def test_user_context_loads_task_runtime_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo'
            workspace.mkdir(parents=True)
            runtime = TaskRuntime.from_workspace(workspace)
            runtime.create_task(title='Review task runtime')

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('taskRuntime', snapshot.user_context)
        self.assertIn('Total tasks: 1', snapshot.user_context['taskRuntime'])

    def test_user_context_loads_plan_runtime_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / 'repo'
            workspace.mkdir(parents=True)
            plan_runtime = PlanRuntime.from_workspace(workspace)
            plan_runtime.update_plan(
                [{'step': 'Inspect the runtime', 'status': 'in_progress'}],
                explanation='Use a stored plan.',
            )

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        self.assertIn('planRuntime', snapshot.user_context)
        self.assertIn('Total plan steps: 1', snapshot.user_context['planRuntime'])

    @unittest.skipIf(shutil.which('git') is None, 'git is required for git context tests')
    def test_git_status_snapshot_contains_branch_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            subprocess.run(['git', 'init', '-b', 'main'], cwd=workspace, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Tester'], cwd=workspace, check=True)
            subprocess.run(['git', 'config', 'user.email', 'tester@example.com'], cwd=workspace, check=True)
            (workspace / 'tracked.txt').write_text('hello\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'tracked.txt'], cwd=workspace, check=True)
            subprocess.run(['git', 'commit', '-m', 'initial'], cwd=workspace, check=True)
            (workspace / 'tracked.txt').write_text('changed\n', encoding='utf-8')

            snapshot = build_context_snapshot(AgentRuntimeConfig(cwd=workspace))

        git_status = snapshot.system_context.get('gitStatus', '')
        self.assertIn('Current branch: main', git_status)
        self.assertIn('Status:', git_status)
        self.assertIn('tracked.txt', git_status)
