"""Tests for the orbit-gap fix in latti_boot.py.

When ~/.latti/memory/auto-proposal-latest.md exists and is recent and
unacked, gather_boot_context() must include it under 'Proactive proposal'.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def tmp_latti(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path.parent))
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_recent_unacked_proposal_surfaces(tmp_latti):
    """Recent proposal with no ack file must appear in boot context."""
    proposal = tmp_latti / "memory" / "auto-proposal-latest.md"
    proposal.write_text(
        "# Auto-Proposal — test\n\n"
        "**Mode:** DRY-RUN  \n"
        "**Trigger:** inbox top priority P9 · wants top pull 0.00\n\n"
        "## What the system would do\n\nP9 inbox needs attention.\n"
    )

    # Reload latti_boot with new env
    import importlib
    from src import latti_boot
    importlib.reload(latti_boot)
    ctx = latti_boot.gather_boot_context()

    assert "Proactive proposal" in ctx
    assert "self_loop" in ctx
    assert "Decide" in ctx


def test_acked_proposal_does_not_surface(tmp_latti):
    """Proposal with ack file at matching mtime must NOT surface."""
    import time
    proposal = tmp_latti / "memory" / "auto-proposal-latest.md"
    proposal.write_text("# Auto-Proposal\n\nP9 trigger\n")
    mtime = proposal.stat().st_mtime
    (tmp_latti / "memory" / "auto-proposal-acked.txt").write_text(str(mtime + 1))

    import importlib
    from src import latti_boot
    importlib.reload(latti_boot)
    ctx = latti_boot.gather_boot_context()

    assert "Proactive proposal" not in ctx


def test_old_proposal_does_not_surface(tmp_latti):
    """Proposal older than 24h must NOT surface."""
    import time
    proposal = tmp_latti / "memory" / "auto-proposal-latest.md"
    proposal.write_text("# Auto-Proposal\n\nP9 trigger\n")
    # Backdate 25h
    old = time.time() - 25 * 3600
    os.utime(proposal, (old, old))

    import importlib
    from src import latti_boot
    importlib.reload(latti_boot)
    ctx = latti_boot.gather_boot_context()

    assert "Proactive proposal" not in ctx


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
