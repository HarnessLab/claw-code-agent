"""Sector Decomposition — independent sectors combined via log-odds product.

OPH connection (Observer-Patch Holography):
  Each observer patch sees an independent sector of the cost landscape.
  The global optimum is reconstructed by combining patch-local optima
  via Bayesian update (log-odds product), NOT averaging.

  This is Lemma 2.4: independent observations combine multiplicatively
  in log-odds space. Consensus measures inter-patch agreement.

Pure Python. Uses the existing solve() from lattice_solver.py.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

from .lattice_solver import CostFn, SolveResult, solve


@dataclass
class SectorResult:
    """Combined result from all sectors."""
    optimum: list[float]
    combined_cost: float
    consensus: float          # 1 = perfect agreement, 0 = total disagreement
    sector_results: dict[str, SolveResult]
    sector_costs: dict[str, float]
    elapsed_ms: float

    def to_text(self) -> str:
        lines = [
            f'Combined optimum: [{", ".join(f"x{i}={v:.6f}" for i, v in enumerate(self.optimum))}]',
            f'Combined cost: {self.combined_cost:.8g}',
            f'Consensus: {self.consensus:.4f}',
            f'Sectors: {len(self.sector_results)}',
        ]
        for name, sr in self.sector_results.items():
            sc = self.sector_costs[name]
            lines.append(f'  {name}: cost={sc:.8g}, confidence={sr.confidence_label}')
        lines.append(f'Time: {self.elapsed_ms:.0f}ms')
        return '\n'.join(lines)


def _cost_to_logodds(cost: float, scale: float = 1.0) -> float:
    """Convert a cost to log-odds: lower cost = higher probability of being optimal."""
    p = math.exp(-cost / max(scale, 1e-30))
    p = max(1e-15, min(1 - 1e-15, p))
    return math.log(p / (1 - p))


def _logodds_to_prob(lo: float) -> float:
    """Convert log-odds back to probability."""
    if lo > 30:
        return 1.0 - 1e-15
    if lo < -30:
        return 1e-15
    return 1.0 / (1.0 + math.exp(-lo))


class SectorSolver:
    """Decompose an optimization into independent sectors.

    Each sector has its own cost function capturing one aspect of the problem.
    Sectors run the lattice solver independently.
    Results combine via log-odds product (Bayesian update), NOT averaging.
    Consensus measures how much sectors agree on the optimum location.

    OPH: each sector is an observer patch. The log-odds product is the
    patch-merging operation that reconstructs the global state.
    """

    def __init__(self, sectors: dict[str, CostFn]):
        if not sectors:
            raise ValueError('need at least one sector')
        self.sectors = sectors

    def solve(self, bounds: list[tuple[float, float]], samples: int = 5000) -> SectorResult:
        """Run each sector independently, combine via log-odds product."""
        t0 = time.monotonic()
        sector_results: dict[str, SolveResult] = {}
        sector_costs: dict[str, float] = {}

        # Solve each sector independently
        for name, cost_fn in self.sectors.items():
            sr = solve(cost_fn, bounds, samples)
            sector_results[name] = sr
            sector_costs[name] = sr.cost

        # Find the cost scale for log-odds conversion
        all_costs = list(sector_costs.values())
        cost_range = max(all_costs) - min(all_costs) if len(all_costs) > 1 else 1.0
        scale = max(cost_range, abs(sum(all_costs) / len(all_costs)), 1e-10)

        # Combine via log-odds product: evaluate each sector's cost at every other
        # sector's optimum, pick the point with highest combined log-odds
        candidates: list[tuple[list[float], float]] = []
        for name, sr in sector_results.items():
            total_logodds = 0.0
            for s_name, s_fn in self.sectors.items():
                c = s_fn(sr.optimum)
                total_logodds += _cost_to_logodds(c, scale)
            candidates.append((sr.optimum, total_logodds))

        best_opt, best_lo = max(candidates, key=lambda t: t[1])
        combined_cost = sum(fn(best_opt) for fn in self.sectors.values())

        # Consensus: 1 - CV of sector costs at the combined optimum
        sector_costs_at_best = [fn(best_opt) for fn in self.sectors.values()]
        mean_c = sum(sector_costs_at_best) / len(sector_costs_at_best)
        if abs(mean_c) > 1e-30 and len(sector_costs_at_best) > 1:
            std_c = math.sqrt(sum((c - mean_c) ** 2 for c in sector_costs_at_best)
                              / len(sector_costs_at_best))
            consensus = max(0.0, 1.0 - std_c / abs(mean_c))
        else:
            consensus = 1.0

        elapsed = (time.monotonic() - t0) * 1000
        return SectorResult(
            optimum=best_opt,
            combined_cost=combined_cost,
            consensus=consensus,
            sector_results=sector_results,
            sector_costs=sector_costs,
            elapsed_ms=elapsed,
        )
