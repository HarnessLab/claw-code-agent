#!/usr/bin/env python3
"""Minimal test: pinned footer with scroll region.

Run this standalone to verify the ANSI works before wiring into Latti.
Type messages — they scroll in the content area. Footer stays pinned.
Ctrl-C to exit.
"""

import shutil
import sys

def w(s):
    sys.stdout.write(s)
    sys.stdout.flush()

def rows():
    return shutil.get_terminal_size().lines

def cols():
    return shutil.get_terminal_size().columns

FOOTER_LINES = 2  # how many lines the footer uses

def draw_footer(msg=''):
    """Draw footer at bottom. Save/restore cursor."""
    r = rows()
    c = cols()
    line1 = '─' * c
    line2 = f'  model │ [~] ██░░░░░░░░ 20%  {msg}'
    # Save cursor, move to footer, draw, restore
    w(f'\0337')                          # DEC save
    w(f'\033[{r-1};1H\033[2K{line1}')    # line r-1: divider
    w(f'\033[{r};1H\033[2K{line2}')      # line r: status
    w(f'\0338')                          # DEC restore

def setup():
    """Clear screen, set scroll region, draw initial footer."""
    r = rows()
    w('\033[2J\033[H')                   # clear + home
    w(f'\033[1;{r - FOOTER_LINES}r')     # scroll region
    draw_footer('ready')
    w('\033[H')                          # cursor to top of content area

def cleanup():
    """Restore full scroll region."""
    r = rows()
    w(f'\033[1;{r}r')                    # reset scroll region
    w(f'\033[{r};1H\n')                  # cursor to bottom

def main():
    setup()
    w('Pinned footer test. Type anything — content scrolls, footer stays.\n\n')
    turn = 0
    try:
        while True:
            w('❯  ')
            line = input()
            if line.strip() in ('/quit', '/exit'):
                break
            turn += 1
            w(f'  You said: {line}\n')
            w(f'  (turn {turn})\n\n')
            draw_footer(f'turn {turn}')
    except (EOFError, KeyboardInterrupt):
        pass
    cleanup()
    print('goodbye')

if __name__ == '__main__':
    main()
