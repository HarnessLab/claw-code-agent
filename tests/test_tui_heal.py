"""Tests for tui_heal — specifically the sanitizer (layer 2)."""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.tui_heal import sanitize


class SanitizerTests(unittest.TestCase):

    # --- things that MUST be stripped ---

    def test_strips_scroll_region_reset(self):
        self.assertEqual(sanitize('\033[r'), '')
        self.assertEqual(sanitize('\033[0r'), '')

    def test_strips_scroll_region_set(self):
        self.assertEqual(sanitize('\033[1;20r'), '')
        self.assertEqual(sanitize('\033[5;50r'), '')

    def test_strips_ris_full_reset(self):
        self.assertEqual(sanitize('\033c'), '')

    def test_strips_soft_reset(self):
        self.assertEqual(sanitize('\033[!p'), '')

    def test_strips_screen_clear(self):
        self.assertEqual(sanitize('\033[2J'), '')
        self.assertEqual(sanitize('\033[3J'), '')

    def test_strips_cursor_home(self):
        self.assertEqual(sanitize('\033[H'), '')
        self.assertEqual(sanitize('\033[1;1H'), '')

    def test_strips_cursor_movement(self):
        self.assertEqual(sanitize('\033[5A'), '')   # cursor up
        self.assertEqual(sanitize('\033[3B'), '')   # cursor down
        self.assertEqual(sanitize('\033[10C'), '')  # cursor right
        self.assertEqual(sanitize('\033[2D'), '')   # cursor left

    def test_strips_alt_screen(self):
        self.assertEqual(sanitize('\033[?1049h'), '')
        self.assertEqual(sanitize('\033[?1049l'), '')
        self.assertEqual(sanitize('\033[?47h'), '')
        self.assertEqual(sanitize('\033[?47l'), '')

    def test_strips_osc_title_set(self):
        self.assertEqual(sanitize('\033]0;window title\007'), '')
        self.assertEqual(sanitize('\033]2;title\033\\'), '')

    def test_strips_reverse_index(self):
        self.assertEqual(sanitize('\033M'), '')

    def test_strips_dec_save_restore(self):
        self.assertEqual(sanitize('\0337'), '')
        self.assertEqual(sanitize('\0338'), '')

    # --- things that MUST be preserved ---

    def test_keeps_plain_text(self):
        t = 'hello world'
        self.assertEqual(sanitize(t), t)

    def test_keeps_sgr_colors(self):
        self.assertEqual(sanitize('\033[0m'), '\033[0m')
        self.assertEqual(sanitize('\033[38;5;75m'), '\033[38;5;75m')
        self.assertEqual(sanitize('\033[1;32m'), '\033[1;32m')
        self.assertEqual(sanitize('\033[m'), '\033[m')

    def test_keeps_reset(self):
        self.assertEqual(sanitize('\033[0m'), '\033[0m')

    def test_no_escape_passthrough(self):
        t = 'no escape here'
        self.assertIs(sanitize(t), t)   # identity (fast path)

    # --- mixed cases ---

    def test_strips_dangerous_keeps_color_in_mixed(self):
        inp = '\033[38;5;114mgreen text\033[0m\033[2J\033[1;1H more text'
        out = sanitize(inp)
        self.assertIn('\033[38;5;114m', out)  # color kept
        self.assertIn('\033[0m', out)          # reset kept
        self.assertNotIn('\033[2J', out)       # screen clear stripped
        self.assertNotIn('\033[1;1H', out)     # cursor home stripped
        self.assertIn('green text', out)
        self.assertIn('more text', out)

    def test_bash_progress_bar_output(self):
        # Typical progress bar: \r + content — carriage return is KEPT (harmless)
        inp = '\r  50% ████░░░░  building...'
        out = sanitize(inp)
        self.assertIn('50%', out)
        self.assertIn('\r', out)

    def test_rogue_scroll_region_in_tool_output(self):
        # Tool outputs a scroll region reset mid-stream
        inp = 'line1\n\033[r\nline2'
        out = sanitize(inp)
        self.assertNotIn('\033[r', out)
        self.assertIn('line1', out)
        self.assertIn('line2', out)

    def test_empty_string(self):
        self.assertEqual(sanitize(''), '')

    def test_none_like_passthrough(self):
        # Should handle non-escape strings without crashing
        for t in ['', '   ', '\n\n', 'abc\ndef']:
            result = sanitize(t)
            self.assertIsInstance(result, str)


if __name__ == '__main__':
    unittest.main()
