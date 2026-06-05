#!/usr/bin/env python3
"""
Practical examples of using EdgeSystemLinterDaemon.

This file demonstrates various use cases and integration patterns.
"""

import sys
import time
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from edge_system_linter_daemon import (
    EdgeSystemLinterDaemon,
    AutoFixLevel,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Example 1: Basic One-Time Linting
# ============================================================================

def example_basic_linting():
    """Run linter once and print results."""
    print("\n" + "="*70)
    print("Example 1: Basic One-Time Linting")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.NONE
    )
    
    # Run once
    daemon.run_once()
    
    # Print report
    print(daemon.report())
    
    # Get statistics
    stats = daemon.get_stats()
    print(f"\nStatistics:")
    print(f"  Total lints: {stats['total_lints']}")
    print(f"  Total issues: {stats['total_issues_found']}")
    print(f"  Files tracked: {stats['files_tracked']}")


# ============================================================================
# Example 2: Background Monitoring
# ============================================================================

def example_background_monitoring():
    """Run linter in background and monitor."""
    print("\n" + "="*70)
    print("Example 2: Background Monitoring")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=2.0,
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    # Start background monitoring
    daemon.start()
    print("Daemon started, monitoring for 10 seconds...")
    
    try:
        for i in range(5):
            time.sleep(2)
            stats = daemon.get_stats()
            print(f"  [{i+1}] Issues found: {stats['total_issues_found']}, "
                  f"Auto-fixes: {stats['total_auto_fixes']}")
    
    finally:
        daemon.stop()
        print("Daemon stopped")


# ============================================================================
# Example 3: Auto-Fix with Different Levels
# ============================================================================

def example_auto_fix_levels():
    """Demonstrate different auto-fix levels."""
    print("\n" + "="*70)
    print("Example 3: Auto-Fix Levels")
    print("="*70)
    
    levels = [
        (AutoFixLevel.NONE, "No auto-fixes"),
        (AutoFixLevel.SAFE, "Safe auto-fixes only"),
        (AutoFixLevel.MODERATE, "Moderate auto-fixes"),
        (AutoFixLevel.AGGRESSIVE, "Aggressive auto-fixes"),
    ]
    
    for level, description in levels:
        print(f"\n{description}:")
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir="src/",
            auto_fix_level=level,
            enable_auto_fix=True
        )
        
        daemon.run_once()
        stats = daemon.get_stats()
        
        print(f"  Issues found: {stats['total_issues_found']}")
        print(f"  Auto-fixes applied: {stats['total_auto_fixes']}")


# ============================================================================
# Example 4: Trend Analysis
# ============================================================================

def example_trend_analysis():
    """Analyze trends over multiple runs."""
    print("\n" + "="*70)
    print("Example 4: Trend Analysis")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        max_history_snapshots=10
    )
    
    # Run multiple times to build history
    print("Building history...")
    for i in range(3):
        daemon.run_once()
        time.sleep(0.5)
        print(f"  Run {i+1} complete")
    
    # Analyze trends
    print("\nTrend Analysis:")
    for filepath in daemon.snapshots.keys():
        trend = daemon.get_trend_analysis(filepath)
        
        if trend:
            print(f"\n  File: {filepath}")
            print(f"    Snapshots: {trend.snapshots_count}")
            print(f"    Error trend: {trend.error_trend}")
            print(f"    Warning trend: {trend.warning_trend}")
            print(f"    Issues fixed: {trend.total_issues_fixed}")
            
            if trend.most_common_rules:
                print(f"    Top issues:")
                for rule, count in trend.most_common_rules[:3]:
                    print(f"      - {rule}: {count}")


# ============================================================================
# Example 5: Context Manager Usage
# ============================================================================

def example_context_manager():
    """Use daemon as context manager."""
    print("\n" + "="*70)
    print("Example 5: Context Manager Usage")
    print("="*70)
    
    with EdgeSystemLinterDaemon(watch_dir="src/") as daemon:
        print("Daemon created and started")
        
        daemon.run_once()
        stats = daemon.get_stats()
        
        print(f"Issues found: {stats['total_issues_found']}")
    
    print("Daemon cleaned up automatically")


# ============================================================================
# Example 6: File-Specific Linting
# ============================================================================

def example_file_specific_linting():
    """Lint specific files."""
    print("\n" + "="*70)
    print("Example 6: File-Specific Linting")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    # Lint specific files
    test_files = list(Path("src/").glob("*.py"))[:3]
    
    for filepath in test_files:
        print(f"\nLinting: {filepath}")
        
        issues, snapshot = daemon.lint_file_autonomous(filepath)
        
        print(f"  Issues found: {len(issues)}")
        print(f"  Errors: {snapshot.errors}")
        print(f"  Warnings: {snapshot.warnings}")
        
        if issues:
            print(f"  Top issues:")
            for issue in issues[:3]:
                print(f"    - {issue.get('rule', 'unknown')}: {issue.get('message', '')}")


# ============================================================================
# Example 7: Monitoring with Alerts
# ============================================================================

def example_monitoring_with_alerts():
    """Monitor code quality with alerts."""
    print("\n" + "="*70)
    print("Example 7: Monitoring with Alerts")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=1.0,
        max_history_snapshots=20
    )
    
    daemon.start()
    
    try:
        print("Monitoring for quality degradation...")
        
        for i in range(5):
            time.sleep(1)
            
            # Check for degradation
            for filepath in daemon.snapshots.keys():
                trend = daemon.get_trend_analysis(filepath)
                
                if trend and trend.error_trend == "degrading":
                    print(f"\n⚠️  ALERT: Quality degrading in {filepath}")
                    print(f"   Top issues: {trend.most_common_rules[:3]}")
            
            stats = daemon.get_stats()
            print(f"[{i+1}] Issues: {stats['total_issues_found']}, "
                  f"Fixes: {stats['total_auto_fixes']}")
    
    finally:
        daemon.stop()


# ============================================================================
# Example 8: Integration with Recovery System
# ============================================================================

def example_recovery_integration():
    """Integrate with recovery system."""
    print("\n" + "="*70)
    print("Example 8: Recovery System Integration")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        enable_recovery_integration=True,
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    daemon.run_once()
    
    # Collect violation data
    violations = []
    
    for filepath, snapshots in daemon.snapshots.items():
        if snapshots:
            snapshot = snapshots[-1]
            
            for issue in snapshot.issues:
                violations.append({
                    'file': filepath,
                    'rule': issue.get('rule'),
                    'severity': issue.get('severity'),
                    'message': issue.get('message'),
                    'line': issue.get('line'),
                    'auto_fixed': issue.get('auto_fixed', False)
                })
    
    print(f"Collected {len(violations)} violations")
    
    # Group by severity
    by_severity = {}
    for v in violations:
        severity = v['severity']
        by_severity.setdefault(severity, []).append(v)
    
    print("\nViolations by severity:")
    for severity, items in by_severity.items():
        print(f"  {severity}: {len(items)}")


# ============================================================================
# Example 9: Performance Monitoring
# ============================================================================

def example_performance_monitoring():
    """Monitor linting performance."""
    print("\n" + "="*70)
    print("Example 9: Performance Monitoring")
    print("="*70)
    
    import time
    
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    # Measure single run
    start = time.time()
    daemon.run_once()
    elapsed = time.time() - start
    
    stats = daemon.get_stats()
    
    print(f"Performance metrics:")
    print(f"  Time per lint: {elapsed:.3f}s")
    print(f"  Files processed: {stats['files_tracked']}")
    print(f"  Issues per file: {stats['total_issues_found'] / max(stats['files_tracked'], 1):.1f}")
    print(f"  Throughput: {stats['files_tracked'] / elapsed:.1f} files/sec")


# ============================================================================
# Example 10: Custom Configuration
# ============================================================================

def example_custom_configuration():
    """Use custom configuration."""
    print("\n" + "="*70)
    print("Example 10: Custom Configuration")
    print("="*70)
    
    # Create daemon with custom settings
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.MODERATE,
        check_interval=0.5,
        max_history_snapshots=50,
        enable_auto_fix=True,
        enable_recovery_integration=True,
        history_dir=".latti/custom_history"
    )
    
    print("Daemon configuration:")
    print(f"  Watch directory: {daemon.watch_dir}")
    print(f"  Auto-fix level: {daemon.auto_fix_level.name}")
    print(f"  Check interval: {daemon.check_interval}s")
    print(f"  Max history: {daemon.max_history_snapshots}")
    print(f"  Auto-fix enabled: {daemon.enable_auto_fix}")
    print(f"  Recovery integration: {daemon.enable_recovery_integration}")
    
    daemon.run_once()
    print(f"\nLinting complete")


# ============================================================================
# Example 11: Batch Processing
# ============================================================================

def example_batch_processing():
    """Process multiple directories."""
    print("\n" + "="*70)
    print("Example 11: Batch Processing")
    print("="*70)
    
    directories = ["src/", "tests/", "examples/"]
    results = {}
    
    for directory in directories:
        if Path(directory).exists():
            print(f"\nProcessing: {directory}")
            
            daemon = EdgeSystemLinterDaemon(
                watch_dir=directory,
                auto_fix_level=AutoFixLevel.SAFE
            )
            
            daemon.run_once()
            stats = daemon.get_stats()
            
            results[directory] = stats
            print(f"  Issues: {stats['total_issues_found']}")
            print(f"  Fixes: {stats['total_auto_fixes']}")
    
    # Summary
    print("\n" + "-"*70)
    print("Summary:")
    total_issues = sum(r['total_issues_found'] for r in results.values())
    total_fixes = sum(r['total_auto_fixes'] for r in results.values())
    
    print(f"  Total issues: {total_issues}")
    print(f"  Total fixes: {total_fixes}")
    print(f"  Fix rate: {(total_fixes/total_issues*100):.1f}%" if total_issues > 0 else "  Fix rate: N/A")


# ============================================================================
# Example 12: Report Generation
# ============================================================================

def example_report_generation():
    """Generate comprehensive reports."""
    print("\n" + "="*70)
    print("Example 12: Report Generation")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    # Run multiple times
    for _ in range(2):
        daemon.run_once()
        time.sleep(0.5)
    
    # Generate report
    report = daemon.report()
    print(report)
    
    # Save report
    report_file = Path(".latti/latest_report.txt")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(report)
    
    print(f"\nReport saved to: {report_file}")


# ============================================================================
# Main
# ============================================================================

def main():
    """Run all examples."""
    examples = [
        ("Basic Linting", example_basic_linting),
        ("Background Monitoring", example_background_monitoring),
        ("Auto-Fix Levels", example_auto_fix_levels),
        ("Trend Analysis", example_trend_analysis),
        ("Context Manager", example_context_manager),
        ("File-Specific Linting", example_file_specific_linting),
        ("Monitoring with Alerts", example_monitoring_with_alerts),
        ("Recovery Integration", example_recovery_integration),
        ("Performance Monitoring", example_performance_monitoring),
        ("Custom Configuration", example_custom_configuration),
        ("Batch Processing", example_batch_processing),
        ("Report Generation", example_report_generation),
    ]
    
    print("\n" + "="*70)
    print("EdgeSystemLinterDaemon Examples")
    print("="*70)
    print("\nAvailable examples:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    
    # Run all examples
    for name, example_func in examples:
        try:
            example_func()
        except Exception as e:
            logger.error(f"Error in {name}: {e}", exc_info=True)
        
        time.sleep(0.5)
    
    print("\n" + "="*70)
    print("All examples completed!")
    print("="*70)


if __name__ == "__main__":
    main()
