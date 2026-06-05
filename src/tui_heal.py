"""TUI healing engine — self-repairing terminal layout for Latti.

Four-layer defense against layout corruption:

  Layer 1 — SIGWINCH flag       set on terminal resize; main loop calls
                                 heal() on next turn. Handler does NOT
                                 write to stdout — avoids racing with
                                 in-flight content writes.
  Layer 2 — Output sanitizer    strip layout-busting escape sequences from
                                 tool output BEFORE it reaches the terminal
  Layer 3 — Cursor guard        at prompt entry, if cursor drifted into
                                 footer rows, pull it back silently
  Layer 5 — heal()              full recovery callable from anywhere:
                                 scroll region + clear footer + redraw + cursor

(The old Layer 4 watchdog thread was removed 2026-04-28 — it raced with
content writes and caused the "flash and vanish" corruption it was meant to
heal.)

Wire-up (in main.py, after tui.banner()):
    from . import tui_heal
    tui_heal.install()

Every turn, before prompt():
    if tui_heal.sigwinch_pending():
        tui_heal.heal()
    tui_heal.cursor_guard()

Teardown (before tui.cleanup()):
    tui_heal.uninstall()

Sanitize tool output before display:
    summary = tui_heal.sanitize(raw_tool_output)
    _tui.tool_result(name, summary)

Manual recovery (e.g. after a crash recovery path):
    tui_heal.heal()
"""

from __future__ import annotations

import re
import signal
import sys
import shutil
from typing import Optional


# ---------------------------------------------------------------------------
# Constants — keep in sync with tui._FOOTER_LINES
# ---------------------------------------------------------------------------

_FOOTER_LINES = 5


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_installed = False
_prev_sigwinch: object = None  # previous SIGWINCH handler
_sigwinch_pending = False       # set by handler, serviced from main thread


# ---------------------------------------------------------------------------
# Layer 1 — SIGWINCH handler
# ---------------------------------------------------------------------------

def _on_sigwinch(signum: int, frame: object) -> None:  # noqa: ARG001
    """Terminal was resized.

    Signal handlers run in the main thread but can interrupt ANY Python
    bytecode — including the middle of a _w() write or a StreamRenderer
    token. Writing ANSI sequences from here would race with in-flight writes
    and corrupt cursor state.

    Instead we just flip a flag and force _ensure_scroll_region to re-pin
    the region next time it's called. The next _draw_footer() (from the
    main render loop) will redraw to the new terminal size.
    """
    global _sigwinch_pending
    _sigwinch_pending = True
    try:
        from . import tui as _tui
        # Flipping _last_rows=0 is a single integer assignment — atomic,
        # safe from a handler. It just hints the next _ensure_scroll_region
        # call to re-issue DECSTBM for the new dimensions.
        _tui._last_rows = 0
    except Exception:
        pass  # never crash the signal handler


def sigwinch_pending() -> bool:
    """Main loop checkpoint: True if a resize happened since last check.

    Callers should redraw the footer when this returns True.
    """
    global _sigwinch_pending
    pending = _sigwinch_pending
    _sigwinch_pending = False
    return pending


# ---------------------------------------------------------------------------
# Layer 2 — Output sanitizer
# ---------------------------------------------------------------------------

# Sequences that can corrupt the TUI layout.  We strip these from any text
# that originates outside Latti (tool output, subprocess stdout, etc.) before
# it is written to the terminal.
#
# KEEP: SGR color/style codes  (\033[…m)
# STRIP:
#   CSI sequences that are NOT SGR:  \033[…{letter} where letter != 'm'
#     — this catches: cursor movement, scroll region set (\033[…r),
#       erase-screen (\033[2J), cursor-home (\033[H), etc.
#   OSC sequences:  \033]…ST  or  \033]…BEL
#   DCS sequences:  \033P…ST
#   SS2/SS3:        \033N  \033O
#   RIS (full reset): \033c
#   Soft reset:     \033[!p
#   Reverse index:  \033M
#   DEC save/restore cursor: \0337 \0338  (only safe from our own code)
#   Alt-screen:     \033[?1049h  \033[?1049l  \033[?47h  \033[?47l

# Matches CSI sequences that are NOT plain SGR (\033[{digits;…}m)
_RE_CSI_NON_SGR = re.compile(
    r'\033\['            # CSI intro
    r'[\x30-\x3f]*'     # parameter bytes (0-9 ; < = > ?)
    r'[\x20-\x2f]*'     # intermediate bytes
    r'[A-LN-Za-ln-z]'   # final byte — anything except 'm' (SGR)
    r'|\033\[[\x30-\x3f]*[\x20-\x2f]*m'  # also: SGR but containing '!' = soft-reset \033[!p handled below
)

# We want to KEEP plain SGR and strip everything else.
# Rebuild: match CSI, keep only if it ends in 'm' AND has no intermediate '!'.
_RE_CSI_DANGEROUS = re.compile(
    r'\033\['
    r'(?!'              # negative lookahead: don't match plain SGR
    r'[\d;]*m'          # \033[{digits;…}m  — safe color code
    r')'
    r'[^\x00-\x1f]*?'  # any params
    r'[\x40-\x7e]'     # final byte
)

# OSC:  \033]{anything}(\033\\ | \007)
_RE_OSC = re.compile(r'\033\][^\x07\x1b]*(?:\x07|\x1b\\)')

# DCS:  \033P{anything}ST
_RE_DCS = re.compile(r'\033P[^\x1b]*\x1b\\')

# Standalone single-char escapes we strip
_RE_SINGLE = re.compile(
    r'\033[cMNO78]'     # RIS, RI, SS2, SS3, DEC save/restore cursor
    r'|\033\[!p'        # soft reset
    r'|\033\[\?(?:1049|47)[hl]'  # alt-screen
)

# Carriage-return-only (no newline) can cause overwrite on same line
# — leave them, they're common in progress bars and harmless.


def sanitize(text: str) -> str:
    """Strip layout-busting escape sequences from external (tool) output.

    Safe SGR color codes are preserved so tool output retains any ANSI
    colours it emits.  Cursor movement, screen-clear, scroll-region-set,
    terminal-reset and alt-screen sequences are removed.

    Args:
        text: Raw string from tool output / subprocess stdout.

    Returns:
        Sanitized string safe to write into the TUI content area.
    """
    if not text or '\033' not in text:
        return text

    # Order matters: strip multi-char patterns first, then single-char.
    text = _RE_OSC.sub('', text)
    text = _RE_DCS.sub('', text)
    text = _RE_SINGLE.sub('', text)
    text = _RE_CSI_DANGEROUS.sub('', text)
    return text


# ---------------------------------------------------------------------------
# Layer 3 — Cursor guard  (called after content write batches)
# ---------------------------------------------------------------------------

def cursor_guard() -> None:
    """If cursor has drifted into footer rows, silently pull it back.

    Uses CPR (cursor position report) to read the actual cursor row.
    Safe to call only when stdin is NOT in raw mode (i.e. not inside
    _read_multiline).  Skips silently if the terminal doesn't respond
    within 50 ms.
    """
    # CPR is expensive (round-trip through kernel) and risky during streaming.
    # We skip it by default and rely on the watchdog blind-redraw instead.
    # This function is kept as an explicit hook for callers that know
    # they're between turns (e.g. prompt() entry).
    try:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write('\033[6n')
            sys.stdout.flush()
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return
            resp = ''
            while True:
                ch = sys.stdin.read(1)
                resp += ch
                if ch == 'R':
                    break
                if len(resp) > 20:
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        # Parse \033[{row};{col}R
        m = re.search(r'\033\[(\d+);(\d+)R', resp)
        if not m:
            return
        row = int(m.group(1))
        r = _rows()
        content_bottom = r - _FOOTER_LINES
        if row > content_bottom:
            # Cursor is in footer rows — move it back
            sys.stdout.write(f'\033[{content_bottom};1H')
            sys.stdout.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Layer 4 — Watchdog (removed 2026-04-28)
#
# Previous implementation ran a daemon thread that blindly redrew the footer
# every 2 s. It caused: (1) a race with main-thread content writes, (2)
# DECSTBM mid-stream teleporting cursor to row 1, (3) the "flash and vanish"
# corruption pattern that motivated the whole healing engine. SIGWINCH (Layer
# 1, deferred via flag) and explicit heal() (Layer 5) cover every case the
# watchdog was meant to catch.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Layer 5 — heal()  full manual recovery
# ---------------------------------------------------------------------------

def heal() -> None:
    """Full layout recovery.

    Sequence:
      1. Re-establish scroll region for current terminal dimensions.
      2. Erase the 4 footer rows (in case they contain garbled content).
      3. Redraw footer (divider / prompt / divider / status).
      4. Move cursor to bottom of content area.

    Safe to call at any point between turns.  Do NOT call during streaming
    or while stdin is in raw mode.
    """
    try:
        from . import tui as _tui
        r = _rows()
        content_bottom = r - _FOOTER_LINES

        # Step 1: re-establish scroll region
        _tui._last_rows = 0
        _tui._ensure_scroll_region()

        # Step 2: erase footer rows
        sys.stdout.write(f'\033[{r - 3};1H\033[J')
        sys.stdout.flush()

        # Step 3: redraw footer
        _tui._draw_footer()

        # Step 4: cursor to content area
        sys.stdout.write(f'\033[{content_bottom};1H')
        sys.stdout.flush()
    except Exception as exc:
        try:
            from . import tui as _tui
            _tui._log_swallowed('tui_heal.heal', exc)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install() -> None:
    """Install all healing layers.  Call once after tui.banner()."""
    global _installed, _prev_sigwinch

    if _installed:
        return

    # Layer 1: SIGWINCH — just sets a flag; main loop services it.
    try:
        _prev_sigwinch = signal.signal(signal.SIGWINCH, _on_sigwinch)
    except (OSError, ValueError):
        # Not available on all platforms / not a TTY
        _prev_sigwinch = None

    _installed = True


def uninstall() -> None:
    """Remove all healing layers.  Call before tui.cleanup()."""
    global _installed, _prev_sigwinch

    if not _installed:
        return

    # Restore SIGWINCH
    try:
        if _prev_sigwinch is not None:
            signal.signal(signal.SIGWINCH, _prev_sigwinch)
        else:
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)
    except (OSError, ValueError):
        pass
    _prev_sigwinch = None

    _installed = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows() -> int:
    try:
        return shutil.get_terminal_size().lines
    except Exception:
        return 24
