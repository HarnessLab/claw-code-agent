from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.agent_prompting import build_prompt_context, build_system_prompt_parts, render_system_prompt
from src.agent_runtime import LocalCodingAgent
from src.agent_session import AgentSessionState
from src.agent_tools import default_tool_registry
from src.agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig


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
