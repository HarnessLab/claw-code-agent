#!/usr/bin/env python3
"""
Standard evaluation benchmark runner for claw-code-agent.

Runs the agent against well-known evaluation suites and reports scores.

Usage:
    # List available benchmark suites
    python3 -m benchmarks.run_suite --list

    # Run a specific suite (built-in subset)
    python3 -m benchmarks.run_suite --suite humaneval
    python3 -m benchmarks.run_suite --suite mbpp
    python3 -m benchmarks.run_suite --suite swe-bench
    python3 -m benchmarks.run_suite --suite aider
    python3 -m benchmarks.run_suite --suite livecodebench
    python3 -m benchmarks.run_suite --suite math
    python3 -m benchmarks.run_suite --suite gsm8k
    python3 -m benchmarks.run_suite --suite aime
    python3 -m benchmarks.run_suite --suite ifeval
    python3 -m benchmarks.run_suite --suite bfcl

    # Run ALL suites
    python3 -m benchmarks.run_suite --all

    # Run by category
    python3 -m benchmarks.run_suite --category coding
    python3 -m benchmarks.run_suite --category math
    python3 -m benchmarks.run_suite --category instruction-following

    # Limit problems per suite (for quick testing)
    python3 -m benchmarks.run_suite --suite humaneval --limit 5

    # Verbose output + save results
    python3 -m benchmarks.run_suite --suite humaneval -v -o results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from benchmarks.suites.base import BenchmarkSuite, SuiteReport


def _load_env_file() -> None:
    """Load environment variables from ~/.latti/.env if it exists."""
    env_file = Path.home() / ".latti" / ".env"
    if env_file.exists():
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith("#"):
                        continue
                    # Parse KEY=VALUE
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        # Only set if not already in environment
                        if key and key not in os.environ:
                            os.environ[key] = value
        except Exception:
            pass  # Silently ignore errors reading .env file


# Load environment variables from ~/.latti/.env
_load_env_file()

# Map OPENROUTER_API_KEY to OPENAI_API_KEY if needed
if "OPENROUTER_API_KEY" in os.environ and "OPENAI_API_KEY" not in os.environ:
    os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]

# Import all suites
from benchmarks.suites.humaneval import HumanEvalBenchmark
from benchmarks.suites.mbpp import MBPPBenchmark
from benchmarks.suites.swe_bench import SWEBenchBenchmark
from benchmarks.suites.aider import AiderBenchmark
from benchmarks.suites.livecodebench import LiveCodeBenchBenchmark
from benchmarks.suites.math_bench import MATHBenchmark
from benchmarks.suites.gsm8k import GSM8KBenchmark
from benchmarks.suites.aime import AIMEBenchmark
from benchmarks.suites.ifeval import IFEvalBenchmark
from benchmarks.suites.bfcl import BFCLBenchmark
from benchmarks.suites.mmlu_pro import MMLUProBenchmark
from benchmarks.suites.gpqa import GPQABenchmark
from benchmarks.suites.bigbench import BigBenchHardBenchmark
from benchmarks.suites.mmmlu import MMMMLUBenchmark
from benchmarks.suites.hle import HLEBenchmark
from benchmarks.suites.tau2 import Tau2Benchmark
from benchmarks.suites.codeforces import CodeforcesBenchmark


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SUITE_REGISTRY: dict[str, type[BenchmarkSuite]] = {
    "humaneval": HumanEvalBenchmark,
    "mbpp": MBPPBenchmark,
    "swe-bench": SWEBenchBenchmark,
    "aider": AiderBenchmark,
    "livecodebench": LiveCodeBenchBenchmark,
    "math": MATHBenchmark,
    "gsm8k": GSM8KBenchmark,
    "aime": AIMEBenchmark,
    "ifeval": IFEvalBenchmark,
    "bfcl": BFCLBenchmark,
    "mmlu-pro": MMLUProBenchmark,
    "gpqa-diamond": GPQABenchmark,
    "bigbench-hard": BigBenchHardBenchmark,
    "mmmlu": MMMMLUBenchmark,
    "hle": HLEBenchmark,
    "tau2": Tau2Benchmark,
    "codeforces": CodeforcesBenchmark,
}

CATEGORY_MAP: dict[str, list[str]] = {
    "coding": ["humaneval", "mbpp", "swe-bench", "aider", "livecodebench", "codeforces"],
    "math": ["math", "gsm8k", "aime"],
    "instruction-following": ["ifeval", "bfcl"],
    "knowledge": ["mmlu-pro", "gpqa-diamond", "mmmlu", "hle"],
    "reasoning": ["bigbench-hard", "tau2"],
}


# ---------------------------------------------------------------------------
# Multi-suite reporting
# ---------------------------------------------------------------------------

def print_combined_report(reports: list[SuiteReport]) -> None:
    """Print a combined summary of all suite runs."""
    print()
    print("=" * 80)
    print("  COMBINED BENCHMARK REPORT")
    print("=" * 80)
    print()
    print(f"  {'Suite':<20} {'Category':<22} {'Passed':>8} {'Total':>8} {'Score':>8} {'Time':>8}")
    print(f"  {'─' * 20} {'─' * 22} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8}")

    total_passed = 0
    total_problems = 0
    total_time = 0.0

    for r in reports:
        suite_cls = SUITE_REGISTRY.get(r.suite_name.lower().replace("-", "").replace("_", ""))
        cat = suite_cls.category if suite_cls else "unknown"
        print(
            f"  {r.suite_name:<20} {cat:<22} "
            f"{r.passed:>8} {r.total:>8} "
            f"{r.score_pct:>7.1f}% {r.duration_sec:>7.1f}s"
        )
        total_passed += r.passed
        total_problems += r.total
        total_time += r.duration_sec

    print()
    print("─" * 80)
    overall_pct = round(100.0 * total_passed / total_problems, 1) if total_problems else 0.0
    print(
        f"  OVERALL: {total_passed}/{total_problems} ({overall_pct}%) "
        f"in {total_time:.1f}s"
    )
    print("─" * 80)

    # Category breakdown
    print()
    print("  Category Breakdown:")
    for cat_name, suite_names in CATEGORY_MAP.items():
        cat_reports = [r for r in reports if r.suite_name.lower().replace("-", "").replace("_", "") in
                       [s.replace("-", "").replace("_", "") for s in suite_names]]
        if cat_reports:
            cp = sum(r.passed for r in cat_reports)
            ct = sum(r.total for r in cat_reports)
            cpct = round(100.0 * cp / ct, 1) if ct else 0.0
            bar_len = 20
            filled = round(bar_len * cp / ct) if ct else 0
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"    {cat_name:<22} {bar}  {cp}/{ct} ({cpct}%)")

    print()


def save_combined_report(reports: list[SuiteReport], path: str) -> None:
    data = {
        "benchmark_run": "claw-code-agent-evaluation",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "suites": [r.to_dict() for r in reports],
        "summary": {
            "total_suites": len(reports),
            "total_problems": sum(r.total for r in reports),
            "total_passed": sum(r.passed for r in reports),
            "overall_score_pct": round(
                100.0 * sum(r.passed for r in reports) / sum(r.total for r in reports), 1
            ) if any(r.total for r in reports) else 0.0,
        },
    }
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    print(f"  Combined results saved to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run standard evaluation benchmarks against claw-code-agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 -m benchmarks.run_suite --list\n"
            "  python3 -m benchmarks.run_suite --suite humaneval\n"
            "  python3 -m benchmarks.run_suite --suite humaneval --limit 5 -v\n"
            "  python3 -m benchmarks.run_suite --category coding\n"
            "  python3 -m benchmarks.run_suite --all\n"
            "  python3 -m benchmarks.run_suite --all -o results.json\n"
        ),
    )
    parser.add_argument("--suite", action="append", default=[],
                        help="Run a specific benchmark suite (can be repeated)")
    parser.add_argument("--category", choices=list(CATEGORY_MAP.keys()),
                        help="Run all suites in a category")
    parser.add_argument("--all", action="store_true",
                        help="Run ALL benchmark suites")
    parser.add_argument("--list", action="store_true",
                        help="List available benchmark suites")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max problems per suite (for quick testing)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Agent timeout per problem in seconds (default: 300)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--output", "-o",
                        help="Save results to JSON file")
    parser.add_argument("--data-dir",
                        help="Directory containing dataset files (JSONL)")
    parser.add_argument("--artifacts-dir",
                        help="Directory where per-problem artifacts will be saved")
    parser.add_argument("--save-passing-artifacts", action="store_true",
                        help="Also save artifacts for passing problems")
    args = parser.parse_args()

    if args.list:
        print()
        print("  Available Benchmark Suites:")
        print("  " + "─" * 68)
        print(f"  {'Name':<18} {'Category':<22} {'Description'}")
        print("  " + "─" * 68)
        for name, cls in SUITE_REGISTRY.items():
            print(f"  {name:<18} {cls.category:<22} {cls.description}")
        print()
        print("  Categories:")
        for cat, suites in CATEGORY_MAP.items():
            print(f"    {cat}: {', '.join(suites)}")
        print()
        return

    # Determine which suites to run
    suite_names: list[str] = []
    if args.all:
        suite_names = list(SUITE_REGISTRY.keys())
    elif args.category:
        suite_names = CATEGORY_MAP.get(args.category, [])
    elif args.suite:
        for s in args.suite:
            if s not in SUITE_REGISTRY:
                print(f"Error: unknown suite '{s}'. Available: {', '.join(SUITE_REGISTRY)}")
                sys.exit(1)
        suite_names = list(args.suite)
    else:
        parser.print_help()
        print("\nError: specify --suite, --category, or --all")
        sys.exit(1)

    if not suite_names:
        print("No suites matched.")
        sys.exit(1)

    # Run suites
    reports: list[SuiteReport] = []
    for name in suite_names:
        cls = SUITE_REGISTRY[name]
        suite = cls(
            data_dir=args.data_dir,
            limit=args.limit,
            agent_timeout=args.timeout,
            verbose=args.verbose,
            artifacts_dir=args.artifacts_dir,
            save_passing_artifacts=args.save_passing_artifacts,
        )
        report = suite.run_all()
        reports.append(report)

    # Combined report
    if len(reports) > 1:
        print_combined_report(reports)

    if args.output:
        save_combined_report(reports, args.output)


if __name__ == "__main__":
    main()
