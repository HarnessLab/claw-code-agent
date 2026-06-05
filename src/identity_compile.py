# src/identity_compile.py
"""Compile Latti's typed substrate into IDENTITY.md (now-file) + HISTORY.md.

See docs/superpowers/specs/2026-05-01-latti-self-writing-identity-design.md.

Substrate read is *typed-only*: file must start with '---\n' AND parse via
LattiMemoryStore.load(). Legacy markdown files in ~/.latti/memory/ are
invisible to identity by design (~98% are operational debris).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Iterator

from src.agent_state_machine import MemoryRecord
from src.state_machine_memory import LattiMemoryStore
from src.identity_templates import (
    WHERE_SECTION, LEARNING_SECTION, IDENTITY_MD,
    PLACEHOLDER_NO_GOALS, PLACEHOLDER_NO_RECORDS,
    PLACEHOLDER_NO_SCARS, PLACEHOLDER_NO_LESSONS,
    HISTORY_HEADER, HISTORY_ENTRY,
    WHO_I_AM_PROMPT, WHO_I_AM_BECOMING_PROMPT,
)


def load_typed_records(memory_dir: Path) -> Iterator[MemoryRecord]:
    """Yield typed MemoryRecords from memory_dir.

    A file is 'typed' if it starts with '---\n' AND LattiMemoryStore.load()
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


_BECOMING_RE = re.compile(
    r'<!-- BECOMING-SECTION-START -->\n(?P<body>.*?)\n<!-- BECOMING-SECTION-END -->',
    re.DOTALL,
)
_WHO_RE = re.compile(
    r'<!-- WHO-SECTION-START -->\n(?P<body>.*?)\n<!-- WHO-SECTION-END -->',
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


def extract_who_section(identity_path: Path) -> str | None:
    """Return the contents between WHO-SECTION markers, or None.

    Markers (mirror of BECOMING) are robust against LLM prose containing
    its own `## ` headers — see Task 16 manual verification finding.
    """
    if not identity_path.is_file():
        return None
    try:
        text = identity_path.read_text(encoding='utf-8')
    except OSError:
        return None
    m = _WHO_RE.search(text)
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


OLLAMA_TIMEOUT = 90.0


def _format_substrate_block(records: list[MemoryRecord]) -> str:
    """Format records as a readable block for Ollama prompt."""
    if not records:
        return '(no typed records yet)'
    lines = []
    for r in records:
        body_one_line = ' '.join(r.body.split())[:200]
        lines.append(f'[{r.kind} {r.id}] {body_one_line}')
    return '\n'.join(lines)


def _format_goals_block(active_goals: list) -> str:
    """Format active goals as a readable block for Ollama prompt."""
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


_RECORD_ID_RE = re.compile(r'\bmem_[a-z0-9_]+(?<!_)')

# Substrate uses 'mem_<slug>' IDs exclusively. Natural-language refs like
# "Decision #3" or "Goal #12" cannot point at a real record by definition,
# so any match here is a hallucination by construction.
_FAKE_REF_RE = re.compile(
    r'\b(?:Decision|Goal|Task|Scar|Lesson|SOP|Record|Memory) #\d+\b'
)


def validate_record_ids(prose: str, valid_ids: set[str]) -> str:
    """Mark hallucinated record references in LLM prose with strikethrough.

    Two patterns marked:
      1. mem_<slug> IDs not in valid_ids (typed-format invented IDs)
      2. "Decision #N" / "Goal #N" / similar natural-language refs —
         these CANNOT reference a real record because substrate uses
         mem_* IDs exclusively, so any such phrase is a hallucination.

    Real example from generation 5 IDENTITY.md prose: gemma wrote
    "the emphasis on data integrity in Decision #3 suggests..." with
    no Decision #3 in substrate. v1b regex missed it (only mem_* form);
    v1c catches both forms.
    """
    def _maybe_mark_id(m: re.Match) -> str:
        cited = m.group(0)
        return cited if cited in valid_ids else f'~~{cited}~~'

    def _mark_fake_ref(m: re.Match) -> str:
        # Always mark — these forms can't be valid by definition.
        return f'~~{m.group(0)}~~'

    prose = _RECORD_ID_RE.sub(_maybe_mark_id, prose)
    prose = _FAKE_REF_RE.sub(_mark_fake_ref, prose)
    return prose


# ---------------------------------------------------------------------------
# Task 10: top-level compile_identity orchestration
# ---------------------------------------------------------------------------

import time as _time
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
    goals: Path


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


def _content_sha(content: str) -> str:
    """SHA256 of IDENTITY.md content with volatile frontmatter lines stripped.

    compiled_at and generation change every run even when body is identical.
    Excluding them lets the sha-gate detect "same prose, different metadata"
    as unchanged and skip a redundant disk write.
    """
    stable = re.sub(r'^compiled_at:.*\n', '', content, count=1, flags=re.MULTILINE)
    stable = re.sub(r'^generation:.*\n', '', stable, count=1, flags=re.MULTILINE)
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


def _load_active_goals(goals_path: Path) -> list:
    """Read goals.jsonl, return ones with status='active'.

    Returns [] if path doesn't exist.
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
                goals[d['id']] = d
    except OSError:
        return []

    class _GoalView:
        def __init__(self, d: dict) -> None:
            self.title = d.get('title', '(unnamed)')
            self.status = d.get('status', 'unknown')
            self.success_criteria = tuple(d.get('success_criteria', ()))

    return [_GoalView(d) for d in goals.values() if d.get('status') == 'active']


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


def compile_identity(*, paths: 'IdentityPaths', ollama_base: str, ollama_model: str,
                     thin: bool = False) -> None:
    """Top-level compile. Idempotent. Failure-isolated by caller (main()).

    Args:
        paths:        Resolved filesystem paths for this invocation.
        ollama_base:  Ollama HTTP base URL (e.g. http://localhost:11434).
        ollama_model: Ollama model name (e.g. gemma:latest).
        thin:         If True, skip Ollama calls; use template placeholders only.
    """
    records = load_typed_records_sorted(paths.memory_dir)
    substrate_sha = compute_substrate_sha(paths.memory_dir)
    prior_meta = _load_meta(paths.meta)
    substrate_changed = substrate_sha != prior_meta.get('substrate_sha')

    active_goals = _load_active_goals(paths.goals)
    where = render_where_section(active_goals=active_goals, records=records)
    learning = render_learning_section(
        scars=[r for r in records if r.kind == 'scar'][-5:],
        lessons=[r for r in records if r.kind == 'lesson'][-3:],
    )

    prior_compile_at = prior_meta.get('compiled_at_epoch')
    becoming = preserve_becoming_if_user_edited(paths.identity, prior_compile_at)
    prior_who = extract_who_section(paths.identity)

    from src.identity_templates import PLACEHOLDER_WHO, PLACEHOLDER_BECOMING

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
            # Mark hallucinated record IDs in LLM prose (v1b hardening).
            valid_ids = {r.id for r in records}
            if who_new is not None:
                who_new = validate_record_ids(who_new, valid_ids)
            if becoming_new is not None:
                becoming_new = validate_record_ids(becoming_new, valid_ids)

        if substrate_changed and who_new is None:
            freshness = 'stale_no_ollama'
        else:
            freshness = 'live'

        who = who_new or prior_who or PLACEHOLDER_WHO
        if becoming is None:
            becoming = becoming_new or extract_becoming_section(paths.identity) or PLACEHOLDER_BECOMING

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

    # sha-gate: compare content excluding volatile compiled_at + generation.
    # write_identity_md_if_changed uses full-content sha; we use a stable sha
    # (timestamp-stripped) so that a re-compile with identical prose but a
    # different timestamp is correctly treated as "unchanged".
    prior_content_sha = prior_meta.get('content_sha')
    new_content_sha = _content_sha(new_identity)
    if prior_content_sha != new_content_sha:
        write_identity_md_if_changed(paths.identity, new_identity, prior_sha=None)
    # else: sha matches → skip write (mtime preserved)

    append_new_records_to_history(
        history_path=paths.history, cursor_path=paths.cursor, records=records,
    )

    _save_meta(paths.meta, {
        'substrate_sha': substrate_sha,
        'content_sha': new_content_sha,
        'generation': prior_meta.get('generation', 0) + 1,
        'compiled_at': _now_iso(),
        'compiled_at_epoch': _time.time(),
    })


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


# ---------------------------------------------------------------------------
# CLI main + exception isolation
# ---------------------------------------------------------------------------

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
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
