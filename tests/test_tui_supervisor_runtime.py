from __future__ import annotations

from pathlib import Path

from src.agent_types import AgentRunResult, UsageStats
from src.background_runtime import BackgroundSessionRecord
from src.tui_supervisor import (
    append_worker_event,
    load_worker_result,
    read_worker_events,
    run_background_turn,
    save_worker_result,
    worker_event_path,
)


class _FakeRuntime:
    def __init__(self, root: Path, records: list[BackgroundSessionRecord]) -> None:
        self.root = root
        self._records = list(records)
        self.on_load = None

    def load_record(self, background_id: str) -> BackgroundSessionRecord:
        if self.on_load is not None:
            self.on_load(background_id)
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
        exit_code=0 if status == 'completed' else 1 if status == 'failed' else None,
        stop_reason=stop_reason,
        session_id=session_id,
        session_path=session_path,
    )


def test_worker_result_round_trip(tmp_path: Path) -> None:
    result = AgentRunResult(
        final_output='hello from worker',
        turns=2,
        tool_calls=1,
        transcript=({'role': 'assistant', 'content': 'hello from worker'},),
        events=({'type': 'tool_result'},),
        usage=UsageStats(input_tokens=5, output_tokens=2),
        total_cost_usd=0.12,
        stop_reason='stop',
        file_history=({'action': 'read_file'},),
        session_id='sess_123',
        session_path='/tmp/sess_123.json',
        scratchpad_directory='/tmp/scratch',
    )

    save_worker_result(tmp_path, 'bg_123', result)
    loaded = load_worker_result(tmp_path, 'bg_123')

    assert loaded == result


def test_worker_events_round_trip_from_offset(tmp_path: Path) -> None:
    append_worker_event(tmp_path, 'bg_events', {'type': 'content_delta', 'delta': 'hel'})
    first, offset = read_worker_events(tmp_path, 'bg_events')
    append_worker_event(tmp_path, 'bg_events', {'type': 'content_delta', 'delta': 'lo'})
    second, final_offset = read_worker_events(tmp_path, 'bg_events', offset=offset)

    assert first == [{'type': 'content_delta', 'delta': 'hel'}]
    assert second == [{'type': 'content_delta', 'delta': 'lo'}]
    assert final_offset > offset


def test_worker_events_do_not_consume_partial_line(tmp_path: Path) -> None:
    path = append_worker_event(tmp_path, 'bg_partial', {'type': 'content_delta', 'delta': 'ready'})
    first, offset = read_worker_events(tmp_path, 'bg_partial')
    with path.open('a', encoding='utf-8') as handle:
        handle.write('{"type":"content_delta","delta":"partial"}')

    partial, partial_offset = read_worker_events(tmp_path, 'bg_partial', offset=offset)
    with worker_event_path(tmp_path, 'bg_partial').open('a', encoding='utf-8') as handle:
        handle.write('\n')
    completed, completed_offset = read_worker_events(tmp_path, 'bg_partial', offset=partial_offset)

    assert first == [{'type': 'content_delta', 'delta': 'ready'}]
    assert partial == []
    assert partial_offset == offset
    assert completed == [{'type': 'content_delta', 'delta': 'partial'}]
    assert completed_offset > partial_offset


def test_run_background_turn_returns_loaded_result_when_worker_completes(tmp_path: Path) -> None:
    result = AgentRunResult(
        final_output='completed turn',
        turns=1,
        tool_calls=0,
        transcript=(),
        usage=UsageStats(input_tokens=3, output_tokens=1),
        session_id='sess_abc',
        session_path='/tmp/sess_abc.json',
    )
    save_worker_result(tmp_path, 'bg_ok', result)
    runtime = _FakeRuntime(
        tmp_path,
        [
            _record('bg_ok', status='running'),
            _record(
                'bg_ok',
                status='completed',
                session_id='sess_abc',
                session_path='/tmp/sess_abc.json',
                stop_reason='completed',
            ),
        ],
    )

    final_record, loaded = run_background_turn(
        runtime,
        launch_worker=lambda: _record('bg_ok', status='running'),
        poll_interval_seconds=0.0,
    )

    assert final_record.status == 'completed'
    assert loaded.final_output == 'completed turn'
    assert loaded.session_id == 'sess_abc'


def test_run_background_turn_drains_worker_events_while_polling(tmp_path: Path) -> None:
    result = AgentRunResult(
        final_output='completed turn',
        turns=1,
        tool_calls=0,
        transcript=(),
        session_id='sess_live',
    )
    save_worker_result(tmp_path, 'bg_live', result)
    runtime = _FakeRuntime(
        tmp_path,
        [
            _record('bg_live', status='running'),
            _record('bg_live', status='completed', session_id='sess_live'),
        ],
    )
    wrote_event = False

    def _on_load(background_id: str) -> None:
        nonlocal wrote_event
        if not wrote_event:
            append_worker_event(
                tmp_path,
                background_id,
                {'type': 'content_delta', 'delta': 'live'},
            )
            wrote_event = True

    runtime.on_load = _on_load
    seen_events: list[dict[str, object]] = []

    final_record, loaded = run_background_turn(
        runtime,
        launch_worker=lambda: _record('bg_live', status='running'),
        poll_interval_seconds=0.0,
        on_event=seen_events.append,
    )

    assert final_record.status == 'completed'
    assert loaded.session_id == 'sess_live'
    assert seen_events == [{'type': 'content_delta', 'delta': 'live'}]
