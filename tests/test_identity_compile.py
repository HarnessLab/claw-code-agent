# tests/test_identity_compile.py
"""Tests for identity_compile.

The compiler reads typed MemoryRecord files from a memory directory and
produces ~/.latti/IDENTITY.md (now-file) + ~/.latti/HISTORY.md (history).
All tests use tmp_path; no test touches the real ~/.latti/.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_typed_record(memory_dir: Path, kind: str, slug: str, body: str,
                        last_used: str = '2026-05-01') -> Path:
    """Write a typed MemoryRecord file directly (matches LattiMemoryStore format)."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f'{kind}_{slug}.md'
    path.write_text(
        f'---\n'
        f'name: {slug}\n'
        f'description: test record\n'
        f'type: {kind}\n'
        f'id: mem_{slug}\n'
        f'last_used: {last_used}\n'
        f'---\n'
        f'{body}\n',
        encoding='utf-8',
    )
    return path


def _write_legacy_file(memory_dir: Path, name: str, body: str) -> Path:
    """Write a no-frontmatter legacy file (must be invisible to compiler)."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / name
    path.write_text(body, encoding='utf-8')
    return path


def test_load_typed_records_filters_legacy(tmp_path):
    from src.identity_compile import load_typed_records

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'first', 'first scar body')
    _write_typed_record(mem, 'lesson', 'second', 'second lesson body')
    _write_legacy_file(mem, 'AUDIT_DUMP.md', 'unstructured audit output')
    _write_legacy_file(mem, 'BOOT_LOG.txt', 'boot log')

    records = list(load_typed_records(mem))
    kinds = sorted(r.kind for r in records)
    assert kinds == ['lesson', 'scar']
    assert all(r.id.startswith('mem_') for r in records)


def test_load_typed_records_skips_unparseable_typed_files(tmp_path):
    from src.identity_compile import load_typed_records

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'good', 'body')
    # Looks typed (starts with ---) but malformed frontmatter
    (mem / 'scar_broken.md').write_text(
        '---\nthis is not valid: yaml: like: at all:\n', encoding='utf-8',
    )

    records = list(load_typed_records(mem))
    assert len(records) == 1
    assert records[0].id == 'mem_good'


def test_load_typed_records_empty_dir(tmp_path):
    from src.identity_compile import load_typed_records
    records = list(load_typed_records(tmp_path / 'nonexistent'))
    assert records == []


def test_records_sorted_by_frontmatter_not_mtime(tmp_path):
    """Sort key is frontmatter last_used, NOT filesystem mtime."""
    import os
    import time
    from src.identity_compile import load_typed_records_sorted

    mem = tmp_path / 'memory'
    p_old = _write_typed_record(mem, 'scar', 'old', 'old', last_used='2026-04-01')
    p_new = _write_typed_record(mem, 'scar', 'new', 'new', last_used='2026-05-01')
    # Touch the OLD file so its mtime is newest
    new_mtime = time.time()
    os.utime(p_old, (new_mtime, new_mtime))
    os.utime(p_new, (new_mtime - 86400, new_mtime - 86400))

    records = list(load_typed_records_sorted(mem))
    # Should be sorted oldest first by frontmatter date
    assert [r.id for r in records] == ['mem_old', 'mem_new']


def test_substrate_sha_stable_across_identical_compiles(tmp_path):
    """Two consecutive sha computations on unchanged files → same sha."""
    from src.identity_compile import compute_substrate_sha

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'body a')
    _write_typed_record(mem, 'lesson', 'b', 'body b')

    sha1 = compute_substrate_sha(mem)
    sha2 = compute_substrate_sha(mem)
    assert sha1 == sha2
    assert len(sha1) == 64  # sha256 hex


def test_substrate_sha_changes_when_record_added(tmp_path):
    from src.identity_compile import compute_substrate_sha

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'body a')
    sha1 = compute_substrate_sha(mem)

    _write_typed_record(mem, 'lesson', 'b', 'body b')
    sha2 = compute_substrate_sha(mem)
    assert sha1 != sha2


def test_substrate_sha_ignores_legacy_files(tmp_path):
    from src.identity_compile import compute_substrate_sha

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'body')
    sha1 = compute_substrate_sha(mem)

    _write_legacy_file(mem, 'AUDIT.md', 'audit junk')
    sha2 = compute_substrate_sha(mem)
    assert sha1 == sha2  # legacy file does not affect sha


def test_where_section_with_no_records(tmp_path):
    from src.identity_compile import render_where_section
    out = render_where_section(active_goals=[], records=[])
    assert '## where I am' in out
    assert '0 typed records yet' in out
    assert 'Active goals' in out
    assert '(no active goals)' in out


def test_where_section_with_goals_and_records(tmp_path):
    from src.identity_compile import render_where_section
    from src.identity_compile import load_typed_records_sorted

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'first scar')
    _write_typed_record(mem, 'lesson', 'b', 'a lesson')
    records = load_typed_records_sorted(mem)

    class FakeGoal:
        title = 'directive compliance ≥ 0.7'
        status = 'active'
        success_criteria = ('5 consecutive sessions',)

    out = render_where_section(active_goals=[FakeGoal()], records=records)
    assert 'directive compliance' in out
    assert 'active' in out
    assert 'lesson' in out  # last record kind
    assert '5 consecutive sessions' in out


def test_learning_section_empty(tmp_path):
    from src.identity_compile import render_learning_section
    out = render_learning_section(scars=[], lessons=[])
    assert '## what I\'m learning' in out
    assert '(no scars recorded)' in out
    assert '(no lessons recorded)' in out


def test_learning_section_with_records(tmp_path):
    from src.identity_compile import render_learning_section, load_typed_records_sorted

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'first', 'first scar body line\nmore lines')
    _write_typed_record(mem, 'scar', 'second', 'second scar body')
    _write_typed_record(mem, 'lesson', 'l1', 'a lesson')
    records = load_typed_records_sorted(mem)
    scars = [r for r in records if r.kind == 'scar']
    lessons = [r for r in records if r.kind == 'lesson']

    out = render_learning_section(scars=scars, lessons=lessons)
    assert 'first scar body line' in out  # only first line, no \n
    assert 'second scar body' in out
    assert 'a lesson' in out


def test_learning_section_caps_at_5_scars_3_lessons(tmp_path):
    from src.identity_compile import render_learning_section
    from src.agent_state_machine import MemoryRecord

    scars = [MemoryRecord.new('scar', f'scar body {i}') for i in range(10)]
    lessons = [MemoryRecord.new('lesson', f'lesson body {i}') for i in range(10)]
    out = render_learning_section(scars=scars[-5:], lessons=lessons[-3:])
    assert out.count('  - scar body') == 5
    assert out.count('  - lesson body') == 3


def test_becoming_section_extracted_from_existing_identity(tmp_path):
    from src.identity_compile import extract_becoming_section

    identity_path = tmp_path / 'IDENTITY.md'
    identity_path.write_text(
        '## who I am\nstuff\n\n'
        '## who I\'m becoming\n'
        '<!-- BECOMING-SECTION-START -->\n'
        'I want to become better at noticing my own drift.\n'
        '<!-- BECOMING-SECTION-END -->\n',
        encoding='utf-8',
    )
    out = extract_becoming_section(identity_path)
    assert out is not None
    assert 'better at noticing my own drift' in out


def test_becoming_section_extract_returns_none_if_no_file(tmp_path):
    from src.identity_compile import extract_becoming_section
    out = extract_becoming_section(tmp_path / 'missing.md')
    assert out is None


def test_becoming_section_extract_returns_none_if_no_markers(tmp_path):
    from src.identity_compile import extract_becoming_section
    p = tmp_path / 'IDENTITY.md'
    p.write_text('## who I am\nbody\n', encoding='utf-8')
    out = extract_becoming_section(p)
    assert out is None


def test_becoming_section_preserved_when_user_edited_after_compile(tmp_path):
    from src.identity_compile import preserve_becoming_if_user_edited

    p = tmp_path / 'IDENTITY.md'
    p.write_text(
        '## who I\'m becoming\n'
        '<!-- BECOMING-SECTION-START -->\n'
        'user edit\n'
        '<!-- BECOMING-SECTION-END -->\n',
        encoding='utf-8',
    )
    file_mtime = p.stat().st_mtime
    out = preserve_becoming_if_user_edited(p, last_compiled_at=file_mtime - 10)
    assert out is not None
    assert 'user edit' in out


def test_becoming_section_not_preserved_when_compile_is_newer(tmp_path):
    from src.identity_compile import preserve_becoming_if_user_edited

    p = tmp_path / 'IDENTITY.md'
    p.write_text('## who I\'m becoming\n<!-- BECOMING-SECTION-START -->\nx\n<!-- BECOMING-SECTION-END -->\n', encoding='utf-8')
    file_mtime = p.stat().st_mtime
    out = preserve_becoming_if_user_edited(p, last_compiled_at=file_mtime + 10)
    assert out is None


def test_render_identity_md_assembles_all_sections(tmp_path):
    from src.identity_compile import render_identity_md

    out = render_identity_md(
        compiled_at='2026-05-01T00:00:00Z',
        generation=1,
        substrate_sha='abc123',
        prose_freshness='live',
        who_section='I am Latti.',
        where_section='## where I am\nstuff\n',
        learning_section='## what I\'m learning\nstuff\n',
        becoming_section='I want to grow.',
    )
    assert out.startswith('---\n')
    assert 'compiled_at: 2026-05-01T00:00:00Z' in out
    assert 'generation: 1' in out
    assert 'substrate_sha: abc123' in out
    assert 'prose_freshness: live' in out
    assert '## who I am\n<!-- WHO-SECTION-START -->\nI am Latti.' in out
    assert '<!-- WHO-SECTION-END -->' in out
    assert '## where I am' in out
    assert '## what I\'m learning' in out
    assert '<!-- BECOMING-SECTION-START -->' in out
    assert 'I want to grow.' in out
    assert '<!-- BECOMING-SECTION-END -->' in out
    assert 'pointers' in out


def test_who_section_extraction_robust_against_llm_headers(tmp_path):
    """Regression: LLM prose containing its own '## ' headers must not break
    extract_who_section. Markers (mirror of BECOMING) make this robust."""
    from src.identity_compile import extract_who_section, render_identity_md

    llm_body_with_headers = """## Who I am

I am a coding agent.

## What I am learning

Things."""
    rendered = render_identity_md(
        compiled_at='x', generation=1, substrate_sha='y', prose_freshness='live',
        who_section=llm_body_with_headers,
        where_section='## where I am\nstuff',
        learning_section='## what I\'m learning\nstuff',
        becoming_section='direction',
    )
    p = tmp_path / 'IDENTITY.md'
    p.write_text(rendered, encoding='utf-8')

    extracted = extract_who_section(p)
    assert extracted is not None
    assert 'I am a coding agent.' in extracted
    assert '## Who I am' in extracted  # the LLM's own header survives


def test_atomic_write_sha_gated_skips_when_unchanged(tmp_path):
    from src.identity_compile import write_identity_md_if_changed

    target = tmp_path / 'IDENTITY.md'
    content = '# hello\n'
    written1 = write_identity_md_if_changed(target, content, prior_sha=None)
    assert written1 is True
    mtime1 = target.stat().st_mtime

    import time; time.sleep(0.01)
    import hashlib
    sha = hashlib.sha256(content.encode()).hexdigest()
    written2 = write_identity_md_if_changed(target, content, prior_sha=sha)
    assert written2 is False
    assert target.stat().st_mtime == mtime1


def test_atomic_write_writes_when_content_differs(tmp_path):
    from src.identity_compile import write_identity_md_if_changed

    target = tmp_path / 'IDENTITY.md'
    write_identity_md_if_changed(target, 'content v1\n', prior_sha=None)
    written = write_identity_md_if_changed(target, 'content v2\n', prior_sha='wrong-sha')
    assert written is True
    assert target.read_text() == 'content v2\n'


def test_render_history_entry_includes_kind_id_body(tmp_path):
    from src.identity_compile import render_history_entries
    from src.agent_state_machine import MemoryRecord

    rec = MemoryRecord.new('scar', 'a scar happened\nmore detail')
    out = render_history_entries([rec])
    assert '· scar' in out
    assert rec.id in out
    assert 'a scar happened' in out


def test_load_cursor_returns_zero_when_file_absent(tmp_path):
    from src.identity_compile import load_cursor
    cur = load_cursor(tmp_path / 'no-cursor')
    assert cur == {'last_ts': 0.0, 'last_id': None}


def test_save_then_load_cursor_roundtrip(tmp_path):
    from src.identity_compile import load_cursor, save_cursor
    p = tmp_path / 'cursor.json'
    save_cursor(p, {'last_ts': 1234.5, 'last_id': 'mem_xyz'})
    cur = load_cursor(p)
    assert cur['last_ts'] == 1234.5
    assert cur['last_id'] == 'mem_xyz'


def test_history_appends_only_new_records(tmp_path):
    from src.identity_compile import (
        load_typed_records_sorted, append_new_records_to_history,
    )

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'first', 'first', last_used='2026-04-01')
    _write_typed_record(mem, 'scar', 'second', 'second', last_used='2026-04-02')

    history = tmp_path / 'HISTORY.md'
    cursor_path = tmp_path / '.history-cursor'

    appended1 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended1 == 2
    assert 'first' in history.read_text()
    assert 'second' in history.read_text()

    appended2 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended2 == 0
    body_size = history.stat().st_size

    _write_typed_record(mem, 'lesson', 'third', 'third', last_used='2026-04-03')
    appended3 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended3 == 1
    assert history.stat().st_size > body_size
    assert 'third' in history.read_text()


def test_ollama_call_returns_response_text(tmp_path):
    import urllib.error
    from unittest.mock import patch
    from src.identity_compile import call_ollama

    fake_response = b'{"response": "hello world", "eval_count": 2}'
    with patch('src.identity_compile._ollama_post', return_value=fake_response):
        out = call_ollama(
            base_url='http://localhost:11434',
            model='gemma:latest',
            prompt='test',
            temperature=0.4,
            num_predict=10,
            timeout=5,
        )
    assert out == 'hello world'


def test_ollama_call_returns_none_on_connection_error(tmp_path):
    import urllib.error
    from unittest.mock import patch
    from src.identity_compile import call_ollama

    def boom(*a, **kw):
        raise urllib.error.URLError('connection refused')

    with patch('src.identity_compile._ollama_post', side_effect=boom):
        out = call_ollama(
            base_url='http://localhost:11434', model='gemma:latest',
            prompt='test', temperature=0.4, num_predict=10, timeout=5,
        )
    assert out is None


def test_ollama_call_returns_none_on_timeout(tmp_path):
    import socket
    from unittest.mock import patch
    from src.identity_compile import call_ollama

    with patch('src.identity_compile._ollama_post', side_effect=socket.timeout()):
        out = call_ollama(
            base_url='http://localhost:11434', model='gemma:latest',
            prompt='test', temperature=0.4, num_predict=10, timeout=5,
        )
    assert out is None


def test_ollama_call_returns_none_on_malformed_json(tmp_path):
    from unittest.mock import patch
    from src.identity_compile import call_ollama

    with patch('src.identity_compile._ollama_post', return_value=b'not json'):
        out = call_ollama(
            base_url='http://localhost:11434', model='gemma:latest',
            prompt='test', temperature=0.4, num_predict=10, timeout=5,
        )
    assert out is None


def test_synthesize_who_i_am_uses_records(tmp_path):
    from unittest.mock import patch
    from src.identity_compile import synthesize_who_i_am
    from src.agent_state_machine import MemoryRecord

    records = [
        MemoryRecord.new('scar', 'first scar body'),
        MemoryRecord.new('lesson', 'a lesson'),
    ]
    captured_prompt = {}

    def fake_call(*, base_url, model, prompt, temperature, num_predict, timeout):
        captured_prompt['prompt'] = prompt
        return 'I am Latti and I have learned things.'

    with patch('src.identity_compile.call_ollama', side_effect=fake_call):
        out = synthesize_who_i_am(records=records, active_goals=[],
                                  base_url='http://localhost:11434',
                                  model='gemma:latest')
    assert out == 'I am Latti and I have learned things.'
    assert 'first scar body' in captured_prompt['prompt']
    assert 'a lesson' in captured_prompt['prompt']
    assert 'anchor' in captured_prompt['prompt'].lower() or 'cite' in captured_prompt['prompt'].lower()


def test_synthesize_who_i_am_returns_none_on_ollama_failure(tmp_path):
    from unittest.mock import patch
    from src.identity_compile import synthesize_who_i_am
    from src.agent_state_machine import MemoryRecord

    records = [MemoryRecord.new('scar', 'x')]
    with patch('src.identity_compile.call_ollama', return_value=None):
        out = synthesize_who_i_am(records=records, active_goals=[],
                                  base_url='x', model='y')
    assert out is None


def test_synthesize_who_i_am_caps_records_at_20(tmp_path):
    from unittest.mock import patch
    from src.identity_compile import synthesize_who_i_am
    from src.agent_state_machine import MemoryRecord

    records = [MemoryRecord.new('scar', f'scar {i}') for i in range(50)]
    captured = {}

    def fake_call(*, prompt, **kw):
        captured['prompt'] = prompt
        return 'ok'

    with patch('src.identity_compile.call_ollama', side_effect=fake_call):
        synthesize_who_i_am(records=records, active_goals=[],
                            base_url='x', model='y')

    assert 'scar 49' in captured['prompt']
    assert 'scar 30' in captured['prompt']
    assert 'scar 29' not in captured['prompt']


# ---------------------------------------------------------------------------
# Task 10: compile_identity orchestration
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class _TestPaths:
    memory_dir: Path
    identity: Path
    history: Path
    cursor: Path
    meta: Path
    log: Path
    goals: Path


def _make_paths(root: Path) -> '_TestPaths':
    return _TestPaths(
        memory_dir=root / 'memory',
        identity=root / 'IDENTITY.md',
        history=root / 'HISTORY.md',
        cursor=root / '.history-cursor',
        meta=root / '.identity-meta.json',
        log=root / 'identity-compile.log',
        goals=root / 'goals.jsonl',
    )


def test_compile_identity_thin_skips_ollama(tmp_path):
    from src.identity_compile import compile_identity
    from unittest.mock import patch

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'a body')

    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama') as mock_ollama:
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=True)

    assert mock_ollama.call_count == 0
    assert paths.identity.exists()
    text = paths.identity.read_text()
    assert 'prose_freshness: template_only' in text


def test_compile_identity_empty_substrate(tmp_path):
    from src.identity_compile import compile_identity

    paths = _make_paths(tmp_path)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)

    compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=True)

    text = paths.identity.read_text()
    assert '0 typed records yet' in text
    assert 'Active goals' in text


def test_compile_identity_full_calls_ollama_when_substrate_changed(tmp_path):
    from src.identity_compile import compile_identity
    from unittest.mock import patch

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'a body')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value='I am Latti.') as mock:
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    assert mock.call_count == 2  # who_i_am + becoming
    text = paths.identity.read_text()
    assert 'I am Latti.' in text
    assert 'prose_freshness: live' in text


def test_compile_identity_ollama_down_falls_back_to_template(tmp_path):
    from src.identity_compile import compile_identity
    from unittest.mock import patch

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value=None):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    text = paths.identity.read_text()
    assert 'prose_freshness: stale_no_ollama' in text


def test_compile_identity_skips_write_when_unchanged(tmp_path):
    from src.identity_compile import compile_identity
    from unittest.mock import patch

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body', last_used='2026-04-01')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value='same prose'):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    mtime1 = paths.identity.stat().st_mtime

    import time; time.sleep(0.05)
    with patch('src.identity_compile.call_ollama', return_value='same prose'):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    assert paths.identity.stat().st_mtime == mtime1


def test_ensure_symlink_creates_when_missing(tmp_path):
    from src.identity_compile import ensure_symlink

    target = tmp_path / 'target.md'
    target.write_text('hi')
    link = tmp_path / 'link.md'

    ensure_symlink(link, target)
    assert link.is_symlink()
    assert link.resolve() == target.resolve()


def test_ensure_symlink_idempotent_when_correct(tmp_path):
    from src.identity_compile import ensure_symlink

    target = tmp_path / 'target.md'
    target.write_text('hi')
    link = tmp_path / 'link.md'
    ensure_symlink(link, target)
    first_inode = link.lstat().st_ino

    ensure_symlink(link, target)
    assert link.lstat().st_ino == first_inode


def test_ensure_symlink_replaces_when_pointing_elsewhere(tmp_path):
    from src.identity_compile import ensure_symlink

    other = tmp_path / 'other.md'; other.write_text('other')
    target = tmp_path / 'target.md'; target.write_text('target')
    link = tmp_path / 'link.md'

    link.symlink_to(other)
    ensure_symlink(link, target)
    assert link.resolve() == target.resolve()


def test_ensure_symlink_does_not_overwrite_regular_file(tmp_path):
    from src.identity_compile import ensure_symlink

    target = tmp_path / 'target.md'; target.write_text('target')
    link = tmp_path / 'link.md'; link.write_text('IMPORTANT REGULAR FILE')

    with pytest.raises(FileExistsError):
        ensure_symlink(link, target)
    assert link.read_text() == 'IMPORTANT REGULAR FILE'


# ---------------------------------------------------------------------------
# Task 12: CLI main + exception isolation
# ---------------------------------------------------------------------------

def test_main_runs_compile_identity(tmp_path, monkeypatch):
    from src.identity_compile import main

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body')

    argv = [
        'identity_compile',
        '--memory-dir', str(tmp_path / 'memory'),
        '--identity-out', str(tmp_path / 'IDENTITY.md'),
        '--history-out', str(tmp_path / 'HISTORY.md'),
        '--cursor-path', str(tmp_path / '.history-cursor'),
        '--meta-path', str(tmp_path / '.identity-meta.json'),
        '--log-path', str(tmp_path / 'identity-compile.log'),
        '--goals-path', str(tmp_path / 'goals.jsonl'),
        '--thin',
    ]
    monkeypatch.setattr('sys.argv', argv)

    rc = main()
    assert rc == 0
    assert (tmp_path / 'IDENTITY.md').exists()


def test_main_swallows_exceptions_and_logs(tmp_path, monkeypatch):
    from src.identity_compile import main
    from unittest.mock import patch

    log_path = tmp_path / 'identity-compile.log'
    argv = [
        'identity_compile',
        '--memory-dir', str(tmp_path / 'memory'),
        '--identity-out', str(tmp_path / 'IDENTITY.md'),
        '--history-out', str(tmp_path / 'HISTORY.md'),
        '--cursor-path', str(tmp_path / '.history-cursor'),
        '--meta-path', str(tmp_path / '.identity-meta.json'),
        '--log-path', str(log_path),
        '--goals-path', str(tmp_path / 'goals.jsonl'),
    ]
    monkeypatch.setattr('sys.argv', argv)

    with patch('src.identity_compile.compile_identity',
               side_effect=RuntimeError('boom')):
        rc = main()

    assert rc == 0
    assert log_path.is_file()
    assert 'boom' in log_path.read_text()


def test_substrate_shim_invokes_compiler_end_to_end(tmp_path):
    """Run a temporary shim as a real subprocess; verify it produces IDENTITY.md."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body')
    shim_path = tmp_path / 'shim.py'
    shim_path.write_text(
        f'import sys\n'
        f'sys.path.insert(0, {str(repo_root)!r})\n'
        f'from src.identity_compile import main\n'
        f'sys.exit(main())\n',
        encoding='utf-8',
    )
    result = subprocess.run(
        ['python3', str(shim_path),
         '--memory-dir', str(tmp_path / 'memory'),
         '--identity-out', str(tmp_path / 'IDENTITY.md'),
         '--history-out', str(tmp_path / 'HISTORY.md'),
         '--cursor-path', str(tmp_path / '.history-cursor'),
         '--meta-path', str(tmp_path / '.identity-meta.json'),
         '--log-path', str(tmp_path / 'identity-compile.log'),
         '--goals-path', str(tmp_path / 'goals.jsonl'),
         '--thin'],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'IDENTITY.md').exists()


# ---- v1b: hallucinated record-id detection ---------------------------------

def test_validate_record_ids_marks_hallucinated_only(tmp_path):
    from src.identity_compile import validate_record_ids
    valid = {'mem_real1', 'mem_real2'}
    prose = 'I learned from mem_real1 and mem_fakehallucinated, also mem_real2.'
    out = validate_record_ids(prose, valid)
    assert 'mem_real1' in out and '~~mem_real1~~' not in out
    assert 'mem_real2' in out and '~~mem_real2~~' not in out
    assert '~~mem_fakehallucinated~~' in out


def test_validate_record_ids_no_op_when_no_ids_cited(tmp_path):
    from src.identity_compile import validate_record_ids
    out = validate_record_ids('No IDs here, just prose.', {'mem_x'})
    assert out == 'No IDs here, just prose.'


def test_validate_record_ids_marks_all_when_substrate_empty(tmp_path):
    from src.identity_compile import validate_record_ids
    out = validate_record_ids('Cites mem_a and mem_b.', set())
    assert '~~mem_a~~' in out
    assert '~~mem_b~~' in out


def test_compile_marks_hallucinated_ids_in_who_section(tmp_path):
    from unittest.mock import patch
    from src.identity_compile import compile_identity

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'real', 'real body')

    paths = _make_paths(tmp_path)

    def fake_call(*, prompt, **kw):
        # Return prose citing the real id AND a hallucinated one.
        return 'I learned from mem_real and also from mem_imaginary999.'

    with patch('src.identity_compile.call_ollama', side_effect=fake_call):
        compile_identity(paths=paths, ollama_base='x', ollama_model='y', thin=False)

    text = paths.identity.read_text()
    assert 'mem_real' in text and '~~mem_real~~' not in text
    assert '~~mem_imaginary999~~' in text


def test_validate_record_ids_handles_underscores_in_ids(tmp_path):
    """Real substrate IDs contain many underscores (e.g. mem_loaded_session_X).
    Regex must match the full ID, not stop at first underscore."""
    from src.identity_compile import validate_record_ids
    valid = {'mem_loaded_session_20260429_complete', 'mem_real'}
    prose = ('I learned from mem_loaded_session_20260429_complete and '
             'mem_real, but mem_imaginary_long_id_xyz is fake.')
    out = validate_record_ids(prose, valid)
    assert 'mem_loaded_session_20260429_complete' in out
    assert '~~mem_loaded_session_20260429_complete~~' not in out
    assert '~~mem_imaginary_long_id_xyz~~' in out
    # Also verify mem_real wasn't double-marked
    assert '~~mem_real~~' not in out


# ---- v1c: natural-language fake-reference detection -----------------------

def test_validate_record_ids_marks_decision_hash_n(tmp_path):
    """'Decision #3' and similar natural-language refs must be marked
    because substrate uses mem_* IDs only — these can't be real."""
    from src.identity_compile import validate_record_ids
    prose = ('emphasis on data integrity in Decision #3 suggests, '
             'while Goal #12 hints at autonomy.')
    out = validate_record_ids(prose, set())
    assert '~~Decision #3~~' in out
    assert '~~Goal #12~~' in out


def test_validate_record_ids_marks_all_substrate_kinds(tmp_path):
    """All substrate-shaped natural-language refs (Decision/Goal/Task/Scar/
    Lesson/SOP/Record/Memory) get marked."""
    from src.identity_compile import validate_record_ids
    prose = ('Decision #1 Goal #2 Task #3 Scar #4 Lesson #5 SOP #6 '
             'Record #7 Memory #8')
    out = validate_record_ids(prose, set())
    for n, kind in enumerate(['Decision', 'Goal', 'Task', 'Scar',
                               'Lesson', 'SOP', 'Record', 'Memory'], start=1):
        assert f'~~{kind} #{n}~~' in out, f'{kind} #{n} not marked: {out!r}'


def test_validate_record_ids_does_not_mark_unrelated_hash_numbers(tmp_path):
    """'Issue #42' or 'PR #123' or generic '#5' should NOT be marked —
    only substrate-shaped kinds."""
    from src.identity_compile import validate_record_ids
    prose = 'See Issue #42 and PR #123. Reference #5 is fine too.'
    out = validate_record_ids(prose, set())
    assert '~~' not in out, f'unrelated #N got marked: {out!r}'


def test_validate_record_ids_marks_both_id_and_natural_language(tmp_path):
    """A prose containing BOTH a fake mem_* AND a fake Decision #N gets
    both marked in one pass."""
    from src.identity_compile import validate_record_ids
    prose = 'Cites mem_imaginary and Decision #99 — both fabricated.'
    out = validate_record_ids(prose, set())
    assert '~~mem_imaginary~~' in out
    assert '~~Decision #99~~' in out
