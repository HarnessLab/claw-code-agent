"""Integration smoke: run compiler against a fixture substrate that mimics
the real ~/.latti/memory/ shape (mixed typed + legacy files), assert
IDENTITY.md has all sections in expected order with no exceptions.

This test does NOT touch the real ~/.latti/. It uses tmp_path with a
realistic mix of file shapes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def _seed_realistic_substrate(memory: Path) -> None:
    memory.mkdir(parents=True, exist_ok=True)

    for i, body in enumerate([
        'tool dispatch swallowed CoderTimeoutError silently; 49s blocking call',
        'wall block never_delete_production_data fired on rm -rf /etc',
        'per-line scanner whitelist requires marker on the matched line',
    ]):
        (memory / f'scar_real{i}.md').write_text(
            f'---\n'
            f'name: scar_real{i}\n'
            f'description: smoke fixture {i}\n'
            f'type: scar\n'
            f'id: mem_real{i}\n'
            f'last_used: 2026-04-{20+i:02d}\n'
            f'---\n{body}\n', encoding='utf-8',
        )

    (memory / 'lesson_smoke.md').write_text(
        '---\nname: lesson_smoke\ndescription: x\ntype: lesson\n'
        'id: mem_lessonx\nlast_used: 2026-04-25\n---\n'
        'sort by frontmatter, not mtime\n', encoding='utf-8',
    )

    (memory / 'decision_smoke.md').write_text(
        '---\nname: decision_smoke\ndescription: x\ntype: decision\n'
        'id: mem_decisionx\nlast_used: 2026-04-26\n---\n'
        'chose typed-only filter over resilient parser\n', encoding='utf-8',
    )

    (memory / 'AUDIT_DUMP_20260427.md').write_text(
        '# audit dump\nbash output goes here\n', encoding='utf-8',
    )
    (memory / 'BOOT_LOG.txt').write_text('boot log noise', encoding='utf-8')
    (memory / 'MEMORY.md').write_text('# index\n', encoding='utf-8')


def test_real_substrate_compile_produces_well_formed_identity(tmp_path):
    from src.identity_compile import compile_identity, IdentityPaths

    memory = tmp_path / 'memory'
    _seed_realistic_substrate(memory)

    paths = IdentityPaths(
        memory_dir=memory,
        identity=tmp_path / 'IDENTITY.md',
        history=tmp_path / 'HISTORY.md',
        cursor=tmp_path / '.history-cursor',
        meta=tmp_path / '.identity-meta.json',
        log=tmp_path / 'identity-compile.log',
        goals=tmp_path / 'goals.jsonl',
    )

    fake_prose = 'I am Latti. I am learning to filter signal from debris.'
    with patch('src.identity_compile.call_ollama', return_value=fake_prose):
        compile_identity(paths=paths,
                         ollama_base='http://localhost:11434',
                         ollama_model='gemma:latest',
                         thin=False)

    text = paths.identity.read_text()

    assert text.index('## who I am') < text.index('## where I am')
    assert text.index('## where I am') < text.index('## what I\'m learning')
    assert text.index('## what I\'m learning') < text.index('## who I\'m becoming')

    assert text.startswith('---\n')
    assert 'compiled_at:' in text
    assert 'substrate_sha:' in text
    assert 'generation: 1' in text
    assert 'prose_freshness: live' in text

    assert fake_prose in text

    assert 'tool dispatch swallowed' in text
    assert 'sort by frontmatter' in text

    assert 'audit dump' not in text
    assert 'boot log' not in text

    assert '<!-- BECOMING-SECTION-START -->' in text
    assert '<!-- BECOMING-SECTION-END -->' in text

    history_text = paths.history.read_text()
    assert 'tool dispatch swallowed' in history_text
    assert 'mem_real0' in history_text

    line_count = text.count('\n')
    assert 20 <= line_count <= 400, f'IDENTITY.md is {line_count} lines'


def test_real_substrate_compile_idempotent(tmp_path):
    from src.identity_compile import compile_identity, IdentityPaths

    memory = tmp_path / 'memory'
    _seed_realistic_substrate(memory)
    paths = IdentityPaths(
        memory_dir=memory,
        identity=tmp_path / 'IDENTITY.md',
        history=tmp_path / 'HISTORY.md',
        cursor=tmp_path / '.history-cursor',
        meta=tmp_path / '.identity-meta.json',
        log=tmp_path / 'identity-compile.log',
        goals=tmp_path / 'goals.jsonl',
    )

    with patch('src.identity_compile.call_ollama', return_value='stable prose'):
        compile_identity(paths=paths, ollama_base='x', ollama_model='y', thin=False)
    mtime1 = paths.identity.stat().st_mtime
    history_size1 = paths.history.stat().st_size

    import time; time.sleep(0.05)

    with patch('src.identity_compile.call_ollama', return_value='stable prose'):
        compile_identity(paths=paths, ollama_base='x', ollama_model='y', thin=False)

    assert paths.identity.stat().st_mtime == mtime1, 'IDENTITY.md should not be rewritten'
    assert paths.history.stat().st_size == history_size1, 'HISTORY.md should not be appended to'
