# EdgeSystemLinterDaemon Integration Guide

Complete guide for integrating the daemon into various environments and workflows.

## Table of Contents

1. [CI/CD Integration](#cicd-integration)
2. [Monitoring Integration](#monitoring-integration)
3. [Alert Integration](#alert-integration)
4. [Development Workflow](#development-workflow)
5. [Production Deployment](#production-deployment)
6. [Advanced Patterns](#advanced-patterns)

---

## CI/CD Integration

### GitHub Actions

#### Basic Workflow

Create `.github/workflows/lint.yml`:

```yaml
name: Code Quality Linting

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install -e .
          pip install pytest pytest-cov
      
      - name: Run linter daemon
        run: |
          python -c "
          from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
          
          daemon = EdgeSystemLinterDaemon(
              watch_dir='src/',
              auto_fix_level=AutoFixLevel.SAFE
          )
          daemon.run_once()
          
          stats = daemon.get_stats()
          print(f'Issues found: {stats[\"total_issues_found\"]}')
          print(f'Auto-fixes: {stats[\"total_auto_fixes\"]}')
          
          if stats['total_issues_found'] > 0:
              print(daemon.report())
              exit(1)
          "
      
      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: lint-report
          path: .latti/latest_report.txt
```

#### Advanced Workflow with Trend Analysis

```yaml
name: Code Quality with Trends

on:
  push:
    branches: [main]
  schedule:
    - cron: '0 9 * * *'  # Daily at 9 AM

jobs:
  quality:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
        with:
          fetch-depth: 0  # Full history for trend analysis
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: pip install -e .
      
      - name: Restore history
        uses: actions/cache@v3
        with:
          path: .latti/lint_history
          key: lint-history-${{ github.ref }}
          restore-keys: lint-history-
      
      - name: Run linter with trend analysis
        run: |
          python scripts/ci_lint_with_trends.py
      
      - name: Comment on PR
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v6
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('.latti/pr_comment.md', 'utf8');
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: report
            });
      
      - name: Save history
        uses: actions/cache@v3
        with:
          path: .latti/lint_history
          key: lint-history-${{ github.ref }}-${{ github.run_id }}
```

#### Script: `scripts/ci_lint_with_trends.py`

```python
#!/usr/bin/env python3
"""CI script with trend analysis."""

import sys
from pathlib import Path
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel

def main():
    daemon = EdgeSystemLinterDaemon(
        watch_dir="src/",
        auto_fix_level=AutoFixLevel.SAFE,
        max_history_snapshots=50
    )
    
    # Run linting
    daemon.run_once()
    
    # Generate report
    report = daemon.report()
    print(report)
    
    # Save full report
    Path(".latti").mkdir(exist_ok=True)
    Path(".latti/latest_report.txt").write_text(report)
    
    # Generate PR comment
    pr_comment = generate_pr_comment(daemon)
    Path(".latti/pr_comment.md").write_text(pr_comment)
    
    # Check for degradation
    stats = daemon.get_stats()
    
    if stats['total_issues_found'] > 0:
        print(f"\n❌ Found {stats['total_issues_found']} issues")
        return 1
    
    print("\n✅ All checks passed")
    return 0

def generate_pr_comment(daemon):
    """Generate markdown comment for PR."""
    stats = daemon.get_stats()
    
    comment = f"""## Code Quality Report

**Summary:**
- Issues found: {stats['total_issues_found']}
- Auto-fixes applied: {stats['total_auto_fixes']}
- Files tracked: {stats['files_tracked']}

"""
    
    # Add trend analysis
    for filepath in list(daemon.snapshots.keys())[:5]:
        trend = daemon.get_trend_analysis(filepath)
        if trend:
            comment += f"### {filepath}\n"
            comment += f"- Error trend: {trend.error_trend}\n"
            comment += f"- Warning trend: {trend.warning_trend}\n"
            
            if trend.most_common_rules:
                comment += "- Top issues:\n"
                for rule, count in trend.most_common_rules[:3]:
                    comment += f"  - {rule}: {count}\n"
            
            comment += "\n"
    
    return comment

if __name__ == "__main__":
    sys.exit(main())
```

### GitLab CI

Create `.gitlab-ci.yml`:

```yaml
stages:
  - lint
  - report

code_quality:
  stage: lint
  image: python:3.10
  
  script:
    - pip install -e .
    - python -c "
        from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
        
        daemon = EdgeSystemLinterDaemon(
            watch_dir='src/',
            auto_fix_level=AutoFixLevel.SAFE
        )
        daemon.run_once()
        
        stats = daemon.get_stats()
        if stats['total_issues_found'] > 0:
            print(daemon.report())
            exit(1)
      "
  
  artifacts:
    reports:
      codequality: lint-report.json
    paths:
      - .latti/
    expire_in: 30 days
  
  cache:
    paths:
      - .latti/lint_history/

quality_report:
  stage: report
  image: python:3.10
  
  script:
    - pip install -e .
    - python scripts/generate_quality_report.py
  
  artifacts:
    paths:
      - quality-report.html
    expire_in: 90 days
  
  only:
    - main
```

### Jenkins

Create `Jenkinsfile`:

```groovy
pipeline {
    agent any
    
    stages {
        stage('Setup') {
            steps {
                sh '''
                    python -m venv venv
                    . venv/bin/activate
                    pip install -e .
                '''
            }
        }
        
        stage('Lint') {
            steps {
                sh '''
                    . venv/bin/activate
                    python scripts/jenkins_lint.py
                '''
            }
        }
        
        stage('Report') {
            steps {
                publishHTML([
                    reportDir: '.latti',
                    reportFiles: 'report.html',
                    reportName: 'Code Quality Report'
                ])
            }
        }
    }
    
    post {
        always {
            archiveArtifacts artifacts: '.latti/**', allowEmptyArchive: true
            cleanWs()
        }
    }
}
```

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Pre-commit hook for code quality

set -e

echo "Running code quality checks..."

python -c "
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel
from pathlib import Path

# Get staged files
import subprocess
result = subprocess.run(['git', 'diff', '--cached', '--name-only'], 
                       capture_output=True, text=True)
staged_files = result.stdout.strip().split('\n')

# Filter Python files
py_files = [f for f in staged_files if f.endswith('.py')]

if not py_files:
    exit(0)

daemon = EdgeSystemLinterDaemon(
    watch_dir='.',
    auto_fix_level=AutoFixLevel.SAFE
)

# Lint staged files
issues_found = False
for filepath in py_files:
    if Path(filepath).exists():
        issues, _ = daemon.lint_file_autonomous(filepath)
        if issues:
            issues_found = True
            print(f'Issues in {filepath}:')
            for issue in issues:
                print(f'  {issue[\"rule\"]}: {issue[\"message\"]}')

if issues_found:
    print('\n❌ Pre-commit checks failed')
    exit(1)

print('✅ Pre-commit checks passed')
"
```

---

## Monitoring Integration

### Continuous Monitoring Service

Create `services/linter_monitor.py`:

```python
#!/usr/bin/env python3
"""Continuous code quality monitoring service."""

import time
import logging
from pathlib import Path
from edge_system_linter_daemon import EdgeSystemLinterDaemon, AutoFixLevel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LinterMonitorService:
    """Continuous monitoring service."""
    
    def __init__(self, watch_dir="src/", check_interval=5.0):
        self.daemon = EdgeSystemLinterDaemon(
            watch_dir=watch_dir,
            auto_fix_level=AutoFixLevel.SAFE,
            check_interval=check_interval,
            enable_recovery_integration=True
        )
        self.metrics = {
            'total_issues': 0,
            'total_fixes': 0,
            'degraded_files': []
        }
    
    def start(self):
        """Start monitoring."""
        logger.info("Starting linter monitor service")
        self.daemon.start()
        
        try:
            while self.daemon.is_running:
                self.check_quality()
                time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()
    
    def check_quality(self):
        """Check code quality and alert on issues."""
        stats = self.daemon.get_stats()
        
        self.metrics['total_issues'] = stats['total_issues_found']
        self.metrics['total_fixes'] = stats['total_auto_fixes']
        
        # Check for degradation
        self.metrics['degraded_files'] = []
        
        for filepath in self.daemon.snapshots.keys():
            trend = self.daemon.get_trend_analysis(filepath)
            
            if trend and trend.error_trend == "degrading":
                self.metrics['degraded_files'].append(filepath)
                self.alert_degradation(filepath, trend)
        
        logger.info(
            f"Quality check: {stats['total_issues_found']} issues, "
            f"{stats['total_auto_fixes']} fixes"
        )
    
    def alert_degradation(self, filepath, trend):
        """Alert on quality degradation."""
        logger.warning(
            f"Quality degrading in {filepath}: "
            f"Top issues: {trend.most_common_rules[:3]}"
        )
        
        # Send to monitoring system
        self.send_metric('code_quality.degradation', 1, {
            'file': filepath,
            'top_issues': str(trend.most_common_rules[:3])
        })
    
    def send_metric(self, metric_name, value, tags=None):
        """Send metric to monitoring system."""
        # Implementation depends on monitoring backend
        logger.debug(f"Metric: {metric_name}={value}, tags={tags}")
    
    def stop(self):
        """Stop monitoring."""
        logger.info("Stopping linter monitor service")
        self.daemon.stop()

if __name__ == "__main__":
    service = LinterMonitorService(watch_dir="src/")
    service.start()
```

### Prometheus Integration

Create `services/prometheus_exporter.py`:

```python
#!/usr/bin/env python3
"""Prometheus metrics exporter for linter daemon."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

# Define metrics
issues_found = Gauge('code_quality_issues_total', 'Total issues found')
auto_fixes_applied = Counter('code_quality_auto_fixes_total', 'Total auto-fixes applied')
lint_duration = Histogram('code_quality_lint_duration_seconds', 'Linting duration')
error_trend = Gauge('code_quality_error_trend', 'Error trend', ['file'])
warning_trend = Gauge('code_quality_warning_trend', 'Warning trend', ['file'])

def export_metrics():
    """Export metrics from daemon."""
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    while True:
        with lint_duration.time():
            daemon.run_once()
        
        stats = daemon.get_stats()
        issues_found.set(stats['total_issues_found'])
        auto_fixes_applied._value.get().inc(stats['total_auto_fixes'])
        
        # Export trend metrics
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            if trend:
                error_val = {'improving': -1, 'stable': 0, 'degrading': 1}
                warning_val = {'improving': -1, 'stable': 0, 'degrading': 1}
                
                error_trend.labels(file=filepath).set(
                    error_val.get(trend.error_trend, 0)
                )
                warning_trend.labels(file=filepath).set(
                    warning_val.get(trend.warning_trend, 0)
                )
        
        time.sleep(60)

if __name__ == "__main__":
    start_http_server(8000)
    export_metrics()
```

### Datadog Integration

Create `services/datadog_integration.py`:

```python
#!/usr/bin/env python3
"""Datadog integration for linter daemon."""

from datadog import initialize, api
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

options = {
    'api_key': 'YOUR_API_KEY',
    'app_key': 'YOUR_APP_KEY'
}

initialize(**options)

def send_to_datadog():
    """Send metrics to Datadog."""
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    while True:
        daemon.run_once()
        stats = daemon.get_stats()
        
        # Send metrics
        api.Metric.send(
            metric='code_quality.issues',
            points=stats['total_issues_found'],
            tags=['service:linter']
        )
        
        api.Metric.send(
            metric='code_quality.auto_fixes',
            points=stats['total_auto_fixes'],
            tags=['service:linter']
        )
        
        # Send trend data
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            if trend:
                api.Metric.send(
                    metric='code_quality.trend',
                    points=1,
                    tags=[
                        f'file:{filepath}',
                        f'error_trend:{trend.error_trend}',
                        f'warning_trend:{trend.warning_trend}'
                    ]
                )
        
        time.sleep(60)

if __name__ == "__main__":
    send_to_datadog()
```

---

## Alert Integration

### Slack Alerts

Create `services/slack_alerter.py`:

```python
#!/usr/bin/env python3
"""Slack integration for linter alerts."""

import os
from slack_sdk import WebClient
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

slack_client = WebClient(token=os.environ['SLACK_BOT_TOKEN'])
CHANNEL = '#code-quality'

def send_slack_alert(message, severity='info'):
    """Send alert to Slack."""
    color = {
        'info': '#36a64f',
        'warning': '#ff9900',
        'error': '#ff0000'
    }.get(severity, '#36a64f')
    
    slack_client.chat_postMessage(
        channel=CHANNEL,
        attachments=[{
            'color': color,
            'text': message,
            'mrkdwn_in': ['text']
        }]
    )

def monitor_with_alerts():
    """Monitor code quality with Slack alerts."""
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    while True:
        daemon.run_once()
        stats = daemon.get_stats()
        
        # Alert on issues
        if stats['total_issues_found'] > 0:
            message = (
                f"🚨 Code Quality Alert\n"
                f"Issues found: {stats['total_issues_found']}\n"
                f"Auto-fixes: {stats['total_auto_fixes']}"
            )
            send_slack_alert(message, 'warning')
        
        # Alert on degradation
        for filepath in daemon.snapshots.keys():
            trend = daemon.get_trend_analysis(filepath)
            
            if trend and trend.error_trend == "degrading":
                message = (
                    f"⚠️ Quality Degrading: {filepath}\n"
                    f"Top issues: {', '.join(r[0] for r in trend.most_common_rules[:3])}"
                )
                send_slack_alert(message, 'error')
        
        time.sleep(300)  # Check every 5 minutes

if __name__ == "__main__":
    monitor_with_alerts()
```

### Email Alerts

Create `services/email_alerter.py`:

```python
#!/usr/bin/env python3
"""Email integration for linter alerts."""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from edge_system_linter_daemon import EdgeSystemLinterDaemon
import time

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "alerts@example.com"
RECIPIENT_EMAIL = "team@example.com"

def send_email_alert(subject, body):
    """Send email alert."""
    message = MIMEMultipart()
    message["From"] = SENDER_EMAIL
    message["To"] = RECIPIENT_EMAIL
    message["Subject"] = subject
    
    message.attach(MIMEText(body, "html"))
    
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER_EMAIL, os.environ['EMAIL_PASSWORD'])
        server.send_message(message)

def monitor_with_email_alerts():
    """Monitor with email alerts."""
    daemon = EdgeSystemLinterDaemon(watch_dir="src/")
    
    while True:
        daemon.run_once()
        stats = daemon.get_stats()
        
        if stats['total_issues_found'] > 0:
            body = f"""
            <h2>Code Quality Report</h2>
            <p>Issues found: {stats['total_issues_found']}</p>
            <p>Auto-fixes: {stats['total_auto_fixes']}</p>
            <pre>{daemon.report()}</pre>
            """
            
            send_email_alert("Code Quality Alert", body)
        
        time.sleep(3600)  # Check hourly

if __name__ == "__main__":
    monitor_with_email_alerts()
```

---

## Development Workflow

### Local Development Setup

Create `scripts/dev_setup.sh`:

```bash
#!/bin/bash
# Development setup script

set -e

echo "Setting up development environment..."

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .
pip install pytest pytest-cov black flake8

# Install pre-commit hook
cp scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Initialize linter history
mkdir -p .latti/lint_history

echo "✅ Development environment ready"
echo "Run 'source venv/bin/activate' to activate"
```

### IDE Integration

#### VS Code

Create `.vscode/settings.json`:

```json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": false,
  "python.linting.flake8Enabled": true,
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.python"
  },
  "python.formatting.provider": "black",
  "files.exclude": {
    ".latti": true,
    "**/__pycache__": true
  }
}
```

Create `.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run Linter",
      "type": "shell",
      "command": "python",
      "args": [
        "-c",
        "from edge_system_linter_daemon import EdgeSystemLinterDaemon; d = EdgeSystemLinterDaemon('src/'); d.run_once(); print(d.report())"
      ],
      "group": {
        "kind": "test",
        "isDefault": true
      }
    }
  ]
}
```

---

## Production Deployment

### Docker Deployment

Create `Dockerfile`:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create linter history directory
RUN mkdir -p .latti/lint_history

# Run linter daemon
CMD ["python", "services/linter_monitor.py"]
```

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  linter:
    build: .
    volumes:
      - ./src:/app/src
      - ./linter_history:/app/.latti/lint_history
    environment:
      - SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}
      - LOG_LEVEL=INFO
    restart: unless-stopped
  
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
  
  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
```

### Kubernetes Deployment

Create `k8s/linter-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: code-quality-linter
  namespace: monitoring

spec:
  replicas: 1
  selector:
    matchLabels:
      app: code-quality-linter
  
  template:
    metadata:
      labels:
        app: code-quality-linter
    
    spec:
      containers:
      - name: linter
        image: myregistry/code-quality-linter:latest
        imagePullPolicy: Always
        
        env:
        - name: SLACK_BOT_TOKEN
          valueFrom:
            secretKeyRef:
              name: linter-secrets
              key: slack-token
        
        volumeMounts:
        - name: source-code
          mountPath: /app/src
        - name: history
          mountPath: /app/.latti/lint_history
        
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
      
      volumes:
      - name: source-code
        emptyDir: {}
      - name: history
        persistentVolumeClaim:
          claimName: linter-history-pvc
```

---

## Advanced Patterns

### Custom Linting Rules

Create `custom_rules.py`:

```python
"""Custom linting rules."""

from edge_system_linter_daemon import EdgeSystemLinterDaemon

class CustomRuleLinter(EdgeSystemLinterDaemon):
    """Linter with custom rules."""
    
    def lint_file_autonomous(self, filepath):
        """Lint with custom rules."""
        issues, snapshot = super().lint_file_autonomous(filepath)
        
        # Add custom rules
        custom_issues = self.check_custom_rules(filepath)
        issues.extend(custom_issues)
        
        return issues, snapshot
    
    def check_custom_rules(self, filepath):
        """Check custom linting rules."""
        issues = []
        
        with open(filepath) as f:
            content = f.read()
        
        # Custom rule 1: No TODO comments
        if 'TODO' in content:
            issues.append({
                'rule': 'CUSTOM_NO_TODO',
                'severity': 'warning',
                'message': 'TODO comments should be tracked in issues',
                'auto_fixed': False
            })
        
        # Custom rule 2: Max file size
        if len(content) > 1000:
            issues.append({
                'rule': 'CUSTOM_FILE_SIZE',
                'severity': 'warning',
                'message': 'File is too large, consider splitting',
                'auto_fixed': False
            })
        
        return issues
```

### Multi-Project Monitoring

Create `services/multi_project_monitor.py`:

```python
"""Monitor multiple projects."""

from edge_system_linter_daemon import EdgeSystemLinterDaemon
from pathlib import Path

class MultiProjectMonitor:
    """Monitor multiple projects."""
    
    def __init__(self, projects):
        self.daemons = {
            name: EdgeSystemLinterDaemon(watch_dir=path)
            for name, path in projects.items()
        }
    
    def run_all(self):
        """Run linting on all projects."""
        results = {}
        
        for name, daemon in self.daemons.items():
            daemon.run_once()
            stats = daemon.get_stats()
            results[name] = stats
        
        return results
    
    def generate_report(self):
        """Generate combined report."""
        report = "# Multi-Project Code Quality Report\n\n"
        
        for name, daemon in self.daemons.items():
            stats = daemon.get_stats()
            report += f"## {name}\n"
            report += f"- Issues: {stats['total_issues_found']}\n"
            report += f"- Fixes: {stats['total_auto_fixes']}\n\n"
        
        return report

if __name__ == "__main__":
    projects = {
        'backend': 'backend/src',
        'frontend': 'frontend/src',
        'shared': 'shared/src'
    }
    
    monitor = MultiProjectMonitor(projects)
    results = monitor.run_all()
    
    print(monitor.generate_report())
```

---

## Summary

The EdgeSystemLinterDaemon integrates seamlessly with:

- **CI/CD**: GitHub Actions, GitLab CI, Jenkins
- **Monitoring**: Prometheus, Datadog, custom services
- **Alerts**: Slack, Email, custom webhooks
- **Development**: Pre-commit hooks, IDE integration
- **Deployment**: Docker, Kubernetes, cloud platforms

Choose the integration patterns that best fit your workflow and infrastructure.
