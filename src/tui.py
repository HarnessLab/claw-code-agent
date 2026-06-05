"""Terminal UI — pi-style dark-green aesthetic for Latti.

Layout:
- Content scrolls in upper region (scroll region)
- Footer pinned at bottom: divider │ prompt │ divider │ status (2 lines)

The ONLY cursor manipulation is in _draw_footer() and prompt().
Content functions (streaming, tools, info) just write to stdout.
The scroll region handles the rest.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import sys
import termios
import tty

# ---------------------------------------------------------------------------
# ANSI — dark-green palette matching pi TUI
# ---------------------------------------------------------------------------

RESET      = '\033[0m'
BOLD       = '\033[1m'
DIM        = '\033[2m'
ITALIC     = '\033[3m'

# Greens
G_BRIGHT   = '\033[38;5;82m'   # bright green  — commands, highlights
G_MID      = '\033[38;5;71m'   # mid green     — tool labels
G_DIM      = '\033[38;5;28m'   # dark green     — subtle accents

# Text
WHITE      = '\033[38;5;255m'  # response body
GRAY       = '\033[38;5;245m'  # secondary info
DARK_GRAY  = '\033[38;5;240m'  # dividers, dims
OFF_WHITE  = '\033[38;5;252m'  # user input echo

# Accents
YELLOW     = '\033[38;5;220m'  # inline code
CYAN       = '\033[38;5;117m'  # bold spans
RED        = '\033[38;5;203m'  # errors
ORANGE     = '\033[38;5;214m'  # warnings / thinking

# Backgrounds
BG_USER    = '\033[48;5;22m'   # dark green bg for user message band
BG_TOOL    = '\033[48;5;235m'  # very dark bg for tool header

# Keep legacy aliases so external callers don't break
BLUE       = '\033[38;5;75m'
GREEN      = G_BRIGHT
MAGENTA    = '\033[38;5;176m'

# Footer height: top-divider + prompt-row + bottom-divider + status1 + status2 = 5 lines
_FOOTER_LINES = 5


# Pre-compiled once — used by status builders on every footer redraw.
# Strips SGR color codes so we can measure visible width before rendering.
_RE_STRIP_ANSI = re.compile(r'\033\[[^m]*m')


def _truncate_visible(text: str, max_visible: int, suffix: str = '…') -> str:
    """Truncate to max_visible printable chars, preserving ANSI SGR spans.

    Unlike text[:n] which could slice mid-escape and leak color, this walks
    the string counting visible chars and copies escape sequences whole.
    Always appends RESET after the suffix so nothing leaks into the next
    write.
    """
    if not text:
        return text
    out: list[str] = []
    visible = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\033' and i + 1 < n and text[i + 1] == '[':
            # Copy the whole SGR sequence (up to 'm') without counting it.
            j = i + 2
            while j < n and text[j] != 'm':
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if visible >= max_visible:
            out.append(suffix)
            out.append(RESET)
            break
        out.append(ch)
        visible += 1
        i += 1
    return ''.join(out)

# Lazy-imported once at module load time — avoids a per-tool-call import inside
# tool_result / tool_error. Set to None if tui_heal isn't available.
try:
    from .tui_heal import sanitize as _sanitize
except Exception:
    _sanitize = None  # type: ignore[assignment]


def _tui_error_log_path() -> str:
    """Where _log_swallowed appends entries.

    Override with CLAW_TUI_ERROR_LOG. Defaults under XDG_CACHE_HOME (or
    ~/.cache) so the agent has a stable local log even outside latti.
    """
    override = os.environ.get('CLAW_TUI_ERROR_LOG')
    if override:
        return override
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'claw-code-agent', 'tui-errors.log')


def _log_swallowed(where: str, exc: BaseException) -> None:
    """Best-effort log for swallowed exceptions in TUI render/heal paths.

    Constitutional rule 4: never silently swallow errors. The TUI deliberately
    swallows exceptions from sanitize/heal so a render bug never crashes the
    agent loop, but the swallow must still leave a debuggable trail.

    Never raises. Writing to the log file failing is itself swallowed —
    logging must never crash the TUI it is trying to instrument.
    """
    try:
        import time
        import traceback
        path = _tui_error_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            fh.write(f'[{ts}] {where}: {type(exc).__name__}: {exc}\n')
            fh.write(traceback.format_exc())
            fh.write('\n')
    except Exception:
        pass


def _w(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def _wb(s: str) -> None:
    """Buffered write — no flush. For batched writes inside a single render pass.

    Callers MUST call sys.stdout.flush() at the end of the render.
    Using this instead of _w() inside _draw_footer cuts 7 flushes to 1.
    """
    sys.stdout.write(s)


def _cols() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _rows() -> int:
    try:
        return shutil.get_terminal_size().lines
    except Exception:
        return 24


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_state = {
    'model':        os.environ.get('OPENAI_MODEL', 'unknown'),
    'cwd':          '~',
    'context_pct':  0,
    'permissions':  'full access',
    'total_tokens': 0,
    'turn_count':   0,
    'cost_usd':     0.0,
    'branch':       '',
    'session_id':   '',
}

_active    = False
_last_rows: int = 0


def _ensure_scroll_region() -> None:
    """(Re-)set the scroll region to the content area.

    Called at every footer draw and at prompt entry so that terminal resize
    or any escape sequence that resets the scroll region never corrupts the
    layout.  Safe to call when the region is already correct.
    """
    global _last_rows, _active
    r = _rows()
    if r != _last_rows or not _active:
        _w(f'\033[1;{r - _FOOTER_LINES}r')
        _last_rows = r
        _active = True


def set_state(
    *,
    model:        str   = '',
    cwd:          str   = '',
    context_pct:  int   = -1,
    permissions:  str   = '',
    total_tokens: int   = -1,
    turn_count:   int   = -1,
    cost_usd:     float = -1.0,
    branch:       str   = '',
    session_id:   str   = '',
) -> None:
    if model:
        _state['model'] = model
    if cwd:
        home = os.path.expanduser('~')
        _state['cwd'] = cwd.replace(home, '~') if cwd.startswith(home) else cwd
    if context_pct >= 0:
        _state['context_pct'] = context_pct
    if permissions:
        _state['permissions'] = permissions
    if total_tokens >= 0:
        _state['total_tokens'] = total_tokens
    if turn_count >= 0:
        _state['turn_count'] = turn_count
    if cost_usd >= 0:
        _state['cost_usd'] = cost_usd
    if branch:
        _state['branch'] = branch
    if session_id:
        _state['session_id'] = session_id


# ---------------------------------------------------------------------------
# Footer rendering — 5 lines pinned at bottom
#
#  row r-4: ── divider ────────────────────────────────────────────────────
#  row r-3: ❯  {prompt text or cursor}
#  row r-2: ── divider ────────────────────────────────────────────────────
#  row r-1: status line 1  — project │ branch │ session │ turns
#  row r:   status line 2  — model │ context bar │ cost │ tokens
# ---------------------------------------------------------------------------

def _fmt_tokens(tok: int | None) -> str:
    if not tok or tok < 0:
        return '0'
    if tok >= 1_000_000:
        return f'{tok / 1_000_000:.1f}M'
    if tok >= 1_000:
        return f'{tok / 1_000:.1f}k'
    return str(tok)


def _build_status1() -> str:
    """Top status line: project path │ branch │ session."""
    c = _cols()
    cwd    = _state['cwd']
    branch = _state['branch']
    sess   = _state['session_id'][:8] if _state['session_id'] else ''

    parts = [f'  {G_BRIGHT}{cwd}{RESET}']
    if branch:
        parts.append(f'{DARK_GRAY}({G_MID}{branch}{DARK_GRAY}){RESET}')
    if sess:
        parts.append(f'{DARK_GRAY}sess:{GRAY}{sess}{RESET}')
    line = f'  {DARK_GRAY}│{RESET} '.join(parts)
    plain = _RE_STRIP_ANSI.sub('', line)
    if len(plain) > c:
        line = f'  {G_BRIGHT}{cwd}{RESET}'
    return line


def _build_status2() -> str:
    """Bottom status line: model │ context bar │ cost │ tokens │ turn N."""
    c      = _cols()
    model  = _state['model']
    short  = model.split('/')[-1] if '/' in model else model
    pct    = _state['context_pct']
    filled = max(0, min(10, pct // 10))
    bar    = f'{G_BRIGHT}{"█" * filled}{DARK_GRAY}{"░" * (10 - filled)}{RESET}'
    tok    = _fmt_tokens(_state['total_tokens'])
    cost   = _state['cost_usd'] or 0.0
    cost_s = f'${cost:.4f}' if cost > 0.001 else '$0.00'
    turn   = _state['turn_count']

    # Build plain-text version first for length check, then apply colour
    plain_core = f'  {short}  {" " * 10}  {pct}%  |  {cost_s}  |  {tok} tokens  |  turn {turn}'
    if len(plain_core) > c:
        # Shorten model name — keep at least 4 chars
        overflow = len(plain_core) - c
        new_len = max(4, len(short) - overflow)
        short = short[:new_len]

    line = (f'  {G_MID}{short}{RESET}  {bar}  {GRAY}{pct}%{RESET}'
            f'  {DARK_GRAY}│{RESET}  {GRAY}{cost_s}{RESET}'
            f'  {DARK_GRAY}│{RESET}  {GRAY}{tok} tokens'
            f'  {DARK_GRAY}│{RESET}  {DARK_GRAY}turn {GRAY}{turn}{RESET}')

    # Safe truncation: strip at plain-text boundary, not ANSI byte position
    plain = _RE_STRIP_ANSI.sub('', line)
    if len(plain) > c:
        # Rebuild without turn (least important)
        line = (f'  {G_MID}{short}{RESET}  {bar}  {GRAY}{pct}%{RESET}'
                f'  {DARK_GRAY}│{RESET}  {GRAY}{cost_s}{RESET}'
                f'  {DARK_GRAY}│{RESET}  {GRAY}{tok} tokens{RESET}')
    return line


def _draw_footer(prompt_text: str = '') -> None:
    """Draw the 5-line footer at absolute row positions.

    Uses DEC save/restore (ESC 7 / ESC 8) to preserve the calling cursor
    position so content flows continuously without gaps between turns.

    Safe now because:
    - _ensure_scroll_region() is never called from content functions
      (no DECSTBM mid-stream that would teleport cursor to row 1)
    - Watchdog thread is disabled (no threading race on cursor position)
    - Scroll region bounds prevent cursor going below content_bottom
      during normal content writes

    Batches all writes into a single string + one flush (was 7 flushes).
    """
    _ensure_scroll_region()
    r = _rows()
    c = _cols()
    div   = f'{DARK_GRAY}{"─" * c}{RESET}'
    stat1 = _build_status1()
    stat2 = _build_status2()

    if prompt_text:
        prompt_row = f'\033[{r-3};1H\033[2K{DARK_GRAY}  {prompt_text}{RESET}'
    else:
        prompt_row = f'\033[{r-3};1H\033[2K{G_BRIGHT}{BOLD}❯  {WHITE}'

    # Single batched write — one syscall, one flush.
    sys.stdout.write(
        '\0337'                                    # DEC save cursor
        f'\033[{r-4};1H\033[2K{div}'
        f'{prompt_row}'
        f'\033[{r-2};1H\033[2K{div}'
        f'\033[{r-1};1H\033[2K{stat1}'
        f'\033[{r};1H\033[2K{stat2}'
        '\0338'                                    # DEC restore cursor
    )
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------

def banner() -> None:
    """Clear screen, set scroll region, draw footer, print banner."""
    global _active, _last_rows
    r = _rows()
    _w('\033[2J\033[H')
    _w(f'\033[1;{r - _FOOTER_LINES}r')
    _active    = True
    _last_rows = r
    _draw_footer()
    # _draw_footer lands cursor at content_bottom — move back to top so
    # banner text and boot info flow from row 1 downward.
    _w('\033[1;1H')
    _w(f'\n{G_BRIGHT}{BOLD}  ◆ Latti{RESET}{GRAY}  — lattice mind{RESET}\n')
    _w(f'{DARK_GRAY}  {"─" * 40}{RESET}\n\n')


def cleanup() -> None:
    """Restore terminal on exit."""
    global _active, _last_rows
    if _active:
        r = _rows()
        _w(f'\033[{r - (_FOOTER_LINES - 1)};1H\033[J')
        _w(f'\033[1;{r}r')
        _w(f'\033[{r};1H\n')
        _active    = False
        _last_rows = 0


def status_footer() -> None:
    """Redraw footer with current state. Called after each turn."""
    _draw_footer()  # _draw_footer already calls _ensure_scroll_region internally


# ---------------------------------------------------------------------------
# Prompt — cursor moves to footer, then back to content area
# ---------------------------------------------------------------------------

_PASTE_TIMEOUT = 0.08


def _read_multiline() -> str:
    """Read one user message, handling multi-line paste correctly."""
    fd           = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    lines: list[str] = []
    current: list[str] = []

    def _flush_line() -> str:
        line = ''.join(current)
        current.clear()
        return line

    def _update_prompt_indicator(n_lines: int) -> None:
        r = _rows()
        if n_lines > 0:
            indicator = (
                f'{G_BRIGHT}{BOLD}❯  {RESET}{CYAN}'
                f'[{n_lines} line{"s" if n_lines != 1 else ""}'
                f' — blank line or Ctrl+D to send]{WHITE}'
            )
        else:
            indicator = f'{G_BRIGHT}{BOLD}❯  {WHITE}'
        _w(f'\033[{r-3};1H\033[2K{indicator}')

    try:
        tty.setraw(fd)

        while True:
            timeout = _PASTE_TIMEOUT if lines else None
            ready, _, _ = select.select([sys.stdin], [], [], timeout)

            if not ready:
                continue

            ch = sys.stdin.read(1)

            if ch == '\x03':
                raise KeyboardInterrupt
            if ch == '\x04':
                if not current and not lines:
                    raise EOFError
                if current:
                    lines.append(_flush_line())
                break

            if ch in ('\r', '\n'):
                line = _flush_line()
                if lines:
                    if line == '':
                        break
                    else:
                        lines.append(line)
                        _update_prompt_indicator(len(lines))
                else:
                    ready2, _, _ = select.select([sys.stdin], [], [], _PASTE_TIMEOUT)
                    if ready2:
                        lines.append(line)
                        _update_prompt_indicator(len(lines))
                    else:
                        lines.append(line)
                        break
                continue

            if ch in ('\x7f', '\x08'):
                if current:
                    current.pop()
                    _w('\b \b')
                continue

            # Arrow keys and other escape sequences — swallow silently.
            # Raw mode sends multi-byte sequences for arrow keys, function
            # keys, Ctrl/Alt combos, bracketed paste markers, etc. Printing
            # any of it would emit literal '[A' / '[200~' into the prompt.
            #
            # Sequences have variable length:
            #   \x1b[A                  (3 bytes, arrow)
            #   \x1b[1;5D               (6 bytes, Ctrl+Arrow)
            #   \x1b[200~ ... \x1b[201~ (bracketed paste)
            #
            # Strategy: read the second byte (\x1b[ = CSI, \x1bO = SS3, or
            # standalone ESC). Then read parameter bytes (\x30-\x3f) +
            # intermediate bytes (\x20-\x2f) + one final byte (\x40-\x7e).
            # Bail after 32 chars or a 50 ms idle gap to avoid hangs.
            if ch == '\x1b':
                try:
                    ready_e, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if not ready_e:
                        continue  # bare ESC keypress — discard
                    introducer = sys.stdin.read(1)
                    if introducer not in ('[', 'O'):
                        continue  # unknown — discard introducer + ESC
                    # Read until we see a final byte or we time out.
                    for _ in range(32):
                        ready_e2, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if not ready_e2:
                            break
                        b = sys.stdin.read(1)
                        # Final byte of a CSI/SS3 sequence is 0x40-0x7e.
                        if '\x40' <= b <= '\x7e':
                            # For bracketed paste start (\x1b[200~) we'd
                            # need to keep reading until \x1b[201~. We
                            # don't support bracketed paste yet; just drop.
                            break
                except Exception:
                    pass
                continue  # discard entire escape sequence

            current.append(ch)
            _w(ch)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return '\n'.join(lines)


def prompt() -> str:
    """Draw prompt in footer, get input, return cursor to content area."""
    _ensure_scroll_region()
    r            = _rows()
    content_bottom = r - _FOOTER_LINES

    _w(f'\033[{r-3};1H\033[2K{G_BRIGHT}{BOLD}❯  {WHITE}')

    try:
        user_input = _read_multiline()
    except (EOFError, KeyboardInterrupt):
        _w(f'\033[{content_bottom};1H')
        _w(f'\n{GRAY}  goodbye{RESET}\n')
        raise

    summary = user_input.replace('\n', ' ↵ ')
    if len(summary) > 80:
        summary = summary[:77] + '…'
    # Move cursor BACK into the content area before drawing footer.
    # _draw_footer uses DEC save/restore (ESC 7/8); if cursor is left at r-3
    # (where the user was typing in the footer prompt row), then save happens
    # at r-3 — and after restore, subsequent user_message() / stream writes
    # land inside the footer rows, where the next _draw_footer() overwrites
    # them. That's the "prompt and answer appear then disappear" bug.
    # Parking cursor at content_bottom ensures DEC restore returns cursor
    # inside the scroll region, so the next writes flow safely into content.
    _w(f'\033[{content_bottom};1H')
    _draw_footer(prompt_text=f'{DARK_GRAY}{summary}{RESET}')
    return user_input


# ---------------------------------------------------------------------------
# User message echo — pi-style: subtle ❯ prefix, no background band
# ---------------------------------------------------------------------------

def user_message(text: str) -> None:
    """Echo the user's message pi-style: dim ❯ prefix, no background fill."""
    first, *rest = text.split('\n') if '\n' in text else [text]
    _w(f'\n{DARK_GRAY}  ❯ {GRAY}{first}{RESET}\n')
    for line in rest:
        _w(f'{DARK_GRAY}    {GRAY}{line}{RESET}\n')


# ---------------------------------------------------------------------------
# Streaming — writes to content area, no cursor manipulation
# ---------------------------------------------------------------------------

class StreamRenderer:
    def __init__(self) -> None:
        self._in_bold        = False
        self._in_code_inline = False
        self._in_code_block  = False
        self._line_start     = True
        self._pending        = ''

    def start(self) -> None:
        # Reset parse state so the same renderer can be re-used across turns
        # without carrying a half-open bold/code/code-block span from a
        # previous stream.
        self._in_bold        = False
        self._in_code_inline = False
        self._in_code_block  = False
        self._pending        = ''
        self._line_start     = True
        _w(f'\n{WHITE}')

    def token(self, text: str) -> None:
        text = self._pending + text
        self._pending = ''
        i = 0
        while i < len(text):
            ch = text[i]

            if self._line_start and text[i:i+3] == '```':
                nl = text.find('\n', i + 3)
                if nl == -1:
                    self._pending = text[i:]
                    return
                if not self._in_code_block:
                    lang = text[i+3:nl].strip()
                    self._in_code_block = True
                    _w('\n')
                    if lang:
                        _w(f'{DARK_GRAY}  {DIM}{CYAN}{lang}{RESET}\n')
                else:
                    self._in_code_block = False
                    _w(f'{RESET}\n{WHITE}')
                i = nl + 1
                self._line_start = True
                continue

            if self._in_code_block:
                nl = text.find('\n', i)
                if nl == -1:
                    _w(f'{G_BRIGHT}{text[i:]}{RESET}')
                    return
                _w(f'{G_BRIGHT}    {text[i:nl]}{RESET}\n')
                i = nl + 1
                self._line_start = True
                continue

            if text[i:i+2] == '**':
                if self._in_bold:
                    _w(RESET + WHITE)
                    self._in_bold = False
                else:
                    _w(BOLD + CYAN)
                    self._in_bold = True
                i += 2
                continue

            if ch == '`' and not self._in_code_block:
                if self._in_code_inline:
                    _w(RESET + WHITE)
                    self._in_code_inline = False
                else:
                    _w(YELLOW)
                    self._in_code_inline = True
                i += 1
                continue

            if self._line_start and ch == '#':
                nl = text.find('\n', i)
                if nl == -1:
                    self._pending = text[i:]
                    return
                line = text[i:nl].lstrip('#').strip()
                _w(f'{BOLD}{G_BRIGHT}{line}{RESET}\n{WHITE}')
                i = nl + 1
                self._line_start = True
                continue

            if ch == '\n':
                _w('\n')
                i += 1
                self._line_start = True
                continue

            if self._line_start:
                _w('  ')
                self._line_start = False

            _w(ch)
            i += 1

    def end(self) -> None:
        # Flush any pending partial token (e.g. a lone '#' that hadn't found
        # its newline yet, or the opening '```' of an unterminated code fence).
        if self._pending:
            _w(self._pending)
            self._pending = ''
        # Close any open span so the terminal returns to default color.
        # Without this, a stream that terminates mid-bold or inside a code
        # block leaks color into whatever gets rendered next (tool bands,
        # user echo, the footer).
        if self._in_bold or self._in_code_inline or self._in_code_block:
            _w(RESET)
            self._in_bold = False
            self._in_code_inline = False
            self._in_code_block = False
        _w(f'{RESET}\n')


# ---------------------------------------------------------------------------
# Tool calls — pi-style: $ command header + truncated output + separator
# ---------------------------------------------------------------------------

# Track lines seen per tool call for the expand hint
_tool_line_counts: dict[str, int] = {}


def tool_start(name: str, detail: str = '') -> None:
    """pi-style tool header: icon + bold label + dim command. No background band."""
    icon  = _tool_icon(name)
    label = _tool_label(name)
    cmd   = detail or ''
    max_cmd = max(10, _cols() - len(label) - 12)
    if cmd:
        cmd = _truncate_visible(cmd, max_cmd)
    cmd_part = f' {DARK_GRAY}{cmd}{RESET}' if cmd else ''
    _w(f'\n{G_MID}{BOLD}  {icon} {label}{RESET}{cmd_part}\n')


def tool_result(name: str, summary: str) -> None:
    """Output line + pi-style separator with inline metadata."""
    if _sanitize is not None:
        try:
            summary = _sanitize(summary)
        except Exception as exc:
            _log_swallowed('tui.tool_result.sanitize', exc)

    # Count lines for expand hint
    n_lines = summary.count('\n') + 1
    _tool_line_counts[name] = n_lines

    # Show first line of output. _truncate_visible preserves ANSI SGR spans
    # so we never slice mid-escape and leak color.
    first = summary.split('\n', 1)[0]
    first = _truncate_visible(first, 117)

    _w(f'{DARK_GRAY}  ⎿ {GRAY}{first}{RESET}\n')

    # Truncation hint if multi-line (pi-style)
    if n_lines > 1:
        _w(f'{DARK_GRAY}  … ({n_lines - 1} more line{"s" if n_lines > 2 else ""}, not shown){RESET}\n')

    # Thin separator — use \033[K so it never wraps on narrow terminals
    _w(f'{DARK_GRAY}  {"─" * (_cols() - 2)}{RESET}\n')


def tool_error(name: str, error: str) -> None:
    if _sanitize is not None:
        try:
            error = _sanitize(error)
        except Exception as exc:
            _log_swallowed('tui.tool_error.sanitize', exc)
    _w(f'{RED}  ⎿ {_truncate_visible(error, 120)}{RESET}\n')
    _w(f'{DARK_GRAY}  {"─" * (_cols() - 2)}{RESET}\n')


def _tool_icon(name: str) -> str:
    return {
        'read_file':      '📄',
        'write_file':     '✏️',
        'edit_file':      '✏️',
        'bash':           '⚡',
        'glob_search':    '🔍',
        'grep_search':    '🔍',
        'list_dir':       '📁',
        'lattice_solve':  '◆',
        'lattice_boolean_solve': '◆',
        'web_fetch':      '🌐',
        'web_search':     '🌐',
        'delegate_agent': '🤖',
        'self_score':     '📊',
    }.get(name, '⏺')


def _tool_label(name: str) -> str:
    return {
        'read_file':      'Read',
        'write_file':     'Write',
        'edit_file':      'Edit',
        'bash':           'Bash',
        'glob_search':    'Glob',
        'grep_search':    'Grep',
        'list_dir':       'List',
        'lattice_solve':  'Lattice',
        'lattice_boolean_solve': 'Lattice Bool',
        'web_fetch':      'Fetch',
        'web_search':     'Search',
        'delegate_agent': 'Agent',
        'self_score':     'Score',
    }.get(name, name)


# ---------------------------------------------------------------------------
# Info / markers
# ---------------------------------------------------------------------------

def info(text: str) -> None:
    _w(f'{DARK_GRAY}  {GRAY}{text}{RESET}\n')

def divider() -> None:
    c = _cols()
    _w(f'{DARK_GRAY}{"─" * c}{RESET}\n')

def done_marker() -> None:
    _w('\n')  # single blank line between response and next prompt

def thinking_start() -> None:
    pass  # silent — no Working… indicator

def thinking_clear() -> None:
    pass

def thinking_block(thinking_text: str, token_count: int = 0) -> None:
    pass  # silent — extended thinking not displayed in TUI

def scar_match(scar_id: str, lesson: str, model: str) -> None:
    _w(f'\n{G_MID}[scar]{RESET} {GRAY}{scar_id}{RESET}\n')
    _w(f'{DARK_GRAY}  lesson:{RESET} {GRAY}{lesson}{RESET}\n')
    _w(f'{DARK_GRAY}  model: {RESET} {G_BRIGHT}{model}{RESET}\n')
    sys.stdout.flush()
