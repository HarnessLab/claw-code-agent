#!/usr/bin/env python3
"""Comprehensive TUI smoke test.

Run: python3 test_tui_smoke.py

Tests every TUI function in sequence. Watch the footer — it should stay
pinned at the bottom through all tests. The prompt should appear IN the
footer area (like Claude Code).

Press Enter when prompted to advance through interactive steps.
Ctrl-C to abort.
"""

import sys
import time
import os

sys.path.insert(0, os.path.dirname(__file__))
from src import tui


def pause(seconds: float = 1.0):
    time.sleep(seconds)


def main():
    # === SETUP ===
    tui.banner()
    tui.info('TUI smoke test starting...')
    pause(1.5)

    # === TEST 1: Footer state updates ===
    tui.info('TEST 1: Footer state updates (watch the bottom)')
    pause(0.5)

    for pct, tok, turn, cost, label in [
        (0,   0,       0, 0.0,    '0%'),
        (25,  50000,   3, 0.12,   '25%'),
        (50,  100000,  8, 0.89,   '50%'),
        (75,  1500000, 15, 5.67,  '75%'),
        (99,  199000,  50, 9.99,  '99%'),
    ]:
        tui.set_state(
            model='anthropic/claude-sonnet-4',
            cwd=os.path.expanduser('~/V5/project'),
            context_pct=pct, total_tokens=tok,
            turn_count=turn, cost_usd=cost,
        )
        tui.status_footer()
        tui.info(f'  footer updated: {label}')
        pause(0.8)

    # === TEST 2: Info + divider ===
    tui.info('TEST 2: Info and divider lines')
    tui.info('  This is an info line')
    tui.divider()
    tui.info('  Another line after divider')
    pause(1)

    # === TEST 3: Streaming markdown ===
    tui.info('TEST 3: Streaming markdown')
    renderer = tui.StreamRenderer()
    renderer.start()
    for chunk in [
        'Hello. ', 'The **kernel** ', 'is running.\n\n',
        '# A Header\n\n',
        'Inline `code` ', 'here.\n\n',
        '```python\n', 'def hello():\n', '    print("world")\n', '```\n\n',
        'And **bold across** ', 'chunks.\n',
    ]:
        renderer.token(chunk)
        time.sleep(0.04)
    renderer.end()
    pause(1)

    # === TEST 4: Tool calls ===
    tui.info('TEST 4: Tool calls')
    tui.tool_start('bash', 'curl -s http://localhost:3737/api/dashboard')
    pause(0.3)
    tui.tool_result('bash', 'exit_code=0')
    tui.tool_start('read_file', '~/project/main.py')
    pause(0.3)
    tui.tool_result('read_file', '42 lines')
    tui.tool_start('web_search', 'ANSI escape codes')
    pause(0.3)
    tui.tool_error('web_search', 'Network timeout after 30s')
    tui.tool_start('lattice_solve', 'Monte Carlo 3-layer')
    pause(0.3)
    tui.tool_result('lattice_solve', 'minimum=-0.4237 at [0.12, 0.85, 0.33]')
    pause(1)

    # === TEST 5: Thinking ===
    tui.info('TEST 5: Thinking indicator')
    tui.thinking_start()
    pause(1.5)
    tui.thinking_clear()
    tui.info('  (thinking cleared)')
    pause(0.5)

    # === TEST 6: Done marker ===
    tui.info('TEST 6: Done marker')
    tui.done_marker()
    pause(1)

    # === TEST 7: Scroll stress ===
    tui.info('TEST 7: 30-line scroll stress — footer must stay pinned')
    pause(0.5)
    for i in range(30):
        tui._w(f'{tui.WHITE}  Line {i+1:02d}: The quick brown fox jumps over the lazy dog{tui.RESET}\n')
        time.sleep(0.04)
    tui.set_state(context_pct=60, total_tokens=120000, turn_count=30, cost_usd=3.45)
    tui.status_footer()
    pause(2)

    # === TEST 8: Interactive prompt ===
    interactive = sys.stdin.isatty()
    if interactive:
        tui.info('TEST 8: Prompt (type something, press Enter)')
        tui.set_state(turn_count=31)
        tui.status_footer()
        try:
            user_input = tui.prompt()
            tui.info(f'  Captured: "{user_input}"')
        except (EOFError, KeyboardInterrupt):
            tui.info('  (prompt skipped)')
    else:
        tui.info('TEST 8: Prompt (skipped — non-interactive)')
    pause(1)

    # === TEST 9: Full turn simulation ===
    if interactive:
        tui.info('TEST 9: Full turn — type a message:')
        tui.set_state(context_pct=40, total_tokens=80000, turn_count=32, cost_usd=1.50)
        tui.status_footer()
        try:
            msg = tui.prompt()
        except (EOFError, KeyboardInterrupt):
            msg = '(skipped)'
    else:
        tui.info('TEST 9: Full turn (non-interactive — simulated)')
        msg = 'simulated input'

    tui.thinking_start()
    pause(1)
    tui.thinking_clear()

    renderer2 = tui.StreamRenderer()
    renderer2.start()
    for ch in f'You said: "{msg}". Processing...\n':
        renderer2.token(ch)
        time.sleep(0.02)
    renderer2.end()

    tui.tool_start('bash', 'echo "working"')
    pause(0.5)
    tui.tool_result('bash', 'exit_code=0')

    renderer3 = tui.StreamRenderer()
    renderer3.start()
    for ch in 'Done. All clear.\n':
        renderer3.token(ch)
        time.sleep(0.02)
    renderer3.end()

    tui.done_marker()
    tui.set_state(context_pct=45, total_tokens=90000, turn_count=33, cost_usd=1.65)
    tui.status_footer()
    pause(2)

    # === TEST 10: Rapid footer updates during content ===
    tui.info('TEST 10: Rapid content + footer updates')
    for i in range(10):
        tui._w(f'{tui.WHITE}  Rapid line {i+1}{tui.RESET}\n')
        tui.set_state(context_pct=50 + i * 5, turn_count=34 + i)
        tui.status_footer()
        time.sleep(0.2)
    pause(1)

    # === DONE ===
    tui.info('═══ ALL 10 TESTS COMPLETE ═══')
    if interactive:
        tui.info('Press Enter to exit and restore terminal...')
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    else:
        pause(1)
    tui.cleanup()
    print('\nTerminal restored. Smoke test done.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        tui.cleanup()
        print('\nAborted.')
    except Exception as e:
        tui.cleanup()
        print(f'\nError: {e}')
        raise
