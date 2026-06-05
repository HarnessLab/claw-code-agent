"""recall_memory tool — exposes LattiMemoryStore.recall to the LLM.

Pre-fix: typed scar/SOP/lesson records existed at ~/.latti/memory/ but
no tool surface let the LLM query them mid-turn. They were dormant.
Post-fix: a registered tool routes (query, kind, limit) into
LattiMemoryStore.recall and returns formatted results the LLM can read.

Tool is registered in default_tool_registry so every Latti session
gets it without per-config wiring.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent_state_machine import MemoryRecord
from src.agent_tools import default_tool_registry
from src.state_machine_memory import LattiMemoryStore


class TestRecallMemoryTool(unittest.TestCase):
    def test_tool_is_registered_in_default_registry(self) -> None:
        registry = default_tool_registry()
        self.assertIn(
            'recall_memory', registry,
            f'recall_memory must be in default registry; got {sorted(registry.keys())}',
        )

    def test_tool_has_required_query_parameter(self) -> None:
        registry = default_tool_registry()
        tool = registry['recall_memory']
        self.assertIn('query', tool.parameters.get('properties', {}))
        self.assertIn('query', tool.parameters.get('required', []))

    def test_tool_handler_calls_recall_and_formats_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            rec = MemoryRecord(
                id='mem_test_1', kind='scar',
                body='never force push to main — broke prod 2025-12',
                last_used=time.time(),
            )
            store.save(rec, name='force_push_main', description='force push scar')

            # Point the tool at the temp memory dir via env var
            with patch.dict(os.environ, {'LATTI_MEMORY_DIR': tmp}):
                registry = default_tool_registry()
                handler = registry['recall_memory'].handler
                # Handler signature: (arguments, context). Build minimal context.
                from src.agent_tools import build_tool_context
                from src.agent_types import AgentRuntimeConfig
                ctx = build_tool_context(AgentRuntimeConfig(cwd=Path(tmp)))
                result = handler({'query': 'force push main'}, ctx)

        # Result should be a string the LLM can read
        self.assertIsInstance(result, str)
        self.assertIn('force', result.lower())
        # Should mention the kind so the LLM knows what type of memory
        self.assertIn('scar', result.lower())

    def test_tool_handler_returns_no_match_message_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'LATTI_MEMORY_DIR': tmp}):
                registry = default_tool_registry()
                handler = registry['recall_memory'].handler
                from src.agent_tools import build_tool_context
                from src.agent_types import AgentRuntimeConfig
                ctx = build_tool_context(AgentRuntimeConfig(cwd=Path(tmp)))
                result = handler({'query': 'nothing here'}, ctx)
        self.assertIsInstance(result, str)
        # Empty store + nothing matches → handler must return a clear
        # "no matches" message rather than an empty string (which the
        # LLM might misread as a silent error).
        self.assertGreater(len(result.strip()), 0)
        self.assertIn('no', result.lower())

    def test_tool_handler_respects_kind_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            store.save(MemoryRecord(id='m1', kind='scar', body='force push danger', last_used=time.time()),
                       name='a', description='scar a')
            store.save(MemoryRecord(id='m2', kind='sop', body='force test edge cases', last_used=time.time()),
                       name='b', description='sop b')

            with patch.dict(os.environ, {'LATTI_MEMORY_DIR': tmp}):
                registry = default_tool_registry()
                handler = registry['recall_memory'].handler
                from src.agent_tools import build_tool_context
                from src.agent_types import AgentRuntimeConfig
                ctx = build_tool_context(AgentRuntimeConfig(cwd=Path(tmp)))
                result = handler({'query': 'force', 'kind': 'sop'}, ctx)

        self.assertIn('sop', result.lower())
        # The 'scar' record should NOT appear when kind='sop' was passed
        self.assertNotIn('force push danger', result)


if __name__ == '__main__':
    unittest.main()
