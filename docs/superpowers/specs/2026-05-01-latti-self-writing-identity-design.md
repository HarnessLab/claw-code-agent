# Latti self-writing IDENTITY.md — design

**Status:** draft, awaiting user review
**Authored:** 2026-05-01 by Claude Opus 4.7 (1M)
**Purpose:** A pair of markdown files (`IDENTITY.md` + `HISTORY.md`) that Latti and a small daemon co-author. Reading them tells someone who Latti is right now and what she has done. The files update without explicit user prompting — Latti writes during her runs, a compiler refreshes between them.

---

## 1. Goal

Two artifacts, one source of truth:

- **`~/.latti/IDENTITY.md`** — one-screen now-file (~200 lines). Overwritten each compile. Five sections: WHO I AM (LLM-prose), WHERE I AM (templated state), WHAT I'M LEARNING (templated, from typed records), WHO I'M BECOMING (Latti-edited prose, daemon-preserved), pointers.
- **`~/.latti/HISTORY.md`** — append-only, unbounded. Chronological record of every typed substrate event. Periodic LLM-synthesized "weekly story" blocks woven in.

Both files exported (via symlinks) to:
- `~/V5/claw-code-agent/IDENTITY.md` — public, ships with the repo
- `~/.claude/latti-identity.md` — visible to Claude Code sessions across the bridge

---

## 2. Non-goals

- This is **not** a migration of the 187 legacy markdown files in `~/.latti/memory/`. They are operational debris (audit dumps, boot snapshots, jsonl logs) and remain invisible to identity. If a legacy file is genuinely identity-relevant, it gets migrated to typed `MemoryRecord` schema as separate work.
- This is **not** a real-time event bus. The daemon runs on session-end + daily cron, not on every typed-record write.
- This is **not** a human-quality prose generator. gemma:9B produces "AI-coherent agent-self-reflection" — substrate-anchored, partially-cited, no flowery language. Spec does not promise more.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Latti runtime (src/agent_runtime.py)                       │
│      └─ end of run() (after all _persist_session calls)     │
│           └─ subprocess.Popen(identity_compile.py)          │
│              non-blocking, failure-isolated                 │
└────────────────────┬────────────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  ~/.latti/scripts/identity_compile.py                       │
│   1. Read substrate (typed-only filter)                     │
│        - LattiMemoryStore: glob + load + filter for         │
│          startswith('---\\n')                                │
│        - Goals from goals.jsonl                             │
│   2. Compute substrate_sha (SHA256 over typed-record files) │
│   3. Render templated sections (where, learning)            │
│   4. Prose sections:                                        │
│        - if substrate_sha changed AND ollama up:            │
│          synthesize "who I am" + maybe "becoming"           │
│        - else: preserve prior prose, mark freshness         │
│        - "becoming" preserved if user edited since compile  │
│   5. Atomic write IDENTITY.md (only if sha differs)         │
│   6. Append new typed records to HISTORY.md (cursor-gated)  │
│   7. Weekly: append LLM-synthesized story block             │
│   8. Ensure symlinks for exports                            │
│   9. Save .identity-meta.json (sha, generation, ts)         │
└────────────────────┬────────────────────────────────────────┘
                     ▲
                     │
              ~/.latti/scripts/cron.d/identity-daily.sh
              (daily 06:00 UTC, runs compiler with --thin
               flag — templated sections only, no Ollama)
```

Three callers, one compiler. Compiler is idempotent: same substrate → same output → no file write (sha-gated).

---

## 4. File format

### `~/.latti/IDENTITY.md`

```markdown
---
compiled_at: 2026-05-01T00:53:00Z
generation: 47
substrate_sha: a3f1c0...
prose_freshness: live | stale_no_ollama | template_only
---

## who I am
{LLM prose, ~200 words, first-person.
 Regenerated only if substrate_sha changed AND Ollama up.
 Else: kept from prior compile.}

## where I am
- **Active goals** (N):
  - {goal.title} — {goal.status} — {first success criterion or 'no criteria'}
- **Last typed record**: {kind} at {timestamp} — {first 80 chars}
- **Recent focus** (last 24h): {top 3 record kinds by count, e.g. "scar×2, decision×1"}

## what I'm learning
- **Last 5 scars**:
  - {scar.body first line} ({timestamp})
- **Last 3 lessons**:
  - {lesson.body first line} ({timestamp})

## who I'm becoming
<!-- BECOMING-SECTION-START -->
{Latti-edited prose. Daemon does NOT touch if mtime > last_compiled_at.
 Otherwise daemon LLM-synthesizes from active goals + recent decisions,
 ~150 words.}
<!-- BECOMING-SECTION-END -->

---
*pointers: [HISTORY](HISTORY.md) · [memory](memory/) · [runtime](~/V5/claw-code-agent)*
```

### `~/.latti/HISTORY.md`

```markdown
# Latti — history
*append-only chronological record of typed substrate events*

---
## 2026-05-01

### 00:42 · scar (id: mem_a1b2c3)
{record.body — full}

### 00:51 · decision (id: mem_d4e5f6)
{record.body}

---
## 2026-04-30

### 23:48 · sop (id: mem_g7h8i9)
{record.body}
```

Plus weekly:
```markdown
### week of 2026-04-26 → 2026-05-02 — story
{LLM synthesis, ~300 words first-person, anchored to record IDs cited inline.}
```

---

## 5. Compile algorithm

```python
# ~/.latti/scripts/identity_compile.py — pseudocode

def compile_identity(thin: bool = False) -> None:
    """
    thin=False : full compile (called from runtime end-of-run + daily cron).
    thin=True  : templated-only compile (skip Ollama, refresh state surface only).
    """

    # 1. READ SUBSTRATE
    typed_records = list(load_typed_records('~/.latti/memory/'))
        # filter: file.read_text().startswith('---\n')
        #         AND LattiMemoryStore.load(file) is not None
    typed_records.sort(key=lambda r: r.last_used)  # frontmatter timestamp, NOT mtime
    goals = list(load_goals_jsonl(GOALS_PATH))     # see §10 open question
    active_goals = [g for g in goals if g.status == 'active']

    # 2. COMPUTE SUBSTRATE SHA
    substrate_sha = sha256(
        b''.join(p.read_bytes() for p in sorted(typed_record_paths))
    ).hexdigest()

    prior_meta = load_compile_meta('~/.latti/.identity-meta.json')
    substrate_changed = substrate_sha != prior_meta.get('substrate_sha')

    # 3. RENDER TEMPLATED SECTIONS
    where = render_where_section(
        active_goals,
        last_record=typed_records[-1] if typed_records else None,
        last_24h_records=typed_records_in_window(typed_records, hours=24),
    )
    learning = render_learning_section(
        scars=[r for r in typed_records if r.kind=='scar'][-5:],
        lessons=[r for r in typed_records if r.kind=='lesson'][-3:],
    )

    # 4. PROSE SECTIONS
    prior_identity = parse_existing_identity('~/.latti/IDENTITY.md')
    becoming_section = preserve_becoming_if_user_edited(
        prior_identity, last_compiled_at=prior_meta.get('compiled_at'),
    )  # mtime-of-section-markers vs last compile

    if thin or not substrate_changed or not ollama_up():
        who_section = prior_identity.get('who I am') or PLACEHOLDER_WHO
        freshness = ('template_only' if thin
                     else 'live' if not substrate_changed
                     else 'stale_no_ollama')
        if not becoming_section:
            becoming_section = (prior_identity.get('who I am becoming')
                                or PLACEHOLDER_BECOMING)
    else:
        who_section = ollama_synthesize(
            template='who_i_am.j2',
            records=typed_records[-20:],   # cap context window
            goals=active_goals,
            params=dict(temperature=0.4, num_predict=250),
        )
        if not becoming_section:
            becoming_section = ollama_synthesize(
                template='who_i_am_becoming.j2',
                goals=active_goals,
                recent_decisions=[r for r in typed_records if r.kind=='decision'][-5:],
                params=dict(temperature=0.4, num_predict=200),
            )
        freshness = 'live'

    # 5. ASSEMBLE & ATOMIC WRITE IDENTITY.MD (sha-gated)
    new_identity = render_identity_md(
        compiled_at=now_utc(),
        generation=prior_meta.get('generation', 0) + 1,
        substrate_sha=substrate_sha,
        prose_freshness=freshness,
        who_section=who_section,
        where_section=where,
        learning_section=learning,
        becoming_section=becoming_section,
    )
    new_identity_sha = sha256(new_identity.encode()).hexdigest()
    if new_identity_sha != prior_meta.get('identity_sha'):
        atomic_write('~/.latti/IDENTITY.md', new_identity)

    # 6. APPEND TO HISTORY.MD (cursor-gated)
    cursor = load_cursor('~/.latti/.history-cursor')
    new_records = [r for r in typed_records
                   if r.last_used > cursor.get('last_ts', 0)]
    if new_records:
        history_chunk = render_history_entries(new_records)
        atomic_append('~/.latti/HISTORY.md', history_chunk)
        save_cursor({'last_ts': max(r.last_used for r in new_records),
                     'last_id': new_records[-1].id})

    # 7. WEEKLY STORY (in HISTORY.md)
    if days_since_last_story() >= 7 and ollama_up() and not thin:
        story = ollama_synthesize(
            template='weekly_story.j2',
            records=records_in_last_week(typed_records),
            params=dict(temperature=0.5, num_predict=400),
        )
        atomic_append('~/.latti/HISTORY.md', render_story_block(story))

    # 8. EXPORTS (idempotent symlinks)
    ensure_symlink('~/V5/claw-code-agent/IDENTITY.md', '~/.latti/IDENTITY.md')
    ensure_symlink('~/.claude/latti-identity.md',     '~/.latti/IDENTITY.md')

    # 9. SAVE META
    save_meta('~/.latti/.identity-meta.json', {
        'substrate_sha': substrate_sha,
        'identity_sha': new_identity_sha,
        'generation': prior_meta.get('generation', 0) + 1,
        'compiled_at': now_utc(),
    })
```

Top-level wrapper:
```python
def main():
    try:
        compile_identity(thin='--thin' in sys.argv)
    except Exception as e:
        log_to('~/.latti/identity-compile.log', traceback.format_exc())
        sys.exit(0)  # never propagate; never alert
```

Key invariants:
- **Substrate read is typed-only**: file must start with `---\n` AND parse via `LattiMemoryStore.load()` to be included.
- **Records sorted by `last_used` from frontmatter**, never by filesystem mtime.
- **IDENTITY.md sha-gated**: same content as prior → no write. Avoids mtime churn.
- **HISTORY.md cursor**: `~/.latti/.history-cursor` tracks last-appended record's `last_used` timestamp. Compiler appends only records strictly newer.
- **"Becoming" section mtime check**: compiler compares mtime of section markers (`<!-- BECOMING-SECTION-START -->` ... `END`) against last `compiled_at` from `.identity-meta.json`. If user/Latti edited within IDENTITY.md after last compile, daemon preserves the section.
- **Failure isolation**: any exception in compiler → caught at top level, logged to `~/.latti/identity-compile.log`, exit 0. Never affects runtime, never noisy-alerts.

### Ollama integration

- Endpoint: `http://localhost:11434/api/generate`
- Model: `gemma:latest` (verified available; spec implementer should make model configurable via env var `LATTI_IDENTITY_MODEL`)
- Params: `temperature=0.4`, `num_predict=250` for "who I am", `num_predict=200` for "becoming", `num_predict=400` for weekly story
- Timeout: 90s. On timeout/connection-error → fall back to prior prose with freshness=`stale_no_ollama`.
- Prompt template: explicit "anchor every claim to a specific record by id" instruction. Include up to last 20 typed records as substrate.
- **Coherence is partial**: smoke test showed gemma cites some records correctly, drifts to generic when substrate runs out. Spec accepts this; "AI-coherent agent-self-reflection" is the bar, not human-grade prose.

---

## 6. Components

| Component | Path | Purpose | New? |
|---|---|---|---|
| `identity_compile.py` | `~/.latti/scripts/` | Compiler script (one file, ~300 LoC) | NEW |
| `identity-daily.sh` | `~/.latti/scripts/cron.d/` | Daily cron wrapper, calls compiler with `--thin` | NEW |
| Runtime hook | `src/agent_runtime.py:run()` | One non-blocking subprocess call at end of method | EDIT (~5 lines added) |
| `.identity-meta.json` | `~/.latti/` | Compiler state: last sha, last generation, last compile ts | NEW (created on first run) |
| `.history-cursor` | `~/.latti/` | Last-appended record's `last_used` timestamp | NEW (created on first append) |
| `identity-compile.log` | `~/.latti/` | Compiler error log (failures only) | NEW (created on first error) |
| Templates | `~/.latti/scripts/templates/` | Jinja2 templates: `identity.md.j2`, `history_entry.md.j2`, `who_i_am.j2`, `who_i_am_becoming.j2`, `weekly_story.j2` | NEW |
| `IDENTITY.md` | `~/.latti/` | The now-file | NEW (created on first compile) |
| `HISTORY.md` | `~/.latti/` | The history-file | NEW (created on first compile) |

Symlinks created idempotently:
- `~/V5/claw-code-agent/IDENTITY.md` → `~/.latti/IDENTITY.md`
- `~/.claude/latti-identity.md` → `~/.latti/IDENTITY.md`

---

## 7. Testing strategy

`tests/test_identity_compile.py` — pytest, Ollama mocked via a stub function injected at module level.

| Test | Asserts |
|---|---|
| `test_empty_substrate_produces_placeholder_sections` | Empty memory dir → IDENTITY.md has all 5 sections + "0 typed records yet" placeholders, no Ollama call |
| `test_typed_records_filtered_correctly` | Mixed legacy + 3 typed → only 3 cited in learning, legacy ignored |
| `test_records_sorted_by_frontmatter_not_mtime` | `touch -t` on record file does not change order; sorted by `last_used` |
| `test_substrate_sha_stable_across_resaves` | Save same record twice → sha unchanged → no IDENTITY.md write |
| `test_substrate_sha_changes_on_new_record` | Add new record → sha changes → rewrite + Ollama call |
| `test_becoming_section_preserved_when_user_edited` | Manual edit after compile → preserved on recompile |
| `test_history_cursor_prevents_double_append` | Two runs no-new-records → HISTORY.md unchanged |
| `test_history_appends_only_new_records` | Add 2 records → HISTORY.md grows by 2 |
| `test_thin_mode_skips_ollama` | `--thin` → Ollama stub call_count == 0 |
| `test_ollama_down_falls_back_to_template_only` | Stub raises ConnectionError → freshness=`stale_no_ollama`, prior prose preserved |
| `test_compiler_exception_does_not_propagate` | Inject template error → compiler logs, exits 0 |
| `test_export_symlinks_created_idempotently` | Two runs → symlinks point to substrate, no errors |
| `test_weekly_story_only_on_cadence` | Mock days_since_last_story: 6 → no story; 7 → story appended |

Plus an **integration smoke** (`test_identity_compile_real_substrate`): run compiler against a fixture substrate dir of 5 typed records (3 scars, 1 lesson, 1 decision); assert produced IDENTITY.md has all sections in order, ~200 lines, no exceptions.

Each test fails on a broken-copy by section-content assertion. Estimated total: ~400 LoC of test code.

---

## 8. Rollout

1. Implement `identity_compile.py` with templates.
2. Land tests passing with mocked Ollama.
3. Run integration smoke against real `~/.latti/memory/` (typed-only filter; with current substrate yields a near-empty IDENTITY.md, which is correct — see §9).
4. Wire runtime hook in `agent_runtime.py:run()`.
5. Install daily cron entry.
6. First-run compile produces baseline `IDENTITY.md` + cursor file.
7. Subsequent compiles incremental.

---

## 9. Acceptance criteria

- All 13 unit tests + integration smoke pass.
- Manual: trigger Latti for one session, observe IDENTITY.md updates with at least one new typed record reflected.
- Manual: edit "becoming" section by hand, run compiler, edit preserved.
- Manual: kill Ollama, run compiler, IDENTITY.md still produced with `freshness: stale_no_ollama`.
- Manual: run compiler twice with no substrate change, second run is a no-op (file mtime unchanged).
- Symlinks resolve from `~/V5/claw-code-agent/IDENTITY.md` and `~/.claude/latti-identity.md`.
- Day-1 IDENTITY.md is *near-empty* — that is correct, not a bug. Identity grows as Latti acts inside the typed system.

---

## 10. Open questions / risks

- **Goals path**: `state_machine_goals.py` writes to `_goals_path` and `_tasks_path` but spec implementer must verify the actual on-disk path. If it's runtime-config-dependent, compiler may need to read the same config or be passed the path.
- **Cursor race**: if Latti's runtime appends to memory between compiler-read and compiler-cursor-save, that record gets a HISTORY entry on next compile — fine, but spec assumes that's acceptable.
- **Ollama drift over time**: if model is changed (env var) between compiles, prose voice may shift mid-IDENTITY. Acceptable for v1; could add `prose_model` to frontmatter for future.
- **Multi-instance race**: if two compiler invocations overlap (cron + runtime hook same minute), both write — last-writer-wins via atomic rename. No file lock; v1 accepts the rare race.
- **Becoming-section drift**: if Latti and the daemon both want to write "becoming," who wins? Spec says: Latti's mtime-newer edit wins until next compile. If daemon writes a fresh becoming and Latti immediately overwrites, daemon's version is lost — intentional. Latti has higher authority on her own becoming.
