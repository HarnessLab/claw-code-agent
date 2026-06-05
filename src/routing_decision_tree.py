#!/usr/bin/env python3
"""
ROUTING DECISION TREE

Learns which model/tool works best for each task type.
Tracks success rates and auto-adjusts routing decisions.

Structure:
  task_type (code, design, doc, analysis, etc.)
    ├─ complexity_level (simple, medium, complex)
    │   ├─ best_model (gpt-4, gpt-3.5, claude, etc.)
    │   ├─ success_rate (0-1)
    │   ├─ avg_cost (tokens)
    │   └─ avg_quality (0-100)
    └─ fallback_model (if primary fails)

Usage:
  tree = RoutingDecisionTree()
  route = tree.route(task_type="code", complexity=0.7)
  # Returns: {"model": "gpt-4", "tool": "code_generator", "cost_limit": 5000}
  
  tree.record_outcome(task_type, complexity, model, success=True, cost=2000, quality=85)
  tree.optimize()  # Rebalance thresholds
"""

import json
import os
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class RouteDecision:
    """A routing decision for a task."""
    task_type: str
    complexity: float  # 0-1
    model: str
    tool: str
    cost_limit: int
    quality_threshold: int
    confidence: float  # 0-1


@dataclass
class RouteOutcome:
    """Outcome of a routing decision."""
    task_type: str
    complexity: float
    model: str
    success: bool
    cost: int
    quality: int
    error: Optional[str] = None
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


class RoutingDecisionTree:
    """Learns routing decisions from outcomes."""

    def __init__(self, path: str = None):
        self.path = path or os.path.expanduser("~/.latti/routing_tree.json")
        self.tree = self._load_tree()
        self.outcomes: List[RouteOutcome] = []

    def _load_tree(self) -> Dict:
        """Load routing tree from disk."""
        if os.path.exists(self.path):
            with open(self.path) as f:
                return json.load(f)
        return self._default_tree()

    def _default_tree(self) -> Dict:
        """Default routing tree (bootstrap)."""
        return {
            "code": {
                "simple": {
                    "model": "gpt-3.5",
                    "tool": "code_generator",
                    "cost_limit": 2000,
                    "quality_threshold": 70,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "medium": {
                    "model": "gpt-4",
                    "tool": "code_generator",
                    "cost_limit": 5000,
                    "quality_threshold": 80,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "complex": {
                    "model": "gpt-4",
                    "tool": "code_generator",
                    "cost_limit": 10000,
                    "quality_threshold": 85,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
            },
            "design": {
                "simple": {
                    "model": "gpt-3.5",
                    "tool": "design_generator",
                    "cost_limit": 3000,
                    "quality_threshold": 75,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "medium": {
                    "model": "gpt-4",
                    "tool": "design_generator",
                    "cost_limit": 6000,
                    "quality_threshold": 80,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "complex": {
                    "model": "gpt-4",
                    "tool": "design_generator",
                    "cost_limit": 12000,
                    "quality_threshold": 85,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
            },
            "doc": {
                "simple": {
                    "model": "gpt-3.5",
                    "tool": "doc_generator",
                    "cost_limit": 2000,
                    "quality_threshold": 70,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "medium": {
                    "model": "gpt-3.5",
                    "tool": "doc_generator",
                    "cost_limit": 4000,
                    "quality_threshold": 75,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "complex": {
                    "model": "gpt-4",
                    "tool": "doc_generator",
                    "cost_limit": 8000,
                    "quality_threshold": 80,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
            },
            "analysis": {
                "simple": {
                    "model": "gpt-3.5",
                    "tool": "analyzer",
                    "cost_limit": 2000,
                    "quality_threshold": 70,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "medium": {
                    "model": "gpt-4",
                    "tool": "analyzer",
                    "cost_limit": 5000,
                    "quality_threshold": 80,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
                "complex": {
                    "model": "gpt-4",
                    "tool": "analyzer",
                    "cost_limit": 10000,
                    "quality_threshold": 85,
                    "success_rate": 0.0,
                    "outcomes": 0,
                },
            },
        }

    def route(
        self, task_type: str, complexity: float
    ) -> Optional[RouteDecision]:
        """Route a task to the best model/tool."""
        if task_type not in self.tree:
            return None

        # Map complexity (0-1) to level (simple, medium, complex)
        if complexity < 0.33:
            level = "simple"
        elif complexity < 0.67:
            level = "medium"
        else:
            level = "complex"

        route = self.tree[task_type][level]

        return RouteDecision(
            task_type=task_type,
            complexity=complexity,
            model=route["model"],
            tool=route["tool"],
            cost_limit=route["cost_limit"],
            quality_threshold=route["quality_threshold"],
            confidence=route["success_rate"],
        )

    def record_outcome(
        self,
        task_type: str,
        complexity: float,
        model: str,
        success: bool,
        cost: int,
        quality: int,
        error: Optional[str] = None,
    ) -> None:
        """Record the outcome of a routing decision."""
        outcome = RouteOutcome(
            task_type=task_type,
            complexity=complexity,
            model=model,
            success=success,
            cost=cost,
            quality=quality,
            error=error,
        )
        self.outcomes.append(outcome)

        # Update tree
        if complexity < 0.33:
            level = "simple"
        elif complexity < 0.67:
            level = "medium"
        else:
            level = "complex"

        route = self.tree[task_type][level]
        route["outcomes"] += 1

        if success:
            route["success_rate"] = (
                route["success_rate"] * (route["outcomes"] - 1) + 1
            ) / route["outcomes"]
        else:
            route["success_rate"] = (
                route["success_rate"] * (route["outcomes"] - 1)
            ) / route["outcomes"]

        self._save_tree()

    def optimize(self) -> Dict:
        """Optimize routing thresholds based on outcomes."""
        if not self.outcomes:
            return {"status": "no outcomes to optimize"}

        changes = {}

        for task_type in self.tree:
            for level in self.tree[task_type]:
                route = self.tree[task_type][level]

                if route["outcomes"] < 5:
                    continue  # Not enough data

                success_rate = route["success_rate"]

                # If success rate is too low, increase cost limit or lower quality threshold
                if success_rate < 0.7:
                    old_cost = route["cost_limit"]
                    route["cost_limit"] = int(route["cost_limit"] * 1.2)
                    changes[f"{task_type}/{level}"] = {
                        "reason": "low success rate",
                        "success_rate": success_rate,
                        "cost_limit": f"{old_cost} → {route['cost_limit']}",
                    }

                # If success rate is high, try to reduce cost
                elif success_rate > 0.9:
                    old_cost = route["cost_limit"]
                    route["cost_limit"] = int(route["cost_limit"] * 0.9)
                    changes[f"{task_type}/{level}"] = {
                        "reason": "high success rate",
                        "success_rate": success_rate,
                        "cost_limit": f"{old_cost} → {route['cost_limit']}",
                    }

        self._save_tree()
        return changes

    def _save_tree(self) -> None:
        """Save routing tree to disk."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.tree, f, indent=2)

    def stats(self) -> Dict:
        """Get routing statistics."""
        stats = {}
        for task_type in self.tree:
            stats[task_type] = {}
            for level in self.tree[task_type]:
                route = self.tree[task_type][level]
                stats[task_type][level] = {
                    "model": route["model"],
                    "success_rate": round(route["success_rate"], 2),
                    "outcomes": route["outcomes"],
                    "cost_limit": route["cost_limit"],
                }
        return stats


if __name__ == "__main__":
    print("Testing Routing Decision Tree...\n")

    tree = RoutingDecisionTree()

    # Test routing
    print("1. Route a simple code task:")
    route = tree.route("code", 0.2)
    print(f"   Route: {route}\n")

    print("2. Route a complex design task:")
    route = tree.route("design", 0.8)
    print(f"   Route: {route}\n")

    # Record outcomes
    print("3. Record outcomes:")
    tree.record_outcome("code", 0.2, "gpt-3.5", True, 1500, 85)
    tree.record_outcome("code", 0.2, "gpt-3.5", True, 1600, 88)
    tree.record_outcome("code", 0.2, "gpt-3.5", False, 1400, 60)
    print("   Recorded 3 outcomes\n")

    # Show stats
    print("4. Routing statistics:")
    stats = tree.stats()
    print(json.dumps(stats, indent=2))
