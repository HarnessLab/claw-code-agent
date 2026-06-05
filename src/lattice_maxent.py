"""Maximum Entropy Constraint Solver — find the least-biased distribution.

OPH connection (Observer-Patch Holography, Lemma 2.6):
  Given constraints <O_i> = c_i, the unique state maximizing von Neumann
  entropy is the Gibbs state: p(x) ~ exp(-sum_i lambda_i * O_i(x)).
  This is not a heuristic — it's axiomatically the only consistent answer.
  Any other distribution smuggles in information you don't have.

  The Lagrange multipliers lambda_i are found by the lattice solver:
  minimize the KL divergence between the Gibbs state and the constraints.

Pure Python. Uses the existing solve() from lattice_solver.py.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .lattice_solver import CostFn, solve


@dataclass
class MaxEntResult:
    """Result of maximum entropy optimization."""
    lambdas: dict[str, float]         # Lagrange multipliers per constraint
    constraint_errors: dict[str, float]  # |<O_i> - target_i| for each
    entropy: float                     # estimated entropy of the solution
    satisfied: bool                    # all constraints within tolerance
    sample_mean: dict[str, float]      # actual <O_i> at the solution
    elapsed_ms: float

    def to_text(self) -> str:
        lines = ['MaxEnt Solution (Gibbs state)']
        lines.append(f'Entropy: {self.entropy:.6f}')
        lines.append(f'Constraints satisfied: {self.satisfied}')
        for name, lam in self.lambdas.items():
            err = self.constraint_errors[name]
            mean = self.sample_mean[name]
            lines.append(f'  {name}: lambda={lam:.6f}, <O>={mean:.6f}, error={err:.6f}')
        lines.append(f'Time: {self.elapsed_ms:.0f}ms')
        return '\n'.join(lines)


def maxent_solve(
    constraints: list[tuple[str, CostFn, float]],
    bounds: list[tuple[float, float]],
    samples: int = 5000,
    tol: float = 0.01,
) -> MaxEntResult:
    """Find the Gibbs state maximizing entropy subject to constraints.

    Args:
        constraints: list of (name, observable_fn, target_value) triples.
            observable_fn: x -> R, maps a point to the observable value.
            target_value: the expected value <O_i> must equal this.
        bounds: search bounds for the domain (where the distribution lives).
        samples: Monte Carlo samples for expectation estimation.
        tol: tolerance for constraint satisfaction.

    Returns:
        MaxEntResult with the Lagrange multipliers that define the Gibbs state.

    OPH: The solution p(x) ~ exp(-sum lambda_i O_i(x)) is the unique
    entropy-maximizing state. The lambdas ARE the answer — they define
    the distribution completely.
    """
    t0 = time.monotonic()
    n_constraints = len(constraints)
    if n_constraints == 0:
        raise ValueError('need at least one constraint')

    names = [c[0] for c in constraints]
    obs_fns = [c[1] for c in constraints]
    targets = [c[2] for c in constraints]
    dims = len(bounds)

    # The cost function for lambda-space: how well the Gibbs state
    # p(x) ~ exp(-sum lambda_i O_i(x)) satisfies the constraints.
    # We estimate <O_i> by importance sampling and minimize
    # sum_i (< O_i > - target_i)^2.
    n_mc = max(200, samples // 10)

    def _lambda_cost(lam_vec: list[float]) -> float:
        # Generate samples from the Gibbs distribution via rejection sampling
        # on a grid within bounds
        log_weights: list[float] = []
        obs_vals: list[list[float]] = [[] for _ in range(n_constraints)]

        for _ in range(n_mc):
            x = [random.uniform(lo, hi) for lo, hi in bounds]
            # log p(x) = -sum lambda_i O_i(x) (unnormalized)
            log_p = 0.0
            o_vals = []
            for k in range(n_constraints):
                o = obs_fns[k](x)
                o_vals.append(o)
                log_p -= lam_vec[k] * o
            log_weights.append(log_p)
            for k in range(n_constraints):
                obs_vals[k].append(o_vals[k])

        # Normalize weights (log-sum-exp for stability)
        max_lw = max(log_weights)
        weights = [math.exp(lw - max_lw) for lw in log_weights]
        w_sum = sum(weights)
        if w_sum < 1e-30:
            return 1e10

        # Compute weighted means <O_i>
        cost = 0.0
        for k in range(n_constraints):
            mean_ok = sum(w * o for w, o in zip(weights, obs_vals[k])) / w_sum
            cost += (mean_ok - targets[k]) ** 2

        return cost

    # Solve for the Lagrange multipliers
    lambda_bounds = [(-10.0, 10.0)] * n_constraints
    result = solve(_lambda_cost, lambda_bounds, samples)
    opt_lambdas = result.optimum

    # Evaluate the solution: compute <O_i> and entropy at the optimal lambdas
    log_weights: list[float] = []
    obs_vals: list[list[float]] = [[] for _ in range(n_constraints)]
    n_eval = max(500, samples // 5)

    for _ in range(n_eval):
        x = [random.uniform(lo, hi) for lo, hi in bounds]
        log_p = 0.0
        o_vals = []
        for k in range(n_constraints):
            o = obs_fns[k](x)
            o_vals.append(o)
            log_p -= opt_lambdas[k] * o
        log_weights.append(log_p)
        for k in range(n_constraints):
            obs_vals[k].append(o_vals[k])

    max_lw = max(log_weights)
    weights = [math.exp(lw - max_lw) for lw in log_weights]
    w_sum = sum(weights)
    probs = [w / w_sum for w in weights] if w_sum > 1e-30 else [1.0 / n_eval] * n_eval

    # Shannon entropy of the weight distribution
    entropy = -sum(p * math.log(max(p, 1e-30)) for p in probs)

    # Constraint errors
    sample_means: dict[str, float] = {}
    constraint_errors: dict[str, float] = {}
    all_satisfied = True
    for k in range(n_constraints):
        mean_ok = sum(w * o for w, o in zip(weights, obs_vals[k])) / max(w_sum, 1e-30)
        sample_means[names[k]] = mean_ok
        err = abs(mean_ok - targets[k])
        constraint_errors[names[k]] = err
        if err > tol:
            all_satisfied = False

    elapsed = (time.monotonic() - t0) * 1000
    return MaxEntResult(
        lambdas={names[k]: opt_lambdas[k] for k in range(n_constraints)},
        constraint_errors=constraint_errors,
        entropy=entropy,
        satisfied=all_satisfied,
        sample_mean=sample_means,
        elapsed_ms=elapsed,
    )
