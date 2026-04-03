from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent_prompting import build_prompt_context, build_system_prompt_parts, render_system_prompt
from src.plan_runtime import PlanRuntime
from src.agent_runtime import LocalCodingAgent
from src.agent_session import AgentSessionState
from src.agent_tools import default_tool_registry
from src.agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig
from src.task_runtime import TaskRuntime


class AgentPromptingTests(unittest.TestCase):
    def test_prompt_builder_contains_expected_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_config = AgentRuntimeConfig(
                cwd=Path(tmp_dir),
                permissions=AgentPermissions(
                    allow_file_write=True,
                    allow_shell_commands=False,
                ),
            )
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# System', prompt)
        self.assertIn('# Doing tasks', prompt)
        self.assertIn('# Using your tools', prompt)
        self.assertIn('# Environment', prompt)
        self.assertIn('__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__', prompt)
        self.assertIn('Primary working directory:', prompt)

    def test_session_state_exports_messages_in_order(self) -> None:
        state = AgentSessionState.create(['sys one', 'sys two'], 'hello')
        state.append_assistant('working', ())
        state.append_tool('read_file', 'call_1', '{"ok": true}')
        messages = state.to_openai_messages()
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')
        self.assertEqual(messages[2]['role'], 'assistant')
        self.assertEqual(messages[3]['role'], 'tool')
        self.assertEqual(messages[3]['tool_call_id'], 'call_1')

    def test_agent_can_render_prompt_without_contacting_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp_dir)),
            )
            prompt = agent.render_system_prompt()
        self.assertIn('Claw Code Python', prompt)
        self.assertIn('# System', prompt)
        self.assertIn('# Environment', prompt)

    def test_prompt_builder_mentions_plugins_when_cache_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            plugin_cache = workspace / '.port_sessions' / 'plugin_cache.json'
            plugin_cache.parent.mkdir(parents=True, exist_ok=True)
            plugin_cache.write_text(
                '{"plugins":[{"name":"example-plugin","enabled":true}]}',
                encoding='utf-8',
            )
            runtime_config = AgentRuntimeConfig(cwd=workspace)
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# Plugins', prompt)

    def test_prompt_builder_mentions_hook_policy_when_manifest_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / '.claw-policy.json').write_text(
                '{"trusted": false, "hooks": {"beforePrompt": ["Follow workspace policy."]}}',
                encoding='utf-8',
            )
            runtime_config = AgentRuntimeConfig(cwd=workspace)
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# Hook Policy', prompt)

    def test_prompt_builder_mentions_mcp_when_manifest_is_loaded(self) -> None:
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
            runtime_config = AgentRuntimeConfig(cwd=workspace)
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# MCP', prompt)

    def test_prompt_builder_mentions_tasks_when_runtime_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = TaskRuntime.from_workspace(workspace)
            runtime.create_task(title='Inspect runtime tasks')
            runtime_config = AgentRuntimeConfig(cwd=workspace)
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# Tasks', prompt)

    def test_prompt_builder_mentions_planning_when_runtime_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = PlanRuntime.from_workspace(workspace)
            runtime.update_plan(
                [{'step': 'Inspect runtime planning', 'status': 'pending'}],
                explanation='Track the current plan.',
            )
            runtime_config = AgentRuntimeConfig(cwd=workspace)
            model_config = ModelConfig(model='Qwen/Qwen3-Coder-30B-A3B-Instruct')
            prompt_context = build_prompt_context(runtime_config, model_config)
            parts = build_system_prompt_parts(
                prompt_context=prompt_context,
                runtime_config=runtime_config,
                tools=default_tool_registry(),
            )

        prompt = render_system_prompt(parts)
        self.assertIn('# Planning', prompt)
