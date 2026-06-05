"""
Base class for benchmark suites and shared benchmark helpers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_component(value: str) -> str:
    cleaned = _SAFE_COMPONENT_RE.sub("_", value).strip("._")
    return cleaned or "item"


def resolve_temp_root() -> Path:
    root = Path(tempfile.gettempdir()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_temp_workspace(prefix: str, suite_name: str, problem_id: str) -> str:
    temp_root = resolve_temp_root()
    safe_prefix = _safe_component(prefix)
    safe_suite = _safe_component(suite_name)
    safe_problem = _safe_component(problem_id)
    return tempfile.mkdtemp(
        prefix=f"{safe_prefix}_{safe_suite}_{safe_problem}_",
        dir=str(temp_root),
    )


@dataclass
class BenchmarkResult:
    problem_id: str
    passed: bool
    expected: str = ""
    actual: str = ""
    duration_sec: float = 0.0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SuiteReport:
    suite_name: str
    total: int
    passed: int
    failed: int
    score_pct: float
    duration_sec: float
    model: str
    results: list[BenchmarkResult]
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "score_pct": self.score_pct,
            "duration_sec": round(self.duration_sec, 2),
            "model": self.model,
            "timestamp": self.timestamp,
            "results": [asdict(r) for r in self.results],
        }


class BenchmarkSuite(ABC):
    name: str = "base"
    description: str = ""
    category: str = "general"

    def __init__(
        self,
        *,
        data_dir: str | None = None,
        limit: int | None = None,
        agent_timeout: float = 300.0,
        verbose: bool = False,
        artifacts_dir: str | None = None,
        save_passing_artifacts: bool = False,
        rate_limit_seconds: float = 2.0,
    ) -> None:
        self.data_dir = data_dir or str(
            Path(__file__).resolve().parent.parent / "data"
        )
        self.limit = limit
        self.agent_timeout = agent_timeout
        self.verbose = verbose
        self.artifacts_dir = artifacts_dir
        self.save_passing_artifacts = save_passing_artifacts
        self.project_root = str(Path(__file__).resolve().parent.parent.parent)
        self.rate_limit_seconds = rate_limit_seconds

    @abstractmethod
    def load_dataset(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def build_prompt(self, problem: dict[str, Any]) -> str:
        ...

    @abstractmethod
    def evaluate(self, problem: dict[str, Any], workspace: str) -> BenchmarkResult:
        ...

    def _run_shell(
        self,
        cmd: str,
        cwd: str,
        timeout: float = 30.0,
    ) -> tuple[int, str]:
        import copy
        # Explicitly forward model credentials + disable behavioral gate for benchmarks
        env = dict(os.environ)  # true copy — copy.copy(os.environ) returns _Environ which mutates real env
        for key in ('OPENAI_MODEL', 'OPENAI_BASE_URL', 'OPENAI_API_KEY',
                    'LATTI_COPILOT_HEADERS', 'LATTI_MODEL_HEAVY',
                    'LATTI_MODEL_LIGHT', 'LATTI_MODEL_MICRO'):
            if key in os.environ:
                env[key] = os.environ[key]
        env['LATTI_GATE'] = '0'  # disable response gate — benchmarks need clean output
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return proc.returncode, (proc.stdout + proc.stderr).strip()
        except subprocess.TimeoutExpired:
            return 1, f"[TIMEOUT after {timeout}s]"
        except Exception as exc:
            return 1, str(exc)

    def run_agent(self, instruction: str, workspace: str) -> tuple[int, str, float]:
        import shlex

        # Pick up model endpoint from environment (set by latti shim or caller)
        model    = os.environ.get('OPENAI_MODEL', 'anthropic/claude-sonnet-4.6')
        base_url = os.environ.get('OPENAI_BASE_URL', 'https://openrouter.ai/api/v1')
        api_key  = os.environ.get('OPENAI_API_KEY', '')

        agent_cmd = (
            f"{sys.executable} -m src.main agent "
            f"{shlex.quote(instruction)} "
            f"--cwd {shlex.quote(workspace)} "
            f"--allow-write "
            f"--allow-shell "
            f"--model {shlex.quote(model)} "
            f"--base-url {shlex.quote(base_url)} "
            + (f"--api-key {shlex.quote(api_key)} " if api_key else "")
        )
        if self.verbose:
            print(f"  agent cmd: {agent_cmd[:160]}...")

        start = time.time()
        code, output = self._run_shell(
            agent_cmd,
            cwd=self.project_root,
            timeout=self.agent_timeout,
        )
        duration = time.time() - start

        if self.verbose:
            print(f"  agent exit={code}  duration={duration:.1f}s")

        return code, output, duration

    def setup_workspace(self, problem: dict[str, Any], workspace: str) -> None:
        del problem, workspace

    def recover_output_files(
        self,
        problem: dict[str, Any],
        workspace: str,
        agent_output: str,
        metadata: dict[str, Any],
    ) -> None:
        del problem, workspace, agent_output, metadata

    def _artifact_root(self, index: int, problem_id: str) -> Path | None:
        if not self.artifacts_dir:
            return None
        root = Path(self.artifacts_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{index:03d}_{_safe_component(problem_id)}"

    def _save_artifacts(
        self,
        *,
        index: int,
        problem: dict[str, Any],
        prompt: str,
        agent_output: str,
        workspace: str,
        result: BenchmarkResult,
        agent_exit_code: int,
    ) -> None:
        artifact_root = self._artifact_root(index, result.problem_id)
        if artifact_root is None:
            return
        if result.passed and not self.save_passing_artifacts:
            return

        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "problem.json").write_text(
            json.dumps(problem, indent=2) + "\n",
            encoding="utf-8",
        )
        (artifact_root / "prompt.txt").write_text(prompt, encoding="utf-8")
        (artifact_root / "agent_output.txt").write_text(agent_output, encoding="utf-8")

        workspace_dst = artifact_root / "workspace"
        if workspace_dst.exists():
            shutil.rmtree(workspace_dst, ignore_errors=True)
        shutil.copytree(workspace, workspace_dst)

        result_payload = asdict(result)
        result_payload["agent_exit_code"] = agent_exit_code
        result_payload["workspace"] = workspace
        (artifact_root / "result.json").write_text(
            json.dumps(result_payload, indent=2) + "\n",
            encoding="utf-8",
        )
        result.metadata["artifact_path"] = str(artifact_root)

    def run_all(self) -> SuiteReport:
        problems = self.load_dataset()
        if self.limit is not None:
            problems = problems[: self.limit]

        print()
        print("=" * 72)
        print(f"  {self.name} BENCHMARK")
        print(f"  {self.description}")
        print("=" * 72)
        model = os.environ.get("OPENAI_MODEL", "unknown")
        print(f"  Model:    {model}")
        print(f"  Problems: {len(problems)}")
        print(f"  Timeout:  {self.agent_timeout}s per problem")
        print("=" * 72)
        print()

        suite_start = time.time()
        all_results: list[BenchmarkResult] = []

        for index, problem in enumerate(problems, 1):
            pid = str(problem.get("id", problem.get("task_id", f"problem-{index}")))
            print(f"[{index}/{len(problems)}] {pid}")

            # Rate limit between problems to avoid 429s from Copilot/OpenRouter
            if index > 1 and self.rate_limit_seconds > 0:
                time.sleep(self.rate_limit_seconds)

            workspace = make_temp_workspace("claw", self.name, pid)
            prompt = ""
            agent_output = ""
            agent_exit_code = -1
            try:
                self.setup_workspace(problem, workspace)
                prompt = self.build_prompt(problem)
                agent_exit_code, agent_output, duration = self.run_agent(prompt, workspace)

                result_metadata: dict[str, Any] = {"agent_exit_code": agent_exit_code}
                self.recover_output_files(problem, workspace, agent_output, result_metadata)
                result = self.evaluate(problem, workspace)
                result.duration_sec = duration
                result.metadata.update(result_metadata)

                self._save_artifacts(
                    index=index,
                    problem=problem,
                    prompt=prompt,
                    agent_output=agent_output,
                    workspace=workspace,
                    result=result,
                    agent_exit_code=agent_exit_code,
                )

                status = "PASS ✅" if result.passed else "FAIL ❌"
                print(f"  -> {status}  ({duration:.1f}s)")
            except Exception as exc:
                result = BenchmarkResult(
                    problem_id=pid,
                    passed=False,
                    error=str(exc),
                    metadata={"agent_exit_code": agent_exit_code},
                )
                self._save_artifacts(
                    index=index,
                    problem=problem,
                    prompt=prompt,
                    agent_output=agent_output,
                    workspace=workspace,
                    result=result,
                    agent_exit_code=agent_exit_code,
                )
                print(f"  -> ERROR ❌  {exc}")
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

            all_results.append(result)
            print()

        suite_duration = time.time() - suite_start
        passed = sum(1 for item in all_results if item.passed)
        total = len(all_results)
        report = SuiteReport(
            suite_name=self.name,
            total=total,
            passed=passed,
            failed=total - passed,
            score_pct=round(100.0 * passed / total, 1) if total else 0.0,
            duration_sec=suite_duration,
            model=model,
            results=all_results,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._print_report(report)
        return report

    @staticmethod
    def _print_report(report: SuiteReport) -> None:
        print()
        print("=" * 72)
        print(f"  {report.suite_name} — RESULTS")
        print("=" * 72)
        print()
        for result in report.results:
            icon = "✅" if result.passed else "❌"
            print(f"  {icon} {result.problem_id:<40} {result.duration_sec:.1f}s")
        print()
        print("─" * 72)
        print(
            f"  Total: {report.total}  |  Passed: {report.passed}  "
            f"|  Failed: {report.failed}  |  Score: {report.score_pct:.1f}%"
        )
        print(f"  Total time: {report.duration_sec:.1f}s")
        print("─" * 72)
        print()

    @staticmethod
    def save_report(report: SuiteReport, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2)
        print(f"  Report saved to {path}")
