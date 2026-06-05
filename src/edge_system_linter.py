#!/usr/bin/env python3
"""
EDGE SYSTEM LINTER

Analyzes code for compliance with EdgeSystemIntegrationV2 patterns.

This linter checks for:
1. Proper task routing (using bandit for model selection)
2. Result recording (outcomes recorded for learning)
3. Failure handling (recovery strategies applied)
4. State persistence (save/load patterns)
5. Optimization integration (periodic optimization calls)
6. Hook integration (using EdgeSystemHookV2)
7. Metadata tracking (routing metadata attached)
8. Cost tracking (token costs recorded)

Usage:
    linter = EdgeSystemLinter()
    issues = linter.lint_file("path/to/code.py")
    for issue in issues:
        print(f"{issue.severity}: {issue.message}")
"""

import ast
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Severity(Enum):
    """Issue severity levels."""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    SUGGESTION = "SUGGESTION"


@dataclass
class LintIssue:
    """A linting issue found in code."""
    severity: Severity
    rule: str
    message: str
    line: int
    column: int = 0
    code_snippet: str = ""
    fix_suggestion: str = ""
    
    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.rule} (line {self.line}): {self.message}"
    
    def detailed(self) -> str:
        """Return detailed issue description."""
        lines = [str(self)]
        if self.code_snippet:
            lines.append(f"  Code: {self.code_snippet}")
        if self.fix_suggestion:
            lines.append(f"  Fix: {self.fix_suggestion}")
        return "\n".join(lines)


class EdgeSystemLinter(ast.NodeVisitor):
    """
    Linter for EdgeSystemIntegrationV2 compliance.
    
    Checks code for proper integration with the edge system:
    - Task routing patterns
    - Result recording patterns
    - Failure handling patterns
    - State persistence patterns
    - Optimization patterns
    - Hook integration patterns
    """
    
    def __init__(self):
        self.issues: List[LintIssue] = []
        self.current_file = ""
        self.current_function = ""
        self.lines = []
        
        # Tracking state
        self.has_hook_import = False
        self.has_hook_usage = False
        self.task_processing_functions = []
        self.result_recording_functions = []
        self.failure_handling_functions = []
        self.optimization_functions = []
        self.state_persistence_functions = []
        
        # Pattern tracking
        self.function_calls = {}  # function_name -> list of call locations
        self.assignments = {}  # variable_name -> assignment info
        self.imports = {}  # module_name -> import info
    
    def lint_file(self, filepath: str) -> List[LintIssue]:
        """
        Lint a Python file.
        
        Args:
            filepath: Path to Python file
        
        Returns:
            List of linting issues
        """
        self.issues = []
        self.current_file = filepath
        self.function_calls = {}
        self.assignments = {}
        self.imports = {}
        self.task_processing_functions = []
        self.result_recording_functions = []
        self.failure_handling_functions = []
        self.optimization_functions = []
        self.state_persistence_functions = []
        
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                self.lines = content.split('\n')
            
            tree = ast.parse(content)
            self.visit(tree)
            
            # Run additional checks
            self._check_hook_integration()
            self._check_task_routing()
            self._check_result_recording()
            self._check_failure_handling()
            self._check_state_persistence()
            self._check_optimization()
            self._check_metadata_tracking()
            self._check_cost_tracking()
            
        except SyntaxError as e:
            self.issues.append(LintIssue(
                severity=Severity.ERROR,
                rule="SYNTAX_ERROR",
                message=f"Syntax error: {e.msg}",
                line=e.lineno or 0,
                column=e.offset or 0
            ))
        except Exception as e:
            self.issues.append(LintIssue(
                severity=Severity.ERROR,
                rule="PARSE_ERROR",
                message=f"Failed to parse file: {str(e)}",
                line=0
            ))
        
        return self.issues
    
    def lint_code(self, code: str) -> List[LintIssue]:
        """
        Lint Python code string.
        
        Args:
            code: Python code as string
        
        Returns:
            List of linting issues
        """
        self.issues = []
        self.current_file = "<string>"
        self.lines = code.split('\n')
        self.function_calls = {}
        self.assignments = {}
        self.imports = {}
        self.task_processing_functions = []
        self.result_recording_functions = []
        self.failure_handling_functions = []
        self.optimization_functions = []
        self.state_persistence_functions = []
        
        try:
            tree = ast.parse(code)
            self.visit(tree)
            
            # Run additional checks
            self._check_hook_integration()
            self._check_task_routing()
            self._check_result_recording()
            self._check_failure_handling()
            self._check_state_persistence()
            self._check_optimization()
            self._check_metadata_tracking()
            self._check_cost_tracking()
            
        except SyntaxError as e:
            self.issues.append(LintIssue(
                severity=Severity.ERROR,
                rule="SYNTAX_ERROR",
                message=f"Syntax error: {e.msg}",
                line=e.lineno or 0,
                column=e.offset or 0
            ))
        except Exception as e:
            self.issues.append(LintIssue(
                severity=Severity.ERROR,
                rule="PARSE_ERROR",
                message=f"Failed to parse code: {str(e)}",
                line=0
            ))
        
        return self.issues
    
    # AST Visitor methods
    
    def visit_Import(self, node: ast.Import):
        """Track imports."""
        for alias in node.names:
            module = alias.name
            self.imports[module] = {
                'line': node.lineno,
                'alias': alias.asname or module
            }
            
            if 'edge_system_integration_v2' in module:
                self.has_hook_import = True
        
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Track from imports."""
        module = node.module or ""
        for alias in node.names:
            name = alias.name
            self.imports[f"{module}.{name}"] = {
                'line': node.lineno,
                'alias': alias.asname or name
            }
            
            if 'EdgeSystemHookV2' in name or 'get_edge_hook_v2' in name:
                self.has_hook_import = True
        
        self.generic_visit(node)
    
    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Track function definitions."""
        self.current_function = node.name
        
        # Categorize functions by pattern
        if any(pattern in node.name.lower() for pattern in ['process', 'route', 'select']):
            self.task_processing_functions.append(node.name)
        
        if any(pattern in node.name.lower() for pattern in ['record', 'log', 'track']):
            self.result_recording_functions.append(node.name)
        
        if any(pattern in node.name.lower() for pattern in ['recover', 'handle', 'error', 'fail']):
            self.failure_handling_functions.append(node.name)
        
        if any(pattern in node.name.lower() for pattern in ['optimize', 'improve', 'tune']):
            self.optimization_functions.append(node.name)
        
        if any(pattern in node.name.lower() for pattern in ['save', 'load', 'persist', 'state']):
            self.state_persistence_functions.append(node.name)
        
        self.generic_visit(node)
        self.current_function = ""
    
    def visit_Call(self, node: ast.Call):
        """Track function calls."""
        func_name = self._get_call_name(node)
        if func_name:
            if func_name not in self.function_calls:
                self.function_calls[func_name] = []
            self.function_calls[func_name].append(node.lineno)
        
        self.generic_visit(node)
    
    def visit_Assign(self, node: ast.Assign):
        """Track assignments."""
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignments[target.id] = {
                    'line': node.lineno,
                    'value': ast.unparse(node.value) if hasattr(ast, 'unparse') else ''
                }
        
        self.generic_visit(node)
    
    # Helper methods
    
    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """Extract function name from Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return '.'.join(reversed(parts))
        return None
    
    def _get_line_content(self, line_num: int) -> str:
        """Get content of a specific line."""
        if 0 < line_num <= len(self.lines):
            return self.lines[line_num - 1].strip()
        return ""
    
    def _add_issue(
        self,
        severity: Severity,
        rule: str,
        message: str,
        line: int,
        fix_suggestion: str = ""
    ):
        """Add a linting issue."""
        self.issues.append(LintIssue(
            severity=severity,
            rule=rule,
            message=message,
            line=line,
            code_snippet=self._get_line_content(line),
            fix_suggestion=fix_suggestion
        ))
    
    # Check methods
    
    def _check_hook_integration(self):
        """Check for proper hook integration."""
        # Check if code has task processing functions
        has_task_processing = any(
            func in self.function_calls 
            for func in ['process_task', 'process', 'route', 'select']
        )
        
        if has_task_processing and not self.has_hook_import:
            self._add_issue(
                Severity.WARNING,
                "MISSING_HOOK_IMPORT",
                "Code processes tasks but doesn't import EdgeSystemHookV2",
                1,
                "Add: from edge_system_integration_v2 import get_edge_hook_v2"
            )
        elif not self.has_hook_import and self.task_processing_functions:
            self._add_issue(
                Severity.WARNING,
                "MISSING_HOOK_IMPORT",
                "Code has task processing functions but doesn't import EdgeSystemHookV2",
                1,
                "Add: from edge_system_integration_v2 import get_edge_hook_v2"
            )
        elif self.has_hook_import:
            # Check if hook is actually used
            if 'get_edge_hook_v2' not in self.function_calls and 'EdgeSystemHookV2' not in self.assignments:
                self._add_issue(
                    Severity.INFO,
                    "UNUSED_HOOK_IMPORT",
                    "Hook is imported but not used",
                    1,
                    "Use: hook = get_edge_hook_v2()"
                )
            else:
                self.has_hook_usage = True
    
    def _check_task_routing(self):
        """Check for proper task routing patterns."""
        # Look for task processing without routing
        for func_name in self.task_processing_functions:
            if func_name not in self.function_calls:
                continue
            
            # Check if function uses hook.process_task
            if 'process_task' not in self.function_calls:
                self._add_issue(
                    Severity.WARNING,
                    "MISSING_TASK_ROUTING",
                    f"Function '{func_name}' processes tasks but doesn't use hook.process_task()",
                    self.function_calls.get(func_name, [0])[0],
                    "Use: upgraded_task = hook.process_task(task)"
                )
    
    def _check_result_recording(self):
        """Check for proper result recording."""
        # Look for task execution without result recording
        has_process_task = any(k.endswith('process_task') for k in self.function_calls.keys())
        has_record_result = any(k.endswith('record_result') or k.endswith('record_outcome') for k in self.function_calls.keys())
        
        if has_process_task and not has_record_result:
            # Find the line number of process_task call
            process_task_line = 1
            for func_name, lines in self.function_calls.items():
                if func_name.endswith('process_task') and lines:
                    process_task_line = lines[0]
                    break
            
            self._add_issue(
                Severity.WARNING,
                "MISSING_RESULT_RECORDING",
                "Tasks are processed but results are not recorded",
                process_task_line,
                "Use: hook.record_result(task_id, model, success, quality, cost)"
            )
        
        # Check if record_result is called with all required parameters
        if any(k.endswith('record_result') or k.endswith('record_outcome') for k in self.function_calls.keys()):
            # This is a basic check - more detailed analysis would require AST inspection
            pass
    
    def _check_failure_handling(self):
        """Check for proper failure handling."""
        # Look for result recording without failure handling
        has_record_result = any(k.endswith('record_result') or k.endswith('record_outcome') for k in self.function_calls.keys())
        has_recovery = any(k.endswith('get_recovery_strategy') or k.endswith('handle_failure') or k.endswith('recover') for k in self.function_calls.keys())
        
        if has_record_result and not has_recovery:
            # Find the line number of record_result call
            record_line = 1
            for func_name, lines in self.function_calls.items():
                if (func_name.endswith('record_result') or func_name.endswith('record_outcome')) and lines:
                    record_line = lines[0]
                    break
            
            self._add_issue(
                Severity.INFO,
                "MISSING_FAILURE_HANDLING",
                "Results are recorded but no failure handling is implemented",
                record_line,
                "Use: strategy, rec = hook.get_recovery_strategy(task_id)"
            )
    
    def _check_state_persistence(self):
        """Check for proper state persistence."""
        has_save = 'save' in self.function_calls or 'save_state' in self.function_calls
        has_load = 'load' in self.function_calls or 'load_state' in self.function_calls
        
        if self.task_processing_functions and not (has_save or has_load):
            self._add_issue(
                Severity.INFO,
                "MISSING_STATE_PERSISTENCE",
                "Tasks are processed but state is not persisted",
                1,
                "Implement save/load for state persistence"
            )
    
    def _check_optimization(self):
        """Check for periodic optimization."""
        if self.task_processing_functions and not self.optimization_functions:
            self._add_issue(
                Severity.INFO,
                "MISSING_OPTIMIZATION",
                "No periodic optimization is implemented",
                1,
                "Use: hook.optimize() periodically"
            )
    
    def _check_metadata_tracking(self):
        """Check for routing metadata tracking."""
        if 'process_task' in self.function_calls:
            # Check if routing_metadata is used
            if 'routing_metadata' not in self.assignments:
                self._add_issue(
                    Severity.INFO,
                    "MISSING_METADATA_TRACKING",
                    "Task routing metadata is not being tracked",
                    self.function_calls['process_task'][0],
                    "Use: metadata = task.get('routing_metadata')"
                )
    
    def _check_cost_tracking(self):
        """Check for cost tracking."""
        has_record_result = any(k.endswith('record_result') or k.endswith('record_outcome') for k in self.function_calls.keys())
        
        if has_record_result:
            # Find the line number of record_result call
            record_line = 1
            for func_name, lines in self.function_calls.items():
                if (func_name.endswith('record_result') or func_name.endswith('record_outcome')) and lines:
                    record_line = lines[0]
                    break
            
            if record_line > 0 and record_line <= len(self.lines):
                # Look at the function call and surrounding lines
                code_section = '\n'.join(self.lines[max(0, record_line-5):min(len(self.lines), record_line+5)])
                if 'cost=' not in code_section and 'cost =' not in code_section:
                    self._add_issue(
                        Severity.WARNING,
                        "MISSING_COST_TRACKING",
                        "Results are recorded but cost/token information is not tracked",
                        record_line,
                        "Pass cost parameter: hook.record_result(..., cost=token_count)"
                    )


class EdgeSystemLinterReport:
    """Generate formatted linting reports."""
    
    def __init__(self, issues: List[LintIssue]):
        self.issues = issues
    
    def summary(self) -> str:
        """Generate summary report."""
        by_severity = {}
        for issue in self.issues:
            severity = issue.severity.value
            if severity not in by_severity:
                by_severity[severity] = 0
            by_severity[severity] += 1
        
        lines = []
        lines.append("\n" + "="*70)
        lines.append("EDGE SYSTEM LINTER REPORT")
        lines.append("="*70)
        lines.append(f"\nTotal issues: {len(self.issues)}")
        
        for severity in ['ERROR', 'WARNING', 'INFO', 'SUGGESTION']:
            count = by_severity.get(severity, 0)
            if count > 0:
                lines.append(f"  {severity}: {count}")
        
        return "\n".join(lines)
    
    def detailed(self) -> str:
        """Generate detailed report."""
        lines = [self.summary()]
        lines.append("\nDETAILS:")
        lines.append("-" * 70)
        
        for issue in self.issues:
            lines.append(issue.detailed())
            lines.append("")
        
        lines.append("="*70)
        return "\n".join(lines)
    
    def json(self) -> Dict:
        """Generate JSON report."""
        return {
            'total': len(self.issues),
            'by_severity': {
                'ERROR': len([i for i in self.issues if i.severity == Severity.ERROR]),
                'WARNING': len([i for i in self.issues if i.severity == Severity.WARNING]),
                'INFO': len([i for i in self.issues if i.severity == Severity.INFO]),
                'SUGGESTION': len([i for i in self.issues if i.severity == Severity.SUGGESTION])
            },
            'issues': [
                {
                    'severity': issue.severity.value,
                    'rule': issue.rule,
                    'message': issue.message,
                    'line': issue.line,
                    'code': issue.code_snippet,
                    'fix': issue.fix_suggestion
                }
                for issue in self.issues
            ]
        }


def lint_file(filepath: str) -> Tuple[List[LintIssue], str]:
    """
    Lint a file and return issues and report.
    
    Args:
        filepath: Path to Python file
    
    Returns:
        (issues, report_string)
    """
    linter = EdgeSystemLinter()
    issues = linter.lint_file(filepath)
    report = EdgeSystemLinterReport(issues)
    return issues, report.detailed()


def lint_code(code: str) -> Tuple[List[LintIssue], str]:
    """
    Lint code string and return issues and report.
    
    Args:
        code: Python code as string
    
    Returns:
        (issues, report_string)
    """
    linter = EdgeSystemLinter()
    issues = linter.lint_code(code)
    report = EdgeSystemLinterReport(issues)
    return issues, report.detailed()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python edge_system_linter.py <file.py>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    issues, report = lint_file(filepath)
    print(report)
    
    # Exit with error code if there are errors
    error_count = len([i for i in issues if i.severity == Severity.ERROR])
    sys.exit(error_count)
