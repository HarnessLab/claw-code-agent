#!/usr/bin/env python3
"""Smoke the real Latti wrapper supervisor path.

This is intentionally a script, not a unit test. It launches ../latti in a
PTY so the real TUI path is active, forces low-memory mode, forces the chat
supervisor for a non-user smoke, and uses a local OpenAI-compatible fake server
so the run costs nothing and never reaches the network.
"""
from __future__ import annotations

import argparse
import json
import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
V5_ROOT = REPO.parent
LATTI_WRAPPER = V5_ROOT / 'latti'
LAST_SESSION = Path.home() / '.latti' / 'last_session'
SESSION_DIR = REPO / '.port_sessions' / 'agent'


@dataclass
class FakeModelState:
    texts: list[str]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def next_text(self) -> str:
        if not self.texts:
            return 'smoke model fallback response'
        return self.texts.pop(0)


class FakeModelHandler(BaseHTTPRequestHandler):
    server: 'FakeModelServer'

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip('/') != '/v1/chat/completions':
            self.send_error(404, 'unknown smoke endpoint')
            return

        raw_length = self.headers.get('Content-Length', '0')
        try:
            length = int(raw_length)
        except ValueError:
            length = 0
        raw = self.rfile.read(max(0, length))
        try:
            payload = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            payload = {}
        self.server.state.requests.append(payload)

        text = self.server.state.next_text()
        if payload.get('stream') is True:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            chunks = [text[: max(1, len(text) // 2)], text[max(1, len(text) // 2) :]]
            for chunk in chunks:
                if not chunk:
                    continue
                event = {'choices': [{'delta': {'content': chunk}}]}
                self.wfile.write(f'data: {json.dumps(event)}\n\n'.encode('utf-8'))
                self.wfile.flush()
            stop = {
                'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 9, 'completion_tokens': 3},
            }
            self.wfile.write(f'data: {json.dumps(stop)}\n\n'.encode('utf-8'))
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            return

        body = {
            'choices': [
                {
                    'message': {'role': 'assistant', 'content': text},
                    'finish_reason': 'stop',
                }
            ],
            'usage': {'prompt_tokens': 9, 'completion_tokens': 3},
        }
        data = json.dumps(body).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], state: FakeModelState) -> None:
        super().__init__(addr, FakeModelHandler)
        self.state = state


class LastSessionBackup:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.existed = path.exists()
        self.content = path.read_bytes() if self.existed else b''

    def clear_for_smoke(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def restore(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.existed:
            self.path.write_bytes(self.content)
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r'\x1b\[[0-9;?]*[ -/]*[@-~]', '', text)


def _spawn_latti(
    *,
    cwd: Path,
    prompt: str,
    base_url: str,
    force_worker_failure: bool,
    timeout_seconds: float,
) -> tuple[int, str]:
    if not LATTI_WRAPPER.exists():
        raise AssertionError(f'latti wrapper missing: {LATTI_WRAPPER}')

    master_fd, slave_fd = pty.openpty()
    command = [
        str(LATTI_WRAPPER),
        str(cwd),
        prompt,
        '--model',
        'smoke-model',
        '--base-url',
        base_url,
        '--api-key',
        'smoke-token',
        '--timeout-seconds',
        '5',
        '--input-cost-per-million',
        '0',
        '--output-cost-per-million',
        '0',
        '--max-model-calls',
        '4',
        '--max-session-turns',
        '4',
    ]
    env = os.environ.copy()
    env.update(
        {
            'TERM': env.get('TERM') or 'xterm-256color',
            'LATTI_BOOT': '0',
            'LATTI_LOW_MEM': '1',
            'LATTI_MIN_SAFE_MB': '0',
            'LATTI_FORCE_CHAT_SUPERVISOR': '1',
            'LATTI_USE_CHAT_SUPERVISOR': 'force',
            'LATTI_BRAID_COMMIT': '0',
            'LATTI_PROMPT_CACHE': '0',
            'LATTI_AUDIT': '0',
            'LATTI_IDENTITY_COMPILE': '0',
            'LATTI_COMMAND_TIMEOUT': '5',
            'OPENAI_BASE_URL': base_url,
            'OPENAI_API_KEY': 'smoke-token',
            'OPENAI_MODEL': 'smoke-model',
        }
    )
    if force_worker_failure:
        env['LATTI_SUPERVISOR_SMOKE_FAIL_AFTER_SESSION'] = '1'

    proc = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(V5_ROOT),
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    deadline = time.monotonic() + timeout_seconds
    output = bytearray()
    sent_exit = False
    exit_after: float | None = None
    last_resend = 0.0
    try:
        while True:
            if proc.poll() is not None:
                break
            if time.monotonic() > deadline:
                plain_tail = _strip_ansi(output.decode('utf-8', errors='replace'))[-4000:]
                raise TimeoutError(
                    f'latti smoke timed out after {timeout_seconds}s\n{plain_tail}'
                )
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    chunk = b''
                if chunk:
                    output.extend(chunk)
            plain = _strip_ansi(output.decode('utf-8', errors='replace'))
            if exit_after is None and (
                'Worker exited before returning a result' in plain
                or 'smoke supervisor healthy' in plain
                or 'smoke resume ok' in plain
            ):
                # Wait long enough for the agent to finish the turn, draw the
                # second prompt, and enter raw mode. tty.setraw uses TCSAFLUSH
                # which discards pending input; bytes written before raw-mode
                # entry are dropped, so we delay AND resend until the process
                # actually exits.
                exit_after = time.monotonic() + 1.5
            if exit_after is not None and time.monotonic() >= exit_after:
                # \x04 = EOF (Ctrl-D). _read_multiline raises EOFError on it
                # when the buffer is empty, which the main loop catches and
                # cleanly returns. Single byte means no partial-delivery race.
                if not sent_exit or (time.monotonic() - last_resend) > 1.0:
                    try:
                        os.write(master_fd, b'\x04')
                    except OSError:
                        pass
                    last_resend = time.monotonic()
                    sent_exit = True
            if sent_exit and proc.poll() is not None:
                break
        try:
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0)
                if not ready:
                    break
                chunk = os.read(master_fd, 8192)
                if not chunk:
                    break
                output.extend(chunk)
        except OSError:
            pass
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        raise
    finally:
        os.close(master_fd)

    return proc.wait(timeout=2), output.decode('utf-8', errors='replace')


def _latest_background_record() -> dict[str, Any]:
    background_dir = REPO / '.port_sessions' / 'background'
    records = sorted(background_dir.glob('bg_*.json'), key=lambda path: path.stat().st_mtime)
    if not records:
        raise AssertionError('no background supervisor record was written')
    return json.loads(records[-1].read_text(encoding='utf-8'))


def _assert_session_file(session_id: str) -> Path:
    session_path = SESSION_DIR / f'{session_id}.json'
    if not session_path.exists():
        raise AssertionError(f'saved session file missing: {session_path}')
    payload = json.loads(session_path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or not payload.get('messages'):
        raise AssertionError(f'saved session file is not usable: {session_path}')
    return session_path


def _messages_blob(request_payload: dict[str, Any]) -> str:
    return json.dumps(request_payload.get('messages', []), ensure_ascii=True)


def run_smoke(timeout_seconds: float) -> None:
    state = FakeModelState(
        texts=[
            'smoke supervisor healthy',
            'smoke failure turn saved before worker exit',
            'smoke resume ok',
        ]
    )
    port = _free_port()
    server = FakeModelServer(('127.0.0.1', port), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{port}/v1'

    backup = LastSessionBackup(LAST_SESSION)
    created_session_id = ''
    try:
        backup.clear_for_smoke()
        with tempfile.TemporaryDirectory(prefix='latti-supervisor-smoke-') as tmp:
            smoke_cwd = Path(tmp)

            healthy_code, healthy_output = _spawn_latti(
                cwd=smoke_cwd,
                prompt='smoke healthy turn',
                base_url=base_url,
                force_worker_failure=False,
                timeout_seconds=timeout_seconds,
            )
            healthy_plain = _strip_ansi(healthy_output)
            if healthy_code != 0:
                raise AssertionError(f'healthy wrapper run exited {healthy_code}\n{healthy_plain}')
            if 'Latti' not in healthy_plain:
                raise AssertionError('TUI banner was not rendered in healthy run')
            if 'smoke supervisor healthy' not in healthy_plain:
                raise AssertionError('healthy run did not stream fake model response')
            if len(state.requests) < 1:
                raise AssertionError('fake model saw no healthy request')
            # The failure scenario should start from a clean wrapper launch.
            # The resume check below intentionally uses the failed turn's
            # session id after the supervisor has preserved it.
            backup.clear_for_smoke()

            failure_code, failure_output = _spawn_latti(
                cwd=smoke_cwd,
                prompt='smoke forced worker failure turn',
                base_url=base_url,
                force_worker_failure=True,
                timeout_seconds=timeout_seconds,
            )
            failure_plain = _strip_ansi(failure_output)
            if failure_code != 0:
                raise AssertionError(f'failure wrapper run exited {failure_code}\n{failure_plain}')
            if 'Latti' not in failure_plain:
                raise AssertionError('TUI banner was not rendered in failure run')
            if 'Worker exited before returning a result' not in failure_plain:
                raise AssertionError('supervisor did not synthesize recoverable failure result')

            record = _latest_background_record()
            if record.get('status') != 'failed':
                raise AssertionError(f'expected failed worker record, got {record!r}')
            if record.get('stop_reason') != 'smoke_forced_worker_failure':
                raise AssertionError(f'expected forced smoke stop reason, got {record!r}')
            created_session_id = str(record.get('session_id') or '')
            if not created_session_id:
                raise AssertionError(f'failed worker record did not preserve session_id: {record!r}')
            session_path = _assert_session_file(created_session_id)

            persisted_last = LAST_SESSION.read_text(encoding='utf-8').strip()
            if persisted_last != created_session_id:
                raise AssertionError(
                    f'last_session mismatch: expected {created_session_id}, got {persisted_last}'
                )

            resume_code, resume_output = _spawn_latti(
                cwd=smoke_cwd,
                prompt='smoke resume turn',
                base_url=base_url,
                force_worker_failure=False,
                timeout_seconds=timeout_seconds,
            )
            resume_plain = _strip_ansi(resume_output)
            if resume_code != 0:
                raise AssertionError(f'resume wrapper run exited {resume_code}\n{resume_plain}')
            if 'smoke resume ok' not in resume_plain:
                raise AssertionError('resume wrapper run did not complete')
            if len(state.requests) < 3:
                raise AssertionError(f'expected at least 3 model requests, got {len(state.requests)}')
            resume_blob = _messages_blob(state.requests[-1])
            if 'smoke forced worker failure turn' not in resume_blob:
                raise AssertionError('resume request did not include saved failed-session prompt')
            if 'smoke failure turn saved before worker exit' not in resume_blob:
                raise AssertionError('resume request did not include saved failed-session assistant text')

            print('SMOKE PASS latti_supervisor')
            print(f'wrapper={LATTI_WRAPPER}')
            print('low_memory=forced')
            print('tui_banner=seen')
            print('supervisor=forced')
            print('worker_failure=smoke_forced_worker_failure')
            print(f'session_id={created_session_id}')
            print(f'session_path={session_path}')
            print('resume=verified')
            print(f'model_requests={len(state.requests)}')
    finally:
        backup.restore()
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Run the real latti wrapper supervisor smoke harness.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Expected trust signals:
              SMOKE PASS latti_supervisor
              low_memory=forced
              tui_banner=seen
              worker_failure=smoke_forced_worker_failure
              resume=verified
            """
        ),
    )
    parser.add_argument('--timeout-seconds', type=float, default=30.0)
    args = parser.parse_args(argv)
    run_smoke(timeout_seconds=args.timeout_seconds)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('SMOKE FAIL latti_supervisor', file=sys.stderr)
        print(str(exc), file=sys.stderr)
        raise
