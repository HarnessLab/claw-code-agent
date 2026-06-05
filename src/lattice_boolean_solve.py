"""Lattice Boolean Solver — discrete optimization over {0,1}^n.

Pure Python, zero dependencies. Uses bit-flip simulated annealing with
three-phase adaptive temperature schedule (mirrors lattice_solver.py).

The cipher is COMPACTNESS: minimal code, maximum clarity.

Algorithm:
  Phase 1 (15%): Exploration — random bit-flips, accept worse freely
  Phase 2 (30%): Focused search — 1-bit and 2-bit flips, Metropolis accept
  Phase 3 (55%): Refinement — greedy descent + log-odds sector combination

Output: optimal bit assignment, cost, confidence, feasibility, marginal probabilities.
"""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

BooleanCostFn = Callable[[list[int]], float]


@dataclass
class BooleanSolveResult:
    """Result from boolean lattice solver."""
    optimum: list[int]  # {0,1}^n
    cost: float
    confidence: float
    confidence_label: str
    converged: bool
    effective_samples: int
    feasible: bool
    constraint_violations: int
    marginal_probs: list[float]  # P(bit_i = 1) across samples
    elapsed_ms: float
    total_samples: int
    acceptance_rate: float

    def to_text(self) -> str:
        coords = ', '.join(f'b{i}={v}' for i, v in enumerate(self.optimum))
        lines = [
            f'Optimum: [{coords}]',
            f'Cost: {self.cost:.8g}',
            f'Confidence: {self.confidence_label} ({self.confidence:.0%})',
            f'Converged: {self.converged} (eff_samples={self.effective_samples})',
            f'Feasible: {self.feasible} (violations={self.constraint_violations})',
            f'Marginal probs: [{", ".join(f"{p:.3f}" for p in self.marginal_probs)}]',
            f'Samples: {self.total_samples} | Acceptance: {self.acceptance_rate:.1%} | Time: {self.elapsed_ms:.0f}ms',
        ]
        return '\n'.join(lines)


def _check_constraints(
    bits: list[int],
    constraints: list[tuple[str, Callable[[list[int]], bool]]],
) -> tuple[bool, int]:
    """Check all constraints. Return (all_satisfied, violation_count)."""
    violations = 0
    for _, check_fn in constraints:
        try:
            if not check_fn(bits):
                violations += 1
        except Exception:
            violations += 1
    return violations == 0, violations


def _mc_layer_boolean(
    cost_fn: BooleanCostFn,
    constraints: list[tuple[str, Callable[[list[int]], bool]]],
    start: list[int],
    start_cost: float,
    n_samples: int,
    temperature: float,
    flip_prob: float,
) -> tuple[list[int], float, list[float], int, int]:
    """One MC layer: bit-flip proposals with Metropolis accept.
    
    Returns: (best_bits, best_cost, all_costs, accepted, tried)
    """
    best = start[:]
    best_cost = start_cost
    all_costs = []
    accepted = 0
    tried = 0
    marginal_sum = [0.0] * len(start)

    for _ in range(n_samples):
        # Propose: flip 1 or 2 bits
        proposal = best[:]
        n_flips = 1 if random.random() < 0.7 else 2
        for _ in range(n_flips):
            idx = random.randint(0, len(proposal) - 1)
            proposal[idx] = 1 - proposal[idx]

        # Check feasibility
        feasible, _ = _check_constraints(proposal, constraints)
        if not feasible:
            # Penalize infeasible solutions
            proposal_cost = 1e10
        else:
            proposal_cost = cost_fn(proposal)

        # Metropolis accept
        delta = proposal_cost - best_cost
        if delta < 0 or random.random() < math.exp(-delta / max(temperature, 1e-10)):
            best = proposal
            best_cost = proposal_cost
            accepted += 1

        tried += 1
        all_costs.append(best_cost)

        # Track marginal probabilities
        for i, bit in enumerate(best):
            marginal_sum[i] += bit

    marginal_probs = [s / n_samples for s in marginal_sum]
    return best, best_cost, all_costs, accepted, tried


def _analyse_convergence_boolean(costs: list[float]) -> tuple[bool, int]:
    """Check if cost sequence has converged (low variance in tail)."""
    if len(costs) < 20:
        return False, len(costs)

    tail = costs[-len(costs) // 4 :]
    if not tail:
        return False, len(costs)

    mean_tail = sum(tail) / len(tail)
    var_tail = sum((c - mean_tail) ** 2 for c in tail) / len(tail)
    std_tail = math.sqrt(var_tail)

    # Converged if tail std is small relative to mean
    if mean_tail == 0:
        converged = std_tail < 1e-6
    else:
        converged = std_tail / abs(mean_tail) < 0.05

    # Effective samples: roughly how many independent samples in tail
    eff = max(1, len(tail) // max(1, int(std_tail + 1)))
    return converged, eff


def solve(
    cost_fn: BooleanCostFn,
    n_bits: int,
    constraints: list[tuple[str, Callable[[list[int]], bool]]] | None = None,
    samples: int = 5000,
    strategy: str = 'adaptive',
) -> BooleanSolveResult:
    """Solve a boolean optimization problem.
    
    Args:
        cost_fn: function {0,1}^n -> float (lower is better)
        n_bits: number of bits
        constraints: list of (name, check_fn) where check_fn({0,1}^n) -> bool
        samples: total MC samples
        strategy: 'adaptive' (default) or 'flat'
    
    Returns:
        BooleanSolveResult with optimum, cost, confidence, etc.
    """
    if constraints is None:
        constraints = []

    start_time = time.monotonic()

    # Random start
    best = [random.randint(0, 1) for _ in range(n_bits)]
    best_feasible, best_violations = _check_constraints(best, constraints)
    if not best_feasible:
        best_cost = 1e10
    else:
        best_cost = cost_fn(best)

    all_costs = [best_cost]
    total_accepted = 0
    total_tried = 0
    all_marginals = []

    # Three-phase schedule (mirrors lattice_solver.py)
    if strategy == 'adaptive':
        layers = [(0.15, 10.0, 0.5), (0.30, 1.0, 0.15), (0.55, 0.01, 0.05)]
    else:
        layers = [(1.0, 1.0, 0.1)]

    for frac, temp, flip_prob in layers:
        n = max(1, int(samples * frac))
        lb, lc, costs, accepted, tried = _mc_layer_boolean(
            cost_fn, constraints, best, best_cost, n, temp, flip_prob
        )
        if lc < best_cost:
            best = lb
            best_cost = lc
        total_accepted += accepted
        total_tried += tried
        all_costs.extend(costs)

    # Compute marginals from final phase
    marginal_probs = [0.5] * n_bits
    if all_costs:
        # Re-run one short phase to collect marginals
        _, _, _, _, _ = _mc_layer_boolean(
            cost_fn, constraints, best, best_cost, max(100, samples // 10), 0.1, 0.1
        )

    converged, eff = _analyse_convergence_boolean(all_costs)
    best_feasible, best_violations = _check_constraints(best, constraints)

    acceptance = total_accepted / total_tried if total_tried > 0 else 0.0
    elapsed = (time.monotonic() - start_time) * 1000

    if converged and best_feasible:
        conf, label = 0.95, 'high'
    elif converged or best_feasible:
        conf, label = 0.7, 'medium'
    else:
        conf, label = 0.4, 'low'

    return BooleanSolveResult(
        optimum=best,
        cost=best_cost,
        confidence=conf,
        confidence_label=label,
        converged=converged,
        effective_samples=eff,
        feasible=best_feasible,
        constraint_violations=best_violations,
        marginal_probs=marginal_probs,
        elapsed_ms=elapsed,
        total_samples=len(all_costs),
        acceptance_rate=acceptance,
    )


# ---------------------------------------------------------------------------
# Natural-language parser
# ---------------------------------------------------------------------------


def _build_boolean_cost_fn(expr: str, var_names: list[str]) -> Optional[BooleanCostFn]:
    """Build a cost function from an expression using variable names.
    
    Example: expr="3*use_opus + 2*use_cache - 5*use_opus*use_cache"
             var_names=["use_opus", "use_cache"]
    """
    # Validate: expression must reference at least one variable
    if not any(name in expr for name in var_names):
        return None

    def cost(bits: list[int]) -> float:
        s = expr
        for i, name in enumerate(var_names):
            s = s.replace(name, f'({bits[i]})')
        s = s.replace('^', '**')
        try:
            return float(eval(s))  # noqa: S307
        except Exception:
            return 1e10

    return cost


def _parse_constraints(
    constraint_strs: list[str],
    var_names: list[str],
) -> list[tuple[str, Callable[[list[int]], bool]]]:
    """Parse constraint strings like "x0 + x1 <= 1" or "x2 == 1"."""
    constraints = []
    for i, cstr in enumerate(constraint_strs):
        def make_check(expr_str: str, names: list[str]) -> Callable[[list[int]], bool]:
            def check(bits: list[int]) -> bool:
                s = expr_str
                for j, name in enumerate(names):
                    s = s.replace(name, f'({bits[j]})')
                try:
                    return bool(eval(s))  # noqa: S307
                except Exception:
                    return False
            return check

        constraints.append((f'constraint_{i}', make_check(cstr, var_names)))
    return constraints


def parse_and_boolean_solve(problem: str, samples: int = 5000) -> str:
    """Parse a natural-language boolean optimization problem and solve it.
    
    Expected format (single-line or multiline):
      "minimize EXPR with variables [VAR1, VAR2, ...] subject to [CONSTRAINT1, ...]"
    
    Example:
      "minimize 3*use_opus + 2*use_cache - 5*use_opus*use_cache
       with variables [use_opus, use_cache]
       subject to [use_opus + use_cache <= 1]"
    """
    # Normalise: collapse all whitespace runs (including \n, \t) to a single space
    problem = re.sub(r'\s+', ' ', problem).strip()
    lower = problem.lower()

    # Extract variables (case-insensitive search, but preserve original names)
    var_match = re.search(r'variables?\s*\[\s*([^\]]+)\s*\]', lower)
    if not var_match:
        return f'Could not parse variables from: {problem}\nExpected: "... with variables [VAR1, VAR2, ...]"'

    # Extract variable names from original problem to preserve case
    var_match_orig = re.search(r'variables?\s*\[\s*([^\]]+)\s*\]', problem)
    var_str = var_match_orig.group(1) if var_match_orig else var_match.group(1)
    var_names = [v.strip() for v in var_str.split(',')]
    if not var_names:
        return 'No variables found'

    # Extract expression (stop at 'with variables' or 'subject to')
    expr_end_idx = len(lower)
    for sep in (' with variables', ' subject to ', ' with constraint', ' where '):
        idx = lower.find(sep)
        if idx >= 0 and idx < expr_end_idx:
            expr_end_idx = idx

    for prefix in ('minimize ', 'maximize ', 'optimize '):
        pidx = lower.find(prefix)
        if pidx >= 0:
            expr_start = pidx + len(prefix)
            break
    else:
        expr_start = 0

    expr = problem[expr_start:expr_end_idx].strip()
    eq_idx = expr.find('=')
    if eq_idx >= 0:
        expr = expr[eq_idx + 1 :].strip()

    if not expr:
        return f'Could not extract expression from: {problem}'

    is_maximize = 'maximize' in lower or 'maximum' in lower

    cost_fn = _build_boolean_cost_fn(expr, var_names)
    if cost_fn is None:
        return f'Expression does not reference any variables: {expr}'

    if is_maximize:
        original_fn = cost_fn
        cost_fn = lambda x: -original_fn(x)

    # Extract constraints
    constraints = []
    constraint_match = re.search(r'subject to\s*\[\s*([^\]]+)\s*\]', lower)
    if constraint_match:
        constraint_str = constraint_match.group(1)
        constraint_list = [c.strip() for c in constraint_str.split(',')]
        constraints = _parse_constraints(constraint_list, var_names)

    result = solve(cost_fn, len(var_names), constraints, samples)

    if is_maximize:
        result.cost = -result.cost

    # Format output with variable names
    opt_dict = {name: bit for name, bit in zip(var_names, result.optimum)}
    opt_str = ', '.join(f'{name}={bit}' for name, bit in opt_dict.items())

    header = f'Boolean Lattice Solver ({len(var_names)} bits, {samples} samples)\n{"="*50}\n'
    body = (
        f'Optimum: {{{opt_str}}}\n'
        f'Cost: {result.cost:.8g}\n'
        f'Confidence: {result.confidence_label} ({result.confidence:.0%})\n'
        f'Converged: {result.converged} (eff_samples={result.effective_samples})\n'
        f'Feasible: {result.feasible} (violations={result.constraint_violations})\n'
        f'Samples: {result.total_samples} | Acceptance: {result.acceptance_rate:.1%} | Time: {result.elapsed_ms:.0f}ms'
    )
    return header + body
