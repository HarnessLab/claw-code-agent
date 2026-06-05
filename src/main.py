from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from dataclasses import replace
import json
from typing import Callable

from .background_runtime import BackgroundSessionRuntime, build_background_worker_command
from .account_runtime import AccountRuntime
from .ask_user_runtime import AskUserRuntime
from .agent_registry import (
    create_agent_definition,
    delete_agent_definition,
    normalize_mutable_source,
    render_agent_mutation,
    update_agent_definition,
)
from .agent_runtime import LocalCodingAgent
from .agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    BudgetConfig,
    ModelConfig,
    ModelPricing,
    OutputSchemaConfig,
)
from .bootstrap_graph import build_bootstrap_graph
from .command_graph import build_command_graph
from .commands import execute_command, get_command, get_commands, render_command_index
from .config_runtime import ConfigRuntime
from .lsp_runtime import LSPRuntime
from .mcp_runtime import MCPRuntime
from .parity_audit import run_parity_audit
from .permissions import ToolPermissionContext
from .port_manifest import build_port_manifest
from .query_engine import QueryEnginePort
from .remote_runtime import (
    RemoteRuntime,
    run_deep_link_mode,
    run_direct_connect_mode,
    run_remote_mode,
    run_ssh_mode,
    run_teleport_mode,
)
from .remote_trigger_runtime import RemoteTriggerRuntime
from .search_runtime import SearchRuntime
from .team_runtime import TeamRuntime
from .task_runtime import TaskRuntime
from .workflow_runtime import WorkflowRuntime
from .worktree_runtime import WorktreeRuntime
from .runtime import PortRuntime
from .session_store import (
    StoredAgentSession,
    deserialize_model_config,
    deserialize_runtime_config,
    load_agent_session,
    load_session,
)
from .setup import run_setup
from .tui_supervisor import append_worker_event, run_background_turn, save_worker_result
from .tool_pool import assemble_tool_pool
from .tools import execute_tool, get_tool, get_tools, render_tool_index


def _add_agent_common_args(parser: argparse.ArgumentParser, *, include_backend: bool) -> None:
    parser.add_argument('--model', default=os.environ.get('OPENAI_MODEL', 'Qwen/Qwen3-Coder-30B-A3B-Instruct'))
    if include_backend:
        parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1'))
        parser.add_argument('--api-key', default=os.environ.get('OPENAI_API_KEY', 'local-token'))
        parser.add_argument('--temperature', type=float, default=0.0)
        parser.add_argument('--timeout-seconds', type=float, default=120.0)
        parser.add_argument('--input-cost-per-million', type=float, default=0.0)
        parser.add_argument('--output-cost-per-million', type=float, default=0.0)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--add-dir', action='append', default=[])
    parser.add_argument('--disable-claude-md', action='store_true')
    parser.add_argument('--allow-write', action='store_true')
    parser.add_argument('--allow-shell', action='store_true')
    parser.add_argument('--unsafe', action='store_true')
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--auto-snip-threshold', type=int)
    parser.add_argument('--auto-compact-threshold', type=int)
    parser.add_argument('--compact-preserve-messages', type=int, default=4)
    parser.add_argument('--max-total-tokens', type=int)
    parser.add_argument('--max-input-tokens', type=int)
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--max-reasoning-tokens', type=int)
    parser.add_argument('--max-budget-usd', type=float)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--max-delegated-tasks', type=int)
    parser.add_argument('--max-model-calls', type=int)
    parser.add_argument('--max-session-turns', type=int)
    parser.add_argument('--max-output-chars', type=int, default=50000)
    parser.add_argument('--command-timeout', type=float,
                        default=float(os.environ.get('LATTI_COMMAND_TIMEOUT', '120')),
                        help='Bash/shell command timeout in seconds (default 120, env: LATTI_COMMAND_TIMEOUT)')
    parser.add_argument('--response-schema-file')
    parser.add_argument('--response-schema-name')
    parser.add_argument('--response-schema-strict', action='store_true')
    parser.add_argument('--scratchpad-root')
    parser.add_argument('--system-prompt')
    parser.add_argument('--append-system-prompt')
    parser.add_argument('--override-system-prompt')


def _build_runtime_config(args: argparse.Namespace) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        cwd=Path(args.cwd).resolve(),
        max_turns=getattr(args, 'max_turns', 12),
        max_output_chars=getattr(args, 'max_output_chars', 50000),
        command_timeout_seconds=float(getattr(args, 'command_timeout', None) or
                                      os.environ.get('LATTI_COMMAND_TIMEOUT', '120')),
        permissions=AgentPermissions(
            allow_file_write=args.allow_write,
            allow_shell_commands=args.allow_shell,
            allow_destructive_shell_commands=args.unsafe,
        ),
        stream_model_responses=bool(getattr(args, 'stream', False)),
        auto_snip_threshold_tokens=getattr(args, 'auto_snip_threshold', None),
        auto_compact_threshold_tokens=getattr(args, 'auto_compact_threshold', None),
        compact_preserve_messages=max(0, int(getattr(args, 'compact_preserve_messages', 4))),
        additional_working_directories=tuple(Path(path).resolve() for path in args.add_dir),
        disable_claude_md_discovery=args.disable_claude_md,
        budget_config=BudgetConfig(
            max_total_tokens=getattr(args, 'max_total_tokens', None),
            max_input_tokens=getattr(args, 'max_input_tokens', None),
            max_output_tokens=getattr(args, 'max_output_tokens', None),
            max_reasoning_tokens=getattr(args, 'max_reasoning_tokens', None),
            max_total_cost_usd=getattr(args, 'max_budget_usd', None),
            max_tool_calls=getattr(args, 'max_tool_calls', None),
            max_delegated_tasks=getattr(args, 'max_delegated_tasks', None),
            max_model_calls=getattr(args, 'max_model_calls', None),
            max_session_turns=getattr(args, 'max_session_turns', None),
        ),
        output_schema=_load_output_schema_config(args),
        session_directory=(Path('.port_sessions') / 'agent').resolve(),
        scratchpad_root=(
            Path(getattr(args, 'scratchpad_root')).resolve()
            if getattr(args, 'scratchpad_root', None)
            else (Path('.port_sessions') / 'scratchpad').resolve()
        ),
    )


def _build_model_config(args: argparse.Namespace) -> ModelConfig:
    return ModelConfig(
        model=args.model,
        base_url=getattr(args, 'base_url', os.environ.get('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')),
        api_key=getattr(args, 'api_key', os.environ.get('OPENAI_API_KEY', 'local-token')),
        temperature=getattr(args, 'temperature', 0.0),
        timeout_seconds=getattr(args, 'timeout_seconds', 120.0),
        pricing=ModelPricing(
            input_cost_per_million_tokens_usd=float(
                getattr(args, 'input_cost_per_million', 0.0) or 0.0
            ),
            output_cost_per_million_tokens_usd=float(
                getattr(args, 'output_cost_per_million', 0.0) or 0.0
            ),
        ),
    )


def _load_output_schema_config(args: argparse.Namespace) -> OutputSchemaConfig | None:
    schema_file = getattr(args, 'response_schema_file', None)
    if not schema_file:
        return None
    payload = json.loads(Path(schema_file).read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('response schema file must contain a top-level JSON object')
    name = getattr(args, 'response_schema_name', None) or Path(schema_file).stem
    return OutputSchemaConfig(
        name=name,
        schema=payload,
        strict=bool(getattr(args, 'response_schema_strict', False)),
    )


def _add_agent_management_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--cwd', default='.')
    parser.add_argument(
        '--source',
        default='project',
        choices=('project', 'user', 'auto', 'projectSettings', 'userSettings'),
    )
    parser.add_argument('--description')
    parser.add_argument('--prompt')
    parser.add_argument('--prompt-file')
    parser.add_argument('--tools')
    parser.add_argument('--model')
    parser.add_argument('--color')
    parser.add_argument('--permission-mode')
    parser.add_argument('--max-turns', type=int)
    parser.add_argument('--initial-prompt')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--one-shot', action='store_true')
    parser.add_argument('--omit-claude-md', action='store_true')
    parser.add_argument('--overwrite', action='store_true')


def _resolve_agent_prompt_text(args: argparse.Namespace) -> str | None:
    prompt_text = getattr(args, 'prompt', None)
    prompt_file = getattr(args, 'prompt_file', None)
    if prompt_text and prompt_file:
        raise ValueError('Specify only one of --prompt or --prompt-file')
    if prompt_file:
        return Path(prompt_file).read_text(encoding='utf-8')
    return prompt_text


def _parse_tools_flag(raw_tools: str | None) -> tuple[str, ...] | None:
    if raw_tools is None:
        return None
    cleaned = raw_tools.strip()
    if not cleaned or cleaned == '*':
        return None
    return tuple(item.strip() for item in cleaned.split(',') if item.strip())


def _build_agent(args: argparse.Namespace) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=_build_model_config(args),
        runtime_config=_build_runtime_config(args),
        custom_system_prompt=args.system_prompt,
        append_system_prompt=args.append_system_prompt,
        override_system_prompt=args.override_system_prompt,
    )


def _append_agent_forwarded_args(
    command: list[str],
    args: argparse.Namespace,
    *,
    include_backend: bool,
) -> None:
    command.extend(['--cwd', str(args.cwd)])
    command.extend(['--max-turns', str(getattr(args, 'max_turns', 12))])
    if include_backend:
        command.extend(['--model', str(args.model)])
        command.extend(['--base-url', str(args.base_url)])
        command.extend(['--api-key', str(args.api_key)])
        command.extend(['--temperature', str(args.temperature)])
        command.extend(['--timeout-seconds', str(args.timeout_seconds)])
        command.extend(['--input-cost-per-million', str(args.input_cost_per_million)])
        command.extend(['--output-cost-per-million', str(args.output_cost_per_million)])
    else:
        command.extend(['--model', str(args.model)])
    for path in getattr(args, 'add_dir', []):
        command.extend(['--add-dir', str(path)])
    for flag in (
        ('--disable-claude-md', getattr(args, 'disable_claude_md', False)),
        ('--allow-write', getattr(args, 'allow_write', False)),
        ('--allow-shell', getattr(args, 'allow_shell', False)),
        ('--unsafe', getattr(args, 'unsafe', False)),
        ('--stream', getattr(args, 'stream', False)),
        ('--show-transcript', getattr(args, 'show_transcript', False)),
        (
            '--response-schema-strict',
            getattr(args, 'response_schema_strict', False),
        ),
    ):
        if flag[1]:
            command.append(flag[0])
    for name, value in (
        ('--auto-snip-threshold', getattr(args, 'auto_snip_threshold', None)),
        ('--auto-compact-threshold', getattr(args, 'auto_compact_threshold', None)),
        ('--compact-preserve-messages', getattr(args, 'compact_preserve_messages', None)),
        ('--max-total-tokens', getattr(args, 'max_total_tokens', None)),
        ('--max-input-tokens', getattr(args, 'max_input_tokens', None)),
        ('--max-output-tokens', getattr(args, 'max_output_tokens', None)),
        ('--max-reasoning-tokens', getattr(args, 'max_reasoning_tokens', None)),
        ('--max-budget-usd', getattr(args, 'max_budget_usd', None)),
        ('--max-tool-calls', getattr(args, 'max_tool_calls', None)),
        ('--max-delegated-tasks', getattr(args, 'max_delegated_tasks', None)),
        ('--max-model-calls', getattr(args, 'max_model_calls', None)),
        ('--max-session-turns', getattr(args, 'max_session_turns', None)),
        ('--response-schema-file', getattr(args, 'response_schema_file', None)),
        ('--response-schema-name', getattr(args, 'response_schema_name', None)),
        ('--scratchpad-root', getattr(args, 'scratchpad_root', None)),
        ('--system-prompt', getattr(args, 'system_prompt', None)),
        ('--append-system-prompt', getattr(args, 'append_system_prompt', None)),
        ('--override-system-prompt', getattr(args, 'override_system_prompt', None)),
    ):
        if value is not None:
            command.extend([name, str(value)])


def _add_agent_resume_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('session_id')
    parser.add_argument('prompt')
    parser.add_argument('--max-turns', type=int)
    parser.add_argument('--show-transcript', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    parser.add_argument('--api-key')
    parser.add_argument('--temperature', type=float)
    parser.add_argument('--timeout-seconds', type=float)
    parser.add_argument('--input-cost-per-million', type=float)
    parser.add_argument('--output-cost-per-million', type=float)
    parser.add_argument('--allow-write', action='store_true')
    parser.add_argument('--allow-shell', action='store_true')
    parser.add_argument('--unsafe', action='store_true')
    parser.add_argument('--stream', action='store_true')
    parser.add_argument('--auto-snip-threshold', type=int)
    parser.add_argument('--auto-compact-threshold', type=int)
    parser.add_argument('--compact-preserve-messages', type=int)
    parser.add_argument('--max-total-tokens', type=int)
    parser.add_argument('--max-input-tokens', type=int)
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--max-reasoning-tokens', type=int)
    parser.add_argument('--max-budget-usd', type=float)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--max-delegated-tasks', type=int)
    parser.add_argument('--max-model-calls', type=int)
    parser.add_argument('--max-session-turns', type=int)
    parser.add_argument('--response-schema-file')
    parser.add_argument('--response-schema-name')
    parser.add_argument('--response-schema-strict', action='store_true')
    parser.add_argument('--scratchpad-root')


def _launch_background_agent(args: argparse.Namespace) -> int:
    background_runtime = BackgroundSessionRuntime()
    background_id = background_runtime.create_id()
    forwarded_args: list[str] = []
    _append_agent_forwarded_args(forwarded_args, args, include_backend=True)
    forwarded_args.extend(['--background-root', str(background_runtime.root)])
    command = build_background_worker_command(
        background_id=background_id,
        prompt=args.prompt,
        forwarded_args=forwarded_args,
    )
    record = background_runtime.launch(
        command,
        prompt=args.prompt,
        workspace_cwd=Path(args.cwd).resolve(),
        model=args.model,
        background_id=background_id,
        process_cwd=Path(__file__).resolve().parent.parent,
    )
    print('# Background Session')
    print(f'background_id={record.background_id}')
    print(f'pid={record.pid}')
    print(f'log_path={record.log_path}')
    print(f'record_path={record.record_path}')
    return 0


def _run_background_worker(args: argparse.Namespace) -> int:
    background_runtime = BackgroundSessionRuntime(Path(args.background_root))
    exit_code = 1
    stop_reason = 'worker_failed'
    session_id = None
    session_path = None
    try:
        agent = _build_agent(args)
        agent.runtime_event_sink = lambda event: append_worker_event(
            background_runtime.root,
            args.background_id,
            event,
        )
        result = _execute_agent_turn(
            agent,
            args.prompt,
            active_session_id=getattr(args, 'resume_session_id', None),
        )
        # Smoke-only hook: simulate a worker that completed the LLM turn
        # (so the session checkpoint at SESSION_DIR/<id>.json is on disk)
        # but exited before writing its result file. The parent's
        # run_background_turn → synthesize_worker_failure_result path then
        # produces the "Worker exited before returning a result" message
        # the supervisor smoke harness asserts on.
        # Tested by scripts/smoke_latti_supervisor.py.
        if os.environ.get('LATTI_SUPERVISOR_SMOKE_FAIL_AFTER_SESSION') == '1':
            session_id = result.session_id
            session_path = result.session_path
            stop_reason = 'smoke_forced_worker_failure'
            exit_code = 1
            return 1
        save_worker_result(background_runtime.root, args.background_id, result)
        _print_agent_result(result, show_transcript=args.show_transcript)
        exit_code = 0
        stop_reason = result.stop_reason or 'completed'
        session_id = result.session_id
        session_path = result.session_path
        return 0
    finally:
        background_runtime.mark_finished(
            args.background_id,
            exit_code=exit_code,
            stop_reason=stop_reason,
            session_id=session_id,
            session_path=session_path,
        )


def _build_resumed_agent(args: argparse.Namespace) -> tuple[LocalCodingAgent, StoredAgentSession]:
    stored_session = load_agent_session(args.session_id)
    model_config = deserialize_model_config(stored_session.model_config)
    runtime_config = deserialize_runtime_config(stored_session.runtime_config)

    if args.model:
        model_config = replace(model_config, model=args.model)
    if args.base_url:
        model_config = replace(model_config, base_url=args.base_url)
    if args.api_key:
        model_config = replace(model_config, api_key=args.api_key)
    if args.temperature is not None:
        model_config = replace(model_config, temperature=args.temperature)
    if args.timeout_seconds is not None:
        model_config = replace(model_config, timeout_seconds=args.timeout_seconds)
    if args.input_cost_per_million is not None or args.output_cost_per_million is not None:
        model_config = replace(
            model_config,
            pricing=replace(
                model_config.pricing,
                input_cost_per_million_tokens_usd=(
                    args.input_cost_per_million
                    if args.input_cost_per_million is not None
                    else model_config.pricing.input_cost_per_million_tokens_usd
                ),
                output_cost_per_million_tokens_usd=(
                    args.output_cost_per_million
                    if args.output_cost_per_million is not None
                    else model_config.pricing.output_cost_per_million_tokens_usd
                ),
            ),
        )

    if args.max_turns is not None:
        runtime_config = replace(runtime_config, max_turns=args.max_turns)
    if args.allow_write or args.allow_shell or args.unsafe:
        runtime_config = replace(
            runtime_config,
            permissions=AgentPermissions(
                allow_file_write=runtime_config.permissions.allow_file_write or args.allow_write,
                allow_shell_commands=runtime_config.permissions.allow_shell_commands or args.allow_shell,
                allow_destructive_shell_commands=runtime_config.permissions.allow_destructive_shell_commands or args.unsafe,
            ),
        )
    if args.stream:
        runtime_config = replace(runtime_config, stream_model_responses=True)
    if (
        args.auto_snip_threshold is not None
        or args.auto_compact_threshold is not None
        or args.compact_preserve_messages is not None
    ):
        runtime_config = replace(
            runtime_config,
            auto_snip_threshold_tokens=(
                args.auto_snip_threshold
                if args.auto_snip_threshold is not None
                else runtime_config.auto_snip_threshold_tokens
            ),
            auto_compact_threshold_tokens=(
                args.auto_compact_threshold
                if args.auto_compact_threshold is not None
                else runtime_config.auto_compact_threshold_tokens
            ),
            compact_preserve_messages=(
                max(0, args.compact_preserve_messages)
                if args.compact_preserve_messages is not None
                else runtime_config.compact_preserve_messages
            ),
        )
    if (
        args.max_total_tokens is not None
        or args.max_input_tokens is not None
        or args.max_output_tokens is not None
        or args.max_reasoning_tokens is not None
        or args.max_budget_usd is not None
        or args.max_tool_calls is not None
        or args.max_delegated_tasks is not None
        or args.max_model_calls is not None
        or args.max_session_turns is not None
    ):
        runtime_config = replace(
            runtime_config,
            budget_config=BudgetConfig(
                max_total_tokens=(
                    args.max_total_tokens
                    if args.max_total_tokens is not None
                    else runtime_config.budget_config.max_total_tokens
                ),
                max_input_tokens=(
                    args.max_input_tokens
                    if args.max_input_tokens is not None
                    else runtime_config.budget_config.max_input_tokens
                ),
                max_output_tokens=(
                    args.max_output_tokens
                    if args.max_output_tokens is not None
                    else runtime_config.budget_config.max_output_tokens
                ),
                max_reasoning_tokens=(
                    args.max_reasoning_tokens
                    if args.max_reasoning_tokens is not None
                    else runtime_config.budget_config.max_reasoning_tokens
                ),
                max_total_cost_usd=(
                    args.max_budget_usd
                    if args.max_budget_usd is not None
                    else runtime_config.budget_config.max_total_cost_usd
                ),
                max_tool_calls=(
                    args.max_tool_calls
                    if args.max_tool_calls is not None
                    else runtime_config.budget_config.max_tool_calls
                ),
                max_delegated_tasks=(
                    args.max_delegated_tasks
                    if args.max_delegated_tasks is not None
                    else runtime_config.budget_config.max_delegated_tasks
                ),
                max_model_calls=(
                    args.max_model_calls
                    if args.max_model_calls is not None
                    else runtime_config.budget_config.max_model_calls
                ),
                max_session_turns=(
                    args.max_session_turns
                    if args.max_session_turns is not None
                    else runtime_config.budget_config.max_session_turns
                ),
            ),
        )
    output_schema = _load_output_schema_config(args)
    if output_schema is not None:
        runtime_config = replace(runtime_config, output_schema=output_schema)
    if args.scratchpad_root:
        runtime_config = replace(
            runtime_config,
            scratchpad_root=Path(args.scratchpad_root).resolve(),
        )

    agent = LocalCodingAgent(
        model_config=model_config,
        runtime_config=runtime_config,
    )
    return agent, stored_session


def _print_agent_result(result, *, show_transcript: bool, chat_mode: bool = False) -> None:
    # If streaming was active, tokens were already printed live — just add a newline
    streamed = any(e.get('type') == 'content_delta' for e in result.events)
    if streamed:
        print()  # newline after streamed output
    else:
        print(result.final_output)
    if not chat_mode:
        print('\n# Usage')
        print(f'total_tokens={result.usage.total_tokens}')
        print(f'input_tokens={result.usage.input_tokens}')
        print(f'output_tokens={result.usage.output_tokens}')
        print(f'total_cost_usd={result.total_cost_usd:.6f}')
        if result.stop_reason:
            print(f'stop_reason={result.stop_reason}')
        if result.session_id:
            print('\n# Session')
            print(f'session_id={result.session_id}')
            if result.session_path:
                print(f'session_path={result.session_path}')
        if result.scratchpad_directory:
            print(f'scratchpad_directory={result.scratchpad_directory}')
    if show_transcript:
        print('\n# Transcript')
        for message in result.transcript:
            role = message.get('role', 'unknown')
            print(f'[{role}]')
            print(message.get('content', ''))


def _execute_agent_turn(
    agent: LocalCodingAgent,
    prompt: str,
    *,
    active_session_id: str | None,
    info_callback: Callable[[str], None] | None = None,
    thinking_start: Callable[[], None] | None = None,
    thinking_clear: Callable[[], None] | None = None,
) -> AgentRunResult:
    def _invoke(action: Callable[[], AgentRunResult]) -> AgentRunResult:
        if thinking_start is not None:
            thinking_start()
        try:
            return action()
        finally:
            if thinking_clear is not None:
                thinking_clear()

    if active_session_id:
        try:
            stored_session = load_agent_session(
                active_session_id,
                directory=agent.runtime_config.session_directory,
            )
            _stored_cost = getattr(stored_session, 'total_cost_usd', 0.0)
            import os as _os_m
            _raw = _os_m.environ.get('LATTI_SAFETY_MAX_COST_USD', '').strip()
            try:
                _safety_ceiling = float(_raw) if _raw else 0.0
            except ValueError:
                _safety_ceiling = 0.0
            _stored_usage = getattr(stored_session, 'usage', None) or {}
            _stored_input_tokens = (
                _stored_usage.get('input_tokens', 0) if isinstance(_stored_usage, dict)
                else getattr(_stored_usage, 'input_tokens', 0)
            )
            _context_limit = 192_000
            _over_budget = False
            _over_context = _stored_input_tokens > _context_limit
            if _over_budget:
                if info_callback is not None:
                    info_callback(
                        f'session {active_session_id[:12]} reset — '
                        f'cost ${_stored_cost:.2f} >= ${_safety_ceiling:.2f} '
                        '— starting fresh'
                    )
                _persist_last_session(None)
                return _invoke(lambda: agent.run(prompt))
            if _over_context:
                from .session_compact import compact_stored_session

                compacted, dropped = compact_stored_session(stored_session)
                if info_callback is not None and dropped > 0:
                    new_tokens = int(compacted.usage.get('input_tokens', 0) or 0)
                    info_callback(
                        f'session {active_session_id[:12]} compacted — '
                        f'{_stored_input_tokens:,} tok → {new_tokens:,} tok '
                        f'({dropped} earliest messages elided; continuity preserved)'
                    )
                return _invoke(lambda: agent.resume(prompt, compacted))
            return _invoke(lambda: agent.resume(prompt, stored_session))
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            _persist_last_session(None)
            return _invoke(lambda: agent.run(prompt))
    return _invoke(lambda: agent.run(prompt))


def _build_background_chat_worker_runner(
    args: argparse.Namespace,
) -> Callable[[str, str | None], AgentRunResult]:
    background_runtime = BackgroundSessionRuntime()
    forwarded_args: list[str] = []
    _append_agent_forwarded_args(forwarded_args, args, include_backend=True)
    forwarded_args.extend(['--background-root', str(background_runtime.root)])
    process_cwd = Path(__file__).resolve().parent.parent
    workspace_cwd = Path(args.cwd).resolve()

    def _worker_runner(prompt: str, resume_session_id: str | None) -> AgentRunResult:
        background_id = background_runtime.create_id()
        command = build_background_worker_command(
            background_id=background_id,
            prompt=prompt,
            forwarded_args=forwarded_args,
            resume_session_id=resume_session_id,
        )
        final_record, result = run_background_turn(
            background_runtime,
            launch_worker=lambda: background_runtime.launch(
                command,
                prompt=prompt,
                workspace_cwd=workspace_cwd,
                model=args.model,
                mode='chat',
                background_id=background_id,
                process_cwd=process_cwd,
            ),
            on_event=getattr(_worker_runner, 'on_event', None),
        )
        if final_record.session_id and not result.session_id:
            result = replace(result, session_id=final_record.session_id)
        if final_record.session_path and not result.session_path:
            result = replace(result, session_path=final_record.session_path)
        return result

    return _worker_runner


def _render_worker_event_to_tui(
    event: dict[str, object],
    *,
    tui,
    stream_renderer,
):
    event_type = event.get('type')
    if event_type == 'content_delta':
        delta = event.get('delta')
        if isinstance(delta, str) and delta:
            if stream_renderer is None:
                stream_renderer = tui.StreamRenderer()
                stream_renderer.start()
            stream_renderer.token(delta)
    elif event_type == 'tool_start':
        tool_name = event.get('tool_name')
        detail = event.get('detail')
        if isinstance(tool_name, str):
            tui.tool_start(tool_name, detail if isinstance(detail, str) else '')
    elif event_type == 'tool_result':
        tool_name = event.get('tool_name')
        content = event.get('content')
        if isinstance(tool_name, str):
            tui.tool_result(tool_name, content if isinstance(content, str) else '')
    elif event_type == 'state_machine_decision':
        action_kind = event.get('action_kind')
        rationale = event.get('rationale')
        if isinstance(action_kind, str):
            reason = rationale if isinstance(rationale, str) else ''
            if reason.startswith('rule_fired: '):
                reason = reason.removeprefix('rule_fired: ')
            tui.info(f'state-machine: {action_kind} - {reason}'.rstrip())
    elif event_type == 'session_checkpoint':
        session_id = event.get('session_id')
        typed_saved = event.get('typed_state_checkpointed') is True
        if isinstance(session_id, str) and session_id:
            status = 'typed-state saved' if typed_saved else 'session saved'
            tui.info(f'checkpoint: {session_id[:12]} {status}')
    elif event_type == 'state_machine_evaluation':
        # Telemetry-only: surfaces evaluator verdicts without altering control
        # flow. v2 will let 'replan'/'done' verdicts drive transitions.
        evaluator = event.get('evaluator')
        verdict = event.get('verdict')
        note = event.get('note')
        if isinstance(evaluator, str) and isinstance(verdict, str):
            # Suppress the noisy 'continue' verdict — only show non-default
            # verdicts (replan, done, escalate, timeout).
            if verdict != 'continue':
                detail = f' — {note}' if isinstance(note, str) and note else ''
                tui.info(f'evaluator {evaluator}: {verdict}{detail}'.rstrip())
    return stream_renderer


def _run_agent_chat_loop(
    agent: LocalCodingAgent,
    *,
    initial_prompt: str | None,
    resume_session_id: str | None,
    show_transcript: bool,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    result_printer: Callable[..., None] = _print_agent_result,
    worker_runner: Callable[[str, str | None], AgentRunResult] | None = None,
) -> int:
    active_session_id = resume_session_id
    first_prompt = initial_prompt

    # Auto-boot: if LATTI_BOOT is set and no explicit prompt, generate one
    # This is Latti's equivalent of Claude Code's SessionStart hook
    if os.environ.get('LATTI_BOOT', '0') == '1' and first_prompt is None and not active_session_id:
        first_prompt = (
            'Boot. Systems checked. Act on what needs attention — '
            'check pending picks, score settled games, handle errors. '
            'Report status in 2-3 lines, then wait for my direction.'
        )

    # Initialize TUI state
    _git_branch = ''
    try:
        import subprocess as _sp
        _git_branch = _sp.check_output(
            ['git', 'branch', '--show-current'],
            cwd=str(agent.runtime_config.cwd),
            stderr=_sp.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        pass

    cumulative_input_tokens = 0
    cumulative_output_tokens = 0
    turn_count = 0

    # Use TUI only for an actual interactive terminal. Piped smoke tests and
    # non-TTY launches cannot support termios raw mode; fall back to plain
    # input/output instead of throwing termios.error at tui.prompt().
    tui = None
    tui_heal = None
    use_tui = (
        input_func is input
        and output_func is print
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get('LATTI_DISABLE_TUI') != '1'
    )

    if use_tui:
        from . import tui
        tui.banner()
        from . import tui_heal
        tui_heal.install()  # SIGWINCH flag + sanitizer + cursor_guard + heal()
        tui.set_state(
            model=agent.model_config.model,
            cwd=str(agent.runtime_config.cwd),
            branch=_git_branch,
            context_pct=0,
            permissions='full access' if agent.runtime_config.permissions.allow_destructive_shell_commands
                else 'write + shell' if agent.runtime_config.permissions.allow_shell_commands
                else 'write' if agent.runtime_config.permissions.allow_file_write
                else 'read-only',
        )
        if active_session_id:
            tui.info(f'resuming session {active_session_id[:12]}...')
        # Run boot actions visibly in the TUI (code, not model)
        if os.environ.get('LATTI_BOOT', '0') == '1':
            try:
                from .latti_boot import _run_boot_services, _run_safe
                svc = _run_boot_services()
                if svc:
                    tui.info(svc)
                # Git status
                git_status = _run_safe('cd ~/V5/claw-code-agent && git status --short 2>/dev/null')
                if git_status:
                    tui.info(f'git: {len(git_status.splitlines())} uncommitted changes')
                # NBA dashboard one-liner
                nba = _run_safe(
                    'curl -s http://localhost:3737/api/dashboard 2>/dev/null | '
                    'python3 -c "import json,sys; d=json.load(sys.stdin); r=d[\'record\']; '
                    'print(f\'NBA: ${d[\"balance\"]:.0f} | {r[\"wins\"]}-{r[\"losses\"]}-{r[\"pushes\"]} | {d[\"roi\"]}% ROI\')" 2>/dev/null'
                )
                if nba:
                    tui.info(nba)
                else:
                    tui.info('NBA engine: offline')
            except Exception:
                pass
    else:
        output_func('# Agent Chat')
        output_func("Enter a prompt. Use '/exit' or '/quit' to stop.")

    while True:
        if first_prompt is not None:
            user_input = first_prompt
            first_prompt = None
        else:
            try:
                if use_tui:
                    # If a SIGWINCH arrived since the last turn, fully heal
                    # the layout for the new terminal dimensions before
                    # drawing the prompt.
                    if tui_heal.sigwinch_pending():
                        tui_heal.heal()
                    tui_heal.cursor_guard()  # Layer 3: nudge cursor out of footer before raw mode
                user_input = tui.prompt() if use_tui else input_func('user> ')
            except (EOFError, KeyboardInterrupt):
                if use_tui:
                    tui_heal.uninstall()
                    tui.cleanup()
                else:
                    output_func('chat_ended=eof')
                return 0

        normalized = user_input.strip()
        if not normalized:
            continue
        # Echo user message as pi-style highlighted band
        if use_tui:
            tui.user_message(normalized)

        # --- Slash commands (intercepted before LLM) ---
        if normalized.startswith('/'):
            from .slash_commands import is_command, handle_command, CommandContext
            if is_command(normalized):
                _cmd_ctx = CommandContext(
                    agent=agent,
                    active_session_id=active_session_id,
                    turn_count=turn_count,
                    cumulative_cost=result.total_cost_usd if 'result' in dir() and result else 0.0,
                    cumulative_tokens=cumulative_input_tokens + cumulative_output_tokens,
                    use_tui=use_tui,
                    tui=tui if use_tui else None,
                    tui_heal=tui_heal if use_tui else None,
                    output_func=output_func,
                    worker_supervisor_active=worker_runner is not None,
                )
                _cmd_result = handle_command(normalized, _cmd_ctx)
                if _cmd_result.exit_session:
                    if use_tui:
                        tui_heal.uninstall()
                        tui.cleanup()
                        tui.info('goodbye')
                    else:
                        output_func('chat_ended=user_exit')
                    return 0
                if _cmd_result.new_session:
                    active_session_id = None
                    _persist_last_session(None)
                continue  # don't send to LLM

        if normalized in {'/exit', '/quit'}:
            if use_tui:
                tui_heal.uninstall()
                tui.cleanup()
                tui.info('goodbye')
            else:
                output_func('chat_ended=user_exit')
            return 0

        if worker_runner is not None:
            worker_stream_renderer = None

            def _on_worker_event(event: dict[str, object]) -> None:
                nonlocal worker_stream_renderer
                if not use_tui:
                    return
                worker_stream_renderer = _render_worker_event_to_tui(
                    event,
                    tui=tui,
                    stream_renderer=worker_stream_renderer,
                )

            try:
                setattr(worker_runner, 'on_event', _on_worker_event if use_tui else None)
            except Exception:
                pass
            if use_tui:
                tui.thinking_start()
            try:
                result = worker_runner(user_input, active_session_id)
            finally:
                if worker_stream_renderer is not None:
                    worker_stream_renderer.end()
                if use_tui:
                    tui.thinking_clear()
        else:
            result = _execute_agent_turn(
                agent,
                user_input,
                active_session_id=active_session_id,
                info_callback=tui.info if use_tui else None,
                thinking_start=tui.thinking_start if use_tui else None,
                thinking_clear=tui.thinking_clear if use_tui else None,
            )
        # Display result — call result_printer with chat_mode if supported
        try:
            result_printer(result, show_transcript=show_transcript, chat_mode=True)
        except TypeError:
            result_printer(result, show_transcript=show_transcript)
        print()  # breathing room
        active_session_id = result.session_id
        # Persist session ID for auto-resume on next launch
        _persist_last_session(active_session_id)
        # Track live session stats
        turn_count += 1
        cumulative_input_tokens += result.usage.input_tokens
        cumulative_output_tokens += result.usage.output_tokens
        # Context % = cumulative conversation tokens (excluding system prompt baseline) vs 200K
        # Use cumulative tokens as a better measure of conversation length
        conversation_tokens = cumulative_input_tokens + cumulative_output_tokens
        ctx_pct = min(99, int(conversation_tokens * 100 / 200_000)) if conversation_tokens > 0 else 0
        if use_tui:
            tui.set_state(
                context_pct=ctx_pct,
                total_tokens=cumulative_input_tokens + cumulative_output_tokens,
                turn_count=turn_count,
                cost_usd=result.total_cost_usd,
            )
            tui.status_footer()  # redraw sticky footer with new data
        # After rendering + persisting the turn, decide whether to run the
        # optional post-turn hooks (auto-speak, self-sculpt). On macOS under
        # compressor/wired pressure those hooks can push Python over jetsam;
        # earlier this branch returned 75 (session-end) but that meant a
        # memory-pressured machine could only ever run one query before
        # latti exited. The session is already saved — we just skip the
        # optional hooks and keep the chat loop running.
        _safe_mb = _macos_safe_memory_mb() if use_tui else 999_999
        _post_turn_threshold = int(os.environ.get('LATTI_POST_TURN_MIN_MB', '200'))
        _already_low_mem = os.environ.get('LATTI_LOW_MEM') == '1'
        _post_turn_action = _post_turn_memory_action(
            safe_mb=_safe_mb,
            threshold_mb=_post_turn_threshold,
            already_low_mem=_already_low_mem,
        )
        if _post_turn_action == 'skip_hooks':
            if not _already_low_mem and use_tui:
                tui.info(
                    f'low memory after turn — disabling voice/self-sculpt for '
                    f'the rest of this session (session: {active_session_id[:12]})'
                )
                # Persist for subsequent turns AND any subprocesses we spawn.
                os.environ['LATTI_LOW_MEM'] = '1'
            _fired = []
        else:
            # Detect if the LLM called speak.sh this turn (via bash tool)
            _detect_llm_spoke(result)
            # Voice — speak first 2 sentences of response (skips if LLM already spoke)
            _speak_response(result.final_output)
            # Self-sculpt — evaluate AND mutate (zero tokens, real-time self-modification)
            try:
                from .self_sculpt import sculpt as _sculpt
                _fired = _sculpt(result.final_output or '', agent=agent)
            except Exception:
                _fired = []
        # === TURN COMPLETE — signal the human ===
        if use_tui:
            tui.done_marker()
            # bell removed


_LATTI_HOME = os.path.expanduser('~/.latti')
_LAST_SESSION_FILE = os.path.join(_LATTI_HOME, 'last_session')


def _persist_last_session(session_id: str | None) -> None:
    """Write the active session ID to disk for auto-resume."""
    if not session_id:
        return
    try:
        os.makedirs(_LATTI_HOME, exist_ok=True)
        with open(_LAST_SESSION_FILE, 'w') as f:
            f.write(session_id)
    except OSError:
        pass


def _load_last_session() -> str | None:
    """Read the last session ID from disk."""
    try:
        with open(_LAST_SESSION_FILE, 'r') as f:
            sid = f.read().strip()
            return sid if sid else None
    except (OSError, FileNotFoundError):
        return None


def _detect_llm_spoke(result) -> None:
    """Scan the turn's transcript for bash tool calls containing speak.sh.

    If the LLM intentionally called speak.sh via the bash tool this turn,
    set _llm_spoke_this_turn so _speak_response skips auto-speak.
    """
    global _llm_spoke_this_turn
    _llm_spoke_this_turn = False
    # Scan transcript — assistant messages with tool_calls contain the command
    for msg in getattr(result, 'transcript', ()):
        role = msg.get('role', '')
        if role != 'assistant':
            continue
        # Check tool_calls array (OpenAI format)
        tool_calls = msg.get('tool_calls', ())
        for tc in tool_calls:
            fn = tc.get('function', {}) if isinstance(tc, dict) else {}
            if fn.get('name') != 'bash':
                continue
            raw_args = fn.get('arguments', '')
            if isinstance(raw_args, str) and 'speak' in raw_args:
                _llm_spoke_this_turn = True
                return
            if isinstance(raw_args, dict) and 'speak' in str(raw_args.get('command', '')):
                _llm_spoke_this_turn = True
                return
        # Also check content — some formats inline tool calls in content
        content = msg.get('content', '')
        if isinstance(content, str) and 'speak.sh' in content:
            _llm_spoke_this_turn = True
            return


def _post_turn_memory_action(
    *,
    safe_mb: int,
    threshold_mb: int,
    already_low_mem: bool,
) -> str:
    """Decide what to do after a turn given current memory pressure.

    Returns:
      'continue'   — run optional post-turn hooks (voice TTS, self-sculpt)
      'skip_hooks' — skip them; chat loop continues either way

    Policy:
      - If the wrapper already promoted us to low-mem mode → always skip.
      - If safe RAM dropped strictly below threshold this turn → skip.
      - Otherwise → continue normally.

    Pure function. No side effects. Tested by tests/test_post_turn_memory.py.
    """
    if already_low_mem:
        return 'skip_hooks'
    if safe_mb < threshold_mb:
        return 'skip_hooks'
    return 'continue'


def _macos_safe_memory_mb() -> int:
    """Return conservative macOS safe-free memory in MB.

    Mirrors the shell launcher guard: free + speculative + purgeable pages.
    Do NOT count inactive pages; under heavy compressor/wired pressure they
    did not prevent jetsam from SIGKILLing the Python/TUI process.
    Non-macOS or parse failure returns a large sentinel so hooks proceed.
    """
    if sys.platform != 'darwin':
        return 10**9
    try:
        import re
        out = subprocess.check_output(['vm_stat'], text=True, timeout=2)
        page_match = re.search(r'page size of (\d+) bytes', out)
        if not page_match:
            return 10**9
        page_size = int(page_match.group(1))
        vals: dict[str, int] = {}
        for line in out.splitlines():
            m = re.match(r'([^:]+):\s+([0-9]+)\.', line)
            if m:
                vals[m.group(1)] = int(m.group(2))
        safe_pages = (
            vals.get('Pages free', 0)
            + vals.get('Pages speculative', 0)
            + vals.get('Pages purgeable', 0)
        )
        return safe_pages * page_size // 1024 // 1024
    except Exception:
        return 10**9


_last_speak_proc: subprocess.Popen | None = None
# Track if the LLM called speak.sh this turn (via bash tool).
# If so, skip auto-speak — the LLM composed voice text intentionally.
_llm_spoke_this_turn: bool = False

# Patterns that should NEVER be auto-spoken — compiled once at module load
import re as _re_module
_NEVER_SPEAK_PATTERNS = [
    _re_module.compile(r'(?i)^(unable to|error:|failed|exception|traceback|ssl:)'),  # errors
    _re_module.compile(r'(?i)^(ok\.|ok,|ok )'),  # fragments/status starts
    _re_module.compile(r'(?i)^(here|let me|i\'ll|i will|starting|proceeding)'),  # action narration
    _re_module.compile(r'(?i)(certificate|timeout|connection refused|api key|401|403|404|409|500)'),  # infra noise
    _re_module.compile(r'(?i)^(fix \d|feat|chore|refactor)\b'),  # commit-message-like starts
    _re_module.compile(r'^\s*[-*•]\s'),  # bullet lists
    _re_module.compile(r'^\s*```'),  # code blocks
    _re_module.compile(r'^\s*\|'),  # table rows
]
_SPEAK_LINE_SKIP = _re_module.compile(r'^[-*•]|^```|^\||^#+\s|^>\s')
_SPEAK_SENTENCE_SPLIT = _re_module.compile(r'(?<=[.!?])\s+')
_SPEAK_MARKDOWN_STRIP = _re_module.compile(r'[*_#`\[\]()]')
_SPEAK_LEADING_STRIP = _re_module.compile(r'^[.\-–—…\s]+')


def _speak_response(text: str) -> None:
    """Speak the first 1-2 meaningful sentences via speak.sh (non-blocking).

    Three guards prevent voice/chat mismatch:
    1. If the LLM already called speak.sh this turn, skip (it composed voice intentionally)
    2. Skip errors, infra noise, narration, fragments
    3. Find the first real sentence, not just the first 2 tokens
    """
    global _last_speak_proc, _llm_spoke_this_turn
    if os.environ.get('LATTI_LOW_MEM') == '1':
        return
    import re as _re

    speak_script = os.path.expanduser('~/.claude/scripts/speak.sh')
    if not os.path.isfile(speak_script):
        return

    # Guard 1: LLM already spoke this turn
    if _llm_spoke_this_turn:
        _llm_spoke_this_turn = False  # reset for next turn
        return

    if not text or not text.strip():
        return

    # Guard 2: Never speak error strings or infra noise (pre-compiled patterns)
    first_line = text.strip().split('\n')[0]
    for compiled_pat in _NEVER_SPEAK_PATTERNS:
        if compiled_pat.search(first_line):
            return

    # Guard 3: Find first meaningful sentence(s), skipping fragments
    lines = text.strip().split('\n')
    meaningful_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _SPEAK_LINE_SKIP.match(line):
            continue
        if len(line) < 20 and not any(c in line for c in '.!?'):
            continue
        meaningful_lines.append(line)
        if len(meaningful_lines) >= 3:
            break

    if not meaningful_lines:
        return

    # Join and extract first 2 proper sentences
    combined = ' '.join(meaningful_lines)
    sentences = _SPEAK_SENTENCE_SPLIT.split(combined)
    snippet = ' '.join(sentences[:2])[:250]

    # Strip markdown formatting for cleaner speech
    snippet = _SPEAK_MARKDOWN_STRIP.sub('', snippet).strip()
    snippet = _SPEAK_LEADING_STRIP.sub('', snippet).strip()

    if not snippet or len(snippet) < 10:
        return

    # Guard 4: Reject incomplete sentences (fragments, trailing ellipsis, setup without landing)
    # Complete sentences end with . ! ? and don't trail off with ... or [incomplete]
    if snippet.endswith(('...', '—', '–', '—\n', '[', '(')):
        return
    if not any(snippet.endswith(p) for p in '.!?'):
        # If no terminal punctuation, reject (likely a fragment or setup)
        return

    # Kill previous auto-speak only (not LLM-initiated speaks)
    if _last_speak_proc is not None:
        try:
            _last_speak_proc.kill()
            _last_speak_proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _last_speak_proc = None

    try:
        _last_speak_proc = subprocess.Popen(
            ['bash', speak_script, snippet],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Python porting workspace for the Claude Code rewrite effort')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('summary', help='render a Markdown summary of the Python porting workspace')
    subparsers.add_parser('manifest', help='print the current Python workspace manifest')
    subparsers.add_parser('parity-audit', help='compare the Python workspace against the local ignored TypeScript archive when available')
    subparsers.add_parser('setup-report', help='render the startup/prefetch setup report')
    subparsers.add_parser('command-graph', help='show command graph segmentation')
    subparsers.add_parser('tool-pool', help='show assembled tool pool with default settings')
    subparsers.add_parser('bootstrap-graph', help='show the mirrored bootstrap/runtime graph stages')

    list_parser = subparsers.add_parser('subsystems', help='list the current Python modules in the workspace')
    list_parser.add_argument('--limit', type=int, default=32)

    commands_parser = subparsers.add_parser('commands', help='list mirrored command entries from the archived snapshot')
    commands_parser.add_argument('--limit', type=int, default=20)
    commands_parser.add_argument('--query')
    commands_parser.add_argument('--no-plugin-commands', action='store_true')
    commands_parser.add_argument('--no-skill-commands', action='store_true')

    tools_parser = subparsers.add_parser('tools', help='list mirrored tool entries from the archived snapshot')
    tools_parser.add_argument('--limit', type=int, default=20)
    tools_parser.add_argument('--query')
    tools_parser.add_argument('--simple-mode', action='store_true')
    tools_parser.add_argument('--no-mcp', action='store_true')
    tools_parser.add_argument('--deny-tool', action='append', default=[])
    tools_parser.add_argument('--deny-prefix', action='append', default=[])

    route_parser = subparsers.add_parser('route', help='route a prompt across mirrored command/tool inventories')
    route_parser.add_argument('prompt')
    route_parser.add_argument('--limit', type=int, default=5)

    bootstrap_parser = subparsers.add_parser('bootstrap', help='build a runtime-style session report from the mirrored inventories')
    bootstrap_parser.add_argument('prompt')
    bootstrap_parser.add_argument('--limit', type=int, default=5)

    loop_parser = subparsers.add_parser('turn-loop', help='run a small stateful turn loop for the mirrored runtime')
    loop_parser.add_argument('prompt')
    loop_parser.add_argument('--limit', type=int, default=5)
    loop_parser.add_argument('--max-turns', type=int, default=3)
    loop_parser.add_argument('--structured-output', action='store_true')

    flush_parser = subparsers.add_parser('flush-transcript', help='persist and flush a temporary session transcript')
    flush_parser.add_argument('prompt')

    load_session_parser = subparsers.add_parser('load-session', help='load a previously persisted session')
    load_session_parser.add_argument('session_id')

    remote_parser = subparsers.add_parser('remote-mode', help='simulate remote-control runtime branching')
    remote_parser.add_argument('target')
    remote_parser.add_argument('--cwd', default='.')
    ssh_parser = subparsers.add_parser('ssh-mode', help='simulate SSH runtime branching')
    ssh_parser.add_argument('target')
    ssh_parser.add_argument('--cwd', default='.')
    teleport_parser = subparsers.add_parser('teleport-mode', help='simulate teleport runtime branching')
    teleport_parser.add_argument('target')
    teleport_parser.add_argument('--cwd', default='.')
    direct_parser = subparsers.add_parser('direct-connect-mode', help='simulate direct-connect runtime branching')
    direct_parser.add_argument('target')
    direct_parser.add_argument('--cwd', default='.')
    deep_link_parser = subparsers.add_parser('deep-link-mode', help='simulate deep-link runtime branching')
    deep_link_parser.add_argument('target')
    deep_link_parser.add_argument('--cwd', default='.')
    remote_status_parser = subparsers.add_parser('remote-status', help='show local remote runtime status')
    remote_status_parser.add_argument('--cwd', default='.')
    remote_profiles_parser = subparsers.add_parser('remote-profiles', help='list configured local remote profiles')
    remote_profiles_parser.add_argument('--cwd', default='.')
    remote_profiles_parser.add_argument('--query')
    remote_disconnect_parser = subparsers.add_parser('remote-disconnect', help='disconnect the active local remote target')
    remote_disconnect_parser.add_argument('--cwd', default='.')
    worktree_status_parser = subparsers.add_parser('worktree-status', help='show local managed git worktree status')
    worktree_status_parser.add_argument('--cwd', default='.')
    worktree_enter_parser = subparsers.add_parser('worktree-enter', help='create and enter a managed git worktree')
    worktree_enter_parser.add_argument('name', nargs='?')
    worktree_enter_parser.add_argument('--cwd', default='.')
    worktree_exit_parser = subparsers.add_parser('worktree-exit', help='exit the active managed git worktree')
    worktree_exit_parser.add_argument('--action', default='keep')
    worktree_exit_parser.add_argument('--discard-changes', action='store_true')
    worktree_exit_parser.add_argument('--cwd', default='.')
    account_status_parser = subparsers.add_parser('account-status', help='show local account runtime status')
    account_status_parser.add_argument('--cwd', default='.')
    account_profiles_parser = subparsers.add_parser('account-profiles', help='list configured local account profiles')
    account_profiles_parser.add_argument('--cwd', default='.')
    account_profiles_parser.add_argument('--query')
    account_login_parser = subparsers.add_parser('account-login', help='activate a local account profile or ephemeral identity')
    account_login_parser.add_argument('target')
    account_login_parser.add_argument('--provider')
    account_login_parser.add_argument('--auth-mode')
    account_login_parser.add_argument('--cwd', default='.')
    account_logout_parser = subparsers.add_parser('account-logout', help='clear the active local account session')
    account_logout_parser.add_argument('--cwd', default='.')
    ask_status_parser = subparsers.add_parser('ask-status', help='show local ask-user runtime status')
    ask_status_parser.add_argument('--cwd', default='.')
    ask_history_parser = subparsers.add_parser('ask-history', help='show local ask-user interaction history')
    ask_history_parser.add_argument('--cwd', default='.')
    search_status_parser = subparsers.add_parser('search-status', help='show local search runtime status')
    search_status_parser.add_argument('--cwd', default='.')
    search_status_parser.add_argument('--provider')
    search_providers_parser = subparsers.add_parser('search-providers', help='list configured local search providers')
    search_providers_parser.add_argument('--cwd', default='.')
    search_providers_parser.add_argument('--query')
    search_activate_parser = subparsers.add_parser('search-activate', help='set the active local search provider')
    search_activate_parser.add_argument('provider')
    search_activate_parser.add_argument('--cwd', default='.')
    search_parser = subparsers.add_parser('search', help='run a real web search against the configured local search runtime')
    search_parser.add_argument('query')
    search_parser.add_argument('--cwd', default='.')
    search_parser.add_argument('--provider')
    search_parser.add_argument('--max-results', type=int, default=5)
    search_parser.add_argument('--domain', action='append', default=[])
    mcp_status_parser = subparsers.add_parser('mcp-status', help='show local MCP runtime status')
    mcp_status_parser.add_argument('--cwd', default='.')
    mcp_resources_parser = subparsers.add_parser('mcp-resources', help='list MCP resources discovered through local manifests and transport-backed servers')
    mcp_resources_parser.add_argument('--cwd', default='.')
    mcp_resources_parser.add_argument('--query')
    mcp_resource_parser = subparsers.add_parser('mcp-resource', help='read an MCP resource by URI')
    mcp_resource_parser.add_argument('uri')
    mcp_resource_parser.add_argument('--cwd', default='.')
    mcp_tools_parser = subparsers.add_parser('mcp-tools', help='list MCP tools exposed by configured MCP servers')
    mcp_tools_parser.add_argument('--cwd', default='.')
    mcp_tools_parser.add_argument('--query')
    mcp_tools_parser.add_argument('--server')
    mcp_call_tool_parser = subparsers.add_parser('mcp-call-tool', help='call an MCP tool exposed by a configured MCP server')
    mcp_call_tool_parser.add_argument('tool_name')
    mcp_call_tool_parser.add_argument('--arguments-json', default='{}')
    mcp_call_tool_parser.add_argument('--server')
    mcp_call_tool_parser.add_argument('--cwd', default='.')
    config_status_parser = subparsers.add_parser('config-status', help='show local workspace config runtime summary')
    config_status_parser.add_argument('--cwd', default='.')
    config_effective_parser = subparsers.add_parser('config-effective', help='render the merged effective local workspace config')
    config_effective_parser.add_argument('--cwd', default='.')
    config_source_parser = subparsers.add_parser('config-source', help='render a specific local config source')
    config_source_parser.add_argument('source')
    config_source_parser.add_argument('--cwd', default='.')
    config_get_parser = subparsers.add_parser('config-get', help='read a local config value by dotted key path')
    config_get_parser.add_argument('key_path')
    config_get_parser.add_argument('--source')
    config_get_parser.add_argument('--cwd', default='.')
    config_set_parser = subparsers.add_parser('config-set', help='write a local config value by dotted key path')
    config_set_parser.add_argument('key_path')
    config_set_parser.add_argument('value_json')
    config_set_parser.add_argument('--source', default='local')
    config_set_parser.add_argument('--cwd', default='.')
    lsp_status_parser = subparsers.add_parser('lsp-status', help='show local LSP runtime summary')
    lsp_status_parser.add_argument('--cwd', default='.')
    lsp_symbols_parser = subparsers.add_parser('lsp-symbols', help='show local LSP document symbols for one file')
    lsp_symbols_parser.add_argument('file_path')
    lsp_symbols_parser.add_argument('--cwd', default='.')
    lsp_workspace_parser = subparsers.add_parser('lsp-workspace-symbols', help='search workspace symbols through the local LSP runtime')
    lsp_workspace_parser.add_argument('query')
    lsp_workspace_parser.add_argument('--max-results', type=int, default=50)
    lsp_workspace_parser.add_argument('--cwd', default='.')
    lsp_definition_parser = subparsers.add_parser('lsp-definition', help='run a local LSP definition query')
    lsp_definition_parser.add_argument('file_path')
    lsp_definition_parser.add_argument('line', type=int)
    lsp_definition_parser.add_argument('character', type=int)
    lsp_definition_parser.add_argument('--max-results', type=int, default=20)
    lsp_definition_parser.add_argument('--cwd', default='.')
    lsp_references_parser = subparsers.add_parser('lsp-references', help='run a local LSP references query')
    lsp_references_parser.add_argument('file_path')
    lsp_references_parser.add_argument('line', type=int)
    lsp_references_parser.add_argument('character', type=int)
    lsp_references_parser.add_argument('--max-results', type=int, default=50)
    lsp_references_parser.add_argument('--cwd', default='.')
    lsp_hover_parser = subparsers.add_parser('lsp-hover', help='run a local LSP hover query')
    lsp_hover_parser.add_argument('file_path')
    lsp_hover_parser.add_argument('line', type=int)
    lsp_hover_parser.add_argument('character', type=int)
    lsp_hover_parser.add_argument('--cwd', default='.')
    lsp_diagnostics_parser = subparsers.add_parser('lsp-diagnostics', help='show local LSP diagnostics')
    lsp_diagnostics_parser.add_argument('--file-path')
    lsp_diagnostics_parser.add_argument('--cwd', default='.')
    lsp_hierarchy_parser = subparsers.add_parser('lsp-call-hierarchy', help='show local LSP call hierarchy at a position')
    lsp_hierarchy_parser.add_argument('file_path')
    lsp_hierarchy_parser.add_argument('line', type=int)
    lsp_hierarchy_parser.add_argument('character', type=int)
    lsp_hierarchy_parser.add_argument('--cwd', default='.')
    lsp_incoming_parser = subparsers.add_parser('lsp-incoming-calls', help='show local LSP incoming calls at a position')
    lsp_incoming_parser.add_argument('file_path')
    lsp_incoming_parser.add_argument('line', type=int)
    lsp_incoming_parser.add_argument('character', type=int)
    lsp_incoming_parser.add_argument('--max-results', type=int, default=50)
    lsp_incoming_parser.add_argument('--cwd', default='.')
    lsp_outgoing_parser = subparsers.add_parser('lsp-outgoing-calls', help='show local LSP outgoing calls at a position')
    lsp_outgoing_parser.add_argument('file_path')
    lsp_outgoing_parser.add_argument('line', type=int)
    lsp_outgoing_parser.add_argument('character', type=int)
    lsp_outgoing_parser.add_argument('--max-results', type=int, default=50)
    lsp_outgoing_parser.add_argument('--cwd', default='.')
    workflow_list_parser = subparsers.add_parser('workflow-list', help='list local workflow definitions')
    workflow_list_parser.add_argument('--cwd', default='.')
    workflow_list_parser.add_argument('--query')
    workflow_get_parser = subparsers.add_parser('workflow-get', help='show one local workflow definition')
    workflow_get_parser.add_argument('workflow_name')
    workflow_get_parser.add_argument('--cwd', default='.')
    workflow_run_parser = subparsers.add_parser('workflow-run', help='record and render a local workflow run')
    workflow_run_parser.add_argument('workflow_name')
    workflow_run_parser.add_argument('--arguments-json', default='{}')
    workflow_run_parser.add_argument('--cwd', default='.')
    trigger_list_parser = subparsers.add_parser('trigger-list', help='list local remote triggers')
    trigger_list_parser.add_argument('--cwd', default='.')
    trigger_list_parser.add_argument('--query')
    trigger_get_parser = subparsers.add_parser('trigger-get', help='show one local remote trigger')
    trigger_get_parser.add_argument('trigger_id')
    trigger_get_parser.add_argument('--cwd', default='.')
    trigger_create_parser = subparsers.add_parser('trigger-create', help='create a local remote trigger')
    trigger_create_parser.add_argument('--body-json', required=True)
    trigger_create_parser.add_argument('--cwd', default='.')
    trigger_update_parser = subparsers.add_parser('trigger-update', help='update a local remote trigger')
    trigger_update_parser.add_argument('trigger_id')
    trigger_update_parser.add_argument('--body-json', required=True)
    trigger_update_parser.add_argument('--cwd', default='.')
    trigger_run_parser = subparsers.add_parser('trigger-run', help='run a local remote trigger')
    trigger_run_parser.add_argument('trigger_id')
    trigger_run_parser.add_argument('--body-json', default='{}')
    trigger_run_parser.add_argument('--cwd', default='.')
    teams_status_parser = subparsers.add_parser('team-status', help='show local collaboration team runtime summary')
    teams_status_parser.add_argument('--cwd', default='.')
    teams_list_parser = subparsers.add_parser('team-list', help='list local collaboration teams')
    teams_list_parser.add_argument('--cwd', default='.')
    teams_list_parser.add_argument('--query')
    team_get_parser = subparsers.add_parser('team-get', help='show one local collaboration team')
    team_get_parser.add_argument('team_name')
    team_get_parser.add_argument('--cwd', default='.')
    team_create_parser = subparsers.add_parser('team-create', help='create a local collaboration team')
    team_create_parser.add_argument('team_name')
    team_create_parser.add_argument('--description')
    team_create_parser.add_argument('--member', action='append', default=[])
    team_create_parser.add_argument('--cwd', default='.')
    team_delete_parser = subparsers.add_parser('team-delete', help='delete a local collaboration team')
    team_delete_parser.add_argument('team_name')
    team_delete_parser.add_argument('--cwd', default='.')
    team_messages_parser = subparsers.add_parser('team-messages', help='show local team messages')
    team_messages_parser.add_argument('--team-name')
    team_messages_parser.add_argument('--cwd', default='.')

    show_command = subparsers.add_parser('show-command', help='show one mirrored command entry by exact name')
    show_command.add_argument('name')
    show_tool = subparsers.add_parser('show-tool', help='show one mirrored tool entry by exact name')
    show_tool.add_argument('name')

    exec_command_parser = subparsers.add_parser('exec-command', help='execute a mirrored command shim by exact name')
    exec_command_parser.add_argument('name')
    exec_command_parser.add_argument('prompt')

    exec_tool_parser = subparsers.add_parser('exec-tool', help='execute a mirrored tool shim by exact name')
    exec_tool_parser.add_argument('name')
    exec_tool_parser.add_argument('payload')

    agent_parser = subparsers.add_parser('agent', help='run the real Python local-model agent')
    agent_parser.add_argument('prompt')
    agent_parser.add_argument('--max-turns', type=int, default=12)
    agent_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(agent_parser, include_backend=True)

    background_parser = subparsers.add_parser('agent-bg', help='run the Python local-model agent as a local background session')
    background_parser.add_argument('prompt')
    background_parser.add_argument('--max-turns', type=int, default=12)
    background_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(background_parser, include_backend=True)

    background_worker_parser = subparsers.add_parser('agent-bg-worker', help=argparse.SUPPRESS)
    background_worker_parser.add_argument('background_id')
    background_worker_parser.add_argument('prompt')
    background_worker_parser.add_argument('--resume-session-id')
    background_worker_parser.add_argument('--background-root', required=True)
    background_worker_parser.add_argument('--max-turns', type=int, default=12)
    background_worker_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(background_worker_parser, include_backend=True)

    ps_parser = subparsers.add_parser('agent-ps', help='list local background agent sessions')
    ps_parser.add_argument('--tail', type=int, default=None)

    logs_parser = subparsers.add_parser('agent-logs', help='show logs for a local background agent session')
    logs_parser.add_argument('background_id')
    logs_parser.add_argument('--tail', type=int, default=None)

    attach_parser = subparsers.add_parser('agent-attach', help='show the current output snapshot for a local background agent session')
    attach_parser.add_argument('background_id')
    attach_parser.add_argument('--tail', type=int, default=None)

    kill_parser = subparsers.add_parser('agent-kill', help='stop a local background agent session')
    kill_parser.add_argument('background_id')

    daemon_parser = subparsers.add_parser('daemon', help='manage local daemon-style background agent sessions')
    daemon_subparsers = daemon_parser.add_subparsers(dest='daemon_command')
    daemon_subparsers.required = True

    daemon_start_parser = daemon_subparsers.add_parser('start', help='launch a local daemon-style background agent session')
    daemon_start_parser.add_argument('prompt')
    daemon_start_parser.add_argument('--max-turns', type=int, default=12)
    daemon_start_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(daemon_start_parser, include_backend=True)

    daemon_worker_parser = daemon_subparsers.add_parser('worker', help=argparse.SUPPRESS)
    daemon_worker_parser.add_argument('background_id')
    daemon_worker_parser.add_argument('prompt')
    daemon_worker_parser.add_argument('--resume-session-id')
    daemon_worker_parser.add_argument('--background-root', required=True)
    daemon_worker_parser.add_argument('--max-turns', type=int, default=12)
    daemon_worker_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(daemon_worker_parser, include_backend=True)

    daemon_ps_parser = daemon_subparsers.add_parser('ps', help='list local daemon-style background sessions')
    daemon_ps_parser.add_argument('--tail', type=int, default=None)

    daemon_logs_parser = daemon_subparsers.add_parser('logs', help='show logs for a local daemon-style background session')
    daemon_logs_parser.add_argument('background_id')
    daemon_logs_parser.add_argument('--tail', type=int, default=None)

    daemon_attach_parser = daemon_subparsers.add_parser('attach', help='show the current output snapshot for a local daemon-style background session')
    daemon_attach_parser.add_argument('background_id')
    daemon_attach_parser.add_argument('--tail', type=int, default=None)

    daemon_kill_parser = daemon_subparsers.add_parser('kill', help='stop a local daemon-style background session')
    daemon_kill_parser.add_argument('background_id')

    chat_parser = subparsers.add_parser('agent-chat', help='run an interactive Python local-model chat loop')
    chat_parser.add_argument('prompt', nargs='?')
    chat_parser.add_argument('--resume-session-id')
    chat_parser.add_argument('--max-turns', type=int, default=12)
    chat_parser.add_argument('--show-transcript', action='store_true')
    _add_agent_common_args(chat_parser, include_backend=True)

    resume_parser = subparsers.add_parser('agent-resume', help='resume a saved Python local-model agent session')
    _add_agent_resume_args(resume_parser)

    prompt_parser = subparsers.add_parser('agent-prompt', help='render the Python agent system prompt')
    _add_agent_common_args(prompt_parser, include_backend=False)

    context_parser = subparsers.add_parser('agent-context', help='render Python /context-style usage accounting')
    _add_agent_common_args(context_parser, include_backend=False)

    context_raw_parser = subparsers.add_parser('agent-context-raw', help='render the raw Python agent context snapshot')
    _add_agent_common_args(context_raw_parser, include_backend=False)

    token_budget_parser = subparsers.add_parser('token-budget', help='render the current token budget and prompt-length limits')
    _add_agent_common_args(token_budget_parser, include_backend=False)

    agents_parser = subparsers.add_parser('agents', help='list active local agent configurations or show one agent definition')
    agents_parser.add_argument('agent_type', nargs='?')
    agents_parser.add_argument('--all', action='store_true')
    _add_agent_common_args(agents_parser, include_backend=False)

    agents_create_parser = subparsers.add_parser('agents-create', help='create a local agent definition markdown file')
    agents_create_parser.add_argument('agent_type')
    _add_agent_management_args(agents_create_parser)

    agents_update_parser = subparsers.add_parser('agents-update', help='update an existing local agent definition markdown file')
    agents_update_parser.add_argument('agent_type')
    _add_agent_management_args(agents_update_parser)

    agents_delete_parser = subparsers.add_parser('agents-delete', help='delete an existing local agent definition markdown file')
    agents_delete_parser.add_argument('agent_type')
    agents_delete_parser.add_argument('--cwd', default='.')
    agents_delete_parser.add_argument(
        '--source',
        default='auto',
        choices=('project', 'user', 'auto', 'projectSettings', 'userSettings'),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest = build_port_manifest()

    if args.command == 'summary':
        print(QueryEnginePort(manifest).render_summary())
        return 0
    if args.command == 'manifest':
        print(manifest.to_markdown())
        return 0
    if args.command == 'parity-audit':
        print(run_parity_audit().to_markdown())
        return 0
    if args.command == 'setup-report':
        print(run_setup().as_markdown())
        return 0
    if args.command == 'command-graph':
        print(build_command_graph().as_markdown())
        return 0
    if args.command == 'tool-pool':
        print(assemble_tool_pool().as_markdown())
        return 0
    if args.command == 'bootstrap-graph':
        print(build_bootstrap_graph().as_markdown())
        return 0
    if args.command == 'subsystems':
        for subsystem in manifest.top_level_modules[: args.limit]:
            print(f'{subsystem.name}\t{subsystem.file_count}\t{subsystem.notes}')
        return 0
    if args.command == 'commands':
        if args.query:
            print(render_command_index(limit=args.limit, query=args.query))
        else:
            commands = get_commands(
                include_plugin_commands=not args.no_plugin_commands,
                include_skill_commands=not args.no_skill_commands,
            )
            output_lines = [f'Command entries: {len(commands)}', '']
            output_lines.extend(f'- {module.name} — {module.source_hint}' for module in commands[: args.limit])
            print('\n'.join(output_lines))
        return 0
    if args.command == 'tools':
        if args.query:
            print(render_tool_index(limit=args.limit, query=args.query))
        else:
            permission_context = ToolPermissionContext.from_iterables(args.deny_tool, args.deny_prefix)
            tools = get_tools(
                simple_mode=args.simple_mode,
                include_mcp=not args.no_mcp,
                permission_context=permission_context,
            )
            output_lines = [f'Tool entries: {len(tools)}', '']
            output_lines.extend(f'- {module.name} — {module.source_hint}' for module in tools[: args.limit])
            print('\n'.join(output_lines))
        return 0
    if args.command == 'route':
        matches = PortRuntime().route_prompt(args.prompt, limit=args.limit)
        if not matches:
            print('No mirrored command/tool matches found.')
            return 0
        for match in matches:
            print(f'{match.kind}\t{match.name}\t{match.score}\t{match.source_hint}')
        return 0
    if args.command == 'bootstrap':
        print(PortRuntime().bootstrap_session(args.prompt, limit=args.limit).as_markdown())
        return 0
    if args.command == 'turn-loop':
        results = PortRuntime().run_turn_loop(
            args.prompt,
            limit=args.limit,
            max_turns=args.max_turns,
            structured_output=args.structured_output,
        )
        for idx, result in enumerate(results, start=1):
            print(f'## Turn {idx}')
            print(result.output)
            print(f'stop_reason={result.stop_reason}')
        return 0
    if args.command == 'flush-transcript':
        engine = QueryEnginePort.from_workspace()
        engine.submit_message(args.prompt)
        path = engine.persist_session()
        print(path)
        print(f'flushed={engine.transcript_store.flushed}')
        return 0
    if args.command == 'load-session':
        session = load_session(args.session_id)
        print(f'{session.session_id}\n{len(session.messages)} messages\nin={session.input_tokens} out={session.output_tokens}')
        return 0
    if args.command == 'remote-mode':
        print(run_remote_mode(args.target, cwd=Path(args.cwd).resolve()).as_text())
        return 0
    if args.command == 'ssh-mode':
        print(run_ssh_mode(args.target, cwd=Path(args.cwd).resolve()).as_text())
        return 0
    if args.command == 'teleport-mode':
        print(run_teleport_mode(args.target, cwd=Path(args.cwd).resolve()).as_text())
        return 0
    if args.command == 'direct-connect-mode':
        print(run_direct_connect_mode(args.target, cwd=Path(args.cwd).resolve()).as_text())
        return 0
    if args.command == 'deep-link-mode':
        print(run_deep_link_mode(args.target, cwd=Path(args.cwd).resolve()).as_text())
        return 0
    if args.command == 'remote-status':
        runtime = RemoteRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Remote')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'remote-profiles':
        runtime = RemoteRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_profiles_index(query=args.query))
        return 0
    if args.command == 'remote-disconnect':
        runtime = RemoteRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.disconnect().as_text())
        return 0
    if args.command == 'worktree-status':
        runtime = WorktreeRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Worktree')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'worktree-enter':
        runtime = WorktreeRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.enter(name=args.name).as_text())
        except (RuntimeError, ValueError) as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'worktree-exit':
        runtime = WorktreeRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.exit(
                    action=args.action,
                    discard_changes=args.discard_changes,
                ).as_text()
            )
        except (RuntimeError, ValueError) as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'account-status':
        runtime = AccountRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Account')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'account-profiles':
        runtime = AccountRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_profiles_index(query=args.query))
        return 0
    if args.command == 'account-login':
        runtime = AccountRuntime.from_workspace(Path(args.cwd).resolve())
        print(
            runtime.login(
                args.target,
                provider=args.provider,
                auth_mode=args.auth_mode,
            ).as_text()
        )
        return 0
    if args.command == 'account-logout':
        runtime = AccountRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.logout().as_text())
        return 0
    if args.command == 'ask-status':
        runtime = AskUserRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Ask User')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'ask-history':
        runtime = AskUserRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_history())
        return 0
    if args.command == 'search-status':
        runtime = SearchRuntime.from_workspace(Path(args.cwd).resolve())
        if args.provider:
            print(runtime.render_provider(args.provider))
        else:
            print('# Search')
            print()
            print(runtime.render_summary())
        return 0
    if args.command == 'search-providers':
        runtime = SearchRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_providers_index(query=args.query))
        return 0
    if args.command == 'search-activate':
        runtime = SearchRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            report = runtime.activate_provider(args.provider)
        except KeyError:
            print(f'Unknown search provider: {args.provider}')
            return 1
        print(report.as_text())
        return 0
    if args.command == 'search':
        runtime = SearchRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            output = runtime.render_search_results(
                args.query,
                provider_name=args.provider,
                max_results=args.max_results,
                domains=tuple(args.domain),
            )
        except (KeyError, LookupError, OSError, ValueError) as exc:
            print(f'Search failed: {exc}')
            return 1
        print(output)
        return 0
    if args.command == 'mcp-status':
        runtime = MCPRuntime.from_workspace(Path(args.cwd).resolve())
        print('# MCP')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'mcp-resources':
        runtime = MCPRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_resource_index(query=args.query))
        return 0
    if args.command == 'mcp-resource':
        runtime = MCPRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_resource(args.uri))
        return 0
    if args.command == 'mcp-tools':
        runtime = MCPRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_tool_index(query=args.query, server_name=args.server))
        return 0
    if args.command == 'mcp-call-tool':
        runtime = MCPRuntime.from_workspace(Path(args.cwd).resolve())
        arguments = json.loads(args.arguments_json)
        if not isinstance(arguments, dict):
            print('arguments-json must decode to a JSON object')
            return 1
        print(
            runtime.render_tool_call(
                args.tool_name,
                arguments=arguments,
                server_name=args.server,
            )
        )
        return 0
    if args.command == 'config-status':
        runtime = ConfigRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Config')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'config-effective':
        runtime = ConfigRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_effective_config())
        return 0
    if args.command == 'config-source':
        runtime = ConfigRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_source(args.source))
        return 0
    if args.command == 'config-get':
        runtime = ConfigRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_value(args.key_path, source=args.source))
        return 0
    if args.command == 'config-set':
        runtime = ConfigRuntime.from_workspace(Path(args.cwd).resolve())
        value = json.loads(args.value_json)
        mutation = runtime.set_value(args.key_path, value, source=args.source)
        print('# Config')
        print()
        print(f'source={mutation.source_name}')
        print(f'key_path={mutation.key_path}')
        print(f'store_path={mutation.store_path}')
        print(f'effective_key_count={mutation.effective_key_count}')
        print(runtime.render_value(args.key_path))
        return 0
    if args.command == 'lsp-status':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        print('# LSP')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'lsp-symbols':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_document_symbols(args.file_path))
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-workspace-symbols':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_workspace_symbols(args.query, max_results=args.max_results))
        return 0
    if args.command == 'lsp-definition':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.render_definition(
                    args.file_path,
                    args.line,
                    args.character,
                    max_results=args.max_results,
                )
            )
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-references':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.render_references(
                    args.file_path,
                    args.line,
                    args.character,
                    max_results=args.max_results,
                )
            )
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-hover':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_hover(args.file_path, args.line, args.character))
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-diagnostics':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_diagnostics(args.file_path))
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-call-hierarchy':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.render_prepare_call_hierarchy(
                    args.file_path,
                    args.line,
                    args.character,
                )
            )
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-incoming-calls':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.render_incoming_calls(
                    args.file_path,
                    args.line,
                    args.character,
                    max_results=args.max_results,
                )
            )
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'lsp-outgoing-calls':
        runtime = LSPRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(
                runtime.render_outgoing_calls(
                    args.file_path,
                    args.line,
                    args.character,
                    max_results=args.max_results,
                )
            )
        except KeyError as exc:
            print(exc)
            return 1
        return 0
    if args.command == 'workflow-list':
        runtime = WorkflowRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_workflows_index(query=args.query))
        return 0
    if args.command == 'workflow-get':
        runtime = WorkflowRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_workflow(args.workflow_name))
        except KeyError:
            print(f'Unknown workflow: {args.workflow_name}')
            return 1
        return 0
    if args.command == 'workflow-run':
        runtime = WorkflowRuntime.from_workspace(Path(args.cwd).resolve())
        arguments = json.loads(args.arguments_json)
        if not isinstance(arguments, dict):
            print('arguments-json must decode to a JSON object')
            return 1
        try:
            print(runtime.render_run_report(args.workflow_name, arguments=arguments))
        except KeyError:
            print(f'Unknown workflow: {args.workflow_name}')
            return 1
        return 0
    if args.command == 'trigger-list':
        runtime = RemoteTriggerRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_trigger_index(query=args.query))
        return 0
    if args.command == 'trigger-get':
        runtime = RemoteTriggerRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_trigger(args.trigger_id))
        except KeyError:
            print(f'Unknown remote trigger: {args.trigger_id}')
            return 1
        return 0
    if args.command == 'trigger-create':
        runtime = RemoteTriggerRuntime.from_workspace(Path(args.cwd).resolve())
        body = json.loads(args.body_json)
        if not isinstance(body, dict):
            print('body-json must decode to a JSON object')
            return 1
        try:
            trigger = runtime.create_trigger(body)
        except (KeyError, TypeError, ValueError) as exc:
            print(exc)
            return 1
        print(runtime.render_trigger(trigger.trigger_id))
        return 0
    if args.command == 'trigger-update':
        runtime = RemoteTriggerRuntime.from_workspace(Path(args.cwd).resolve())
        body = json.loads(args.body_json)
        if not isinstance(body, dict):
            print('body-json must decode to a JSON object')
            return 1
        try:
            trigger = runtime.update_trigger(args.trigger_id, body)
        except (KeyError, TypeError, ValueError) as exc:
            print(exc)
            return 1
        print(runtime.render_trigger(trigger.trigger_id))
        return 0
    if args.command == 'trigger-run':
        runtime = RemoteTriggerRuntime.from_workspace(Path(args.cwd).resolve())
        body = json.loads(args.body_json)
        if not isinstance(body, dict):
            print('body-json must decode to a JSON object')
            return 1
        try:
            print(runtime.render_run_report(args.trigger_id, body=body))
        except KeyError:
            print(f'Unknown remote trigger: {args.trigger_id}')
            return 1
        return 0
    if args.command == 'team-status':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        print('# Teams')
        print()
        print(runtime.render_summary())
        return 0
    if args.command == 'team-list':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        print(runtime.render_teams_index(query=args.query))
        return 0
    if args.command == 'team-get':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_team(args.team_name))
        except KeyError:
            print(f'Unknown team: {args.team_name}')
            return 1
        return 0
    if args.command == 'team-create':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            team = runtime.create_team(
                args.team_name,
                description=args.description,
                members=args.member,
            )
        except KeyError:
            print(f'Team already exists: {args.team_name}')
            return 1
        print(f'created team {team.name}')
        return 0
    if args.command == 'team-delete':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            team = runtime.delete_team(args.team_name)
        except KeyError:
            print(f'Unknown team: {args.team_name}')
            return 1
        print(f'deleted team {team.name}')
        return 0
    if args.command == 'team-messages':
        runtime = TeamRuntime.from_workspace(Path(args.cwd).resolve())
        try:
            print(runtime.render_messages(team_name=args.team_name))
        except KeyError:
            print(f'Unknown team: {args.team_name}')
            return 1
        return 0
    if args.command == 'show-command':
        module = get_command(args.name)
        if module is None:
            print(f'Command not found: {args.name}')
            return 1
        print('\n'.join([module.name, module.source_hint, module.responsibility]))
        return 0
    if args.command == 'show-tool':
        module = get_tool(args.name)
        if module is None:
            print(f'Tool not found: {args.name}')
            return 1
        print('\n'.join([module.name, module.source_hint, module.responsibility]))
        return 0
    if args.command == 'exec-command':
        result = execute_command(args.name, args.prompt)
        print(result.message)
        return 0 if result.handled else 1
    if args.command == 'exec-tool':
        result = execute_tool(args.name, args.payload)
        print(result.message)
        return 0 if result.handled else 1
    if args.command == 'agent':
        agent = _build_agent(args)
        result = agent.run(args.prompt)
        _print_agent_result(result, show_transcript=args.show_transcript)
        return 0
    if args.command == 'agent-bg':
        return _launch_background_agent(args)
    if args.command == 'agent-bg-worker':
        return _run_background_worker(args)
    if args.command == 'agent-ps':
        print(BackgroundSessionRuntime().render_ps())
        return 0
    if args.command == 'agent-logs':
        print(
            BackgroundSessionRuntime().render_logs(
                args.background_id,
                tail=args.tail,
            )
        )
        return 0
    if args.command == 'agent-attach':
        print(
            BackgroundSessionRuntime().render_attach(
                args.background_id,
                tail=args.tail,
            )
        )
        return 0
    if args.command == 'agent-kill':
        record = BackgroundSessionRuntime().kill(args.background_id)
        print('# Background Session')
        print(f'background_id={record.background_id}')
        print(f'status={record.status}')
        print(f'pid={record.pid}')
        if record.exit_code is not None:
            print(f'exit_code={record.exit_code}')
        return 0
    if args.command == 'daemon':
        if args.daemon_command == 'start':
            return _launch_background_agent(args)
        if args.daemon_command == 'worker':
            return _run_background_worker(args)
        if args.daemon_command == 'ps':
            print(BackgroundSessionRuntime().render_ps())
            return 0
        if args.daemon_command == 'logs':
            print(
                BackgroundSessionRuntime().render_logs(
                    args.background_id,
                    tail=args.tail,
                )
            )
            return 0
        if args.daemon_command == 'attach':
            print(
                BackgroundSessionRuntime().render_attach(
                    args.background_id,
                    tail=args.tail,
                )
            )
            return 0
        if args.daemon_command == 'kill':
            record = BackgroundSessionRuntime().kill(args.background_id)
            print('# Background Session')
            print(f'background_id={record.background_id}')
            print(f'status={record.status}')
            print(f'pid={record.pid}')
            if record.exit_code is not None:
                print(f'exit_code={record.exit_code}')
            return 0
    if args.command == 'agent-chat':
        # Latti boot hook: gather system state and inject into prompt
        if os.environ.get('LATTI_BOOT', '0') == '1':
            try:
                from .latti_boot import gather_boot_context
                boot_ctx = gather_boot_context()
                if boot_ctx and args.append_system_prompt:
                    args.append_system_prompt = args.append_system_prompt + '\n\n' + boot_ctx
                elif boot_ctx:
                    args.append_system_prompt = boot_ctx
            except Exception:
                pass  # boot hook failure is non-fatal
        agent = _build_agent(args)
        worker_runner = None
        supervisor_mode = os.environ.get('LATTI_USE_CHAT_SUPERVISOR', '1')
        supervisor_forced = (
            os.environ.get('LATTI_FORCE_CHAT_SUPERVISOR') == '1'
            or supervisor_mode.lower() == 'force'
        )
        supervisor_allowed = supervisor_mode != '0'
        supervisor_terminal_ready = sys.stdin.isatty() and sys.stdout.isatty()
        if supervisor_allowed and (supervisor_forced or supervisor_terminal_ready):
            worker_runner = _build_background_chat_worker_runner(args)
        return _run_agent_chat_loop(
            agent,
            initial_prompt=args.prompt,
            resume_session_id=args.resume_session_id,
            show_transcript=args.show_transcript,
            worker_runner=worker_runner,
        )
    if args.command == 'agent-resume':
        agent, stored_session = _build_resumed_agent(args)
        result = agent.resume(args.prompt, stored_session)
        _print_agent_result(result, show_transcript=args.show_transcript)
        return 0
    if args.command == 'agent-prompt':
        agent = _build_agent(args)
        print(agent.render_system_prompt())
        return 0
    if args.command == 'agent-context':
        agent = _build_agent(args)
        print(agent.render_context_report())
        return 0
    if args.command == 'agent-context-raw':
        agent = _build_agent(args)
        print(agent.render_context_snapshot_report())
        return 0
    if args.command == 'token-budget':
        agent = _build_agent(args)
        agent.last_session = agent.build_session()
        print(agent.render_token_budget_report())
        return 0
    if args.command == 'agents':
        agent = _build_agent(args)
        if args.agent_type:
            print(agent.render_agent_detail_report(args.agent_type))
        else:
            print(agent.render_agents_report(show_all=bool(args.all)))
        return 0
    if args.command == 'agents-create':
        prompt_text = _resolve_agent_prompt_text(args)
        result = create_agent_definition(
            Path(args.cwd).resolve(),
            agent_type=args.agent_type,
            description=args.description or f'Use this agent when the task calls for {args.agent_type}.',
            system_prompt=prompt_text
            or (
                f'You are the {args.agent_type} agent.\n'
                'Read the task carefully, use the available tools deliberately, and return a concise result.'
            ),
            source=normalize_mutable_source(args.source),
            overwrite=bool(args.overwrite),
            tools=_parse_tools_flag(args.tools),
            model=args.model,
            color=args.color,
            permission_mode=args.permission_mode,
            max_turns=args.max_turns,
            initial_prompt=args.initial_prompt,
            background=bool(args.background),
            one_shot=bool(args.one_shot),
            omit_claude_md=bool(args.omit_claude_md),
        )
        print(render_agent_mutation(result))
        return 0
    if args.command == 'agents-update':
        prompt_text = _resolve_agent_prompt_text(args)
        update_kwargs: dict[str, object] = {}
        if args.description is not None:
            update_kwargs['description'] = args.description
        if prompt_text is not None:
            update_kwargs['system_prompt'] = prompt_text
        if args.tools is not None:
            update_kwargs['tools'] = _parse_tools_flag(args.tools)
        if args.model is not None:
            update_kwargs['model'] = args.model
        if args.color is not None:
            update_kwargs['color'] = args.color
        if args.permission_mode is not None:
            update_kwargs['permission_mode'] = args.permission_mode
        if args.max_turns is not None:
            update_kwargs['max_turns'] = args.max_turns
        if args.initial_prompt is not None:
            update_kwargs['initial_prompt'] = args.initial_prompt
        if args.background:
            update_kwargs['background'] = True
        if args.one_shot:
            update_kwargs['one_shot'] = True
        if args.omit_claude_md:
            update_kwargs['omit_claude_md'] = True
        result = update_agent_definition(
            Path(args.cwd).resolve(),
            agent_type=args.agent_type,
            source=normalize_mutable_source(args.source, allow_auto=True),
            **update_kwargs,
        )
        print(render_agent_mutation(result))
        return 0
    if args.command == 'agents-delete':
        result = delete_agent_definition(
            Path(args.cwd).resolve(),
            agent_type=args.agent_type,
            source=normalize_mutable_source(args.source, allow_auto=True),
        )
        print(render_agent_mutation(result))
        return 0

    parser.error(f'unknown command: {args.command}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
