# Edge System Linter Daemon Guide

## Overview

The **EdgeSystemLinterDaemon** is an autonomous, self-looping linter that continuously monitors your codebase for violations of edge system patterns and automatically applies fixes.

### Key Features

1. **Autonomous Monitoring**: Watches for file changes and automatically re-lints
2. **Self-Healing**: Applies safe fixes automatically at configurable levels
3. **History Tracking**: Records all lint results with timestamps and trends
4. **Trend Analysis**: Detects improving/degrading code quality over time
5. **Background Daemon**: Runs in a separate thread without blocking your code
6. **Recovery Integration**: Reports violations to the recovery system
7. **Configurable Fix Levels**: From no fixes to aggressive auto-correction

## Installation

The daemon is part of the edge system linter module:

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
```

## Quick Start

### Basic Usage

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon

# Create daemon
daemon = EdgeSystemLinterDaemon(watch_dir="src/")

# Start monitoring in background
daemon.start()

# ... your code runs ...

# Stop when done
daemon.stop()
```

### Single Pass

```python
daemon = EdgeSystemLinterDaemon(watch_dir="src/")
daemon.run_once()  # Lint all files once and exit
```

### Context Manager

```python
with EdgeSystemLinterDaemon(watch_dir="src/") as daemon:
    daemon.run_once()
# Automatically stopped
```

## Configuration

### Auto-Fix Levels

The daemon supports four auto-fix levels:

#### 1. **NONE** - No automatic fixes
```python
daemon = EdgeSystemLinterDaemon(
    auto_fix_level=AutoFixLevel.NONE,
    enable_auto_fix=False
)
```
- Only reports issues
- No code modifications
- Best for: Review and learning

#### 2. **SAFE** - Only obvious fixes
```python
daemon = EdgeSystemLinterDaemon(
    auto_fix_level=AutoFixLevel.SAFE,
    enable_auto_fix=True
)
```
- Adds missing imports
- Fixes obvious syntax issues
- No logic changes
- Best for: Production with confidence

#### 3. **MODERATE** - Common patterns
```python
daemon = EdgeSystemLinterDaemon(
    auto_fix_level=AutoFixLevel.MODERATE,
    enable_auto_fix=True
)
```
- Adds hook initialization
- Adds common boilerplate
- Minimal logic changes
- Best for: Development

#### 4. **AGGRESSIVE** - Most issues
```python
daemon = EdgeSystemLinterDaemon(
    auto_fix_level=AutoFixLevel.AGGRESSIVE,
    enable_auto_fix=True
)
```
- Adds result recording templates
- Suggests complex fixes
- May require review
- Best for: Automated cleanup

### Other Parameters

```python
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",              # Directory to monitor
    history_dir=".latti/lint_history/",  # Where to store history
    auto_fix_level=AutoFixLevel.SAFE,    # Fix level
    check_interval=2.0,            # Seconds between checks
    max_history_snapshots=100,     # Keep last N snapshots per file
    enable_auto_fix=True,          # Enable/disable fixes
    enable_recovery_integration=True  # Report to recovery system
)
```

## Usage Patterns

### Pattern 1: Development with Auto-Fix

```python
# In your development setup
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.MODERATE,
    check_interval=1.0  # Check every second
)
daemon.start()

# Your code runs, daemon fixes issues in background
# Check results periodically
print(daemon.report())
```

### Pattern 2: CI/CD Pipeline

```python
# In your CI pipeline
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE,
    check_interval=0.5
)
daemon.run_once()

# Check results
stats = daemon.get_stats()
if stats['total_issues_found'] > 0:
    print(daemon.report())
    sys.exit(1)
```

### Pattern 3: Monitoring with Trends

```python
# Long-running service
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE,
    max_history_snapshots=1000  # Keep more history
)
daemon.start()

# Periodically check trends
while True:
    time.sleep(60)
    for filepath in daemon.snapshots.keys():
        trend = daemon.get_trend_analysis(filepath)
        if trend and trend.error_trend == "degrading":
            alert(f"Code quality degrading in {filepath}")
```

### Pattern 4: Batch Processing

```python
# Process multiple files
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.MODERATE
)

# Process once
daemon.run_once()

# Get detailed report
print(daemon.report())

# Export history
for filepath, snapshots in daemon.snapshots.items():
    print(f"\n{filepath}:")
    for snapshot in snapshots:
        print(f"  {snapshot.timestamp}: {snapshot.total_issues} issues")
```

## API Reference

### Main Methods

#### `start()`
Start the daemon in a background thread.

```python
daemon.start()
# Daemon now runs continuously
```

#### `stop()`
Stop the background daemon.

```python
daemon.stop()
# Daemon stops, thread joins
```

#### `run_once()`
Run a single pass of linting.

```python
daemon.run_once()
# Lints all changed files and returns
```

#### `lint_file_autonomous(filepath)`
Lint a specific file and record snapshot.

```python
issues, snapshot = daemon.lint_file_autonomous(Path("src/main.py"))
print(f"Found {len(issues)} issues")
print(f"Applied {snapshot.auto_fixes_applied} fixes")
```

#### `get_trend_analysis(filepath)`
Get trend analysis for a file.

```python
trend = daemon.get_trend_analysis("src/main.py")
if trend:
    print(f"Error trend: {trend.error_trend}")
    print(f"Most common issues: {trend.most_common_rules}")
```

#### `get_stats()`
Get current statistics.

```python
stats = daemon.get_stats()
print(f"Total lints: {stats['total_lints']}")
print(f"Total issues: {stats['total_issues_found']}")
print(f"Auto-fixes applied: {stats['total_auto_fixes']}")
```

#### `report()`
Generate a comprehensive report.

```python
print(daemon.report())
```

Output:
```
============================================================
EDGE SYSTEM LINTER DAEMON REPORT
============================================================
Status: RUNNING
Uptime: 123.5s
Total lints: 45
Total issues found: 127
Total auto-fixes applied: 23
Files tracked: 8
Auto-fix level: safe
...
```

## Data Structures

### LintSnapshot

Represents a single lint result at a point in time.

```python
@dataclass
class LintSnapshot:
    timestamp: str              # ISO format timestamp
    filepath: str               # File path
    file_hash: str              # SHA256 of file content
    total_issues: int           # Total issues found
    errors: int                 # Number of errors
    warnings: int               # Number of warnings
    infos: int                  # Number of info messages
    suggestions: int            # Number of suggestions
    issues: List[Dict]          # Detailed issue list
    auto_fixes_applied: int     # Number of fixes applied
```

### LintTrend

Represents trend analysis over multiple snapshots.

```python
@dataclass
class LintTrend:
    filepath: str                           # File path
    snapshots_count: int                    # Number of snapshots
    error_trend: str                        # "improving", "stable", "degrading"
    warning_trend: str                      # Same as above
    most_common_rules: List[Tuple[str, int]]  # Top rules and counts
    first_seen: str                         # First snapshot timestamp
    last_seen: str                          # Last snapshot timestamp
    total_issues_fixed: int                 # Total fixes applied
```

## History Storage

The daemon stores snapshots as JSON files in the history directory:

```
.latti/lint_history/
├── src_main_py_2026-05-03T14-20-08.json
├── src_utils_py_2026-05-03T14-20-10.json
└── src_config_py_2026-05-03T14-20-12.json
```

Each file contains:
```json
{
  "timestamp": "2026-05-03T14:20:08.123456",
  "filepath": "src/main.py",
  "file_hash": "abc123...",
  "total_issues": 3,
  "errors": 1,
  "warnings": 2,
  "infos": 0,
  "suggestions": 0,
  "auto_fixes_applied": 1,
  "issues": [
    {
      "severity": "error",
      "rule": "MISSING_HOOK_IMPORT",
      "message": "Missing hook import",
      "line": 5
    }
  ]
}
```

## Command-Line Interface

The daemon can be run from the command line:

```bash
# Start daemon (runs forever)
python -m edge_system_linter_daemon

# Run once and exit
python -m edge_system_linter_daemon --once

# Show report
python -m edge_system_linter_daemon --report

# Custom settings
python -m edge_system_linter_daemon \
    --watch src/ \
    --history .latti/lint_history/ \
    --auto-fix safe \
    --interval 2.0 \
    --once
```

## Integration with Recovery System

The daemon can report violations to the recovery system:

```python
daemon = EdgeSystemLinterDaemon(
    enable_recovery_integration=True
)

# When violations are found, they're reported to:
# - Recovery system for tracking
# - Metrics system for monitoring
# - Alert system for critical issues
```

## Best Practices

### 1. Use Appropriate Fix Levels

- **Development**: Use MODERATE or AGGRESSIVE
- **CI/CD**: Use SAFE
- **Production**: Use NONE or SAFE

### 2. Monitor Trends

```python
# Check for degrading code quality
for filepath in daemon.snapshots.keys():
    trend = daemon.get_trend_analysis(filepath)
    if trend and trend.error_trend == "degrading":
        # Alert or take action
        pass
```

### 3. Regular Reporting

```python
# Generate reports periodically
import schedule

def report_stats():
    print(daemon.report())

schedule.every(1).hour.do(report_stats)
```

### 4. Handle Exceptions

```python
try:
    daemon.start()
    # ... your code ...
except Exception as e:
    print(f"Daemon error: {e}")
finally:
    daemon.stop()
```

### 5. Respect File Permissions

The daemon respects file permissions and won't modify files it can't write to.

## Troubleshooting

### Daemon Not Detecting Changes

- Check that `watch_dir` exists and is correct
- Verify file permissions
- Check `check_interval` is not too long

### Auto-Fixes Not Applied

- Verify `enable_auto_fix=True`
- Check `auto_fix_level` is not NONE
- Review file permissions

### History Growing Too Large

- Reduce `max_history_snapshots`
- Manually clean up `.latti/lint_history/`
- Use `--report` to export before cleanup

### Performance Issues

- Increase `check_interval`
- Reduce `max_history_snapshots`
- Exclude large directories from `watch_dir`

## Examples

### Example 1: Development Setup

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel

# Start daemon for development
daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.MODERATE,
    check_interval=1.0
)
daemon.start()

# Your development code runs here
# Daemon automatically fixes issues in background

# Periodically check status
import time
for _ in range(10):
    time.sleep(5)
    stats = daemon.get_stats()
    print(f"Lints: {stats['total_lints']}, Issues: {stats['total_issues_found']}")

daemon.stop()
```

### Example 2: CI/CD Integration

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
import sys

daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE
)

# Run once
daemon.run_once()

# Check results
stats = daemon.get_stats()
print(daemon.report())

# Fail if too many issues
if stats['total_issues_found'] > 10:
    sys.exit(1)
```

### Example 3: Trend Monitoring

```python
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    max_history_snapshots=1000
)
daemon.start()

# Monitor for 1 hour
for _ in range(60):
    time.sleep(60)
    
    # Check trends
    for filepath in daemon.snapshots.keys():
        trend = daemon.get_trend_analysis(filepath)
        if trend:
            print(f"{filepath}: {trend.error_trend}")

daemon.stop()
```

## See Also

- [Edge System Linter Guide](LINTER_GUIDE.md)
- [Edge System Integration Guide](INTEGRATION_GUIDE.md)
- [Recovery System Documentation](RECOVERY_GUIDE.md)
