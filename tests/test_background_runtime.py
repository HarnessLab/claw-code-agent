from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from src.background_runtime import BackgroundSessionRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BackgroundRuntimeTests(unittest.TestCase):
    def test_runtime_can_launch_and_kill_generic_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            runtime = BackgroundSessionRuntime(workspace / '.port_sessions' / 'background')
            record = runtime.launch(
                [sys.executable, '-c', 'import time; time.sleep(10)'],
                prompt='sleep',
                workspace_cwd=workspace,
                model='local/test-model',
                process_cwd=workspace,
            )
            running = runtime.load_record(record.background_id)
            killed = runtime.kill(record.background_id)
            for _ in range(30):
                if runtime.load_record(record.background_id).status != 'running':
                    break
                time.sleep(0.1)

        self.assertEqual(running.status, 'running')
        self.assertEqual(killed.status, 'killed')
        self.assertEqual(killed.stop_reason, 'killed')

    def test_agent_background_cli_exposes_ps_logs_and_attach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            workspace = run_dir / 'workspace'
            workspace.mkdir()
            env = os.environ.copy()
            existing_pythonpath = env.get('PYTHONPATH')
            env['PYTHONPATH'] = (
                f'{PROJECT_ROOT}:{existing_pythonpath}'
                if existing_pythonpath
                else str(PROJECT_ROOT)
            )
            launch = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'src.main',
                    'agent-bg',
                    '/help',
                    '--cwd',
                    str(workspace),
                ],
                cwd=run_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            background_id = next(
                line.split('=', 1)[1]
                for line in launch.stdout.splitlines()
                if line.startswith('background_id=')
            )
            runtime = BackgroundSessionRuntime(run_dir / '.port_sessions' / 'background')
            record = runtime.load_record(background_id)
            for _ in range(60):
                if record.status in {'completed', 'failed', 'exited'}:
                    break
                time.sleep(0.1)
                record = runtime.load_record(background_id)
            ps = subprocess.run(
                [sys.executable, '-m', 'src.main', 'agent-ps'],
                cwd=run_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            logs = subprocess.run(
                [sys.executable, '-m', 'src.main', 'agent-logs', background_id],
                cwd=run_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            attach = subprocess.run(
                [sys.executable, '-m', 'src.main', 'agent-attach', background_id],
                cwd=run_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn(background_id, launch.stdout)
        self.assertIn(background_id, ps.stdout)
        self.assertIn('# Background Logs', logs.stdout)
        self.assertIn('# Slash Commands', logs.stdout)
        self.assertIn('# Background Attach', attach.stdout)
