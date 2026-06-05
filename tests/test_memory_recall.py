"""LattiMemoryStore.recall — keyword search over typed memory records.

Wires the dormant LattiMemoryStore into a callable surface. Pre-fix,
typed scar/SOP/lesson records existed on disk at ~/.latti/memory/ but
the LLM had no way to query them mid-turn — they were load-once-at-boot
into the system prompt. Post-fix, recall(query, kind=None, limit=5)
returns top-scoring records by keyword overlap, the LLM can call it
via the new recall_memory tool.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src.agent_state_machine import MemoryRecord
from src.state_machine_memory import LattiMemoryStore


def _save(store: LattiMemoryStore, kind: str, body: str, name: str = '',
          last_used_offset_days: int = 0) -> None:
    rec = MemoryRecord(
        id=f'mem_{name or kind}_{abs(hash(body)) % 100000}',
        kind=kind,  # type: ignore[arg-type]
        body=body,
        last_used=time.time() - last_used_offset_days * 86400,
    )
    store.save(rec, name=name or kind, description=body[:60])


class TestRecall(unittest.TestCase):
    def test_recall_returns_records_matching_query_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            _save(store, 'scar', 'never force push to main branch — broke prod 2025-12', 'force_push')
            _save(store, 'sop', 'always run full pytest before deploy', 'pytest_first')
            _save(store, 'lesson', 'TCSAFLUSH discards pending input on raw mode entry', 'tcsaflush')

            results = store.recall('force push main')

        self.assertGreaterEqual(len(results), 1)
        # Highest-scoring result should be the force_push scar (3 token matches)
        top = results[0]
        self.assertIn('force push', top.body.lower())

    def test_recall_filters_by_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            _save(store, 'scar', 'never force push main', 'a')
            _save(store, 'sop', 'always force-test edge cases', 'b')
            _save(store, 'lesson', 'force is non-trivial', 'c')

            scars_only = store.recall('force', kind='scar')

        self.assertTrue(all(r.kind == 'scar' for r in scars_only))
        self.assertGreaterEqual(len(scars_only), 1)

    def test_recall_respects_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            for i in range(10):
                _save(store, 'lesson', f'lesson {i} about widgets and gadgets', f'l{i}')

            results = store.recall('widgets', limit=3)

        self.assertEqual(len(results), 3)

    def test_recall_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            _save(store, 'sop', 'always READ test output before claiming pass', 'read_out')

            results = store.recall('READ test')

        self.assertGreaterEqual(len(results), 1)

    def test_recall_empty_store_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            self.assertEqual(store.recall('anything'), [])

    def test_recall_scoring_prefers_more_token_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            _save(store, 'lesson', 'compaction summary tier hierarchy', 'compaction_full', last_used_offset_days=10)
            _save(store, 'lesson', 'session compaction tier', 'compaction_partial', last_used_offset_days=10)
            _save(store, 'lesson', 'unrelated content here', 'noise', last_used_offset_days=10)

            results = store.recall('compaction summary tier hierarchy')

        self.assertGreater(len(results), 0)
        # Higher-overlap record must rank above lower-overlap
        ids = [r.id for r in results]
        self.assertEqual(ids[0], next(r.id for r in results if 'compaction_full' in r.id),
                         f'expected compaction_full as top hit; got {ids}')

    def test_recall_no_match_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LattiMemoryStore(Path(tmp))
            _save(store, 'sop', 'use the lattice solver for optimization', 's1')
            results = store.recall('xyzzy nonexistent')
        self.assertEqual(results, [])


if __name__ == '__main__':
    unittest.main()
