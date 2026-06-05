#!/usr/bin/env python3
"""
Practical examples for EdgeSystemLinterDaemon.

This file demonstrates common use cases and patterns.
"""

import time
from pathlib import Path
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel


# ============================================================================
# Example 1: Basic One-Time Linting
# ============================================================================

def example_basic_linting():
    """Run linting once and print results."""
    print("\n" + "="*70)
    print("Example 1: Basic One-Time Linting")
    print("="*70)
    
    # Create daemon
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    # Run linting
    daemon.run_once()
    
    # Get statistics
    stats = daemon.get_stats()
    print(f"\nStatistics:")
    print(f"  Total lints: {stats['total_lints']}")
    print(f"  Issues found: {stats['total_issues_found']}")
    print(f"  Auto-fixes: {stats['total_auto_fixes']}")
    print(f"  Files tracked: {stats['files_tracked']}")
    
    # Print full report
    print(f"\nFull Report:")
    print(daemon.report())


# ============================================================================
# Example 2: Continuous Monitoring
# ============================================================================

def example_continuous_monitoring():
    """Monitor code quality continuously."""
    print("\n" + "="*70)
    print("Example 2: Continuous Monitoring")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.SAFE,
        check_interval=2.0
    )
    
    print("\nStarting daemon (will run for 10 seconds)...")
    daemon.start()
    
    try:
        for i in range(5):
            time.sleep(2)
            stats = daemon.get_stats()
            print(f"  [{i+1}] Issues: {stats['total_issues_found']}, "
                  f"Fixes: {stats['total_auto_fixes']}")
    finally:
        daemon.stop()
        print("\nDaemon stopped")


# ============================================================================
# Example 3: Trend Analysis
# ============================================================================

def example_trend_analysis():
    """Analyze code quality trends."""
    print("\n" + "="*70)
    print("Example 3: Trend Analysis")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        max_history_snapshots=50
    )
    
    # Build history by running multiple times
    print("\nBuilding history (5 linting runs)...")
    for i in range(5):
        daemon.run_once()
        time.sleep(0.5)
        print(f"  Run {i+1}/5 complete")
    
    # Analyze trends
    print("\nTrend Analysis:")
    for filepath in list(daemon.snapshots.keys())[:3]:
        trend = daemon.get_trend_analysis(filepath)
        
        if trend:
            print(f"\n  {filepath}:")
            print(f"    Snapshots: {trend.snapshots_count}")
            print(f"    Error trend: {trend.error_trend}")
            print(f"    Warning trend: {trend.warning_trend}")
            print(f"    Total fixed: {trend.total_issues_fixed}")
            
            if trend.most_common_rules:
                print(f"    Top issues:")
                for rule, count in trend.most_common_rules[:3]:
                    print(f"      - {rule}: {count}")


# ============================================================================
# Example 4: Auto-Fix Levels
# ============================================================================

def example_auto_fix_levels():
    """Demonstrate different auto-fix levels."""
    print("\n" + "="*70)
    print("Example 4: Auto-Fix Levels")
    print("="*70)
    
    levels = [
        (AutoFixLevel.NONE, "No fixes"),
        (AutoFixLevel.SAFE, "Safe fixes only"),
        (AutoFixLevel.MODERATE, "Common patterns"),
        (AutoFixLevel.AGGRESSIVE, "Comprehensive"),
    ]
    
    for level, description in levels:
        print(f"\n  Testing {description} ({level.name})...")
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir="src/",
            auto_fix_level=level
        )
        
        daemon.run_once()
        stats = daemon.get_stats()
        
        print(f"    Issues found: {stats['total_issues_found']}")
        print(f"    Auto-fixes: {stats['total_auto_fixes']}")


# ============================================================================
# Example 5: Context Manager Usage
# ============================================================================

def example_context_manager():
    """Use daemon as context manager."""
    print("\n" + "="*70)
    print("Example 5: Context Manager Usage")
    print("="*70)
    
    with EdgeSystemLinterDaemon(watch_dir="src/") as daemon:
        print("\nDaemon created and ready")
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
    test_files = [
        "src/module1.py",
        "src/module2.py",
        "src/utils.py"
    ]
    
    for filepath in test_files:
        if Path(filepath).exists():
            print(f"\nLinting {filepath}...")
            issues, snapshot = daemon.lint_file_autonomous(filepath)
            
            print(f"  Issues: {len(issues)}")
            print(f"  Errors: {snapshot.errors}")
            print(f"  Warnings: {snapshot.warnings}")
            
            if issues:
                print(f"  Details:")
                for issue in issues[:3]:
                    print(f"    - {issue['rule']}: {issue['message']}")


# ============================================================================
# Example 7: Quality Monitoring with Alerts
# ============================================================================

def example_quality_monitoring_with_alerts():
    """Monitor quality and alert on degradation."""
    print("\n" + "="*70)
    print("Example 7: Quality Monitoring with Alerts")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    print("\nMonitoring for 10 seconds...")
    daemon.start()
    
    try:
        for i in range(5):
            time.sleep(2)
            
            # Check for degradation
            for filepath in daemon.snapshots.keys():
                trend = daemon.get_trend_analysis(filepath)
                
                if trend:
                    if trend.error_trend == "degrading":
                        print(f"\n⚠️  ALERT: Quality degrading in {filepath}")
                        print(f"   Top issues: {trend.most_common_rules[:3]}")
                    
                    if trend.warning_trend == "improving":
                        print(f"\n✅ GOOD: Quality improving in {filepath}")
    finally:
        daemon.stop()


# ============================================================================
# Example 8: Integration with Recovery System
# ============================================================================

def example_recovery_integration():
    """Integrate with recovery system."""
    print("\n" + "="*70)
    print("Example 8: Integration with Recovery System")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        enable_recovery_integration=True
    )
    
    daemon.run_once()
    
    # Collect violations for recovery system
    violations = []
    
    for filepath, snapshots in daemon.snapshots.items():
        if snapshots:
            latest = snapshots[-1]
            
            for issue in latest.issues:
                violations.append({
                    'file': filepath,
                    'rule': issue['rule'],
                    'severity': issue['severity'],
                    'message': issue['message'],
                    'auto_fixed': issue.get('auto_fixed', False),
                    'timestamp': latest.timestamp
                })
    
    print(f"\nCollected {len(violations)} violations")
    
    # Group by severity
    by_severity = {}
    for v in violations:
        severity = v['severity']
        by_severity.setdefault(severity, []).append(v)
    
    for severity, items in by_severity.items():
        print(f"\n  {severity.upper()}: {len(items)}")
        for item in items[:3]:
            print(f"    - {item['file']}: {item['rule']}")


# ============================================================================
# Example 9: Performance Optimization
# ============================================================================

def example_performance_optimization():
    """Optimize daemon performance."""
    print("\n" + "="*70)
    print("Example 9: Performance Optimization")
    print("="*70)
    
    # Configuration for different scenarios
    configs = [
        {
            'name': 'Development',
            'check_interval': 1.0,
            'max_history': 100,
            'auto_fix_level': AutoFixLevel.MODERATE
        },
        {
            'name': 'CI/CD',
            'check_interval': 5.0,
            'max_history': 20,
            'auto_fix_level': AutoFixLevel.SAFE
        },
        {
            'name': 'Production',
            'check_interval': 10.0,
            'max_history': 10,
            'auto_fix_level': AutoFixLevel.NONE
        }
    ]
    
    for config in configs:
        print(f"\n  {config['name']} Configuration:")
        print(f"    Check interval: {config['check_interval']}s")
        print(f"    Max history: {config['max_history']}")
        print(f"    Auto-fix level: {config['auto_fix_level'].name}")
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir="src/",
            check_interval=config['check_interval'],
            max_history_snapshots=config['max_history'],
            auto_fix_level=config['auto_fix_level']
        )
        
        daemon.run_once()
        stats = daemon.get_stats()
        print(f"    Issues found: {stats['total_issues_found']}")


# ============================================================================
# Example 10: Custom Reporting
# ============================================================================

def example_custom_reporting():
    """Generate custom reports."""
    print("\n" + "="*70)
    print("Example 10: Custom Reporting")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    daemon.run_once()
    
    # Generate custom report
    report = "# Code Quality Report\n\n"
    
    stats = daemon.get_stats()
    report += f"## Summary\n"
    report += f"- Total issues: {stats['total_issues_found']}\n"
    report += f"- Auto-fixes: {stats['total_auto_fixes']}\n"
    report += f"- Files tracked: {stats['files_tracked']}\n\n"
    
    # File-by-file breakdown
    report += "## File Details\n\n"
    
    for filepath, snapshots in daemon.snapshots.items():
        if snapshots:
            latest = snapshots[-1]
            report += f"### {filepath}\n"
            report += f"- Errors: {latest.errors}\n"
            report += f"- Warnings: {latest.warnings}\n"
            report += f"- Processing time: {latest.processing_time:.3f}s\n"
            
            if latest.issues:
                report += "- Issues:\n"
                for issue in latest.issues[:5]:
                    report += f"  - {issue['rule']}: {issue['message']}\n"
            
            report += "\n"
    
    print(report)
    
    # Save report
    Path(".latti").mkdir(exist_ok=True)
    Path(".latti/custom_report.md").write_text(report)
    print("Report saved to .latti/custom_report.md")


# ============================================================================
# Example 11: Batch Processing
# ============================================================================

def example_batch_processing():
    """Process multiple files in batch."""
    print("\n" + "="*70)
    print("Example 11: Batch Processing")
    print("="*70)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    # Get all Python files
    src_dir = Path("src/")
    py_files = list(src_dir.glob("**/*.py"))
    
    print(f"\nProcessing {len(py_files)} files...")
    
    results = {
        'total_issues': 0,
        'total_fixes': 0,
        'files_with_issues': 0
    }
    
    for filepath in py_files:
        issues, snapshot = daemon.lint_file_autonomous(str(filepath))
        
        if issues:
            results['files_with_issues'] += 1
            results['total_issues'] += len(issues)
            results['total_fixes'] += snapshot.auto_fixes_applied
    
    print(f"\nBatch Results:")
    print(f"  Files with issues: {results['files_with_issues']}")
    print(f"  Total issues: {results['total_issues']}")
    print(f"  Total fixes: {results['total_fixes']}")


# ============================================================================
# Example 12: Error Handling
# ============================================================================

def example_error_handling():
    """Handle errors gracefully."""
    print("\n" + "="*70)
    print("Example 12: Error Handling")
    print("="*70)
    
    try:
        # Non-existent directory
        daemon = EdgeSystemLinterDaemon(watch_dir="nonexistent/")
        daemon.run_once()
    except FileNotFoundError as e:
        print(f"\n✓ Caught expected error: {e}")
    
    try:
        # Invalid auto-fix level
        daemon = EdgeSystemLinterDaemon(
            watch_dir="src/",
            auto_fix_level="invalid"
        )
    except ValueError as e:
        print(f"✓ Caught expected error: {e}")
    
    # Graceful degradation
    try:
        daemon = EdgeSystemLinterDaemon(watch_dir="src/")
        daemon.run_once()
        print("\n✓ Daemon handled errors gracefully")
    except Exception as e:
        print(f"✓ Caught error: {e}")
        print("  Continuing operation...")


# ============================================================================
# Main
# ============================================================================

def main():
    """Run all examples."""
    print("\n" + "="*70)
    print("EdgeSystemLinterDaemon - Practical Examples")
    print("="*70)
    
    examples = [
        ("Basic Linting", example_basic_linting),
        ("Continuous Monitoring", example_continuous_monitoring),
        ("Trend Analysis", example_trend_analysis),
        ("Auto-Fix Levels", example_auto_fix_levels),
        ("Context Manager", example_context_manager),
        ("File-Specific Linting", example_file_specific_linting),
        ("Quality Monitoring", example_quality_monitoring_with_alerts),
        ("Recovery Integration", example_recovery_integration),
        ("Performance Optimization", example_performance_optimization),
        ("Custom Reporting", example_custom_reporting),
        ("Batch Processing", example_batch_processing),
        ("Error Handling", example_error_handling),
    ]
    
    for i, (name, func) in enumerate(examples, 1):
        try:
            func()
        except Exception as e:
            print(f"\n❌ Example {i} ({name}) failed: {e}")
        
        if i < len(examples):
            input("\nPress Enter to continue to next example...")
    
    print("\n" + "="*70)
    print("All examples completed!")
    print("="*70)


if __name__ == "__main__":
    main()
