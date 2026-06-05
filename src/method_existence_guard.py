"""Catch `self.X(...)` calls where method `X` doesn't exist anywhere in src/.

The exact failure mode this prevents:

  # commit 84bc6a7 added at agent_runtime.py:448
  self._inject_next_priority()
  # but `def _inject_next_priority` was never defined anywhere.
  # Every chat turn raised AttributeError. 134 tests had been red
  # for weeks because of it. Production crashed on first invocation.

The guard is intentionally COARSE: it does not track class boundaries,
inheritance, or mixins. It just verifies that for every `self.X(`
reference, at least ONE `def X(` exists somewhere in the source tree
under inspection. This rules out the typo / missing-stub class of bug
that has historically blocked latti.

Limitations (false negatives — by design):
  - A method defined in an unrelated class still satisfies the check.
    A future refactor could add per-class scoping; the current bug
    bar is "called but undefined ANYWHERE."
  - Methods bound via `self.X = ...` assignment are recognized
    (not flagged).
  - Dunder methods (`__init__`, `__enter__`, etc.) are exempt — they're
    inherited from object/Protocol and may not have explicit defs.

Wired as:
  - tests/test_method_existence_guard.py: pytest CI gate. Fails CI if
    any new commit introduces a missing-method call.
  - CLI: `python -m src.method_existence_guard [<src_dir>]` for
    pre-commit hook integration. Exits 1 on any missing method.

Tested by tests/test_method_existence_guard.py.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MissingCall:
    name: str
    source: str
    line: int


# Names ALWAYS skipped — inherited from object/Protocol/typing/stdlib
# base classes (ast.NodeVisitor, threading, etc.) or are special Python
# attributes accessed without explicit definition. Adding to this set is
# fine for known-stdlib bases; do NOT add latti-defined method names
# here (that would defeat the guard's purpose).
_EXEMPT_NAMES = frozenset({
    # Object protocol
    '__init__', '__new__', '__del__', '__repr__', '__str__', '__bytes__',
    '__hash__', '__bool__', '__eq__', '__ne__', '__lt__', '__le__',
    '__gt__', '__ge__', '__call__', '__getattr__', '__setattr__',
    '__delattr__', '__getattribute__', '__dir__',
    # Container protocol
    '__len__', '__contains__', '__iter__', '__next__', '__reversed__',
    '__getitem__', '__setitem__', '__delitem__',
    # Context manager
    '__enter__', '__exit__', '__aenter__', '__aexit__',
    # Class protocol
    '__class__', '__init_subclass__', '__subclasshook__',
    '__instancecheck__', '__subclasscheck__',
    # Numeric protocol
    '__add__', '__sub__', '__mul__', '__truediv__', '__floordiv__',
    '__mod__', '__pow__', '__neg__', '__pos__', '__abs__',
    '__radd__', '__rsub__', '__rmul__',
    # Async
    '__await__', '__aiter__', '__anext__',
    # Pickle / copy
    '__reduce__', '__reduce_ex__', '__copy__', '__deepcopy__',
    '__getstate__', '__setstate__',
    # Dataclass
    '__post_init__',
    # Common stdlib base classes (ast.NodeVisitor, NodeTransformer)
    'visit', 'generic_visit',
    # Common ML/torch surface (deepseek_v4_model.py uses self.parameters())
    'parameters', 'forward', 'state_dict', 'load_state_dict',
    'register_buffer', 'register_parameter',
    # Common stdlib mixin/queue/threading methods
    'put', 'get', 'task_done', 'join', 'qsize', 'empty', 'full',
    # logging.Logger inherited
    'debug', 'info', 'warning', 'error', 'critical', 'exception',
    'log', 'setLevel', 'addHandler',
})

# self.<name>( pattern. Captures the method name in group 1.
# Restricted to a word followed by `(` so attribute reads (no call)
# don't trigger.
_SELF_CALL_RE = re.compile(r'\bself\.([A-Za-z_][A-Za-z_0-9]*)\s*\(')


def _scan_one(
    text: str,
    source_name: str,
    known_defs: set[str] | None = None,
) -> list[MissingCall]:
    """Inner: take source text + file label + cross-file def set."""
    # Collect local defs (def X) from this file.
    local_defs: set[str] = set()
    # Collect names assigned via `self.X = ...` (treat as legitimate).
    self_assignments: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local_defs.add(node.name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'
                ):
                    self_assignments.add(target.attr)
        if isinstance(node, ast.AnnAssign):
            t = node.target
            if (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == 'self'
            ):
                self_assignments.add(t.attr)
        # Class-level annotations: dataclass fields (field_name: T = default)
        # are declared at the class body level, not via self.X = ...
        # When self.field_name(...) is called later, this catches it.
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    self_assignments.add(stmt.target.id)
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            self_assignments.add(target.id)

    available = local_defs | self_assignments | (known_defs or set())

    # AST-based scan eliminates false positives from regex matching
    # inside docstrings, comments, and string literals. Walks the tree
    # for Call nodes whose func is Attribute(value=Name('self'), attr=X).
    findings: list[MissingCall] = []
    seen: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == 'self'):
            continue
        name = func.attr
        if name in _EXEMPT_NAMES or name in available:
            continue
        line = getattr(node, 'lineno', 0)
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        findings.append(MissingCall(name=name, source=source_name, line=line))
    return findings


def find_missing_method_calls(
    text: str,
    *,
    source: str = '<inline>',
    known_defs: set[str] | None = None,
) -> list[MissingCall]:
    """Scan a single Python source string for self.X() calls without
    a satisfying def somewhere in the local file or known_defs set.

    Args:
      text: the Python source text to scan.
      source: filename to attribute findings to (for error messages).
      known_defs: optional set of method names defined ELSEWHERE in
        the tree. Treated as satisfying any call site even if not
        present in this file. Used by scan_source_tree to share defs
        across files.
    """
    return _scan_one(text, source, known_defs)


def _collect_defs(src_dir: Path) -> set[str]:
    """First pass: collect every `def X` name across all .py files."""
    all_defs: set[str] = set()
    for py in src_dir.rglob('*.py'):
        try:
            text = py.read_text(encoding='utf-8')
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                all_defs.add(node.name)
    return all_defs


def scan_source_tree(src_dir: Path) -> list[MissingCall]:
    """Walk src_dir, return all self.X() calls with no def X anywhere.

    Two-pass: collect every def name across the tree, then scan each
    file's self.X() references against that union. A method defined in
    one file satisfies a call from another (coarse but catches the
    "not defined anywhere" failure).
    """
    src_dir = Path(src_dir)
    if not src_dir.is_dir():
        return []
    all_defs = _collect_defs(src_dir)
    findings: list[MissingCall] = []
    for py in sorted(src_dir.rglob('*.py')):
        try:
            text = py.read_text(encoding='utf-8')
        except OSError:
            continue
        rel = str(py.relative_to(src_dir.parent))
        findings.extend(_scan_one(text, rel, known_defs=all_defs))
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry: scan src/ (or argv[1] if given), exit 1 if any missing."""
    args = argv if argv is not None else sys.argv[1:]
    target = Path(args[0]) if args else Path(__file__).resolve().parent
    missing = scan_source_tree(target)
    if not missing:
        return 0
    print(f'method-existence guard: {len(missing)} missing method call(s):',
          file=sys.stderr)
    for m in missing:
        print(f'  {m.source}:{m.line} self.{m.name}() — no def found',
              file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
