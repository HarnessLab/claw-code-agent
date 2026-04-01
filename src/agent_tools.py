from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent_types import AgentPermissions, AgentRuntimeConfig, ToolExecutionResult


class ToolPermissionError(RuntimeError):
    """Raised when the runtime configuration does not allow a tool action."""


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot complete because of invalid input or state."""


@dataclass(frozen=True)
class ToolExecutionContext:
    root: Path
    command_timeout_seconds: float
    max_output_chars: int
    permissions: AgentPermissions


ToolHandler = Callable[[dict[str, Any], ToolExecutionContext], str]


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai_tool(self) -> dict[str, object]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': self.parameters,
            },
        }

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        try:
            content = self.handler(arguments, context)
            return ToolExecutionResult(name=self.name, ok=True, content=content)
        except (ToolPermissionError, ToolExecutionError, OSError, subprocess.SubprocessError) as exc:
            return ToolExecutionResult(name=self.name, ok=False, content=str(exc))


def build_tool_context(config: AgentRuntimeConfig) -> ToolExecutionContext:
    return ToolExecutionContext(
        root=config.cwd.resolve(),
        command_timeout_seconds=config.command_timeout_seconds,
        max_output_chars=config.max_output_chars,
        permissions=config.permissions,
    )


def execute_tool(
    tool_registry: dict[str, AgentTool],
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    tool = tool_registry.get(name)
    if tool is None:
        return ToolExecutionResult(
            name=name,
            ok=False,
            content=f'Unknown tool: {name}',
        )
    return tool.execute(arguments, context)


def default_tool_registry() -> dict[str, AgentTool]:
    tools = [
        AgentTool(
            name='list_dir',
            description='List files and directories under a workspace path.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Relative path from workspace root.'},
                    'max_entries': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                },
            },
            handler=_list_dir,
        ),
        AgentTool(
            name='read_file',
            description='Read the contents of a UTF-8 text file inside the workspace.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Relative file path from workspace root.'},
                    'start_line': {'type': 'integer', 'minimum': 1},
                    'end_line': {'type': 'integer', 'minimum': 1},
                },
                'required': ['path'],
            },
            handler=_read_file,
        ),
        AgentTool(
            name='write_file',
            description='Write a complete file inside the workspace. Creates parent directories when needed.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string'},
                },
                'required': ['path', 'content'],
            },
            handler=_write_file,
        ),
        AgentTool(
            name='edit_file',
            description='Replace text inside a workspace file using exact string matching.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'old_text': {'type': 'string'},
                    'new_text': {'type': 'string'},
                    'replace_all': {'type': 'boolean'},
                },
                'required': ['path', 'old_text', 'new_text'],
            },
            handler=_edit_file,
        ),
        AgentTool(
            name='glob_search',
            description='Find files matching a glob pattern inside the workspace.',
            parameters={
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string'},
                },
                'required': ['pattern'],
            },
            handler=_glob_search,
        ),
        AgentTool(
            name='grep_search',
            description='Search for a string or regular expression inside workspace files.',
            parameters={
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string'},
                    'path': {'type': 'string'},
                    'literal': {'type': 'boolean'},
                    'max_matches': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                },
                'required': ['pattern'],
            },
            handler=_grep_search,
        ),
        AgentTool(
            name='bash',
            description='Run a shell command in the workspace. Use sparingly and prefer dedicated file tools for edits.',
            parameters={
                'type': 'object',
                'properties': {
                    'command': {'type': 'string'},
                },
                'required': ['command'],
            },
            handler=_run_bash,
        ),
    ]
    return {tool.name: tool for tool in tools}


def serialize_tool_result(result: ToolExecutionResult) -> str:
    payload = {
        'tool': result.name,
        'ok': result.ok,
        'content': result.content,
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def _truncate_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return f'{head}\n...[truncated]...\n{tail}'


def _require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ToolExecutionError(f'{key} must be a non-empty string')
    return value


def _coerce_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolExecutionError(f'{key} must be an integer')
    return value


def _resolve_path(raw_path: str, context: ToolExecutionContext, *, allow_missing: bool = True) -> Path:
    expanded = Path(raw_path).expanduser()
    candidate = expanded if expanded.is_absolute() else context.root / expanded
    resolved = candidate.resolve(strict=not allow_missing)
    try:
        resolved.relative_to(context.root)
    except ValueError as exc:
        raise ToolExecutionError(
            f'Path {raw_path!r} escapes the workspace root {context.root}'
        ) from exc
    return resolved


def _ensure_write_allowed(context: ToolExecutionContext) -> None:
    if not context.permissions.allow_file_write:
        raise ToolPermissionError(
            'File write tools are disabled. Re-run with --allow-write to enable edits.'
        )


def _ensure_shell_allowed(command: str, context: ToolExecutionContext) -> None:
    if not context.permissions.allow_shell_commands:
        raise ToolPermissionError(
            'Shell commands are disabled. Re-run with --allow-shell to enable bash.'
        )
    if context.permissions.allow_destructive_shell_commands:
        return
    destructive_patterns = [
        r'(^|[;&|])\s*rm\s',
        r'(^|[;&|])\s*mv\s',
        r'(^|[;&|])\s*dd\s',
        r'(^|[;&|])\s*shutdown\s',
        r'(^|[;&|])\s*reboot\s',
        r'(^|[;&|])\s*mkfs',
        r'(^|[;&|])\s*chmod\s+-R\s+777',
        r'(^|[;&|])\s*chown\s+-R',
        r'(^|[;&|])\s*git\s+reset\s+--hard',
        r'(^|[;&|])\s*git\s+clean\s+-fd',
        r'(^|[;&|])\s*:\s*>\s*',
    ]
    lowered = command.lower()
    if any(re.search(pattern, lowered) for pattern in destructive_patterns):
        raise ToolPermissionError(
            'Potentially destructive shell command blocked. Re-run with --unsafe to allow it.'
        )


def _list_dir(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    raw_path = arguments.get('path', '.')
    if not isinstance(raw_path, str):
        raise ToolExecutionError('path must be a string')
    max_entries = _coerce_int(arguments, 'max_entries', 200)
    target = _resolve_path(raw_path, context)
    if not target.exists():
        raise ToolExecutionError(f'Path not found: {raw_path}')
    if not target.is_dir():
        raise ToolExecutionError(f'Path is not a directory: {raw_path}')
    entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    lines: list[str] = []
    for entry in entries[:max_entries]:
        kind = 'dir' if entry.is_dir() else 'file'
        rel = entry.relative_to(context.root)
        lines.append(f'{kind}\t{rel}')
    if len(entries) > max_entries:
        lines.append(f'... truncated at {max_entries} entries ...')
    return '\n'.join(lines) if lines else '(empty directory)'


def _read_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    target = _resolve_path(_require_string(arguments, 'path'), context, allow_missing=False)
    if not target.is_file():
        raise ToolExecutionError(f'Path is not a file: {target}')
    text = target.read_text(encoding='utf-8', errors='replace')
    start_line = arguments.get('start_line')
    end_line = arguments.get('end_line')
    if start_line is None and end_line is None:
        return _truncate_output(text, context.max_output_chars)
    if start_line is not None and (isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1):
        raise ToolExecutionError('start_line must be an integer >= 1')
    if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < 1):
        raise ToolExecutionError('end_line must be an integer >= 1')
    lines = text.splitlines()
    start_idx = max((start_line or 1) - 1, 0)
    end_idx = end_line or len(lines)
    selected = lines[start_idx:end_idx]
    rendered = '\n'.join(f'{start_idx + idx + 1}: {line}' for idx, line in enumerate(selected))
    return _truncate_output(rendered, context.max_output_chars)


def _write_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    target = _resolve_path(_require_string(arguments, 'path'), context)
    content = arguments.get('content')
    if not isinstance(content, str):
        raise ToolExecutionError('content must be a string')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    rel = target.relative_to(context.root)
    return f'wrote {rel} ({len(content)} chars)'


def _edit_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    target = _resolve_path(_require_string(arguments, 'path'), context, allow_missing=False)
    if not target.is_file():
        raise ToolExecutionError(f'Path is not a file: {target}')
    old_text = arguments.get('old_text')
    new_text = arguments.get('new_text')
    replace_all = arguments.get('replace_all', False)
    if not isinstance(old_text, str):
        raise ToolExecutionError('old_text must be a string')
    if not isinstance(new_text, str):
        raise ToolExecutionError('new_text must be a string')
    if not isinstance(replace_all, bool):
        raise ToolExecutionError('replace_all must be a boolean')
    current = target.read_text(encoding='utf-8', errors='replace')
    occurrences = current.count(old_text)
    if occurrences == 0:
        raise ToolExecutionError('old_text was not found in the target file')
    if occurrences > 1 and not replace_all:
        raise ToolExecutionError(
            f'old_text matched {occurrences} times; pass replace_all=true to replace every match'
        )
    updated = current.replace(old_text, new_text) if replace_all else current.replace(old_text, new_text, 1)
    target.write_text(updated, encoding='utf-8')
    rel = target.relative_to(context.root)
    replaced = occurrences if replace_all else 1
    return f'edited {rel}; replaced {replaced} occurrence(s)'


def _glob_search(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    pattern = _require_string(arguments, 'pattern')
    matches = sorted(context.root.glob(pattern))
    if not matches:
        return '(no matches)'
    rendered = [str(path.relative_to(context.root)) for path in matches]
    return _truncate_output('\n'.join(rendered), context.max_output_chars)


def _grep_search(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    pattern = _require_string(arguments, 'pattern')
    raw_path = arguments.get('path', '.')
    if not isinstance(raw_path, str):
        raise ToolExecutionError('path must be a string')
    literal = arguments.get('literal', False)
    if not isinstance(literal, bool):
        raise ToolExecutionError('literal must be a boolean')
    max_matches = _coerce_int(arguments, 'max_matches', 100)
    root = _resolve_path(raw_path, context)
    if not root.exists():
        raise ToolExecutionError(f'Path not found: {raw_path}')
    regex = re.compile(re.escape(pattern) if literal else pattern)
    hits: list[str] = []
    file_iter = root.rglob('*') if root.is_dir() else [root]
    for file_path in file_iter:
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = file_path.relative_to(context.root)
                hits.append(f'{rel}:{line_no}: {line}')
                if len(hits) >= max_matches:
                    return '\n'.join(hits + [f'... truncated at {max_matches} matches ...'])
    return '\n'.join(hits) if hits else '(no matches)'


def _run_bash(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    command = _require_string(arguments, 'command')
    _ensure_shell_allowed(command, context)
    completed = subprocess.run(
        command,
        shell=True,
        executable='/bin/bash',
        cwd=context.root,
        capture_output=True,
        text=True,
        timeout=context.command_timeout_seconds,
    )
    stdout = completed.stdout or ''
    stderr = completed.stderr or ''
    payload = [
        f'exit_code={completed.returncode}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    return _truncate_output('\n'.join(payload).strip(), context.max_output_chars)
