from __future__ import annotations

from pathlib import Path

from src.background_runtime import BackgroundSessionRecord
from src.tui_supervisor import run_background_turn


class _FakeRuntime:
    def __init__(self, root: Path, records: list[BackgroundSessionRecord]) -> None:
        self.root = root
        self._records = list(records)

    def load_record(self, background_id: str) -> BackgroundSessionRecord:
        assert self._records
        return self._records.pop(0)


def _record(
    background_id: str,
    *,
    status: str,
    session_id: str | None = None,
    session_path: str | None = None,
    stop_reason: str | None = None,
) -> BackgroundSessionRecord:
    return BackgroundSessionRecord(
        background_id=background_id,
        pid=123,
        prompt='prompt',
        workspace_cwd='/tmp',
        model='gpt-4o-mini',
        mode='agent',
        status=status,
        log_path='/tmp/log.txt',
        record_path='/tmp/record.json',
        started_at='2026-04-29T00:00:00+00:00',
        command=('python3', '-m', 'src.main'),
        finished_at='2026-04-29T00:00:01+00:00' if status != 'running' else None,
        exit_code=1 if status in {'failed', 'exited', 'killed'} else None,
        stop_reason=stop_reason,
        session_id=session_id,
        session_path=session_path,
    )


def test_run_background_turn_synthesizes_recoverable_result_when_worker_dies(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime(
        tmp_path,
        [
            _record('bg_fail', status='running'),
            _record(
                'bg_fail',
                status='failed',
                session_id='sess_recover',
                session_path='/tmp/sess_recover.json',
                stop_reason='worker_failed',
            ),
        ],
    )

    final_record, result = run_background_turn(
        runtime,
        launch_worker=lambda: _record('bg_fail', status='running'),
        poll_interval_seconds=0.0,
    )

    assert final_record.status == 'failed'
    assert result.stop_reason == 'worker_failed'
    assert result.session_id == 'sess_recover'
    assert 'worker exited before returning a result' in result.final_output.lower()
