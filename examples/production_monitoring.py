#!/usr/bin/env python3
"""
Production Monitoring Example for EdgeSystemLinterDaemon

Demonstrates how to deploy and monitor the autonomous linter daemon in production.

This example shows:
- Daemon deployment in production environment
- Health monitoring and alerting
- Metrics collection and reporting
- Graceful shutdown and recovery
- Integration with monitoring systems (Prometheus, DataDog, etc.)
"""

import sys
import os
import json
import time
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from edge_system_linter_daemon import EdgeSystemLinterDaemon
from edge_system_linter import EdgeSystemLinter


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class HealthMetrics:
    """Health metrics for the daemon."""
    timestamp: str
    daemon_running: bool
    last_lint_time: Optional[str]
    total_lints: int
    total_issues_found: int
    avg_lint_duration: float
    error_count: int
    uptime_seconds: float


class ProductionMonitor:
    """Monitors and manages the linter daemon in production."""
    
    def __init__(self, repo_path: str, metrics_dir: str = "metrics"):
        """
        Initialize production monitor.
        
        Args:
            repo_path: Path to repository to lint
            metrics_dir: Directory for metrics and logs
        """
        self.repo_path = repo_path
        self.metrics_dir = Path(metrics_dir)
        self.metrics_dir.mkdir(exist_ok=True)
        
        self.daemon = None
        self.linter = EdgeSystemLinter(repo_path)
        
        # Metrics tracking
        self.metrics = {
            'total_lints': 0,
            'total_issues': 0,
            'lint_durations': [],
            'errors': [],
            'start_time': datetime.now(),
            'last_lint_time': None,
        }
        
        self.running = False
        self.monitor_thread = None
        
    def start_daemon(self, config: dict = None):
        """Start the linter daemon with production configuration."""
        if config is None:
            config = {
                'check_interval': 300,  # 5 minutes
                'max_iterations': None,  # Run indefinitely
                'enable_auto_fix': True,
                'verbose': False,
                'report_format': 'json'
            }
        
        self.daemon = EdgeSystemLinterDaemon(
            repo_path=self.repo_path,
            config=config
        )
        
        logger.info("✅ Daemon started in production mode")
        
    def collect_metrics(self) -> Dict:
        """Collect current metrics from daemon."""
        return {
            'timestamp': datetime.now().isoformat(),
            'total_lints': self.metrics['total_lints'],
            'total_issues': self.metrics['total_issues'],
            'avg_lint_duration': (
                sum(self.metrics['lint_durations']) / len(self.metrics['lint_durations'])
                if self.metrics['lint_durations'] else 0
            ),
            'error_count': len(self.metrics['errors']),
            'uptime': (datetime.now() - self.metrics['start_time']).total_seconds(),
        }
        
    def run_linting_iteration(self) -> Dict:
        """Run a single linting iteration and collect metrics."""
        start_time = time.time()
        
        try:
            results = self.linter.lint_repository()
            duration = time.time() - start_time
            
            self.metrics['total_lints'] += 1
            self.metrics['lint_durations'].append(duration)
            self.metrics['total_issues'] += len(results.get('issues', []))
            self.metrics['last_lint_time'] = datetime.now()
            
            logger.info(f"✅ Lint completed in {duration:.2f}s, found {len(results.get('issues', []))} issues")
            
            return {
                'success': True,
                'duration': duration,
                'issues_found': len(results.get('issues', [])),
                'results': results
            }
            
        except Exception as e:
            duration = time.time() - start_time
            self.metrics['errors'].append({
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            })
            logger.error(f"❌ Lint failed: {e}")
            
            return {
                'success': False,
                'duration': duration,
                'error': str(e)
            }
            
    def get_health_status(self) -> HealthMetrics:
        """Get current health status."""
        metrics = self.collect_metrics()
        
        return HealthMetrics(
            timestamp=metrics['timestamp'],
            daemon_running=self.running,
            last_lint_time=self.metrics['last_lint_time'].isoformat() if self.metrics['last_lint_time'] else None,
            total_lints=metrics['total_lints'],
            total_issues_found=metrics['total_issues'],
            avg_lint_duration=metrics['avg_lint_duration'],
            error_count=metrics['error_count'],
            uptime_seconds=metrics['uptime']
        )
        
    def check_health_alerts(self) -> List[str]:
        """Check for health alerts."""
        alerts = []
        health = self.get_health_status()
        
        # Check error rate
        if health.error_count > 10:
            alerts.append(f"⚠️  High error count: {health.error_count}")
        
        # Check if daemon is stale
        if health.last_lint_time:
            last_lint = datetime.fromisoformat(health.last_lint_time)
            if datetime.now() - last_lint > timedelta(hours=1):
                alerts.append("⚠️  No linting activity in last hour")
        
        # Check average duration
        if health.avg_lint_duration > 300:  # 5 minutes
            alerts.append(f"⚠️  Slow linting: {health.avg_lint_duration:.1f}s average")
        
        return alerts
        
    def save_metrics_snapshot(self):
        """Save current metrics to file."""
        health = self.get_health_status()
        
        snapshot_path = self.metrics_dir / f"metrics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        
        with open(snapshot_path, 'w') as f:
            json.dump(asdict(health), f, indent=2)
        
        logger.info(f"📊 Metrics saved to {snapshot_path}")
        
    def export_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format."""
        health = self.get_health_status()
        
        metrics_text = f"""# HELP edge_linter_total_lints Total number of linting runs
# TYPE edge_linter_total_lints counter
edge_linter_total_lints {health.total_lints}

# HELP edge_linter_total_issues Total issues found
# TYPE edge_linter_total_issues counter
edge_linter_total_issues {health.total_issues_found}

# HELP edge_linter_avg_duration Average linting duration in seconds
# TYPE edge_linter_avg_duration gauge
edge_linter_avg_duration {health.avg_lint_duration}

# HELP edge_linter_errors Total errors
# TYPE edge_linter_errors counter
edge_linter_errors {health.error_count}

# HELP edge_linter_uptime Daemon uptime in seconds
# TYPE edge_linter_uptime gauge
edge_linter_uptime {health.uptime_seconds}

# HELP edge_linter_running Daemon running status
# TYPE edge_linter_running gauge
edge_linter_running {1 if health.daemon_running else 0}
"""
        
        return metrics_text
        
    def monitoring_loop(self, interval: int = 300):
        """
        Main monitoring loop.
        
        Args:
            interval: Monitoring interval in seconds
        """
        logger.info(f"🔄 Starting monitoring loop (interval: {interval}s)")
        self.running = True
        
        while self.running:
            try:
                # Run linting iteration
                result = self.run_linting_iteration()
                
                # Check health
                alerts = self.check_health_alerts()
                if alerts:
                    for alert in alerts:
                        logger.warning(alert)
                
                # Save metrics
                self.save_metrics_snapshot()
                
                # Sleep until next iteration
                time.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("⏹️  Monitoring loop interrupted")
                break
            except Exception as e:
                logger.error(f"❌ Monitoring loop error: {e}")
                time.sleep(interval)
                
    def start_monitoring(self, interval: int = 300):
        """
        Start monitoring in background thread.
        
        Args:
            interval: Monitoring interval in seconds
        """
        self.monitor_thread = threading.Thread(
            target=self.monitoring_loop,
            args=(interval,),
            daemon=False
        )
        self.monitor_thread.start()
        logger.info("✅ Monitoring thread started")
        
    def stop_monitoring(self):
        """Stop monitoring gracefully."""
        logger.info("⏹️  Stopping monitoring...")
        self.running = False
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=10)
        
        logger.info("✅ Monitoring stopped")
        
    def generate_report(self) -> str:
        """Generate production report."""
        health = self.get_health_status()
        
        report = f"""
╔════════════════════════════════════════════════════════════╗
║         EdgeSystemLinter Production Report                 ║
╚════════════════════════════════════════════════════════════╝

📊 Status: {'🟢 RUNNING' if health.daemon_running else '🔴 STOPPED'}
⏰ Timestamp: {health.timestamp}

📈 Metrics:
  • Total Lints: {health.total_lints}
  • Total Issues Found: {health.total_issues_found}
  • Average Duration: {health.avg_lint_duration:.2f}s
  • Errors: {health.error_count}
  • Uptime: {health.uptime_seconds / 3600:.1f} hours

🔍 Last Lint: {health.last_lint_time or 'Never'}

⚠️  Alerts:
"""
        
        alerts = self.check_health_alerts()
        if alerts:
            for alert in alerts:
                report += f"  {alert}\n"
        else:
            report += "  ✅ No alerts\n"
        
        return report


def main():
    """Main entry point for production monitoring."""
    repo_path = os.getenv('REPO_PATH', '.')
    
    monitor = ProductionMonitor(repo_path)
    
    try:
        # Start daemon
        monitor.start_daemon()
        
        # Start monitoring
        monitor.start_monitoring(interval=300)
        
        # Print initial report
        print(monitor.generate_report())
        
        # Keep running
        while True:
            time.sleep(3600)  # Print report every hour
            print(monitor.generate_report())
            
    except KeyboardInterrupt:
        print("\n⏹️  Shutting down...")
        monitor.stop_monitoring()
        print("✅ Shutdown complete")


if __name__ == '__main__':
    main()
