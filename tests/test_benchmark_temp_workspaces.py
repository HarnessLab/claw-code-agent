from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.suites.base import make_temp_workspace, resolve_temp_root


class BenchmarkTempWorkspaceTests(unittest.TestCase):
    def test_make_temp_workspace_sanitizes_suite_and_problem_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "benchmarks.suites.base.tempfile.gettempdir",
                return_value=tmp_dir,
            ):
                workspace = make_temp_workspace("claw", "HumanEval", "HumanEval/0")
            try:
                workspace_path = Path(workspace)
                self.assertTrue(workspace_path.is_dir())
                self.assertEqual(workspace_path.parent.resolve(), Path(tmp_dir).resolve())
                self.assertNotIn("/", workspace_path.name)
                self.assertIn("HumanEval_0", workspace_path.name)
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

    def test_resolve_temp_root_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_root = Path(tmp_dir) / "nested" / "tmp"
            with patch(
                "benchmarks.suites.base.tempfile.gettempdir",
                return_value=str(missing_root),
            ):
                resolved = resolve_temp_root()
            self.assertEqual(resolved, missing_root.resolve())
            self.assertTrue(missing_root.is_dir())


if __name__ == "__main__":
    unittest.main()
