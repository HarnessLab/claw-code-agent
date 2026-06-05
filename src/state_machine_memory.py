"""Persistence bridge between typed MemoryRecord and ~/.latti/memory/ files.

Step 5.8 of the runway in ``~/.latti/STATE_MACHINE.md``: the typed MemoryRecord
schema exists in agent_state_machine.py, but no code today writes one to disk.
This module bridges that — saving records as YAML-frontmatter+markdown files
matching the existing scar/SOP/feedback format, and updating the MEMORY.md
index atomically.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Iterable

from src.agent_state_machine import MemoryRecord, MemoryKind


_FRONTMATTER_PATTERN = re.compile(
    r'^---\n(?P<fm>.*?)\n---\n(?P<body>.*)\Z', re.DOTALL,
)
# Slug-friendly chars for filename derivation
_SLUG_CHARS = re.compile(r'[^a-zA-Z0-9_]+')


def _slugify(name: str, fallback: str) -> str:
    s = _SLUG_CHARS.sub('_', name).strip('_').lower()
    return s or fallback


def _today_str() -> str:
    return datetime.date.today().isoformat()


def _format_frontmatter(record: MemoryRecord, name: str | None = None,
                        description: str | None = None) -> str:
    """Build the YAML frontmatter block for a MemoryRecord."""
    lines = ['---']
    if name:
        lines.append(f'name: {name}')
    if description:
        # Single-line description; collapse newlines
        desc = description.replace('\n', ' ').strip()
        lines.append(f'description: {desc}')
    lines.append(f'type: {record.kind}')
    lines.append(f'id: {record.id}')
    last_used = datetime.date.fromtimestamp(record.last_used).isoformat() \
        if record.last_used else _today_str()
    lines.append(f'last_used: {last_used}')
    if record.source_session_id:
        lines.append(f'originSessionId: {record.source_session_id}')
    if record.source_turn_id:
        lines.append(f'sourceTurnId: {record.source_turn_id}')
    lines.append('---')
    return '\n'.join(lines)


class LattiMemoryStore:
    """Reads/writes MemoryRecords to ~/.latti/memory/ as frontmatter+markdown.

    Filename convention: ``{kind}_{slug}.md`` where slug is derived from a
    user-supplied ``name`` (slugified) or from the record id if no name is
    given. The ``MEMORY.md`` index is updated on save with a one-line pointer.
    """

    def __init__(self, memory_dir: Path | str) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / 'MEMORY.md'

    @property
    def memory_dir(self) -> Path:
        return self._dir

    def save(
        self,
        record: MemoryRecord,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Path:
        """Write the record to disk and update MEMORY.md index. Returns path."""
        slug = _slugify(name or record.id, fallback=record.id.replace('mem_', ''))
        filename = f'{record.kind}_{slug}.md'
        path = self._dir / filename

        body = record.body or ''
        if not body.endswith('\n'):
            body = body + '\n'

        content = _format_frontmatter(record, name=name, description=description) \
            + '\n' + body

        # Atomic write: tempfile + rename
        tmp = path.with_suffix(path.suffix + f'.tmp.{record.id}')
        tmp.write_text(content, encoding='utf-8')
        tmp.replace(path)

        self._update_index(filename, name or record.id, description or '')
        return path

    def load(self, file_path: Path | str) -> MemoryRecord | None:
        """Parse a memory file back into a MemoryRecord. Returns None on failure."""
        p = Path(file_path)
        if not p.is_file():
            return None
        try:
            text = p.read_text(encoding='utf-8')
        except OSError:
            return None
        m = _FRONTMATTER_PATTERN.match(text)
        if not m:
            return None
        fm_lines = m.group('fm').splitlines()
        body = m.group('body').rstrip('\n')

        fm: dict[str, str] = {}
        for line in fm_lines:
            if ':' in line:
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip()

        kind = fm.get('type')
        # Map legacy kinds to the closest MemoryKind first.
        _LEGACY_TO_MEMORY = {'feedback': 'scar', 'project': 'reference', 'user': 'reference'}
        if kind in _LEGACY_TO_MEMORY:
            kind = _LEGACY_TO_MEMORY[kind]
        if kind not in ('scar', 'sop', 'lesson', 'decision', 'reference'):
            return None

        rec_id = fm.get('id') or f'mem_loaded_{p.stem}'
        last_used_str = fm.get('last_used') or _today_str()
        try:
            d = datetime.date.fromisoformat(last_used_str)
            ts = datetime.datetime(d.year, d.month, d.day).timestamp()
        except (ValueError, TypeError):
            ts = datetime.datetime.now().timestamp()

        return MemoryRecord(
            id=rec_id,
            kind=kind,  # type: ignore[arg-type]
            body=body,
            last_used=ts,
            source_session_id=fm.get('originSessionId'),
            source_turn_id=fm.get('sourceTurnId'),
        )

    def recall(
        self,
        query: str,
        *,
        kind: MemoryKind | None = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Keyword-overlap search over stored MemoryRecords.

        Tokenizes ``query`` (lowercase, drop tokens shorter than 3 chars),
        scores each record by the count of distinct query tokens that
        appear in its body, and returns the top ``limit`` records sorted
        by score descending. Ties broken by recency (more recent
        ``last_used`` wins).

        Records with zero token overlap are dropped — the LLM should
        receive an empty list, not noise, when nothing matches.

        Tested by tests/test_memory_recall.py.
        """
        if not query or not query.strip():
            return []
        query_tokens = {
            tok for tok in re.findall(r'[a-z0-9]+', query.lower())
            if len(tok) >= 3
        }
        if not query_tokens:
            return []
        scored: list[tuple[int, float, MemoryRecord]] = []
        for rec in self.list_records(kind=kind):
            body_tokens = set(re.findall(r'[a-z0-9]+', rec.body.lower()))
            overlap = len(query_tokens & body_tokens)
            if overlap == 0:
                continue
            scored.append((overlap, rec.last_used, rec))
        # Sort by score desc, then recency desc.
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [rec for _score, _ts, rec in scored[:limit]]

    def list_records(self, kind: MemoryKind | None = None) -> list[MemoryRecord]:
        """Return all records on disk, optionally filtered by kind."""
        out: list[MemoryRecord] = []
        for path in sorted(self._dir.glob('*.md')):
            if path.name == 'MEMORY.md':
                continue
            rec = self.load(path)
            if rec is None:
                continue
            if kind is not None and rec.kind != kind:
                continue
            out.append(rec)
        return out

    def _update_index(self, filename: str, name: str, description: str) -> None:
        """Append a one-line pointer to MEMORY.md if not already present."""
        line = f'- [{filename}]({filename}) — {description or name}'
        existing = ''
        if self._index_path.exists():
            existing = self._index_path.read_text(encoding='utf-8')
        # Skip if the filename is already indexed
        if f'[{filename}](' in existing:
            return
        if existing and not existing.endswith('\n'):
            existing = existing + '\n'
        self._index_path.write_text(existing + line + '\n', encoding='utf-8')
