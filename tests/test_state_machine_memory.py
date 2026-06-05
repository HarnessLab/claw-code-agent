"""Tests for LattiMemoryStore — typed MemoryRecord persistence to disk."""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from src.agent_state_machine import MemoryRecord
from src.state_machine_memory import LattiMemoryStore


def test_save_writes_frontmatter_and_body(tmp_path):
    store = LattiMemoryStore(tmp_path)
    r = MemoryRecord.new(kind='scar', body='YOUR INSTINCT: x\nWHAT WORKS: y\nTRIGGER: z')
    path = store.save(r, name='test_scar', description='a test scar')

    assert path.exists()
    content = path.read_text()
    assert content.startswith('---\n')
    assert 'name: test_scar' in content
    assert 'description: a test scar' in content
    assert 'type: scar' in content
    assert f'id: {r.id}' in content
    assert 'YOUR INSTINCT: x' in content


def test_filename_uses_kind_and_slug(tmp_path):
    store = LattiMemoryStore(tmp_path)
    r = MemoryRecord.new(kind='sop', body='step 1; step 2')
    path = store.save(r, name='Some Mixed-Case Name!')
    assert path.name == 'sop_some_mixed_case_name.md'


def test_round_trip_save_then_load(tmp_path):
    store = LattiMemoryStore(tmp_path)
    original = MemoryRecord.new(
        kind='lesson',
        body='Lesson body content here.',
        source_session_id='sess_42',
        source_turn_id='turn_99',
    )
    path = store.save(original, name='roundtrip', description='round-trip test')

    loaded = store.load(path)
    assert loaded is not None
    assert loaded.kind == 'lesson'
    assert loaded.body == 'Lesson body content here.'
    assert loaded.source_session_id == 'sess_42'
    assert loaded.source_turn_id == 'turn_99'


def test_index_file_updated_on_save(tmp_path):
    store = LattiMemoryStore(tmp_path)
    r = MemoryRecord.new(kind='scar', body='body')
    store.save(r, name='indexed', description='check the index')

    index = (tmp_path / 'MEMORY.md').read_text()
    assert '[scar_indexed.md](scar_indexed.md)' in index
    assert 'check the index' in index


def test_index_does_not_duplicate_same_file(tmp_path):
    store = LattiMemoryStore(tmp_path)
    r1 = MemoryRecord.new(kind='scar', body='one')
    r2 = MemoryRecord.new(kind='scar', body='two — same slug, different id')
    store.save(r1, name='samename')
    store.save(r2, name='samename')

    index = (tmp_path / 'MEMORY.md').read_text()
    # Same filename → only one index entry
    assert index.count('[scar_samename.md](scar_samename.md)') == 1


def test_list_records_filters_by_kind(tmp_path):
    store = LattiMemoryStore(tmp_path)
    store.save(MemoryRecord.new(kind='scar', body='s'), name='a')
    store.save(MemoryRecord.new(kind='sop', body='o'), name='b')
    store.save(MemoryRecord.new(kind='scar', body='s2'), name='c')

    scars = store.list_records(kind='scar')
    sops = store.list_records(kind='sop')
    assert len(scars) == 2
    assert len(sops) == 1
    assert all(r.kind == 'scar' for r in scars)


def test_list_records_no_filter_returns_all(tmp_path):
    store = LattiMemoryStore(tmp_path)
    store.save(MemoryRecord.new(kind='scar', body='s'), name='a')
    store.save(MemoryRecord.new(kind='sop', body='o'), name='b')
    all_recs = store.list_records()
    assert len(all_recs) == 2


def test_atomic_save_no_partial_file_on_replace(tmp_path):
    """Save uses tempfile + rename so no partial files linger after success."""
    store = LattiMemoryStore(tmp_path)
    r = MemoryRecord.new(kind='reference', body='x')
    store.save(r, name='atomic')
    # No .tmp.* artifacts
    leftover = list(tmp_path.glob('*.tmp.*'))
    assert leftover == []


def test_load_returns_none_for_nonexistent_path(tmp_path):
    store = LattiMemoryStore(tmp_path)
    assert store.load(tmp_path / 'does_not_exist.md') is None


def test_load_returns_none_for_file_without_frontmatter(tmp_path):
    store = LattiMemoryStore(tmp_path)
    plain = tmp_path / 'plain.md'
    plain.write_text('no frontmatter here\n')
    assert store.load(plain) is None


def test_legacy_feedback_kind_coerced_to_scar(tmp_path):
    """Pre-existing files use type: feedback (not in MemoryKind enum). Loader
    should coerce to a valid MemoryKind so old scars are still readable."""
    store = LattiMemoryStore(tmp_path)
    legacy = tmp_path / 'feedback_legacy.md'
    legacy.write_text(
        '---\n'
        'name: legacy\n'
        'description: legacy feedback\n'
        'type: feedback\n'
        'last_used: 2026-04-28\n'
        '---\n'
        'YOUR INSTINCT: x\nWORKS: y\nTRIGGER: z\n',
    )
    rec = store.load(legacy)
    assert rec is not None
    assert rec.kind == 'scar'  # coerced from legacy 'feedback'
    assert 'YOUR INSTINCT' in rec.body
