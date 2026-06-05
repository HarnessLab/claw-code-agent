#!/usr/bin/env python3
"""
ROUTING OPTIMIZER

Adjusts routing thresholds based on real-world performance.

Monitors:
  - Success rate per route (model + task type + complexity)
  - Cost per route (tokens used)
  - Quality per route (artifact quality score)
  - Failure modes (what goes wrong and why)

Optimizes:
  - Cost limits (increase if failing, decrease if succeeding)
  - Quality thresholds (adjust based on actual quality)
  - Model selection (switch models if one consistently outperforms)
  - Complexity thresholds (adjust simple/medium/complex boundaries)

Usage:
  optimizer = RoutingOptimizer(tree)
  optimizer.record_outcome(task_type, complexity, model, success, cost, quality)
  changes = optimizer.optimize()
  # Returns: {"code/medium": {"reason": "low success", "action": "increase cost limit"}}
"""

import json
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class PerformanceMetric:
    """Performance metric for a route."""
    route_key: str  # "code/medium/gpt-4"
    success_count: int = 0
    failure_count: int = 0
    total_cost: int = 0
    total_quality: int = 0
    last_updated: str = None

    def __post_init__(self):
        if self.last_updated is None:
            self.last_updated = datetime.now().isoformat()

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total

    @property
    def avg_cost(self) -> int:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0
        return self.total_cost // total

    @property
    def avg_quality(self) -> int:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0
        return self.total_quality // total


class RoutingOptimizer:
    """Optimizes routing decisions based on outcomes."""

    def __init__(self, tree_path: str = None):
        self.tree_path = tree_path or os.path.expanduser(
            "~/.latti/routing_tree.json"
        )
        self.metrics_path = os.path.expanduser(
            "~/.latti/routing_metrics.json"
        )
        self.metrics: Dict[str, PerformanceMetric] = self._load_metrics()

    def _load_metrics(self) -> Dict[str, PerformanceMetric]:
        """Load metrics from disk."""
        if os.path.exists(self.metrics_path):
            with open(self.metrics_path) as f:
                data = json.load(f)
                return {
                    k: PerformanceMetric(**v) for k, v in data.items()
                }
        return {}

    def _save_metrics(self) -> None:
        """Save metrics to disk."""
        os.makedirs(os.path.dirname(self.metrics_path), exist_ok=True)
        data = {
            k: {
                "route_key": v.route_key,
                "success_count": v.success_count,
                "failure_count": v.failure_count,
                "total_cost": v.total_cost,
                "total_quality": v.total_quality,
                "last_updated": v.last_updated,
            }
            for k, v in self.metrics.items()
        }
        with open(self.metrics_path, "w") as f:
            json.dump(data, f, indent=2)

    def record_outcome(
        self,
        task_type: str,
        complexity: float,
        model: str,
        success: bool,
        cost: int,
        quality: int,
    ) -> None:
        """Record the outcome of a routing decision."""
        # Map complexity to level
        if complexity < 0.33:
            level = "simple"
        elif complexity < 0.67:
            level = "medium"
        else:
            level = "complex"

        route_key = f"{task_type}/{level}/{model}"

        if route_key not in self.metrics:
            self.metrics[route_key] = PerformanceMetric(route_key=route_key)

        metric = self.metrics[route_key]

        if success:
            metric.success_count += 1
        else:
            metric.failure_count += 1

        metric.total_cost += cost
        metric.total_quality += quality
        metric.last_updated = datetime.now().isoformat()

        self._save_metrics()

    def optimize(self) -> Dict:
        """Optimize routing thresholds based on metrics."""
        changes = {}

        for route_key, metric in self.metrics.items():
            total = metric.success_count + metric.failure_count

            # Need at least 5 outcomes to optimize
            if total < 5:
                continue

            success_rate = metric.success_rate
            avg_quality = metric.avg_quality

            # Rule 1: Low success rate → increase cost limit
            if success_rate < 0.6:
                changes[route_key] = {
                    "reason": "low success rate",
                    "success_rate": round(success_rate, 2),
                    "action": "increase cost limit by 20%",
                    "priority": "high",
                }

            # Rule 2: High success rate + high quality → decrease cost limit
            elif success_rate > 0.85 and avg_quality > 80:
                changes[route_key] = {
                    "reason": "high success + quality",
                    "success_rate": round(success_rate, 2),
                    "avg_quality": avg_quality,
                    "action": "decrease cost limit by 10%",
                    "priority": "low",
                }

            # Rule 3: Low quality despite success → increase quality threshold
            if avg_quality < 70:
                changes[route_key] = {
                    "reason": "low quality",
                    "avg_quality": avg_quality,
                    "action": "increase quality threshold",
                    "priority": "medium",
                }

        return changes

    def recommend_model_switch(self) -> Dict:
        """Recommend switching models if one consistently outperforms."""
        recommendations = {}

        # Group metrics by task_type and level
        by_task_level = {}
        for route_key, metric in self.metrics.items():
            parts = route_key.split("/")
            if len(parts) != 3:
                continue

            task_type, level, model = parts
            key = f"{task_type}/{level}"

            if key not in by_task_level:
                by_task_level[key] = {}

            by_task_level[key][model] = metric

        # Compare models
        for key, models in by_task_level.items():
            if len(models) < 2:
                continue

            # Find best model
            best_model = max(
                models.items(),
                key=lambda x: (x[1].success_rate, x[1].avg_quality),
            )
            best_name, best_metric = best_model

            # Check if significantly better
            for model_name, metric in models.items():
                if model_name == best_name:
                    continue

                if (
                    best_metric.success_rate > metric.success_rate + 0.2
                    and best_metric.avg_quality > metric.avg_quality + 10
                ):
                    recommendations[key] = {
                        "current_model": model_name,
                        "recommended_model": best_name,
                        "reason": "significantly better success rate and quality",
                        "current_success_rate": round(
                            metric.success_rate, 2
                        ),
                        "recommended_success_rate": round(
                            best_metric.success_rate, 2
                        ),
                        "current_quality": metric.avg_quality,
                        "recommended_quality": best_metric.avg_quality,
                    }

        return recommendations

    def stats(self) -> Dict:
        """Get optimization statistics."""
        stats = {
            "total_routes": len(self.metrics),
            "total_outcomes": sum(
                m.success_count + m.failure_count
                for m in self.metrics.values()
            ),
            "overall_success_rate": 0.0,
            "overall_avg_quality": 0,
            "routes": {},
        }

        total_success = 0
        total_outcomes = 0
        total_quality = 0

        for route_key, metric in self.metrics.items():
            total = metric.success_count + metric.failure_count
            if total == 0:
                continue

            total_success += metric.success_count
            total_outcomes += total
            total_quality += metric.total_quality

            stats["routes"][route_key] = {
                "success_rate": round(metric.success_rate, 2),
                "avg_cost": metric.avg_cost,
                "avg_quality": metric.avg_quality,
                "outcomes": total,
            }

        if total_outcomes > 0:
            stats["overall_success_rate"] = round(
                total_success / total_outcomes, 2
            )
            stats["overall_avg_quality"] = total_quality // total_outcomes

        return stats


if __name__ == "__main__":
    print("Testing Routing Optimizer...\n")

    optimizer = RoutingOptimizer()

    # Record some outcomes
    print("1. Recording outcomes:")
    outcomes = [
        ("code", 0.2, "gpt-3.5", True, 1500, 85),
        ("code", 0.2, "gpt-3.5", True, 1600, 88),
        ("code", 0.2, "gpt-3.5", False, 1400, 60),
        ("code", 0.2, "gpt-3.5", False, 1500, 65),
        ("code", 0.2, "gpt-3.5", True, 1550, 82),
        ("code", 0.5, "gpt-4", True, 3000, 92),
        ("code", 0.5, "gpt-4", True, 3100, 95),
        ("code", 0.5, "gpt-4", True, 2900, 90),
        ("code", 0.5, "gpt-4", True, 3050, 93),
        ("code", 0.5, "gpt-4", True, 3000, 91),
    ]

    for task_type, complexity, model, success, cost, quality in outcomes:
        optimizer.record_outcome(
            task_type, complexity, model, success, cost, quality
        )
        print(f"   Recorded: {task_type}/{complexity}/{model} → {success}")

    print("\n2. Optimization recommendations:")
    changes = optimizer.optimize()
    print(json.dumps(changes, indent=2))

    print("\n3. Model switch recommendations:")
    recommendations = optimizer.recommend_model_switch()
    print(json.dumps(recommendations, indent=2))

    print("\n4. Statistics:")
    stats = optimizer.stats()
    print(json.dumps(stats, indent=2))
