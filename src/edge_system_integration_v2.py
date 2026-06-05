#!/usr/bin/env python3
"""
EDGE SYSTEM INTEGRATION V2
Wires Phase 5 optimization components into Phase 4 integration.

This module integrates:
1. Multi-Armed Bandit (Thompson Sampling) for model selection
2. Bayesian Optimizer for cost/quality tradeoff
3. Failure Mode Analyzer for recovery strategies

The result is a self-optimizing system that:
- Learns which models work best for different task types
- Balances cost vs quality based on constraints
- Detects failure patterns and recommends recovery
- Continuously improves routing decisions
"""

import json
import os
import sys
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from pathlib import Path

# Import Phase 4 components
sys.path.insert(0, os.path.expanduser("~/.latti"))
from reasoning_router import ReasoningRouter, ReasoningUpgrader
from edge_diagnostic import EdgeDiagnostic

# Import Phase 5 components
from multi_armed_bandit import MultiArmedBandit
from bayesian_optimizer import BayesianOptimizer
from failure_mode_analyzer import FailureModeAnalyzer


class EdgeSystemIntegrationV2:
    """
    Integrated edge system with Phase 5 optimization.
    
    Workflow:
    1. Task arrives
    2. Analyze complexity
    3. Use bandit to select model (Thompson Sampling)
    4. Execute task with selected model
    5. Record outcome in bandit
    6. If failed, use analyzer to recommend recovery
    7. Periodically optimize using Bayesian optimizer
    """
    
    def __init__(self, latti_home: str = None, models: List[str] = None):
        """
        Initialize integrated system.
        
        Args:
            latti_home: Path to .latti directory
            models: List of available models (default: gpt-3.5, gpt-4, claude)
        """
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.models = models or ["gpt-3.5", "gpt-4", "claude"]
        
        # Phase 4 components
        self.router = ReasoningRouter(latti_home)
        self.upgrader = ReasoningUpgrader(latti_home)
        self.diagnostic = EdgeDiagnostic(latti_home)
        
        # Phase 5 components
        self.bandit = MultiArmedBandit(self.models)
        self.optimizer = BayesianOptimizer()
        self.analyzer = FailureModeAnalyzer()
        
        # Tracking
        self.integration_log = []
        self.task_results = []
        self.load_state()
    
    def load_state(self):
        """Load saved state from disk."""
        # Load integration log
        log_path = os.path.join(self.latti_home, "edge_integration_v2.jsonl")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    self.integration_log = [json.loads(line) for line in f if line.strip()]
            except:
                self.integration_log = []
        
        # Load task results
        results_path = os.path.join(self.latti_home, "edge_task_results.jsonl")
        if os.path.exists(results_path):
            try:
                with open(results_path, 'r') as f:
                    self.task_results = [json.loads(line) for line in f if line.strip()]
                    # Replay results into bandit and analyzer
                    self._replay_results()
            except:
                self.task_results = []
    
    def _replay_results(self):
        """Replay task results into bandit and analyzer."""
        for result in self.task_results:
            if result.get("status") == "executed":
                # Record in bandit
                self.bandit.record_outcome(
                    model=result.get("model", "unknown"),
                    success=result.get("success", False),
                    quality=result.get("quality", 0),
                    cost=result.get("cost", 0)
                )
                
                # Record failures in analyzer
                if not result.get("success", False):
                    self.analyzer.record_failure(
                        task_id=result.get("task_id", "unknown"),
                        task_type=result.get("task_type", "unknown"),
                        model=result.get("model", "unknown"),
                        error_type=result.get("error_type", "unknown"),
                        error_message=result.get("error_message", ""),
                        cost=result.get("cost", 0),
                        quality=result.get("quality", 0),
                        regenerations=result.get("regenerations", 0)
                    )
    
    def save_state(self):
        """Save state to disk."""
        # Save integration log
        log_path = os.path.join(self.latti_home, "edge_integration_v2.jsonl")
        with open(log_path, 'w') as f:
            for entry in self.integration_log:
                f.write(json.dumps(entry) + "\n")
        
        # Save task results
        results_path = os.path.join(self.latti_home, "edge_task_results.jsonl")
        with open(results_path, 'w') as f:
            for result in self.task_results:
                f.write(json.dumps(result) + "\n")
    
    def process_task(self, task: Dict) -> Dict:
        """
        Process a task through the integrated system.
        
        Args:
            task: Task description with id, description, type
        
        Returns:
            Task with routing metadata and selected model
        """
        task_id = task.get("id", f"task_{len(self.task_results)}")
        task_type = task.get("type", "general")
        
        # Step 1: Analyze complexity
        complexity = self._analyze_complexity(task)
        
        # Step 2: Select model using Thompson Sampling
        selected_model = self.bandit.select_model()
        
        # Step 3: Upgrade task with routing metadata
        upgraded = self.upgrader.upgrade_task(task)
        upgraded["model"] = selected_model
        upgraded["routing_metadata"] = {
            "complexity_score": complexity,
            "selected_model": selected_model,
            "bandit_stats": self.bandit.get_stats(),
            "timestamp": datetime.now().isoformat()
        }
        
        # Step 4: Log the interception
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task_id,
            "task_type": task_type,
            "original_model": task.get("model", "unknown"),
            "routed_model": selected_model,
            "complexity_score": complexity,
            "status": "intercepted"
        }
        self.integration_log.append(log_entry)
        
        # Step 5: Create task result entry
        result_entry = {
            "task_id": task_id,
            "task_type": task_type,
            "model": selected_model,
            "complexity": complexity,
            "status": "intercepted",
            "timestamp": datetime.now().isoformat()
        }
        self.task_results.append(result_entry)
        
        self.save_state()
        return upgraded
    
    def _analyze_complexity(self, task: Dict) -> float:
        """
        Analyze task complexity (0-1).
        
        Args:
            task: Task description
        
        Returns:
            Complexity score (0-1)
        """
        description = task.get("description", "")
        
        # Simple heuristics
        token_count = len(description.split())
        nesting_depth = description.count("(") + description.count("[")
        has_dependencies = "depend" in description.lower()
        has_ambiguity = "?" in description
        
        # Normalize to 0-1
        complexity = min(1.0, (
            (token_count / 1000) * 0.3 +
            (nesting_depth / 10) * 0.2 +
            (0.2 if has_dependencies else 0) +
            (0.2 if has_ambiguity else 0) +
            0.1  # Base complexity
        ))
        
        return complexity
    
    def record_execution(
        self,
        task_id: str,
        model: str,
        success: bool,
        quality: int,
        cost: int,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        regenerations: int = 0
    ) -> None:
        """
        Record task execution result.
        
        Args:
            task_id: Task identifier
            model: Model used
            success: Whether task succeeded
            quality: Quality score (0-100)
            cost: Cost in tokens
            error_type: Type of error (if failed)
            error_message: Error message (if failed)
            regenerations: Number of regeneration attempts
        """
        # Find task result entry
        result_entry = None
        for entry in self.task_results:
            if entry["task_id"] == task_id:
                result_entry = entry
                break
        
        if result_entry is None:
            result_entry = {
                "task_id": task_id,
                "model": model,
                "status": "executed",
                "timestamp": datetime.now().isoformat()
            }
            self.task_results.append(result_entry)
        
        # Update result entry
        result_entry["status"] = "executed"
        result_entry["success"] = success
        result_entry["quality"] = quality
        result_entry["cost"] = cost
        result_entry["error_type"] = error_type
        result_entry["error_message"] = error_message
        result_entry["regenerations"] = regenerations
        result_entry["execution_time"] = datetime.now().isoformat()
        
        # Record in bandit
        self.bandit.record_outcome(
            model=model,
            success=success,
            quality=quality,
            cost=cost
        )
        
        # Record in optimizer
        self.optimizer.add_observation(
            cost=cost,
            quality=quality
        )
        
        # Record failures in analyzer
        if not success:
            task_type = result_entry.get("task_type", "unknown")
            self.analyzer.record_failure(
                task_id=task_id,
                task_type=task_type,
                model=model,
                error_type=error_type or "unknown",
                error_message=error_message or "",
                cost=cost,
                quality=quality,
                regenerations=regenerations
            )
        
        self.save_state()
    
    def get_recovery_strategy(self, task_id: str) -> Tuple[str, str]:
        """
        Get recovery strategy for a failed task.
        
        Args:
            task_id: Task identifier
        
        Returns:
            (strategy, recommendation)
        """
        # Find task result
        result_entry = None
        for entry in self.task_results:
            if entry["task_id"] == task_id:
                result_entry = entry
                break
        
        if result_entry is None or result_entry.get("success", True):
            return "none", "Task succeeded or not found"
        
        # Find failure in analyzer
        failure = None
        for f in self.analyzer.failures:
            if f.task_id == task_id:
                failure = f
                break
        
        if failure is None:
            return "unknown", "Failure not found in analyzer"
        
        model = result_entry.get("model", "unknown")
        
        # Get analyzer recommendation
        strategy, recommendation = self.analyzer.recommend_recovery(failure)
        
        # If strategy is "switch_model", use bandit to recommend
        if strategy == "switch_model":
            should_switch, reason, recommended = self.bandit.recommend_switch(model)
            if should_switch:
                return "switch_model", f"Switch to {recommended}: {reason}"
            else:
                return "regenerate", "No better model available, try regenerating"
        
        return strategy, recommendation
    
    def optimize(self) -> Dict:
        """
        Run periodic optimization.
        
        Returns:
            Optimization results
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "bandit_stats": self.bandit.get_stats(),
            "optimizer_frontier": self.optimizer.get_pareto_frontier(),
            "analyzer_stats": self.analyzer.get_stats(),
            "recommendations": []
        }
        
        # Bandit recommendations
        for model in self.models:
            should_switch, reason, recommended = self.bandit.recommend_switch(model)
            if should_switch:
                results["recommendations"].append({
                    "type": "model_switch",
                    "from": model,
                    "to": recommended,
                    "reason": reason
                })
        
        # Optimizer recommendations
        frontier = self.optimizer.get_pareto_frontier()
        if frontier:
            results["recommendations"].append({
                "type": "pareto_frontier",
                "frontier": frontier,
                "reason": "Cost/quality tradeoff options"
            })
        
        # Analyzer recommendations
        analyzer_recs = self.analyzer.get_recommendations()
        for key, rec in analyzer_recs.items():
            results["recommendations"].append({
                "type": "failure_analysis",
                "key": key,
                "issue": rec.get("issue", ""),
                "action": rec.get("action", "")
            })
        
        return results
    
    def get_stats(self) -> Dict:
        """Get comprehensive statistics."""
        successful = sum(1 for r in self.task_results if r.get("success", False))
        total = len(self.task_results)
        
        return {
            "total_tasks": total,
            "successful_tasks": successful,
            "success_rate": (successful / total * 100) if total > 0 else 0,
            "avg_quality": (sum(r.get("quality", 0) for r in self.task_results) / total) if total > 0 else 0,
            "total_cost": sum(r.get("cost", 0) for r in self.task_results),
            "bandit_stats": self.bandit.get_stats(),
            "analyzer_stats": self.analyzer.get_stats(),
            "optimizer_frontier": self.optimizer.get_pareto_frontier()
        }
    
    def report(self) -> str:
        """Generate comprehensive report."""
        stats = self.get_stats()
        
        lines = []
        lines.append("\n" + "="*70)
        lines.append("EDGE SYSTEM INTEGRATION V2 REPORT")
        lines.append("="*70)
        
        # Overall stats
        lines.append("\nOVERALL PERFORMANCE:")
        lines.append(f"  Total tasks: {stats['total_tasks']}")
        lines.append(f"  Successful: {stats['successful_tasks']} ({stats['success_rate']:.1f}%)")
        lines.append(f"  Avg quality: {stats['avg_quality']:.1f}/100")
        lines.append(f"  Total cost: {stats['total_cost']} tokens")
        
        # Bandit stats
        lines.append("\nMODEL SELECTION (THOMPSON SAMPLING):")
        for model, stat in stats['bandit_stats'].items():
            lines.append(f"  {model}:")
            lines.append(f"    Success rate: {stat['success_rate']:.1%}")
            lines.append(f"    Avg quality: {stat['avg_quality']:.0f}")
            lines.append(f"    Avg cost: {stat['avg_cost']:.0f} tokens")
            lines.append(f"    Cost per quality: {stat['cost_per_quality']:.2f}")
        
        # Failure patterns
        lines.append("\nFAILURE ANALYSIS:")
        analyzer_stats = stats.get('analyzer_stats', {})
        most_common = analyzer_stats.get('most_common_errors', [])
        if most_common:
            for error_type, count in most_common:
                lines.append(f"  {error_type}: {count} occurrences")
        else:
            lines.append("  No failures recorded")
        
        # Pareto frontier
        lines.append("\nCOST/QUALITY TRADEOFF (PARETO FRONTIER):")
        frontier = stats['optimizer_frontier']
        if frontier:
            for point in frontier:
                lines.append(f"  Cost: {point['cost']:.0f}, Quality: {point['quality']:.0f}")
        else:
            lines.append("  Insufficient data for frontier")
        
        lines.append("="*70)
        return "\n".join(lines)


class EdgeSystemHookV2:
    """
    Hook for integration with agent runtime.
    Provides simple interface for Phase 5.5 integration.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.integration = EdgeSystemIntegrationV2()
        return cls._instance
    
    def process_task(self, task: Dict) -> Dict:
        """Process a task through the integrated system."""
        return self.integration.process_task(task)
    
    def record_result(
        self,
        task_id: str,
        model: str,
        success: bool,
        quality: int,
        cost: int,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        regenerations: int = 0
    ) -> None:
        """Record task execution result."""
        self.integration.record_execution(
            task_id=task_id,
            model=model,
            success=success,
            quality=quality,
            cost=cost,
            error_type=error_type,
            error_message=error_message,
            regenerations=regenerations
        )
    
    def get_recovery_strategy(self, task_id: str) -> Tuple[str, str]:
        """Get recovery strategy for failed task."""
        return self.integration.get_recovery_strategy(task_id)
    
    def optimize(self) -> Dict:
        """Run periodic optimization."""
        return self.integration.optimize()
    
    def get_stats(self) -> Dict:
        """Get statistics."""
        return self.integration.get_stats()
    
    def report(self) -> str:
        """Get report."""
        return self.integration.report()


# Global hook instance
_edge_hook_v2 = None

def get_edge_hook_v2() -> EdgeSystemHookV2:
    """Get the global edge system hook V2."""
    global _edge_hook_v2
    if _edge_hook_v2 is None:
        _edge_hook_v2 = EdgeSystemHookV2()
    return _edge_hook_v2


if __name__ == "__main__":
    # Example usage
    hook = get_edge_hook_v2()
    
    # Simulate tasks
    tasks = [
        {
            "id": "task_1",
            "description": "Design a distributed cache system with consistency guarantees",
            "type": "architecture"
        },
        {
            "id": "task_2",
            "description": "Write a simple REST API endpoint",
            "type": "code"
        },
        {
            "id": "task_3",
            "description": "Analyze the Byzantine Generals Problem and propose solutions",
            "type": "analysis"
        }
    ]
    
    print("Processing tasks through integrated system...\n")
    
    for task in tasks:
        print(f"Task: {task['id']}")
        upgraded = hook.process_task(task)
        print(f"  Routed to: {upgraded['model']}")
        print(f"  Complexity: {upgraded['routing_metadata']['complexity_score']:.2f}")
        
        # Simulate execution
        import random
        success = random.random() > 0.2
        quality = random.randint(60, 95) if success else random.randint(20, 50)
        cost = random.randint(1000, 4000)
        
        hook.record_result(
            task_id=task['id'],
            model=upgraded['model'],
            success=success,
            quality=quality,
            cost=cost,
            error_type="syntax" if not success else None,
            error_message="Invalid syntax" if not success else None
        )
        
        print(f"  Result: {'✓' if success else '✗'} (quality: {quality}, cost: {cost})")
        print()
    
    # Run optimization
    print("Running optimization...\n")
    opt_results = hook.optimize()
    print(f"Recommendations: {len(opt_results['recommendations'])}")
    for rec in opt_results['recommendations']:
        print(f"  - {rec['type']}: {rec['reason']}")
    
    # Print report
    print(hook.report())
