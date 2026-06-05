#!/usr/bin/env python3
"""
LATTI EDGE DIAGNOSTIC
Measures three dimensions of system performance:
1. Reasoning depth (chain length, complexity, edge case handling)
2. Artifact quality (code runs, designs are implementable, no rework needed)
3. Routing accuracy (right tool/model for the task)

Runs on last N tasks and identifies the bottleneck.
"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

class EdgeDiagnostic:
    def __init__(self, latti_home: str = None):
        self.latti_home = latti_home or os.path.expanduser("~/.latti")
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "reasoning_depth": {},
            "artifact_quality": {},
            "routing_accuracy": {},
            "bottleneck": None,
            "recommendation": None
        }
    
    def measure_reasoning_depth(self, task_log_path: str = None) -> Dict:
        """
        Measure reasoning depth from agent execution logs.
        Metrics:
        - Chain length (number of reasoning steps)
        - Tool calls (complexity of reasoning)
        - Self-corrections (did it catch its own errors?)
        - Edge case handling (did it anticipate problems?)
        """
        if task_log_path is None:
            task_log_path = os.path.join(self.latti_home, "agent_runtime_execution_log.jsonl")
        
        if not os.path.exists(task_log_path):
            return {"status": "no_data", "score": 0}
        
        metrics = {
            "avg_chain_length": 0,
            "avg_tool_calls": 0,
            "self_corrections": 0,
            "edge_case_detections": 0,
            "total_tasks": 0,
            "score": 0
        }
        
        try:
            with open(task_log_path, 'r') as f:
                tasks = [json.loads(line) for line in f if line.strip()]
            
            if not tasks:
                return {"status": "no_tasks", "score": 0}
            
            # Take last 5 tasks
            recent_tasks = tasks[-5:]
            metrics["total_tasks"] = len(recent_tasks)
            
            total_chain_length = 0
            total_tool_calls = 0
            
            for task in recent_tasks:
                # Chain length = number of turns
                chain_length = task.get("turns", 1)
                total_chain_length += chain_length
                
                # Tool calls = complexity
                tool_calls = len(task.get("tools_called", []))
                total_tool_calls += tool_calls
                
                # Self-corrections = did it fix itself?
                if task.get("corrections_made", 0) > 0:
                    metrics["self_corrections"] += 1
                
                # Edge case detection = did it anticipate problems?
                if task.get("edge_cases_handled", 0) > 0:
                    metrics["edge_case_detections"] += 1
            
            metrics["avg_chain_length"] = total_chain_length / len(recent_tasks) if recent_tasks else 0
            metrics["avg_tool_calls"] = total_tool_calls / len(recent_tasks) if recent_tasks else 0
            
            # Score: 0-100
            # Ideal: chain_length > 3, tool_calls > 2, self_corrections > 0, edge_cases > 0
            score = 0
            if metrics["avg_chain_length"] > 3:
                score += 25
            if metrics["avg_tool_calls"] > 2:
                score += 25
            if metrics["self_corrections"] > 0:
                score += 25
            if metrics["edge_case_detections"] > 0:
                score += 25
            
            metrics["score"] = score
            return metrics
        
        except Exception as e:
            return {"status": "error", "error": str(e), "score": 0}
    
    def measure_artifact_quality(self, artifact_log_path: str = None) -> Dict:
        """
        Measure artifact quality.
        Metrics:
        - Pass rate (code runs, designs work)
        - Rework rate (how many times did user need to fix it?)
        - Completeness (did it include all necessary parts?)
        - Usability (can user actually use it?)
        """
        if artifact_log_path is None:
            artifact_log_path = os.path.join(self.latti_home, "loose_ends.jsonl")
        
        if not os.path.exists(artifact_log_path):
            return {"status": "no_data", "score": 0}
        
        metrics = {
            "pass_rate": 0,
            "rework_rate": 0,
            "completeness": 0,
            "usability": 0,
            "total_artifacts": 0,
            "score": 0
        }
        
        try:
            with open(artifact_log_path, 'r') as f:
                artifacts = [json.loads(line) for line in f if line.strip()]
            
            if not artifacts:
                return {"status": "no_artifacts", "score": 0}
            
            # Take last 5 artifacts
            recent_artifacts = artifacts[-5:]
            metrics["total_artifacts"] = len(recent_artifacts)
            
            passed = 0
            reworks = 0
            complete = 0
            usable = 0
            
            for artifact in recent_artifacts:
                # Pass rate: did it work on first try?
                if artifact.get("status") == "complete":
                    passed += 1
                
                # Rework rate: how many iterations?
                reworks += artifact.get("iterations", 1) - 1
                
                # Completeness: all required sections present?
                if artifact.get("completeness_score", 0) > 0.8:
                    complete += 1
                
                # Usability: user could actually use it?
                if artifact.get("user_feedback", {}).get("usable", False):
                    usable += 1
            
            metrics["pass_rate"] = (passed / len(recent_artifacts) * 100) if recent_artifacts else 0
            metrics["rework_rate"] = (reworks / len(recent_artifacts)) if recent_artifacts else 0
            metrics["completeness"] = (complete / len(recent_artifacts) * 100) if recent_artifacts else 0
            metrics["usability"] = (usable / len(recent_artifacts) * 100) if recent_artifacts else 0
            
            # Score: 0-100
            # Ideal: pass_rate > 80%, rework_rate < 1, completeness > 80%, usability > 80%
            score = 0
            if metrics["pass_rate"] > 80:
                score += 25
            if metrics["rework_rate"] < 1:
                score += 25
            if metrics["completeness"] > 80:
                score += 25
            if metrics["usability"] > 80:
                score += 25
            
            metrics["score"] = score
            return metrics
        
        except Exception as e:
            return {"status": "error", "error": str(e), "score": 0}
    
    def measure_routing_accuracy(self, routing_log_path: str = None) -> Dict:
        """
        Measure routing accuracy.
        Metrics:
        - Model selection accuracy (did it pick the right model?)
        - Tool selection accuracy (did it pick the right tool?)
        - Fallback rate (how often did it need to retry?)
        - Cost efficiency (did it use the cheapest option that works?)
        """
        if routing_log_path is None:
            routing_log_path = os.path.join(self.latti_home, "agent_runtime_execution_log.jsonl")
        
        if not os.path.exists(routing_log_path):
            return {"status": "no_data", "score": 0}
        
        metrics = {
            "model_accuracy": 0,
            "tool_accuracy": 0,
            "fallback_rate": 0,
            "cost_efficiency": 0,
            "total_routes": 0,
            "score": 0
        }
        
        try:
            with open(routing_log_path, 'r') as f:
                routes = [json.loads(line) for line in f if line.strip()]
            
            if not routes:
                return {"status": "no_routes", "score": 0}
            
            # Take last 5 routes
            recent_routes = routes[-5:]
            metrics["total_routes"] = len(recent_routes)
            
            correct_models = 0
            correct_tools = 0
            fallbacks = 0
            efficient = 0
            
            for route in recent_routes:
                # Model accuracy: did it succeed on first try?
                if route.get("model_success", False):
                    correct_models += 1
                
                # Tool accuracy: did the tool work?
                if route.get("tool_success", False):
                    correct_tools += 1
                
                # Fallback rate: did it need to retry?
                if route.get("fallbacks", 0) > 0:
                    fallbacks += 1
                
                # Cost efficiency: was it the cheapest option?
                if route.get("cost_efficient", False):
                    efficient += 1
            
            metrics["model_accuracy"] = (correct_models / len(recent_routes) * 100) if recent_routes else 0
            metrics["tool_accuracy"] = (correct_tools / len(recent_routes) * 100) if recent_routes else 0
            metrics["fallback_rate"] = (fallbacks / len(recent_routes)) if recent_routes else 0
            metrics["cost_efficiency"] = (efficient / len(recent_routes) * 100) if recent_routes else 0
            
            # Score: 0-100
            # Ideal: model_accuracy > 80%, tool_accuracy > 80%, fallback_rate < 1, cost_efficiency > 80%
            score = 0
            if metrics["model_accuracy"] > 80:
                score += 25
            if metrics["tool_accuracy"] > 80:
                score += 25
            if metrics["fallback_rate"] < 1:
                score += 25
            if metrics["cost_efficiency"] > 80:
                score += 25
            
            metrics["score"] = score
            return metrics
        
        except Exception as e:
            return {"status": "error", "error": str(e), "score": 0}
    
    def identify_bottleneck(self) -> Tuple[str, str]:
        """
        Identify which dimension is the bottleneck.
        Returns: (bottleneck_name, recommendation)
        """
        reasoning_score = self.results["reasoning_depth"].get("score", 0)
        artifact_score = self.results["artifact_quality"].get("score", 0)
        routing_score = self.results["routing_accuracy"].get("score", 0)
        
        scores = {
            "reasoning_depth": reasoning_score,
            "artifact_quality": artifact_score,
            "routing_accuracy": routing_score
        }
        
        bottleneck = min(scores, key=scores.get)
        
        recommendations = {
            "reasoning_depth": "Switch to o1-mini for complex tasks. Increase chain length. Add edge case detection.",
            "artifact_quality": "Add artifact validation. Run code before emitting. Iterate until passing.",
            "routing_accuracy": "Build decision tree from past successes. Learn which model/tool works best for each task type."
        }
        
        return bottleneck, recommendations.get(bottleneck, "Unknown")
    
    def run(self) -> Dict:
        """Run full diagnostic."""
        print("[LATTI EDGE DIAGNOSTIC] Starting...")
        
        print("  Measuring reasoning depth...")
        self.results["reasoning_depth"] = self.measure_reasoning_depth()
        
        print("  Measuring artifact quality...")
        self.results["artifact_quality"] = self.measure_artifact_quality()
        
        print("  Measuring routing accuracy...")
        self.results["routing_accuracy"] = self.measure_routing_accuracy()
        
        print("  Identifying bottleneck...")
        bottleneck, recommendation = self.identify_bottleneck()
        self.results["bottleneck"] = bottleneck
        self.results["recommendation"] = recommendation
        
        return self.results
    
    def report(self) -> str:
        """Generate human-readable report."""
        report = []
        report.append("\n" + "="*60)
        report.append("LATTI EDGE DIAGNOSTIC REPORT")
        report.append("="*60)
        report.append(f"Timestamp: {self.results['timestamp']}\n")
        
        # Reasoning Depth
        rd = self.results["reasoning_depth"]
        report.append("REASONING DEPTH")
        report.append(f"  Score: {rd.get('score', 0)}/100")
        report.append(f"  Avg chain length: {rd.get('avg_chain_length', 0):.1f}")
        report.append(f"  Avg tool calls: {rd.get('avg_tool_calls', 0):.1f}")
        report.append(f"  Self-corrections: {rd.get('self_corrections', 0)}")
        report.append(f"  Edge case detections: {rd.get('edge_case_detections', 0)}\n")
        
        # Artifact Quality
        aq = self.results["artifact_quality"]
        report.append("ARTIFACT QUALITY")
        report.append(f"  Score: {aq.get('score', 0)}/100")
        report.append(f"  Pass rate: {aq.get('pass_rate', 0):.1f}%")
        report.append(f"  Rework rate: {aq.get('rework_rate', 0):.1f} iterations")
        report.append(f"  Completeness: {aq.get('completeness', 0):.1f}%")
        report.append(f"  Usability: {aq.get('usability', 0):.1f}%\n")
        
        # Routing Accuracy
        ra = self.results["routing_accuracy"]
        report.append("ROUTING ACCURACY")
        report.append(f"  Score: {ra.get('score', 0)}/100")
        report.append(f"  Model accuracy: {ra.get('model_accuracy', 0):.1f}%")
        report.append(f"  Tool accuracy: {ra.get('tool_accuracy', 0):.1f}%")
        report.append(f"  Fallback rate: {ra.get('fallback_rate', 0):.1f}")
        report.append(f"  Cost efficiency: {ra.get('cost_efficiency', 0):.1f}%\n")
        
        # Bottleneck
        report.append("BOTTLENECK IDENTIFIED")
        report.append(f"  {self.results['bottleneck'].upper()}")
        report.append(f"  Recommendation: {self.results['recommendation']}\n")
        
        report.append("="*60)
        
        return "\n".join(report)


if __name__ == "__main__":
    diagnostic = EdgeDiagnostic()
    results = diagnostic.run()
    print(diagnostic.report())
    
    # Save results
    output_path = os.path.join(diagnostic.latti_home, "edge_diagnostic_results.json")
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
