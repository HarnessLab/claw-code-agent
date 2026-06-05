#!/usr/bin/env python3
"""
CI/CD Integration Example for EdgeSystemLinterDaemon

Demonstrates how to integrate the autonomous linter daemon into CI/CD pipelines
(GitHub Actions, GitLab CI, Jenkins, etc.).

This example shows:
- Daemon startup in CI environment
- Automated linting on every commit
- Report generation and artifact upload
- Failure handling and exit codes
"""

import sys
import os
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from edge_system_linter_daemon import EdgeSystemLinterDaemon
from edge_system_linter import EdgeSystemLinter


class CICDIntegration:
    """Handles CI/CD pipeline integration for the linter daemon."""
    
    def __init__(self, repo_path: str, output_dir: str = "linter-reports"):
        """
        Initialize CI/CD integration.
        
        Args:
            repo_path: Path to repository to lint
            output_dir: Directory for reports and artifacts
        """
        self.repo_path = repo_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.daemon = None
        self.linter = EdgeSystemLinter(repo_path)
        
    def setup_daemon(self, config: dict = None):
        """Setup the linter daemon with CI-specific configuration."""
        if config is None:
            config = {
                'check_interval': 5,  # Faster in CI
                'max_iterations': 10,  # Limited iterations
                'enable_auto_fix': False,  # Don't auto-fix in CI
                'verbose': True,
                'report_format': 'json'
            }
        
        self.daemon = EdgeSystemLinterDaemon(
            repo_path=self.repo_path,
            config=config
        )
        print(f"✅ Daemon configured for CI/CD")
        
    def run_linting_pass(self) -> dict:
        """
        Run a single linting pass and collect results.
        
        Returns:
            Dictionary with linting results
        """
        print(f"\n🔍 Running linting pass at {datetime.now().isoformat()}")
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'issues': [],
            'stats': {}
        }
        
        # Run linter
        linting_results = self.linter.lint_repository()
        
        results['issues'] = linting_results.get('issues', [])
        results['stats'] = {
            'total_issues': len(linting_results.get('issues', [])),
            'critical': len([i for i in linting_results.get('issues', []) 
                           if i.get('severity') == 'critical']),
            'warnings': len([i for i in linting_results.get('issues', []) 
                           if i.get('severity') == 'warning']),
            'info': len([i for i in linting_results.get('issues', []) 
                        if i.get('severity') == 'info']),
        }
        
        return results
        
    def generate_report(self, results: dict) -> str:
        """
        Generate a formatted report from linting results.
        
        Args:
            results: Linting results dictionary
            
        Returns:
            Path to generated report
        """
        report_path = self.output_dir / f"linter-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        
        with open(report_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"📄 Report generated: {report_path}")
        return str(report_path)
        
    def generate_markdown_report(self, results: dict) -> str:
        """
        Generate a markdown report for GitHub/GitLab comments.
        
        Args:
            results: Linting results dictionary
            
        Returns:
            Markdown formatted report
        """
        stats = results['stats']
        issues = results['issues']
        
        md = f"""# 🔍 EdgeSystemLinter Report

**Timestamp:** {results['timestamp']}

## Summary
- **Total Issues:** {stats['total_issues']}
- **Critical:** {stats['critical']}
- **Warnings:** {stats['warnings']}
- **Info:** {stats['info']}

"""
        
        if issues:
            md += "## Issues Found\n\n"
            for issue in issues[:20]:  # Limit to first 20
                severity = issue.get('severity', 'unknown').upper()
                path = issue.get('path', 'unknown')
                message = issue.get('message', 'No message')
                md += f"- **[{severity}]** `{path}`: {message}\n"
            
            if len(issues) > 20:
                md += f"\n... and {len(issues) - 20} more issues\n"
        else:
            md += "✅ No issues found!\n"
        
        return md
        
    def post_github_comment(self, report: str, pr_number: int = None):
        """
        Post linting report as GitHub PR comment.
        
        Args:
            report: Markdown formatted report
            pr_number: PR number (auto-detected if not provided)
        """
        if not pr_number:
            pr_number = os.getenv('GITHUB_PR_NUMBER')
        
        if not pr_number:
            print("⚠️  No PR number available, skipping GitHub comment")
            return
        
        # This would use GitHub API in real scenario
        print(f"📝 Would post comment to PR #{pr_number}")
        print(f"Comment preview:\n{report[:200]}...")
        
    def upload_artifacts(self, report_path: str):
        """
        Upload artifacts to CI system.
        
        Args:
            report_path: Path to report file
        """
        # GitHub Actions example
        if os.getenv('GITHUB_ACTIONS'):
            print(f"📤 Uploading artifact: {report_path}")
            # In real scenario: use actions/upload-artifact
        
        # GitLab CI example
        if os.getenv('GITLAB_CI'):
            print(f"📤 Artifact will be available in GitLab")
        
    def determine_exit_code(self, results: dict) -> int:
        """
        Determine exit code based on linting results.
        
        Args:
            results: Linting results dictionary
            
        Returns:
            Exit code (0 = success, 1 = warnings, 2 = critical)
        """
        stats = results['stats']
        
        if stats['critical'] > 0:
            print("❌ Critical issues found")
            return 2
        elif stats['warnings'] > 0:
            print("⚠️  Warnings found")
            return 1
        else:
            print("✅ No issues found")
            return 0
            
    def run_ci_pipeline(self) -> int:
        """
        Run complete CI/CD pipeline.
        
        Returns:
            Exit code for CI system
        """
        print("=" * 60)
        print("🚀 EdgeSystemLinter CI/CD Pipeline")
        print("=" * 60)
        
        try:
            # Setup
            self.setup_daemon()
            
            # Run linting
            results = self.run_linting_pass()
            
            # Generate reports
            json_report = self.generate_report(results)
            md_report = self.generate_markdown_report(results)
            
            # Post to GitHub if available
            self.post_github_comment(md_report)
            
            # Upload artifacts
            self.upload_artifacts(json_report)
            
            # Determine exit code
            exit_code = self.determine_exit_code(results)
            
            print("=" * 60)
            print(f"Pipeline complete. Exit code: {exit_code}")
            print("=" * 60)
            
            return exit_code
            
        except Exception as e:
            print(f"❌ Pipeline failed: {e}")
            return 2


def main():
    """Main entry point for CI/CD integration."""
    repo_path = os.getenv('REPO_PATH', '.')
    
    integration = CICDIntegration(repo_path)
    exit_code = integration.run_ci_pipeline()
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
