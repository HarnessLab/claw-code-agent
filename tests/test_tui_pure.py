"""Pure-function tests for tui.py — no terminal I/O.

Covers helpers that are safe to exercise without a real TTY:
  - _fmt_tokens       (formatting)
  - _truncate_visible (ANSI-safe truncation)
  - StreamRenderer    (state reset across turns, mid-span termination)
  - _RE_STRIP_ANSI    (strip regex)
"""
from __future__ import annotations

import io
import sys

from src import tui


def test_fmt_tokens_regular_values() -> None:
    assert tui._fmt_tokens(0)        == '0'
    assert tui._fmt_tokens(42)       == '42'
    assert tui._fmt_tokens(999)      == '999'
    assert tui._fmt_tokens(1_000)    == '1.0k'
    assert tui._fmt_tokens(1_234)    == '1.2k'
    assert tui._fmt_tokens(999_999)  == '1000.0k'
    assert tui._fmt_tokens(1_000_000) == '1.0M'
    assert tui._fmt_tokens(12_500_000) == '12.5M'


def test_fmt_tokens_edge_cases() -> None:
    # None, negative, and zero must not crash the status line builder.
    assert tui._fmt_tokens(None) == '0'
    assert tui._fmt_tokens(-1)   == '0'
    assert tui._fmt_tokens(-999) == '0'


def test_truncate_visible_no_truncation() -> None:
    assert tui._truncate_visible('hello', 10) == 'hello'
    assert tui._truncate_visible('', 10)      == ''
    assert tui._truncate_visible('hi', 2)     == 'hi'


def test_truncate_visible_plain_truncation() -> None:
    result = tui._truncate_visible('abcdefghij', 5)
    # 5 visible chars + ellipsis suffix + RESET
    assert result.startswith('abcde')
    assert '…' in result
    assert result.endswith(tui.RESET)


def test_truncate_visible_preserves_ansi_spans() -> None:
    # Red 'abc' + plain 'defgh' with truncation at 4 visible chars.
    inp = '\033[31mabc\033[0mdefgh'
    result = tui._truncate_visible(inp, 4)
    # Should include the red-'abc' span whole, 1 more char ('d'), then ellipsis.
    assert '\033[31m' in result
    assert '\033[0m' in result
    assert 'abcd' in result.replace('\033[31m', '').replace('\033[0m', '')
    # Never slice mid-escape: no dangling '\033' or '\033[' at end.
    assert not result.endswith('\033')
    assert not result.endswith('\033[')


def test_truncate_visible_ansi_does_not_count_as_visible() -> None:
    # 10 visible chars wrapped in color — should NOT truncate.
    inp = '\033[31m' + 'x' * 10 + '\033[0m'
    result = tui._truncate_visible(inp, 10)
    # All 10 'x' preserved, no ellipsis.
    stripped = tui._RE_STRIP_ANSI.sub('', result)
    assert stripped == 'x' * 10
    assert '…' not in result


def test_strip_ansi_regex() -> None:
    colored = '\033[38;5;82mhello\033[0m world'
    assert tui._RE_STRIP_ANSI.sub('', colored) == 'hello world'
    # Plain text is unchanged
    assert tui._RE_STRIP_ANSI.sub('', 'abc') == 'abc'


def test_stream_renderer_start_resets_state(monkeypatch) -> None:
    r = tui.StreamRenderer()
    # Corrupt state (simulate a half-open span from a previous stream).
    r._in_bold = True
    r._in_code_inline = True
    r._in_code_block = True
    r._pending = 'leftover'
    r._line_start = False

    # Capture writes
    buf = io.StringIO()
    monkeypatch.setattr(sys.stdout, 'write', buf.write)
    monkeypatch.setattr(sys.stdout, 'flush', lambda: None)

    r.start()

    assert r._in_bold is False
    assert r._in_code_inline is False
    assert r._in_code_block is False
    assert r._pending == ''
    assert r._line_start is True


def test_stream_renderer_end_closes_open_spans(monkeypatch) -> None:
    r = tui.StreamRenderer()
    r._in_bold = True

    buf = io.StringIO()
    monkeypatch.setattr(sys.stdout, 'write', buf.write)
    monkeypatch.setattr(sys.stdout, 'flush', lambda: None)

    r.end()
    out = buf.getvalue()

    # After end(), all spans must be closed.
    assert r._in_bold is False
    assert r._in_code_inline is False
    assert r._in_code_block is False
    # A RESET must have been written so the next render starts clean.
    assert tui.RESET in out


def test_stream_renderer_end_closes_code_block(monkeypatch) -> None:
    r = tui.StreamRenderer()
    r._in_code_block = True

    buf = io.StringIO()
    monkeypatch.setattr(sys.stdout, 'write', buf.write)
    monkeypatch.setattr(sys.stdout, 'flush', lambda: None)

    r.end()

    # The code_block state flag must be cleared even if the stream ended
    # mid-block — otherwise the next turn would start inside a code block.
    assert r._in_code_block is False
    assert tui.RESET in buf.getvalue()


def test_stream_renderer_end_flushes_pending(monkeypatch) -> None:
    r = tui.StreamRenderer()
    r._pending = '# header-without-newline'

    buf = io.StringIO()
    monkeypatch.setattr(sys.stdout, 'write', buf.write)
    monkeypatch.setattr(sys.stdout, 'flush', lambda: None)

    r.end()

    assert '# header-without-newline' in buf.getvalue()
    assert r._pending == ''
