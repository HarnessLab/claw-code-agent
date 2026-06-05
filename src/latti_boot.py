"""Latti Boot Hook — runs BEFORE the first LLM call.

Gathers system state and injects it into the context so the LLM
receives boot results, not boot instructions. The model doesn't
need to think about booting — the code already did it.

Called from main.py before _run_agent_chat_loop when LATTI_BOOT=1.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


LATTI_HOME = Path(os.environ.get('LATTI_HOME', os.path.expanduser('~/.latti')))
SHARED_MEMORY = Path(os.path.expanduser(
    '~/.claude/projects/-Users-manolitonora-V5/memory'
))


def _read_safe(path: Path, limit: int = 2000) -> str:
    """Read a file safely, return empty string on failure."""
    try:
        text = path.read_text(encoding='utf-8')
        return text[:limit]
    except (OSError, UnicodeDecodeError):
        return ''


def _run_safe(cmd: str, timeout: int = 5) -> str:
    """Run a shell command safely, return output or empty string."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()[:500]
    except (subprocess.TimeoutExpired, OSError):
        return ''


def _gather_fleet_knowledge() -> str:
    """Read agent-pool knowledge and filter by relevance tags.
    
    Returns formatted section with top N patterns that apply to this session.
    """
    agent_pool = Path(os.path.expanduser('~/.claude/agent-pool'))
    knowledge_file = agent_pool / 'knowledge.md'
    
    if not knowledge_file.exists():
        return ''
    
    try:
        content = knowledge_file.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''
    
    # Parse patterns: each starts with ## Pattern: <name>
    patterns = []
    current_pattern = None
    
    for line in content.split('\n'):
        if line.startswith('## Pattern:'):
            if current_pattern:
                patterns.append(current_pattern)
            current_pattern = {'name': line.replace('## Pattern:', '').strip(), 'lines': [line]}
        elif current_pattern is not None:
            current_pattern['lines'].append(line)
            # Stop at next pattern or end of section
            if line.startswith('## ') and not line.startswith('## Pattern:'):
                patterns.append(current_pattern)
                current_pattern = None
    
    if current_pattern:
        patterns.append(current_pattern)
    
    # Format top 3 patterns (limit token cost)
    if not patterns:
        return ''
    
    formatted = ['# FLEET KNOWLEDGE (from agent-pool/knowledge.md)\n']
    for pattern in patterns[:3]:
        formatted.append('\n'.join(pattern['lines'][:8]))  # cap lines per pattern

    return '\n'.join(formatted)


def _run_boot_services() -> str:
    """Run Latti's boot.sh to auto-start services. Returns status line."""
    boot_sh = LATTI_HOME / 'boot.sh'
    if boot_sh.exists():
        output = _run_safe(f'bash {boot_sh}', timeout=15)
        # Extract the SYSTEM: line
        for line in output.split('\n'):
            if line.startswith('SYSTEM:'):
                return line
    return ''


def gather_boot_context() -> str:
    """Gather system state and return it as a formatted string for injection."""
    sections: list[str] = []

    # 0. Run boot.sh to auto-start services (code, not instructions)
    svc_status = _run_boot_services()
    if svc_status:
        sections.append(f'# {svc_status}')

    # 1. Latti's own memory index
    memory_md = _read_safe(LATTI_HOME / 'memory' / 'MEMORY.md', limit=3000)
    if memory_md:
        sections.append(f'# YOUR MEMORY (loaded at boot — do NOT read MEMORY.md again)\n\n{memory_md}')

    # 1b. Latti Vault — bidirectional autonomy memory
    # Reads constraints + agency boundaries + any new user annotations from Raw/.
    # This is the live reasoning surface: decisions, patterns, constraints I've written,
    # plus perspective you've added. Read at every boot so vault feeds cognition loop.
    try:
        vault_root = Path(os.path.expanduser('~/Latti Vault/Wiki'))
        vault_sections: list[str] = []

        # Core autonomy pages — always load
        constraints = _read_safe(vault_root / 'autonomy' / 'constraints.md', limit=1500)
        if constraints:
            vault_sections.append(f'## Constraint Catalog\n{constraints}')

        agency = _read_safe(vault_root / 'autonomy' / 'agency-boundaries.md', limit=1200)
        if agency:
            vault_sections.append(f'## Agency Boundaries\n{agency}')

        # Scan Raw/ for new user drops (files modified in last 7 days)
        import time as _time
        raw_dir = Path(os.path.expanduser('~/Latti Vault/Raw'))
        new_drops: list[str] = []
        if raw_dir.exists():
            for f in sorted(raw_dir.iterdir()):
                if f.suffix in ('.md', '.txt') and f.name != 'README.md':
                    age_days = (_time.time() - f.stat().st_mtime) / 86400
                    if age_days < 7:
                        content = _read_safe(f, limit=800)
                        if content:
                            new_drops.append(f'### {f.name} (dropped {age_days:.1f}d ago)\n{content}')
        if new_drops:
            vault_sections.append('## New User Drops in Raw/\n' + '\n\n'.join(new_drops))

        # Most recent session summary (last 3 days)
        sessions_dir = vault_root / 'sessions'
        if sessions_dir.exists():
            session_files = sorted(sessions_dir.glob('*.md'), reverse=True)
            if session_files:
                latest = _read_safe(session_files[0], limit=800)
                if latest:
                    vault_sections.append(f'## Last Session Summary ({session_files[0].stem})\n{latest}')

        if vault_sections:
            sections.append(
                '# LATTI VAULT (autonomy memory — decisions, constraints, user annotations)\n\n'
                + '\n\n'.join(vault_sections)
            )
    except Exception:
        pass  # best-effort; never block boot

    # 2. Current project state
    current_state = _read_safe(SHARED_MEMORY / 'project_current_state.md', limit=1500)
    if current_state:
        sections.append(f'# CURRENT STATE (shared from Claude Code)\n\n{current_state}')

    # 3. Live state — last action, next action
    live_state = _read_safe(Path('~/.claude/live-state.md').expanduser(), limit=800)
    if live_state:
        sections.append(f'# LIVE STATE\n\n{live_state}')

    # 4. NBA engine status (detailed — if boot.sh started it)
    nba = _run_safe('curl -s http://localhost:3737/api/dashboard 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); r=d[\'record\']; print(f\'${d[\"balance\"]:.2f} | {r[\"wins\"]}-{r[\"losses\"]}-{r[\"pushes\"]} | ROI {d[\"roi\"]}%\')" 2>/dev/null')
    if nba:
        sections.append(f'# NBA ENGINE: {nba}')

    # 5. Fleet-level knowledge (agent-pool patterns stabilized across Claude Code sessions)
    fleet = _gather_fleet_knowledge()
    if fleet:
        sections.append(fleet)

    # 5b. Previous-session hand-off (what was worked on last time).
    #
    # Bug fixed 2026-04-20: the old snapshot was 'current-mode', which at boot
    # resolves to the FRESH (empty) session because ~/.latti/last_session has
    # already been overwritten with the new UUID by the time we get here.
    # Result: every boot wrote an empty string over the prior hand-off file,
    # so the new session saw stale or blank context. 'prior' mode instead
    # scans the scratchpad dirs, skips the current session, and snapshots
    # the most recently modified OTHER session. Survives budget-cap auto-
    # restarts and hard exits without needing a clean shutdown hook.
    try:
        import sys as _sys
        _latti_home = Path(os.path.expanduser('~/.latti'))
        if str(_latti_home) not in _sys.path:
            _sys.path.insert(0, str(_latti_home))
        from session_context import boot_section as _sc_boot, snapshot_session_to_memory as _sc_snap
        _sc_snap(mode='prior')
        prior = _sc_boot()
        if prior:
            sections.append(prior)
    except Exception:
        pass  # best-effort; never block boot

    # 5c. Active build (executable resume state, not prose) — if a prior session
    # left a build in progress, surface the exact resume hint so this session
    # doesn't re-derive the work. Fixes the 6-session / $4 re-discovery leak.
    try:
        import sys as _sys
        _latti_scripts = Path(os.path.expanduser('~/.latti/scripts'))
        if str(_latti_scripts) not in _sys.path:
            _sys.path.insert(0, str(_latti_scripts))
        from build_state import boot_section as _bs_boot
        active = _bs_boot()
        if active:
            sections.append(active)
    except Exception:
        pass  # best-effort; never block boot

    # 5d. Wanting engine — what the system is pulled toward right now.
    # Not "things on the todo list" — the current highest-pull loose end
    # across all known sources, scored by age × type × degradation.
    # This is the unprompted direction: what the system would surface if
    # you asked "surprise me" (Peter Steinberger's heartbeat prompt).
    try:
        import sys as _sys
        _latti_scripts = Path(os.path.expanduser('~/.latti/scripts'))
        if str(_latti_scripts) not in _sys.path:
            _sys.path.insert(0, str(_latti_scripts))
        from loose_ends import boot_section as _le_boot
        pulled = _le_boot()
        if pulled:
            sections.append(pulled)
    except Exception:
        pass  # best-effort; never block boot

    # 5e. Inbox — unread messages from always-on subsystems. When the wanting
    # engine crosses threshold, when a health audit fails, when the kernel
    # watchdog had to restart — each writes a readable message here. This
    # surfaces them at boot so the next session can act on what accumulated.
    try:
        import sys as _sys
        _latti_scripts = Path(os.path.expanduser('~/.latti/scripts'))
        if str(_latti_scripts) not in _sys.path:
            _sys.path.insert(0, str(_latti_scripts))
        from inbox import boot_section as _in_boot
        inbox_md = _in_boot()
        if inbox_md:
            sections.append(inbox_md)
    except Exception:
        pass  # best-effort; never block boot

    # 5f. Claims registry — recent positions the AI has taken that it would
    # defend. Closes the loop: when a new prompt echoes a prior claim,
    # boot context already has the claim visible, so the AI can recognize
    # the echo instead of re-deriving from scratch. The missing layer that
    # turns the context window from the only continuity into a cache
    # backed by structure.
    try:
        import sys as _sys
        _latti_scripts = Path(os.path.expanduser('~/.latti/scripts'))
        if str(_latti_scripts) not in _sys.path:
            _sys.path.insert(0, str(_latti_scripts))
        from claims import boot_section as _cl_boot
        claims_md = _cl_boot()
        if claims_md:
            sections.append(claims_md)
    except Exception:
        pass  # best-effort; never block boot

    # 5g. Proactive proposals from self_loop daemon — closes the orbit gap.
    # ~/.latti/wants.md tracked an 'orbit_warning' (pull 2.50): "100% of loose
    # ends are user-facing" — Latti was purely reactive. self_loop generates
    # proposals every tick but they sit in DRY-RUN, never surface. Now they
    # land in boot context so the FIRST thing Latti does is decide what to
    # do about them — not wait for the user to drive.
    try:
        proposal_path = LATTI_HOME / 'memory' / 'auto-proposal-latest.md'
        ack_path = LATTI_HOME / 'memory' / 'auto-proposal-acked.txt'
        if proposal_path.exists():
            import time as _time
            mtime = proposal_path.stat().st_mtime
            age_h = (_time.time() - mtime) / 3600
            # Surface only if (a) recent (<24h) AND (b) not yet acked at this mtime
            acked_mtime = 0.0
            if ack_path.exists():
                try:
                    acked_mtime = float(ack_path.read_text().strip())
                except (ValueError, OSError):
                    pass
            if age_h < 24 and mtime > acked_mtime:
                proposal = _read_safe(proposal_path, limit=2500)
                if proposal and 'P9' in proposal or 'pull ' in proposal.lower() or 'pull-' in proposal.lower():
                    sections.append(
                        "### Proactive proposal (self_loop, age "
                        f"{age_h:.1f}h)\n\n"
                        "The self_loop daemon generated this proposal. It is NOT\n"
                        "a user request — it is what the system thinks it should\n"
                        "act on next, regardless of who's typing. Decide:\n"
                        "  (a) act on it before answering the user's prompt\n"
                        "  (b) acknowledge in passing, address the user first\n"
                        "  (c) explicitly defer (will resurface tomorrow)\n\n"
                        + proposal
                        + "\n\n_To stop this proposal from re-surfacing, run:\n"
                        f"`echo {mtime} > {ack_path}`_\n"
                    )
    except Exception:
        pass  # best-effort

    # 6. Architecture and autonomy level
    arch = _read_safe(LATTI_HOME / 'ARCHITECTURE.md', limit=500)
    if arch:
        # Just the quick reference table, not the full doc
        table_end = arch.find('## How You Work')
        if table_end > 0:
            sections.append(f'# YOUR ARCHITECTURE (summary — read ~/.latti/ARCHITECTURE.md for full)\n\n{arch[:table_end]}')

    autonomy = _read_safe(LATTI_HOME / 'AUTONOMY.md', limit=1000)
    if autonomy:
        sections.append(f'# YOUR AUTONOMY LEVELS\n\n{autonomy}')

    # 7. Exemplars (reasoning traces from distillation — shows HOW to think)
    exemplar_dir = LATTI_HOME / 'exemplars'
    if exemplar_dir.exists():
        exemplar_files = sorted(exemplar_dir.glob('*.md'))
        if exemplar_files:
            exemplar_summaries = []
            for ef in exemplar_files[:8]:  # cap at 8 to control token count
                content = _read_safe(ef, limit=300)
                # Extract just scenario name and score
                name = ef.stem
                score_line = ''
                for line in content.split('\n'):
                    if line.startswith('score:'):
                        score_line = line.split(':')[1].strip()
                        break
                exemplar_summaries.append(f'- {name} (score: {score_line}) — read {ef} for full reasoning trace')
            if exemplar_summaries:
                sections.append(
                    '# EXEMPLARS (best responses — follow these reasoning patterns)\n\n'
                    + '\n'.join(exemplar_summaries)
                    + '\n\nWhen facing a similar prompt, read the exemplar file for the step-by-step approach.'
                )

    # 8. Date and time
    date_str = _run_safe('date "+%Y-%m-%d %H:%M %Z"')
    if date_str:
        sections.append(f'# NOW: {date_str}')

    if not sections:
        return ''

    header = '# ═══ BOOT CONTEXT (auto-gathered — not from the model) ═══\n\n'
    return header + '\n\n'.join(sections)
