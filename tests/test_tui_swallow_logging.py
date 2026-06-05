"""Swallowed-exception logging in tui.py / tui_heal.py.

Constitutional rule 4: never silently swallow errors. The TUI render path
deliberately swallows some exceptions (a sanitizer or heal step failing
must not crash the agent loop), but the swallow must still leave a trail
so a future failure is debuggable instead of invisible.

Covered failure points:
  - tui.tool_result   — sanitizer raised
  - tui.tool_error    — sanitizer raised
  - tui_heal.heal()   — recovery itself raised
"""
from __future__ import annotations

import io
import os
import sys

import pytest


@pytest.fixture
def tui_log_path(tmp_path, monkeypatch):
    """Redirect _log_swallowed output into a temp file via env var."""
    log = tmp_path / "tui-errors.log"
    monkeypatch.setenv("CLAW_TUI_ERROR_LOG", str(log))
    return log


def _reload_tui():
    # Force a fresh import so the env var is picked up if cached.
    import importlib
    from src import tui as _tui
    importlib.reload(_tui)
    return _tui


def test_log_swallowed_writes_entry(tui_log_path):
    tui = _reload_tui()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        tui._log_swallowed("test.where", exc)
    assert tui_log_path.exists()
    content = tui_log_path.read_text()
    assert "test.where" in content
    assert "RuntimeError" in content
    assert "boom" in content


def test_log_swallowed_never_raises_on_bad_path(monkeypatch):
    monkeypatch.setenv("CLAW_TUI_ERROR_LOG", "/nonexistent/dir/that/cannot/exist/log")
    tui = _reload_tui()
    try:
        raise ValueError("v")
    except ValueError as exc:
        tui._log_swallowed("test.bad_path", exc)  # must not raise


def test_tool_result_sanitizer_failure_logs_and_continues(tui_log_path, monkeypatch):
    tui = _reload_tui()

    def boom_sanitize(_: str) -> str:
        raise RuntimeError("sanitize-failure")

    monkeypatch.setattr(tui, "_sanitize", boom_sanitize)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    tui.tool_result("read_file", "ok\nline2\nline3")

    out = buf.getvalue()
    assert "ok" in out                # render kept going with unsanitized input
    log = tui_log_path.read_text()
    assert "tool_result" in log
    assert "sanitize-failure" in log


def test_tool_error_sanitizer_failure_logs_and_continues(tui_log_path, monkeypatch):
    tui = _reload_tui()

    def boom_sanitize(_: str) -> str:
        raise RuntimeError("err-sanitize-failure")

    monkeypatch.setattr(tui, "_sanitize", boom_sanitize)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    tui.tool_error("read_file", "permission denied")

    out = buf.getvalue()
    assert "permission denied" in out
    log = tui_log_path.read_text()
    assert "tool_error" in log
    assert "err-sanitize-failure" in log


def test_heal_failure_is_logged(tui_log_path, monkeypatch):
    from src import tui_heal
    import importlib
    importlib.reload(tui_heal)

    # Force heal()'s body to raise by making _ensure_scroll_region blow up.
    from src import tui as _tui
    importlib.reload(_tui)

    def boom():
        raise RuntimeError("heal-blew-up")

    monkeypatch.setattr(_tui, "_ensure_scroll_region", boom)

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    tui_heal.heal()  # must not raise

    log = tui_log_path.read_text()
    assert "heal" in log
    assert "heal-blew-up" in log
