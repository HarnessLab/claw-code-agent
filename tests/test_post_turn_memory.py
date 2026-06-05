"""Post-turn memory decision in the agent-chat loop.

Latti's chat loop ran a memory check after each turn that would EXIT the
session (return 75) whenever safe RAM dropped below LATTI_MIN_SAFE_MB.
With a default threshold of 1000 MB and a typical machine reporting
~190 MB of safe RAM, every interactive session ended after the first
turn — perceived by the user as 'latti auto kills after one query'.

The fix: skip the optional post-turn hooks (voice TTS, self-sculpt) under
pressure — which is what the LATTI_LOW_MEM branch already does — and let
the chat loop continue. Jetsam-protection no longer requires terminating
the session.
"""
from __future__ import annotations

from src import main as _main


def test_normal_memory_continues_normally():
    action = _main._post_turn_memory_action(
        safe_mb=2000,
        threshold_mb=200,
        already_low_mem=False,
    )
    assert action == 'continue'


def test_low_memory_skips_hooks_not_exits():
    # 190 MB under a 200 MB threshold — the exact scenario where the old
    # code returned 75. New behavior must skip hooks and let the loop run.
    action = _main._post_turn_memory_action(
        safe_mb=190,
        threshold_mb=200,
        already_low_mem=False,
    )
    assert action == 'skip_hooks'


def test_already_low_mem_skips_hooks():
    # If the wrapper already promoted the session to low-mem mode at boot,
    # we always skip the optional hooks regardless of current safe memory.
    action = _main._post_turn_memory_action(
        safe_mb=5000,
        threshold_mb=200,
        already_low_mem=True,
    )
    assert action == 'skip_hooks'


def test_at_threshold_continues():
    # Boundary: equal to threshold is NOT considered pressure — only strictly
    # below triggers hook-skip. Avoids flapping at the edge.
    action = _main._post_turn_memory_action(
        safe_mb=200,
        threshold_mb=200,
        already_low_mem=False,
    )
    assert action == 'continue'


def test_action_returns_only_known_strings():
    for safe in (10, 100, 200, 1000, 5000):
        for already in (False, True):
            action = _main._post_turn_memory_action(
                safe_mb=safe,
                threshold_mb=200,
                already_low_mem=already,
            )
            assert action in {'continue', 'skip_hooks'}
