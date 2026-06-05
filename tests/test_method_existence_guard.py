"""Method-existence guard — catches `self.X(...)` calls without a `def X`.

Pre-fix: commit 84bc6a7 added `self._inject_next_priority()` at
agent_runtime.py:448 without ever defining the method. Every chat
turn raised AttributeError. 134 tests had been red for weeks because
of it. The diff passed unit tests (no test exercised the call site)
but production crashed on first invocation.

This guard scans Python source files for `self.<name>(` patterns and
verifies each name has at least one `def <name>(` definition
somewhere in the same source tree. Coarse — it doesn't track class
boundaries, so a method defined in an unrelated class still satisfies
the check (false negative). But it CATCHES the exact failure mode
that took down latti for weeks: a call to a method that doesn't exist
ANYWHERE.

Wired as:
  - pytest test (CI gate): runs against src/, fails on missing methods
  - CLI module (`python -m src.method_existence_guard`): git pre-commit
    hook integration
"""
from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from src.method_existence_guard import (
    find_missing_method_calls,
    scan_source_tree,
)


class TestFindMissingMethodCalls(unittest.TestCase):
    def test_method_called_and_defined_passes(self) -> None:
        src = textwrap.dedent("""\
            class A:
                def foo(self):
                    return self.bar()
                def bar(self):
                    return 1
        """)
        missing = find_missing_method_calls(src, source='inline.py')
        self.assertEqual(missing, [],
                         f'expected no missing methods; got {missing}')

    def test_method_called_but_not_defined_is_flagged(self) -> None:
        # The exact shape of the _inject_next_priority bug.
        src = textwrap.dedent("""\
            class A:
                def run(self):
                    self._inject_next_priority()
        """)
        missing = find_missing_method_calls(src, source='inline.py')
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].name, '_inject_next_priority')
        self.assertEqual(missing[0].source, 'inline.py')

    def test_method_assigned_via_setattr_is_ok(self) -> None:
        # If self.X is assigned somewhere, calling self.X() is legitimate
        # even without a `def X`. Common pattern for callbacks.
        src = textwrap.dedent("""\
            class A:
                def __init__(self):
                    self.callback = lambda: None
                def run(self):
                    self.callback()
        """)
        missing = find_missing_method_calls(src, source='inline.py')
        self.assertEqual(missing, [])

    def test_dunder_methods_are_not_flagged(self) -> None:
        # Built-ins like __init__, __enter__, __iter__ are not flagged
        # even if not explicitly defined (they're inherited from object).
        src = textwrap.dedent("""\
            class A:
                def run(self):
                    self.__class__
                    self.__init_subclass__()
        """)
        missing = find_missing_method_calls(src, source='inline.py')
        self.assertEqual(missing, [])

    def test_known_definition_in_other_module_satisfies(self) -> None:
        src_a = textwrap.dedent("""\
            class A:
                def run(self):
                    self.helper_method()
        """)
        src_b = textwrap.dedent("""\
            class B:
                def helper_method(self):
                    return 'ok'
        """)
        # Cross-file: helper_method defined in src_b satisfies a.py's call
        # (coarse but catches the missing-everywhere case).
        all_defs = {'helper_method'}
        missing = find_missing_method_calls(src_a, source='a.py', known_defs=all_defs)
        self.assertEqual(missing, [])

    def test_method_called_via_property_not_flagged(self) -> None:
        # Property-decorated methods are accessed as self.X (no parens
        # in the call). Our regex hits self.X( specifically, so property
        # access without call is invisible — not a false positive.
        src = textwrap.dedent("""\
            class A:
                @property
                def my_prop(self):
                    return 1
                def run(self):
                    return self.my_prop
        """)
        missing = find_missing_method_calls(src, source='inline.py')
        self.assertEqual(missing, [])


class TestScanSourceTree(unittest.TestCase):
    """The integration test that catches the actual src/ tree."""

    def test_src_tree_has_no_missing_method_calls(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        src_dir = repo_root / 'src'
        missing = scan_source_tree(src_dir)
        if missing:
            failures = '\n'.join(
                f'  {m.source}:{m.line} self.{m.name}() — no def found anywhere in src/'
                for m in missing
            )
            self.fail(
                f'method-existence guard found {len(missing)} call(s) to '
                f'undefined methods:\n{failures}'
            )


if __name__ == '__main__':
    unittest.main()
