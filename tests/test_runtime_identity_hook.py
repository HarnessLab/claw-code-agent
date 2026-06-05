"""Test that agent_runtime spawns the identity compiler at end of run().

The compiler is invoked via subprocess.Popen (non-blocking, fire-and-forget).
Hook failure must NOT affect the run() return value.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def test_run_spawns_identity_compiler_subprocess(monkeypatch, tmp_path):
    """The hook should call subprocess.Popen on the identity_compile shim."""
    monkeypatch.setenv('LATTI_IDENTITY_COMPILE', '1')

    # Create a fake shim file so the is_file() guard passes
    shim_dir = tmp_path / 'scripts'
    shim_dir.mkdir(parents=True)
    fake_shim = shim_dir / 'identity_compile.py'
    fake_shim.write_text('# fake shim\n')

    monkeypatch.setattr('src.agent_runtime._IDENTITY_SHIM', fake_shim)

    spawn_calls = []

    def fake_popen(args, **kw):
        spawn_calls.append(args)
        m = MagicMock()
        m.pid = 99999
        return m

    with patch('src.agent_runtime.subprocess.Popen', side_effect=fake_popen):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()

    assert len(spawn_calls) == 1
    cmd = spawn_calls[0]
    assert any('identity_compile.py' in str(arg) for arg in cmd)


def test_hook_no_op_when_env_var_absent(monkeypatch, tmp_path):
    monkeypatch.delenv('LATTI_IDENTITY_COMPILE', raising=False)

    spawn_calls = []
    def fake_popen(args, **kw):
        spawn_calls.append(args)
        return MagicMock()

    with patch('src.agent_runtime.subprocess.Popen', side_effect=fake_popen):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()

    assert len(spawn_calls) == 0


def test_hook_no_op_when_shim_missing(monkeypatch, tmp_path):
    """If the substrate shim doesn't exist, hook silently no-ops."""
    monkeypatch.setenv('LATTI_IDENTITY_COMPILE', '1')
    monkeypatch.setattr('src.agent_runtime._IDENTITY_SHIM', tmp_path / 'does-not-exist.py')

    spawn_calls = []
    def fake_popen(args, **kw):
        spawn_calls.append(args)
        return MagicMock()

    with patch('src.agent_runtime.subprocess.Popen', side_effect=fake_popen):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()

    assert len(spawn_calls) == 0


def test_hook_swallows_subprocess_error(monkeypatch, tmp_path):
    """If Popen itself raises, hook must not propagate."""
    monkeypatch.setenv('LATTI_IDENTITY_COMPILE', '1')

    fake_shim = tmp_path / 'shim.py'
    fake_shim.write_text('# fake\n')
    monkeypatch.setattr('src.agent_runtime._IDENTITY_SHIM', fake_shim)

    def boom(*a, **kw):
        raise OSError('exec failed')

    with patch('src.agent_runtime.subprocess.Popen', side_effect=boom):
        from src.agent_runtime import _maybe_spawn_identity_compiler
        _maybe_spawn_identity_compiler()  # must not raise
