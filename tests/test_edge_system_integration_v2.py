"""
Test suite for EdgeSystemIntegrationV2.

Tests the integration of Phase 5 optimization components (bandit, optimizer, analyzer)
with Phase 4 edge system components (router, upgrader, diagnostic).
"""

import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Import the integration module
import sys
sys.path.insert(0, os.path.expanduser("~/.latti"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from edge_system_integration_v2 import (
    EdgeSystemIntegrationV2,
    EdgeSystemHookV2,
    get_edge_hook_v2
)


class TestEdgeSystemIntegrationV2:
    """Test EdgeSystemIntegrationV2 core functionality."""
    
    @pytest.fixture
    def temp_latti_home(self):
        """Create a temporary .latti directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def integration(self, temp_latti_home):
        """Create an EdgeSystemIntegrationV2 instance for testing."""
        return EdgeSystemIntegrationV2(latti_home=temp_latti_home)
    
    def test_initialization(self, integration):
        """Test that EdgeSystemIntegrationV2 initializes correctly."""
        assert integration is not None
        assert integration.router is not None
        assert integration.upgrader is not None
        assert integration.diagnostic is not None
        assert integration.bandit is not None
        assert integration.optimizer is not None
        assert integration.analyzer is not None
        assert integration.models == ["gpt-3.5", "gpt-4", "claude"]
    
    def test_custom_models(self, temp_latti_home):
        """Test initialization with custom models."""
        custom_models = ["model-a", "model-b", "model-c"]
        integration = EdgeSystemIntegrationV2(
            latti_home=temp_latti_home,
            models=custom_models
        )
        assert integration.models == custom_models
    
    def test_process_task_routing(self, integration):
        """Test that tasks are routed to appropriate models."""
        task = {
            "id": "task_1",
            "description": "Write a simple function",
            "type": "code"
        }
        
        result = integration.process_task(task)
        
        assert result is not None
        assert "model" in result
        assert result["model"] in integration.models
        assert "routing_metadata" in result
        assert "complexity_score" in result["routing_metadata"]
    
    def test_process_task_complexity_scoring(self, integration):
        """Test that complexity scoring works correctly."""
        simple_task = {
            "id": "simple",
            "description": "Print hello world",
            "type": "code"
        }
        
        complex_task = {
            "id": "complex",
            "description": "Design a distributed consensus algorithm with Byzantine fault tolerance",
            "type": "architecture"
        }
        
        simple_result = integration.process_task(simple_task)
        complex_result = integration.process_task(complex_task)
        
        simple_complexity = simple_result["routing_metadata"]["complexity_score"]
        complex_complexity = complex_result["routing_metadata"]["complexity_score"]
        
        # Complex task should have higher complexity score
        assert complex_complexity >= simple_complexity
    
    def test_record_execution_success(self, integration):
        """Test recording successful task execution."""
        task_id = "task_success"
        model = "gpt-4"
        
        integration.record_execution(
            task_id=task_id,
            model=model,
            success=True,
            quality=85,
            cost=2000,
            error_type=None,
            error_message=None,
            regenerations=0
        )
        
        # Verify the result was recorded
        assert len(integration.task_results) > 0
        last_result = integration.task_results[-1]
        assert last_result["task_id"] == task_id
        assert last_result["model"] == model
        assert last_result["success"] is True
        assert last_result["quality"] == 85
        assert last_result["cost"] == 2000
    
    def test_record_execution_failure(self, integration):
        """Test recording failed task execution."""
        task_id = "task_failure"
        model = "gpt-3.5"
        
        integration.record_execution(
            task_id=task_id,
            model=model,
            success=False,
            quality=30,
            cost=1000,
            error_type="timeout",
            error_message="Task exceeded time limit",
            regenerations=2
        )
        
        # Verify the result was recorded
        assert len(integration.task_results) > 0
        last_result = integration.task_results[-1]
        assert last_result["task_id"] == task_id
        assert last_result["success"] is False
        assert last_result["error_type"] == "timeout"
        assert last_result["regenerations"] == 2
    
    def test_bandit_learning(self, integration):
        """Test that the bandit learns from outcomes."""
        # Record multiple outcomes for different models
        outcomes = [
            ("gpt-3.5", True, 80, 1500),
            ("gpt-3.5", True, 85, 1600),
            ("gpt-4", True, 90, 2500),
            ("gpt-4", False, 20, 2000),
            ("claude", True, 75, 1800),
            ("claude", False, 30, 1700),
        ]
        
        for i, (model, success, quality, cost) in enumerate(outcomes):
            integration.record_execution(
                task_id=f"task_{i}",
                model=model,
                success=success,
                quality=quality,
                cost=cost
            )
        
        # Get bandit stats
        stats = integration.get_stats()
        assert "bandit_stats" in stats
        
        # Verify that gpt-3.5 has the best success rate
        bandit_stats = stats["bandit_stats"]
        gpt35_success = bandit_stats["gpt-3.5"]["success_rate"]
        gpt4_success = bandit_stats["gpt-4"]["success_rate"]
        claude_success = bandit_stats["claude"]["success_rate"]
        
        assert gpt35_success == 1.0  # 2/2 successes
        assert gpt4_success == 0.5   # 1/2 successes
        assert claude_success == 0.5  # 1/2 successes
    
    def test_optimizer_frontier(self, integration):
        """Test that the optimizer computes Pareto frontier."""
        # Record outcomes with different cost/quality tradeoffs
        outcomes = [
            ("gpt-3.5", True, 70, 1000),
            ("gpt-4", True, 90, 3000),
            ("claude", True, 80, 2000),
        ]
        
        for i, (model, success, quality, cost) in enumerate(outcomes):
            integration.record_execution(
                task_id=f"task_{i}",
                model=model,
                success=success,
                quality=quality,
                cost=cost
            )
        
        # Get optimization results
        opt_results = integration.optimize()
        assert "optimizer_frontier" in opt_results
        
        # Frontier should have at least one point
        frontier = opt_results["optimizer_frontier"]
        assert len(frontier) > 0
        
        # Each frontier point should have cost, quality, and efficiency
        for point in frontier:
            assert "cost" in point
            assert "quality" in point
            assert "efficiency" in point
    
    def test_failure_mode_analysis(self, integration):
        """Test that the analyzer detects failure patterns."""
        # Record multiple failures with the same error type
        for i in range(3):
            integration.record_execution(
                task_id=f"task_timeout_{i}",
                model="gpt-3.5",
                success=False,
                quality=20,
                cost=1000,
                error_type="timeout",
                error_message="Task exceeded time limit"
            )
        
        # Record some successes
        for i in range(2):
            integration.record_execution(
                task_id=f"task_success_{i}",
                model="gpt-3.5",
                success=True,
                quality=85,
                cost=1500
            )
        
        # Get stats
        stats = integration.get_stats()
        assert "analyzer_stats" in stats
        
        analyzer_stats = stats["analyzer_stats"]
        assert analyzer_stats["total_failures"] == 3
        assert "most_common_errors" in analyzer_stats
        
        # Timeout should be the most common error
        most_common = analyzer_stats["most_common_errors"][0]
        assert most_common[0] == "timeout"
        assert most_common[1] == 3
    
    def test_recovery_strategy(self, integration):
        """Test that recovery strategies are recommended."""
        # Record a failure
        integration.record_execution(
            task_id="task_failed",
            model="gpt-3.5",
            success=False,
            quality=20,
            cost=1000,
            error_type="timeout",
            error_message="Task exceeded time limit"
        )
        
        # Get recovery strategy
        strategy_type, strategy_desc = integration.get_recovery_strategy("task_failed")
        
        assert strategy_type is not None
        assert strategy_desc is not None
        assert isinstance(strategy_type, str)
        assert isinstance(strategy_desc, str)
    
    def test_state_persistence(self, temp_latti_home):
        """Test that state is persisted and loaded correctly."""
        # Create first integration instance and record some data
        integration1 = EdgeSystemIntegrationV2(latti_home=temp_latti_home)
        
        for i in range(3):
            integration1.record_execution(
                task_id=f"task_{i}",
                model="gpt-4",
                success=True,
                quality=85,
                cost=2000
            )
        
        # Create second instance - should load the saved state
        integration2 = EdgeSystemIntegrationV2(latti_home=temp_latti_home)
        
        # Verify that the state was loaded
        assert len(integration2.task_results) >= 3
    
    def test_report_generation(self, integration):
        """Test that reports are generated correctly."""
        # Record some data
        for i in range(3):
            integration.record_execution(
                task_id=f"task_{i}",
                model="gpt-4",
                success=True,
                quality=85,
                cost=2000
            )
        
        # Generate report
        report = integration.report()
        
        assert report is not None
        assert isinstance(report, str)
        assert len(report) > 0
        assert "gpt-4" in report or "Model" in report


class TestEdgeSystemHookV2:
    """Test EdgeSystemHookV2 hook interface."""
    
    @pytest.fixture
    def hook(self):
        """Create an EdgeSystemHookV2 instance for testing."""
        return EdgeSystemHookV2()
    
    def test_hook_initialization(self, hook):
        """Test that the hook initializes correctly."""
        assert hook is not None
        assert hook.integration is not None
    
    def test_hook_process_task(self, hook):
        """Test that the hook can process tasks."""
        task = {
            "id": "hook_task_1",
            "description": "Test task",
            "type": "code"
        }
        
        result = hook.process_task(task)
        
        assert result is not None
        assert "model" in result
        assert "routing_metadata" in result
    
    def test_hook_record_result(self, hook):
        """Test that the hook can record results."""
        hook.record_result(
            task_id="hook_task_1",
            model="gpt-4",
            success=True,
            quality=85,
            cost=2000
        )
        
        # Verify the result was recorded
        stats = hook.get_stats()
        assert "bandit_stats" in stats
    
    def test_hook_optimize(self, hook):
        """Test that the hook can run optimization."""
        # Record some data first
        for i in range(3):
            hook.record_result(
                task_id=f"hook_task_{i}",
                model="gpt-4",
                success=True,
                quality=85,
                cost=2000
            )
        
        # Run optimization
        opt_results = hook.optimize()
        
        assert opt_results is not None
        assert "timestamp" in opt_results
    
    def test_hook_get_stats(self, hook):
        """Test that the hook can get statistics."""
        # Record some data
        hook.record_result(
            task_id="hook_task_1",
            model="gpt-4",
            success=True,
            quality=85,
            cost=2000
        )
        
        # Get stats
        stats = hook.get_stats()
        
        assert stats is not None
        assert "bandit_stats" in stats
        assert "gpt-4" in stats["bandit_stats"]
    
    def test_hook_get_report(self, hook):
        """Test that the hook can generate reports."""
        # Record some data
        for i in range(3):
            hook.record_result(
                task_id=f"hook_task_{i}",
                model="gpt-4",
                success=True,
                quality=85,
                cost=2000
            )
        
        # Get report
        report = hook.report()
        
        assert report is not None
        assert isinstance(report, str)
        assert len(report) > 0


class TestGlobalHookInstance:
    """Test the global hook instance."""
    
    def test_get_edge_hook_v2_singleton(self):
        """Test that get_edge_hook_v2 returns a singleton."""
        hook1 = get_edge_hook_v2()
        hook2 = get_edge_hook_v2()
        
        assert hook1 is hook2
    
    def test_global_hook_functionality(self):
        """Test that the global hook works correctly."""
        hook = get_edge_hook_v2()
        
        # Process a task
        task = {
            "id": "global_task_1",
            "description": "Test task",
            "type": "code"
        }
        
        result = hook.process_task(task)
        assert result is not None
        
        # Record a result
        hook.record_result(
            task_id="global_task_1",
            model=result["model"],
            success=True,
            quality=85,
            cost=2000
        )
        
        # Get stats
        stats = hook.get_stats()
        assert "bandit_stats" in stats


class TestIntegrationWorkflow:
    """Test complete integration workflows."""
    
    @pytest.fixture
    def integration(self):
        """Create an integration instance for workflow testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield EdgeSystemIntegrationV2(latti_home=tmpdir)
    
    def test_complete_workflow(self, integration):
        """Test a complete task processing workflow."""
        # Define tasks
        tasks = [
            {
                "id": "task_1",
                "description": "Design a distributed cache system",
                "type": "architecture"
            },
            {
                "id": "task_2",
                "description": "Write a REST API endpoint",
                "type": "code"
            },
            {
                "id": "task_3",
                "description": "Analyze Byzantine Generals Problem",
                "type": "analysis"
            }
        ]
        
        # Process each task
        for task in tasks:
            # Route task
            routed = integration.process_task(task)
            assert routed is not None
            
            # Simulate execution
            success = task["id"] != "task_1"  # task_1 fails
            quality = 85 if success else 30
            cost = 2000 if success else 1500
            
            # Record result
            integration.record_execution(
                task_id=task["id"],
                model=routed["model"],
                success=success,
                quality=quality,
                cost=cost,
                error_type="timeout" if not success else None,
                error_message="Task exceeded time limit" if not success else None
            )
        
        # Run optimization
        opt_results = integration.optimize()
        assert opt_results is not None
        
        # Get stats
        stats = integration.get_stats()
        assert stats["analyzer_stats"]["total_failures"] == 1
        
        # Generate report
        report = integration.report()
        assert report is not None
        assert len(report) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
