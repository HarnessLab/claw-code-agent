from __future__ import annotations

import argparse
import os
from pathlib import Path
from dataclasses import replace
import json
from typing import Callable

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
from .direct_modes import run_deep_link, run_direct_connect
from .parity_audit import run_parity_audit
from .permissions import ToolPermissionContext
from .port_manifest import build_port_manifest
from .query_engine import QueryEnginePort
from .remote_runtime import run_remote_mode, run_ssh_mode, run_teleport_mode
from .runtime import PortRuntime
from .session_store import (
    StoredAgentSession,
    deserialize_model_config,
    deserialize_runtime_config,
    load_agent_session,
    load_session,
)
from .setup import run_setup
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


def _build_agent(args: argparse.Namespace) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=_build_model_config(args),
        runtime_config=_build_runtime_config(args),
        custom_system_prompt=args.system_prompt,
        append_system_prompt=args.append_system_prompt,
        override_system_prompt=args.override_system_prompt,
    )


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


def _print_agent_result(result, *, show_transcript: bool) -> None:
    print(result.final_output)
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


def _run_agent_chat_loop(
    agent: LocalCodingAgent,
    *,
    initial_prompt: str | None,
    resume_session_id: str | None,
    show_transcript: bool,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    result_printer: Callable[..., None] = _print_agent_result,
) -> int:
    active_session_id = resume_session_id
    first_prompt = initial_prompt

    output_func('# Agent Chat')
    output_func("Enter a prompt. Use '/exit' or '/quit' to stop.")
    if active_session_id:
        output_func(f'resuming_session_id={active_session_id}')

    while True:
        if first_prompt is not None:
            prompt = first_prompt
            first_prompt = None
        else:
            try:
                prompt = input_func('user> ')
            except EOFError:
                output_func('chat_ended=eof')
                return 0
            except KeyboardInterrupt:
                output_func('\nchat_ended=interrupt')
                return 130

        normalized = prompt.strip()
        if not normalized:
            continue
        if normalized in {'/exit', '/quit'}:
            output_func('chat_ended=user_exit')
            return 0

        if active_session_id:
            stored_session = load_agent_session(
                active_session_id,
                directory=agent.runtime_config.session_directory,
            )
            result = agent.resume(prompt, stored_session)
        else:
            result = agent.run(prompt)
        result_printer(result, show_transcript=show_transcript)
        active_session_id = result.session_id


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
    ssh_parser = subparsers.add_parser('ssh-mode', help='simulate SSH runtime branching')
    ssh_parser.add_argument('target')
    teleport_parser = subparsers.add_parser('teleport-mode', help='simulate teleport runtime branching')
    teleport_parser.add_argument('target')
    direct_parser = subparsers.add_parser('direct-connect-mode', help='simulate direct-connect runtime branching')
    direct_parser.add_argument('target')
    deep_link_parser = subparsers.add_parser('deep-link-mode', help='simulate deep-link runtime branching')
    deep_link_parser.add_argument('target')

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
        print(run_remote_mode(args.target).as_text())
        return 0
    if args.command == 'ssh-mode':
        print(run_ssh_mode(args.target).as_text())
        return 0
    if args.command == 'teleport-mode':
        print(run_teleport_mode(args.target).as_text())
        return 0
    if args.command == 'direct-connect-mode':
        print(run_direct_connect(args.target).as_text())
        return 0
    if args.command == 'deep-link-mode':
        print(run_deep_link(args.target).as_text())
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
    if args.command == 'agent-chat':
        agent = _build_agent(args)
        return _run_agent_chat_loop(
            agent,
            initial_prompt=args.prompt,
            resume_session_id=args.resume_session_id,
            show_transcript=args.show_transcript,
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

    parser.error(f'unknown command: {args.command}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
