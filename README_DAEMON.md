# EdgeSystemLinterDaemon

A production-ready autonomous code linting daemon that continuously monitors, analyzes, and auto-fixes code quality issues with intelligent recovery integration.

## Features

### Core Capabilities

- **Autonomous Monitoring**: Continuously watches directories for code changes
- **Intelligent Linting**: Detects code quality issues with configurable severity levels
- **Auto-Fix System**: Automatically fixes issues at configurable aggressiveness levels
- **Trend Analysis**: Tracks code quality trends over time
- **Recovery Integration**: Reports violations to recovery system for tracking
- **History Management**: Maintains snapshots for historical analysis
- **Performance Optimized**: Efficient file watching and processing

### Auto-Fix Levels

1. **NONE**: No automatic fixes (analysis only)
2. **SAFE**: Only obvious, non-breaking fixes
3. **MODERATE**: Common patterns and style issues
4. **AGGRESSIVE**: Comprehensive refactoring and optimization

### Monitoring Features

- Real-time file change detection
- Configurable check intervals
- Trend analysis (improving/stable/degrading)
- Issue categorization by severity
- Auto-fix success tracking
- Performance metrics

## Installation

```bash
# From source
pip install -e .

# Or directly
pip install edge-system-linter-daemon
```

## Quick Start

### Basic Usage

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

# Create daemon
daemon = EdgeSystemLinterDaemon(watch_dir="src/")

# Run once
daemon.run_once()

# Print report
print(daemon.report())
```

### Background Monitoring

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel

# Create daemon with auto-fix
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE,
    check_interval=2.0
)

# Start background monitoring
daemon.start()

try:
    # Your application code
    run_application()
finally:
    daemon.stop()
```

### Context Manager

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

with EdgeSystemLinterDaemon(watch_dir="src/") as daemon:
    daemon.run_once()
    print(daemon.report())
```

## Configuration

### Constructor Parameters

```python
EdgeSystemLinterDaemon(
    watch_dir: str = ".",                    # Directory to monitor
    auto_fix_level: AutoFixLevel = SAFE,     # Auto-fix aggressiveness
    check_interval: float = 1.0,             # Check interval in seconds
    enable_auto_fix: bool = True,            # Enable auto-fixing
    enable_recovery_integration: bool = True, # Report to recovery system
    max_history_snapshots: int = 100,        # Max snapshots to keep
    history_dir: str = ".latti/lint_history" # History storage directory
)
```

### Configuration File

Create `.latti/daemon.config.json`:

```json
{
  "watch_dir": "src/",
  "auto_fix_level": "safe",
  "check_interval": 1.0,
  "enable_auto_fix": true,
  "enable_recovery_integration": true,
  "max_history_snapshots": 100,
  "history_dir": ".latti/lint_history"
}
```

## API Reference

### Core Methods

#### `run_once()`
Run linting once on all watched files.

```python
daemon.run_once()
```

#### `start()`
Start background monitoring daemon.

```python
daemon.start()
```

#### `stop()`
Stop background monitoring daemon.

```python
daemon.stop()
```

#### `lint_file_autonomous(filepath)`
Lint a specific file autonomously.

```python
issues, snapshot = daemon.lint_file_autonomous("src/module.py")
```

Returns:
- `issues`: List of detected issues
- `snapshot`: LintSnapshot object with detailed results

### Analysis Methods

#### `get_stats()`
Get current statistics.

```python
stats = daemon.get_stats()
# Returns:
# {
#     'total_lints': int,
#     'total_issues_found': int,
#     'total_auto_fixes': int,
#     'files_tracked': int,
#     'last_lint_time': float
# }
```

#### `get_trend_analysis(filepath)`
Analyze trends for a specific file.

```python
trend = daemon.get_trend_analysis("src/module.py")
# Returns TrendAnalysis object with:
# - snapshots_count: Number of snapshots
# - error_trend: "improving" | "stable" | "degrading"
# - warning_trend: "improving" | "stable" | "degrading"
# - total_issues_fixed: Number of issues fixed
# - most_common_rules: List of (rule, count) tuples
```

#### `report()`
Generate comprehensive report.

```python
report = daemon.report()
print(report)
```

### Properties

#### `is_running`
Check if daemon is running.

```python
if daemon.is_running:
    print("Daemon is active")
```

#### `snapshots`
Access all snapshots.

```python
for filepath, snapshots in daemon.snapshots.items():
    print(f"{filepath}: {len(snapshots)} snapshots")
```

## Issue Format

Issues are dictionaries with the following structure:

```python
{
    'rule': str,           # Rule identifier (e.g., 'E501')
    'severity': str,       # 'error' | 'warning' | 'info'
    'message': str,        # Human-readable message
    'line': int,           # Line number (optional)
    'column': int,         # Column number (optional)
    'auto_fixed': bool,    # Whether auto-fixed
    'fix_details': str     # Details of fix applied (optional)
}
```

## Snapshot Structure

```python
class LintSnapshot:
    filepath: str                    # File path
    timestamp: float                 # Unix timestamp
    issues: List[Dict]              # List of issues
    errors: int                     # Error count
    warnings: int                   # Warning count
    auto_fixes_applied: int         # Number of auto-fixes
    processing_time: float          # Time to lint file
```

## Trend Analysis

```python
class TrendAnalysis:
    snapshots_count: int                    # Number of snapshots
    error_trend: str                        # "improving" | "stable" | "degrading"
    warning_trend: str                      # "improving" | "stable" | "degrading"
    total_issues_fixed: int                 # Total issues fixed
    most_common_rules: List[Tuple[str, int]] # Top rules by frequency
```

## Examples

### Example 1: One-Time Linting

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

daemon = EdgeSystemLinterDaemon(watch_dir="src/")
daemon.run_once()

stats = daemon.get_stats()
print(f"Found {stats['total_issues_found']} issues")
print(daemon.report())
```

### Example 2: Continuous Monitoring

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
import time

daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE,
    check_interval=2.0
)

daemon.start()

try:
    for i in range(10):
        time.sleep(2)
        stats = daemon.get_stats()
        print(f"Issues: {stats['total_issues_found']}, "
              f"Fixes: {stats['total_auto_fixes']}")
finally:
    daemon.stop()
```

### Example 3: Trend Analysis

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

daemon = EdgeSystemLinterDaemon(watch_dir="src/")

# Build history
for _ in range(5):
    daemon.run_once()
    time.sleep(1)

# Analyze trends
for filepath in daemon.snapshots.keys():
    trend = daemon.get_trend_analysis(filepath)
    
    if trend:
        print(f"\n{filepath}:")
        print(f"  Error trend: {trend.error_trend}")
        print(f"  Top issues: {trend.most_common_rules[:3]}")
```

### Example 4: Quality Monitoring with Alerts

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

daemon = EdgeSystemLinterDaemon(watch_dir="src/")
daemon.start()

try:
    while daemon.is_running:
        time.sleep(5)
        
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            
            if trend and trend.error_trend == "degrading":
                print(f"⚠️  Quality degrading in {filepath}")
                print(f"   Top issues: {trend.most_common_rules[:3]}")
finally:
    daemon.stop()
```

### Example 5: Integration with Recovery System

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    enable_recovery_integration=True
)

daemon.run_once()

# Collect violations
violations = []
for filepath, snapshots in daemon.snapshots.items():
    if snapshots:
        for issue in snapshots[-1].issues:
            violations.append({
                'file': filepath,
                'rule': issue['rule'],
                'severity': issue['severity'],
                'auto_fixed': issue.get('auto_fixed', False)
            })

print(f"Collected {len(violations)} violations")
```

## Integration Guides

### CI/CD Integration

See [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md#cicd-integration) for:
- GitHub Actions
- GitLab CI
- Jenkins
- Pre-commit hooks

### Monitoring Integration

See [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md#monitoring-integration) for:
- Continuous monitoring
- Metrics collection
- Prometheus integration
- Datadog integration

### Alert Integration

See [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md#alert-integration) for:
- Slack alerts
- Email alerts
- Custom alerting

## Performance Considerations

### Memory Usage

- Each snapshot stores file issues and metadata
- Default: 100 snapshots per file
- Reduce `max_history_snapshots` for large codebases

```python
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    max_history_snapshots=20  # Reduce history
)
```

### CPU Usage

- Check interval controls frequency
- Larger intervals reduce CPU usage
- Default: 1.0 second

```python
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    check_interval=5.0  # Check every 5 seconds
)
```

### Disk Usage

- History stored in `.latti/lint_history/`
- Clean up old snapshots periodically

```bash
# Clean history
rm -rf .latti/lint_history/
```

## Troubleshooting

### Daemon not detecting changes

**Problem**: Files are modified but daemon doesn't detect them.

**Solutions**:
1. Verify watch directory exists: `Path(watch_dir).exists()`
2. Check file permissions: `os.access(filepath, os.R_OK)`
3. Increase check interval: `check_interval=2.0`

### Auto-fixes not applied

**Problem**: Issues found but not auto-fixed.

**Solutions**:
1. Verify `enable_auto_fix=True`
2. Check `auto_fix_level` is not `NONE`
3. Verify file write permissions
4. Check logs for error messages

### High memory usage

**Problem**: Daemon consuming too much memory.

**Solutions**:
1. Reduce `max_history_snapshots`: `max_history_snapshots=20`
2. Clean history: `rm -rf .latti/lint_history/`
3. Increase `check_interval`: `check_interval=5.0`

### Performance issues

**Problem**: Linting is slow.

**Solutions**:
1. Exclude large directories from watch
2. Increase `check_interval`
3. Use `AutoFixLevel.SAFE` instead of `AGGRESSIVE`
4. Reduce number of files being watched

## Best Practices

### 1. Use Appropriate Auto-Fix Levels

```python
# Development: More aggressive
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.MODERATE
)

# CI/CD: Conservative
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE
)
```

### 2. Monitor Trends

```python
# Alert on degradation
for filepath in daemon.snapshots.keys():
    trend = daemon.get_trend_analysis(filepath)
    if trend and trend.error_trend == "degrading":
        send_alert(f"Quality degrading in {filepath}")
```

### 3. Regular Reporting

```python
# Generate daily reports
import schedule

def daily_report():
    daemon.run_once()
    report = daemon.report()
    send_email(report)

schedule.every().day.at("09:00").do(daily_report)
```

### 4. Handle Errors Gracefully

```python
try:
    daemon.run_once()
except Exception as e:
    logger.error(f"Linting error: {e}")
    # Continue operation
```

### 5. Clean Up Resources

```python
try:
    daemon.start()
    # Your code
finally:
    daemon.stop()  # Always stop daemon
```

## Testing

Run the test suite:

```bash
pytest tests/test_daemon.py -v
```

Run specific tests:

```bash
pytest tests/test_daemon.py::TestEdgeSystemLinterDaemon::test_run_once -v
```

Run with coverage:

```bash
pytest tests/test_daemon.py --cov=src/edge_system_linter_daemon
```

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or suggestions:

1. Check [Troubleshooting](#troubleshooting) section
2. Review [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md)
3. Check existing issues on GitHub
4. Create a new issue with details

## Changelog

### Version 1.0.0

- Initial release
- Core linting daemon
- Auto-fix system
- Trend analysis
- Recovery integration
- Comprehensive testing

## See Also

- [INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) - Integration patterns
- [LINTER_GUIDE.md](docs/LINTER_GUIDE.md) - Linting rules and configuration
- [examples/daemon_example.py](examples/daemon_example.py) - Practical examples
- [tests/test_daemon.py](tests/test_daemon.py) - Test suite
