#!/usr/bin/env python3
"""
COMPLEXITY ANALYZER

Measures task complexity to predict which model tier is needed.

Factors:
  - Token count (input + expected output)
  - Nesting depth (function calls, loops, conditionals)
  - Dependencies (external libraries, APIs, databases)
  - Ambiguity (unclear requirements, edge cases)
  - Scope (lines of code, number of components)

Output: complexity score (0-1)
  0.0-0.33: simple (gpt-3.5 sufficient)
  0.33-0.67: medium (gpt-4 recommended)
  0.67-1.0: complex (gpt-4 required, may need iteration)

Usage:
  analyzer = ComplexityAnalyzer()
  complexity = analyzer.analyze(task_description, task_type="code")
  # Returns: 0.65 (medium-complex)
"""

import re
from typing import Dict, Optional


class ComplexityAnalyzer:
    """Analyzes task complexity."""

    def __init__(self):
        self.weights = {
            "token_count": 0.25,
            "nesting_depth": 0.20,
            "dependencies": 0.20,
            "ambiguity": 0.20,
            "scope": 0.15,
        }

    def analyze(
        self, task_description: str, task_type: str = "code"
    ) -> float:
        """Analyze task complexity (0-1)."""
        scores = {
            "token_count": self._score_token_count(task_description),
            "nesting_depth": self._score_nesting_depth(task_description),
            "dependencies": self._score_dependencies(task_description),
            "ambiguity": self._score_ambiguity(task_description),
            "scope": self._score_scope(task_description, task_type),
        }

        # Weighted average
        complexity = sum(
            scores[key] * self.weights[key] for key in scores
        )

        return min(1.0, max(0.0, complexity))

    def _score_token_count(self, text: str) -> float:
        """Score based on token count (rough estimate: 1 token ≈ 4 chars)."""
        token_count = len(text) / 4
        # 0 tokens = 0.0, 5000 tokens = 1.0
        return min(1.0, token_count / 5000)

    def _score_nesting_depth(self, text: str) -> float:
        """Score based on nesting depth (brackets, parentheses, indentation)."""
        # Count max nesting depth
        max_depth = 0
        current_depth = 0

        for char in text:
            if char in "([{":
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif char in ")]}":
                current_depth -= 1

        # 0 depth = 0.0, 10+ depth = 1.0
        return min(1.0, max_depth / 10)

    def _score_dependencies(self, text: str) -> float:
        """Score based on external dependencies mentioned."""
        dependency_keywords = [
            "import",
            "require",
            "api",
            "database",
            "external",
            "library",
            "package",
            "module",
            "service",
            "integration",
        ]

        count = sum(
            len(re.findall(rf"\b{kw}\b", text, re.IGNORECASE))
            for kw in dependency_keywords
        )

        # 0 deps = 0.0, 10+ deps = 1.0
        return min(1.0, count / 10)

    def _score_ambiguity(self, text: str) -> float:
        """Score based on ambiguity indicators."""
        ambiguity_keywords = [
            "maybe",
            "might",
            "could",
            "unclear",
            "not sure",
            "edge case",
            "exception",
            "error handling",
            "optional",
            "depends on",
        ]

        count = sum(
            len(re.findall(rf"\b{kw}\b", text, re.IGNORECASE))
            for kw in ambiguity_keywords
        )

        # 0 ambiguities = 0.0, 10+ ambiguities = 1.0
        return min(1.0, count / 10)

    def _score_scope(self, text: str, task_type: str) -> float:
        """Score based on scope (lines of code, components, etc.)."""
        lines = len(text.split("\n"))

        if task_type == "code":
            # 0 lines = 0.0, 500+ lines = 1.0
            return min(1.0, lines / 500)
        elif task_type == "design":
            # 0 lines = 0.0, 200+ lines = 1.0
            return min(1.0, lines / 200)
        elif task_type == "doc":
            # 0 lines = 0.0, 300+ lines = 1.0
            return min(1.0, lines / 300)
        else:
            # 0 lines = 0.0, 400+ lines = 1.0
            return min(1.0, lines / 400)

    def detailed_analysis(
        self, task_description: str, task_type: str = "code"
    ) -> Dict:
        """Return detailed complexity analysis."""
        scores = {
            "token_count": self._score_token_count(task_description),
            "nesting_depth": self._score_nesting_depth(task_description),
            "dependencies": self._score_dependencies(task_description),
            "ambiguity": self._score_ambiguity(task_description),
            "scope": self._score_scope(task_description, task_type),
        }

        complexity = sum(
            scores[key] * self.weights[key] for key in scores
        )
        complexity = min(1.0, max(0.0, complexity))

        # Determine level
        if complexity < 0.33:
            level = "simple"
        elif complexity < 0.67:
            level = "medium"
        else:
            level = "complex"

        return {
            "complexity": round(complexity, 2),
            "level": level,
            "scores": {k: round(v, 2) for k, v in scores.items()},
            "weights": self.weights,
        }


if __name__ == "__main__":
    print("Testing Complexity Analyzer...\n")

    analyzer = ComplexityAnalyzer()

    # Test 1: Simple task
    print("1. Simple task:")
    simple_task = "Write a function that adds two numbers."
    complexity = analyzer.analyze(simple_task, "code")
    print(f"   Task: {simple_task}")
    print(f"   Complexity: {complexity}\n")

    # Test 2: Medium task
    print("2. Medium task:")
    medium_task = """
    Write a REST API endpoint that:
    - Accepts a POST request with user data
    - Validates the data (email, phone, address)
    - Stores it in a database
    - Returns a JSON response with the user ID
    - Handles errors (invalid email, duplicate user, database connection failure)
    """
    complexity = analyzer.analyze(medium_task, "code")
    print(f"   Task: {medium_task.strip()}")
    print(f"   Complexity: {complexity}\n")

    # Test 3: Complex task
    print("3. Complex task:")
    complex_task = """
    Build a distributed cache system that:
    - Supports multiple backends (Redis, Memcached, in-memory)
    - Implements consistent hashing for node distribution
    - Handles node failures with automatic rebalancing
    - Supports TTL and LRU eviction policies
    - Provides monitoring and metrics
    - Integrates with existing microservices
    - Handles edge cases: network partitions, clock skew, concurrent updates
    - Maybe needs to support transactions?
    - Could integrate with Kafka for cache invalidation
    - Unclear if we need to support cross-region replication
    """
    complexity = analyzer.analyze(complex_task, "code")
    print(f"   Task: {complex_task.strip()}")
    print(f"   Complexity: {complexity}\n")

    # Test 4: Detailed analysis
    print("4. Detailed analysis of medium task:")
    analysis = analyzer.detailed_analysis(medium_task, "code")
    print(f"   Complexity: {analysis['complexity']}")
    print(f"   Level: {analysis['level']}")
    print(f"   Scores: {analysis['scores']}")
