"""Latti lattice solver — three-layer adaptive Monte Carlo.

Pure Python, zero dependencies. Same algorithm as the Rust crate:
exploration → focused search → annealing refinement.

The cipher is COMPACTNESS.
"""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

CostFn = Callable[[list[float]], float]


@dataclass
class SolveResult:
    optimum: list[float]
    cost: float
    confidence: float
    confidence_label: str
    converged: bool
    effective_samples: int
    block_var_ratio: float
    tail_type: str
    tail_exponent: float
    tail_r2: float
    scale_stable: bool
    elapsed_ms: float
    total_samples: int
    acceptance_rate: float

    def to_text(self) -> str:
        coords = ', '.join(f'x{i}={v:.6f}' for i, v in enumerate(self.optimum))
        return (
            f'Optimum: [{coords}]\n'
            f'Value: {self.cost:.8g}\n'
            f'Confidence: {self.confidence_label} ({self.confidence:.0%})\n'
            f'Converged: {self.converged} (eff_samples={self.effective_samples}, block_var_ratio={self.block_var_ratio:.4f})\n'
            f'Tail: {self.tail_type} (exponent={self.tail_exponent:.4f}, R²={self.tail_r2:.4f})\n'
            f'Scale stable: {self.scale_stable}\n'
            f'Samples: {self.total_samples} | Acceptance: {self.acceptance_rate:.1%} | Time: {self.elapsed_ms:.0f}ms'
        )


def _compactify_bounds(bounds: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = []
    for lo, hi in bounds:
        lo2 = lo if math.isfinite(lo) else -1e3
        hi2 = hi if math.isfinite(hi) else 1e3
        if abs(hi2 - lo2) > 1e6:
            lo2, hi2 = -1e3, 1e3
        result.append((lo2, hi2))
    return result


def _clamp(x: list[float], bounds: list[tuple[float, float]]) -> list[float]:
    return [max(lo, min(hi, xi)) for xi, (lo, hi) in zip(x, bounds)]


def _zoom_bounds(bounds: list[tuple[float, float]], centre: list[float], frac: float) -> list[tuple[float, float]]:
    result = []
    for (lo, hi), c in zip(bounds, centre):
        half = (hi - lo) * frac * 0.5
        result.append((max(lo, c - half), min(hi, c + half)))
    return result


def _mc_layer(
    cost_fn: CostFn,
    bounds: list[tuple[float, float]],
    start: list[float],
    start_cost: float,
    n_samples: int,
    temperature: float,
    initial_step: float,
) -> tuple[list[float], float, list[float], int, int]:
    dims = len(start)
    current = list(start)
    current_cost = start_cost
    best = list(current)
    best_cost = current_cost

    step_sizes = [(hi - lo) * initial_step for lo, hi in bounds]
    all_costs: list[float] = []
    accepted = 0
    total = 0
    window_accepted = 0
    window_total = 0
    tune_interval = 200

    for i in range(n_samples):
        proposal = [current[d] + random.uniform(-1, 1) * step_sizes[d] for d in range(dims)]
        proposal = _clamp(proposal, bounds)
        prop_cost = cost_fn(proposal)
        d_cost = prop_cost - current_cost
        total += 1
        window_total += 1

        if d_cost < 0:
            accept = True
        elif temperature > 1e-15:
            accept = random.random() < math.exp(-d_cost / temperature)
        else:
            accept = False

        if accept:
            current = proposal
            current_cost = prop_cost
            accepted += 1
            window_accepted += 1
            if current_cost < best_cost:
                best = list(current)
                best_cost = current_cost

        all_costs.append(current_cost)

        if (i + 1) % tune_interval == 0 and window_total > 0:
            rate = window_accepted / window_total
            if rate < 0.25:
                step_sizes = [s * 0.8 for s in step_sizes]
            elif rate > 0.55:
                step_sizes = [s * 1.3 for s in step_sizes]
            window_accepted = 0
            window_total = 0

    return best, best_cost, all_costs, accepted, total


def _lin_reg(x: list[float], y: list[float]) -> tuple[float, float]:
    n = len(x)
    if n < 2:
        return 0.0, 0.0
    sx = sum(x)
    sy = sum(y)
    sxx = sum(a * a for a in x)
    sxy = sum(a * b for a, b in zip(x, y))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-30:
        return 0.0, 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    y_mean = sy / n
    ss_tot = sum((v - y_mean) ** 2 for v in y)
    if ss_tot < 1e-30:
        return slope, 1.0
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r2


def _analyse_convergence(costs: list[float]) -> tuple[bool, int, float]:
    n = len(costs)
    if n < 20:
        return False, n, 1.0
    block_size = max(10, n // 20)
    n_blocks = n // block_size
    if n_blocks < 2:
        return False, n, 1.0
    total_mean = sum(costs) / n
    total_var = sum((c - total_mean) ** 2 for c in costs) / n
    block_means = []
    for b in range(n_blocks):
        s = b * block_size
        block_means.append(sum(costs[s:s + block_size]) / block_size)
    bm_mean = sum(block_means) / n_blocks
    block_var = sum((m - bm_mean) ** 2 for m in block_means) / n_blocks
    ratio = block_var / total_var if total_var > 1e-30 else 0.0
    eff = min(n, int(n / (ratio * n_blocks)) if ratio > 1e-30 else n)
    converged = eff > 100 and ratio < 0.1
    return converged, eff, ratio


def _analyse_concentration(costs: list[float]) -> tuple[str, float, float, float]:
    n = len(costs)
    if n < 10:
        return 'insufficient_data', 0.0, 0.0, 0.0
    sorted_c = sorted(costs)
    p50 = sorted_c[n // 2]
    p95 = sorted_c[int(n * 0.95)]
    tail_risk = p95 / p50 if abs(p50) > 1e-30 else 0.0
    start_idx = n * 3 // 4
    tail = sorted_c[start_idx:]
    tail_n = len(tail)
    if tail_n < 5:
        return 'insufficient_tail', 0.0, 0.0, tail_risk
    s_vals = [(tail_n - i) / n for i in range(tail_n)]
    ln_s = [math.log(s) for s in s_vals if s > 0]
    x_exp = tail[:len(ln_s)]
    exp_slope, exp_r2 = _lin_reg(x_exp, ln_s)
    valid = [(math.log(x), math.log(s)) for x, s in zip(tail, s_vals) if x > 0 and s > 0]
    if len(valid) >= 3:
        lx = [p[0] for p in valid]
        ls = [p[1] for p in valid]
        poly_slope, poly_r2 = _lin_reg(lx, ls)
    else:
        poly_slope, poly_r2 = 0.0, 0.0
    if exp_r2 >= poly_r2:
        return 'exponential', -exp_slope, exp_r2, tail_risk
    return 'polynomial', -poly_slope, poly_r2, tail_risk


def _check_scale_stability(costs: list[float]) -> bool:
    n = len(costs)
    if n < 40:
        return True
    half = n // 2
    mean1 = sum(costs[:half]) / half
    mean2 = sum(costs[half:]) / (n - half)
    total_mean = (mean1 + mean2) / 2
    if abs(total_mean) < 1e-30:
        return True
    return abs(mean1 - mean2) / abs(total_mean) < 0.5


def _classify_landscape(
    cost_fn: CostFn, bounds: list[tuple[float, float]], n_scout: int = 200,
) -> tuple[str, list[float], float]:
    """Scout the landscape and classify it for algorithm selection.

    Returns (strategy, best_point, best_cost).
    Strategies: 'smooth', 'convex', 'rugged', 'flat'.
    """
    dims = len(bounds)

    # Scout: random samples
    points = [[random.uniform(lo, hi) for lo, hi in bounds] for _ in range(n_scout)]
    costs = [cost_fn(p) for p in points]

    best_idx = min(range(n_scout), key=lambda i: costs[i])
    best_point = points[best_idx]
    best_cost = costs[best_idx]

    # Check gradient coherence (finite differences at best point)
    eps = 1e-5
    grad_coherent = True
    for d in range(dims):
        shifted = list(best_point)
        shifted[d] += eps
        shifted[d] = min(bounds[d][1], shifted[d])
        f_plus = cost_fn(shifted)
        shifted[d] = best_point[d] - eps
        shifted[d] = max(bounds[d][0], shifted[d])
        f_minus = cost_fn(shifted)
        grad = (f_plus - f_minus) / (2 * eps)
        if not math.isfinite(grad):
            grad_coherent = False
            break

    # Check for multiple basins
    sorted_costs = sorted(costs)
    low_costs = [c for c in sorted_costs if c < sorted_costs[n_scout // 4]]
    cost_spread = max(low_costs) - min(low_costs) if low_costs else 0
    single_basin = cost_spread < abs(best_cost) * 0.1 if abs(best_cost) > 1e-10 else cost_spread < 1e-6

    # Check flatness
    cost_range = sorted_costs[-1] - sorted_costs[0]
    is_flat = cost_range < 1e-8

    if is_flat:
        return 'flat', best_point, best_cost
    elif grad_coherent and single_basin:
        return 'smooth', best_point, best_cost
    elif grad_coherent:
        return 'rugged', best_point, best_cost
    else:
        return 'rugged', best_point, best_cost


def _gradient_polish(
    cost_fn: CostFn, start: list[float], bounds: list[tuple[float, float]],
    steps: int = 500, lr: float = 0.01,
) -> tuple[list[float], float]:
    """Simple gradient descent polish from a starting point."""
    dims = len(bounds)
    x = list(start)
    best_x = list(x)
    best_cost = cost_fn(x)
    eps = 1e-6

    for _ in range(steps):
        grad = []
        for d in range(dims):
            xp = list(x)
            xp[d] = min(bounds[d][1], x[d] + eps)
            xm = list(x)
            xm[d] = max(bounds[d][0], x[d] - eps)
            grad.append((cost_fn(xp) - cost_fn(xm)) / (2 * eps))

        # Update
        for d in range(dims):
            x[d] -= lr * grad[d]
            x[d] = max(bounds[d][0], min(bounds[d][1], x[d]))

        c = cost_fn(x)
        if c < best_cost:
            best_cost = c
            best_x = list(x)

        # Adaptive lr
        if sum(g * g for g in grad) < 1e-12:
            break

    return best_x, best_cost


def solve(
    cost_fn: CostFn,
    bounds: list[tuple[float, float]],
    samples: int = 10000,
) -> SolveResult:
    """Adaptive solver — classifies landscape, picks the right algorithm."""
    start_time = time.monotonic()
    dims = len(bounds)
    bounds = _compactify_bounds(bounds)

    # Phase 1: Scout and classify
    strategy, scout_best, scout_cost = _classify_landscape(cost_fn, bounds)

    best = scout_best
    best_cost = scout_cost
    all_costs: list[float] = []
    total_accepted = 0
    total_tried = 0

    # Phase 2: Apply strategy
    if strategy == 'smooth' and dims <= 10:
        # Gradient descent polish — fast and precise for smooth landscapes
        best, best_cost = _gradient_polish(cost_fn, best, bounds, steps=1000)
        all_costs.append(best_cost)
        total_accepted = 1
        total_tried = 1
    else:
        # Monte Carlo — works everywhere, especially rugged landscapes
        if dims <= 3:
            layers = [(1.0, 1.0, 0.3)]
        else:
            layers = [(0.15, 10.0, 0.5), (0.30, 1.0, 0.15), (0.55, 0.01, 0.05)]

        for frac, temp, step in layers:
            n = max(1, int(samples * frac))
            lb, lc, costs, accepted, tried = _mc_layer(cost_fn, bounds, best, best_cost, n, temp, step)
            if lc < best_cost:
                best = lb
                best_cost = lc
            total_accepted += accepted
            total_tried += tried
            all_costs.extend(costs)
            bounds = _zoom_bounds(bounds, best, 0.3)

    # Phase 3: Gradient polish on MC result (if landscape is smooth enough)
    if strategy != 'flat' and len(all_costs) > 10:
        polished, polished_cost = _gradient_polish(cost_fn, best, _compactify_bounds(bounds))
        if polished_cost < best_cost:
            best = polished
            best_cost = polished_cost

    converged, eff, ratio = _analyse_convergence(all_costs)
    tail_type, tail_exp, tail_r2, _ = _analyse_concentration(all_costs)
    stable = _check_scale_stability(all_costs)
    acceptance = total_accepted / total_tried if total_tried > 0 else 0.0
    elapsed = (time.monotonic() - start_time) * 1000

    if converged and stable and tail_r2 > 0.8:
        conf, label = 0.95, 'high'
    elif converged or stable:
        conf, label = 0.7, 'medium'
    else:
        conf, label = 0.4, 'low'

    return SolveResult(
        optimum=best, cost=best_cost,
        confidence=conf, confidence_label=label,
        converged=converged, effective_samples=eff, block_var_ratio=ratio,
        tail_type=tail_type, tail_exponent=tail_exp, tail_r2=tail_r2,
        scale_stable=stable, elapsed_ms=elapsed,
        total_samples=len(all_costs), acceptance_rate=acceptance,
    )


# ---------------------------------------------------------------------------
# Natural-language parser (same as Rust router)
# ---------------------------------------------------------------------------

def _extract_bounds(text: str) -> list[tuple[float, float]]:
    return [(float(lo), float(hi)) for lo, hi in re.findall(r'\[([+-]?\d*\.?\d+)\s*,\s*([+-]?\d*\.?\d+)\]', text)]


def _normalize_expr(expr: str, dims: int) -> str:
    """Convert bare variable names (x, y, z, ...) to indexed form (x0, x1, x2, ...)."""
    bare_names = ['x', 'y', 'z', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k']
    result = expr
    for idx, name in enumerate(bare_names[:dims]):
        result = re.sub(r'\b' + name + r'\b', f'x{idx}', result)
    return result



def _build_cost_fn(expr: str, dims: int) -> Optional[CostFn]:
    # Normalize bare variable names to indexed form
    expr = _normalize_expr(expr, dims)
    
    # Validate: expression must reference x0..x{dims-1}
    if not any(f'x{i}' in expr for i in range(dims)):
        return None

    def cost(x: list[float]) -> float:
        s = expr
        for i in range(len(x) - 1, -1, -1):
            s = s.replace(f'x{i}', f'({x[i]})')
        s = s.replace('^', '**')
        try:
            return float(eval(s))  # noqa: S307
        except Exception:
            return 1e10

    return cost


def parse_and_solve(problem: str, samples: int = 10000) -> str:
    """Parse a natural-language optimization problem and solve it."""
    lower = problem.lower()
    bounds = _extract_bounds(lower)
    if not bounds:
        return f'Could not parse bounds from: {problem}\nExpected format: "minimize EXPR in [lo,hi] x [lo,hi]"'

    dims = len(bounds)

    # Extract expression
    for sep in (' in ', ' for ', ' bounds '):
        idx = lower.find(sep)
        if idx >= 0:
            break
    else:
        return f'Could not find expression separator (in/for/bounds) in: {problem}'

    for prefix in ('minimize ', 'maximize ', 'optimize ', 'find the minimum of ', 'find the maximum of '):
        pidx = lower.find(prefix)
        if pidx >= 0:
            expr_start = pidx + len(prefix)
            break
    else:
        expr_start = 0

    expr = problem[expr_start:idx].strip()
    # Clean up f(x,y) = ... patterns
    eq_idx = expr.find('=')
    if eq_idx >= 0:
        expr = expr[eq_idx + 1:].strip()

    if not expr:
        return f'Could not extract expression from: {problem}'

    is_maximize = 'maximize' in lower or 'maximum' in lower

    cost_fn = _build_cost_fn(expr, dims)
    if cost_fn is None:
        return f'Expression does not reference variables x0..x{dims-1}: {expr}'

    if is_maximize:
        original_fn = cost_fn
        cost_fn = lambda x: -original_fn(x)

    result = solve(cost_fn, bounds, samples)

    if is_maximize:
        result.cost = -result.cost

    header = f'Lattice Monte Carlo Solver ({dims}D, {samples} samples)\n{"="*50}\n'
    return header + result.to_text()
