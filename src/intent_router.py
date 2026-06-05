"""
Intent Router — Pre-Cognitive Layer.

Classifies the incoming prompt into a task type and produces an IntentManifest
that configures the Gauntlet's scoring weights for that task.

No LLM call. No fake geometry. Real heuristics that run in <1ms.

Task taxonomy:
  CODE_GEN      — write new code from scratch
  REFACTOR      — restructure existing code
  DEBUG         — find/fix a bug
  EXPLAIN       — explain code or concept
  CYCLIC        — schedule, rotation, wrap-around, modular arithmetic
  COMBINATORIAL — permutations, combinations, search over discrete space
  HIERARCHICAL  — tree, graph, recursive structure
  CONSTRAINT    — satisfy a set of rules/constraints (good Z3 target)
  GENERAL       — everything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskType(Enum):
    CODE_GEN      = "code_gen"
    REFACTOR      = "refactor"
    DEBUG         = "debug"
    EXPLAIN       = "explain"
    CYCLIC        = "cyclic"
    COMBINATORIAL = "combinatorial"
    HIERARCHICAL  = "hierarchical"
    CONSTRAINT    = "constraint"
    GENERAL       = "general"


@dataclass
class IntentManifest:
    """
    The 'physics' for this task cycle.

    gauntlet_weights: how much each validation wall contributes to energy G.
      Higher weight = that wall matters more for this task type.
      G = sum(weight_i * fail_i) where fail_i ∈ {0, 1, partial}

    z3_enabled: whether to attempt Z3 constraint extraction on this task.
      Only meaningful for CONSTRAINT and CYCLIC tasks.

    temperature: suggested sampling temperature for the Forge.
      Creative tasks → higher. Constraint tasks → lower.

    k_candidates: how many candidates to generate.
    """
    task_type: TaskType
    gauntlet_weights: dict[str, float]
    z3_enabled: bool
    temperature: float
    k_candidates: int
    rationale: str

    # Optional: extracted constraint hints for Z3
    constraint_hints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Keyword patterns per task type
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[TaskType, list[str]]] = [
    (TaskType.CYCLIC, [
        r'\bschedule\b', r'\brotation\b', r'\bwrap\b', r'\bcircular\b',
        r'\bmodulo\b', r'\bmod\b', r'\bcycle\b', r'\bweekly\b', r'\bdaily\b',
        r'\bmonday\b', r'\bsunday\b', r'\bday of week\b', r'\bshift\b',
        r'\bround.?robin\b', r'\bperiodic\b', r'\brecurring\b',
    ]),
    (TaskType.COMBINATORIAL, [
        r'\bpermutation', r'\bcombination', r'\bsubset\b', r'\bbacktrack\b',
        r'\bbrute.?force\b', r'\ball possible\b', r'\bgenerate all\b',
        r'\bn.?choose.?k\b', r'\bbinomial\b', r'\bknapsack\b', r'\btsp\b',
        r'\btraveling salesman\b',
    ]),
    (TaskType.HIERARCHICAL, [
        r'\btree\b', r'\bgraph\b', r'\brecursive\b', r'\brecursion\b',
        r'\bparent\b.*\bchild\b', r'\bnode\b', r'\bdepth.?first\b',
        r'\bbreadth.?first\b', r'\bbfs\b', r'\bdfs\b', r'\btraversal\b',
        r'\bhierarch\b',
    ]),
    (TaskType.CONSTRAINT, [
        r'\bconstraint\b', r'\bsatisf\b', r'\bmust\b.*\bnot\b',
        r'\bcannot\b', r'\bforbid\b', r'\brequire\b', r'\bvalidat\b',
        r'\bensure\b.*\balways\b', r'\binvariant\b', r'\bprecondition\b',
        r'\bpostcondition\b', r'\bprove\b', r'\bverif\b',
    ]),
    (TaskType.DEBUG, [
        r'\bbug\b', r'\bfix\b', r'\berror\b', r'\bfail\b', r'\bcrash\b',
        r'\bexception\b', r'\btraceback\b', r'\bwrong output\b',
        r'\bnot working\b', r'\bbroken\b', r'\bdebug\b', r'\bissue\b',
    ]),
    (TaskType.REFACTOR, [
        r'\brefactor\b', r'\bclean up\b', r'\bimprove\b', r'\boptimize\b',
        r'\bsimplify\b', r'\brewrite\b', r'\brestructure\b', r'\bextract\b',
        r'\bdecouple\b', r'\bmodularize\b',
    ]),
    (TaskType.EXPLAIN, [
        r'\bexplain\b', r'\bwhat is\b', r'\bhow does\b', r'\bwhy does\b',
        r'\bdescribe\b', r'\bwhat does\b', r'\bunderstand\b', r'\bmeaning\b',
        r'\bdocument\b', r'\bcomment\b',
    ]),
    (TaskType.CODE_GEN, [
        r'\bwrite\b', r'\bcreate\b', r'\bbuild\b', r'\bimplement\b',
        r'\bgenerate\b', r'\bmake\b', r'\badd\b.*\bfunction\b',
        r'\badd\b.*\bclass\b', r'\bnew\b.*\bmodule\b',
    ]),
]

# Gauntlet weight profiles per task type
# Keys: "syntax", "lint", "intent", "z3"
_WEIGHT_PROFILES: dict[TaskType, dict[str, float]] = {
    TaskType.CODE_GEN:      {"syntax": 1.0, "lint": 0.8, "intent": 1.2, "z3": 0.0},
    TaskType.REFACTOR:      {"syntax": 1.0, "lint": 1.2, "intent": 1.0, "z3": 0.0},
    TaskType.DEBUG:         {"syntax": 1.0, "lint": 0.6, "intent": 1.5, "z3": 0.0},
    TaskType.EXPLAIN:       {"syntax": 0.2, "lint": 0.1, "intent": 2.0, "z3": 0.0},
    TaskType.CYCLIC:        {"syntax": 1.0, "lint": 0.8, "intent": 1.0, "z3": 1.5},
    TaskType.COMBINATORIAL: {"syntax": 1.0, "lint": 0.8, "intent": 1.0, "z3": 1.2},
    TaskType.HIERARCHICAL:  {"syntax": 1.0, "lint": 0.8, "intent": 1.2, "z3": 0.5},
    TaskType.CONSTRAINT:    {"syntax": 1.0, "lint": 0.6, "intent": 0.8, "z3": 2.0},
    TaskType.GENERAL:       {"syntax": 1.0, "lint": 0.8, "intent": 1.0, "z3": 0.0},
}

_TEMPERATURE_MAP: dict[TaskType, float] = {
    TaskType.CODE_GEN:      0.7,
    TaskType.REFACTOR:      0.5,
    TaskType.DEBUG:         0.3,
    TaskType.EXPLAIN:       0.6,
    TaskType.CYCLIC:        0.4,
    TaskType.COMBINATORIAL: 0.4,
    TaskType.HIERARCHICAL:  0.5,
    TaskType.CONSTRAINT:    0.2,
    TaskType.GENERAL:       0.6,
}

_K_MAP: dict[TaskType, int] = {
    TaskType.CODE_GEN:      4,
    TaskType.REFACTOR:      3,
    TaskType.DEBUG:         4,
    TaskType.EXPLAIN:       2,
    TaskType.CYCLIC:        4,
    TaskType.COMBINATORIAL: 4,
    TaskType.HIERARCHICAL:  3,
    TaskType.CONSTRAINT:    6,  # constraint tasks benefit most from diversity
    TaskType.GENERAL:       3,
}


def _extract_constraint_hints(prompt: str) -> list[str]:
    """
    Extract natural-language constraint statements that Z3 might be able to
    formalize. Returns a list of hint strings.

    These are passed to the Z3 wall in the Gauntlet as context.
    """
    hints = []
    # Look for "X must/cannot/should/always/never Y" patterns
    patterns = [
        r'[A-Za-z_]\w*\s+(?:must|cannot|should|always|never|is always|is never)\s+[^.]+',
        r'(?:if|when)\s+[^,]+,\s+(?:then\s+)?[^.]+',
        r'[A-Za-z_]\w*\s+(?:>=|<=|>|<|==|!=)\s+\d+',
        r'(?:sum|total|count)\s+(?:of\s+)?[^.]+\s+(?:must|should|equals?)\s+[^.]+',
    ]
    for pat in patterns:
        for m in re.finditer(pat, prompt, re.IGNORECASE):
            hint = m.group(0).strip()
            if len(hint) > 10 and hint not in hints:
                hints.append(hint)
    return hints[:8]  # cap at 8 hints


def classify(prompt: str) -> IntentManifest:
    """
    Classify a prompt and return an IntentManifest.

    Scoring: each matching pattern adds 1 point to that task type's score.
    The task type with the highest score wins. Ties go to the earlier entry
    in _PATTERNS (more specific types are listed first).
    """
    prompt_lower = prompt.lower()
    scores: dict[TaskType, int] = {t: 0 for t, _ in _PATTERNS}
    scores[TaskType.GENERAL] = 0

    for task_type, patterns in _PATTERNS:
        for pat in patterns:
            if re.search(pat, prompt_lower):
                scores[task_type] += 1

    # Pick winner
    winner = max(scores, key=lambda t: scores[t])
    if scores[winner] == 0:
        winner = TaskType.GENERAL

    weights = _WEIGHT_PROFILES[winner]
    z3_enabled = weights["z3"] > 0.0
    constraint_hints = _extract_constraint_hints(prompt) if z3_enabled else []

    rationale_parts = []
    for task_type, patterns in _PATTERNS:
        if scores[task_type] > 0:
            rationale_parts.append(f"{task_type.value}={scores[task_type]}")

    return IntentManifest(
        task_type=winner,
        gauntlet_weights=weights,
        z3_enabled=z3_enabled,
        temperature=_TEMPERATURE_MAP[winner],
        k_candidates=_K_MAP[winner],
        rationale=f"scores: {', '.join(rationale_parts) or 'none'} → {winner.value}",
        constraint_hints=constraint_hints,
    )
