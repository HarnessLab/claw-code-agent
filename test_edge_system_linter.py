#!/usr/bin/env python3
"""
Tests for EdgeSystemLinter.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from edge_system_linter import (
    EdgeSystemLinter,
    EdgeSystemLinterReport,
    Severity,
    lint_file,
    lint_code
)


class TestEdgeSystemLinter:
    """Test EdgeSystemLinter."""
    
    def test_lint_code_with_hook_import(self):
        """Test linting code with hook import."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()
task = {"id": "task_1", "description": "test"}
upgraded = hook.process_task(task)
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have no errors
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0
    
    def test_lint_code_missing_hook_import(self):
        """Test linting code without hook import."""
        code = """
def process_task(task):
    # Process task without using hook
    return task
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have warning about missing hook
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        assert any('MISSING_HOOK_IMPORT' in i.rule for i in warnings)
    
    def test_lint_code_missing_result_recording(self):
        """Test linting code without result recording."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_and_execute(task):
    upgraded = hook.process_task(task)
    # Execute but don't record result
    return upgraded
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have warning about missing result recording
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        assert any('MISSING_RESULT_RECORDING' in i.rule for i in warnings)
    
    def test_lint_code_with_result_recording(self):
        """Test linting code with result recording."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_and_execute(task):
    upgraded = hook.process_task(task)
    # Execute task
    success = True
    quality = 85
    cost = 2000
    
    # Record result
    hook.record_result(
        task_id=task['id'],
        model=upgraded['model'],
        success=success,
        quality=quality,
        cost=cost
    )
    return upgraded
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have no errors
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0
    
    def test_lint_code_missing_cost_tracking(self):
        """Test linting code without cost tracking."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def record_result(task_id, model, success, quality):
    # Missing cost parameter
    hook.record_result(
        task_id=task_id,
        model=model,
        success=success,
        quality=quality
    )
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have warning about missing cost tracking
        warnings = [i for i in issues if i.severity == Severity.WARNING]
        assert any('MISSING_COST_TRACKING' in i.rule for i in warnings)
    
    def test_lint_code_missing_failure_handling(self):
        """Test linting code without failure handling."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_task(task):
    upgraded = hook.process_task(task)
    # Execute and record but don't handle failures
    hook.record_result(
        task_id=task['id'],
        model=upgraded['model'],
        success=False,
        quality=20,
        cost=1000
    )
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have info about missing failure handling
        infos = [i for i in issues if i.severity == Severity.INFO]
        assert any('MISSING_FAILURE_HANDLING' in i.rule for i in infos)
    
    def test_lint_code_with_failure_handling(self):
        """Test linting code with failure handling."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_task(task):
    upgraded = hook.process_task(task)
    success = execute_task(upgraded)
    
    hook.record_result(
        task_id=task['id'],
        model=upgraded['model'],
        success=success,
        quality=50,
        cost=1000
    )
    
    if not success:
        strategy, recommendation = hook.get_recovery_strategy(task['id'])
        handle_recovery(strategy, recommendation)

def handle_recovery(strategy, recommendation):
    pass

def execute_task(task):
    return True
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have no errors
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0
    
    def test_lint_code_missing_optimization(self):
        """Test linting code without optimization."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_tasks(tasks):
    for task in tasks:
        upgraded = hook.process_task(task)
        # Process but never optimize
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have info about missing optimization
        infos = [i for i in issues if i.severity == Severity.INFO]
        assert any('MISSING_OPTIMIZATION' in i.rule for i in infos)
    
    def test_lint_code_with_optimization(self):
        """Test linting code with optimization."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2

hook = get_edge_hook_v2()

def process_tasks(tasks):
    for task in tasks:
        upgraded = hook.process_task(task)
        hook.record_result(
            task_id=task['id'],
            model=upgraded['model'],
            success=True,
            quality=85,
            cost=2000
        )
    
    # Periodic optimization
    results = hook.optimize()
    return results
"""
        linter = EdgeSystemLinter()
        issues = linter.lint_code(code)
        
        # Should have no errors
        errors = [i for i in issues if i.severity == Severity.ERROR]
        assert len(errors) == 0


class TestEdgeSystemLinterReport:
    """Test EdgeSystemLinterReport."""
    
    def test_report_summary(self):
        """Test report summary generation."""
        from edge_system_linter import LintIssue
        
        issues = [
            LintIssue(
                severity=Severity.ERROR,
                rule="TEST_ERROR",
                message="Test error",
                line=1
            ),
            LintIssue(
                severity=Severity.WARNING,
                rule="TEST_WARNING",
                message="Test warning",
                line=2
            ),
            LintIssue(
                severity=Severity.INFO,
                rule="TEST_INFO",
                message="Test info",
                line=3
            )
        ]
        
        report = EdgeSystemLinterReport(issues)
        summary = report.summary()
        
        assert "Total issues: 3" in summary
        assert "ERROR: 1" in summary
        assert "WARNING: 1" in summary
        assert "INFO: 1" in summary
    
    def test_report_json(self):
        """Test JSON report generation."""
        from edge_system_linter import LintIssue
        
        issues = [
            LintIssue(
                severity=Severity.ERROR,
                rule="TEST_ERROR",
                message="Test error",
                line=1
            )
        ]
        
        report = EdgeSystemLinterReport(issues)
        json_report = report.json()
        
        assert json_report['total'] == 1
        assert json_report['by_severity']['ERROR'] == 1
        assert len(json_report['issues']) == 1


class TestLintFunctions:
    """Test module-level lint functions."""
    
    def test_lint_code_function(self):
        """Test lint_code function."""
        code = """
from edge_system_integration_v2 import get_edge_hook_v2
hook = get_edge_hook_v2()
"""
        issues, report = lint_code(code)
        
        assert isinstance(issues, list)
        assert isinstance(report, str)
        assert "EDGE SYSTEM LINTER REPORT" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
