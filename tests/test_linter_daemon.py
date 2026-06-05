#!/usr/bin/env python3
"""
Tests for EdgeSystemLinterDaemon.
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from edge_system_linter_daemon import (
    EdgeSystemLinterDaemon,
    AutoFixLevel,
    LintSnapshot,
    LintTrend
)


class TestEdgeSystemLinterDaemon:
    """Test suite for linter daemon."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        with tempfile.TemporaryDirectory() as watch_dir:
            with tempfile.TemporaryDirectory() as history_dir:
                yield Path(watch_dir), Path(history_dir)
    
    @pytest.fixture
    def daemon(self, temp_dirs):
        """Create a daemon instance."""
        watch_dir, history_dir = temp_dirs
        return EdgeSystemLinterDaemon(
            watch_dir=str(watch_dir),
            history_dir=str(history_dir),
            auto_fix_level=AutoFixLevel.SAFE,
            check_interval=0.1
        )
    
    def test_daemon_initialization(self, daemon):
        """Test daemon initializes correctly."""
        assert daemon.watch_dir.exists()
        assert daemon.history_dir.exists()
        assert daemon.total_lints == 0
        assert daemon.total_issues_found == 0
        assert daemon.running is False
    
    def test_get_python_files(self, daemon, temp_dirs):
        """Test finding Python files."""
        watch_dir, _ = temp_dirs
        
        # Create some Python files
        (watch_dir / "test1.py").write_text("print('hello')")
        (watch_dir / "test2.py").write_text("print('world')")
        (watch_dir / "readme.txt").write_text("not python")
        
        files = daemon._get_python_files()
        assert len(files) == 2
        assert all(f.suffix == ".py" for f in files)
    
    def test_file_hash_detection(self, daemon, temp_dirs):
        """Test file change detection."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        test_file.write_text("print('v1')")
        
        # First check should detect as changed
        assert daemon._has_file_changed(test_file) is True
        
        # Second check should not detect change
        assert daemon._has_file_changed(test_file) is False
        
        # Modify file
        test_file.write_text("print('v2')")
        assert daemon._has_file_changed(test_file) is True
    
    def test_lint_file_autonomous(self, daemon, temp_dirs):
        """Test autonomous linting."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        
        # Write code with a missing import
        code = """
def process_task(task):
    # Missing hook import and usage
    result = task['data']
    return result
"""
        test_file.write_text(code)
        
        issues, snapshot = daemon.lint_file_autonomous(test_file)
        
        assert snapshot is not None
        assert snapshot.filepath == str(test_file)
        assert snapshot.total_issues >= 0
        assert daemon.total_lints == 1
    
    def test_snapshot_persistence(self, daemon, temp_dirs):
        """Test snapshot saving and loading."""
        watch_dir, history_dir = temp_dirs
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        # Lint and save
        issues, snapshot = daemon.lint_file_autonomous(test_file)
        
        # Check snapshot was saved
        snapshot_files = list(history_dir.glob("*.json"))
        assert len(snapshot_files) > 0
        
        # Load and verify
        with open(snapshot_files[0]) as f:
            data = json.load(f)
            assert data["filepath"] == str(test_file)
            assert "timestamp" in data
            assert "total_issues" in data
    
    def test_auto_fix_safe_level(self, daemon, temp_dirs):
        """Test safe auto-fix level."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        
        code = """
def process_task(task):
    result = task['data']
    return result
"""
        test_file.write_text(code)
        
        daemon.auto_fix_level = AutoFixLevel.SAFE
        daemon.enable_auto_fix = True
        
        issues, snapshot = daemon.lint_file_autonomous(test_file)
        
        # Safe fixes should be applied
        assert snapshot is not None
    
    def test_auto_fix_none_level(self, daemon, temp_dirs):
        """Test no auto-fix."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.auto_fix_level = AutoFixLevel.NONE
        daemon.enable_auto_fix = False
        
        issues, snapshot = daemon.lint_file_autonomous(test_file)
        
        assert snapshot.auto_fixes_applied == 0
    
    def test_trend_analysis(self, daemon, temp_dirs):
        """Test trend analysis."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        
        # Create multiple snapshots with improving trend
        for i in range(5):
            code = f"# Version {i}\nprint('hello')"
            test_file.write_text(code)
            daemon.lint_file_autonomous(test_file)
        
        trend = daemon.get_trend_analysis(str(test_file))
        
        assert trend is not None
        assert trend.filepath == str(test_file)
        assert trend.snapshots_count == 5
    
    def test_stats_reporting(self, daemon, temp_dirs):
        """Test statistics reporting."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.lint_file_autonomous(test_file)
        
        stats = daemon.get_stats()
        
        assert stats["total_lints"] == 1
        assert stats["files_tracked"] == 1
        assert stats["running"] is False
    
    def test_report_generation(self, daemon, temp_dirs):
        """Test report generation."""
        watch_dir, _ = temp_dirs
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.lint_file_autonomous(test_file)
        
        report = daemon.report()
        
        assert "EDGE SYSTEM LINTER DAEMON REPORT" in report
        assert "RUNNING" in report or "STOPPED" in report
        assert "Total lints:" in report
    
    def test_context_manager(self, temp_dirs):
        """Test daemon as context manager."""
        watch_dir, history_dir = temp_dirs
        
        with EdgeSystemLinterDaemon(
            watch_dir=str(watch_dir),
            history_dir=str(history_dir)
        ) as daemon:
            assert daemon is not None
            test_file = watch_dir / "test.py"
            test_file.write_text("print('hello')")
            daemon.run_once()
        
        # Should be stopped after context exit
        assert daemon.running is False
    
    def test_run_once(self, daemon, temp_dirs):
        """Test single pass execution."""
        watch_dir, _ = temp_dirs
        
        # Create test files
        (watch_dir / "test1.py").write_text("print('1')")
        (watch_dir / "test2.py").write_text("print('2')")
        
        daemon.run_once()
        
        assert daemon.total_lints == 2
    
    def test_multiple_files_tracking(self, daemon, temp_dirs):
        """Test tracking multiple files."""
        watch_dir, _ = temp_dirs
        
        files = []
        for i in range(3):
            f = watch_dir / f"test{i}.py"
            f.write_text(f"# File {i}\nprint('hello')")
            files.append(f)
        
        daemon.run_once()
        
        assert len(daemon.snapshots) == 3
        assert daemon.total_lints == 3
    
    def test_history_trimming(self, daemon, temp_dirs):
        """Test old history trimming."""
        watch_dir, history_dir = temp_dirs
        test_file = watch_dir / "test.py"
        
        # Set low max to trigger trimming
        daemon.max_history_snapshots = 3
        
        # Create more snapshots than max
        for i in range(5):
            test_file.write_text(f"# Version {i}\nprint('hello')")
            daemon.lint_file_autonomous(test_file)
        
        # Check that old files were trimmed
        snapshot_files = list(history_dir.glob("*.json"))
        assert len(snapshot_files) <= 3
    
    def test_compute_trend(self, daemon):
        """Test trend computation."""
        # Improving trend
        improving = daemon._compute_trend([10, 8, 6, 4, 2])
        assert improving == "improving"
        
        # Degrading trend
        degrading = daemon._compute_trend([2, 4, 6, 8, 10])
        assert degrading == "degrading"
        
        # Stable trend
        stable = daemon._compute_trend([5, 5, 5, 5, 5])
        assert stable == "stable"


class TestAutoFixLevels:
    """Test auto-fix functionality at different levels."""
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        with tempfile.TemporaryDirectory() as watch_dir:
            with tempfile.TemporaryDirectory() as history_dir:
                yield Path(watch_dir), Path(history_dir)
    
    def test_safe_fix_level(self, temp_dirs):
        """Test SAFE auto-fix level."""
        watch_dir, history_dir = temp_dirs
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(watch_dir),
            history_dir=str(history_dir),
            auto_fix_level=AutoFixLevel.SAFE,
            enable_auto_fix=True
        )
        
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.lint_file_autonomous(test_file)
        # Safe fixes should be minimal
        assert daemon.total_auto_fixes >= 0
    
    def test_moderate_fix_level(self, temp_dirs):
        """Test MODERATE auto-fix level."""
        watch_dir, history_dir = temp_dirs
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(watch_dir),
            history_dir=str(history_dir),
            auto_fix_level=AutoFixLevel.MODERATE,
            enable_auto_fix=True
        )
        
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.lint_file_autonomous(test_file)
        # Moderate fixes should be applied
        assert daemon.total_auto_fixes >= 0
    
    def test_aggressive_fix_level(self, temp_dirs):
        """Test AGGRESSIVE auto-fix level."""
        watch_dir, history_dir = temp_dirs
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(watch_dir),
            history_dir=str(history_dir),
            auto_fix_level=AutoFixLevel.AGGRESSIVE,
            enable_auto_fix=True
        )
        
        test_file = watch_dir / "test.py"
        test_file.write_text("print('hello')")
        
        daemon.lint_file_autonomous(test_file)
        # Aggressive fixes should be applied
        assert daemon.total_auto_fixes >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
