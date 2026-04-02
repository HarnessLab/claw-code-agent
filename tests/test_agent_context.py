from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.agent_context import (
    build_context_snapshot,
    clear_context_caches,
    set_system_prompt_injection,
)
from src.agent_types import AgentRuntimeConfig


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
