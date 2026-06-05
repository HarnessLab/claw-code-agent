#!/usr/bin/env python3
"""
REASONING ROUTER
Routes tasks to the right model based on complexity.

Simple tasks → Claude Sonnet (fast, cheap)
Complex tasks → o1-mini (deep reasoning, edge cases)

Learns from past successes to improve routing over time.
"""

import json
import os
from typing import Dict, Tuple, List
from datetime import datetime

class ReasoningRouter:
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.routing_history = []
        self.model_performance = {
            "sonnet": {"success_rate": 0.8, "avg_chain_length": 1.5, "cost": 1.0},
            "o1-mini": {"success_rate": 0.95, "avg_chain_length": 4.5, "cost": 3.0}
        }
        self.load_history()
    
    def load_history(self):
        """Load routing history from disk."""
        history_path = os.path.join(self.latti_home, "routing_history.jsonl")
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r') as f:
                    self.routing_history = [json.loads(line) for line in f if line.strip()]
            except:
                self.routing_history = []
    
    def save_history(self):
        """Save routing history to disk."""
        history_path = os.path.join(self.latti_home, "routing_history.jsonl")
        with open(history_path, 'w') as f:
            for entry in self.routing_history:
                f.write(json.dumps(entry) + "\n")
    
    def estimate_complexity(self, task: Dict) -> float:
        """
        Estimate task complexity (0-1).
        Factors:
        - Task description length (longer = more complex)
        - Keywords indicating complexity (edge cases, multi-step, etc.)
        - Historical success rate on similar tasks
        """
        complexity = 0.0
        
        # Factor 1: Description length
        description = task.get("description", "")
        if len(description) > 500:
            complexity += 0.3
        elif len(description) > 200:
            complexity += 0.15
        
        # Factor 2: Complexity keywords
        keywords = [
            "edge case", "multi-step", "complex", "difficult", "tricky",
            "optimize", "refactor", "architecture", "design", "system",
            "debug", "troubleshoot", "performance", "security"
        ]
        keyword_count = sum(1 for kw in keywords if kw in description.lower())
        complexity += min(0.3, keyword_count * 0.1)
        
        # Factor 3: Task type
        task_type = task.get("type", "")
        if task_type in ["architecture", "design", "optimization", "debugging"]:
            complexity += 0.2
        
        return min(1.0, complexity)
    
    def route(self, task: Dict) -> Tuple[str, Dict]:
        """
        Route a task to the appropriate model.
        Returns: (model_name, routing_metadata)
        """
        complexity = self.estimate_complexity(task)
        
        # Decision threshold: if complexity > 0.5, use o1-mini
        if complexity > 0.5:
            model = "o1-mini"
            reasoning = "High complexity detected. Using o1-mini for deep reasoning."
        else:
            model = "sonnet"
            reasoning = "Low complexity. Using Sonnet for speed."
        
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task.get("id", "unknown"),
            "complexity_score": complexity,
            "model_selected": model,
            "reasoning": reasoning,
            "success": None,  # Will be filled in after execution
            "chain_length": None,
            "cost": None
        }
        
        return model, metadata
    
    def record_result(self, metadata: Dict, success: bool, chain_length: int, cost: float):
        """Record the result of a routing decision."""
        metadata["success"] = success
        metadata["chain_length"] = chain_length
        metadata["cost"] = cost
        
        self.routing_history.append(metadata)
        self.save_history()
        
        # Update model performance
        model = metadata["model_selected"]
        if model in self.model_performance:
            # Simple moving average
            current = self.model_performance[model]
            current["success_rate"] = (current["success_rate"] * 0.9) + (success * 0.1)
            current["avg_chain_length"] = (current["avg_chain_length"] * 0.9) + (chain_length * 0.1)
            current["cost"] = cost
    
    def get_routing_stats(self) -> Dict:
        """Get routing statistics."""
        if not self.routing_history:
            return {"total_routes": 0, "sonnet_success": 0, "o1_success": 0}
        
        sonnet_routes = [r for r in self.routing_history if r["model_selected"] == "sonnet"]
        o1_routes = [r for r in self.routing_history if r["model_selected"] == "o1-mini"]
        
        sonnet_success = sum(1 for r in sonnet_routes if r.get("success", False))
        o1_success = sum(1 for r in o1_routes if r.get("success", False))
        
        return {
            "total_routes": len(self.routing_history),
            "sonnet_routes": len(sonnet_routes),
            "sonnet_success_rate": (sonnet_success / len(sonnet_routes) * 100) if sonnet_routes else 0,
            "o1_routes": len(o1_routes),
            "o1_success_rate": (o1_success / len(o1_routes) * 100) if o1_routes else 0,
            "model_performance": self.model_performance
        }


class ReasoningUpgrader:
    """
    Upgrades reasoning by:
    1. Routing complex tasks to o1-mini
    2. Increasing chain length for all tasks
    3. Adding edge case detection
    """
    
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.router = ReasoningRouter(latti_home)
    
    def upgrade_task(self, task: Dict) -> Dict:
        """
        Upgrade a task with better reasoning.
        """
        # Route to appropriate model
        model, metadata = self.router.route(task)
        
        # Add reasoning instructions
        upgraded_task = task.copy()
        upgraded_task["model"] = model
        upgraded_task["routing_metadata"] = metadata
        
        # Add reasoning prompts
        if model == "o1-mini":
            upgraded_task["system_prompt"] = """You are a deep reasoning assistant. 
For this task:
1. Think through the problem step by step
2. Identify edge cases and potential issues
3. Propose multiple approaches and evaluate them
4. Explain your reasoning clearly
5. Catch and correct your own mistakes

Use your full reasoning capability."""
        else:
            upgraded_task["system_prompt"] = """You are a fast, accurate assistant.
For this task:
1. Understand the core requirement
2. Identify any edge cases
3. Provide a clear, direct solution
4. Verify your answer before responding"""
        
        return upgraded_task
    
    def report(self) -> str:
        """Generate upgrade report."""
        stats = self.router.get_routing_stats()
        
        report = []
        report.append("\n" + "="*60)
        report.append("REASONING UPGRADE REPORT")
        report.append("="*60)
        report.append(f"Total routes: {stats['total_routes']}")
        report.append(f"Sonnet routes: {stats['sonnet_routes']} ({stats['sonnet_success_rate']:.1f}% success)")
        report.append(f"o1-mini routes: {stats['o1_routes']} ({stats['o1_success_rate']:.1f}% success)")
        report.append("\nModel Performance:")
        for model, perf in stats['model_performance'].items():
            report.append(f"  {model}:")
            report.append(f"    Success rate: {perf['success_rate']:.1%}")
            report.append(f"    Avg chain length: {perf['avg_chain_length']:.1f}")
            report.append(f"    Cost: ${perf['cost']:.2f}")
        report.append("="*60)
        
        return "\n".join(report)


if __name__ == "__main__":
    # Example usage
    router = ReasoningRouter()
    
    # Test task 1: Simple
    simple_task = {
        "id": "task_1",
        "description": "Write a hello world function",
        "type": "code"
    }
    
    # Test task 2: Complex
    complex_task = {
        "id": "task_2",
        "description": "Design a distributed system architecture that handles edge cases like network partitions, Byzantine failures, and multi-step consensus protocols. Optimize for performance and security.",
        "type": "architecture"
    }
    
    print("Routing simple task...")
    model1, meta1 = router.route(simple_task)
    print(f"  Model: {model1}")
    print(f"  Complexity: {meta1['complexity_score']:.2f}")
    print(f"  Reasoning: {meta1['reasoning']}")
    
    print("\nRouting complex task...")
    model2, meta2 = router.route(complex_task)
    print(f"  Model: {model2}")
    print(f"  Complexity: {meta2['complexity_score']:.2f}")
    print(f"  Reasoning: {meta2['reasoning']}")
    
    # Simulate results
    router.record_result(meta1, success=True, chain_length=2, cost=0.01)
    router.record_result(meta2, success=True, chain_length=5, cost=0.05)
    
    upgrader = ReasoningUpgrader()
    print(upgrader.report())
