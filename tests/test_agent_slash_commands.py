from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent_runtime import LocalCodingAgent
from src.agent_slash_commands import looks_like_command, parse_slash_command
from src.agent_types import AgentRuntimeConfig, ModelConfig


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
