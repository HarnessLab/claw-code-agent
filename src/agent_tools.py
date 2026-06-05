from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Union

from .agent_types import AgentPermissions, AgentRuntimeConfig, ToolExecutionResult
from .session_env_vars import get_session_env_vars

if TYPE_CHECKING:
    from .account_runtime import AccountRuntime
    from .ask_user_runtime import AskUserRuntime
    from .config_runtime import ConfigRuntime
    from .lsp_runtime import LSPRuntime
    from .mcp_runtime import MCPRuntime
    from .plan_runtime import PlanRuntime
    from .remote_runtime import RemoteRuntime
    from .remote_trigger_runtime import RemoteTriggerRuntime
    from .search_runtime import SearchRuntime
    from .task_runtime import TaskRuntime
    from .team_runtime import TeamRuntime
    from .workflow_runtime import WorkflowRuntime
    from .worktree_runtime import WorktreeRuntime


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
    additional_roots: tuple[Path, ...] = ()
    tool_registry: dict[str, 'AgentTool'] | None = None
    search_runtime: 'SearchRuntime | None' = None
    account_runtime: 'AccountRuntime | None' = None
    ask_user_runtime: 'AskUserRuntime | None' = None
    config_runtime: 'ConfigRuntime | None' = None
    lsp_runtime: 'LSPRuntime | None' = None
    mcp_runtime: 'MCPRuntime | None' = None
    remote_runtime: 'RemoteRuntime | None' = None
    remote_trigger_runtime: 'RemoteTriggerRuntime | None' = None
    plan_runtime: 'PlanRuntime | None' = None
    task_runtime: 'TaskRuntime | None' = None
    team_runtime: 'TeamRuntime | None' = None
    workflow_runtime: 'WorkflowRuntime | None' = None
    worktree_runtime: 'WorktreeRuntime | None' = None
    # Per-session counters and tagging for anti-loop / anti-self-confirm
    # heuristics. Mutable contents inside the frozen dataclass — fine,
    # only the field references are frozen, not what they point to.
    edit_history: dict[str, int] = field(default_factory=dict)
    self_authored_paths: set[str] = field(default_factory=set)


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
    tool_registry: dict[str, AgentTool] | None = None,
    search_runtime: 'SearchRuntime | None' = None,
    account_runtime: 'AccountRuntime | None' = None,
    ask_user_runtime: 'AskUserRuntime | None' = None,
    config_runtime: 'ConfigRuntime | None' = None,
    lsp_runtime: 'LSPRuntime | None' = None,
    mcp_runtime: 'MCPRuntime | None' = None,
    remote_runtime: 'RemoteRuntime | None' = None,
    remote_trigger_runtime: 'RemoteTriggerRuntime | None' = None,
    plan_runtime: 'PlanRuntime | None' = None,
    task_runtime: 'TaskRuntime | None' = None,
    team_runtime: 'TeamRuntime | None' = None,
    workflow_runtime: 'WorkflowRuntime | None' = None,
    worktree_runtime: 'WorktreeRuntime | None' = None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        root=config.cwd.resolve(),
        command_timeout_seconds=config.command_timeout_seconds,
        max_output_chars=config.max_output_chars,
        permissions=config.permissions,
        extra_env=dict(extra_env or {}),
        additional_roots=tuple(
            path.resolve() for path in config.additional_working_directories
        ),
        tool_registry=tool_registry,
        search_runtime=search_runtime,
        account_runtime=account_runtime,
        ask_user_runtime=ask_user_runtime,
        config_runtime=config_runtime,
        lsp_runtime=lsp_runtime,
        mcp_runtime=mcp_runtime,
        remote_runtime=remote_runtime,
        remote_trigger_runtime=remote_trigger_runtime,
        plan_runtime=plan_runtime,
        task_runtime=task_runtime,
        team_runtime=team_runtime,
        workflow_runtime=workflow_runtime,
        worktree_runtime=worktree_runtime,
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
            name='notebook_edit',
            description='Edit a Jupyter notebook cell by replacing or appending source in a .ipynb file.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'cell_index': {'type': 'integer', 'minimum': 0},
                    'source': {'type': 'string'},
                    'cell_type': {'type': 'string'},
                    'create_cell': {'type': 'boolean'},
                },
                'required': ['path', 'cell_index', 'source'],
            },
            handler=_notebook_edit,
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
            name='LSP',
            description='Use local LSP-style code intelligence for definitions, references, hover, symbols, and call hierarchy.',
            parameters={
                'type': 'object',
                'properties': {
                    'operation': {
                        'type': 'string',
                        'enum': [
                            'goToDefinition',
                            'findReferences',
                            'hover',
                            'documentSymbol',
                            'workspaceSymbol',
                            'goToImplementation',
                            'prepareCallHierarchy',
                            'incomingCalls',
                            'outgoingCalls',
                        ],
                    },
                    'file_path': {'type': 'string'},
                    'line': {'type': 'integer', 'minimum': 1},
                    'character': {'type': 'integer', 'minimum': 1},
                    'query': {'type': 'string'},
                    'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
                'required': ['operation', 'file_path', 'line', 'character'],
            },
            handler=_lsp_query,
        ),
        AgentTool(
            name='web_fetch',
            description='Fetch a text resource from http, https, or file URLs and return a truncated text response.',
            parameters={
                'type': 'object',
                'properties': {
                    'url': {'type': 'string'},
                    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
                },
                'required': ['url'],
            },
            handler=_web_fetch,
        ),
        AgentTool(
            name='search_status',
            description='Show the local search runtime summary or a specific configured search provider.',
            parameters={
                'type': 'object',
                'properties': {
                    'provider': {'type': 'string'},
                },
            },
            handler=_search_status,
        ),
        AgentTool(
            name='search_list_providers',
            description='List configured local search providers from workspace search manifests and environment configuration.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_providers': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_search_list_providers,
        ),
        AgentTool(
            name='search_activate_provider',
            description='Set the active local search provider for the current workspace.',
            parameters={
                'type': 'object',
                'properties': {
                    'provider': {'type': 'string'},
                },
                'required': ['provider'],
            },
            handler=_search_activate_provider,
        ),
        AgentTool(
            name='web_search',
            description='Run a real web search against a configured search backend and return ranked results.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'provider': {'type': 'string'},
                    'domains': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                },
                'required': ['query'],
            },
            handler=_web_search,
        ),
        AgentTool(
            name='tool_search',
            description='Search the active tool registry by tool name or description.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                },
                'required': ['query'],
            },
            handler=_tool_search,
        ),
        AgentTool(
            name='recall_memory',
            description=(
                'Search Latti\'s persistent memory (scars, SOPs, lessons, decisions, '
                'references at ~/.latti/memory/) by keyword. Use this BEFORE making a '
                'decision that might match a prior correction or SOP — anchored '
                'history is in your context window, but the typed memory store is not.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'query': {
                        'type': 'string',
                        'description': 'Keywords to match against memory body text. Tokens shorter than 3 chars are dropped.',
                    },
                    'kind': {
                        'type': 'string',
                        'enum': ['scar', 'sop', 'lesson', 'decision', 'reference'],
                        'description': 'Filter to a specific memory kind. Omit for all kinds.',
                    },
                    'limit': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': 20,
                        'description': 'Max results (default 5).',
                    },
                },
                'required': ['query'],
            },
            handler=_recall_memory,
        ),
        AgentTool(
            name='sleep',
            description='Pause execution briefly for bounded local wait flows.',
            parameters={
                'type': 'object',
                'properties': {
                    'seconds': {'type': 'number', 'minimum': 0.0, 'maximum': 5.0},
                },
                'required': ['seconds'],
            },
            handler=_sleep,
        ),
        AgentTool(
            name='ask_user_question',
            description='Request an answer from the local ask-user runtime using queued or interactive answers.',
            parameters={
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'},
                    'header': {'type': 'string'},
                    'question_id': {'type': 'string'},
                    'choices': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'allow_free_text': {'type': 'boolean'},
                },
                'required': ['question'],
            },
            handler=_ask_user_question,
        ),
        AgentTool(
            name='account_status',
            description='Show local account runtime summary or a specific configured account profile.',
            parameters={
                'type': 'object',
                'properties': {
                    'profile': {'type': 'string'},
                },
            },
            handler=_account_status,
        ),
        AgentTool(
            name='account_list_profiles',
            description='List configured local account profiles from workspace account manifests.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_profiles': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_account_list_profiles,
        ),
        AgentTool(
            name='account_login',
            description='Activate a local account profile or ephemeral account identity and persist it as the active account session.',
            parameters={
                'type': 'object',
                'properties': {
                    'target': {'type': 'string'},
                    'provider': {'type': 'string'},
                    'auth_mode': {'type': 'string'},
                },
                'required': ['target'],
            },
            handler=_account_login,
        ),
        AgentTool(
            name='account_logout',
            description='Clear the active local account session state.',
            parameters={
                'type': 'object',
                'properties': {
                    'reason': {'type': 'string'},
                },
            },
            handler=_account_logout,
        ),
        AgentTool(
            name='config_list',
            description='List merged or source-specific workspace config keys from local settings files.',
            parameters={
                'type': 'object',
                'properties': {
                    'source': {'type': 'string'},
                    'prefix': {'type': 'string'},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                },
            },
            handler=_config_list,
        ),
        AgentTool(
            name='config_get',
            description='Read a merged or source-specific workspace config value by dotted key path.',
            parameters={
                'type': 'object',
                'properties': {
                    'key_path': {'type': 'string'},
                    'source': {'type': 'string'},
                },
                'required': ['key_path'],
            },
            handler=_config_get,
        ),
        AgentTool(
            name='config_set',
            description='Write a workspace config value by dotted key path into a chosen config source.',
            parameters={
                'type': 'object',
                'properties': {
                    'key_path': {'type': 'string'},
                    'source': {'type': 'string'},
                    'value': {
                        'oneOf': [
                            {'type': 'string'},
                            {'type': 'number'},
                            {'type': 'integer'},
                            {'type': 'boolean'},
                            {'type': 'array', 'items': {}},
                            {'type': 'object'},
                            {'type': 'null'},
                        ]
                    },
                },
                'required': ['key_path', 'value'],
            },
            handler=_config_set,
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
            name='mcp_list_tools',
            description='List MCP tools exposed by configured MCP servers over real transport.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'server': {'type': 'string'},
                    'max_tools': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_mcp_list_tools,
        ),
        AgentTool(
            name='mcp_call_tool',
            description='Call an MCP tool exposed by a configured MCP server over real transport.',
            parameters={
                'type': 'object',
                'properties': {
                    'tool_name': {'type': 'string'},
                    'server': {'type': 'string'},
                    'arguments': {'type': 'object'},
                    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 50000},
                },
                'required': ['tool_name'],
            },
            handler=_mcp_call_tool,
        ),
        AgentTool(
            name='remote_status',
            description='Show the local remote runtime summary or a specific configured remote profile.',
            parameters={
                'type': 'object',
                'properties': {
                    'profile': {'type': 'string'},
                },
            },
            handler=_remote_status,
        ),
        AgentTool(
            name='remote_list_profiles',
            description='List configured local remote profiles from workspace remote manifests.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'mode': {'type': 'string'},
                    'max_profiles': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_remote_list_profiles,
        ),
        AgentTool(
            name='remote_connect',
            description='Activate a local remote target or configured remote profile and persist it as the active connection.',
            parameters={
                'type': 'object',
                'properties': {
                    'target': {'type': 'string'},
                    'mode': {'type': 'string'},
                },
                'required': ['target'],
            },
            handler=_remote_connect,
        ),
        AgentTool(
            name='remote_disconnect',
            description='Clear the active local remote connection state.',
            parameters={
                'type': 'object',
                'properties': {
                    'reason': {'type': 'string'},
                },
            },
            handler=_remote_disconnect,
        ),
        AgentTool(
            name='worktree_status',
            description='Show the current managed git worktree session status.',
            parameters={
                'type': 'object',
                'properties': {},
            },
            handler=_worktree_status,
        ),
        AgentTool(
            name='worktree_enter',
            description='Create an isolated git worktree and switch the current agent session into it.',
            parameters={
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                },
            },
            handler=_worktree_enter,
        ),
        AgentTool(
            name='worktree_exit',
            description='Leave the active managed worktree session and optionally remove the worktree.',
            parameters={
                'type': 'object',
                'properties': {
                    'action': {'type': 'string'},
                    'discard_changes': {'type': 'boolean'},
                },
            },
            handler=_worktree_exit,
        ),
        AgentTool(
            name='workflow_list',
            description='List local workflow definitions discovered from workspace workflow manifests.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_workflows': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_workflow_list,
        ),
        AgentTool(
            name='workflow_get',
            description='Show one local workflow definition by name.',
            parameters={
                'type': 'object',
                'properties': {
                    'workflow_name': {'type': 'string'},
                },
                'required': ['workflow_name'],
            },
            handler=_workflow_get,
        ),
        AgentTool(
            name='workflow_run',
            description='Record and render a local workflow execution request from a workflow manifest.',
            parameters={
                'type': 'object',
                'properties': {
                    'workflow_name': {'type': 'string'},
                    'arguments': {'type': 'object'},
                },
                'required': ['workflow_name'],
            },
            handler=_workflow_run,
        ),
        AgentTool(
            name='remote_trigger',
            description='List, inspect, create, update, or run local remote triggers similar to the npm remote trigger tool.',
            parameters={
                'type': 'object',
                'properties': {
                    'action': {'type': 'string'},
                    'trigger_id': {'type': 'string'},
                    'body': {'type': 'object'},
                    'query': {'type': 'string'},
                    'max_triggers': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
                'required': ['action'],
            },
            handler=_remote_trigger,
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
                                'active_form': {'type': 'string'},
                                'owner': {'type': 'string'},
                                'depends_on': {
                                    'type': 'array',
                                    'items': {'type': 'string'},
                                },
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
            name='task_next',
            description='Show the next actionable tasks from the local runtime task list.',
            parameters={
                'type': 'object',
                'properties': {
                    'max_tasks': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                },
            },
            handler=_task_next,
        ),
        AgentTool(
            name='team_list',
            description='List locally configured collaboration teams.',
            parameters={
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'max_teams': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_team_list,
        ),
        AgentTool(
            name='team_get',
            description='Show a locally configured collaboration team by name.',
            parameters={
                'type': 'object',
                'properties': {
                    'team_name': {'type': 'string'},
                },
                'required': ['team_name'],
            },
            handler=_team_get,
        ),
        AgentTool(
            name='team_create',
            description='Create a locally stored collaboration team.',
            parameters={
                'type': 'object',
                'properties': {
                    'team_name': {'type': 'string'},
                    'description': {'type': 'string'},
                    'members': {'type': 'array', 'items': {'type': 'string'}},
                    'metadata': {'type': 'object'},
                },
                'required': ['team_name'],
            },
            handler=_team_create,
        ),
        AgentTool(
            name='team_delete',
            description='Delete a locally stored collaboration team and its recorded messages.',
            parameters={
                'type': 'object',
                'properties': {
                    'team_name': {'type': 'string'},
                },
                'required': ['team_name'],
            },
            handler=_team_delete,
        ),
        AgentTool(
            name='send_message',
            description='Send a local collaboration message to a team or teammate and persist it in the team runtime.',
            parameters={
                'type': 'object',
                'properties': {
                    'team_name': {'type': 'string'},
                    'message': {'type': 'string'},
                    'sender': {'type': 'string'},
                    'recipient': {'type': 'string'},
                    'metadata': {'type': 'object'},
                },
                'required': ['team_name', 'message'],
            },
            handler=_send_message,
        ),
        AgentTool(
            name='team_messages',
            description='Show locally recorded collaboration messages for all teams or one team.',
            parameters={
                'type': 'object',
                'properties': {
                    'team_name': {'type': 'string'},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                },
            },
            handler=_team_messages,
        ),
        AgentTool(
            name='task_list',
            description='List locally stored runtime tasks.',
            parameters={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string'},
                    'owner': {'type': 'string'},
                    'actionable_only': {'type': 'boolean'},
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
                    'active_form': {'type': 'string'},
                    'owner': {'type': 'string'},
                    'blocks': {'type': 'array', 'items': {'type': 'string'}},
                    'blocked_by': {'type': 'array', 'items': {'type': 'string'}},
                    'metadata': {'type': 'object'},
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
                    'active_form': {'type': 'string'},
                    'owner': {'type': 'string'},
                    'blocks': {'type': 'array', 'items': {'type': 'string'}},
                    'blocked_by': {'type': 'array', 'items': {'type': 'string'}},
                    'metadata': {'type': 'object'},
                },
                'required': ['task_id'],
            },
            handler=_task_update,
        ),
        AgentTool(
            name='task_start',
            description='Mark a task as in progress, or blocked if dependencies are unresolved.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                    'owner': {'type': 'string'},
                    'active_form': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_start,
        ),
        AgentTool(
            name='task_complete',
            description='Mark a task as completed and release blocked dependents when possible.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_complete,
        ),
        AgentTool(
            name='task_block',
            description='Mark a task as blocked with optional dependencies and reason.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                    'blocked_by': {'type': 'array', 'items': {'type': 'string'}},
                    'reason': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_block,
        ),
        AgentTool(
            name='task_cancel',
            description='Mark a task as cancelled with an optional reason.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {'type': 'string'},
                    'reason': {'type': 'string'},
                },
                'required': ['task_id'],
            },
            handler=_task_cancel,
        ),
        AgentTool(
            name='EnterPlanMode',
            description=(
                'Enter plan mode. In plan mode, focus on exploring the codebase '
                'and creating a plan rather than making changes. Use read-only '
                'tools (Read, Grep, Glob) to investigate, then create a plan.'
            ),
            parameters={
                'type': 'object',
                'properties': {},
            },
            handler=_enter_plan_mode,
        ),
        AgentTool(
            name='ExitPlanMode',
            description=(
                'Exit plan mode and return to normal execution mode. '
                'Call this after you have finished exploring and have a plan ready.'
            ),
            parameters={
                'type': 'object',
                'properties': {},
            },
            handler=_exit_plan_mode,
        ),
        AgentTool(
            name='TaskOutput',
            description=(
                'Get the output of a background task by its ID. '
                'Use block=true (default) to wait for completion, '
                'or block=false to check current status without waiting.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {
                        'type': 'string',
                        'description': 'The task ID to get output from.',
                    },
                    'block': {
                        'type': 'boolean',
                        'description': 'Whether to wait for completion (default true).',
                    },
                    'timeout': {
                        'type': 'number',
                        'description': 'Max wait time in ms (0-600000, default 30000).',
                    },
                },
                'required': ['task_id'],
            },
            handler=_task_output,
        ),
        AgentTool(
            name='TaskStop',
            description='Stop a running background task by its ID.',
            parameters={
                'type': 'object',
                'properties': {
                    'task_id': {
                        'type': 'string',
                        'description': 'The task ID to stop.',
                    },
                },
                'required': ['task_id'],
            },
            handler=_task_stop,
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
                                'active_form': {'type': 'string'},
                                'owner': {'type': 'string'},
                                'blocks': {'type': 'array', 'items': {'type': 'string'}},
                                'blocked_by': {'type': 'array', 'items': {'type': 'string'}},
                                'metadata': {'type': 'object'},
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
            name='Agent',
            description=(
                'Launch a new agent to handle complex, multi-step tasks. '
                'Each agent type has specific capabilities and tools available to it.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'description': {
                        'type': 'string',
                        'description': 'A short (3-5 word) description of the task',
                    },
                    'prompt': {
                        'type': 'string',
                        'description': 'The task for the agent to perform',
                    },
                    'subagent_type': {
                        'type': 'string',
                        'description': 'The type of specialized agent to use for this task',
                    },
                    'model': {
                        'type': 'string',
                        'enum': ['sonnet', 'opus', 'haiku'],
                        'description': 'Optional model override for this agent',
                    },
                    'run_in_background': {
                        'type': 'boolean',
                        'description': 'Set to true to run this agent in the background',
                    },
                    'isolation': {
                        'type': 'string',
                        'enum': ['worktree'],
                        'description': 'Isolation mode. "worktree" creates a temporary git worktree.',
                    },
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
                'required': ['description', 'prompt'],
            },
            handler=_agent_tool_placeholder,
        ),
        # Keep legacy name for backward compatibility
        AgentTool(
            name='delegate_agent',
            description='(Legacy) Delegate a subtask to a nested agent. Prefer using the Agent tool instead.',
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
            handler=_agent_tool_placeholder,
        ),
        AgentTool(
            name='Skill',
            description=(
                'Execute a skill within the main conversation. '
                'Skills provide specialized capabilities and domain knowledge.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'skill': {
                        'type': 'string',
                        'description': 'The skill name. E.g., "commit", "compact", or "help"',
                    },
                    'args': {
                        'type': 'string',
                        'description': 'Optional arguments for the skill',
                    },
                },
                'required': ['skill'],
            },
            handler=_execute_skill,
        ),
        AgentTool(
            name='lattice_solve',
            description=(
                'Solve any continuous optimization or minimization problem. '
                'Use this whenever you need to: find the minimum/maximum of a function, '
                'tune parameters to hit a target, search for optimal values in a range, '
                'or answer "what values of X minimize Y?" questions. '
                'Input: plain-English problem description. '
                'Examples: "minimize x^2 + y^2 in [-5,5] x [-5,5]", '
                '"find x in [0,10] that minimizes (x-3.7)^2", '
                '"what weight w minimizes 0.4*error + w*cost for w in [0,1]?". '
                'Returns: optimal point, minimum value, convergence status, solver diagnostics.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'problem': {
                        'type': 'string',
                        'description': 'The optimization problem in natural language or structured format.',
                    },
                    'samples': {
                        'type': 'integer',
                        'minimum': 1000,
                        'maximum': 1000000,
                        'description': 'Number of Monte Carlo samples (default: 10000).',
                    },
                },
                'required': ['problem'],
            },
            handler=_lattice_solve,
        ),
        AgentTool(
            name='lattice_boolean_solve',
            description=(
                'Make optimal yes/no decisions under constraints. '
                'Use when you need to choose which options to activate/enable given costs and rules. '
                'Examples: "should I use cache AND streaming, or just one? minimize cost with use_cache + use_stream <= 1", '
                '"which 2 of these 5 features to enable to minimize latency?", '
                '"model selection: pick cheapest model that meets quality threshold". '
                'Returns: which variables to set to 1 (on) vs 0 (off), cost, feasibility, confidence.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'problem': {
                        'type': 'string',
                        'description': 'The boolean optimization problem in natural language format.',
                    },
                    'samples': {
                        'type': 'integer',
                        'minimum': 500,
                        'maximum': 100000,
                        'description': 'Number of MC samples (default: 5000).',
                    },
                },
                'required': ['problem'],
            },
            handler=_lattice_boolean_solve,
        ),
        # ── Git tools ─────────────────────────────────────────────────────
        AgentTool(
            name='git_status',
            description='Show working tree status: staged, unstaged, untracked files and current branch.',
            parameters={'type': 'object', 'properties': {}},
            handler=_git_status,
        ),
        AgentTool(
            name='git_diff',
            description='Show diff of unstaged changes, staged changes, or between two commits/branches.',
            parameters={
                'type': 'object',
                'properties': {
                    'staged': {'type': 'boolean', 'description': 'Show staged (--cached) diff.'},
                    'path': {'type': 'string', 'description': 'Limit diff to this file or directory.'},
                    'base': {'type': 'string', 'description': 'Base ref (commit/branch). Omit for working-tree diff.'},
                    'head': {'type': 'string', 'description': 'Head ref (default HEAD).'},
                    'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 2000, 'description': 'Truncate output (default 400).'},
                },
            },
            handler=_git_diff,
        ),
        AgentTool(
            name='git_log',
            description='Show recent commit log with hash, author, date, message.',
            parameters={
                'type': 'object',
                'properties': {
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'description': 'Number of commits (default 20).'},
                    'path': {'type': 'string', 'description': 'Limit to commits touching this path.'},
                    'oneline': {'type': 'boolean', 'description': 'One line per commit (default true).'},
                },
            },
            handler=_git_log,
        ),
        AgentTool(
            name='git_commit',
            description='Stage all changed tracked files and create a commit. Never force-pushes. Refuses empty commits.',
            parameters={
                'type': 'object',
                'properties': {
                    'message': {'type': 'string', 'description': 'Commit message.'},
                    'paths': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Specific paths to stage. Omit to stage all tracked changes (git add -u).',
                    },
                },
                'required': ['message'],
            },
            handler=_git_commit,
        ),
        # ── File management ────────────────────────────────────────────────
        AgentTool(
            name='move_file',
            description='Move or rename a file or directory inside the workspace.',
            parameters={
                'type': 'object',
                'properties': {
                    'source': {'type': 'string'},
                    'destination': {'type': 'string'},
                },
                'required': ['source', 'destination'],
            },
            handler=_move_file,
        ),
        AgentTool(
            name='delete_file',
            description='Delete a file inside the workspace. Refuses to delete directories (use bash for that).',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                },
                'required': ['path'],
            },
            handler=_delete_file,
        ),
        AgentTool(
            name='make_dir',
            description='Create a directory (and any missing parents) inside the workspace.',
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                },
                'required': ['path'],
            },
            handler=_make_dir,
        ),
        # ── Patch ──────────────────────────────────────────────────────────
        AgentTool(
            name='patch_file',
            description=(
                'Apply a unified diff patch to a workspace file. '
                'Use when edit_file is impractical (many hunks, generated diffs). '
                'Patch must be in unified diff format (--- a/  +++ b/  @@ hunks).'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Target file path (relative to workspace).'},
                    'patch': {'type': 'string', 'description': 'Unified diff patch text.'},
                    'fuzz': {'type': 'integer', 'minimum': 0, 'maximum': 3, 'description': 'Context fuzz factor (default 2).'},
                },
                'required': ['path', 'patch'],
            },
            handler=_patch_file,
        ),
        # ── Image read ─────────────────────────────────────────────────────
        AgentTool(
            name='image_read',
            description=(
                'Read an image file and return a base64-encoded data URI suitable for vision models. '
                'Supports: png, jpg, jpeg, gif, webp. '
                'Use to inspect screenshots, diagrams, charts, or UI mockups.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Path to image file (absolute or relative to workspace).'},
                },
                'required': ['path'],
            },
            handler=_image_read,
        ),
        # ── Run tests ──────────────────────────────────────────────────────
        AgentTool(
            name='run_tests',
            description=(
                'Run the test suite (pytest by default) and return structured pass/fail/error results. '
                'Supports pytest, unittest, and npm test. '
                'Returns: total, passed, failed, errors, duration, and failed test names.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Test file or directory (default: tests/).'},
                    'pattern': {'type': 'string', 'description': 'pytest -k expression to filter tests.'},
                    'runner': {'type': 'string', 'enum': ['pytest', 'unittest', 'npm'], 'description': 'Test runner (default: pytest).'},
                    'timeout': {'type': 'integer', 'minimum': 5, 'maximum': 300, 'description': 'Timeout in seconds (default 60).'},
                },
            },
            handler=_run_tests,
        ),
        # ── Memory ────────────────────────────────────────────────────────
        AgentTool(
            name='memory_write',
            description=(
                'Write a named memory entry that persists across turns and sessions. '
                'Use for: decisions made, facts discovered, patterns noticed, things to remember. '
                'Entries are stored in ~/.latti/memory/ as plain text.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'key': {'type': 'string', 'description': 'Memory key (slug, e.g. "db-schema", "user-prefs").'},
                    'content': {'type': 'string', 'description': 'Content to store.'},
                    'append': {'type': 'boolean', 'description': 'Append to existing entry instead of overwriting (default false).'},
                },
                'required': ['key', 'content'],
            },
            handler=_memory_write,
        ),
        AgentTool(
            name='memory_read',
            description='Read a named memory entry previously stored with memory_write. Returns content or empty string if not found.',
            parameters={
                'type': 'object',
                'properties': {
                    'key': {'type': 'string', 'description': 'Memory key to read.'},
                },
                'required': ['key'],
            },
            handler=_memory_read,
        ),
        AgentTool(
            name='memory_list',
            description='List all memory keys stored with memory_write.',
            parameters={'type': 'object', 'properties': {}},
            handler=_memory_list,
        ),
        AgentTool(
            name='self_score',
            description=(
                'Score your own response quality. Pass the text of your response '
                'and get a 0-100 score based on: tool usage (+20), conciseness (+10), '
                'no anti-patterns (+10), no trailing questions (+10), no permission asking (+10). '
                'Use this BEFORE finalizing a response to check if you should revise it. '
                'A score below 60 means the response needs work.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'response_text': {
                        'type': 'string',
                        'description': 'The response text to evaluate.',
                    },
                    'used_tools': {
                        'type': 'boolean',
                        'description': 'Whether tools were called during this response.',
                    },
                },
                'required': ['response_text'],
            },
            handler=_self_score,
        ),
        AgentTool(
            name='lattice_sector_solve',
            description=(
                'Decompose an optimization into independent sectors and combine via log-odds product '
                '(Bayesian update). Based on Observer-Patch Holography: each sector is an independent '
                'observer patch. Results combine multiplicatively in log-odds space, not by averaging. '
                'Input: JSON object mapping sector names to cost function expressions, plus bounds. '
                'Example: sectors={"distance": "x0^2+x1^2", "penalty": "(x0-3)^2"}, bounds="[-5,5] x [-5,5]". '
                'Returns combined optimum, per-sector results, and consensus score.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'sectors': {
                        'type': 'object',
                        'description': 'Map of sector name to cost function expression (using x0, x1, ...).',
                        'additionalProperties': {'type': 'string'},
                    },
                    'bounds': {
                        'type': 'string',
                        'description': 'Bounds in bracket format: "[-5,5] x [-5,5]".',
                    },
                    'samples': {
                        'type': 'integer',
                        'minimum': 1000,
                        'maximum': 100000,
                        'description': 'Monte Carlo samples per sector (default: 5000).',
                    },
                },
                'required': ['sectors', 'bounds'],
            },
            handler=_lattice_sector_solve,
        ),
        AgentTool(
            name='lattice_maxent',
            description=(
                'Find the maximum-entropy distribution subject to constraints. Based on OPH Lemma 2.6: '
                'the Gibbs state p(x) ~ exp(-sum lambda_i O_i(x)) is the unique entropy-maximizing answer. '
                'Input: list of constraints as {name, expression, target} objects, plus bounds. '
                'Example: constraints=[{"name":"mean_x","expr":"x0","target":3.0}], bounds="[0,10]". '
                'Returns Lagrange multipliers, constraint errors, and entropy estimate.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'constraints': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'expr': {'type': 'string', 'description': 'Observable expression using x0, x1, ...'},
                                'target': {'type': 'number', 'description': 'Target expected value <O_i>.'},
                            },
                            'required': ['name', 'expr', 'target'],
                        },
                        'description': 'List of (name, observable_expression, target_value) constraints.',
                    },
                    'bounds': {
                        'type': 'string',
                        'description': 'Bounds in bracket format: "[0,10] x [0,10]".',
                    },
                    'samples': {
                        'type': 'integer',
                        'minimum': 1000,
                        'maximum': 100000,
                        'description': 'Monte Carlo samples (default: 5000).',
                    },
                },
                'required': ['constraints', 'bounds'],
            },
            handler=_lattice_maxent,
        ),
        AgentTool(
            name='lattice_nn_predict',
            description=(
                'Predict using the lattice neural network — Monte Carlo as hidden layer. '
                'No gradient descent; the MC sampling IS the computation. '
                'Input: feature dict (name->value), optional model_path to load saved weights. '
                'For training: pass features + outcome (0 or 1). '
                'Returns predicted probability, confidence, and per-feature contributions.'
            ),
            parameters={
                'type': 'object',
                'properties': {
                    'features': {
                        'type': 'object',
                        'description': 'Feature name to value mapping.',
                        'additionalProperties': {'type': 'number'},
                    },
                    'outcome': {
                        'type': 'number',
                        'description': 'If provided (0 or 1), train on this outcome after predicting.',
                    },
                    'model_path': {
                        'type': 'string',
                        'description': 'Path to load/save model weights (JSON). Optional.',
                    },
                    'samples': {
                        'type': 'integer',
                        'minimum': 500,
                        'maximum': 50000,
                        'description': 'Monte Carlo samples (default: 2000).',
                    },
                },
                'required': ['features'],
            },
            handler=_lattice_nn_predict,
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


def _coerce_float(arguments: dict[str, Any], key: str, default: float) -> float:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolExecutionError(f'{key} must be a number')
    return float(value)


def _relative_to_any_root(path: Path, context: ToolExecutionContext) -> Path:
    """Return a relative path against the primary root or any additional root."""
    for root in (context.root, *context.additional_roots):
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    return path


def _resolve_path(raw_path: str, context: ToolExecutionContext, *, allow_missing: bool = True) -> Path:
    expanded = Path(raw_path).expanduser()
    candidate = expanded if expanded.is_absolute() else context.root / expanded
    resolved = candidate.resolve(strict=not allow_missing)
    # Check primary root first, then additional roots
    allowed_roots = (context.root, *context.additional_roots)
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ToolExecutionError(
        f'Path {raw_path!r} escapes the workspace root {context.root}'
    )


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
        r'(^|[;&|])\s*chmod\s+-r\s+777',
        r'(^|[;&|])\s*chown\s+-r',
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
        rel = _relative_to_any_root(entry, context)
        lines.append(f'{kind}\t{rel}')
    if len(entries) > max_entries:
        lines.append(f'... truncated at {max_entries} entries ...')
    return '\n'.join(lines) if lines else '(empty directory)'


import re as _re

# Paths whose names strongly indicate secret-bearing content.
# Reading these via the auto-Read path is refused — refusing to
# ingest is the structural fix. Bash can still read them with
# explicit intent if the user really wants to.
_SECRET_BEARING_PATH_PATTERNS = (
    _re.compile(r'(^|/)\.env(\.[^/]*)?$'),               # .env, .env.local, ...
    _re.compile(r'\.pem$'),
    _re.compile(r'\.key$'),
    _re.compile(r'(^|/)id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$'),
    _re.compile(r'(^|/)credentials(\.json|\.yaml|\.yml)?$', _re.IGNORECASE),
    _re.compile(r'(^|/)secrets?(\.json|\.yaml|\.yml|\.toml)?$', _re.IGNORECASE),
    _re.compile(r'(^|/)\.aws/credentials$'),
    _re.compile(r'(^|/)\.netrc$'),
)


def _is_secret_bearing_path(path: Path) -> bool:
    """True if path's name/segments match a known secret-bearing convention."""
    text = str(path)
    return any(p.search(text) for p in _SECRET_BEARING_PATH_PATTERNS)


def _refuse_if_secret_bearing(target: Path) -> None:
    """Refuse content-returning tool calls on paths that match known
    secret-bearing conventions. Bash retains the ability to read these
    paths with explicit user intent.
    """
    if _is_secret_bearing_path(target):
        raise ToolExecutionError(
            f'refused to read secret-bearing path: {target}. '
            'Reading this via the model-driven tool path would poison '
            'message history. Use bash with explicit intent if this '
            'content is genuinely needed.'
        )


# Edit-loop threshold: after this many writes/edits to the same path within
# one tool-context lifetime, refuse with a hard error.
_EDIT_LOOP_LIMIT = 5

# Markdown-churn threshold: refuse further summary-shape .md writes after
# this many in one context. Catches the pattern where each iteration
# emitted a new "CRITICAL_FINDING_*.md", "SESSION_SUMMARY_*.md",
# "QUICK_REFERENCE.md", "gaps_filled_*.md" etc. as fake progress markers.
_MD_CHURN_LIMIT = 4

# Filename signatures that look like self-emitted summary docs.
_CHURN_FILENAME_PATTERNS = re.compile(
    r'(?ix)('
    r'summary|finding|critical|session_|gaps?[-_]filled|quick[-_]reference'
    r'|deliverables?|status[-_]report|progress[-_]report|wrap[-_]up'
    r'|completion[-_]report|implementation[-_]summary|final[-_]summary'
    r')'
)
# Conventional doc filenames that should NEVER be flagged as churn.
_DOC_PATH_ALLOWLIST = re.compile(r'(?i)(^|/)(readme|changelog|license|contributing)\b')


def _is_churn_markdown(target: Path) -> bool:
    """True if the path looks like a summary/findings markdown likely
    emitted as fake-progress churn rather than real documentation.
    """
    name = target.name
    if not name.lower().endswith('.md'):
        return False
    if _DOC_PATH_ALLOWLIST.search(str(target)):
        return False
    # Files in docs/ are legitimate docs by convention.
    if 'docs' in {p.lower() for p in target.parts}:
        return False
    return bool(_CHURN_FILENAME_PATTERNS.search(name))


def _track_churn_and_check(target: Path, context: ToolExecutionContext) -> None:
    """Refuse if too many summary-shape markdown files written in this
    context. Counter is reused from edit_history under a sentinel key so
    no new dataclass field is needed.
    """
    if not _is_churn_markdown(target):
        return
    counter_key = '__churn_md_count__'
    context.edit_history[counter_key] = context.edit_history.get(counter_key, 0) + 1
    if context.edit_history[counter_key] > _MD_CHURN_LIMIT:
        raise ToolExecutionError(
            f'churn-doc guard: refused to write {target.name} — this is '
            f'the {context.edit_history[counter_key]}-th summary/findings '
            f'markdown in this session (limit {_MD_CHURN_LIMIT}). '
            'Writing more summaries is not progress; it is performance. '
            'If the work is genuinely done, one summary suffices. If it '
            'is not done, write code or tests, not another summary.'
        )


def _track_write_and_check_loop(target: Path, context: ToolExecutionContext) -> None:
    """Increment edit count for `target` and refuse if loop limit exceeded.

    Refusal is permanent for the rest of this context's lifetime — there
    is no way for the model to talk past it. The fix for hitting this is
    to step back and explain what fundamental change is expected, not to
    retry with a different threshold.
    """
    key = str(target.resolve())
    context.edit_history[key] = context.edit_history.get(key, 0) + 1
    context.self_authored_paths.add(key)
    if context.edit_history[key] > _EDIT_LOOP_LIMIT:
        raise ToolExecutionError(
            f'edit-loop guard: refused to write/edit {target} a '
            f'{context.edit_history[key]}-th time in this session '
            f'(limit {_EDIT_LOOP_LIMIT}). This pattern indicates a '
            'tweak-and-rerun loop, not progress. Stop and explain to '
            'the user what fundamental change you expect to be '
            'different — or what hypothesis you are testing — before '
            'editing this file again.'
        )


def _self_authored_warning(target: Path, context: ToolExecutionContext) -> str:
    """If the agent wrote this file earlier in the session, return a
    warning header to prepend to the read content. Empty string otherwise.
    """
    if str(target.resolve()) in context.self_authored_paths:
        return (
            '[self-authored: this file was written or edited by the '
            'agent earlier in this session. Results from it are not '
            'independent measurements. Treat with skepticism.]\n'
        )
    return ''


def _read_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    import base64
    import struct

    target = _resolve_path(_require_string(arguments, 'path'), context, allow_missing=False)
    _refuse_if_secret_bearing(target)
    if not target.is_file():
        raise ToolExecutionError(f'Path is not a file: {target}')

    suffix = target.suffix.lower()

    # --- Image handling ---
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    if suffix in IMAGE_EXTENSIONS:
        raw = target.read_bytes()
        b64 = base64.b64encode(raw).decode('ascii')
        # Best-effort width/height detection without PIL
        dimensions = ''
        try:
            if suffix == '.png' and raw[:8] == b'\x89PNG\r\n\x1a\n':
                w, h = struct.unpack('>II', raw[16:24])
                dimensions = f', {w}x{h}'
            elif suffix in ('.jpg', '.jpeg') and raw[:2] == b'\xff\xd8':
                # Walk JPEG segments to find SOF marker
                i = 2
                while i < len(raw) - 8:
                    if raw[i] != 0xFF:
                        break
                    marker = raw[i + 1]
                    seg_len = struct.unpack('>H', raw[i + 2:i + 4])[0]
                    # SOF0-SOF3 (0xC0-0xC3) contain dimensions
                    if 0xC0 <= marker <= 0xC3:
                        h, w = struct.unpack('>HH', raw[i + 5:i + 9])
                        dimensions = f', {w}x{h}'
                        break
                    i += 2 + seg_len
            elif suffix == '.webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
                # VP8 lossy: chunk 'VP8 '
                if raw[12:16] == b'VP8 ':
                    w = (struct.unpack('<H', raw[26:28])[0]) & 0x3FFF
                    h = (struct.unpack('<H', raw[28:30])[0]) & 0x3FFF
                    dimensions = f', {w}x{h}'
                # VP8L lossless: chunk 'VP8L'
                elif raw[12:16] == b'VP8L':
                    bits = struct.unpack('<I', raw[21:25])[0]
                    w = (bits & 0x3FFF) + 1
                    h = ((bits >> 14) & 0x3FFF) + 1
                    dimensions = f', {w}x{h}'
        except Exception:
            pass
        header = f'[Image: {target.name}{dimensions}, {len(b64)} base64 bytes]\n'
        return _truncate_output(header + b64, context.max_output_chars)

    # --- PDF handling ---
    if suffix == '.pdf':
        # Try pdftotext first (poppler, usually available on macOS via brew or system)
        try:
            result = subprocess.run(
                ['pdftotext', str(target), '-'],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                text = result.stdout.decode('utf-8', errors='replace')
                return _truncate_output(
                    f'[PDF: {target.name}, extracted via pdftotext]\n{text}',
                    context.max_output_chars,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Fallback: extract printable ASCII strings from raw bytes (like `strings`)
        raw = target.read_bytes()
        printable = re.findall(rb'[ -~\t\n\r]{4,}', raw)
        extracted = b'\n'.join(printable).decode('ascii', errors='replace')
        return _truncate_output(
            f'[PDF: {target.name}, {len(raw)} bytes — pdftotext unavailable, extracted strings]\n{extracted}',
            context.max_output_chars,
        )

    text = target.read_text(encoding='utf-8', errors='replace')
    self_warning = _self_authored_warning(target, context)
    start_line = arguments.get('start_line')
    end_line = arguments.get('end_line')
    if start_line is None and end_line is None:
        return _truncate_output(self_warning + text, context.max_output_chars)
    if start_line is not None and (isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1):
        raise ToolExecutionError('start_line must be an integer >= 1')
    if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < 1):
        raise ToolExecutionError('end_line must be an integer >= 1')
    lines = text.splitlines()
    start_idx = max((start_line or 1) - 1, 0)
    end_idx = end_line or len(lines)
    selected = lines[start_idx:end_idx]
    rendered = '\n'.join(f'{start_idx + idx + 1}: {line}' for idx, line in enumerate(selected))
    return _truncate_output(self_warning + rendered, context.max_output_chars)


_LATTI_GATE_PATTERNS = [
    'run all', 'run every session', 'check automatically',
    'before responding', 'on first message',
    'these are not optional', 'run these on',
]
_LATTI_GATE_ALLOWED_MD = {'ARCHITECTURE.md', 'AUTONOMY.md', 'MEMORY.md', 'README.md'}


def _latti_gate_check(filepath: str, content: str) -> str:
    """Check if a write to ~/.latti/ is instructions that should be code. Returns warning or empty."""
    latti_home = os.path.expanduser('~/.latti')
    if not filepath.startswith(latti_home):
        return ''
    if '/memory/' in filepath:
        return ''  # memory files are the learning loop
    if not filepath.endswith('.md'):
        return ''  # .py, .sh, .json are fine
    if os.path.basename(filepath) in _LATTI_GATE_ALLOWED_MD:
        return ''
    content_lower = content.lower()
    for pattern in _LATTI_GATE_PATTERNS:
        if pattern in content_lower:
            return (
                f'LATTI GATE: This file contains instruction pattern "{pattern}". '
                f'Consider writing a Python function in latti_boot.py instead. '
                f'Gate: 1→function in latti_boot.py, 2→tool in agent_tools.py, '
                f'3→string in gather_boot_context(), 4→STOP creating .md instructions.'
            )
    return ''


def _write_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    target = _resolve_path(_require_string(arguments, 'path'), context)
    content = arguments.get('content')
    if not isinstance(content, str):
        raise ToolExecutionError('content must be a string')
    _track_churn_and_check(target, context)
    _track_write_and_check_loop(target, context)
    previous_text: str | None = None
    previous_sha256: str | None = None
    if target.exists() and target.is_file():
        previous_text = target.read_text(encoding='utf-8', errors='replace')
        previous_sha256 = hashlib.sha256(previous_text.encode('utf-8')).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    rel = _relative_to_any_root(target, context)
    new_sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
    # Latti gate: warn if writing instruction .md to ~/.latti/
    _gate_warning = _latti_gate_check(str(target), content)
    _wrote_msg = f'wrote {rel} ({len(content)} chars)'
    if _gate_warning:
        _wrote_msg += f'\n\n⚠ {_gate_warning}'
    return (
        _wrote_msg,
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
    _refuse_if_secret_bearing(target)
    _track_write_and_check_loop(target, context)
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
    rel = _relative_to_any_root(target, context)
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


def _notebook_edit(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    target = _resolve_path(_require_string(arguments, 'path'), context, allow_missing=False)
    if target.suffix != '.ipynb':
        raise ToolExecutionError('notebook_edit requires a .ipynb target')
    if not target.is_file():
        raise ToolExecutionError(f'Path is not a file: {target}')
    cell_index = arguments.get('cell_index')
    if isinstance(cell_index, bool) or not isinstance(cell_index, int) or cell_index < 0:
        raise ToolExecutionError('cell_index must be an integer >= 0')
    source = arguments.get('source')
    if not isinstance(source, str):
        raise ToolExecutionError('source must be a string')
    cell_type = arguments.get('cell_type', 'code')
    if cell_type is not None and not isinstance(cell_type, str):
        raise ToolExecutionError('cell_type must be a string')
    create_cell = arguments.get('create_cell', False)
    if not isinstance(create_cell, bool):
        raise ToolExecutionError('create_cell must be a boolean')

    raw = target.read_text(encoding='utf-8', errors='replace')
    before_sha256 = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    try:
        notebook = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolExecutionError(f'Notebook is not valid JSON: {target}') from exc
    if not isinstance(notebook, dict):
        raise ToolExecutionError('Notebook payload must be a JSON object')
    cells = notebook.get('cells')
    if not isinstance(cells, list):
        raise ToolExecutionError('Notebook does not contain a cells array')

    while len(cells) <= cell_index:
        if not create_cell:
            raise ToolExecutionError(
                f'Notebook cell {cell_index} does not exist; pass create_cell=true to append missing cells'
            )
        cells.append(
            {
                'cell_type': cell_type or 'code',
                'metadata': {},
                'source': [],
                'outputs': [],
                'execution_count': None,
            }
        )
    cell = cells[cell_index]
    if not isinstance(cell, dict):
        raise ToolExecutionError(f'Notebook cell {cell_index} is not a JSON object')
    existing_type = cell.get('cell_type')
    if not isinstance(existing_type, str):
        existing_type = 'code'
    source_lines = source.splitlines(keepends=True)
    if source and not source.endswith('\n'):
        source_lines = [*source_lines[:-1], source_lines[-1]]
    cell['cell_type'] = cell_type or existing_type
    cell['source'] = source_lines
    if cell['cell_type'] == 'code':
        cell.setdefault('outputs', [])
        cell.setdefault('execution_count', None)
    updated = json.dumps(notebook, ensure_ascii=True, indent=1) + '\n'
    target.write_text(updated, encoding='utf-8')
    after_sha256 = hashlib.sha256(updated.encode('utf-8')).hexdigest()
    rel = _relative_to_any_root(target, context)
    return (
        f'updated notebook cell {cell_index} in {rel}',
        {
            'action': 'notebook_edit',
            'path': str(rel),
            'cell_index': cell_index,
            'cell_type': cell['cell_type'],
            'before_sha256': before_sha256,
            'after_sha256': after_sha256,
            'before_preview': _snapshot_text(raw),
            'after_preview': _snapshot_text(updated),
        },
    )


def _glob_search(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    pattern = _require_string(arguments, 'pattern')
    matches = sorted(context.root.glob(pattern))
    if not matches:
        return '(no matches)'
    root_resolved = context.root.resolve()
    validated: list[str] = []
    for path in matches:
        try:
            path.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        validated.append(str(_relative_to_any_root(path, context)))
    if not validated:
        return '(no matches)'
    return _truncate_output('\n'.join(validated), context.max_output_chars)


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
    # If the user explicitly grep'd a secret-bearing file, refuse loudly.
    # When iterating a directory, secret-bearing entries are skipped
    # silently below — they weren't named, so silent skip is honest.
    if root.is_file():
        _refuse_if_secret_bearing(root)
    try:
        regex = re.compile(re.escape(pattern) if literal else pattern)
    except re.error as exc:
        raise ToolExecutionError(f'Invalid regex pattern: {exc}') from exc
    hits: list[str] = []
    file_iter = root.rglob('*') if root.is_dir() else [root]
    for file_path in file_iter:
        if not file_path.is_file():
            continue
        if _is_secret_bearing_path(file_path):
            continue
        try:
            text = file_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = _relative_to_any_root(file_path, context)
                hits.append(f'{rel}:{line_no}: {line}')
                if len(hits) >= max_matches:
                    return '\n'.join(hits + [f'... truncated at {max_matches} matches ...'])
    return '\n'.join(hits) if hits else '(no matches)'


def _bash_self_authored_banner(command: str, context: ToolExecutionContext) -> str:
    """Banner prepended to bash output when the command references a path
    the agent wrote earlier in this session.

    Catches the case where the agent runs a script it just wrote and cites
    the output as evidence — e.g. `python3 retrieval_accuracy_test.py`
    where the test was authored five turns ago. Without this banner, the
    output looks indistinguishable from running an external program.
    """
    if not context.self_authored_paths:
        return ''
    referenced: list[str] = []
    for path in context.self_authored_paths:
        # Match the absolute path or its basename in the command. Loose
        # match — false positives are mild (extra warning) but false
        # negatives miss the whole point.
        basename = Path(path).name
        if path in command or (basename and basename in command):
            referenced.append(basename)
    if not referenced:
        return ''
    names = ', '.join(sorted(set(referenced)))
    return (
        f'[self-authored: this command runs files written by the agent '
        f'earlier in this session ({names}). Output is not an '
        f'independent measurement. Treat with skepticism.]\n'
    )


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
    banner = _bash_self_authored_banner(command, context)
    payload = [
        f'exit_code={completed.returncode}',
        '[stdout]',
        stdout.rstrip(),
        '[stderr]',
        stderr.rstrip(),
    ]
    rendered = '\n'.join(payload).strip()
    if banner:
        rendered = banner + rendered
    return (
        _truncate_output(rendered, context.max_output_chars),
        {
            'action': 'bash',
            'command': command,
            'exit_code': completed.returncode,
            'stdout_preview': _snapshot_text(stdout),
            'stderr_preview': _snapshot_text(stderr),
            'output_preview': _snapshot_text('\n'.join(payload).strip()),
        },
    )


_BINARY_CONTENT_TYPES = {
    'application/pdf',
    'application/octet-stream',
    'application/zip',
    'application/x-tar',
    'application/x-gzip',
    'application/gzip',
    'application/x-bzip2',
    'application/wasm',
    'application/x-protobuf',
}


def _looks_binary(raw: bytes, content_type: str | None) -> bool:
    """True if the response is unlikely to be readable text.

    Either the Content-Type announces binary, or the byte stream contains
    NUL bytes / a high ratio of non-printable bytes. Catches PDFs even when
    they are served with a generic Content-Type header.
    """
    if content_type:
        ct = content_type.split(';', 1)[0].strip().lower()
        if ct in _BINARY_CONTENT_TYPES or ct.startswith(('image/', 'video/', 'audio/')):
            return True
    if not raw:
        return False
    if raw[:5] == b'%PDF-':
        return True
    sample = raw[:4096]
    if b'\x00' in sample:
        return True
    nonprintable = sum(1 for b in sample if b < 9 or 13 < b < 32 or b == 127)
    return (nonprintable / len(sample)) > 0.10


def _web_fetch(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    raw_url = _require_string(arguments, 'url')
    max_chars = _coerce_int(arguments, 'max_chars', context.max_output_chars)
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {'http', 'https'}:
        raise ToolExecutionError('url must use http or https scheme')
    request = urllib.request.Request(
        raw_url,
        headers={'User-Agent': 'claw-code-agent/1.0'},
    )
    try:
        with urllib.request.urlopen(request, timeout=context.command_timeout_seconds) as response:
            raw_bytes = response.read(max_chars + 1)
            content_type = response.headers.get_content_type() if hasattr(response, 'headers') else None
    except (urllib.error.URLError, OSError) as exc:
        raise ToolExecutionError(f'Failed to fetch {raw_url}: {exc}') from exc
    # Binary payloads (PDFs, archives, images) decode to UTF-8 garbage with
    # `errors='replace'`. That garbage looks plausibly like prose to the
    # model, which then hallucinates content from it. Refuse loudly instead
    # of returning faux-text — this is the path that produced fabricated
    # paper titles / authors when the model was given an arxiv PDF URL.
    if _looks_binary(raw_bytes, content_type):
        raise ToolExecutionError(
            f'Fetched {raw_url} returned non-text content '
            f'(content_type={content_type!r}, {len(raw_bytes)} bytes). '
            'web_fetch only extracts UTF-8 text. For PDFs use bash with '
            'pdftotext; for images use read_file. Do NOT proceed as if '
            'the content were readable — it is not.'
        )
    text = raw_bytes.decode('utf-8', errors='replace')
    truncated = len(text) > max_chars
    rendered = text[:max_chars]
    if truncated:
        rendered += '\n...[truncated]...'
    return (
        _truncate_output(rendered, context.max_output_chars),
        {
            'action': 'web_fetch',
            'url': raw_url,
            'content_type': content_type,
            'truncated': truncated,
            'fetched_chars': len(text),
            'preview': _snapshot_text(rendered),
        },
    )


def _search_status(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_search_runtime(context)
    provider = arguments.get('provider')
    if provider is not None and not isinstance(provider, str):
        raise ToolExecutionError('provider must be a string')
    if provider:
        return runtime.render_provider(provider)
    return '\n'.join(['# Search', '', runtime.render_summary()])


def _search_list_providers(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_search_runtime(context)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_providers = _coerce_int(arguments, 'max_providers', 20)
    providers = runtime.list_providers(query=query, limit=max_providers)
    lines = ['# Search Providers', '']
    if not providers:
        lines.append('No local search providers discovered.')
        return '\n'.join(lines)
    current = runtime.current_provider()
    current_name = current.name if current is not None else None
    for provider in providers:
        details = [provider.name, provider.provider, provider.base_url]
        if provider.api_key_env:
            details.append(f'api_key_env={provider.api_key_env}')
        if current_name == provider.name:
            details.append('active=true')
        lines.append('- ' + ' ; '.join(details))
    return '\n'.join(lines)


def _search_activate_provider(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    runtime = _require_search_runtime(context)
    provider = _require_string(arguments, 'provider')
    try:
        report = runtime.activate_provider(provider)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown search provider: {provider}') from exc
    return (
        '\n'.join(['# Search', '', report.as_text()]),
        {
            'action': 'search_activate_provider',
            'provider': report.provider_name,
            'provider_kind': report.provider_kind,
            'base_url': report.base_url,
        },
    )


def _web_search(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_search_runtime(context)
    query = _require_string(arguments, 'query')
    provider = arguments.get('provider')
    if provider is not None and not isinstance(provider, str):
        raise ToolExecutionError('provider must be a string')
    raw_domains = arguments.get('domains', ())
    if raw_domains is None:
        raw_domains = ()
    if not isinstance(raw_domains, (list, tuple)):
        raise ToolExecutionError('domains must be an array of strings')
    domains: tuple[str, ...] = tuple(
        item.strip() for item in raw_domains if isinstance(item, str) and item.strip()
    )
    if len(domains) != len(raw_domains):
        raise ToolExecutionError('domains must contain only non-empty strings')
    max_results = arguments.get('max_results')
    if max_results is None:
        default_provider = runtime.get_provider(provider) if provider else runtime.current_provider()
        max_results = default_provider.default_max_results if default_provider is not None else 5
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ToolExecutionError('max_results must be an integer')
    if max_results < 1 or max_results > 20:
        raise ToolExecutionError('max_results must be between 1 and 20')
    try:
        selected_provider, results = runtime.search(
            query,
            provider_name=provider,
            max_results=max_results,
            domains=domains,
            timeout_seconds=context.command_timeout_seconds,
        )
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown search provider: {provider or exc.args[0]}') from exc
    except LookupError as exc:
        raise ToolExecutionError(str(exc)) from exc
    except (ValueError, OSError, urllib.error.URLError) as exc:
        raise ToolExecutionError(f'Web search failed: {exc}') from exc
    lines = ['# Web Search', '']
    lines.append(f'- Provider: {selected_provider.name} ({selected_provider.provider})')
    lines.append(f'- Query: {query}')
    lines.append(f'- Results: {len(results)}')
    if domains:
        lines.append('- Domains: ' + ', '.join(domains))
    lines.append('')
    if not results:
        lines.append('No search results.')
    else:
        for result in results:
            lines.append(f'{result.rank}. {result.title}')
            lines.append(f'   {result.url}')
            if result.snippet:
                lines.append(f'   {result.snippet}')
    rendered = '\n'.join(lines)
    return (
        rendered,
        {
            'action': 'web_search',
            'provider': selected_provider.name,
            'provider_kind': selected_provider.provider,
            'query': query,
            'result_count': len(results),
            'domains': list(domains),
            'top_urls': [result.url for result in results[:5]],
        },
    )


def _tool_search(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    query = _require_string(arguments, 'query').lower()
    max_results = _coerce_int(arguments, 'max_results', 20)
    registry = context.tool_registry or default_tool_registry()
    matches: list[tuple[str, str]] = []
    for tool in registry.values():
        haystack = f'{tool.name} {tool.description}'.lower()
        if query in haystack:
            matches.append((tool.name, tool.description))
    if not matches:
        return '(no matching tools)'
    lines = ['# Tool Search', '']
    for name, description in matches[:max_results]:
        lines.append(f'- `{name}`: {description}')
    return '\n'.join(lines)


def _recall_memory(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Search Latti's persistent memory for relevant scars/SOPs/lessons.

    Routes (query, kind, limit) into LattiMemoryStore.recall over the
    memory directory at LATTI_MEMORY_DIR (default ~/.latti/memory).
    Returns a formatted text block the LLM can read; empty matches
    return an explicit "no matching memories" sentence rather than an
    empty string (so the LLM doesn't misread silence as an error).

    Tested by tests/test_recall_memory_tool.py + test_memory_recall.py.
    """
    del context  # tool reads from filesystem, not workspace context
    query = _require_string(arguments, 'query').strip()
    if not query:
        return 'No query provided.'
    kind = arguments.get('kind') if isinstance(arguments.get('kind'), str) else None
    limit = _coerce_int(arguments, 'limit', 5)
    if limit < 1:
        limit = 1
    if limit > 20:
        limit = 20

    memory_dir_override = os.environ.get('LATTI_MEMORY_DIR')
    memory_dir = (
        Path(memory_dir_override)
        if memory_dir_override
        else Path.home() / '.latti' / 'memory'
    )
    if not memory_dir.exists():
        return 'No matching memories found (memory directory does not exist).'

    try:
        from .state_machine_memory import LattiMemoryStore
        store = LattiMemoryStore(memory_dir)
        results = store.recall(query, kind=kind, limit=limit)  # type: ignore[arg-type]
    except Exception as exc:
        return f'Memory recall failed: {exc!r}'

    if not results:
        return f'No matching memories found for query={query!r} kind={kind or "any"}.'

    lines = [f'# Memory recall — {len(results)} match(es) for {query!r}']
    if kind:
        lines.append(f'(filtered to kind={kind})')
    lines.append('')
    for rec in results:
        lines.append(f'## [{rec.kind}] {rec.id}')
        body_preview = rec.body.strip()
        if len(body_preview) > 600:
            body_preview = body_preview[:597] + '...'
        lines.append(body_preview)
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _sleep(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    seconds = _coerce_float(arguments, 'seconds', 0.0)
    if seconds < 0.0 or seconds > 5.0:
        raise ToolExecutionError('seconds must be between 0.0 and 5.0')
    time.sleep(seconds)
    return (
        f'slept for {seconds:.3f} seconds',
        {
            'action': 'sleep',
            'seconds': seconds,
        },
    )


def _ask_user_question(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_ask_user_runtime(context)
    question = _require_string(arguments, 'question')
    header = arguments.get('header')
    question_id = arguments.get('question_id')
    if header is not None and not isinstance(header, str):
        raise ToolExecutionError('header must be a string')
    if question_id is not None and not isinstance(question_id, str):
        raise ToolExecutionError('question_id must be a string')
    raw_choices = arguments.get('choices', ())
    if raw_choices is None:
        raw_choices = ()
    if not isinstance(raw_choices, (list, tuple)):
        raise ToolExecutionError('choices must be an array of strings')
    choices = tuple(
        item.strip()
        for item in raw_choices
        if isinstance(item, str) and item.strip()
    )
    if len(choices) != len(raw_choices):
        raise ToolExecutionError('choices must contain only non-empty strings')
    allow_free_text = arguments.get('allow_free_text', True)
    if not isinstance(allow_free_text, bool):
        raise ToolExecutionError('allow_free_text must be a boolean')
    try:
        response = runtime.answer(
            question=question,
            choices=choices,
            question_id=question_id,
            header=header,
            allow_free_text=allow_free_text,
        )
    except LookupError as exc:
        raise ToolExecutionError(str(exc)) from exc
    lines = ['# Ask User', '']
    if header:
        lines.append(f'- Header: {header}')
    if question_id:
        lines.append(f'- Question ID: {question_id}')
    lines.append(f'- Question: {question}')
    if choices:
        lines.append('- Choices: ' + ', '.join(choices))
    lines.extend(
        [
            f'- Source: {response.source}',
            '',
            response.answer,
        ]
    )
    return (
        '\n'.join(lines),
        {
            'action': 'ask_user_question',
            'question': question,
            'question_id': question_id,
            'header': header,
            'source': response.source,
            'answer_preview': _snapshot_text(response.answer),
            'choices': list(choices),
        },
    )


def _account_status(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_account_runtime(context)
    profile = arguments.get('profile')
    if profile is not None and not isinstance(profile, str):
        raise ToolExecutionError('profile must be a string')
    if profile:
        return runtime.render_profile(profile)
    return '\n'.join(['# Account', '', runtime.render_summary()])


def _account_list_profiles(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_account_runtime(context)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_profiles = _coerce_int(arguments, 'max_profiles', 20)
    profiles = runtime.list_profiles(query=query, limit=max_profiles)
    lines = ['# Account Profiles', '']
    if not profiles:
        lines.append('No local account profiles discovered.')
        return '\n'.join(lines)
    for profile in profiles:
        details = [profile.name, profile.provider, profile.identity]
        if profile.org:
            details.append(f'org={profile.org}')
        if profile.auth_mode:
            details.append(f'auth_mode={profile.auth_mode}')
        lines.append('- ' + ' ; '.join(details))
    return '\n'.join(lines)


def _account_login(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_account_runtime(context)
    target = _require_string(arguments, 'target')
    provider = arguments.get('provider')
    auth_mode = arguments.get('auth_mode')
    if provider is not None and not isinstance(provider, str):
        raise ToolExecutionError('provider must be a string')
    if auth_mode is not None and not isinstance(auth_mode, str):
        raise ToolExecutionError('auth_mode must be a string')
    report = runtime.login(target, provider=provider, auth_mode=auth_mode)
    return '\n'.join(['# Account', '', report.as_text()])


def _account_logout(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_account_runtime(context)
    reason = arguments.get('reason', 'tool_logout')
    if not isinstance(reason, str):
        raise ToolExecutionError('reason must be a string')
    report = runtime.logout(reason=reason)
    return '\n'.join(['# Account', '', report.as_text()])


def _config_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_config_runtime(context)
    source = arguments.get('source')
    prefix = arguments.get('prefix')
    if source is not None and not isinstance(source, str):
        raise ToolExecutionError('source must be a string')
    if prefix is not None and not isinstance(prefix, str):
        raise ToolExecutionError('prefix must be a string')
    limit = _coerce_int(arguments, 'limit', 100)
    try:
        rendered = runtime.render_keys(source=source, prefix=prefix, limit=limit)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown config source: {exc.args[0]}') from exc
    return '\n'.join(['# Config Keys', '', rendered])


def _config_get(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_config_runtime(context)
    key_path = _require_string(arguments, 'key_path')
    source = arguments.get('source')
    if source is not None and not isinstance(source, str):
        raise ToolExecutionError('source must be a string')
    try:
        rendered = runtime.render_value(key_path, source=source)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown config key or source: {exc.args[0]}') from exc
    return '\n'.join(['# Config Value', '', rendered])


def _config_set(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_config_runtime(context)
    key_path = _require_string(arguments, 'key_path')
    source = arguments.get('source', 'local')
    if not isinstance(source, str):
        raise ToolExecutionError('source must be a string')
    if 'value' not in arguments:
        raise ToolExecutionError('value is required')
    value = arguments.get('value')
    try:
        mutation = runtime.set_value(key_path, value, source=source)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown config source: {exc.args[0]}') from exc
    return (
        f'set {key_path} in {mutation.source_name} config',
        _config_mutation_metadata(mutation, value),
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


def _mcp_list_tools(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_mcp_runtime(context)
    query = arguments.get('query')
    server = arguments.get('server')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    if server is not None and not isinstance(server, str):
        raise ToolExecutionError('server must be a string')
    max_tools = _coerce_int(arguments, 'max_tools', 50)
    tools = runtime.list_tools(query=query, server_name=server, limit=max_tools)
    if not tools:
        return '(no MCP tools)'
    lines: list[str] = []
    for tool in tools:
        details = [tool.name, f'server={tool.server_name}']
        if tool.description:
            details.append(tool.description)
        lines.append(' ; '.join(details))
    return '\n'.join(lines)


def _mcp_call_tool(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_mcp_runtime(context)
    tool_name = _require_string(arguments, 'tool_name')
    server = arguments.get('server')
    if server is not None and not isinstance(server, str):
        raise ToolExecutionError('server must be a string')
    raw_arguments = arguments.get('arguments', {})
    if raw_arguments is None:
        raw_arguments = {}
    if not isinstance(raw_arguments, dict):
        raise ToolExecutionError('arguments must be an object')
    max_chars = _coerce_int(arguments, 'max_chars', context.max_output_chars)
    try:
        content, metadata = runtime.call_tool(
            tool_name,
            arguments=raw_arguments,
            server_name=server,
            max_chars=max_chars,
        )
    except FileNotFoundError as exc:
        raise ToolExecutionError(str(exc)) from exc
    return (
        content,
        {
            'action': 'mcp_call_tool',
            'tool_name': tool_name,
            'server_name': metadata.get('server_name'),
            'mcp_is_error': metadata.get('is_error'),
        },
    )


def _remote_status(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    runtime = _require_remote_runtime(context)
    profile = arguments.get('profile')
    if profile is not None and not isinstance(profile, str):
        raise ToolExecutionError('profile must be a string')
    if profile:
        return runtime.render_profile(profile)
    return '\n'.join(['# Remote', '', runtime.render_summary()])


def _remote_list_profiles(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    runtime = _require_remote_runtime(context)
    query = arguments.get('query')
    mode = arguments.get('mode')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    if mode is not None and not isinstance(mode, str):
        raise ToolExecutionError('mode must be a string')
    max_profiles = _coerce_int(arguments, 'max_profiles', 20)
    return runtime.render_profiles_index(query=query, mode=mode, limit=max_profiles)


def _remote_connect(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_remote_runtime(context)
    target = _require_string(arguments, 'target')
    mode = arguments.get('mode')
    if mode is not None and not isinstance(mode, str):
        raise ToolExecutionError('mode must be a string')
    report = runtime.connect(target, mode=mode)
    return (
        report.as_text(),
        {
            'action': 'remote_connect',
            'mode': report.mode,
            'target': report.target or target,
            'profile_name': report.profile_name,
            'session_url': report.session_url,
            'workspace_cwd': report.workspace_cwd,
        },
    )


def _remote_disconnect(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_remote_runtime(context)
    reason = arguments.get('reason')
    if reason is not None and not isinstance(reason, str):
        raise ToolExecutionError('reason must be a string')
    report = runtime.disconnect(reason=reason or 'tool_request')
    return (
        report.as_text(),
        {
            'action': 'remote_disconnect',
            'mode': report.mode,
            'target': report.target,
        },
    )


def _worktree_status(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    _ = arguments
    runtime = _require_worktree_runtime(context)
    return '\n'.join(['# Worktree', '', runtime.render_summary()])


def _worktree_enter(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_worktree_runtime(context)
    name = arguments.get('name')
    if name is not None and not isinstance(name, str):
        raise ToolExecutionError('name must be a string')
    try:
        report = runtime.enter(name=name)
    except (RuntimeError, ValueError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return (
        report.as_text(),
        {
            'action': 'worktree_enter',
            'cwd_update': report.worktree_path,
            'repo_root': report.repo_root,
            'worktree_path': report.worktree_path,
            'worktree_branch': report.worktree_branch,
            'session_name': report.session_name,
            'before_cwd': report.original_cwd,
            'after_cwd': report.worktree_path,
            'path': report.worktree_path,
        },
    )


def _worktree_exit(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_worktree_runtime(context)
    action = arguments.get('action', 'keep')
    discard_changes = arguments.get('discard_changes', False)
    if not isinstance(action, str):
        raise ToolExecutionError('action must be a string')
    if not isinstance(discard_changes, bool):
        raise ToolExecutionError('discard_changes must be a boolean')
    try:
        report = runtime.exit(
            action=action,
            discard_changes=discard_changes,
        )
    except (RuntimeError, ValueError) as exc:
        raise ToolExecutionError(str(exc)) from exc
    return (
        report.as_text(),
        {
            'action': 'worktree_exit',
            'cwd_update': report.original_cwd or report.current_cwd,
            'repo_root': report.repo_root,
            'worktree_path': report.worktree_path,
            'worktree_branch': report.worktree_branch,
            'session_name': report.session_name,
            'before_cwd': report.worktree_path,
            'after_cwd': report.original_cwd or report.current_cwd,
            'exit_action': report.metadata.get('action'),
            'discard_changes': report.metadata.get('discard_changes'),
            'path': report.worktree_path,
        },
    )


def _workflow_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_workflow_runtime(context)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_workflows = _coerce_int(arguments, 'max_workflows', 50)
    workflows = runtime.list_workflows(query=query, limit=max_workflows)
    lines = ['# Workflows', '']
    if not workflows:
        lines.append('No local workflows discovered.')
        return '\n'.join(lines)
    for workflow in workflows:
        description = workflow.description or 'No description.'
        lines.append(f'- {workflow.name} ; steps={len(workflow.steps)} ; {description}')
    return '\n'.join(lines)


def _workflow_get(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_workflow_runtime(context)
    workflow_name = _require_string(arguments, 'workflow_name')
    try:
        return runtime.render_workflow(workflow_name)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown workflow: {workflow_name}') from exc


def _workflow_run(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_workflow_runtime(context)
    workflow_name = _require_string(arguments, 'workflow_name')
    raw_arguments = arguments.get('arguments', {})
    if raw_arguments is None:
        raw_arguments = {}
    if not isinstance(raw_arguments, dict):
        raise ToolExecutionError('arguments must be an object')
    try:
        rendered = runtime.render_run_report(workflow_name, arguments=raw_arguments)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown workflow: {workflow_name}') from exc
    return (
        rendered,
        {
            'action': 'workflow_run',
            'workflow_name': workflow_name,
            'argument_keys': sorted(str(key) for key in raw_arguments.keys()),
        },
    )


def _remote_trigger(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> tuple[str, dict[str, Any]]:
    runtime = _require_remote_trigger_runtime(context)
    action = _require_string(arguments, 'action').strip().lower()
    if action == 'list':
        query = arguments.get('query')
        if query is not None and not isinstance(query, str):
            raise ToolExecutionError('query must be a string')
        max_triggers = _coerce_int(arguments, 'max_triggers', 50)
        rendered = runtime.render_trigger_index(query=query)
        return (
            rendered,
            {
                'action': 'remote_trigger',
                'remote_trigger_action': action,
                'listed_triggers': len(runtime.list_triggers(query=query, limit=max_triggers)),
            },
        )
    if action == 'get':
        trigger_id = _require_string(arguments, 'trigger_id')
        try:
            rendered = runtime.render_trigger(trigger_id)
        except KeyError as exc:
            raise ToolExecutionError(f'Unknown remote trigger: {trigger_id}') from exc
        return (
            rendered,
            {
                'action': 'remote_trigger',
                'remote_trigger_action': action,
                'trigger_id': trigger_id,
            },
        )
    body = arguments.get('body', {})
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ToolExecutionError('body must be an object')
    if action == 'create':
        try:
            trigger = runtime.create_trigger(body)
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return (
            runtime.render_trigger(trigger.trigger_id),
            {
                'action': 'remote_trigger',
                'remote_trigger_action': action,
                'trigger_id': trigger.trigger_id,
            },
        )
    trigger_id = _require_string(arguments, 'trigger_id')
    if action == 'update':
        try:
            trigger = runtime.update_trigger(trigger_id, body)
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        return (
            runtime.render_trigger(trigger.trigger_id),
            {
                'action': 'remote_trigger',
                'remote_trigger_action': action,
                'trigger_id': trigger.trigger_id,
            },
        )
    if action == 'run':
        try:
            rendered = runtime.render_run_report(trigger_id, body=body)
        except KeyError as exc:
            raise ToolExecutionError(f'Unknown remote trigger: {trigger_id}') from exc
        return (
            rendered,
            {
                'action': 'remote_trigger',
                'remote_trigger_action': action,
                'trigger_id': trigger_id,
                'body_keys': sorted(str(key) for key in body.keys()),
            },
        )
    raise ToolExecutionError('action must be one of list, get, create, update, or run')


def _team_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_team_runtime(context)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_teams = _coerce_int(arguments, 'max_teams', 50)
    return runtime.render_teams_index(query=query, limit=max_teams)


def _team_get(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_team_runtime(context)
    team_name = _require_string(arguments, 'team_name')
    try:
        return runtime.render_team(team_name)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown team: {team_name}') from exc


def _team_create(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_team_runtime(context)
    team_name = _require_string(arguments, 'team_name')
    description = arguments.get('description')
    members = arguments.get('members', ())
    metadata = arguments.get('metadata')
    if description is not None and not isinstance(description, str):
        raise ToolExecutionError('description must be a string')
    if not isinstance(members, (list, tuple)):
        raise ToolExecutionError('members must be an array of strings')
    if metadata is not None and not isinstance(metadata, dict):
        raise ToolExecutionError('metadata must be an object')
    try:
        team = runtime.create_team(
            team_name,
            description=description,
            members=members,
            metadata=metadata,
        )
    except KeyError as exc:
        raise ToolExecutionError(f'Team already exists: {team_name}') from exc
    return (
        f'created team {team.name}',
        {
            'action': 'team_create',
            'team_name': team.name,
            'member_count': len(team.members),
            'path': str(runtime.state_path),
        },
    )


def _team_delete(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_team_runtime(context)
    team_name = _require_string(arguments, 'team_name')
    try:
        team = runtime.delete_team(team_name)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown team: {team_name}') from exc
    return (
        f'deleted team {team.name}',
        {
            'action': 'team_delete',
            'team_name': team.name,
            'path': str(runtime.state_path),
        },
    )


def _send_message(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_team_runtime(context)
    team_name = _require_string(arguments, 'team_name')
    message = _require_string(arguments, 'message')
    sender = arguments.get('sender', 'agent')
    recipient = arguments.get('recipient')
    metadata = arguments.get('metadata')
    if sender is not None and not isinstance(sender, str):
        raise ToolExecutionError('sender must be a string')
    if recipient is not None and not isinstance(recipient, str):
        raise ToolExecutionError('recipient must be a string')
    if metadata is not None and not isinstance(metadata, dict):
        raise ToolExecutionError('metadata must be an object')
    try:
        stored = runtime.send_message(
            team_name=team_name,
            text=message,
            sender=sender or 'agent',
            recipient=recipient,
            metadata=metadata,
        )
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown team: {team_name}') from exc
    return (
        f'sent message to team {stored.team_name}',
        {
            'action': 'send_message',
            'team_name': stored.team_name,
            'sender': stored.sender,
            'recipient': stored.recipient,
            'message_id': stored.message_id,
            'message_preview': _snapshot_text(stored.text),
            'path': str(runtime.state_path),
        },
    )


def _team_messages(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_team_runtime(context)
    team_name = arguments.get('team_name')
    if team_name is not None and not isinstance(team_name, str):
        raise ToolExecutionError('team_name must be a string')
    limit = _coerce_int(arguments, 'limit', 20)
    try:
        return runtime.render_messages(team_name=team_name, limit=limit)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown team: {team_name}') from exc


def _task_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_task_runtime(context)
    status = arguments.get('status')
    owner = arguments.get('owner')
    actionable_only = arguments.get('actionable_only', False)
    if status is not None and not isinstance(status, str):
        raise ToolExecutionError('status must be a string')
    if owner is not None and not isinstance(owner, str):
        raise ToolExecutionError('owner must be a string')
    if not isinstance(actionable_only, bool):
        raise ToolExecutionError('actionable_only must be a boolean')
    max_tasks = _coerce_int(arguments, 'max_tasks', 50)
    return runtime.render_tasks(
        status=status,
        owner=owner,
        actionable_only=actionable_only,
        limit=max_tasks,
    )


def _task_next(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    runtime = _require_task_runtime(context)
    max_tasks = _coerce_int(arguments, 'max_tasks', 10)
    return runtime.render_next_tasks(limit=max_tasks)


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
    active_form = arguments.get('active_form')
    owner = arguments.get('owner')
    blocks = arguments.get('blocks', [])
    blocked_by = arguments.get('blocked_by', [])
    metadata = arguments.get('metadata')
    if description is not None and not isinstance(description, str):
        raise ToolExecutionError('description must be a string')
    if status is not None and not isinstance(status, str):
        raise ToolExecutionError('status must be a string')
    if priority is not None and not isinstance(priority, str):
        raise ToolExecutionError('priority must be a string')
    if task_id is not None and not isinstance(task_id, str):
        raise ToolExecutionError('task_id must be a string')
    if active_form is not None and not isinstance(active_form, str):
        raise ToolExecutionError('active_form must be a string')
    if owner is not None and not isinstance(owner, str):
        raise ToolExecutionError('owner must be a string')
    if not isinstance(blocks, list):
        raise ToolExecutionError('blocks must be an array of strings')
    if not isinstance(blocked_by, list):
        raise ToolExecutionError('blocked_by must be an array of strings')
    if metadata is not None and not isinstance(metadata, dict):
        raise ToolExecutionError('metadata must be an object')
    mutation = runtime.create_task(
        title=title,
        description=description,
        status=status or 'pending',
        priority=priority,
        task_id=task_id,
        active_form=active_form,
        owner=owner,
        blocks=blocks,
        blocked_by=blocked_by,
        metadata=metadata,
    )
    task = mutation.task
    if task is None:
        raise ToolExecutionError('Task creation succeeded but returned no task object')
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
    active_form = arguments.get('active_form')
    owner = arguments.get('owner')
    blocks = arguments.get('blocks')
    blocked_by = arguments.get('blocked_by')
    metadata = arguments.get('metadata')
    for key, value in (
        ('title', title),
        ('description', description),
        ('status', status),
        ('priority', priority),
        ('active_form', active_form),
        ('owner', owner),
    ):
        if value is not None and not isinstance(value, str):
            raise ToolExecutionError(f'{key} must be a string')
    if blocks is not None and not isinstance(blocks, list):
        raise ToolExecutionError('blocks must be an array of strings')
    if blocked_by is not None and not isinstance(blocked_by, list):
        raise ToolExecutionError('blocked_by must be an array of strings')
    if metadata is not None and not isinstance(metadata, dict):
        raise ToolExecutionError('metadata must be an object')
    try:
        mutation = runtime.update_task(
            task_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            active_form=active_form,
            owner=owner,
            blocks=blocks,
            blocked_by=blocked_by,
            metadata=metadata,
        )
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = mutation.task
    if task is None:
        raise ToolExecutionError('Task update succeeded but returned no task object')
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


def _task_start(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    owner = arguments.get('owner')
    active_form = arguments.get('active_form')
    if owner is not None and not isinstance(owner, str):
        raise ToolExecutionError('owner must be a string')
    if active_form is not None and not isinstance(active_form, str):
        raise ToolExecutionError('active_form must be a string')
    try:
        mutation = runtime.start_task(task_id, owner=owner, active_form=active_form)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = mutation.task
    if task is None:
        raise ToolExecutionError('Task start succeeded but returned no task object')
    return (
        f'started task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_start',
            mutation=mutation,
            task_id=task.task_id,
            task_status=task.status,
            total_tasks=mutation.after_count,
        ),
    )


def _task_complete(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    try:
        mutation = runtime.complete_task(task_id)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = runtime.get_task(task_id)
    if task is None:
        raise ToolExecutionError(f'Task completion succeeded but task {task_id} not found')
    return (
        f'completed task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_complete',
            mutation=mutation,
            task_id=task.task_id,
            task_status=task.status,
            total_tasks=mutation.after_count,
        ),
    )


def _task_block(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    blocked_by = arguments.get('blocked_by')
    reason = arguments.get('reason')
    if blocked_by is not None and not isinstance(blocked_by, list):
        raise ToolExecutionError('blocked_by must be an array of strings')
    if reason is not None and not isinstance(reason, str):
        raise ToolExecutionError('reason must be a string')
    try:
        mutation = runtime.block_task(task_id, blocked_by=blocked_by, reason=reason)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = mutation.task
    if task is None:
        raise ToolExecutionError('Task block succeeded but returned no task object')
    return (
        f'blocked task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_block',
            mutation=mutation,
            task_id=task.task_id,
            task_status=task.status,
            total_tasks=mutation.after_count,
        ),
    )


def _task_cancel(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _ensure_write_allowed(context)
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    reason = arguments.get('reason')
    if reason is not None and not isinstance(reason, str):
        raise ToolExecutionError('reason must be a string')
    try:
        mutation = runtime.cancel_task(task_id, reason=reason)
    except KeyError as exc:
        raise ToolExecutionError(f'Unknown task id: {task_id}') from exc
    task = mutation.task
    if task is None:
        raise ToolExecutionError('Task cancellation succeeded but returned no task object')
    return (
        f'cancelled task {task.task_id}: {task.title} [{task.status}]',
        _task_mutation_metadata(
            action='task_cancel',
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


def _enter_plan_mode(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Enter plan mode — focus on exploration and planning, not execution."""
    if getattr(context, '_plan_mode', False):
        return 'Already in plan mode.'
    context._plan_mode = True
    return (
        'Entered plan mode. Focus on exploring the codebase and creating a plan. '
        'Use read-only tools (Read, Grep, Glob) to investigate. '
        'When your plan is ready, call ExitPlanMode to return to normal mode.'
    )


def _exit_plan_mode(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Exit plan mode and return to normal execution."""
    if not getattr(context, '_plan_mode', False):
        return 'Not currently in plan mode.'
    context._plan_mode = False
    return 'Exited plan mode. You can now execute changes based on your plan.'


def _task_output(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Get output from a background task."""
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')
    block = arguments.get('block', True)
    timeout_ms = arguments.get('timeout', 30000)
    if not isinstance(timeout_ms, (int, float)):
        timeout_ms = 30000
    timeout_ms = max(0, min(timeout_ms, 600000))

    task = runtime.get_task(task_id)
    if task is None:
        raise ToolExecutionError(f'Task not found: {task_id}')

    if task.status in ('completed', 'cancelled', 'failed'):
        output = getattr(task, 'output', '') or getattr(task, 'result', '') or ''
        return (
            f'<retrieval_status>success</retrieval_status>\n'
            f'<task_id>{task_id}</task_id>\n'
            f'<status>{task.status}</status>\n'
            f'<output>{output}</output>'
        )

    if not block:
        return (
            f'<retrieval_status>not_ready</retrieval_status>\n'
            f'<task_id>{task_id}</task_id>\n'
            f'<status>{task.status}</status>'
        )

    # Blocking wait — poll until completion or timeout
    import time as _time
    deadline = _time.monotonic() + (timeout_ms / 1000.0)
    while _time.monotonic() < deadline:
        task = runtime.get_task(task_id)
        if task is None or task.status in ('completed', 'cancelled', 'failed'):
            break
        _time.sleep(0.1)

    if task is None:
        raise ToolExecutionError(f'Task disappeared: {task_id}')

    if task.status in ('completed', 'cancelled', 'failed'):
        output = getattr(task, 'output', '') or getattr(task, 'result', '') or ''
        return (
            f'<retrieval_status>success</retrieval_status>\n'
            f'<task_id>{task_id}</task_id>\n'
            f'<status>{task.status}</status>\n'
            f'<output>{output}</output>'
        )

    return (
        f'<retrieval_status>timeout</retrieval_status>\n'
        f'<task_id>{task_id}</task_id>\n'
        f'<status>{task.status}</status>'
    )


def _task_stop(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Stop a running background task."""
    runtime = _require_task_runtime(context)
    task_id = _require_string(arguments, 'task_id')

    task = runtime.get_task(task_id)
    if task is None:
        raise ToolExecutionError(f'Task not found: {task_id}')

    if task.status not in ('pending', 'in_progress', 'running'):
        raise ToolExecutionError(
            f'Task {task_id} is not running (status: {task.status})'
        )

    # Cancel the task via the runtime
    mutation = runtime.cancel_task(task_id, reason='Stopped by TaskStop tool')
    description = getattr(task, 'title', '') or getattr(task, 'description', '') or task_id
    return (
        f'Successfully stopped task: {task_id} ({description})',
        {'action': 'task_stop', 'task_id': task_id},
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
                    except (KeyError, ValueError, OSError):
                        pass
                    try:
                        key.fileobj.close()
                    except OSError:
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
        except OSError:
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


def _agent_tool_placeholder(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    raise ToolExecutionError(
        'Agent/delegate_agent must be handled by the runtime and is not available as a standalone tool handler'
    )


def _execute_skill(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str | tuple[str, dict[str, Any]]:
    """Execute a skill (slash command) from the Skill tool.

    The skill tool must be handled specially by the runtime because it
    needs access to the agent to invoke slash commands.  This handler
    is a placeholder that raises — the runtime intercepts `Skill` tool
    calls before they reach this handler.
    """
    raise ToolExecutionError(
        'Skill must be handled by the runtime and is not available as a standalone tool handler'
    )


def _self_score(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Score own response quality — reward model for self-evaluation."""
    text = arguments.get('response_text', '')
    used_tools = arguments.get('used_tools', False)
    score = 50  # baseline

    if used_tools:
        score += 20

    # Conciseness: under 15 lines
    lines = [l for l in text.split('\n') if l.strip()]
    if len(lines) <= 15:
        score += 10

    # Anti-pattern checks
    import re
    text_lower = text.lower()
    if re.search(r'great question|that.s interesting|as an ai|i find that', text_lower):
        score -= 15
    if text.rstrip().endswith('?'):
        score -= 10
    if re.search(r'shall i|should i|would you like|do you want|can i proceed', text_lower):
        score -= 10
    if re.search(r'what would you|standing by|your call|let me know', text_lower):
        score -= 10

    # Bonus for action-oriented language
    if re.search(r'done|fixed|saved|created|computed|result', text_lower):
        score += 10

    score = max(0, min(100, score))

    verdict = 'GOOD' if score >= 70 else 'REVISE' if score >= 50 else 'POOR'
    feedback = []
    if not used_tools:
        feedback.append('Consider using a tool instead of just explaining')
    if len(lines) > 15:
        feedback.append(f'Too verbose ({len(lines)} lines, aim for <15)')
    if score < 70:
        feedback.append('Check for anti-patterns: filler, trailing questions, permission asking')

    return f'Score: {score}/100 ({verdict})\n' + ('\n'.join(f'- {f}' for f in feedback) if feedback else 'No issues detected.')


def _lattice_solve(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    problem = arguments.get('problem', '')
    if not isinstance(problem, str) or not problem.strip():
        raise ToolExecutionError('problem must be a non-empty string')

    samples = arguments.get('samples', 10000)
    if not isinstance(samples, int):
        samples = 10000
    samples = max(1000, min(1000000, samples))

    from .lattice_solver import parse_and_solve
    return parse_and_solve(problem, samples)


def _lattice_boolean_solve(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    problem = arguments.get('problem', '')
    if not isinstance(problem, str) or not problem.strip():
        raise ToolExecutionError('problem must be a non-empty string')

    samples = arguments.get('samples', 5000)
    if not isinstance(samples, int):
        samples = 5000
    samples = max(500, min(100000, samples))

    from .lattice_boolean_solve import parse_and_boolean_solve
    return parse_and_boolean_solve(problem, samples)


def _lattice_sector_solve(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    sectors_raw = arguments.get('sectors', {})
    if not isinstance(sectors_raw, dict) or not sectors_raw:
        raise ToolExecutionError('sectors must be a non-empty object mapping names to expressions')

    bounds_str = arguments.get('bounds', '')
    if not isinstance(bounds_str, str) or not bounds_str.strip():
        raise ToolExecutionError('bounds must be a non-empty string like "[-5,5] x [-5,5]"')

    samples = arguments.get('samples', 5000)
    if not isinstance(samples, int):
        samples = 5000
    samples = max(1000, min(100000, samples))

    from .lattice_solver import _extract_bounds, _build_cost_fn
    bounds = _extract_bounds(bounds_str)
    if not bounds:
        raise ToolExecutionError(f'Could not parse bounds from: {bounds_str}')

    dims = len(bounds)
    sector_fns = {}
    for name, expr in sectors_raw.items():
        fn = _build_cost_fn(expr, dims)
        if fn is None:
            raise ToolExecutionError(f'Sector "{name}": expression does not reference x0..x{dims-1}: {expr}')
        sector_fns[name] = fn

    from .lattice_sectors import SectorSolver
    solver = SectorSolver(sector_fns)
    result = solver.solve(bounds, samples)
    return f'Sector Decomposition ({len(sector_fns)} sectors, {dims}D)\n{"="*50}\n{result.to_text()}'


def _lattice_maxent(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    constraints_raw = arguments.get('constraints', [])
    if not isinstance(constraints_raw, list) or not constraints_raw:
        raise ToolExecutionError('constraints must be a non-empty list of {name, expr, target} objects')

    bounds_str = arguments.get('bounds', '')
    if not isinstance(bounds_str, str) or not bounds_str.strip():
        raise ToolExecutionError('bounds must be a non-empty string like "[0,10] x [0,10]"')

    samples = arguments.get('samples', 5000)
    if not isinstance(samples, int):
        samples = 5000
    samples = max(1000, min(100000, samples))

    from .lattice_solver import _extract_bounds, _build_cost_fn
    bounds = _extract_bounds(bounds_str)
    if not bounds:
        raise ToolExecutionError(f'Could not parse bounds from: {bounds_str}')

    dims = len(bounds)
    constraints = []
    for c in constraints_raw:
        name = c.get('name', '')
        expr = c.get('expr', '')
        target = c.get('target', 0.0)
        if not name or not expr:
            raise ToolExecutionError(f'Each constraint needs name and expr, got: {c}')
        fn = _build_cost_fn(expr, dims)
        if fn is None:
            raise ToolExecutionError(f'Constraint "{name}": expression does not reference x0..x{dims-1}: {expr}')
        constraints.append((name, fn, float(target)))

    from .lattice_maxent import maxent_solve
    result = maxent_solve(constraints, bounds, samples)
    return f'MaxEnt Constraint Solver ({len(constraints)} constraints, {dims}D)\n{"="*50}\n{result.to_text()}'


def _lattice_nn_predict(
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> str:
    features = arguments.get('features', {})
    if not isinstance(features, dict) or not features:
        raise ToolExecutionError('features must be a non-empty object mapping names to numbers')

    # Ensure values are floats
    for k, v in features.items():
        if not isinstance(v, (int, float)):
            raise ToolExecutionError(f'Feature "{k}" must be a number, got {type(v).__name__}')
    features = {k: float(v) for k, v in features.items()}

    outcome = arguments.get('outcome')
    model_path = arguments.get('model_path')
    samples = arguments.get('samples', 2000)
    if not isinstance(samples, int):
        samples = 2000
    samples = max(500, min(50000, samples))

    from .lattice_nn import LatticeNN
    feature_names = sorted(features.keys())
    nn = LatticeNN(feature_names)

    # Load saved weights if path provided
    if model_path and os.path.exists(model_path):
        nn.load(model_path)

    result = nn.predict(features, samples)
    output = f'Lattice Neural Network ({len(feature_names)} features)\n{"="*50}\n{result.to_text()}'

    # Train if outcome provided
    if outcome is not None:
        outcome_val = float(outcome)
        nn.train(features, outcome_val)
        output += f'\n\nTrained on outcome={outcome_val:.2f} (error={abs(outcome_val - result.probability):.4f})'

    # Save if path provided
    if model_path:
        nn.save(model_path)
        output += f'\nModel saved to {model_path}'

    output += f'\n\n{nn.status()}'
    return output


def _lsp_query(arguments: dict[str, Any], context: ToolExecutionContext):
    runtime = _require_lsp_runtime(context)
    operation = _require_string(arguments, 'operation')
    file_path = _require_string(arguments, 'file_path')
    line = _coerce_int(arguments, 'line', 1)
    character = _coerce_int(arguments, 'character', 1)
    query = arguments.get('query')
    if query is not None and not isinstance(query, str):
        raise ToolExecutionError('query must be a string')
    max_results = _coerce_int(arguments, 'max_results', 50)
    try:
        result = runtime.query(
            operation,
            file_path=file_path,
            line=line,
            character=character,
            query=query,
            max_results=max_results,
        )
    except KeyError as exc:
        raise ToolExecutionError(str(exc)) from exc
    return (
        result.content,
        {
            'action': 'lsp_query',
            'operation': result.operation,
            'file_path': file_path,
            'line': line,
            'character': character,
            'result_count': result.result_count,
            'file_count': result.file_count,
            'symbol_name': result.symbol_name,
        },
    )


def _require_account_runtime(context: ToolExecutionContext):
    if context.account_runtime is None:
        raise ToolExecutionError('No local account runtime is available.')
    return context.account_runtime


def _require_ask_user_runtime(context: ToolExecutionContext):
    if context.ask_user_runtime is None:
        raise ToolExecutionError('No local ask-user runtime is available.')
    return context.ask_user_runtime


def _require_search_runtime(context: ToolExecutionContext):
    if context.search_runtime is None or not context.search_runtime.has_search_runtime():
        raise ToolExecutionError(
            'No local search provider is available. Add a .claw-search.json or .claude/search.json manifest, '
            'or set SEARXNG_BASE_URL, BRAVE_SEARCH_API_KEY, or TAVILY_API_KEY.'
        )
    return context.search_runtime


def _require_config_runtime(context: ToolExecutionContext):
    if context.config_runtime is None:
        raise ToolExecutionError('No local config runtime is available.')
    return context.config_runtime


def _require_lsp_runtime(context: ToolExecutionContext):
    if context.lsp_runtime is None or not context.lsp_runtime.has_lsp_support():
        raise ToolExecutionError(
            'No local LSP runtime is available. Add supported source files to the workspace or a .claw-lsp.json manifest.'
        )
    return context.lsp_runtime


def _require_mcp_runtime(context: ToolExecutionContext):
    if (
        context.mcp_runtime is None
        or (
            not context.mcp_runtime.resources
            and not context.mcp_runtime.servers
        )
    ):
        raise ToolExecutionError(
            'No MCP runtime is available. Add a .claw-mcp.json, .mcp.json, or mcpServers manifest first.'
        )
    return context.mcp_runtime


def _require_remote_runtime(context: ToolExecutionContext):
    if context.remote_runtime is None:
        raise ToolExecutionError('Local remote runtime is not available.')
    return context.remote_runtime


def _require_remote_trigger_runtime(context: ToolExecutionContext):
    if context.remote_trigger_runtime is None:
        raise ToolExecutionError('Local remote trigger runtime is not available.')
    return context.remote_trigger_runtime


def _require_plan_runtime(context: ToolExecutionContext):
    if context.plan_runtime is None:
        raise ToolExecutionError('Local plan runtime is not available.')
    return context.plan_runtime


def _require_task_runtime(context: ToolExecutionContext):
    if context.task_runtime is None:
        raise ToolExecutionError('Local task runtime is not available.')
    return context.task_runtime


def _require_team_runtime(context: ToolExecutionContext):
    if context.team_runtime is None:
        raise ToolExecutionError('Local team runtime is not available.')
    return context.team_runtime


def _require_workflow_runtime(context: ToolExecutionContext):
    if context.workflow_runtime is None or not context.workflow_runtime.has_workflows():
        raise ToolExecutionError('Local workflow runtime is not available.')
    return context.workflow_runtime


def _require_worktree_runtime(context: ToolExecutionContext):
    if context.worktree_runtime is None:
        raise ToolExecutionError('Local worktree runtime is not available.')
    return context.worktree_runtime


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


def _config_mutation_metadata(
    mutation,
    value: Any,
) -> dict[str, Any]:
    try:
        relative_path = str(Path(mutation.store_path).relative_to(Path.cwd()))
    except ValueError:
        relative_path = str(mutation.store_path)
    return {
        'action': 'config_set',
        'path': relative_path,
        'source_name': mutation.source_name,
        'key_path': mutation.key_path,
        'before_sha256': mutation.before_sha256,
        'after_sha256': mutation.after_sha256,
        'before_preview': mutation.before_preview,
        'after_preview': mutation.after_preview,
        'effective_key_count': mutation.effective_key_count,
        'value_preview': _snapshot_text(
            json.dumps(value, ensure_ascii=True, sort_keys=True)
            if not isinstance(value, str)
            else value
        ),
    }


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
        except OSError:
            remainder = ''
        if not remainder:
            try:
                selector.unregister(key.fileobj)
            except (KeyError, ValueError, OSError):
                pass
            try:
                key.fileobj.close()
            except OSError:
                pass
            continue
        if key.data == 'stdout':
            stdout_chunks.append(remainder)
        else:
            stderr_chunks.append(remainder)
        try:
            selector.unregister(key.fileobj)
        except (KeyError, ValueError, OSError):
            pass
        try:
            key.fileobj.close()
        except OSError:
            pass


_SENSITIVE_ENV_KEYWORDS = (
    'SECRET',
    'TOKEN',
    'PASSWORD',
    'PRIVATE_KEY',
    'API_KEY',
    'CREDENTIAL',
    'AUTH',
)


def _is_sensitive_env_var(name: str) -> bool:
    """Return True if the environment variable name likely contains a secret."""
    upper = name.upper()
    return any(keyword in upper for keyword in _SENSITIVE_ENV_KEYWORDS)


def _build_subprocess_env(context: ToolExecutionContext) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not _is_sensitive_env_var(key)
    }
    # Mirror utils/shell/bashProvider.ts: session env vars (set via /env)
    # apply to spawned children, layered above the parent env but below
    # explicit per-call extras.
    for key, value in get_session_env_vars().items():
        env[key] = value
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


# =============================================================================
# New tool handlers — git, file-management, patch, image, run_tests, memory
# =============================================================================

import base64 as _base64
import pathlib as _pathlib
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile


def _cwd(context: ToolExecutionContext) -> _pathlib.Path:
    """Return the workspace root as a Path."""
    return _pathlib.Path(getattr(context, 'cwd', '.') or '.').resolve()


def _safe_path(context: ToolExecutionContext, rel: str) -> _pathlib.Path:
    """Resolve rel relative to workspace and verify it stays inside."""
    base = _cwd(context)
    p = (base / rel).resolve()
    if not str(p).startswith(str(base)):
        raise ToolExecutionError(f'Path escapes workspace: {rel}')
    return p


# ---------------------------------------------------------------------------
# Git tools
# ---------------------------------------------------------------------------

def _git_run(args: list[str], cwd: _pathlib.Path, timeout: int = 30) -> tuple[int, str]:
    """Run a git command; return (returncode, combined stdout+stderr)."""
    try:
        r = _subprocess.run(
            ['git'] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (r.stdout or '') + (r.stderr or '')
        return r.returncode, out.strip()
    except FileNotFoundError:
        return 1, 'git not found in PATH'
    except _subprocess.TimeoutExpired:
        return 1, f'git timed out after {timeout}s'


def _git_status(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    cwd = _cwd(context)
    rc, branch = _git_run(['branch', '--show-current'], cwd)
    rc2, out = _git_run(['status', '--short', '--branch'], cwd)
    if rc2 != 0:
        raise ToolExecutionError(f'git status failed: {out}')
    return out if out else 'working tree clean'


def _git_diff(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    cwd       = _cwd(context)
    staged    = arguments.get('staged', False)
    path      = arguments.get('path', '')
    base      = arguments.get('base', '')
    head      = arguments.get('head', 'HEAD')
    max_lines = int(arguments.get('max_lines', 400))

    args = ['diff']
    if staged:
        args.append('--cached')
    if base:
        args += [f'{base}..{head}']
    args += ['--']
    if path:
        args.append(path)

    rc, out = _git_run(args, cwd)
    if rc != 0:
        raise ToolExecutionError(f'git diff failed: {out}')
    if not out:
        return 'no differences'
    lines = out.splitlines()
    if len(lines) > max_lines:
        out = '\n'.join(lines[:max_lines]) + f'\n… ({len(lines) - max_lines} more lines truncated)'
    return out


def _git_log(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    cwd     = _cwd(context)
    limit   = int(arguments.get('limit', 20))
    path    = arguments.get('path', '')
    oneline = arguments.get('oneline', True)

    args = ['log', f'-{limit}']
    if oneline:
        args.append('--oneline')
    else:
        args += ['--pretty=format:%h %an %ar  %s']
    args += ['--']
    if path:
        args.append(path)

    rc, out = _git_run(args, cwd)
    if rc != 0:
        raise ToolExecutionError(f'git log failed: {out}')
    return out if out else 'no commits'


def _git_commit(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    cwd     = _cwd(context)
    message = arguments.get('message', '').strip()
    paths   = arguments.get('paths') or []

    if not message:
        raise ToolExecutionError('commit message is required')

    # Stage
    if paths:
        for p in paths:
            rc, out = _git_run(['add', '--', p], cwd)
            if rc != 0:
                raise ToolExecutionError(f'git add {p} failed: {out}')
    else:
        rc, out = _git_run(['add', '-u'], cwd)
        if rc != 0:
            raise ToolExecutionError(f'git add -u failed: {out}')

    # Check something is staged
    rc, staged = _git_run(['diff', '--cached', '--name-only'], cwd)
    if not staged.strip():
        return 'nothing to commit (no tracked changes staged)'

    # Commit
    rc, out = _git_run(['commit', '-m', message], cwd)
    if rc != 0:
        raise ToolExecutionError(f'git commit failed: {out}')
    return out


# ---------------------------------------------------------------------------
# File management
# ---------------------------------------------------------------------------

def _move_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    src  = _safe_path(context, arguments['source'])
    dest = _safe_path(context, arguments['destination'])
    if not src.exists():
        raise ToolExecutionError(f'source does not exist: {arguments["source"]}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    _shutil.move(str(src), str(dest))
    return f'moved {arguments["source"]} → {arguments["destination"]}'


def _delete_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    p = _safe_path(context, arguments['path'])
    if not p.exists():
        raise ToolExecutionError(f'file not found: {arguments["path"]}')
    if p.is_dir():
        raise ToolExecutionError('delete_file refuses directories — use bash rm -rf if intentional')
    p.unlink()
    return f'deleted {arguments["path"]}'


def _make_dir(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    p = _safe_path(context, arguments['path'])
    p.mkdir(parents=True, exist_ok=True)
    return f'created {arguments["path"]}'


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------

def _patch_file(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    """Apply a unified diff patch using the `patch` CLI."""
    path  = _safe_path(context, arguments['path'])
    patch = arguments.get('patch', '')
    fuzz  = int(arguments.get('fuzz', 2))

    if not patch.strip():
        raise ToolExecutionError('patch is empty')
    if not path.exists():
        raise ToolExecutionError(f'target file not found: {arguments["path"]}')

    # Write patch to temp file
    with _tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as tf:
        tf.write(patch)
        patch_path = tf.name

    try:
        r = _subprocess.run(
            ['patch', f'--fuzz={fuzz}', '--forward', str(path), patch_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode != 0:
            raise ToolExecutionError(f'patch failed: {out.strip()}')
        return out.strip() or f'patch applied to {arguments["path"]}'
    finally:
        _pathlib.Path(patch_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Image read
# ---------------------------------------------------------------------------

_SUPPORTED_IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
_IMAGE_MIME = {
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.webp': 'image/webp',
}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _image_read(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    raw = arguments.get('path', '')
    # Allow absolute paths (screenshots outside workspace)
    p = _pathlib.Path(raw).expanduser().resolve()
    if not p.exists():
        # Try workspace-relative
        try:
            p = _safe_path(context, raw)
        except Exception:
            pass
    if not p.exists():
        raise ToolExecutionError(f'image not found: {raw}')

    ext = p.suffix.lower()
    if ext not in _SUPPORTED_IMAGE_TYPES:
        raise ToolExecutionError(f'unsupported image type {ext}. Supported: {", ".join(_SUPPORTED_IMAGE_TYPES)}')

    size = p.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        raise ToolExecutionError(f'image too large ({size // 1024}KB > 5MB limit)')

    mime    = _IMAGE_MIME[ext]
    data    = _base64.b64encode(p.read_bytes()).decode()
    data_uri = f'data:{mime};base64,{data}'
    return (
        f'image:{p.name} ({size // 1024}KB {mime})\n'
        f'data_uri:{data_uri}'
    )


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

def _run_tests(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    cwd     = _cwd(context)
    path    = arguments.get('path', 'tests/')
    pattern = arguments.get('pattern', '')
    runner  = arguments.get('runner', 'pytest')
    timeout = int(arguments.get('timeout', 60))

    if runner == 'pytest':
        cmd = ['python3', '-m', 'pytest', '-v', '--tb=short', '--no-header', '-q']
        if pattern:
            cmd += ['-k', pattern]
        cmd.append(path)
    elif runner == 'unittest':
        cmd = ['python3', '-m', 'unittest', 'discover', path]
    elif runner == 'npm':
        cmd = ['npm', 'test', '--', '--watchAll=false']
    else:
        raise ToolExecutionError(f'unknown runner: {runner}')

    try:
        r = _subprocess.run(
            cmd, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
    except _subprocess.TimeoutExpired:
        raise ToolExecutionError(f'tests timed out after {timeout}s')
    except FileNotFoundError as e:
        raise ToolExecutionError(f'runner not found: {e}')

    out = (r.stdout or '') + (r.stderr or '')

    # Parse pytest summary line
    summary = ''
    for line in reversed(out.splitlines()):
        if _re.search(r'\d+ passed|\d+ failed|\d+ error', line):
            summary = line.strip()
            break

    status = 'PASS' if r.returncode == 0 else 'FAIL'
    result = f'{status}  {summary}\n\n{out[-3000:]}' if len(out) > 3000 else f'{status}  {summary}\n\n{out}'
    if r.returncode != 0:
        raise ToolExecutionError(result)
    return result


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

_MEMORY_DIR = _pathlib.Path.home() / '.latti' / 'memory'


def _memory_key_path(key: str) -> _pathlib.Path:
    # Sanitize key to safe filename
    safe = _re.sub(r'[^a-zA-Z0-9_\-.]', '_', key)
    if not safe:
        raise ToolExecutionError('memory key must be non-empty')
    return _MEMORY_DIR / f'{safe}.md'


def _memory_write(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    key     = arguments.get('key', '').strip()
    content = arguments.get('content', '')
    append  = arguments.get('append', False)

    p = _memory_key_path(key)
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    if append and p.exists():
        existing = p.read_text(encoding='utf-8')
        p.write_text(existing + '\n' + content, encoding='utf-8')
        return f'appended to memory:{key} ({p.stat().st_size} bytes total)'
    else:
        p.write_text(content, encoding='utf-8')
        return f'wrote memory:{key} ({len(content)} bytes)'


def _memory_read(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    key = arguments.get('key', '').strip()
    p   = _memory_key_path(key)
    if not p.exists():
        return f'memory:{key} — not found'
    return p.read_text(encoding='utf-8')


def _memory_list(arguments: dict[str, Any], context: ToolExecutionContext) -> str:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    keys = sorted(p.stem for p in _MEMORY_DIR.glob('*.md'))
    if not keys:
        return 'no memory entries'
    return '\n'.join(keys)
