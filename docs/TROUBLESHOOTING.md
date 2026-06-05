# EdgeSystemLinterDaemon Troubleshooting Guide

Comprehensive troubleshooting guide for common issues and solutions.

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Runtime Issues](#runtime-issues)
3. [Performance Issues](#performance-issues)
4. [Integration Issues](#integration-issues)
5. [Data Issues](#data-issues)
6. [Debugging](#debugging)

---

## Installation Issues

### Issue: Import Error - Module Not Found

**Symptom:**
```
ModuleNotFoundError: No module named 'edge_system_linter_daemon'
```

**Solutions:**

1. **Verify installation:**
   ```bash
   pip list | grep edge-system-linter
   ```

2. **Reinstall package:**
   ```bash
   pip uninstall edge-system-linter-daemon
   pip install -e .
   ```

3. **Check Python path:**
   ```python
   import sys
   print(sys.path)
   ```

4. **Use virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -e .
   ```

### Issue: Dependency Conflicts

**Symptom:**
```
ERROR: pip's dependency resolver does not currently take into account all the packages
```

**Solutions:**

1. **Update pip:**
   ```bash
   pip install --upgrade pip
   ```

2. **Install specific versions:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Check compatibility:**
   ```bash
   pip check
   ```

4. **Use compatible versions:**
   ```bash
   pip install edge-system-linter-daemon==1.0.0
   ```

### Issue: Permission Denied

**Symptom:**
```
PermissionError: [Errno 13] Permission denied
```

**Solutions:**

1. **Use user installation:**
   ```bash
   pip install --user edge-system-linter-daemon
   ```

2. **Fix directory permissions:**
   ```bash
   chmod -R 755 ~/.local/lib/python3.x/site-packages/
   ```

3. **Use sudo (not recommended):**
   ```bash
   sudo pip install edge-system-linter-daemon
   ```

---

## Runtime Issues

### Issue: Daemon Won't Start

**Symptom:**
```
RuntimeError: Failed to start daemon
```

**Solutions:**

1. **Check watch directory exists:**
   ```python
   from pathlib import Path
   watch_dir = Path("src/")
   assert watch_dir.exists(), f"{watch_dir} does not exist"
   ```

2. **Verify permissions:**
   ```bash
   ls -la src/
   ```

3. **Check for port conflicts:**
   ```bash
   lsof -i :8000  # If using HTTP server
   ```

4. **Enable debug logging:**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.start()
   ```

### Issue: Daemon Crashes Unexpectedly

**Symptom:**
```
Process terminated with exit code 1
```

**Solutions:**

1. **Check logs:**
   ```bash
   cat .latti/daemon.log
   ```

2. **Run with error handling:**
   ```python
   try:
       daemon = EdgeSystemLinterDaemon(watch_dir="src/")
       daemon.start()
   except Exception as e:
       print(f"Error: {e}")
       import traceback
       traceback.print_exc()
   ```

3. **Reduce resource usage:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       check_interval=5.0,  # Increase interval
       max_history_snapshots=10  # Reduce history
   )
   ```

4. **Check system resources:**
   ```bash
   free -h  # Memory
   df -h    # Disk space
   ```

### Issue: No Issues Found (But Should Be)

**Symptom:**
```
Issues found: 0
```

**Solutions:**

1. **Verify watch directory:**
   ```python
   from pathlib import Path
   
   watch_dir = Path("src/")
   py_files = list(watch_dir.glob("**/*.py"))
   print(f"Found {len(py_files)} Python files")
   ```

2. **Check file permissions:**
   ```bash
   ls -la src/*.py
   ```

3. **Verify linting rules are enabled:**
   ```python
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   print(daemon.enabled_rules)
   ```

4. **Test with known issue:**
   ```python
   # Create test file with obvious issue
   Path("src/test_issue.py").write_text("x=1")  # Missing spaces
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.run_once()
   ```

### Issue: Too Many False Positives

**Symptom:**
```
Issues found: 1000+
```

**Solutions:**

1. **Adjust auto-fix level:**
   ```python
   from edge_system_linter_daemon import AutoFixLevel
   
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       auto_fix_level=AutoFixLevel.SAFE  # More conservative
   )
   ```

2. **Configure rule severity:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       min_severity="error"  # Only errors, not warnings
   )
   ```

3. **Exclude directories:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       exclude_patterns=["**/test_*.py", "**/migrations/"]
   )
   ```

4. **Create .lintignore:**
   ```
   # .lintignore
   build/
   dist/
   *.egg-info/
   __pycache__/
   .venv/
   ```

---

## Performance Issues

### Issue: Daemon Uses Too Much CPU

**Symptom:**
```
CPU usage: 80-100%
```

**Solutions:**

1. **Increase check interval:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       check_interval=10.0  # Check every 10 seconds instead of 1
   )
   ```

2. **Reduce history size:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       max_history_snapshots=5  # Keep only 5 snapshots
   )
   ```

3. **Exclude large directories:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       exclude_patterns=["**/node_modules/", "**/venv/"]
   )
   ```

4. **Use NONE auto-fix level:**
   ```python
   from edge_system_linter_daemon import AutoFixLevel
   
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       auto_fix_level=AutoFixLevel.NONE  # Skip auto-fixing
   )
   ```

### Issue: Daemon Uses Too Much Memory

**Symptom:**
```
Memory usage: 500MB+
```

**Solutions:**

1. **Reduce history snapshots:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       max_history_snapshots=5  # Default is 50
   )
   ```

2. **Clear history periodically:**
   ```python
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.run_once()
   daemon.clear_history()  # Free memory
   ```

3. **Monitor memory usage:**
   ```python
   import psutil
   
   process = psutil.Process()
   print(f"Memory: {process.memory_info().rss / 1024 / 1024:.1f} MB")
   ```

4. **Use streaming mode:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       streaming_mode=True  # Process files one at a time
   )
   ```

### Issue: Linting Takes Too Long

**Symptom:**
```
Processing time: 30+ seconds
```

**Solutions:**

1. **Profile the daemon:**
   ```python
   import cProfile
   import pstats
   
   profiler = cProfile.Profile()
   profiler.enable()
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.run_once()
   
   profiler.disable()
   stats = pstats.Stats(profiler)
   stats.sort_stats('cumulative')
   stats.print_stats(10)
   ```

2. **Disable expensive rules:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       disabled_rules=["COMPLEX_ANALYSIS", "DEEP_INSPECTION"]
   )
   ```

3. **Use parallel processing:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       parallel_workers=4  # Use 4 processes
   )
   ```

4. **Lint only changed files:**
   ```python
   import subprocess
   
   # Get changed files from git
   result = subprocess.run(
       ['git', 'diff', '--name-only'],
       capture_output=True,
       text=True
   )
   changed_files = result.stdout.strip().split('\n')
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   for filepath in changed_files:
       daemon.lint_file_autonomous(filepath)
   ```

---

## Integration Issues

### Issue: CI/CD Pipeline Fails

**Symptom:**
```
GitHub Actions: Job failed with exit code 1
```

**Solutions:**

1. **Check workflow syntax:**
   ```bash
   # Validate GitHub Actions workflow
   yamllint .github/workflows/lint.yml
   ```

2. **View detailed logs:**
   - Go to GitHub Actions tab
   - Click on failed workflow
   - Expand "Run linter daemon" step

3. **Test locally:**
   ```bash
   # Simulate CI environment
   python -c "
   from edge_system_linter_daemon import EdgeSystemLinterDaemon
   daemon = EdgeSystemLinterDaemon('src/')
   daemon.run_once()
   stats = daemon.get_stats()
   if stats['total_issues_found'] > 0:
       print(daemon.report())
       exit(1)
   "
   ```

4. **Check dependencies:**
   ```yaml
   - name: Install dependencies
     run: |
       pip install -e .
       pip install pytest
   ```

### Issue: Slack Alerts Not Sending

**Symptom:**
```
No messages in Slack channel
```

**Solutions:**

1. **Verify token:**
   ```bash
   echo $SLACK_BOT_TOKEN
   ```

2. **Test Slack connection:**
   ```python
   from slack_sdk import WebClient
   
   client = WebClient(token="xoxb-...")
   response = client.auth_test()
   print(response)
   ```

3. **Check channel permissions:**
   ```python
   client.chat_postMessage(
       channel="#code-quality",
       text="Test message"
   )
   ```

4. **Enable debug logging:**
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   from slack_sdk import WebClient
   client = WebClient(token="xoxb-...")
   ```

### Issue: Prometheus Metrics Not Appearing

**Symptom:**
```
No metrics in Prometheus dashboard
```

**Solutions:**

1. **Verify exporter is running:**
   ```bash
   curl http://localhost:8000/metrics
   ```

2. **Check Prometheus config:**
   ```yaml
   # prometheus.yml
   scrape_configs:
     - job_name: 'linter'
       static_configs:
         - targets: ['localhost:8000']
   ```

3. **Test metric export:**
   ```python
   from prometheus_client import Counter
   
   test_counter = Counter('test_metric', 'Test')
   test_counter.inc()
   
   # Should appear in /metrics
   ```

4. **Check firewall:**
   ```bash
   netstat -tlnp | grep 8000
   ```

---

## Data Issues

### Issue: History Data Corrupted

**Symptom:**
```
ValueError: Invalid snapshot data
```

**Solutions:**

1. **Clear history:**
   ```bash
   rm -rf .latti/lint_history/
   ```

2. **Rebuild history:**
   ```python
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.clear_history()
   daemon.run_once()
   ```

3. **Backup before clearing:**
   ```bash
   cp -r .latti .latti.backup
   rm -rf .latti/lint_history/
   ```

### Issue: Report File Not Generated

**Symptom:**
```
FileNotFoundError: .latti/latest_report.txt
```

**Solutions:**

1. **Create .latti directory:**
   ```bash
   mkdir -p .latti
   ```

2. **Check permissions:**
   ```bash
   ls -la .latti/
   chmod 755 .latti/
   ```

3. **Generate report manually:**
   ```python
   from pathlib import Path
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.run_once()
   
   report = daemon.report()
   Path(".latti").mkdir(exist_ok=True)
   Path(".latti/latest_report.txt").write_text(report)
   ```

### Issue: Snapshots Not Being Saved

**Symptom:**
```
Snapshots: 0
```

**Solutions:**

1. **Verify snapshot directory:**
   ```bash
   ls -la .latti/snapshots/
   ```

2. **Check disk space:**
   ```bash
   df -h
   ```

3. **Enable snapshot saving:**
   ```python
   daemon = EdgeSystemLinterDaemon(
       watch_dir="src/",
       save_snapshots=True
   )
   ```

---

## Debugging

### Enable Debug Logging

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('.latti/debug.log'),
        logging.StreamHandler()
    ]
)

# Create daemon
daemon = EdgeSystemLinterDaemon(watch_dir="src/")
daemon.run_once()
```

### Inspect Internal State

```python
daemon = EdgeSystemLinterDaemon(watch_dir="src/")
daemon.run_once()

# Check snapshots
print(f"Snapshots: {len(daemon.snapshots)}")
for filepath, snapshots in daemon.snapshots.items():
    print(f"  {filepath}: {len(snapshots)} snapshots")

# Check statistics
stats = daemon.get_stats()
for key, value in stats.items():
    print(f"  {key}: {value}")

# Check trends
for filepath in daemon.snapshots.keys():
    trend = daemon.get_trend_analysis(filepath)
    if trend:
        print(f"  {filepath}: {trend.error_trend}")
```

### Test Individual Components

```python
# Test linting
from edge_system_linter_daemon import EdgeSystemLinterDaemon

daemon = EdgeSystemLinterDaemon(watch_dir="src/")
issues, snapshot = daemon.lint_file_autonomous("src/test.py")
print(f"Issues: {len(issues)}")
print(f"Snapshot: {snapshot}")

# Test auto-fixing
from edge_system_linter_daemon import AutoFixLevel

daemon = EdgeSystemLinterDaemon(
    watch_dir="src/",
    auto_fix_level=AutoFixLevel.SAFE
)
daemon.run_once()
print(f"Auto-fixes: {daemon.get_stats()['total_auto_fixes']}")

# Test trend analysis
trend = daemon.get_trend_analysis("src/test.py")
print(f"Trend: {trend}")
```

### Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `FileNotFoundError: [Errno 2] No such file or directory: 'src/'` | Watch directory doesn't exist | Create directory or fix path |
| `PermissionError: [Errno 13] Permission denied` | No read permissions | `chmod 755 src/` |
| `RuntimeError: Daemon already running` | Daemon instance already active | Stop previous instance first |
| `ValueError: Invalid auto-fix level` | Invalid AutoFixLevel value | Use valid enum value |
| `KeyError: 'total_issues_found'` | Stats not available | Run `daemon.run_once()` first |
| `IndexError: list index out of range` | No snapshots available | Run linting first |

---

## Getting Help

If you can't find a solution:

1. **Check the logs:**
   ```bash
   cat .latti/daemon.log
   cat .latti/debug.log
   ```

2. **Review the documentation:**
   - README.md - Overview
   - API_REFERENCE.md - API details
   - INTEGRATION_GUIDE.md - Integration examples

3. **Run diagnostics:**
   ```python
   from edge_system_linter_daemon import EdgeSystemLinterDaemon
   
   daemon = EdgeSystemLinterDaemon(watch_dir="src/")
   daemon.run_diagnostics()
   ```

4. **Report an issue:**
   - Include error message
   - Include logs
   - Include minimal reproduction case
   - Include Python version and OS

---

## Performance Tuning Checklist

- [ ] Increase `check_interval` for slower systems
- [ ] Reduce `max_history_snapshots` to save memory
- [ ] Exclude unnecessary directories with `exclude_patterns`
- [ ] Use `AutoFixLevel.NONE` if auto-fixing is slow
- [ ] Enable parallel processing with `parallel_workers`
- [ ] Monitor resource usage with system tools
- [ ] Profile with cProfile to find bottlenecks
- [ ] Use streaming mode for large codebases

---

## Quick Reference

```bash
# View logs
tail -f .latti/daemon.log

# Clear history
rm -rf .latti/lint_history/

# Check disk usage
du -sh .latti/

# Monitor process
ps aux | grep linter

# Kill daemon
pkill -f edge_system_linter

# Test installation
python -c "from edge_system_linter_daemon import EdgeSystemLinterDaemon; print('OK')"
```
