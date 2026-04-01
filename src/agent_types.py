from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str = 'http://127.0.0.1:8000/v1'
    api_key: str = 'local-token'
    temperature: float = 0.0
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: JSONDict


@dataclass(frozen=True)
class AssistantTurn:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    raw_message: JSONDict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentPermissions:
    allow_file_write: bool = False
    allow_shell_commands: bool = False
    allow_destructive_shell_commands: bool = False


@dataclass(frozen=True)
class AgentRuntimeConfig:
    cwd: Path
    max_turns: int = 12
    command_timeout_seconds: float = 30.0
    max_output_chars: int = 12000
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    additional_working_directories: tuple[Path, ...] = ()
    disable_claude_md_discovery: bool = False
    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    ok: bool
    content: str


@dataclass(frozen=True)
class AgentRunResult:
    final_output: str
    turns: int
    tool_calls: int
    transcript: tuple[JSONDict, ...]
    session_id: str | None = None
    session_path: str | None = None
