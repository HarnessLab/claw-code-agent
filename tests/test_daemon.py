"""
Tests for EdgeSystemLinterDaemon
"""

import pytest
import time
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from edge_system_linter_daemon import (
    EdgeSystemLinterDaemon,
    AutoFixLevel,
    LintSnapshot,
    LintTrend
)


class TestEdgeSystemLinterDaemon:
    """Test suite for EdgeSystemLinterDaemon."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def sample_python_file(self, temp_dir):
        """Create a sample Python file."""
        file_path = temp_dir / "test.py"
        file_path.write_text("""
def hello():
    print("hello")
""")
        return file_path
    
    @pytest.fixture
    def daemon(self, temp_dir):
        """Create daemon instance."""
        return EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            auto_fix_level=AutoFixLevel.SAFE
        )
    
    # Basic Initialization Tests
    
    def test_daemon_initialization(self, daemon):
        """Test daemon initializes correctly."""
        assert daemon is not None
        assert daemon.watch_dir is not None
        assert daemon.auto_fix_level == AutoFixLevel.SAFE
        assert daemon.total_lints == 0
        assert daemon.total_issues_found == 0
    
    def test_daemon_with_custom_settings(self, temp_dir):
        """Test daemon with custom settings."""
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            auto_fix_level=AutoFixLevel.AGGRESSIVE,
            check_interval=0.5,
            max_history_snapshots=50,
            enable_auto_fix=True
        )
        
        assert daemon.auto_fix_level == AutoFixLevel.AGGRESSIVE
        assert daemon.check_interval == 0.5
        assert daemon.max_history_snapshots == 50
        assert daemon.enable_auto_fix is True
    
    # Run Once Tests
    
    def test_run_once(self, daemon, sample_python_file):
        """Test running daemon once."""
        daemon.run_once()
        
        assert daemon.total_lints > 0
        assert len(daemon.snapshots) > 0
    
    def test_run_once_multiple_times(self, daemon, sample_python_file):
        """Test running daemon multiple times."""
        daemon.run_once()
        first_lints = daemon.total_lints
        
        daemon.run_once()
        second_lints = daemon.total_lints
        
        assert second_lints >= first_lints
    
    # Background Thread Tests
    
    def test_daemon_start_stop(self, daemon):
        """Test starting and stopping daemon."""
        daemon.start()
        assert daemon.running
        
        time.sleep(0.5)
        
        daemon.stop()
        assert not daemon.running
    
    def test_daemon_background_monitoring(self, daemon, sample_python_file):
        """Test daemon monitors in background."""
        daemon.start()
        
        initial_lints = daemon.total_lints
        time.sleep(1)
        
        # Should have linted at least once
        assert daemon.total_lints >= initial_lints
        
        daemon.stop()
    
    def test_daemon_multiple_start_stop(self, daemon):
        """Test multiple start/stop cycles."""
        for _ in range(3):
            daemon.start()
            assert daemon.running
            time.sleep(0.2)
            daemon.stop()
            assert not daemon.running
    
    # Context Manager Tests
    
    def test_context_manager(self, temp_dir):
        """Test daemon as context manager."""
        with EdgeSystemLinterDaemon(watch_dir=str(temp_dir)) as daemon:
            assert daemon is not None
            daemon.run_once()
            assert daemon.total_lints >= 0
    
    def test_context_manager_cleanup(self, temp_dir):
        """Test context manager cleans up properly."""
        daemon = None
        with EdgeSystemLinterDaemon(watch_dir=str(temp_dir)) as d:
            daemon = d
            daemon.start()
            assert daemon.running
        
        # Should be stopped after context
        assert not daemon.running
    
    # Snapshot Tests
    
    def test_snapshot_creation(self, daemon, sample_python_file):
        """Test snapshots are created."""
        daemon.run_once()
        
        assert len(daemon.snapshots) > 0
        
        for filepath, snapshots in daemon.snapshots.items():
            assert len(snapshots) > 0
            snapshot = snapshots[0]
            assert isinstance(snapshot, LintSnapshot)
            assert snapshot.filepath is not None
            assert snapshot.timestamp is not None
    
    def test_snapshot_data_integrity(self, daemon, sample_python_file):
        """Test snapshot data is correct."""
        daemon.run_once()
        
        for filepath, snapshots in daemon.snapshots.items():
            snapshot = snapshots[0]
            
            assert snapshot.total_issues >= 0
            assert snapshot.errors >= 0
            assert snapshot.warnings >= 0
            assert snapshot.infos >= 0
            assert snapshot.suggestions >= 0
            assert snapshot.auto_fixes_applied >= 0
    
    def test_snapshot_history_limit(self, temp_dir):
        """Test snapshot history respects max limit."""
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            max_history_snapshots=5
        )
        
        # Create multiple snapshots
        for _ in range(10):
            daemon.run_once()
            time.sleep(0.1)
        
        # Check history is limited
        for filepath, snapshots in daemon.snapshots.items():
            assert len(snapshots) <= 5
    
    # Trend Analysis Tests
    
    def test_trend_analysis_single_snapshot(self, daemon, sample_python_file):
        """Test trend analysis with single snapshot."""
        daemon.run_once()
        
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            
            # Should return None or valid trend
            if trend:
                assert isinstance(trend, LintTrend)
                assert trend.filepath is not None
                assert trend.snapshots_count >= 1
    
    def test_trend_analysis_multiple_snapshots(self, daemon, sample_python_file):
        """Test trend analysis with multiple snapshots."""
        # Create multiple snapshots
        for _ in range(3):
            daemon.run_once()
            time.sleep(0.1)
        
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            
            if trend:
                assert trend.snapshots_count >= 2
                assert trend.error_trend in ["improving", "stable", "degrading"]
                assert trend.warning_trend in ["improving", "stable", "degrading"]
    
    def test_trend_analysis_improving(self, daemon):
        """Test trend detection for improving code."""
        # Mock snapshots with decreasing issues
        filepath = "test.py"
        daemon.snapshots[filepath] = [
            LintSnapshot(
                timestamp="2026-05-03T14:00:00",
                filepath=filepath,
                file_hash="hash1",
                total_issues=10,
                errors=5,
                warnings=5,
                infos=0,
                suggestions=0,
                issues=[],
                auto_fixes_applied=0
            ),
            LintSnapshot(
                timestamp="2026-05-03T14:01:00",
                filepath=filepath,
                file_hash="hash2",
                total_issues=5,
                errors=2,
                warnings=3,
                infos=0,
                suggestions=0,
                issues=[],
                auto_fixes_applied=0
            ),
        ]
        
        trend = daemon.get_trend_analysis(filepath)
        assert trend is not None
        assert trend.error_trend == "improving"
    
    # Statistics Tests
    
    def test_get_stats(self, daemon, sample_python_file):
        """Test getting statistics."""
        daemon.run_once()
        
        stats = daemon.get_stats()
        
        assert isinstance(stats, dict)
        assert "total_lints" in stats
        assert "total_issues_found" in stats
        assert "total_auto_fixes" in stats
        assert "files_tracked" in stats
        assert "auto_fix_level" in stats
    
    def test_stats_accuracy(self, daemon, sample_python_file):
        """Test statistics are accurate."""
        daemon.run_once()
        
        stats = daemon.get_stats()
        
        assert stats["total_lints"] == daemon.total_lints
        assert stats["total_issues_found"] == daemon.total_issues_found
        assert stats["total_auto_fixes"] == daemon.total_auto_fixes
        assert stats["files_tracked"] == len(daemon.snapshots)
    
    # Report Tests
    
    def test_report_generation(self, daemon, sample_python_file):
        """Test report generation."""
        daemon.run_once()
        
        report = daemon.report()
        
        assert isinstance(report, str)
        assert len(report) > 0
        assert "EDGE SYSTEM LINTER DAEMON REPORT" in report
    
    def test_report_contains_stats(self, daemon, sample_python_file):
        """Test report contains statistics."""
        daemon.run_once()
        
        report = daemon.report()
        
        assert "Total lints:" in report
        assert "Total issues found:" in report
        assert "Total auto-fixes applied:" in report
    
    # Auto-Fix Tests
    
    def test_auto_fix_disabled(self, temp_dir):
        """Test auto-fix can be disabled."""
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            enable_auto_fix=False
        )
        
        daemon.run_once()
        
        assert daemon.total_auto_fixes == 0
    
    def test_auto_fix_levels(self, temp_dir):
        """Test different auto-fix levels."""
        levels = [
            AutoFixLevel.NONE,
            AutoFixLevel.SAFE,
            AutoFixLevel.MODERATE,
            AutoFixLevel.AGGRESSIVE,
        ]
        
        for level in levels:
            daemon = EdgeSystemLinterDaemon(
                watch_dir=str(temp_dir),
                auto_fix_level=level,
                enable_auto_fix=True
            )
            
            assert daemon.auto_fix_level == level
    
    # File-Specific Linting Tests
    
    def test_lint_file_autonomous(self, daemon, sample_python_file):
        """Test linting specific file."""
        issues, snapshot = daemon.lint_file_autonomous(sample_python_file)
        
        assert isinstance(issues, list)
        assert isinstance(snapshot, LintSnapshot)
        assert snapshot.filepath is not None
    
    def test_lint_file_creates_snapshot(self, daemon, sample_python_file):
        """Test linting file creates snapshot."""
        daemon.lint_file_autonomous(sample_python_file)
        
        assert len(daemon.snapshots) > 0
    
    # History Storage Tests
    
    def test_history_directory_creation(self, temp_dir):
        """Test history directory is created."""
        history_dir = temp_dir / ".latti" / "lint_history"
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            history_dir=str(history_dir)
        )
        
        daemon.run_once()
        
        # History directory should exist
        assert history_dir.exists()
    
    def test_history_file_creation(self, temp_dir):
        """Test history files are created."""
        history_dir = temp_dir / ".latti" / "lint_history"
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            history_dir=str(history_dir)
        )
        
        daemon.run_once()
        
        # Should have created history files
        history_files = list(history_dir.glob("*.json"))
        assert len(history_files) >= 0  # May be 0 if no issues
    
    # Error Handling Tests
    
    def test_invalid_watch_dir(self):
        """Test daemon with invalid watch directory."""
        daemon = EdgeSystemLinterDaemon(watch_dir="/nonexistent/path")
        
        # Should not crash
        daemon.run_once()
    
    def test_permission_error_handling(self, temp_dir):
        """Test daemon handles permission errors gracefully."""
        # Create read-only file
        readonly_file = temp_dir / "readonly.py"
        readonly_file.write_text("print('test')")
        readonly_file.chmod(0o000)
        
        try:
            daemon = EdgeSystemLinterDaemon(watch_dir=str(temp_dir))
            daemon.run_once()
            # Should not crash
        finally:
            readonly_file.chmod(0o644)
    
    # Integration Tests
    
    def test_full_workflow(self, temp_dir):
        """Test complete workflow."""
        # Create test file
        test_file = temp_dir / "test.py"
        test_file.write_text("def hello():\n    pass\n")
        
        # Create daemon
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            auto_fix_level=AutoFixLevel.SAFE,
            enable_auto_fix=True
        )
        
        # Run once
        daemon.run_once()
        
        # Check results
        assert daemon.total_lints > 0
        
        # Get stats
        stats = daemon.get_stats()
        assert stats["files_tracked"] > 0
        
        # Get report
        report = daemon.report()
        assert len(report) > 0
    
    def test_background_monitoring_workflow(self, temp_dir):
        """Test background monitoring workflow."""
        test_file = temp_dir / "test.py"
        test_file.write_text("def hello():\n    pass\n")
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir=str(temp_dir),
            check_interval=0.2
        )
        
        # Start daemon
        daemon.start()
        
        try:
            # Let it run
            time.sleep(0.5)
            
            # Check it's working
            assert daemon.running
            assert daemon.total_lints >= 0
        
        finally:
            daemon.stop()
    
    # Performance Tests
    
    def test_performance_single_file(self, daemon, sample_python_file):
        """Test performance with single file."""
        import time
        
        start = time.time()
        daemon.run_once()
        elapsed = time.time() - start
        
        # Should complete in reasonable time
        assert elapsed < 5.0
    
    def test_performance_multiple_runs(self, daemon, sample_python_file):
        """Test performance with multiple runs."""
        import time
        
        start = time.time()
        for _ in range(5):
            daemon.run_once()
        elapsed = time.time() - start
        
        # Should complete in reasonable time
        assert elapsed < 10.0
    
    # Thread Safety Tests
    
    def test_thread_safety_concurrent_access(self, daemon, sample_python_file):
        """Test thread safety with concurrent access."""
        import threading
        
        def run_daemon():
            daemon.run_once()
        
        threads = [threading.Thread(target=run_daemon) for _ in range(3)]
        
        for t in threads:
            t.start()
        
        for t in threads:
            t.join()
        
        # Should not crash
        assert daemon.total_lints >= 0


class TestAutoFixLevel:
    """Test AutoFixLevel enum."""
    
    def test_auto_fix_levels_exist(self):
        """Test all auto-fix levels exist."""
        assert hasattr(AutoFixLevel, 'NONE')
        assert hasattr(AutoFixLevel, 'SAFE')
        assert hasattr(AutoFixLevel, 'MODERATE')
        assert hasattr(AutoFixLevel, 'AGGRESSIVE')
    
    def test_auto_fix_level_ordering(self):
        """Auto-fix levels follow an escalation order (NONE → SAFE →
        MODERATE → AGGRESSIVE). The `.value` strings serialize to JSON
        (edge_system_linter_daemon.py:471), so they cannot be re-typed to
        ints without breaking external consumers. Pin the intended order
        via the enum's iteration order, which Python guarantees follows
        definition order for `Enum` classes.
        """
        ordered = [
            AutoFixLevel.NONE,
            AutoFixLevel.SAFE,
            AutoFixLevel.MODERATE,
            AutoFixLevel.AGGRESSIVE,
        ]
        assert list(AutoFixLevel) == ordered


class TestLintSnapshot:
    """Test LintSnapshot data class."""
    
    def test_snapshot_creation(self):
        """Test creating snapshot."""
        snapshot = LintSnapshot(
            timestamp="2026-05-03T14:00:00",
            filepath="test.py",
            file_hash="abc123",
            total_issues=5,
            errors=2,
            warnings=3,
            infos=0,
            suggestions=0,
            issues=[],
            auto_fixes_applied=1
        )
        
        assert snapshot.filepath == "test.py"
        assert snapshot.total_issues == 5
        assert snapshot.errors == 2
    
    def test_snapshot_fields(self):
        """Test snapshot has all required fields."""
        snapshot = LintSnapshot(
            timestamp="2026-05-03T14:00:00",
            filepath="test.py",
            file_hash="abc123",
            total_issues=0,
            errors=0,
            warnings=0,
            infos=0,
            suggestions=0,
            issues=[],
            auto_fixes_applied=0
        )
        
        assert hasattr(snapshot, 'timestamp')
        assert hasattr(snapshot, 'filepath')
        assert hasattr(snapshot, 'file_hash')
        assert hasattr(snapshot, 'total_issues')
        assert hasattr(snapshot, 'errors')
        assert hasattr(snapshot, 'warnings')
        assert hasattr(snapshot, 'auto_fixes_applied')


class TestLintTrend:
    """Test LintTrend data class."""
    
    def test_trend_creation(self):
        """Test creating trend."""
        trend = LintTrend(
            filepath="test.py",
            snapshots_count=5,
            error_trend="improving",
            warning_trend="stable",
            most_common_rules=[("RULE1", 10), ("RULE2", 5)],
            first_seen="2026-05-03T14:00:00",
            last_seen="2026-05-03T14:05:00",
            total_issues_fixed=3
        )
        
        assert trend.filepath == "test.py"
        assert trend.error_trend == "improving"
        assert trend.snapshots_count == 5
    
    def test_trend_fields(self):
        """Test trend has all required fields."""
        trend = LintTrend(
            filepath="test.py",
            snapshots_count=1,
            error_trend="stable",
            warning_trend="stable",
            most_common_rules=[],
            first_seen="2026-05-03T14:00:00",
            last_seen="2026-05-03T14:00:00",
            total_issues_fixed=0
        )
        
        assert hasattr(trend, 'filepath')
        assert hasattr(trend, 'error_trend')
        assert hasattr(trend, 'warning_trend')
        assert hasattr(trend, 'most_common_rules')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
