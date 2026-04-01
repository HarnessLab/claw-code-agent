from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


@dataclass(frozen=True)
class ParsedSlashCommand:
    command_name: str
    args: str
    is_mcp: bool


@dataclass(frozen=True)
class SlashCommandResult:
    handled: bool
    should_query: bool
    prompt: str | None = None
    output: str = ''
    transcript: tuple[dict[str, Any], ...] = ()


SlashCommandHandler = Callable[['LocalCodingAgent', str, str], SlashCommandResult]


@dataclass(frozen=True)
class SlashCommandSpec:
    names: tuple[str, ...]
    description: str
    handler: SlashCommandHandler


def parse_slash_command(input_text: str) -> ParsedSlashCommand | None:
    trimmed = input_text.strip()
    if not trimmed.startswith('/'):
        return None

    without_slash = trimmed[1:]
    words = without_slash.split(' ')
    if not words or not words[0]:
        return None

    command_name = words[0]
    is_mcp = False
    args_start_index = 1
    if len(words) > 1 and words[1] == '(MCP)':
        command_name = f'{command_name} (MCP)'
        is_mcp = True
        args_start_index = 2

    return ParsedSlashCommand(
        command_name=command_name,
        args=' '.join(words[args_start_index:]),
        is_mcp=is_mcp,
    )


def looks_like_command(command_name: str) -> bool:
    return re.search(r'[^a-zA-Z0-9:\-_]', command_name) is None


def preprocess_slash_command(
    agent: 'LocalCodingAgent',
    input_text: str,
) -> SlashCommandResult:
    if not input_text.strip().startswith('/'):
        return SlashCommandResult(handled=False, should_query=True, prompt=input_text)

    parsed = parse_slash_command(input_text)
    if parsed is None:
        return _local_result(
            input_text,
            'Commands are in the form `/command [args]`.',
        )

    if parsed.is_mcp:
        return _local_result(
            input_text,
            'MCP slash commands are not implemented in the Python runtime yet.',
        )

    spec = find_slash_command(parsed.command_name)
    if spec is None:
        if looks_like_command(parsed.command_name):
            return _local_result(input_text, f'Unknown skill: {parsed.command_name}')
        return SlashCommandResult(handled=False, should_query=True, prompt=input_text)

    return spec.handler(agent, parsed.args.strip(), input_text)


def get_slash_command_specs() -> tuple[SlashCommandSpec, ...]:
    return (
        SlashCommandSpec(
            names=('help', 'commands'),
            description='Show the built-in Python slash commands.',
            handler=_handle_help,
        ),
        SlashCommandSpec(
            names=('context', 'usage'),
            description='Show estimated session context usage similar to the npm /context command.',
            handler=_handle_context,
        ),
        SlashCommandSpec(
            names=('context-raw', 'env'),
            description='Show the raw environment, user context, and system context snapshot.',
            handler=_handle_context_raw,
        ),
        SlashCommandSpec(
            names=('prompt', 'system-prompt'),
            description='Render the effective Python system prompt.',
            handler=_handle_prompt,
        ),
        SlashCommandSpec(
            names=('permissions',),
            description='Show the active tool permission mode.',
            handler=_handle_permissions,
        ),
        SlashCommandSpec(
            names=('model',),
            description='Show or update the active model for the current agent instance.',
            handler=_handle_model,
        ),
        SlashCommandSpec(
            names=('tools',),
            description='List the registered tools and whether the current permissions allow them.',
            handler=_handle_tools,
        ),
        SlashCommandSpec(
            names=('memory',),
            description='Show the currently loaded CLAUDE.md memory bundle and discovered files.',
            handler=_handle_memory,
        ),
        SlashCommandSpec(
            names=('status', 'session'),
            description='Show a short runtime/session status summary.',
            handler=_handle_status,
        ),
        SlashCommandSpec(
            names=('clear',),
            description='Clear ephemeral Python runtime state for this process.',
            handler=_handle_clear,
        ),
    )


def find_slash_command(command_name: str) -> SlashCommandSpec | None:
    lowered = command_name.lower()
    for spec in get_slash_command_specs():
        if lowered in spec.names:
            return spec
    return None


def _handle_help(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    lines = ['# Slash Commands', '']
    for spec in get_slash_command_specs():
        primary = f'/{spec.names[0]}'
        aliases = ', '.join(f'/{name}' for name in spec.names[1:])
        label = f'{primary} ({aliases})' if aliases else primary
        lines.append(f'- `{label}`: {spec.description}')
    lines.extend(
        [
            '',
            'These commands are handled locally before the model loop, similar to the npm runtime.',
        ]
    )
    return _local_result(input_text, '\n'.join(lines))


def _handle_context(agent: 'LocalCodingAgent', args: str, input_text: str) -> SlashCommandResult:
    prompt = args or None
    return _local_result(input_text, agent.render_context_report(prompt))


def _handle_context_raw(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_context_snapshot_report())


def _handle_prompt(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_system_prompt())


def _handle_permissions(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_permissions_report())


def _handle_model(agent: 'LocalCodingAgent', args: str, input_text: str) -> SlashCommandResult:
    if not args:
        return _local_result(input_text, f'Current model: {agent.model_config.model}')
    agent.set_model(args)
    return _local_result(input_text, f'Set model to {agent.model_config.model}')


def _handle_tools(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_tools_report())


def _handle_memory(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_memory_report())


def _handle_status(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    return _local_result(input_text, agent.render_status_report())


def _handle_clear(agent: 'LocalCodingAgent', _args: str, input_text: str) -> SlashCommandResult:
    agent.clear_runtime_state()
    return _local_result(
        input_text,
        'Cleared ephemeral Python agent state for this process.',
    )


def _local_result(input_text: str, output: str) -> SlashCommandResult:
    transcript = (
        {'role': 'user', 'content': input_text},
        {'role': 'assistant', 'content': output},
    )
    return SlashCommandResult(
        handled=True,
        should_query=False,
        output=output,
        transcript=transcript,
    )
