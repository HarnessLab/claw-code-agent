from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_runtime import LocalCodingAgent
from src.agent_slash_commands import looks_like_command, parse_slash_command
from src.agent_types import AgentRuntimeConfig, ModelConfig
from src.plan_runtime import PlanRuntime
from src.task_runtime import TaskRuntime


class AgentSlashCommandTests(unittest.TestCase):
    def test_parse_slash_command(self) -> None:
        parsed = parse_slash_command('/context extra args')
        assert parsed is not None
        self.assertEqual(parsed.command_name, 'context')
        self.assertEqual(parsed.args, 'extra args')
        self.assertFalse(parsed.is_mcp)

    def test_looks_like_command(self) -> None:
        self.assertTrue(looks_like_command('context'))
        self.assertFalse(looks_like_command('foo/bar'))

    def test_model_command_updates_agent_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            result = agent.run('/model local/test-model')
        self.assertIn('Set model to local/test-model', result.final_output)
        self.assertEqual(agent.model_config.model, 'local/test-model')

    def test_unknown_command_returns_local_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            result = agent.run('/unknown-command')
        self.assertEqual(result.final_output, 'Unknown skill: unknown-command')

    def test_context_command_renders_usage_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / 'CLAUDE.md').write_text('repo instructions\n', encoding='utf-8')
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=workspace),
            )
            result = agent.run('/context')
        self.assertIn('## Context Usage', result.final_output)
        self.assertIn('### Estimated usage by category', result.final_output)
        self.assertIn('### Memory Files', result.final_output)

    def test_mcp_and_resource_commands_render_local_reports(self) -> None:
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
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=workspace),
            )
            mcp_result = agent.run('/mcp')
            resources_result = agent.run('/resources')
            resource_result = agent.run('/resource mcp://workspace/notes')
            legacy_mcp_result = agent.run('/mcp (MCP)')
        self.assertIn('# MCP', mcp_result.final_output)
        self.assertIn('Local MCP resources: 1', mcp_result.final_output)
        self.assertIn('# MCP Resources', resources_result.final_output)
        self.assertIn('mcp://workspace/notes', resources_result.final_output)
        self.assertIn('# MCP Resource', resource_result.final_output)
        self.assertIn('mcp notes', resource_result.final_output)
        self.assertIn('# MCP', legacy_mcp_result.final_output)

    def test_tasks_and_task_commands_render_local_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = TaskRuntime.from_workspace(workspace)
            mutation = runtime.create_task(
                title='Review runtime tasks',
                status='in_progress',
            )
            task_id = mutation.task.task_id if mutation.task is not None else ''
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=workspace),
            )
            tasks_result = agent.run('/tasks')
            task_result = agent.run(f'/task {task_id}')
            todo_result = agent.run('/todo in_progress')
        self.assertIn('# Tasks', tasks_result.final_output)
        self.assertIn(task_id, tasks_result.final_output)
        self.assertIn('# Task', task_result.final_output)
        self.assertIn('in_progress', task_result.final_output)
        self.assertIn('# Tasks', todo_result.final_output)

    def test_plan_command_renders_local_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            plan_runtime = PlanRuntime.from_workspace(workspace)
            plan_runtime.update_plan(
                [{'step': 'Inspect the plan command', 'status': 'in_progress'}],
                explanation='Use the local plan runtime.',
            )
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=workspace),
            )
            plan_result = agent.run('/plan')
        self.assertIn('# Plan', plan_result.final_output)
        self.assertIn('Inspect the plan command', plan_result.final_output)

    def test_tools_and_status_commands_render_local_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            tools_result = agent.run('/tools')
            status_result = agent.run('/status')
        self.assertIn('# Tools', tools_result.final_output)
        self.assertIn('`read_file`', tools_result.final_output)
        self.assertIn('# Status', status_result.final_output)
        self.assertIn('Last run: none', status_result.final_output)

    def test_hooks_and_trust_commands_render_local_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / '.claw-policy.json').write_text(
                (
                    '{"trusted": false, '
                    '"managedSettings": {"reviewMode": "strict"}, '
                    '"safeEnv": ["HOOK_SAFE_TOKEN"]}'
                ),
                encoding='utf-8',
            )
            with patch.dict('os.environ', {'HOOK_SAFE_TOKEN': 'demo-secret'}, clear=False):
                agent = LocalCodingAgent(
                    model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                    runtime_config=AgentRuntimeConfig(cwd=workspace),
                )
                hooks_result = agent.run('/hooks')
                trust_result = agent.run('/trust')
        self.assertIn('# Hook Policy', hooks_result.final_output)
        self.assertIn('Local hook/policy manifests', hooks_result.final_output)
        self.assertIn('# Trust', trust_result.final_output)
        self.assertIn('untrusted', trust_result.final_output)
        self.assertIn('reviewMode=strict', trust_result.final_output)
        self.assertIn('HOOK_SAFE_TOKEN=demo-secret', trust_result.final_output)

    def test_clear_command_clears_saved_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            agent.last_session = agent.build_session('hello')
            agent.last_run_result = object()  # type: ignore[assignment]
            result = agent.run('/clear')
        self.assertIn('Cleared ephemeral Python agent state', result.final_output)
        self.assertIsNone(agent.last_session)
        self.assertIsNone(agent.last_run_result)
