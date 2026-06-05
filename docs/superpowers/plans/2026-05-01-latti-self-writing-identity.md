# Latti self-writing IDENTITY.md — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small compiler that reads Latti's typed memory substrate and produces two markdown files (`~/.latti/IDENTITY.md` overwritten each compile, `~/.latti/HISTORY.md` append-only). Compiler runs at end of every Latti session and once daily via cron.

**Architecture:** Compiler module lives at `src/identity_compile.py` (importable for tests). Thin shim at `~/.latti/scripts/identity_compile.py` calls into the module. Substrate read is *typed-only* — files must start with `---\n` AND parse via `LattiMemoryStore.load()`. LLM prose via local Ollama (`gemma:latest`) with template-only fallback when Ollama is down. SHA-gated writes prevent mtime churn. HISTORY append is cursor-gated.

**Tech Stack:** Python 3.10+, jinja2 (templating), urllib (Ollama HTTP — no new dependency), pytest, existing `LattiMemoryStore` from `src/state_machine_memory.py`.

**Reference spec:** `docs/superpowers/specs/2026-05-01-latti-self-writing-identity-design.md` (a0c5ccf).

---

## File structure

| File | Action | Purpose |
|---|---|---|
| `src/identity_compile.py` | CREATE | Compiler module; main entry `compile_identity(thin: bool)` and `main()` for CLI |
| `src/identity_templates.py` | CREATE | String templates (no jinja2 dependency — Python f-strings/format) for IDENTITY.md, history entries, Ollama prompts |
| `tests/test_identity_compile.py` | CREATE | All unit tests (~13) + integration smoke |
| `tests/conftest.py` | MODIFY (or create if missing) | Fixtures: typed-record builder, fake Ollama server, isolated `~/.latti` tmp |
| `~/.latti/scripts/identity_compile.py` | CREATE | Thin shim: `import sys; sys.path.insert(0, '~/V5/claw-code-agent'); from src.identity_compile import main; main()` |
| `~/.latti/scripts/cron.d/identity-daily.sh` | CREATE | Daily cron wrapper, calls shim with `--thin` |
| `src/agent_runtime.py` | MODIFY | Add ~5 lines at end of `run()` to spawn compiler subprocess |

**Decision:** No jinja2 — adds a dependency for what amounts to f-string substitution. Use Python's `str.format()` and `textwrap`. Templates are strings in `src/identity_templates.py`.

---

## Conventions

- All code Python 3.10+, type-hinted.
- Test framework: pytest (already used by repo).
- Fixtures use `tmp_path` for `~/.latti`-equivalent isolation; never touch the real `~/.latti/` from tests.
- One commit per task. Conventional commits: `feat(identity):`, `test(identity):`, `fix(identity):`.
- All functions take explicit paths as arguments — no hardcoded `~/.latti` inside functions. The CLI entry point resolves real paths and passes them in. Makes everything testable.

---

## Task 1: Module scaffold + typed-only substrate read

**Files:**
- Create: `src/identity_compile.py`
- Create: `tests/test_identity_compile.py`

- [ ] **Step 1: Create empty test file with first failing test**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/V5/claw-code-agent
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 3 errors (`ModuleNotFoundError: No module named 'src.identity_compile'`).

- [ ] **Step 3: Create the module with minimal implementation**

```python
# src/identity_compile.py
"""Compile Latti's typed substrate into IDENTITY.md (now-file) + HISTORY.md.

See docs/superpowers/specs/2026-05-01-latti-self-writing-identity-design.md.

Substrate read is *typed-only*: file must start with '---\\n' AND parse via
LattiMemoryStore.load(). Legacy markdown files in ~/.latti/memory/ are
invisible to identity by design (~98% are operational debris).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from src.agent_state_machine import MemoryRecord
from src.state_machine_memory import LattiMemoryStore


def load_typed_records(memory_dir: Path) -> Iterator[MemoryRecord]:
    """Yield typed MemoryRecords from memory_dir.

    A file is 'typed' if it starts with '---\\n' AND LattiMemoryStore.load()
    returns a non-None record. Anything else is silently skipped.
    """
    if not memory_dir.is_dir():
        return
    store = LattiMemoryStore(memory_dir)
    for path in sorted(memory_dir.glob('*.md')):
        if path.name == 'MEMORY.md':
            continue  # index file, not a record
        try:
            head = path.read_bytes()[:4]
        except OSError:
            continue
        if head != b'---\n':
            continue
        record = store.load(path)
        if record is not None:
            yield record
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): typed-only substrate reader

Compiler module scaffold with load_typed_records — reads ~/.latti/memory/
filtering to records that (a) start with '---\\n' AND (b) parse via
LattiMemoryStore.load. Legacy markdown invisible by design.

3/3 tests pass."
```

---

## Task 2: Frontmatter-sorted records + substrate SHA

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_identity_compile.py`:

```python
import os
import time


def test_records_sorted_by_frontmatter_not_mtime(tmp_path):
    """Sort key is frontmatter last_used, NOT filesystem mtime."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: existing 3 pass; new 4 fail with `ImportError: cannot import name 'load_typed_records_sorted'` / `'compute_substrate_sha'`.

- [ ] **Step 3: Add implementations**

Append to `src/identity_compile.py`:

```python
import hashlib
import datetime


def load_typed_records_sorted(memory_dir: Path) -> list[MemoryRecord]:
    """Load typed records sorted by frontmatter last_used (oldest first).

    last_used in MemoryRecord is a Unix timestamp (float). Frontmatter
    stores it as date-string; LattiMemoryStore.load reconstructs the float
    from the date (midnight UTC of that date), so sort order is by date.
    """
    return sorted(load_typed_records(memory_dir), key=lambda r: r.last_used)


def compute_substrate_sha(memory_dir: Path) -> str:
    """SHA256 of all typed-record file contents, sorted by filename.

    Legacy (non-typed) files are excluded by the typed-only walk.
    Frontmatter last_used is date-granular, so same-day re-saves of a
    record produce identical file bytes → stable sha.
    """
    if not memory_dir.is_dir():
        return hashlib.sha256(b'').hexdigest()
    h = hashlib.sha256()
    for record_path in _typed_record_paths(memory_dir):
        h.update(record_path.read_bytes())
    return h.hexdigest()


def _typed_record_paths(memory_dir: Path) -> list[Path]:
    """Filenames of typed records in deterministic order."""
    if not memory_dir.is_dir():
        return []
    paths = []
    for path in sorted(memory_dir.glob('*.md')):
        if path.name == 'MEMORY.md':
            continue
        try:
            if path.read_bytes()[:4] == b'---\n':
                paths.append(path)
        except OSError:
            continue
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): frontmatter-sorted records + substrate SHA

load_typed_records_sorted sorts by frontmatter last_used (not mtime —
mtime can lie under copy/touch). compute_substrate_sha is stable across
identical compiles, changes on new records, ignores legacy files.

7/7 tests pass."
```

---

## Task 3: WHERE section rendering (templated, no LLM)

**Files:**
- Create: `src/identity_templates.py`
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_identity_compile.py`:

```python
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

    # Build a small fixture
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
```

- [ ] **Step 2: Run tests, verify failure**

```bash
python3 -m pytest tests/test_identity_compile.py::test_where_section_with_no_records tests/test_identity_compile.py::test_where_section_with_goals_and_records -v
```

Expected: ImportError on `render_where_section`.

- [ ] **Step 3: Create templates module + add renderer**

```python
# src/identity_templates.py
"""String templates for IDENTITY.md sections and Ollama prompts.

No jinja2 — Python str.format() suffices for these substitution patterns.
Keep templates as module-level constants for clarity and easy override.
"""

WHERE_SECTION = """## where I am
- **Active goals** ({n_goals}):
{goal_lines}
- **Last typed record**: {last_record}
- **Recent focus** (last 24h): {recent_focus}
"""

LEARNING_SECTION = """## what I'm learning
- **Last 5 scars**:
{scar_lines}
- **Last 3 lessons**:
{lesson_lines}
"""

PLACEHOLDER_WHO = "*(0 typed records yet — identity grows as Latti acts inside the typed system)*"
PLACEHOLDER_BECOMING = "*(no direction recorded yet — daemon will synthesize once goals + decisions exist)*"
PLACEHOLDER_NO_GOALS = "  - (no active goals)"
PLACEHOLDER_NO_RECORDS = "(0 typed records yet)"
PLACEHOLDER_NO_SCARS = "  - (no scars recorded)"
PLACEHOLDER_NO_LESSONS = "  - (no lessons recorded)"
```

Append to `src/identity_compile.py`:

```python
from collections import Counter
from src.identity_templates import (
    WHERE_SECTION, LEARNING_SECTION,
    PLACEHOLDER_NO_GOALS, PLACEHOLDER_NO_RECORDS,
    PLACEHOLDER_NO_SCARS, PLACEHOLDER_NO_LESSONS,
)


def render_where_section(active_goals: list, records: list[MemoryRecord]) -> str:
    """Render the templated WHERE section.

    active_goals: any object with .title, .status, .success_criteria attrs.
    records: typed MemoryRecords sorted oldest first.
    """
    if active_goals:
        goal_lines = '\n'.join(
            f'  - {g.title} — {g.status} — '
            f'{g.success_criteria[0] if g.success_criteria else "no criteria"}'
            for g in active_goals
        )
    else:
        goal_lines = PLACEHOLDER_NO_GOALS

    if records:
        last = records[-1]
        body_preview = last.body.replace('\n', ' ')[:80]
        last_record = (
            f'{last.kind} at {datetime.date.fromtimestamp(last.last_used).isoformat()} '
            f'— {body_preview}'
        )
        cutoff = max(r.last_used for r in records) - 86400  # 24h
        recent = [r for r in records if r.last_used >= cutoff]
        if recent:
            counts = Counter(r.kind for r in recent)
            recent_focus = ', '.join(f'{k}×{v}' for k, v in counts.most_common(3))
        else:
            recent_focus = '(no records in last 24h)'
    else:
        last_record = PLACEHOLDER_NO_RECORDS
        recent_focus = PLACEHOLDER_NO_RECORDS

    return WHERE_SECTION.format(
        n_goals=len(active_goals),
        goal_lines=goal_lines,
        last_record=last_record,
        recent_focus=recent_focus,
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py src/identity_templates.py tests/test_identity_compile.py
git commit -m "feat(identity): WHERE section renderer

Templated where-section with active goals + last record + 24h focus
counter. Empty-substrate path emits explicit '0 typed records yet'
placeholders rather than blank sections.

9/9 tests pass."
```

---

## Task 4: LEARNING section rendering

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
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
    # Caller is responsible for slicing; renderer renders whatever it gets.
    # Test: 5 scar lines + 3 lesson lines.
    assert out.count('  - scar body') == 5
    assert out.count('  - lesson body') == 3
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `render_learning_section`.

- [ ] **Step 3: Implement**

Append to `src/identity_compile.py`:

```python
def render_learning_section(scars: list[MemoryRecord],
                            lessons: list[MemoryRecord]) -> str:
    """Render the templated LEARNING section.

    Caller passes already-sliced lists (last 5 scars, last 3 lessons).
    """
    def _line(r: MemoryRecord) -> str:
        first_line = r.body.splitlines()[0] if r.body.strip() else '(empty)'
        ts = datetime.date.fromtimestamp(r.last_used).isoformat()
        return f'  - {first_line} ({ts})'

    scar_lines = '\n'.join(_line(s) for s in scars) if scars else PLACEHOLDER_NO_SCARS
    lesson_lines = '\n'.join(_line(l) for l in lessons) if lessons else PLACEHOLDER_NO_LESSONS
    return LEARNING_SECTION.format(scar_lines=scar_lines, lesson_lines=lesson_lines)
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): LEARNING section renderer

Renders last-N scars and last-N lessons as bulleted lists. Caller slices;
renderer formats. Empty-list path emits explicit placeholders.

12/12 tests pass."
```

---

## Task 5: BECOMING section preservation

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
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
    """If file mtime > last_compiled_at, treat as user-edited and preserve."""
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
    # Compile claimed to happen 10 seconds before file mtime → file is newer
    out = preserve_becoming_if_user_edited(p, last_compiled_at=file_mtime - 10)
    assert out is not None
    assert 'user edit' in out


def test_becoming_section_not_preserved_when_compile_is_newer(tmp_path):
    """If last_compiled_at > file mtime, daemon is free to overwrite."""
    from src.identity_compile import preserve_becoming_if_user_edited

    p = tmp_path / 'IDENTITY.md'
    p.write_text('## who I\'m becoming\n<!-- BECOMING-SECTION-START -->\nx\n<!-- BECOMING-SECTION-END -->\n', encoding='utf-8')
    file_mtime = p.stat().st_mtime
    out = preserve_becoming_if_user_edited(p, last_compiled_at=file_mtime + 10)
    assert out is None  # daemon may regenerate
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on the two new functions.

- [ ] **Step 3: Implement**

Append to `src/identity_compile.py`:

```python
import re

_BECOMING_RE = re.compile(
    r'<!-- BECOMING-SECTION-START -->\n(?P<body>.*?)\n<!-- BECOMING-SECTION-END -->',
    re.DOTALL,
)


def extract_becoming_section(identity_path: Path) -> str | None:
    """Return the contents between BECOMING-SECTION markers, or None."""
    if not identity_path.is_file():
        return None
    try:
        text = identity_path.read_text(encoding='utf-8')
    except OSError:
        return None
    m = _BECOMING_RE.search(text)
    return m.group('body') if m else None


def preserve_becoming_if_user_edited(identity_path: Path,
                                     last_compiled_at: float | None) -> str | None:
    """Return the existing becoming-section if the file is newer than last compile.

    If last_compiled_at is None (no prior compile) → return None (no preservation
    needed; daemon will write fresh).
    Returns None if no preservation should happen — daemon is free to regenerate.
    """
    if last_compiled_at is None:
        return None
    if not identity_path.is_file():
        return None
    if identity_path.stat().st_mtime > last_compiled_at:
        return extract_becoming_section(identity_path)
    return None
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): BECOMING section user-edit preservation

extract_becoming_section pulls body between marker comments.
preserve_becoming_if_user_edited returns the prior body when file mtime
> last_compiled_at, signaling 'human/Latti edited this; do not overwrite.'

17/17 tests pass."
```

---

## Task 6: IDENTITY.md template assembly + atomic SHA-gated write

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `src/identity_templates.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
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
    assert '## who I am\nI am Latti.' in out
    assert '## where I am' in out
    assert '## what I\'m learning' in out
    assert '<!-- BECOMING-SECTION-START -->' in out
    assert 'I want to grow.' in out
    assert '<!-- BECOMING-SECTION-END -->' in out
    assert 'pointers' in out


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
    assert target.stat().st_mtime == mtime1  # unchanged


def test_atomic_write_writes_when_content_differs(tmp_path):
    from src.identity_compile import write_identity_md_if_changed

    target = tmp_path / 'IDENTITY.md'
    write_identity_md_if_changed(target, 'content v1\n', prior_sha=None)
    written = write_identity_md_if_changed(target, 'content v2\n', prior_sha='wrong-sha')
    assert written is True
    assert target.read_text() == 'content v2\n'
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `render_identity_md`, `write_identity_md_if_changed`.

- [ ] **Step 3: Add full IDENTITY.md template + implementations**

Append to `src/identity_templates.py`:

```python
IDENTITY_MD = """---
compiled_at: {compiled_at}
generation: {generation}
substrate_sha: {substrate_sha}
prose_freshness: {prose_freshness}
---

## who I am
{who_section}

{where_section}
{learning_section}
## who I'm becoming
<!-- BECOMING-SECTION-START -->
{becoming_section}
<!-- BECOMING-SECTION-END -->

---
*pointers: [HISTORY](HISTORY.md) · [memory](memory/) · [runtime](~/V5/claw-code-agent)*
"""
```

Append to `src/identity_compile.py`:

```python
from src.identity_templates import IDENTITY_MD


def render_identity_md(*, compiled_at: str, generation: int, substrate_sha: str,
                       prose_freshness: str, who_section: str, where_section: str,
                       learning_section: str, becoming_section: str) -> str:
    """Assemble the complete IDENTITY.md text from rendered sections."""
    return IDENTITY_MD.format(
        compiled_at=compiled_at,
        generation=generation,
        substrate_sha=substrate_sha,
        prose_freshness=prose_freshness,
        who_section=who_section.strip(),
        where_section=where_section.strip(),
        learning_section=learning_section.strip(),
        becoming_section=becoming_section.strip(),
    )


def write_identity_md_if_changed(target: Path, content: str,
                                 prior_sha: str | None) -> bool:
    """Atomically write content to target if its sha differs from prior_sha.

    Returns True if a write occurred, False if skipped (sha matched).
    """
    new_sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    if prior_sha is not None and new_sha == prior_sha:
        return False
    tmp = target.with_suffix(target.suffix + '.tmp')
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(target)
    return True
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py src/identity_templates.py tests/test_identity_compile.py
git commit -m "feat(identity): IDENTITY.md template + atomic sha-gated write

render_identity_md assembles frontmatter + 5 sections.
write_identity_md_if_changed skips when sha matches prior — prevents
mtime churn that would falsely trigger 'recently modified' tooling.

20/20 tests pass."
```

---

## Task 7: HISTORY.md append + cursor mechanism

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `src/identity_templates.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
import json


def test_render_history_entry_includes_kind_id_body(tmp_path):
    from src.identity_compile import render_history_entries
    from src.agent_state_machine import MemoryRecord

    rec = MemoryRecord.new('scar', 'a scar happened\nmore detail')
    rec_dict = rec.to_dict()
    # Use the actual record object
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
        load_cursor, save_cursor,
    )

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'first', 'first', last_used='2026-04-01')
    _write_typed_record(mem, 'scar', 'second', 'second', last_used='2026-04-02')

    history = tmp_path / 'HISTORY.md'
    cursor_path = tmp_path / '.history-cursor'

    # First run: both records new
    appended1 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended1 == 2
    assert 'first' in history.read_text()
    assert 'second' in history.read_text()

    # Second run: no new records
    appended2 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended2 == 0
    body_size = history.stat().st_size

    # Add a third record
    _write_typed_record(mem, 'lesson', 'third', 'third', last_used='2026-04-03')
    appended3 = append_new_records_to_history(
        history_path=history, cursor_path=cursor_path,
        records=load_typed_records_sorted(mem),
    )
    assert appended3 == 1
    assert history.stat().st_size > body_size
    assert 'third' in history.read_text()
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on the new symbols.

- [ ] **Step 3: Implement**

Append to `src/identity_templates.py`:

```python
HISTORY_HEADER = """# Latti — history
*append-only chronological record of typed substrate events*

"""

HISTORY_ENTRY = """---
## {date}

### {time} · {kind} (id: {record_id})
{body}

"""
```

Append to `src/identity_compile.py`:

```python
from src.identity_templates import HISTORY_HEADER, HISTORY_ENTRY


def render_history_entries(records: list[MemoryRecord]) -> str:
    """Render N records as concatenated HISTORY.md entries."""
    chunks = []
    for r in records:
        dt = datetime.datetime.fromtimestamp(r.last_used, tz=datetime.timezone.utc)
        chunks.append(HISTORY_ENTRY.format(
            date=dt.date().isoformat(),
            time=dt.strftime('%H:%M'),
            kind=r.kind,
            record_id=r.id,
            body=r.body.strip(),
        ))
    return ''.join(chunks)


def load_cursor(cursor_path: Path) -> dict:
    """Read the last-appended cursor; default to zero if missing."""
    if not cursor_path.is_file():
        return {'last_ts': 0.0, 'last_id': None}
    try:
        return json.loads(cursor_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'last_ts': 0.0, 'last_id': None}


def save_cursor(cursor_path: Path, cursor: dict) -> None:
    """Atomically save cursor to disk."""
    tmp = cursor_path.with_suffix(cursor_path.suffix + '.tmp')
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(cursor), encoding='utf-8')
    tmp.replace(cursor_path)


def append_new_records_to_history(*, history_path: Path, cursor_path: Path,
                                  records: list[MemoryRecord]) -> int:
    """Append records strictly newer than cursor.last_ts. Returns count appended."""
    cursor = load_cursor(cursor_path)
    new_records = [r for r in records if r.last_used > cursor['last_ts']]
    if not new_records:
        return 0
    history_path.parent.mkdir(parents=True, exist_ok=True)
    if not history_path.exists():
        history_path.write_text(HISTORY_HEADER, encoding='utf-8')
    chunk = render_history_entries(new_records)
    with history_path.open('a', encoding='utf-8') as f:
        f.write(chunk)
    save_cursor(cursor_path, {
        'last_ts': max(r.last_used for r in new_records),
        'last_id': new_records[-1].id,
    })
    return len(new_records)
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 24 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py src/identity_templates.py tests/test_identity_compile.py
git commit -m "feat(identity): HISTORY.md append + cursor mechanism

render_history_entries formats records as dated entries.
append_new_records_to_history is cursor-gated: only records strictly
newer than cursor.last_ts are appended. Cursor persists in JSON.
Re-running with no new records is a true no-op.

24/24 tests pass."
```

---

## Task 8: Ollama call helper + fallback

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
import urllib.error
from unittest.mock import patch


def test_ollama_call_returns_response_text(tmp_path):
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
    from src.identity_compile import call_ollama

    with patch('src.identity_compile._ollama_post', side_effect=socket.timeout()):
        out = call_ollama(
            base_url='http://localhost:11434', model='gemma:latest',
            prompt='test', temperature=0.4, num_predict=10, timeout=5,
        )
    assert out is None


def test_ollama_call_returns_none_on_malformed_json(tmp_path):
    from src.identity_compile import call_ollama

    with patch('src.identity_compile._ollama_post', return_value=b'not json'):
        out = call_ollama(
            base_url='http://localhost:11434', model='gemma:latest',
            prompt='test', temperature=0.4, num_predict=10, timeout=5,
        )
    assert out is None
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `call_ollama`.

- [ ] **Step 3: Implement**

Append to `src/identity_compile.py`:

```python
import socket
import urllib.request
import urllib.error


def _ollama_post(base_url: str, payload: bytes, timeout: float) -> bytes:
    """Raw POST to /api/generate. Separate function so tests can patch it."""
    req = urllib.request.Request(
        f'{base_url.rstrip("/")}/api/generate',
        data=payload, method='POST',
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def call_ollama(*, base_url: str, model: str, prompt: str, temperature: float,
                num_predict: int, timeout: float) -> str | None:
    """Call Ollama generate, return response text or None on any failure.

    Failure modes that return None:
    - URL error (connection refused, DNS failure)
    - socket.timeout
    - non-200 HTTP
    - malformed JSON
    - missing 'response' key in JSON
    """
    payload = json.dumps({
        'model': model,
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': temperature, 'num_predict': num_predict},
    }).encode('utf-8')

    try:
        raw = _ollama_post(base_url, payload, timeout)
    except (urllib.error.URLError, socket.timeout, OSError):
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    response = data.get('response')
    if not isinstance(response, str):
        return None
    return response.strip()
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 28 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): Ollama HTTP call with full failure-isolation

call_ollama returns None on URL error, timeout, non-200, malformed JSON,
or missing 'response' key. Caller decides what to do with None — never
raises. _ollama_post separated so tests patch the network boundary, not
the parsing/error logic.

28/28 tests pass."
```

---

## Task 9: Prose section integration (who I am + becoming)

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `src/identity_templates.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
def test_synthesize_who_i_am_uses_records(tmp_path):
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
    # The 'cite by id' instruction must be present
    assert 'anchor' in captured_prompt['prompt'].lower() or 'cite' in captured_prompt['prompt'].lower()


def test_synthesize_who_i_am_returns_none_on_ollama_failure(tmp_path):
    from src.identity_compile import synthesize_who_i_am
    from src.agent_state_machine import MemoryRecord

    records = [MemoryRecord.new('scar', 'x')]
    with patch('src.identity_compile.call_ollama', return_value=None):
        out = synthesize_who_i_am(records=records, active_goals=[],
                                  base_url='x', model='y')
    assert out is None


def test_synthesize_who_i_am_caps_records_at_20(tmp_path):
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

    # Only last 20 records should appear in prompt
    assert 'scar 49' in captured['prompt']
    assert 'scar 30' in captured['prompt']
    assert 'scar 29' not in captured['prompt']
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `synthesize_who_i_am`.

- [ ] **Step 3: Implement**

Append to `src/identity_templates.py`:

```python
WHO_I_AM_PROMPT = """You are Latti, a coding agent. Below is your typed substrate. Write 3 short first-person paragraphs (~150 words total) titled 'who I am' — answering: what kind of agent am I right now, what am I learning, what direction am I pulling toward. Anchor every claim to a specific record below by citing its id (e.g. mem_xyz). No flowery language, no preamble.

SUBSTRATE:
{substrate_block}

GOALS:
{goals_block}
"""

WHO_I_AM_BECOMING_PROMPT = """You are Latti, a coding agent. Below are your active goals and recent decisions. Write a single first-person paragraph (~150 words) titled 'who I am becoming' — answering: what direction do these goals + decisions pull me toward. Anchor every claim to a specific goal or decision id. No flowery language, no preamble.

GOALS:
{goals_block}

RECENT DECISIONS:
{decisions_block}
"""
```

Append to `src/identity_compile.py`:

```python
from src.identity_templates import WHO_I_AM_PROMPT, WHO_I_AM_BECOMING_PROMPT

OLLAMA_TIMEOUT = 90.0


def _format_substrate_block(records: list[MemoryRecord]) -> str:
    if not records:
        return '(no typed records yet)'
    lines = []
    for r in records:
        body_one_line = ' '.join(r.body.split())[:200]
        lines.append(f'[{r.kind} {r.id}] {body_one_line}')
    return '\n'.join(lines)


def _format_goals_block(active_goals: list) -> str:
    if not active_goals:
        return '(no active goals)'
    return '\n'.join(
        f'- {g.title} ({g.status})'
        + (f' — {", ".join(g.success_criteria)}' if g.success_criteria else '')
        for g in active_goals
    )


def synthesize_who_i_am(*, records: list[MemoryRecord], active_goals: list,
                        base_url: str, model: str) -> str | None:
    """Call Ollama to synthesize the WHO I AM prose section.

    Caps record context at the last 20.
    """
    capped = records[-20:]
    prompt = WHO_I_AM_PROMPT.format(
        substrate_block=_format_substrate_block(capped),
        goals_block=_format_goals_block(active_goals),
    )
    return call_ollama(
        base_url=base_url, model=model, prompt=prompt,
        temperature=0.4, num_predict=250, timeout=OLLAMA_TIMEOUT,
    )


def synthesize_becoming(*, active_goals: list, decisions: list[MemoryRecord],
                        base_url: str, model: str) -> str | None:
    """Call Ollama to synthesize the BECOMING prose section."""
    prompt = WHO_I_AM_BECOMING_PROMPT.format(
        goals_block=_format_goals_block(active_goals),
        decisions_block=_format_substrate_block(decisions[-5:]),
    )
    return call_ollama(
        base_url=base_url, model=model, prompt=prompt,
        temperature=0.4, num_predict=200, timeout=OLLAMA_TIMEOUT,
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 31 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py src/identity_templates.py tests/test_identity_compile.py
git commit -m "feat(identity): Ollama prose synthesis for who-i-am + becoming

synthesize_who_i_am caps context at last 20 records and instructs the
model to anchor claims to record ids. synthesize_becoming uses goals +
last 5 decisions. Both return None on Ollama failure (caller falls back
to prior prose with stale freshness mark).

31/31 tests pass."
```

---

## Task 10: Top-level compile_identity orchestration

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
def test_compile_identity_thin_skips_ollama(tmp_path):
    from src.identity_compile import compile_identity

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

    mem = tmp_path / 'memory'
    _write_typed_record(mem, 'scar', 'a', 'a body')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value='I am Latti.') as mock:
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    # Two calls: who_i_am + becoming (no prior prose to preserve)
    assert mock.call_count == 2
    text = paths.identity.read_text()
    assert 'I am Latti.' in text
    assert 'prose_freshness: live' in text


def test_compile_identity_ollama_down_falls_back_to_template(tmp_path):
    from src.identity_compile import compile_identity

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value=None):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    text = paths.identity.read_text()
    assert 'prose_freshness: stale_no_ollama' in text
    # Placeholders fill in for missing prose
    assert '0 typed records yet' in text or 'identity grows' in text


def test_compile_identity_skips_write_when_unchanged(tmp_path):
    from src.identity_compile import compile_identity

    _write_typed_record(tmp_path / 'memory', 'scar', 'a', 'body', last_used='2026-04-01')
    paths = _make_paths(tmp_path)

    with patch('src.identity_compile.call_ollama', return_value='same prose'):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    mtime1 = paths.identity.stat().st_mtime

    import time; time.sleep(0.05)
    with patch('src.identity_compile.call_ollama', return_value='same prose'):
        compile_identity(paths=paths, ollama_base='http://x', ollama_model='m', thin=False)

    # Identity file should be unchanged (sha-gated)
    assert paths.identity.stat().st_mtime == mtime1
```

Add helper at top of test file (after the existing `_write_*` helpers):

```python
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
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError or AttributeError on `compile_identity`.

- [ ] **Step 3: Implement orchestration**

Append to `src/identity_compile.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityPaths:
    """Resolved paths for one compile invocation. CLI builds this from ~/.latti/."""
    memory_dir: Path
    identity: Path
    history: Path
    cursor: Path
    meta: Path
    log: Path
    goals: Path  # for future use; goals loader pluggable for now


def _load_meta(meta_path: Path) -> dict:
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(meta_path: Path, meta: dict) -> None:
    tmp = meta_path.with_suffix(meta_path.suffix + '.tmp')
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    tmp.replace(meta_path)


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_active_goals(goals_path: Path) -> list:
    """Read goals.jsonl, return ones with status='active'.

    NOTE: spec §10 flagged that goals_path is runtime-config-dependent.
    For v1, return [] if path doesn't exist; later wire to actual goals
    persistence path.
    """
    if not goals_path.is_file():
        return []
    goals: dict[str, dict] = {}
    try:
        for line in goals_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if 'id' in d:
                goals[d['id']] = d  # last-write-wins per id
    except OSError:
        return []

    class _GoalView:
        def __init__(self, d):
            self.title = d.get('title', '(unnamed)')
            self.status = d.get('status', 'unknown')
            self.success_criteria = tuple(d.get('success_criteria', ()))

    return [_GoalView(d) for d in goals.values() if d.get('status') == 'active']


def compile_identity(*, paths: IdentityPaths, ollama_base: str, ollama_model: str,
                     thin: bool = False) -> None:
    """Top-level compile. Idempotent. Failure-isolated by caller (main())."""
    records = load_typed_records_sorted(paths.memory_dir)
    substrate_sha = compute_substrate_sha(paths.memory_dir)
    prior_meta = _load_meta(paths.meta)
    substrate_changed = substrate_sha != prior_meta.get('substrate_sha')

    # Templated sections
    active_goals = _load_active_goals(paths.goals)
    where = render_where_section(active_goals=active_goals, records=records)
    learning = render_learning_section(
        scars=[r for r in records if r.kind == 'scar'][-5:],
        lessons=[r for r in records if r.kind == 'lesson'][-3:],
    )

    # Prose sections
    prior_compile_at = prior_meta.get('compiled_at_epoch')
    becoming = preserve_becoming_if_user_edited(paths.identity, prior_compile_at)
    prior_who = extract_section(paths.identity, 'who I am') if paths.identity.is_file() else None

    if thin:
        who = prior_who or PLACEHOLDER_WHO
        if becoming is None:
            becoming = extract_becoming_section(paths.identity) or PLACEHOLDER_BECOMING
        freshness = 'template_only'
    else:
        who_new = None
        becoming_new = None
        if substrate_changed:
            who_new = synthesize_who_i_am(
                records=records, active_goals=active_goals,
                base_url=ollama_base, model=ollama_model,
            )
            if becoming is None:
                becoming_new = synthesize_becoming(
                    active_goals=active_goals,
                    decisions=[r for r in records if r.kind == 'decision'],
                    base_url=ollama_base, model=ollama_model,
                )

        if who_new is None and becoming_new is None and substrate_changed:
            freshness = 'stale_no_ollama'
        elif not substrate_changed:
            freshness = 'live'  # nothing to refresh; prior prose still valid
        else:
            freshness = 'live'

        who = who_new or prior_who or PLACEHOLDER_WHO
        if becoming is None:
            becoming = becoming_new or extract_becoming_section(paths.identity) or PLACEHOLDER_BECOMING

    # Assemble + sha-gated write
    new_identity = render_identity_md(
        compiled_at=_now_iso(),
        generation=prior_meta.get('generation', 0) + 1,
        substrate_sha=substrate_sha,
        prose_freshness=freshness,
        who_section=who,
        where_section=where,
        learning_section=learning,
        becoming_section=becoming,
    )
    write_identity_md_if_changed(paths.identity, new_identity, prior_meta.get('identity_sha'))

    # History append
    append_new_records_to_history(
        history_path=paths.history, cursor_path=paths.cursor, records=records,
    )

    # Save meta
    _save_meta(paths.meta, {
        'substrate_sha': substrate_sha,
        'identity_sha': hashlib.sha256(new_identity.encode('utf-8')).hexdigest(),
        'generation': prior_meta.get('generation', 0) + 1,
        'compiled_at': _now_iso(),
        'compiled_at_epoch': time.time(),
    })


def extract_section(identity_path: Path, header_name: str) -> str | None:
    """Extract the body of an `## <header_name>` section from IDENTITY.md.

    Returns the text between this section's header and the next `## ` header,
    or None if not found.
    """
    if not identity_path.is_file():
        return None
    try:
        text = identity_path.read_text(encoding='utf-8')
    except OSError:
        return None
    pattern = re.compile(
        rf'^## {re.escape(header_name)}\n(?P<body>.*?)(?=^## |\Z)',
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(text)
    return m.group('body').strip() if m else None
```

Add `import time` at top of `src/identity_compile.py` if not already imported.

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 36 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): top-level compile_identity orchestration

Wires substrate read, sha computation, prior-meta load, templated section
render, Ollama prose synthesis with fallback, sha-gated identity write,
history append, and meta save. --thin flag skips Ollama and marks
freshness=template_only.

36/36 tests pass."
```

---

## Task 11: Symlink exports

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
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

    ensure_symlink(link, target)  # second call no-op
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
    """If the link path exists as a regular file (not a symlink), don't clobber."""
    from src.identity_compile import ensure_symlink

    target = tmp_path / 'target.md'; target.write_text('target')
    link = tmp_path / 'link.md'; link.write_text('IMPORTANT REGULAR FILE')

    with pytest.raises(FileExistsError):
        ensure_symlink(link, target)
    assert link.read_text() == 'IMPORTANT REGULAR FILE'
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `ensure_symlink`.

- [ ] **Step 3: Implement**

Append to `src/identity_compile.py`:

```python
import os


def ensure_symlink(link_path: Path, target_path: Path) -> None:
    """Ensure link_path is a symlink to target_path.

    - If link_path doesn't exist: create symlink.
    - If link_path is a symlink already pointing at target: no-op.
    - If link_path is a symlink pointing elsewhere: replace.
    - If link_path is a regular file or directory: raise FileExistsError.
    """
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink():
        if link_path.resolve() == target_path.resolve():
            return
        link_path.unlink()
        os.symlink(target_path, link_path)
        return

    if link_path.exists():
        raise FileExistsError(
            f'{link_path} exists as a non-symlink; refusing to clobber'
        )

    os.symlink(target_path, link_path)
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 40 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): idempotent symlink exports

ensure_symlink creates / no-ops / replaces a symlink, but refuses to
overwrite a regular file (defensive — prevents data loss if the export
path was used by something else).

40/40 tests pass."
```

---

## Task 12: CLI main + exception isolation

**Files:**
- Modify: `src/identity_compile.py`
- Modify: `tests/test_identity_compile.py`

- [ ] **Step 1: Add failing tests**

```python
def test_main_runs_compile_identity(tmp_path, monkeypatch):
    """main() with --memory-dir / --identity-out etc. flags runs compile."""
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
    """If compile_identity raises, main writes traceback to log_path and exits 0."""
    from src.identity_compile import main

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

    assert rc == 0  # never propagate
    assert log_path.is_file()
    assert 'boom' in log_path.read_text()
```

- [ ] **Step 2: Run, verify fail**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: ImportError on `main`.

- [ ] **Step 3: Implement**

Append to `src/identity_compile.py`:

```python
import argparse
import sys
import traceback


DEFAULT_OLLAMA_BASE = 'http://localhost:11434'
DEFAULT_OLLAMA_MODEL = 'gemma:latest'


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Compile Latti IDENTITY.md + HISTORY.md')
    p.add_argument('--memory-dir', required=True, type=Path)
    p.add_argument('--identity-out', required=True, type=Path)
    p.add_argument('--history-out', required=True, type=Path)
    p.add_argument('--cursor-path', required=True, type=Path)
    p.add_argument('--meta-path', required=True, type=Path)
    p.add_argument('--log-path', required=True, type=Path)
    p.add_argument('--goals-path', required=True, type=Path)
    p.add_argument('--ollama-base', default=DEFAULT_OLLAMA_BASE)
    p.add_argument('--ollama-model', default=DEFAULT_OLLAMA_MODEL)
    p.add_argument('--thin', action='store_true',
                   help='Skip Ollama; templated sections only')
    return p


def main() -> int:
    """CLI entry. Always returns 0; failures are logged to --log-path."""
    args = _build_arg_parser().parse_args()
    paths = IdentityPaths(
        memory_dir=args.memory_dir,
        identity=args.identity_out,
        history=args.history_out,
        cursor=args.cursor_path,
        meta=args.meta_path,
        log=args.log_path,
        goals=args.goals_path,
    )
    try:
        compile_identity(
            paths=paths,
            ollama_base=args.ollama_base,
            ollama_model=args.ollama_model,
            thin=args.thin,
        )
    except Exception:
        try:
            args.log_path.parent.mkdir(parents=True, exist_ok=True)
            with args.log_path.open('a', encoding='utf-8') as f:
                f.write(f'--- {_now_iso()} ---\n')
                f.write(traceback.format_exc())
                f.write('\n')
        except Exception:
            pass  # logging failure must not propagate either
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run, verify pass**

```bash
python3 -m pytest tests/test_identity_compile.py -v
```

Expected: 42 passed.

- [ ] **Step 5: Commit**

```bash
git add src/identity_compile.py tests/test_identity_compile.py
git commit -m "feat(identity): CLI main with full exception isolation

main() builds IdentityPaths from argparse, calls compile_identity, and
swallows any exception into --log-path. Always returns 0. The runtime
hook (Task 14) will subprocess-spawn this; runtime must NEVER see a
non-zero exit from the compiler.

42/42 tests pass."
```

---

## Task 13: Substrate shim + cron entry

**Files:**
- Create: `~/.latti/scripts/identity_compile.py`
- Create: `~/.latti/scripts/cron.d/identity-daily.sh`
- Modify: `tests/test_identity_compile.py` (smoke test on shim)

- [ ] **Step 1: Add a smoke test that runs the shim as a subprocess**

```python
def test_substrate_shim_invokes_compiler_end_to_end(tmp_path, monkeypatch):
    """Run the substrate shim as a real subprocess; verify it produces IDENTITY.md.

    This test writes a temporary shim that points at the test's tmp paths,
    then runs it. The real shim at ~/.latti/scripts/identity_compile.py is
    tested separately in Task 15 integration.
    """
    import subprocess
    import shutil

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
```

- [ ] **Step 2: Run, verify fail (the shim doesn't exist yet, but the test creates its own — should pass already)**

Actually this test creates its own shim and runs it. Should pass once Task 12 is committed.

```bash
python3 -m pytest tests/test_identity_compile.py::test_substrate_shim_invokes_compiler_end_to_end -v
```

Expected: 1 passed.

- [ ] **Step 3: Create the real substrate shim**

```bash
cat > ~/.latti/scripts/identity_compile.py <<'EOF'
#!/usr/bin/env python3
"""Substrate shim for identity_compile.

Source of truth lives in ~/V5/claw-code-agent/src/identity_compile.py.
This shim adds the repo to sys.path and dispatches to main().
"""
import sys
from pathlib import Path

REPO = Path.home() / 'V5' / 'claw-code-agent'
sys.path.insert(0, str(REPO))

from src.identity_compile import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
EOF
chmod +x ~/.latti/scripts/identity_compile.py
```

- [ ] **Step 4: Create the daily cron wrapper**

```bash
mkdir -p ~/.latti/scripts/cron.d
cat > ~/.latti/scripts/cron.d/identity-daily.sh <<'EOF'
#!/bin/bash
# Daily templated refresh of Latti IDENTITY.md.
# Skips Ollama (--thin); fast and cheap. Runs once a day at 06:00 UTC.
set -uo pipefail

HOME_DIR="${HOME:-/Users/manolitonora}"
LATTI="$HOME_DIR/.latti"

python3 "$LATTI/scripts/identity_compile.py" \
  --memory-dir   "$LATTI/memory" \
  --identity-out "$LATTI/IDENTITY.md" \
  --history-out  "$LATTI/HISTORY.md" \
  --cursor-path  "$LATTI/.history-cursor" \
  --meta-path    "$LATTI/.identity-meta.json" \
  --log-path     "$LATTI/identity-compile.log" \
  --goals-path   "$LATTI/goals.jsonl" \
  --thin

# Exit 0 always; the compiler does its own error logging.
exit 0
EOF
chmod +x ~/.latti/scripts/cron.d/identity-daily.sh
```

- [ ] **Step 5: Verify shim runs against real substrate**

```bash
python3 ~/.latti/scripts/identity_compile.py \
  --memory-dir   ~/.latti/memory \
  --identity-out /tmp/identity-smoke.md \
  --history-out  /tmp/history-smoke.md \
  --cursor-path  /tmp/cursor-smoke \
  --meta-path    /tmp/meta-smoke.json \
  --log-path     /tmp/identity-compile-smoke.log \
  --goals-path   ~/.latti/goals.jsonl \
  --thin

echo "exit=$?"
ls -la /tmp/identity-smoke.md
head -30 /tmp/identity-smoke.md
```

Expected: exit 0, IDENTITY.md file exists, contains all 5 sections, `prose_freshness: template_only`.

- [ ] **Step 6: Commit**

```bash
cd ~/V5/claw-code-agent
git add tests/test_identity_compile.py
git commit -m "test(identity): substrate shim subprocess smoke

Constructs a temporary shim, runs it via subprocess, verifies it produces
IDENTITY.md end-to-end. The real substrate shim at ~/.latti/scripts/
identity_compile.py is created out-of-tree (cannot be tracked by this
repo) but has identical structure.

43/43 tests pass."
```

---

## Task 14: Runtime hook in agent_runtime.py

**Files:**
- Modify: `src/agent_runtime.py`
- Modify: `tests/test_identity_compile.py` (or new test file)

- [ ] **Step 1: Locate the end of `run()` in agent_runtime.py**

```bash
grep -n "def run(" src/agent_runtime.py
# Expect: line 349
```

Find where the `run()` method returns its final `AgentRunResult`. The hook fires there, after the last `_persist_session` call but before the return.

- [ ] **Step 2: Write a test for the hook (new test file to keep concerns separate)**

Create `tests/test_runtime_identity_hook.py`:

```python
"""Test that agent_runtime.run() spawns the identity compiler at end-of-session.

The compiler is invoked via subprocess.Popen (non-blocking, fire-and-forget).
Hook failure must NOT affect the run() return value.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def test_run_spawns_identity_compiler_subprocess(monkeypatch):
    """End of run() should call subprocess.Popen on the identity_compile shim."""
    # Shape this test against the actual run() integration. Set the env flag
    # the hook gates on so the hook fires only when explicitly enabled.
    monkeypatch.setenv('LATTI_IDENTITY_COMPILE', '1')

    spawn_calls = []

    def fake_popen(args, **kw):
        spawn_calls.append(args)
        m = MagicMock()
        m.pid = 99999
        return m

    with patch('src.agent_runtime.subprocess.Popen', side_effect=fake_popen):
        # Trigger the hook directly. (Wrapping a full run() call would require
        # heavy fixtures — calling the hook function directly is the smallest
        # test that proves wiring.)
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()

    assert len(spawn_calls) == 1
    cmd = spawn_calls[0]
    assert any('identity_compile.py' in arg for arg in cmd)


def test_hook_no_op_when_env_var_absent(monkeypatch):
    monkeypatch.delenv('LATTI_IDENTITY_COMPILE', raising=False)

    spawn_calls = []
    def fake_popen(args, **kw):
        spawn_calls.append(args)
        return MagicMock()

    with patch('src.agent_runtime.subprocess.Popen', side_effect=fake_popen):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()

    assert len(spawn_calls) == 0  # gated off


def test_hook_swallows_subprocess_error(monkeypatch):
    """If Popen itself raises (shim missing), hook must not propagate."""
    monkeypatch.setenv('LATTI_IDENTITY_COMPILE', '1')

    def boom(*a, **kw):
        raise FileNotFoundError('shim not found')

    with patch('src.agent_runtime.subprocess.Popen', side_effect=boom):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        # Should not raise
        _maybe_spawn_identity_compiler()
```

- [ ] **Step 3: Run, verify fail**

```bash
python3 -m pytest tests/test_runtime_identity_hook.py -v
```

Expected: 3 errors (`ImportError: cannot import name '_maybe_spawn_identity_compiler'`).

- [ ] **Step 4: Add the hook function to agent_runtime.py**

First check whether `subprocess`, `os`, `sys`, `Path` are already imported at the top of `src/agent_runtime.py`:

```bash
head -50 src/agent_runtime.py | grep -E "^(import|from)" | head -20
```

If `subprocess`, `os`, `sys` are already imported, skip those imports below. If `pathlib.Path` is already imported, skip that one too. Otherwise add what's missing to the existing import block (do NOT add a second `import subprocess` line — Python re-imports are no-ops but they confuse readers).

Then add this hook function near the end of the imports / top-level helpers (before any class definitions):

```python
_LATTI_DIR = Path.home() / '.latti'
_IDENTITY_SHIM = _LATTI_DIR / 'scripts' / 'identity_compile.py'


def _maybe_spawn_identity_compiler() -> None:
    """Fire-and-forget spawn of the identity compiler at session end.

    Gated on LATTI_IDENTITY_COMPILE=1 so existing test fixtures that build
    runtime instances don't accidentally trigger compiles. Any failure
    (missing shim, Popen error) is silently swallowed — must NOT affect
    the run() return value.
    """
    if os.environ.get('LATTI_IDENTITY_COMPILE') != '1':
        return
    if not _IDENTITY_SHIM.is_file():
        return
    try:
        subprocess.Popen(
            [
                sys.executable, str(_IDENTITY_SHIM),
                '--memory-dir',   str(_LATTI_DIR / 'memory'),
                '--identity-out', str(_LATTI_DIR / 'IDENTITY.md'),
                '--history-out',  str(_LATTI_DIR / 'HISTORY.md'),
                '--cursor-path',  str(_LATTI_DIR / '.history-cursor'),
                '--meta-path',    str(_LATTI_DIR / '.identity-meta.json'),
                '--log-path',     str(_LATTI_DIR / 'identity-compile.log'),
                '--goals-path',   str(_LATTI_DIR / 'goals.jsonl'),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return  # never propagate
```

- [ ] **Step 5: Wire the hook into `run()`**

`run()` may have multiple return paths (early returns, error returns). Wire the hook only at the **canonical successful return** — the final return after the main loop completes. Skip error/early returns; the spec does not require identity compiles on error paths, and adding them on every exit point increases surface area for v1.

```bash
grep -n "def run(self" src/agent_runtime.py
# Confirm: line 349 (or whatever the current line is)
```

Read the body of `run()` and find the final `return result` (or whatever the canonical return statement is at the bottom of the method, after all `_persist_session` calls). Insert one line before it:

```python
        _maybe_spawn_identity_compiler()
        return result  # ← existing line; do not modify
```

Do NOT replicate the call at every early-return site — that's intentional v1 scope. If you find the canonical return is unclear (e.g., the method has many similar exit points), pause and check with the spec author rather than guessing.

- [ ] **Step 6: Run hook tests**

```bash
python3 -m pytest tests/test_runtime_identity_hook.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Run the full test suite to confirm no regression**

```bash
python3 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all prior tests still pass; 3 new hook tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/agent_runtime.py tests/test_runtime_identity_hook.py
git commit -m "feat(identity): runtime hook spawns compiler at session end

_maybe_spawn_identity_compiler is fire-and-forget Popen of the substrate
shim. Gated on LATTI_IDENTITY_COMPILE=1 env var so existing test fixtures
that construct runtimes don't accidentally trigger compiles. Failure
(missing shim, OSError) is silently swallowed; never propagates to run().

3/3 hook tests pass; full suite green."
```

---

## Task 15: Integration smoke against real substrate

**Files:**
- Modify: `tests/test_identity_compile.py` (or create `tests/test_identity_smoke.py`)

- [ ] **Step 1: Write the integration smoke test**

Create `tests/test_identity_smoke.py`:

```python
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

    # Three typed scars
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

    # One typed lesson
    (memory / 'lesson_smoke.md').write_text(
        '---\nname: lesson_smoke\ndescription: x\ntype: lesson\n'
        'id: mem_lessonx\nlast_used: 2026-04-25\n---\n'
        'sort by frontmatter, not mtime\n', encoding='utf-8',
    )

    # One typed decision
    (memory / 'decision_smoke.md').write_text(
        '---\nname: decision_smoke\ndescription: x\ntype: decision\n'
        'id: mem_decisionx\nlast_used: 2026-04-26\n---\n'
        'chose typed-only filter over resilient parser\n', encoding='utf-8',
    )

    # Legacy junk that must be invisible
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

    # Mock Ollama: return a stable string so we can assert presence.
    fake_prose = 'I am Latti. I am learning to filter signal from debris.'
    with patch('src.identity_compile.call_ollama', return_value=fake_prose):
        compile_identity(paths=paths,
                         ollama_base='http://localhost:11434',
                         ollama_model='gemma:latest',
                         thin=False)

    text = paths.identity.read_text()

    # All five top-level sections present in order
    assert text.index('## who I am') < text.index('## where I am')
    assert text.index('## where I am') < text.index('## what I\'m learning')
    assert text.index('## what I\'m learning') < text.index('## who I\'m becoming')

    # Frontmatter present
    assert text.startswith('---\n')
    assert 'compiled_at:' in text
    assert 'substrate_sha:' in text
    assert 'generation: 1' in text
    assert 'prose_freshness: live' in text

    # Mocked prose appears in who-i-am
    assert fake_prose in text

    # Real substrate content surfaced
    assert 'tool dispatch swallowed' in text
    assert 'sort by frontmatter' in text  # the lesson

    # Legacy files invisible
    assert 'audit dump' not in text
    assert 'boot log' not in text

    # Becoming section markers present
    assert '<!-- BECOMING-SECTION-START -->' in text
    assert '<!-- BECOMING-SECTION-END -->' in text

    # History was created and contains the typed records
    history_text = paths.history.read_text()
    assert 'tool dispatch swallowed' in history_text
    assert 'mem_real0' in history_text

    # Reasonable size: ~200 lines target, but allow 100-400 range
    line_count = text.count('\n')
    assert 50 <= line_count <= 400, f'IDENTITY.md is {line_count} lines'


def test_real_substrate_compile_idempotent(tmp_path):
    """Running compile twice with no substrate change → second run is no-op."""
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
```

- [ ] **Step 2: Run the smoke test**

```bash
python3 -m pytest tests/test_identity_smoke.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Run the FULL suite to confirm no regression anywhere**

```bash
python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_identity_smoke.py
git commit -m "test(identity): integration smoke against realistic substrate

Seeds tmp_path with mixed typed + legacy files (3 scars, 1 lesson, 1
decision, 1 audit-dump junk, 1 boot-log junk, 1 MEMORY.md). Asserts:
- All 5 sections present in expected order
- Frontmatter populated (sha, generation, freshness)
- Mocked prose surfaces in who-i-am
- Real substrate content surfaces (typed)
- Legacy junk invisible
- BECOMING markers present
- HISTORY created with typed records
- 50-400 line size envelope
- Idempotency: two runs same substrate → no rewrites

2/2 smoke tests pass; full suite green."
```

---

## Task 16: First-real-substrate manual verification

This is a manual verification, not a test. Run AFTER all 15 tasks are committed.

- [ ] **Step 1: Run the substrate shim against the real substrate, --thin (no Ollama)**

```bash
python3 ~/.latti/scripts/identity_compile.py \
  --memory-dir   ~/.latti/memory \
  --identity-out ~/.latti/IDENTITY.md \
  --history-out  ~/.latti/HISTORY.md \
  --cursor-path  ~/.latti/.history-cursor \
  --meta-path    ~/.latti/.identity-meta.json \
  --log-path     ~/.latti/identity-compile.log \
  --goals-path   ~/.latti/goals.jsonl \
  --thin

echo "exit=$?"
```

Expected: exit 0, no errors in `~/.latti/identity-compile.log`.

- [ ] **Step 2: Inspect the produced IDENTITY.md**

```bash
cat ~/.latti/IDENTITY.md
```

Expected: all 5 sections, near-empty content (typed records are ~2% of `~/.latti/memory/` per spec §9 acceptance), `prose_freshness: template_only`.

- [ ] **Step 3: Run again WITHOUT --thin (full LLM)**

Make sure Ollama is up:
```bash
curl -s -m 3 http://localhost:11434/api/tags | head -c 100
```

Then:
```bash
python3 ~/.latti/scripts/identity_compile.py \
  --memory-dir   ~/.latti/memory \
  --identity-out ~/.latti/IDENTITY.md \
  --history-out  ~/.latti/HISTORY.md \
  --cursor-path  ~/.latti/.history-cursor \
  --meta-path    ~/.latti/.identity-meta.json \
  --log-path     ~/.latti/identity-compile.log \
  --goals-path   ~/.latti/goals.jsonl

echo "exit=$?"
cat ~/.latti/IDENTITY.md
```

Expected: exit 0, `prose_freshness: live`, "who I am" section contains real LLM-generated prose anchored to record IDs.

- [ ] **Step 4: Install the daily cron entry**

```bash
( crontab -l 2>/dev/null; echo '0 6 * * * /Users/manolitonora/.latti/scripts/cron.d/identity-daily.sh' ) | crontab -
crontab -l | grep identity-daily
```

Expected: cron entry visible.

- [ ] **Step 5: Set up exports**

```bash
ln -sfn ~/.latti/IDENTITY.md ~/V5/claw-code-agent/IDENTITY.md
ln -sfn ~/.latti/IDENTITY.md ~/.claude/latti-identity.md

readlink ~/V5/claw-code-agent/IDENTITY.md
readlink ~/.claude/latti-identity.md
```

Expected: both resolve to `~/.latti/IDENTITY.md`.

(Future: a small `setup_exports.sh` script in `~/.latti/scripts/` could automate this. Out of scope for v1.)

- [ ] **Step 6: Enable the runtime hook**

Add `export LATTI_IDENTITY_COMPILE=1` to your shell profile, OR run a Latti session with the env var set:

```bash
LATTI_IDENTITY_COMPILE=1 python3 ~/V5/claw-code-agent/path/to/latti-cli ...
```

After the session ends, check that `~/.latti/IDENTITY.md` has updated:
```bash
ls -la ~/.latti/IDENTITY.md
cat ~/.latti/.identity-meta.json
```

Expected: mtime updated since session started; generation incremented.

---

## Acceptance criteria (from spec §9)

After Task 16 manual verification:

- [ ] All 13+ unit tests pass (Tasks 1-12)
- [ ] 1 substrate-shim subprocess test passes (Task 13)
- [ ] 3 runtime hook tests pass (Task 14)
- [ ] 2 integration smoke tests pass (Task 15)
- [ ] Real substrate compile (--thin) produces valid IDENTITY.md
- [ ] Real substrate compile (full) produces IDENTITY.md with LLM prose
- [ ] Daily cron installed and visible in `crontab -l`
- [ ] Symlinks resolve from `~/V5/claw-code-agent/IDENTITY.md` and `~/.claude/latti-identity.md`
- [ ] Day-1 IDENTITY.md is near-empty — confirmed correct per spec §2 non-goals
- [ ] Manual: run twice with no substrate change → no mtime change on IDENTITY.md

---

## Self-review (engineer should run after Task 12 completes, before Task 13)

After all unit tests pass, briefly verify these spec invariants are present in your code:

1. **Substrate filter**: confirm `load_typed_records` skips `MEMORY.md` AND skips files where `path.read_bytes()[:4] != b'---\n'` AND skips files where `LattiMemoryStore.load()` returns None. Three layers of filter. (Spec §3 typed-only.)
2. **Sort by frontmatter**: confirm `load_typed_records_sorted` uses `r.last_used` (NOT `path.stat().st_mtime`). (Spec §5 invariants.)
3. **SHA-gating**: confirm `write_identity_md_if_changed` skips when `new_sha == prior_sha`. (Spec §5 invariants.)
4. **Becoming preservation**: confirm the mtime check uses `last_compiled_at` from `.identity-meta.json` (not from process start). (Spec §5 invariants.)
5. **Failure isolation**: confirm `main()` wraps `compile_identity()` in try/except that ALWAYS returns 0. (Spec §5 invariants.)
6. **Cursor monotonicity**: confirm `append_new_records_to_history` uses `>` strict inequality, not `>=`, against cursor.last_ts. (Spec §5 invariants.)

If any check fails, the offending code violates a spec invariant — fix before proceeding to Task 13.

---

## Open issues from spec §10 (track during implementation)

- **Goals path**: spec assumed `~/.latti/goals.jsonl`. The plan defaults to that via `--goals-path`. If the actual `state_machine_goals.py` writes to a different default, update the cron wrapper and the runtime hook arguments.
- **Multi-instance race**: cron + runtime hook firing the same minute → last-writer-wins. Acceptable for v1.
- **Becoming-section drift**: Latti's mtime-newer edit wins over daemon. Acceptable per spec §10.
