from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_types import AgentPermissions, AgentRuntimeConfig, ModelConfig


@dataclass(frozen=True)
class StoredSession:
    session_id: str
    messages: tuple[str, ...]
    input_tokens: int
    output_tokens: int


DEFAULT_SESSION_DIR = Path('.port_sessions')
DEFAULT_AGENT_SESSION_DIR = DEFAULT_SESSION_DIR / 'agent'


def save_session(session: StoredSession, directory: Path | None = None) -> Path:
    target_dir = directory or DEFAULT_SESSION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{session.session_id}.json'
    path.write_text(json.dumps(asdict(session), indent=2))
    return path


def load_session(session_id: str, directory: Path | None = None) -> StoredSession:
    target_dir = directory or DEFAULT_SESSION_DIR
    data = json.loads((target_dir / f'{session_id}.json').read_text())
    return StoredSession(
        session_id=data['session_id'],
        messages=tuple(data['messages']),
        input_tokens=data['input_tokens'],
        output_tokens=data['output_tokens'],
    )


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class StoredAgentSession:
    session_id: str
    model_config: JSONDict
    runtime_config: JSONDict
    system_prompt_parts: tuple[str, ...]
    user_context: dict[str, str]
    system_context: dict[str, str]
    messages: tuple[JSONDict, ...]
    turns: int
    tool_calls: int


def save_agent_session(session: StoredAgentSession, directory: Path | None = None) -> Path:
    target_dir = directory or DEFAULT_AGENT_SESSION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f'{session.session_id}.json'
    path.write_text(json.dumps(asdict(session), indent=2), encoding='utf-8')
    return path


def load_agent_session(session_id: str, directory: Path | None = None) -> StoredAgentSession:
    target_dir = directory or DEFAULT_AGENT_SESSION_DIR
    data = json.loads((target_dir / f'{session_id}.json').read_text(encoding='utf-8'))
    return StoredAgentSession(
        session_id=data['session_id'],
        model_config=dict(data['model_config']),
        runtime_config=dict(data['runtime_config']),
        system_prompt_parts=tuple(data['system_prompt_parts']),
        user_context=dict(data['user_context']),
        system_context=dict(data['system_context']),
        messages=tuple(
            message for message in data['messages'] if isinstance(message, dict)
        ),
        turns=int(data['turns']),
        tool_calls=int(data['tool_calls']),
    )


def serialize_model_config(model_config: ModelConfig) -> JSONDict:
    return {
        'model': model_config.model,
        'base_url': model_config.base_url,
        'api_key': model_config.api_key,
        'temperature': model_config.temperature,
        'timeout_seconds': model_config.timeout_seconds,
    }


def deserialize_model_config(payload: JSONDict) -> ModelConfig:
    return ModelConfig(
        model=str(payload['model']),
        base_url=str(payload.get('base_url', 'http://127.0.0.1:8000/v1')),
        api_key=str(payload.get('api_key', 'local-token')),
        temperature=float(payload.get('temperature', 0.0)),
        timeout_seconds=float(payload.get('timeout_seconds', 120.0)),
    )


def serialize_runtime_config(runtime_config: AgentRuntimeConfig) -> JSONDict:
    return {
        'cwd': str(runtime_config.cwd),
        'max_turns': runtime_config.max_turns,
        'command_timeout_seconds': runtime_config.command_timeout_seconds,
        'max_output_chars': runtime_config.max_output_chars,
        'permissions': {
            'allow_file_write': runtime_config.permissions.allow_file_write,
            'allow_shell_commands': runtime_config.permissions.allow_shell_commands,
            'allow_destructive_shell_commands': runtime_config.permissions.allow_destructive_shell_commands,
        },
        'additional_working_directories': [str(path) for path in runtime_config.additional_working_directories],
        'disable_claude_md_discovery': runtime_config.disable_claude_md_discovery,
        'session_directory': str(runtime_config.session_directory),
    }


def deserialize_runtime_config(payload: JSONDict) -> AgentRuntimeConfig:
    permissions_payload = payload.get('permissions')
    if not isinstance(permissions_payload, dict):
        permissions_payload = {}
    return AgentRuntimeConfig(
        cwd=Path(str(payload['cwd'])).resolve(),
        max_turns=int(payload.get('max_turns', 12)),
        command_timeout_seconds=float(payload.get('command_timeout_seconds', 30.0)),
        max_output_chars=int(payload.get('max_output_chars', 12000)),
        permissions=AgentPermissions(
            allow_file_write=bool(permissions_payload.get('allow_file_write', False)),
            allow_shell_commands=bool(permissions_payload.get('allow_shell_commands', False)),
            allow_destructive_shell_commands=bool(permissions_payload.get('allow_destructive_shell_commands', False)),
        ),
        additional_working_directories=tuple(
            Path(str(path)).resolve()
            for path in payload.get('additional_working_directories', [])
        ),
        disable_claude_md_discovery=bool(payload.get('disable_claude_md_discovery', False)),
        session_directory=Path(str(payload.get('session_directory', DEFAULT_AGENT_SESSION_DIR))).resolve(),
    )
