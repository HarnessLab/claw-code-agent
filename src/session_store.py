from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    BudgetConfig,
    ModelConfig,
    ModelPricing,
    OutputSchemaConfig,
    UsageStats,
)


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
    usage: JSONDict
    total_cost_usd: float
    file_history: tuple[JSONDict, ...]
    budget_state: JSONDict
    plugin_state: JSONDict
    scratchpad_directory: str | None = None


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
        usage=dict(data.get('usage', {})),
        total_cost_usd=float(data.get('total_cost_usd', 0.0)),
        file_history=tuple(
            entry for entry in data.get('file_history', []) if isinstance(entry, dict)
        ),
        budget_state=(
            dict(data.get('budget_state', {}))
            if isinstance(data.get('budget_state'), dict)
            else {}
        ),
        plugin_state=(
            dict(data.get('plugin_state', {}))
            if isinstance(data.get('plugin_state'), dict)
            else {}
        ),
        scratchpad_directory=(
            str(data['scratchpad_directory'])
            if isinstance(data.get('scratchpad_directory'), str)
            else None
        ),
    )


def serialize_model_config(model_config: ModelConfig) -> JSONDict:
    return {
        'model': model_config.model,
        'base_url': model_config.base_url,
        'api_key': model_config.api_key,
        'temperature': model_config.temperature,
        'timeout_seconds': model_config.timeout_seconds,
        'pricing': {
            'input_cost_per_million_tokens_usd': model_config.pricing.input_cost_per_million_tokens_usd,
            'output_cost_per_million_tokens_usd': model_config.pricing.output_cost_per_million_tokens_usd,
            'cache_creation_input_cost_per_million_tokens_usd': model_config.pricing.cache_creation_input_cost_per_million_tokens_usd,
            'cache_read_input_cost_per_million_tokens_usd': model_config.pricing.cache_read_input_cost_per_million_tokens_usd,
        },
    }


def deserialize_model_config(payload: JSONDict) -> ModelConfig:
    return ModelConfig(
        model=str(payload['model']),
        base_url=str(payload.get('base_url', 'http://127.0.0.1:8000/v1')),
        api_key=str(payload.get('api_key', 'local-token')),
        temperature=float(payload.get('temperature', 0.0)),
        timeout_seconds=float(payload.get('timeout_seconds', 120.0)),
        pricing=_deserialize_pricing(payload.get('pricing')),
    )


def serialize_runtime_config(runtime_config: AgentRuntimeConfig) -> JSONDict:
    return {
        'cwd': str(runtime_config.cwd),
        'max_turns': runtime_config.max_turns,
        'command_timeout_seconds': runtime_config.command_timeout_seconds,
        'max_output_chars': runtime_config.max_output_chars,
        'stream_model_responses': runtime_config.stream_model_responses,
        'auto_snip_threshold_tokens': runtime_config.auto_snip_threshold_tokens,
        'auto_compact_threshold_tokens': runtime_config.auto_compact_threshold_tokens,
        'compact_preserve_messages': runtime_config.compact_preserve_messages,
        'permissions': {
            'allow_file_write': runtime_config.permissions.allow_file_write,
            'allow_shell_commands': runtime_config.permissions.allow_shell_commands,
            'allow_destructive_shell_commands': runtime_config.permissions.allow_destructive_shell_commands,
        },
        'additional_working_directories': [str(path) for path in runtime_config.additional_working_directories],
        'disable_claude_md_discovery': runtime_config.disable_claude_md_discovery,
        'budget_config': {
            'max_total_tokens': runtime_config.budget_config.max_total_tokens,
            'max_input_tokens': runtime_config.budget_config.max_input_tokens,
            'max_output_tokens': runtime_config.budget_config.max_output_tokens,
            'max_reasoning_tokens': runtime_config.budget_config.max_reasoning_tokens,
            'max_total_cost_usd': runtime_config.budget_config.max_total_cost_usd,
            'max_tool_calls': runtime_config.budget_config.max_tool_calls,
            'max_delegated_tasks': runtime_config.budget_config.max_delegated_tasks,
            'max_model_calls': runtime_config.budget_config.max_model_calls,
            'max_session_turns': runtime_config.budget_config.max_session_turns,
        },
        'output_schema': (
            {
                'name': runtime_config.output_schema.name,
                'schema': runtime_config.output_schema.schema,
                'strict': runtime_config.output_schema.strict,
            }
            if runtime_config.output_schema is not None
            else None
        ),
        'session_directory': str(runtime_config.session_directory),
        'scratchpad_root': str(runtime_config.scratchpad_root),
    }


def deserialize_runtime_config(payload: JSONDict) -> AgentRuntimeConfig:
    permissions_payload = payload.get('permissions')
    if not isinstance(permissions_payload, dict):
        permissions_payload = {}
    budget_payload = payload.get('budget_config')
    if not isinstance(budget_payload, dict):
        budget_payload = {}
    output_schema_payload = payload.get('output_schema')
    return AgentRuntimeConfig(
        cwd=Path(str(payload['cwd'])).resolve(),
        max_turns=int(payload.get('max_turns', 12)),
        command_timeout_seconds=float(payload.get('command_timeout_seconds', 30.0)),
        max_output_chars=int(payload.get('max_output_chars', 12000)),
        stream_model_responses=bool(payload.get('stream_model_responses', False)),
        auto_snip_threshold_tokens=_optional_int(payload.get('auto_snip_threshold_tokens')),
        auto_compact_threshold_tokens=_optional_int(payload.get('auto_compact_threshold_tokens')),
        compact_preserve_messages=int(payload.get('compact_preserve_messages', 4)),
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
        budget_config=BudgetConfig(
            max_total_tokens=_optional_int(budget_payload.get('max_total_tokens')),
            max_input_tokens=_optional_int(budget_payload.get('max_input_tokens')),
            max_output_tokens=_optional_int(budget_payload.get('max_output_tokens')),
            max_reasoning_tokens=_optional_int(budget_payload.get('max_reasoning_tokens')),
            max_total_cost_usd=_optional_float(budget_payload.get('max_total_cost_usd')),
            max_tool_calls=_optional_int(budget_payload.get('max_tool_calls')),
            max_delegated_tasks=_optional_int(budget_payload.get('max_delegated_tasks')),
            max_model_calls=_optional_int(budget_payload.get('max_model_calls')),
            max_session_turns=_optional_int(budget_payload.get('max_session_turns')),
        ),
        output_schema=_deserialize_output_schema(output_schema_payload),
        session_directory=Path(str(payload.get('session_directory', DEFAULT_AGENT_SESSION_DIR))).resolve(),
        scratchpad_root=Path(str(payload.get('scratchpad_root', DEFAULT_SESSION_DIR / 'scratchpad'))).resolve(),
    )


def usage_from_payload(payload: JSONDict | None) -> UsageStats:
    if not isinstance(payload, dict):
        return UsageStats()
    return UsageStats(
        input_tokens=_optional_int(payload.get('input_tokens')) or 0,
        output_tokens=_optional_int(payload.get('output_tokens')) or 0,
        cache_creation_input_tokens=_optional_int(payload.get('cache_creation_input_tokens')) or 0,
        cache_read_input_tokens=_optional_int(payload.get('cache_read_input_tokens')) or 0,
        reasoning_tokens=_optional_int(payload.get('reasoning_tokens')) or 0,
    )


def _deserialize_pricing(payload: Any) -> ModelPricing:
    if not isinstance(payload, dict):
        return ModelPricing()
    return ModelPricing(
        input_cost_per_million_tokens_usd=_optional_float(payload.get('input_cost_per_million_tokens_usd')) or 0.0,
        output_cost_per_million_tokens_usd=_optional_float(payload.get('output_cost_per_million_tokens_usd')) or 0.0,
        cache_creation_input_cost_per_million_tokens_usd=(
            _optional_float(payload.get('cache_creation_input_cost_per_million_tokens_usd'))
            or 0.0
        ),
        cache_read_input_cost_per_million_tokens_usd=(
            _optional_float(payload.get('cache_read_input_cost_per_million_tokens_usd'))
            or 0.0
        ),
    )


def _deserialize_output_schema(payload: Any) -> OutputSchemaConfig | None:
    if not isinstance(payload, dict):
        return None
    schema = payload.get('schema')
    if not isinstance(schema, dict):
        return None
    name = payload.get('name')
    if not isinstance(name, str) or not name:
        return None
    return OutputSchemaConfig(
        name=name,
        schema=dict(schema),
        strict=bool(payload.get('strict', False)),
    )


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
