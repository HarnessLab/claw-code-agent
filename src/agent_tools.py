from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Union

from .agent_types import AgentPermissions, AgentRuntimeConfig, ToolExecutionResult

if TYPE_CHECKING:
    from .mcp_runtime import MCPRuntime
    from .plan_runtime import PlanRuntime
    from .task_runtime import TaskRuntime


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
    extra_env: dict[str, str] = field(default_factory=dict)
    mcp_runtime: 'MCPRuntime | None' = None
    plan_runtime: 'PlanRuntime | None' = None
    task_runtime: 'TaskRuntime | None' = None


ToolHandler = Callable[
    [dict[str, Any], ToolExecutionContext],
    Union[str, tuple[str, dict[str, Any]]],
]


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
            result = self.handler(arguments, context)
            if isinstance(result, tuple):
                content, metadata = result
            else:
                content, metadata = result, {}
            return ToolExecutionResult(name=self.name, ok=True, content=content, metadata=metadata)
        except ToolPermissionError as exc:
            return ToolExecutionResult(
                name=self.name,
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'permission_denied'},
            )
        except (ToolExecutionError, OSError, subprocess.SubprocessError) as exc:
            return ToolExecutionResult(
                name=self.name,
                ok=False,
                content=str(exc),
                metadata={'error_kind': 'tool_execution_error'},
            )


@dataclass(frozen=True)
class ToolStreamUpdate:
    kind: str
    content: str = ''
    stream: str | None = None
    result: ToolExecutionResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def build_tool_context(
    config: AgentRuntimeConfig,
    *,
    extra_env: dict[str, str] | None = None,
    mcp_runtime: 'MCPRuntime | None' = None,
    plan_runtime: 'PlanRuntime | None' = None,
    task_runtime: 'TaskRuntime | None' = None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        root=config.cwd.resolve(),
        command_timeout_seconds=config.command_timeout_seconds,
        max_output_chars=config.max_output_chars,
        permissions=config.permissions,
        extra_env=dict(extra_env or {}),
        mcp_runtime=mcp_runtime,
        plan_runtime=plan_runtime,
        task_runtime=task_runtime,
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


def execute_tool_streaming(
    tool_registry: dict[str, AgentTool],
    name: str,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> Iterator[ToolStreamUpdate]:
    tool = tool_registry.get(name)
    if tool is None:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(
                name=name,
                ok=False,
                content=f'Unknown tool: {name}',
            ),
        )
        return

    if name == 'bash':
        yield from _stream_bash(arguments, context)
        return

    result = tool.execute(arguments, context)
    if result.ok and result.content and name != 'delegate_agent':
        yield from _stream_static_text_result(result)
        return
    yield ToolStreamUpdate(kind='result', result=result)


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
        AgentTool(
            name='mcp_list_resources',
            description='List local MCP resources discovered from workspace MCP manifests.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_resources': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_mcp_list_resources,
        ),
        AgentTool(
            name='mcp_read_resource',
            description='Read a local MCP resource by URI from workspace MCP manifests.',
            parameters={
                'type': 'object',
                'properties': {
                    'uri': {'type': 'string'},
                    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 50000},
                },
                'required': ['uri'],
            },
            handler=_mcp_read_resource,
        ),
        AgentTool(
            name='plan_get',
            description='Show the current local runtime plan.',
            parameters={
                'type': 'object',
                'properties': {},
            },
            handler=_plan_get,
        ),
        AgentTool(
            name='update_plan',
            description='Replace the current local runtime plan with a structured multi-step plan and optionally sync it to tasks.',
            parameters={
                'type': 'object',
                'properties': {
                    'explanation': {'type': 'string'},
                    'sync_tasks': {'type': 'boolean'},
                    'items': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'step': {'type': 'string'},
                                'status': {'type': 'string'},
                                'task_id': {'type': 'string'},
                                'description': {'type': 'string'},
                                'priority': {'type': 'string'},
                            },
                            'required': ['step'],
                        },
                    },
                },
                'required': ['items'],
            },
            handler=_update_plan,
        ),
        AgentTool(
            name='plan_clear',
            description='Clear the current local runtime plan and optionally sync the task runtime.',
            parameters={
                'type': 'object',
                'properties': {
                    'sync_tasks': {'type': 'boolean'},
                },
            },
            handler=_plan_clear,
        ),
        AgentTool(
            name='task_list',
            description='List locally stored runtime tasks.',
            parameters={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string'},
                    'max_tasks': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_task_list,
        ),
        AgentTool(
            name='task_get',
            description='Show a locally stored runtime task by id.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_get,
        ),
        AgentTool(
            name='task_create',
            description='Create a locally stored runtime task.',
            parameters={
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'description': {'type': 'string'},
                    'status': {'type': 'string'},
                    'priority': {'type': 'string'},
                    'task_id': {'type': 'string'},
                },
                'required': ['title'],
            },
            handler=_task_create,
        ),
        AgentTool(
            name='task_update',
            description='Update a locally stored runtime task by id.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                    'title': {'type': 'string'},
                    'description': {'type': 'string'},
                    'status': {'type': 'string'},
                    'priority': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_update,
        ),
        AgentTool(
            name='todo_write',
            description='Replace the current local runtime task list with a structured todo list.',
            parameters={
                'type': 'object',
                'properties': {
                    'items': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'task_id': {'type': 'string'},
                                'title': {'type': 'string'},
                                'description': {'type': 'string'},
                                'status': {'type': 'string'},
                                'priority': {'type': 'string'},
                            },
                            'required': ['title'],
                        },
                    },
                },
                'required': ['items'],
            },
            handler=_todo_write,
        ),
        AgentTool(
            name='delegate_agent',
            description='Delegate a subtask to a nested Python coding agent and return its summary.',
            parameters={
                'type': 'object',
                'properties': {
                    'prompt': {'type': 'string'},
                    'subtasks': {
                        'type': 'array',
                        'items': {
                            'oneOf': [
                                {'type': 'string'},
                                {
                                    'type': 'object',
                                    'properties': {
                                        'prompt': {'type': 'string'},
                                        'label': {'type': 'string'},
                                        'max_turns': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                                        'resume_session_id': {'type': 'string'},
                                        'session_id': {'type': 'string'},
                                        'depends_on': {
                                            'type': 'array',
                                            'items': {'type': 'string'},
                                        },
                                    },
                                    'required': ['prompt'],
                                },
                            ]
                        },
                    },
                    'resume_session_id': {'type': 'string'},
                    'session_id': {'type': 'string'},
                    'max_turns': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'allow_write': {'type': 'boolean'},
                    'allow_shell': {'type': 'boolean'},
                    'include_parent_context': {'type': 'boolean'},
                    'continue_on_error': {'type': 'boolean'},
                    'max_failures': {'type': 'integer', 'minimum': 0, 'maximum': 20},
                    'strategy': {'type': 'string'},
                },
            },
            handler=_delegate_agent_placeholder,
        ),
    ]
    return {tool.name: tool for tool in tools}


def serialize_tool_result(result: ToolExecutionResult) -> str:
    payload = {
        'tool': result.name,
        'ok': result.ok,
        'content': result.content,
    }
    if result.metadata:
        payload['metadata'] = result.metadata
    return json.dumps(payload, ensure_ascii=True, indent=2)


def _truncate_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return f'{head}\n...[truncated]...\n{tail}'


def _snapshot_text(text: str, limit: int = 240) -> str:
    normalized = ' '.join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + '...'


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
    previous_text: str | None = None
    previous_sha256: str | None = None
    if target.exists() and target.is_file():
        previous_text = target.read_text(encoding='utf-8', errors='replace')
        previous_sha256 = hashlib.sha256(previous_text.encode('utf-8')).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    rel = target.relative_to(context.root)
    new_sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return (
        f'wrote {rel} ({len(content)} chars)',
        {
            'action': 'write_file',
            'path': str(rel),
            'before_exists': previous_text is not None,
            'before_sha256': previous_sha256,
            'before_size': len(previous_text) if previous_text is not None else 0,
            'before_preview': (
                _snapshot_text(previous_text)
                if previous_text is not None
                else None
            ),
            'after_sha256': new_sha256,
            'after_size': len(content),
            'after_preview': _snapshot_text(content),
            'content_length': len(content),
        },
    )


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
    before_sha256 = hashlib.sha256(current.encode('utf-8')).hexdigest()
    updated = current.replace(old_text, new_text) if replace_all else current.replace(old_text, new_text, 1)
    target.write_text(updated, encoding='utf-8')
    rel = target.relative_to(context.root)
    replaced = occurrences if replace_all else 1
    after_sha256 = hashlib.sha256(updated.encode('utf-8')).hexdigest()
    return (
        f'edited {rel}; replaced {replaced} occurrence(s)',
        {
            'action': 'edit_file',
            'path': str(rel),
            'before_sha256': before_sha256,
            'after_sha256': after_sha256,
            'before_size': len(current),
            'after_size': len(updated),
            'before_preview': _snapshot_text(current),
            'after_preview': _snapshot_text(updated),
            'old_text_preview': _snapshot_text(old_text),
            'new_text_preview': _snapshot_text(new_text),
            'replaced_occurrences': replaced,
        },
    )


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
        env=_build_subprocess_env(context),
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
    return (
        _truncate_output('\n'.join(payload).strip(), context.max_output_chars),
        {
            'action': 'bash',
            'command': command,
            'exit_code': completed.returncode,
            'stdout_preview': _snapshot_text(stdout),
            'stderr_preview': _snapshot_text(stderr),
            'output_preview': _snapshot_text('\n'.join(payload).strip()),
        },
    )


def _mcp_list_resources(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_mcp_runtime(context)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_resources = _coerce_int(arguments, 'max_resources', 50)
    resources = runtime.list_resources(query=query, limit=max_resources)
    if not resources:
        return '(no MCP resources)'
    lines: list[str] = []
    for resource in resources:
        details = [resource.uri, f'server={resource.server_name}']
        if resource.name:
            details.append(f'name={resource.name}')
        if resource.mime_type:
            details.append(f'mime={resource.mime_type}')
        if resource.resolved_path:
            details.append(f'path={resource.resolved_path}')
        lines.append(' ; '.join(details))
    return '\n'.join(lines)


def _mcp_read_resource(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_mcp_runtime(context)
    uri = _require_string(arguments, 'uri')
    max_chars = _coerce_int(arguments, 'max_chars', context.max_output_chars)
    try:
        content = runtime.read_resource(uri, max_chars=max_chars)
    except FileNotFoundError as exc:
        raise ToolExecutionError(str(exc)) from exc
    return content


def _task_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_task_runtime(context)
    status = arguments.get('status')
    if status is not None and not isinstance(status, str):
        raise ToolExecutionError('status must be a string')
    max_tasks = _coerce_int(arguments, 'max_tasks', 50)
    return runtime.render_tasks(status=status, limit=max_tasks)


def _task_get(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_task_runtime(context)
    return runtime.render_task(_require_string(arguments, 'task_id'))


def _plan_get(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    del arguments
    runtime = _require_plan_runtime(context)
    return runtime.render_plan()


def _update_plan(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_plan_runtime(context)
    items = arguments.get('items')
    if not isinstance(items, list):
        raise ToolExecutionError('items must be an array of plan step objects')
    explanation = arguments.get('explanation')
    if explanation is not None and not isinstance(explanation, str):
        raise ToolExecutionError('explanation must be a string')
    sync_tasks = arguments.get('sync_tasks', True)
    if not isinstance(sync_tasks, bool):
        raise ToolExecutionError('sync_tasks must be a boolean')
    mutation = runtime.update_plan(
        [item for item in items if isinstance(item, dict)],
        explanation=explanation,
        task_runtime=context.task_runtime,
        sync_tasks=sync_tasks,
    )
    return (
        f'updated plan with {mutation.after_count} step(s)',
        _plan_mutation_metadata(
            action='update_plan',
            mutation=mutation,
            total_steps=mutation.after_count,
            sync_tasks=sync_tasks,
        ),
    )


def _plan_clear(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_plan_runtime(context)
    sync_tasks = arguments.get('sync_tasks', True)
    if not isinstance(sync_tasks, bool):
        raise ToolExecutionError('sync_tasks must be a boolean')
    mutation = runtime.clear_plan(
        task_runtime=context.task_runtime if sync_tasks else None,
    )
    return (
        'cleared local plan',
        _plan_mutation_metadata(
            action='plan_clear',
            mutation=mutation,
            total_steps=0,
            sync_tasks=sync_tasks,
        ),
    )


def _task_create(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    title = _require_string(arguments, 'title')
    description = arguments.get('description')
    status = arguments.get('status', 'todo')
    priority = arguments.get('priority')
    task_id = arguments.get('task_id')
    if description is not None and not isinstance(description, str):
        raise ToolExecutionError('description must be a string')
    if status is not None and not isinstance(status, str):
        raise ToolExecutionError('status must be a string')
    if priority is not None and not isinstance(priority, str):
        raise ToolExecutionError('priority must be a string')
    if task_id is not None and not isinstance(task_id, str):
        raise ToolExecutionError('task_id must be a string')
    mutation = runtime.create_task(
        title=title,
        description=description,
        status=status or 'todo',
        priority=priority,
        task_id=task_id,
    )
    task = mutation.task
    assert task is not None
    return (
        f'created task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_create',
            mutation=mutation,
            task_id=task.task_id,
            task_status=task.status,
            total_tasks=mutation.after_count,
        ),
    )


def _task_update(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    title = arguments.get('title')
    description = arguments.get('description')
    status = arguments.get('status')
    priority = arguments.get('priority')
    for key, value in (
        ('title', title),
        ('description', description),
        ('status', status),
        ('priority', priority),
    ):
        if value is not None and not isinstance(value, str):
            raise ToolExecutionError(f'{key} must be a string')
    try:
        mutation = runtime.update_task(
            task_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
        )
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = mutation.task
    assert task is not None
    return (
        f'updated task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_update',
            mutation=mutation,
            task_id=task.task_id,
            task_status=task.status,
            total_tasks=mutation.after_count,
        ),
    )


def _todo_write(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    items = arguments.get('items')
    if not isinstance(items, list):
        raise ToolExecutionError('items must be an array of task objects')
    mutation = runtime.replace_tasks(
        [item for item in items if isinstance(item, dict)]
    )
    return (
        f'replaced todo list with {mutation.after_count} task(s)',
        _task_mutation_metadata(
            action='todo_write',
            mutation=mutation,
            total_tasks=mutation.after_count,
        ),
    )


def _stream_bash(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> Iterator[ToolStreamUpdate]:
    try:
        command = _require_string(arguments, 'command')
        _ensure_shell_allowed(command, context)
        process = subprocess.Popen(
            command,
            shell=True,
            executable='/bin/bash',
            cwd=context.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_build_subprocess_env(context),
        )
    except (ToolPermissionError, ToolExecutionError, OSError, subprocess.SubprocessError) as exc:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(name='bash', ok=False, content=str(exc)),
        )
        return

    selector = selectors.DefaultSelector()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, data='stdout')
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, data='stderr')

    deadline = time.monotonic() + context.command_timeout_seconds
    timeout_error: str | None = None

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timeout_error = (
                    f'Command timed out after {context.command_timeout_seconds:.1f}s: {command}'
                )
                process.kill()
                break
            events = selector.select(timeout=min(remaining, 0.1))
            if not events and process.poll() is not None:
                _drain_registered_streams(selector, stdout_chunks, stderr_chunks)
                break
            for key, _ in events:
                stream_name = str(key.data)
                line = key.fileobj.readline()
                if line == '':
                    try:
                        selector.unregister(key.fileobj)
                    except Exception:
                        pass
                    try:
                        key.fileobj.close()
                    except Exception:
                        pass
                    continue
                if stream_name == 'stdout':
                    stdout_chunks.append(line)
                else:
                    stderr_chunks.append(line)
                yield ToolStreamUpdate(
                    kind='delta',
                    content=line,
                    stream=stream_name,
                )
    finally:
        try:
            selector.close()
        except Exception:
            pass

    exit_code = process.wait()
    if timeout_error is not None:
        yield ToolStreamUpdate(
            kind='result',
            result=ToolExecutionResult(
                name='bash',
                ok=False,
                content=timeout_error,
                metadata={
                    'action': 'bash',
                    'command': command,
                    'exit_code': exit_code,
                    'timed_out': True,
                    'stdout_preview': _snapshot_text(''.join(stdout_chunks)),
                    'stderr_preview': _snapshot_text(''.join(stderr_chunks)),
                },
            ),
        )
        return

    stdout = ''.join(stdout_chunks)
    stderr = ''.join(stderr_chunks)
    payload = [
        f'exit_code={exit_code}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    yield ToolStreamUpdate(
        kind='result',
        result=ToolExecutionResult(
            name='bash',
            ok=True,
            content=_truncate_output('\n'.join(payload).strip(), context.max_output_chars),
            metadata={
                'action': 'bash',
                'command': command,
                'exit_code': exit_code,
                'streamed': True,
                'stdout_preview': _snapshot_text(stdout),
                'stderr_preview': _snapshot_text(stderr),
                'output_preview': _snapshot_text('\n'.join(payload).strip()),
            },
        ),
    )


def _delegate_agent_placeholder(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    raise ToolExecutionError(
        'delegate_agent must be handled by the runtime and is not available as a standalone tool handler'
    )


def _require_mcp_runtime(context: ToolExecutionContext):
    if context.mcp_runtime is None or not context.mcp_runtime.resources:
        raise ToolExecutionError(
            'No local MCP resources are available. Add a .claw-mcp.json or .mcp.json manifest first.'
        )
    return context.mcp_runtime


def _require_plan_runtime(context: ToolExecutionContext):
    if context.plan_runtime is None:
        raise ToolExecutionError('Local plan runtime is not available.')
    return context.plan_runtime


def _require_task_runtime(context: ToolExecutionContext):
    if context.task_runtime is None:
        raise ToolExecutionError('Local task runtime is not available.')
    return context.task_runtime


def _task_mutation_metadata(
    *,
    action: str,
    mutation,
    task_id: str | None = None,
    task_status: str | None = None,
    total_tasks: int,
) -> dict[str, Any]:
    try:
        relative_path = str(Path(mutation.store_path).relative_to(Path.cwd()))
    except ValueError:
        relative_path = str(mutation.store_path)
    payload: dict[str, Any] = {
        'action': action,
        'path': relative_path,
        'before_sha256': mutation.before_sha256,
        'after_sha256': mutation.after_sha256,
        'before_preview': mutation.before_preview,
        'after_preview': mutation.after_preview,
        'before_task_count': mutation.before_count,
        'after_task_count': mutation.after_count,
        'total_tasks': total_tasks,
    }
    if task_id is not None:
        payload['task_id'] = task_id
    if task_status is not None:
        payload['task_status'] = task_status
    return payload


def _plan_mutation_metadata(
    *,
    action: str,
    mutation,
    total_steps: int,
    sync_tasks: bool,
) -> dict[str, Any]:
    try:
        relative_path = str(Path(mutation.store_path).relative_to(Path.cwd()))
    except ValueError:
        relative_path = str(mutation.store_path)
    payload: dict[str, Any] = {
        'action': action,
        'path': relative_path,
        'before_sha256': mutation.before_sha256,
        'after_sha256': mutation.after_sha256,
        'before_preview': mutation.before_preview,
        'after_preview': mutation.after_preview,
        'before_plan_count': mutation.before_count,
        'after_plan_count': mutation.after_count,
        'total_steps': total_steps,
        'sync_tasks': sync_tasks,
    }
    if mutation.explanation is not None:
        payload['explanation'] = mutation.explanation
    if mutation.synced_task_store_path is not None:
        try:
            payload['synced_task_store_path'] = str(
                Path(mutation.synced_task_store_path).relative_to(Path.cwd())
            )
        except ValueError:
            payload['synced_task_store_path'] = mutation.synced_task_store_path
    if mutation.synced_task_sha256 is not None:
        payload['synced_task_sha256'] = mutation.synced_task_sha256
    if mutation.synced_tasks:
        payload['synced_tasks'] = mutation.synced_tasks
    return payload


def _drain_registered_streams(
    selector: selectors.BaseSelector,
    stdout_chunks: list[str],
    stderr_chunks: list[str],
) -> None:
    for key in list(selector.get_map().values()):
        try:
            remainder = key.fileobj.read()
        except Exception:
            remainder = ''
        if not remainder:
            try:
                selector.unregister(key.fileobj)
            except Exception:
                pass
            try:
                key.fileobj.close()
            except Exception:
                pass
            continue
        if key.data == 'stdout':
            stdout_chunks.append(remainder)
        else:
            stderr_chunks.append(remainder)
        try:
            selector.unregister(key.fileobj)
        except Exception:
            pass
        try:
            key.fileobj.close()
        except Exception:
            pass


def _build_subprocess_env(context: ToolExecutionContext) -> dict[str, str]:
    env = os.environ.copy()
    env.update(context.extra_env)
    return env


def _stream_static_text_result(
    result: ToolExecutionResult,
    *,
    chunk_size: int = 400,
) -> Iterator[ToolStreamUpdate]:
    content = result.content
    if content:
        for start in range(0, len(content), chunk_size):
            yield ToolStreamUpdate(
                kind='delta',
                content=content[start:start + chunk_size],
                stream='tool',
            )
    metadata = dict(result.metadata)
    metadata.setdefault('streamed', True)
    yield ToolStreamUpdate(
        kind='result',
        result=ToolExecutionResult(
            name=result.name,
            ok=result.ok,
            content=result.content,
            metadata=metadata,
        ),
    )
