#!/usr/bin/env python3
"""
Practical example: Running EdgeSystemLinterDaemon autonomously.

This demonstrates how the daemon runs completely autonomously
with zero human intervention once started.
"""

import time
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel


def example_1_fire_and_forget():
    """
    Example 1: Fire-and-forget autonomous daemon.
    
    Start the daemon and let it run forever.
    """
    print("\n" + "="*60)
    print("EXAMPLE 1: Fire-and-Forget Autonomous Daemon")
    print("="*60)
    
    # Create daemon
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=5.0,
        enable_auto_fix=True,
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    # Start it - runs autonomously in background
    daemon.start()
    print("✓ Daemon started - running autonomously in background")
    print("✓ Will monitor 'src/' directory every 5 seconds")
    print("✓ Will automatically fix safe issues")
    print("✓ No further interaction needed")
    
    # Daemon runs autonomously while we do other things
    print("\nDaemon is now running autonomously...")
    print("You can query stats anytime:")
    
    for i in range(3):
        time.sleep(2)
        stats = daemon.get_stats()
        print(f"\n  [{i+1}] Uptime: {stats['uptime_seconds']:.1f}s, "
              f"Lints: {stats['total_lints']}, "
              f"Issues: {stats['total_issues_found']}, "
              f"Fixes: {stats['total_auto_fixes']}")
    
    # Stop when done
    daemon.stop()
    print("\n✓ Daemon stopped gracefully")


def example_2_with_monitoring():
    """
    Example 2: Autonomous daemon with active monitoring.
    
    Start daemon and monitor its progress.
    """
    print("\n" + "="*60)
    print("EXAMPLE 2: Autonomous Daemon with Monitoring")
    print("="*60)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=3.0,
        enable_auto_fix=True,
        auto_fix_level=AutoFixLevel.MODERATE
    )
    
    daemon.start()
    print("✓ Daemon started with MODERATE auto-fix level")
    
    # Monitor autonomously running daemon
    print("\nMonitoring autonomous daemon:")
    for i in range(5):
        time.sleep(1)
        stats = daemon.get_stats()
        
        if stats['running']:
            print(f"\n  Iteration {i+1}:")
            print(f"    Running: {stats['running']}")
            print(f"    Uptime: {stats['uptime_seconds']:.1f}s")
            print(f"    Total lints: {stats['total_lints']}")
            print(f"    Issues found: {stats['total_issues_found']}")
            print(f"    Auto-fixes: {stats['total_auto_fixes']}")
            print(f"    Files tracked: {stats['files_tracked']}")
    
    daemon.stop()
    print("\n✓ Daemon stopped")
    
    # Get final report
    report = daemon.report()
    print("\nFinal Report:")
    print(report)


def example_3_context_manager():
    """
    Example 3: Using context manager for automatic cleanup.
    
    Daemon runs autonomously and stops automatically.
    """
    print("\n" + "="*60)
    print("EXAMPLE 3: Context Manager (Auto-cleanup)")
    print("="*60)
    
    with EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=2.0,
        enable_auto_fix=True,
        auto_fix_level=AutoFixLevel.SAFE
    ) as daemon:
        daemon.start()
        print("✓ Daemon started (will auto-stop on exit)")
        
        # Daemon runs autonomously
        for i in range(3):
            time.sleep(1)
            stats = daemon.get_stats()
            print(f"  [{i+1}] Running: {stats['running']}, "
                  f"Lints: {stats['total_lints']}")
    
    print("✓ Daemon auto-stopped (exited context)")


def example_4_single_pass():
    """
    Example 4: Single pass (non-autonomous).
    
    For comparison - runs once then stops.
    """
    print("\n" + "="*60)
    print("EXAMPLE 4: Single Pass (Non-Autonomous)")
    print("="*60)
    
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        enable_auto_fix=True,
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    # Run once - doesn't loop
    daemon.run_once()
    print("✓ Single pass complete")
    
    stats = daemon.get_stats()
    print(f"\nStats:")
    print(f"  Lints: {stats['total_lints']}")
    print(f"  Issues: {stats['total_issues_found']}")
    print(f"  Fixes: {stats['total_auto_fixes']}")


def example_5_production_scenario():
    """
    Example 5: Production monitoring scenario.
    
    Daemon runs 24/7 with minimal overhead.
    """
    print("\n" + "="*60)
    print("EXAMPLE 5: Production Monitoring Scenario")
    print("="*60)
    
    # In production, you'd use a longer check interval
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        check_interval=60.0,  # Check every minute
        enable_auto_fix=True,
        auto_fix_level=AutoFixLevel.SAFE
    )
    
    daemon.start()
    print("✓ Production daemon started")
    print("✓ Will check every 60 seconds")
    print("✓ Will apply safe fixes automatically")
    print("✓ Runs 24/7 with minimal CPU/memory overhead")
    
    # Simulate production uptime
    print("\nSimulating production uptime (5 seconds):")
    for i in range(5):
        time.sleep(1)
        stats = daemon.get_stats()
        print(f"  [{i+1}s] Uptime: {stats['uptime_seconds']:.1f}s, "
              f"Status: {'RUNNING' if stats['running'] else 'STOPPED'}")
    
    daemon.stop()
    print("\n✓ Production daemon stopped")


def main():
    """Run all examples."""
    print("\n" + "="*60)
    print("EdgeSystemLinterDaemon - Autonomous Examples")
    print("="*60)
    
    examples = [
        ("Fire-and-Forget", example_1_fire_and_forget),
        ("With Monitoring", example_2_with_monitoring),
        ("Context Manager", example_3_context_manager),
        ("Single Pass", example_4_single_pass),
        ("Production Scenario", example_5_production_scenario),
    ]
    
    for name, func in examples:
        try:
            func()
        except Exception as e:
            print(f"\n✗ Error in {name}: {e}")
    
    print("\n" + "="*60)
    print("All examples completed!")
    print("="*60)
    print("\nKey Takeaways:")
    print("  ✓ Daemon runs autonomously in background thread")
    print("  ✓ No human intervention needed after start()")
    print("  ✓ Can query stats anytime while running")
    print("  ✓ Stops gracefully on demand")
    print("  ✓ Perfect for CI/CD, dev, and production")


if __name__ == "__main__":
    main()
