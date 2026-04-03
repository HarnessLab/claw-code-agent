from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_BACKGROUND_DIR = Path('.port_sessions') / 'background'
_DETACHED_PROCESSES: dict[int, subprocess.Popen[Any]] = {}


@dataclass(frozen=True)
class BackgroundSessionRecord:
    background_id: str
    pid: int
    prompt: str
    workspace_cwd: str
    model: str
    mode: str
    status: str
    log_path: str
    record_path: str
    started_at: str
    command: tuple[str, ...]
    finished_at: str | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    session_id: str | None = None
    session_path: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> 'BackgroundSessionRecord':
        return cls(
            background_id=str(payload.get('background_id') or ''),
            pid=int(payload.get('pid') or 0),
            prompt=str(payload.get('prompt') or ''),
            workspace_cwd=str(payload.get('workspace_cwd') or ''),
            model=str(payload.get('model') or ''),
            mode=str(payload.get('mode') or 'agent'),
            status=str(payload.get('status') or 'unknown'),
            log_path=str(payload.get('log_path') or ''),
            record_path=str(payload.get('record_path') or ''),
            started_at=str(payload.get('started_at') or ''),
            command=tuple(
                str(item)
                for item in payload.get('command', [])
                if isinstance(item, (str, int, float))
            ),
            finished_at=(
                str(payload.get('finished_at'))
                if isinstance(payload.get('finished_at'), str) and payload.get('finished_at')
                else None
            ),
            exit_code=(
                int(payload.get('exit_code'))
                if isinstance(payload.get('exit_code'), int)
                else None
            ),
            stop_reason=(
                str(payload.get('stop_reason'))
                if isinstance(payload.get('stop_reason'), str) and payload.get('stop_reason')
                else None
            ),
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
        )


class BackgroundSessionRuntime:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or DEFAULT_BACKGROUND_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create_id(self) -> str:
        return f'bg_{uuid4().hex[:12]}'

    def record_path(self, background_id: str) -> Path:
        return self.root / f'{background_id}.json'

    def log_path(self, background_id: str) -> Path:
        return self.root / f'{background_id}.log'

    def launch(
        self,
        command: list[str],
        *,
        prompt: str,
        workspace_cwd: Path,
        model: str,
        mode: str = 'agent',
        background_id: str | None = None,
        process_cwd: Path | None = None,
    ) -> BackgroundSessionRecord:
        background_id = background_id or self.create_id()
        log_path = self.log_path(background_id)
        record_path = self.record_path(background_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open('a', encoding='utf-8') as handle:
            process = subprocess.Popen(
                command,
                stdout=handle,
                stderr=subprocess.STDOUT,
                cwd=str(process_cwd or Path.cwd()),
                start_new_session=True,
            )
        _DETACHED_PROCESSES[process.pid] = process
        record = BackgroundSessionRecord(
            background_id=background_id,
            pid=process.pid,
            prompt=prompt,
            workspace_cwd=str(workspace_cwd),
            model=model,
            mode=mode,
            status='running',
            log_path=str(log_path),
            record_path=str(record_path),
            started_at=_utc_now(),
            command=tuple(command),
        )
        self.save_record(record)
        return record

    def save_record(self, record: BackgroundSessionRecord) -> Path:
        path = Path(record.record_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(record), ensure_ascii=True, indent=2), encoding='utf-8')
        return path

    def load_record(self, background_id: str) -> BackgroundSessionRecord:
        data = json.loads(self.record_path(background_id).read_text(encoding='utf-8'))
        record = BackgroundSessionRecord.from_dict(data)
        return self.refresh_record(record)

    def list_records(self) -> tuple[BackgroundSessionRecord, ...]:
        records: list[BackgroundSessionRecord] = []
        for path in sorted(self.root.glob('bg_*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            records.append(self.refresh_record(BackgroundSessionRecord.from_dict(payload)))
        return tuple(sorted(records, key=lambda item: item.started_at, reverse=True))

    def refresh_record(self, record: BackgroundSessionRecord) -> BackgroundSessionRecord:
        if record.status != 'running':
            return record
        if _is_process_running(record.pid):
            return record
        updated = BackgroundSessionRecord(
            background_id=record.background_id,
            pid=record.pid,
            prompt=record.prompt,
            workspace_cwd=record.workspace_cwd,
            model=record.model,
            mode=record.mode,
            status='exited',
            log_path=record.log_path,
            record_path=record.record_path,
            started_at=record.started_at,
            command=record.command,
            finished_at=record.finished_at or _utc_now(),
            exit_code=record.exit_code,
            stop_reason=record.stop_reason,
            session_id=record.session_id,
            session_path=record.session_path,
        )
        self.save_record(updated)
        process = _DETACHED_PROCESSES.pop(record.pid, None)
        if process is not None and process.returncode is None:
            process.returncode = updated.exit_code
        return updated

    def mark_finished(
        self,
        background_id: str,
        *,
        exit_code: int,
        stop_reason: str | None = None,
        session_id: str | None = None,
        session_path: str | None = None,
        status: str | None = None,
    ) -> BackgroundSessionRecord:
        record = self.load_record(background_id)
        final_status = status or ('completed' if exit_code == 0 else 'failed')
        updated = BackgroundSessionRecord(
            background_id=record.background_id,
            pid=record.pid,
            prompt=record.prompt,
            workspace_cwd=record.workspace_cwd,
            model=record.model,
            mode=record.mode,
            status=final_status,
            log_path=record.log_path,
            record_path=record.record_path,
            started_at=record.started_at,
            command=record.command,
            finished_at=_utc_now(),
            exit_code=exit_code,
            stop_reason=stop_reason,
            session_id=session_id,
            session_path=session_path,
        )
        self.save_record(updated)
        process = _DETACHED_PROCESSES.pop(record.pid, None)
        if process is not None and process.returncode is None:
            process.returncode = updated.exit_code
        return updated

    def kill(self, background_id: str) -> BackgroundSessionRecord:
        record = self.load_record(background_id)
        if record.status != 'running':
            return record
        try:
            os.killpg(record.pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(record.pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                waited_pid, _ = os.waitpid(record.pid, os.WNOHANG)
                if waited_pid == record.pid:
                    break
            except ChildProcessError:
                break
            except OSError:
                break
            if not _is_process_running(record.pid):
                break
            time.sleep(0.05)
        updated = BackgroundSessionRecord(
            background_id=record.background_id,
            pid=record.pid,
            prompt=record.prompt,
            workspace_cwd=record.workspace_cwd,
            model=record.model,
            mode=record.mode,
            status='killed',
            log_path=record.log_path,
            record_path=record.record_path,
            started_at=record.started_at,
            command=record.command,
            finished_at=_utc_now(),
            exit_code=-signal.SIGTERM,
            stop_reason='killed',
            session_id=record.session_id,
            session_path=record.session_path,
        )
        self.save_record(updated)
        process = _DETACHED_PROCESSES.pop(record.pid, None)
        if process is not None and process.returncode is None:
            process.returncode = updated.exit_code
        return updated

    def read_logs(self, background_id: str, *, tail: int | None = None) -> str:
        record = self.load_record(background_id)
        path = Path(record.log_path)
        if not path.exists():
            return ''
        text = path.read_text(encoding='utf-8', errors='replace')
        if tail is None or tail <= 0:
            return text
        lines = text.splitlines()
        return '\n'.join(lines[-tail:])

    def render_ps(self) -> str:
        records = self.list_records()
        lines = ['# Background Sessions', '']
        if not records:
            lines.append('No local background sessions are currently recorded.')
            return '\n'.join(lines)
        for record in records:
            parts = [
                record.background_id,
                f'status={record.status}',
                f'pid={record.pid}',
                f'model={record.model}',
                f'cwd={record.workspace_cwd}',
            ]
            if record.exit_code is not None:
                parts.append(f'exit_code={record.exit_code}')
            lines.append('- ' + '; '.join(parts))
            lines.append(f'  prompt: {_snapshot_text(record.prompt)}')
        return '\n'.join(lines)

    def render_logs(self, background_id: str, *, tail: int | None = None) -> str:
        record = self.load_record(background_id)
        log_text = self.read_logs(background_id, tail=tail)
        lines = [
            '# Background Logs',
            '',
            f'- Background session: {record.background_id}',
            f'- Status: {record.status}',
            f'- PID: {record.pid}',
            f'- Log path: {record.log_path}',
            '',
            log_text.rstrip() or '(empty log)',
        ]
        return '\n'.join(lines)

    def render_attach(self, background_id: str, *, tail: int | None = None) -> str:
        record = self.load_record(background_id)
        lines = [
            '# Background Attach',
            '',
            f'- Background session: {record.background_id}',
            f'- Status: {record.status}',
            f'- Workspace cwd: {record.workspace_cwd}',
        ]
        if record.session_id:
            lines.append(f'- Agent session id: {record.session_id}')
        if record.session_path:
            lines.append(f'- Agent session path: {record.session_path}')
        lines.extend(['', self.read_logs(background_id, tail=tail).rstrip() or '(empty log)'])
        return '\n'.join(lines)


def build_background_worker_command(
    *,
    background_id: str,
    prompt: str,
    forwarded_args: list[str],
) -> list[str]:
    return [
        sys.executable,
        '-m',
        'src.main',
        'agent-bg-worker',
        background_id,
        prompt,
        *forwarded_args,
    ]


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_text(text: str, limit: int = 140) -> str:
    normalized = ' '.join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + '...'
