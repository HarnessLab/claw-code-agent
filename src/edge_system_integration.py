#!/usr/bin/env python3
"""
EDGE SYSTEM INTEGRATION
Wires the reasoning router into the agent loop.

This module:
1. Intercepts tasks before they reach the LLM
2. Routes them to the appropriate model (Sonnet or o1-mini)
3. Records results for continuous improvement
4. Measures impact on reasoning depth, artifact quality, routing accuracy
"""

import json
import os
import sys
from typing import Dict, Tuple, Optional
from datetime import datetime
from pathlib import Path

# Import the reasoning router
sys.path.insert(0, os.path.expanduser("~/.latti"))
from reasoning_router import ReasoningRouter, ReasoningUpgrader
from edge_diagnostic import EdgeDiagnostic


class EdgeSystemIntegration:
    """
    Main integration point for the edge system.
    Sits between the user request and the LLM call.
    """
    
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.router = ReasoningRouter(latti_home)
        self.upgrader = ReasoningUpgrader(latti_home)
        self.diagnostic = EdgeDiagnostic(latti_home)
        self.integration_log = []
        self.load_log()
    
    def load_log(self):
        """Load integration log from disk."""
        log_path = os.path.join(self.latti_home, "edge_integration.jsonl")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    self.integration_log = [json.loads(line) for line in f if line.strip()]
            except:
                self.integration_log = []
    
    def save_log(self):
        """Save integration log to disk."""
        log_path = os.path.join(self.latti_home, "edge_integration.jsonl")
        with open(log_path, 'w') as f:
            for entry in self.integration_log:
                f.write(json.dumps(entry) + "\n")
    
    def intercept_task(self, task: Dict) -> Dict:
        """
        Intercept a task and upgrade it with better routing.
        
        Args:
            task: The original task from the user
        
        Returns:
            Upgraded task with model routing and reasoning instructions
        """
        # Upgrade the task
        upgraded = self.upgrader.upgrade_task(task)
        
        # Log the interception
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task.get("id", "unknown"),
            "original_model": task.get("model", "unknown"),
            "routed_model": upgraded.get("model", "unknown"),
            "complexity_score": upgraded.get("routing_metadata", {}).get("complexity_score", 0),
            "status": "intercepted"
        }
        self.integration_log.append(log_entry)
        self.save_log()
        
        return upgraded
    
    def record_execution(self, task_id: str, model: str, success: bool, 
                        chain_length: int, cost: float, reasoning_depth: int = 0):
        """
        Record the execution of a task.
        
        Args:
            task_id: The task ID
            model: The model used (sonnet or o1-mini)
            success: Whether the task succeeded
            chain_length: Number of reasoning steps
            cost: Cost in dollars
            reasoning_depth: Depth of reasoning (0-100)
        """
        # Find the log entry for this task
        for entry in self.integration_log:
            if entry["task_id"] == task_id:
                entry["status"] = "executed"
                entry["success"] = success
                entry["chain_length"] = chain_length
                entry["cost"] = cost
                entry["reasoning_depth"] = reasoning_depth
                entry["execution_time"] = datetime.now().isoformat()
                break
        
        self.save_log()
        
        # Update router performance
        routing_metadata = {
            "task_id": task_id,
            "model_selected": model,
            "complexity_score": 0.5  # Will be updated from log
        }
        self.router.record_result(routing_metadata, success, chain_length, cost)
    
    def should_upgrade_reasoning(self) -> bool:
        """
        Determine if reasoning needs to be upgraded.
        Returns True if reasoning depth is still low.
        """
        results = self.diagnostic.run()
        reasoning_score = results["reasoning_depth"].get("score", 0)
        return reasoning_score < 50
    
    def get_integration_stats(self) -> Dict:
        """Get integration statistics."""
        if not self.integration_log:
            return {"total_tasks": 0, "success_rate": 0, "avg_chain_length": 0}
        
        successful = sum(1 for e in self.integration_log if e.get("success", False))
        total_chain_length = sum(e.get("chain_length", 0) for e in self.integration_log)
        
        return {
            "total_tasks": len(self.integration_log),
            "successful_tasks": successful,
            "success_rate": (successful / len(self.integration_log) * 100) if self.integration_log else 0,
            "avg_chain_length": (total_chain_length / len(self.integration_log)) if self.integration_log else 0,
            "total_cost": sum(e.get("cost", 0) for e in self.integration_log),
            "routing_stats": self.router.get_routing_stats()
        }
    
    def report(self) -> str:
        """Generate integration report."""
        stats = self.get_integration_stats()
        
        report = []
        report.append("\n" + "="*60)
        report.append("EDGE SYSTEM INTEGRATION REPORT")
        report.append("="*60)
        report.append(f"Total tasks: {stats['total_tasks']}")
        report.append(f"Successful: {stats['successful_tasks']} ({stats['success_rate']:.1f}%)")
        report.append(f"Avg chain length: {stats['avg_chain_length']:.1f}")
        report.append(f"Total cost: ${stats['total_cost']:.2f}")
        report.append("\nRouting Stats:")
        routing = stats['routing_stats']
        report.append(f"  Sonnet routes: {routing['sonnet_routes']} ({routing['sonnet_success_rate']:.1f}% success)")
        report.append(f"  o1-mini routes: {routing['o1_routes']} ({routing['o1_success_rate']:.1f}% success)")
        report.append("="*60)
        
        return "\n".join(report)


class EdgeSystemHook:
    """
    Hook that can be called from the agent runtime.
    Provides a simple interface for integration.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.integration = EdgeSystemIntegration()
        return cls._instance
    
    def process_task(self, task: Dict) -> Dict:
        """Process a task through the edge system."""
        return self.integration.intercept_task(task)
    
    def record_result(self, task_id: str, model: str, success: bool, 
                     chain_length: int, cost: float):
        """Record the result of a task execution."""
        self.integration.record_execution(task_id, model, success, chain_length, cost)
    
    def get_stats(self) -> Dict:
        """Get current statistics."""
        return self.integration.get_integration_stats()
    
    def report(self) -> str:
        """Get integration report."""
        return self.integration.report()


# Global hook instance
_edge_hook = None

def get_edge_hook() -> EdgeSystemHook:
    """Get the global edge system hook."""
    global _edge_hook
    if _edge_hook is None:
        _edge_hook = EdgeSystemHook()
    return _edge_hook


if __name__ == "__main__":
    # Example usage
    hook = get_edge_hook()
    
    # Simulate a task
    task = {
        "id": "example_task_1",
        "description": "Design a distributed system that handles Byzantine failures",
        "type": "architecture"
    }
    
    print("Processing task through edge system...")
    upgraded = hook.process_task(task)
    print(f"  Original model: {task.get('model', 'unknown')}")
    print(f"  Routed model: {upgraded.get('model', 'unknown')}")
    print(f"  Complexity: {upgraded.get('routing_metadata', {}).get('complexity_score', 0):.2f}")
    
    # Simulate execution
    print("\nRecording execution result...")
    hook.record_result("example_task_1", "o1-mini", True, 5, 0.05)
    
    print(hook.report())
