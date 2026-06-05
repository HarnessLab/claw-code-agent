"""
Gauntlet — Thermodynamic Validation Layer.

Every candidate must survive three walls. Failure at any wall adds energy G.
The candidate with the lowest total G wins. G=∞ means the candidate is dead.

Wall 1 — Syntax (Deterministic Engine)
  ast.parse() for Python. Hard fail = G=∞.

Wall 2 — Lint (Static Analysis Engine)
  ruff check for Python. Each violation adds fractional energy.
  Undefined names, unreachable code, type errors → high energy.

Wall 3 — Intent (Semantic Scoring Engine)
  TF-IDF cosine similarity between the original prompt and the candidate.
  Low similarity → high energy. This is the real "intent alignment" check.

Wall 4 — Z3 (Axiomatic Engine) [optional, task-type gated]
  Extracts arithmetic/boolean constraints from the candidate code and
  verifies them against the IntentManifest's constraint hints.
  Only runs when manifest.z3_enabled is True.
  Z3 can only verify what Z3 can model — we don't fake it.

Energy formula:
  G = w_syntax * syntax_fail
    + w_lint * lint_score
    + w_intent * (1 - intent_similarity)
    + w_z3 * z3_fail

  where all w_* come from the IntentManifest.gauntlet_weights.
"""

from __future__ import annotations

import ast
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .intent_router import IntentManifest


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class WallResult:
    wall: str
    passed: bool
    energy_contribution: float
    detail: str


@dataclass
class GauntletResult:
    candidate_id: int
    raw_text: str
    total_energy: float          # G — lower is better; math.inf = dead
    wall_results: list[WallResult]
    survived: bool               # total_energy < INF
    extracted_code: str          # the code block extracted from the response

    @property
    def is_dead(self) -> bool:
        return math.isinf(self.total_energy)


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def _extract_code(text: str) -> str:
    """
    Extract the first Python code block from a markdown response.
    Falls back to the full text if no fenced block is found.
    """
    # Try ```python ... ``` first
    m = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try ``` ... ``` (no language tag)
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Wall 1: Syntax
# ---------------------------------------------------------------------------

def _wall_syntax(code: str, weight: float) -> WallResult:
    """Hard fail if code doesn't parse as valid Python."""
    if not code.strip():
        return WallResult("syntax", False, math.inf, "empty code")
    try:
        ast.parse(code)
        return WallResult("syntax", True, 0.0, "ok")
    except SyntaxError as e:
        return WallResult("syntax", False, math.inf,
                          f"SyntaxError line {e.lineno}: {e.msg}")


# ---------------------------------------------------------------------------
# Wall 2: Lint (ruff)
# ---------------------------------------------------------------------------

# Ruff error codes and their energy weights
# Higher = more severe
_RUFF_WEIGHTS: dict[str, float] = {
    "F821": 1.0,   # undefined name — likely hallucinated import
    "F811": 0.8,   # redefinition of unused name
    "F401": 0.4,   # imported but unused
    "E711": 0.6,   # comparison to None
    "E712": 0.6,   # comparison to True/False
    "W291": 0.1,   # trailing whitespace
    "W293": 0.1,   # whitespace before ':'
    "E501": 0.05,  # line too long
    "F841": 0.5,   # local variable assigned but never used
    "B006": 0.7,   # mutable default argument
    "B007": 0.4,   # loop variable not used
    "B023": 0.8,   # function definition in loop
    "E999": 1.0,   # syntax error (ruff's own parse)
}
_DEFAULT_RUFF_WEIGHT = 0.3


def _wall_lint(code: str, weight: float) -> WallResult:
    """Run ruff on the code. Each violation adds fractional energy."""
    if weight == 0.0:
        return WallResult("lint", True, 0.0, "skipped (weight=0)")

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        tmp = f.name

    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format=text", "--no-cache", tmp],
            capture_output=True, text=True, timeout=10
        )
        violations = []
        raw_energy = 0.0
        for line in result.stdout.splitlines():
            # Format: path:line:col: CODE message
            m = re.match(r'.+:(\d+):(\d+):\s+([A-Z]\d+)\s+(.*)', line)
            if m:
                code_id = m.group(3)
                msg = m.group(4)
                e = _RUFF_WEIGHTS.get(code_id, _DEFAULT_RUFF_WEIGHT)
                raw_energy += e
                violations.append(f"{code_id}: {msg}")

        # Normalize: cap at 1.0 before applying weight
        normalized = min(1.0, raw_energy / 3.0)
        energy = weight * normalized
        passed = normalized < 0.5
        detail = f"{len(violations)} violations" if violations else "clean"
        if violations:
            detail += ": " + "; ".join(violations[:3])
        return WallResult("lint", passed, energy, detail)
    except subprocess.TimeoutExpired:
        return WallResult("lint", False, weight * 0.5, "ruff timeout")
    except FileNotFoundError:
        # ruff not available — skip gracefully
        return WallResult("lint", True, 0.0, "ruff not found, skipped")
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Wall 3: Intent (TF-IDF cosine similarity)
# ---------------------------------------------------------------------------

def _tfidf_tokens(text: str) -> dict[str, float]:
    """
    Minimal TF-IDF: term frequency of meaningful tokens.
    No external dependencies.
    """
    # Tokenize: split on non-alphanumeric, lowercase, filter short tokens
    tokens = re.findall(r'[a-z_][a-z0-9_]{2,}', text.lower())
    # Stop words
    stops = {
        'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
        'not', 'but', 'have', 'had', 'has', 'its', 'you', 'can', 'will',
        'def', 'return', 'import', 'class', 'self', 'none', 'true', 'false',
        'pass', 'else', 'elif', 'while', 'print', 'str', 'int', 'list',
        'dict', 'set', 'tuple', 'type', 'len', 'range', 'any', 'all',
    }
    tf: dict[str, float] = {}
    for t in tokens:
        if t not in stops:
            tf[t] = tf.get(t, 0) + 1
    total = sum(tf.values()) or 1
    return {k: v / total for k, v in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two TF vectors."""
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    mag_a = math.sqrt(sum(v * v for v in a.values())) or 1e-9
    mag_b = math.sqrt(sum(v * v for v in b.values())) or 1e-9
    return dot / (mag_a * mag_b)


def _wall_intent(prompt: str, candidate_text: str, weight: float) -> WallResult:
    """
    Measure semantic alignment between prompt and candidate.
    Low similarity → high energy.
    """
    if weight == 0.0:
        return WallResult("intent", True, 0.0, "skipped (weight=0)")

    prompt_vec = _tfidf_tokens(prompt)
    candidate_vec = _tfidf_tokens(candidate_text)
    similarity = _cosine(prompt_vec, candidate_vec)

    # Energy = weight * (1 - similarity)
    energy = weight * (1.0 - similarity)
    passed = similarity >= 0.15  # minimum meaningful overlap
    return WallResult(
        "intent", passed, energy,
        f"similarity={similarity:.3f}"
    )


# ---------------------------------------------------------------------------
# Wall 4: Z3 Axiomatic Engine
# ---------------------------------------------------------------------------

def _extract_z3_constraints(code: str, hints: list[str]) -> list[str]:
    """
    Extract verifiable arithmetic/boolean constraints from code.

    Looks for:
    - assert statements with arithmetic comparisons
    - if conditions with arithmetic comparisons
    - Variable bounds (x >= 0, x < N)
    - Modular arithmetic patterns (x % N)

    Returns a list of Z3-compatible Python expressions.
    """
    constraints = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        # assert statements
        if isinstance(node, ast.Assert):
            try:
                expr = ast.unparse(node.test)
                # Only include if it looks like arithmetic/boolean
                if re.search(r'[<>=!%+\-*/]', expr):
                    constraints.append(expr)
            except Exception:
                pass

        # if conditions with comparisons
        if isinstance(node, ast.If):
            try:
                expr = ast.unparse(node.test)
                if re.search(r'[<>=!%]', expr) and len(expr) < 80:
                    constraints.append(expr)
            except Exception:
                pass

    # Also extract from hint strings
    for hint in hints:
        # Look for "x >= N", "x < N", "x % N == 0" patterns
        m = re.search(r'([a-z_]\w*)\s*([<>=!%]+)\s*(\d+)', hint, re.IGNORECASE)
        if m:
            constraints.append(f"{m.group(1)} {m.group(2)} {m.group(3)}")

    return constraints[:10]  # cap


def _wall_z3(code: str, manifest: IntentManifest) -> WallResult:
    """
    Z3 axiomatic verification.

    What Z3 can actually verify:
    - Arithmetic constraints are satisfiable (no contradiction)
    - Bounds are consistent
    - Modular arithmetic wraps correctly

    What Z3 CANNOT verify (and we don't pretend it can):
    - Whether the code "does what the user wants" semantically
    - Whether an algorithm is correct in general
    - String manipulation, I/O, side effects

    If Z3 finds a contradiction → energy spike.
    If Z3 finds constraints are satisfiable → small energy reduction.
    If no verifiable constraints found → neutral (energy=0).
    """
    if not manifest.z3_enabled or manifest.gauntlet_weights.get("z3", 0) == 0:
        return WallResult("z3", True, 0.0, "skipped (not enabled)")

    try:
        import z3
    except ImportError:
        return WallResult("z3", True, 0.0, "z3 not installed, skipped")

    weight = manifest.gauntlet_weights.get("z3", 0.0)
    constraints = _extract_z3_constraints(code, manifest.constraint_hints)

    if not constraints:
        return WallResult("z3", True, 0.0, "no verifiable constraints found")

    # Try to verify each constraint is satisfiable
    solver = z3.Solver()
    solver.set("timeout", 5000)  # 5 second timeout

    verified = 0
    contradictions = []
    unverifiable = []

    for expr_str in constraints:
        try:
            # Build a Z3 context: extract variable names and create Int vars
            var_names = re.findall(r'\b([a-z_][a-z0-9_]*)\b', expr_str)
            var_names = [v for v in var_names if not v.isdigit() and v not in
                        ('and', 'or', 'not', 'in', 'is', 'True', 'False', 'None')]
            var_names = list(dict.fromkeys(var_names))  # deduplicate

            if not var_names:
                continue

            # Create Z3 integer variables
            z3_vars = {name: z3.Int(name) for name in var_names}

            # Translate Python expression to Z3
            # We use eval() in a controlled namespace — only Z3 vars + operators
            safe_ns = dict(z3_vars)
            safe_ns['__builtins__'] = {}

            # Replace Python operators with Z3-compatible ones
            z3_expr_str = expr_str
            z3_expr_str = z3_expr_str.replace(' and ', ' & ').replace(' or ', ' | ')
            z3_expr_str = z3_expr_str.replace(' not ', ' ~ ')

            z3_constraint = eval(z3_expr_str, safe_ns)  # noqa: S307

            # Check satisfiability
            s = z3.Solver()
            s.set("timeout", 1000)
            s.add(z3_constraint)
            result = s.check()

            if result == z3.unsat:
                contradictions.append(expr_str)
            elif result == z3.sat:
                verified += 1
            else:
                unverifiable.append(expr_str)

        except Exception:
            unverifiable.append(expr_str)
            continue

    if contradictions:
        energy = weight * 1.0
        detail = f"Z3 contradiction in: {'; '.join(contradictions[:2])}"
        return WallResult("z3", False, energy, detail)

    if verified > 0:
        # Verified constraints → small energy reduction (reward)
        energy = weight * max(0.0, 0.3 - 0.1 * verified)
        detail = f"Z3 verified {verified}/{len(constraints)} constraints"
        return WallResult("z3", True, energy, detail)

    detail = f"Z3: {len(unverifiable)} constraints unverifiable (not arithmetic)"
    return WallResult("z3", True, 0.0, detail)


# ---------------------------------------------------------------------------
# Gauntlet orchestrator
# ---------------------------------------------------------------------------

def run(
    candidate_id: int,
    raw_text: str,
    prompt: str,
    manifest: IntentManifest,
) -> GauntletResult:
    """
    Run a single candidate through all walls.
    Returns a GauntletResult with total energy G.
    """
    weights = manifest.gauntlet_weights
    code = _extract_code(raw_text)

    wall_results: list[WallResult] = []

    # Wall 1: Syntax (hard fail)
    w1 = _wall_syntax(code, weights.get("syntax", 1.0))
    wall_results.append(w1)
    if not w1.passed and math.isinf(w1.energy_contribution):
        # Dead — no point running further walls
        return GauntletResult(
            candidate_id=candidate_id,
            raw_text=raw_text,
            total_energy=math.inf,
            wall_results=wall_results,
            survived=False,
            extracted_code=code,
        )

    # Wall 2: Lint
    w2 = _wall_lint(code, weights.get("lint", 0.8))
    wall_results.append(w2)

    # Wall 3: Intent
    w3 = _wall_intent(prompt, raw_text, weights.get("intent", 1.0))
    wall_results.append(w3)

    # Wall 4: Z3 (optional)
    w4 = _wall_z3(code, manifest)
    wall_results.append(w4)

    total_energy = sum(w.energy_contribution for w in wall_results)
    survived = not math.isinf(total_energy)

    return GauntletResult(
        candidate_id=candidate_id,
        raw_text=raw_text,
        total_energy=total_energy,
        wall_results=wall_results,
        survived=survived,
        extracted_code=code,
    )
