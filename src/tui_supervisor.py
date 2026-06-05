from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .agent_types import AgentRunResult, JSONDict, UsageStats
from .background_runtime import BackgroundSessionRecord


def worker_result_path(root: Path, background_id: str) -> Path:
    return Path(root).resolve() / f'{background_id}.result.json'


def worker_event_path(root: Path, background_id: str) -> Path:
    return Path(root).resolve() / f'{background_id}.events.jsonl'


def append_worker_event(root: Path, background_id: str, event: JSONDict) -> Path:
    path = worker_event_path(root, background_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(dict(event), ensure_ascii=True, separators=(',', ':')) + '\n')
    return path


def read_worker_events(
    root: Path,
    background_id: str,
    *,
    offset: int = 0,
) -> tuple[list[JSONDict], int]:
    path = worker_event_path(root, background_id)
    if not path.exists():
        return [], offset
    events: list[JSONDict] = []
    with path.open('r', encoding='utf-8') as handle:
        handle.seek(max(0, offset))
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.endswith('\n'):
                handle.seek(line_start)
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        new_offset = handle.tell()
    return events, new_offset


def save_worker_result(root: Path, background_id: str, result: AgentRunResult) -> Path:
    path = worker_result_path(root, background_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'final_output': result.final_output,
        'turns': result.turns,
        'tool_calls': result.tool_calls,
        'transcript': list(result.transcript),
        'events': list(result.events),
        'usage': result.usage.to_dict(),
        'total_cost_usd': result.total_cost_usd,
        'stop_reason': result.stop_reason,
        'file_history': list(result.file_history),
        'session_id': result.session_id,
        'session_path': result.session_path,
        'scratchpad_directory': result.scratchpad_directory,
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
    return path


def load_worker_result(root: Path, background_id: str) -> AgentRunResult:
    payload = json.loads(worker_result_path(root, background_id).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('worker result payload must be a JSON object')
    return AgentRunResult(
        final_output=str(payload.get('final_output') or ''),
        turns=int(payload.get('turns') or 0),
        tool_calls=int(payload.get('tool_calls') or 0),
        transcript=_tuple_of_json_dicts(payload.get('transcript')),
        events=_tuple_of_json_dicts(payload.get('events')),
        usage=_usage_from_dict(payload.get('usage')),
        total_cost_usd=float(payload.get('total_cost_usd') or 0.0),
        stop_reason=(
            str(payload.get('stop_reason'))
            if isinstance(payload.get('stop_reason'), str) and payload.get('stop_reason')
            else None
        ),
        file_history=_tuple_of_json_dicts(payload.get('file_history')),
        session_id=(
            str(payload.get('session_id'))
            if isinstance(payload.get('session_id'), str) and payload.get('session_id')
            else None
        ),
        session_path=(
            str(payload.get('session_path'))
            if isinstance(payload.get('session_path'), str) and payload.get('session_path')
            else None
        ),
        scratchpad_directory=(
            str(payload.get('scratchpad_directory'))
            if isinstance(payload.get('scratchpad_directory'), str)
            and payload.get('scratchpad_directory')
            else None
        ),
    )


def synthesize_worker_failure_result(record: BackgroundSessionRecord) -> AgentRunResult:
    reason = record.stop_reason or record.status or 'worker_failed'
    return AgentRunResult(
        final_output=(
            'Worker exited before returning a result. '
            f'status={record.status} stop_reason={reason}. '
            'The chat supervisor is still alive; you can continue from the saved session.'
        ),
        turns=0,
        tool_calls=0,
        transcript=(),
        usage=UsageStats(),
        total_cost_usd=0.0,
        stop_reason=reason,
        file_history=(),
        session_id=record.session_id,
        session_path=record.session_path,
    )


def run_background_turn(
    runtime,
    *,
    launch_worker,
    poll_interval_seconds: float = 0.1,
    timeout_seconds: float | None = None,
    on_event: Callable[[JSONDict], None] | None = None,
) -> tuple[BackgroundSessionRecord, AgentRunResult]:
    record = launch_worker()
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    event_offset = 0

    def _drain_events() -> None:
        nonlocal event_offset
        if on_event is None:
            return
        events, event_offset = read_worker_events(
            runtime.root,
            record.background_id,
            offset=event_offset,
        )
        for event in events:
            on_event(event)

    while True:
        _drain_events()
        current = runtime.load_record(record.background_id)
        _drain_events()
        if current.status != 'running':
            try:
                return current, load_worker_result(runtime.root, current.background_id)
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                return current, synthesize_worker_failure_result(current)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f'background turn timed out: {record.background_id}')
        time.sleep(max(0.0, poll_interval_seconds))


def _usage_from_dict(payload: object) -> UsageStats:
    if not isinstance(payload, dict):
        return UsageStats()
    return UsageStats(
        input_tokens=int(payload.get('input_tokens') or 0),
        output_tokens=int(payload.get('output_tokens') or 0),
        cache_creation_input_tokens=int(payload.get('cache_creation_input_tokens') or 0),
        cache_read_input_tokens=int(payload.get('cache_read_input_tokens') or 0),
        reasoning_tokens=int(payload.get('reasoning_tokens') or 0),
    )


def _tuple_of_json_dicts(payload: object) -> tuple[JSONDict, ...]:
    if not isinstance(payload, list):
        return ()
    return tuple(item for item in payload if isinstance(item, dict))
