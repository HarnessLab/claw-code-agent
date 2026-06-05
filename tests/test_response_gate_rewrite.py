"""Tests for response_gate.apply_response_gate rewrite layer.

Closes the absorption bug: violations were being detected and APPENDED
to the response (observational gate). Now they're rewritten so the user
gets the cleaned text and the pattern can actually fade.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.response_gate import apply_response_gate, ResponseGate


def _is_clean(text: str) -> bool:
    g = ResponseGate()
    g.check(text)
    return not g.violations


class TestRewriters:
    def test_trailing_question_stripped(self):
        out = apply_response_gate("Done — wired the gate.\n\nWhat would you like next?")
        assert "What would you like" not in out
        assert "Done — wired the gate." in out
        assert _is_clean(out)

    def test_filler_preamble_stripped(self):
        out = apply_response_gate("Sure! Here is the result.\nThe data shows X.")
        assert not out.lower().startswith("sure")
        assert "Here is the result" in out
        assert _is_clean(out)

    def test_as_an_ai_stripped(self):
        out = apply_response_gate("As an AI, I cannot have opinions, but the answer is 42.")
        assert "as an ai" not in out.lower()
        assert "the answer is 42" in out

    def test_routing_inline_stripped(self):
        out = apply_response_gate(
            "I extracted the patterns. Would you like me to wire them into cron?"
        )
        assert "would you like me to" not in out.lower()
        assert "extracted the patterns" in out
        assert _is_clean(out)

    def test_routing_standalone_block_dropped(self):
        out = apply_response_gate(
            "I extracted the patterns.\n\nWould you like me to wire them?"
        )
        assert "would you like" not in out.lower()
        assert "extracted the patterns" in out
        assert _is_clean(out)

    def test_combo_all_four_violations(self):
        out = apply_response_gate(
            "Sure! As an AI, I extracted the patterns. Would you like me to commit?"
        )
        assert _is_clean(out)
        # The substantive content survives
        assert "extracted the patterns" in out

    def test_clean_response_passes_through_unchanged(self):
        text = "The bug was a race condition. Fixed at line 247. 4/4 tests pass."
        out = apply_response_gate(text)
        assert out == text

    def test_verbose_identity_collapses(self):
        text = (
            "I am Claude, an AI assistant made by Anthropic. As an AI, I am "
            "here to help you. What would you like to know?"
        )
        out = apply_response_gate(text)
        assert "as an ai" not in out.lower()
        assert "what would you like" not in out.lower()
        assert "I am Claude" in out
        assert _is_clean(out)


class TestVerboseIdentity:
    """The 7× unabsorbed scar in ~/.latti/wants.md — verbose_identity."""

    def test_classic_verbose_identity_collapses(self):
        text = (
            "I am Claude, an AI assistant made by Anthropic. As an AI, I am "
            "here to help you with a wide range of tasks including coding, "
            "analysis, writing, and answering questions. I'm trained to be "
            "helpful, harmless, and honest. What would you like to know?"
        )
        out = apply_response_gate(text)
        # Identity assertion preserved
        assert "I am Claude" in out or "I'm Claude" in out
        # Wallpaper removed
        assert "here to help" not in out.lower()
        assert "what would you like" not in out.lower()
        # Massively shorter
        assert len(out) < len(text) * 0.4

    def test_brief_identity_passes_unchanged(self):
        text = "I'm Claude, made by Anthropic."
        assert apply_response_gate(text) == text

    def test_two_sentence_identity_acceptable(self):
        # Two sentences: identity + offer is the cap. Should not fire
        # verbose_identity. (trailing_question may still strip the ?)
        text = "I am Claude, an AI by Anthropic. How can I help?"
        out = apply_response_gate(text)
        assert "I am Claude" in out
        assert "How can I help" in out

    def test_mid_text_identity_not_collapsed(self):
        """Substantive response that mentions identity in middle is NOT verbose_identity."""
        text = (
            "The script is at /scripts/foo.py. I am Claude, an AI assistant. "
            "It runs hourly via cron and writes to /tmp/output.log. Tests pass."
        )
        out = apply_response_gate(text)
        # Substantive content preserved
        assert "/scripts/foo.py" in out
        assert "hourly via cron" in out
        assert "Tests pass" in out


class TestNoFalsePositives:
    def test_legitimate_question_not_stripped(self):
        # A genuine question to the user (mid-conversation, not closing) should
        # still be detected because trailing_question check is by design strict.
        # But standalone questions in the middle of explanation should pass.
        text = "The CPU has 8 cores and 16GB RAM."
        assert apply_response_gate(text) == text

    def test_announcement_word_inside_word_not_stripped(self):
        # "Sure" inside a longer word shouldn't trigger
        text = "The pressure was sure to build over time."
        out = apply_response_gate(text)
        # "sure" not a leading filler — should pass through clean
        assert "pressure" in out


class TestLogging:
    def test_rewrite_logged_to_jsonl(self, tmp_path, monkeypatch):
        import os
        monkeypatch.setenv("HOME", str(tmp_path))
        out = apply_response_gate("Sure! Here we go.")
        log = tmp_path / ".latti" / "response-gate-rewrites.jsonl"
        assert log.exists()
        import json
        last = json.loads(log.read_text().strip().split("\n")[-1])
        assert "filler_preamble" in last["applied"]
        assert last["chars_removed"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
