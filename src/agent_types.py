from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: 'UsageStats') -> 'UsageStats':
        return UsageStats(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> JSONDict:
        return {
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'cache_creation_input_tokens': self.cache_creation_input_tokens,
            'cache_read_input_tokens': self.cache_read_input_tokens,
            'reasoning_tokens': self.reasoning_tokens,
            'total_tokens': self.total_tokens,
        }


@dataclass(frozen=True)
class ModelPricing:
    input_cost_per_million_tokens_usd: float = 0.0
    output_cost_per_million_tokens_usd: float = 0.0
    cache_creation_input_cost_per_million_tokens_usd: float = 0.0
    cache_read_input_cost_per_million_tokens_usd: float = 0.0

    def estimate_cost_usd(self, usage: UsageStats) -> float:
        return (
            (usage.input_tokens / 1_000_000.0) * self.input_cost_per_million_tokens_usd
            + (usage.output_tokens / 1_000_000.0) * self.output_cost_per_million_tokens_usd
            + (
                usage.cache_creation_input_tokens / 1_000_000.0
            )
            * self.cache_creation_input_cost_per_million_tokens_usd
            + (
                usage.cache_read_input_tokens / 1_000_000.0
            )
            * self.cache_read_input_cost_per_million_tokens_usd
        )


@dataclass(frozen=True)
class BudgetConfig:
    max_total_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_reasoning_tokens: int | None = None
    max_total_cost_usd: float | None = None
    max_tool_calls: int | None = None
    max_delegated_tasks: int | None = None
    max_model_calls: int | None = None
    max_session_turns: int | None = None


@dataclass(frozen=True)
class OutputSchemaConfig:
    name: str
    schema: JSONDict
    strict: bool = False


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str = 'http://127.0.0.1:8000/v1'
    api_key: str = 'local-token'
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    pricing: ModelPricing = field(default_factory=ModelPricing)


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
    usage: UsageStats = field(default_factory=UsageStats)


@dataclass(frozen=True)
class StreamEvent:
    type: str
    delta: str = ''
    tool_call_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ''
    finish_reason: str | None = None
    usage: UsageStats = field(default_factory=UsageStats)
    raw_event: JSONDict = field(default_factory=dict)

    def to_dict(self) -> JSONDict:
        return {
            'type': self.type,
            'delta': self.delta,
            'tool_call_index': self.tool_call_index,
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
            'arguments_delta': self.arguments_delta,
            'finish_reason': self.finish_reason,
            'usage': self.usage.to_dict(),
            'raw_event': dict(self.raw_event),
        }


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
    stream_model_responses: bool = False
    auto_snip_threshold_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    compact_preserve_messages: int = 4
    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    additional_working_directories: tuple[Path, ...] = ()
    disable_claude_md_discovery: bool = False
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)
    output_schema: OutputSchemaConfig | None = None
    session_directory: Path = field(default_factory=lambda: (Path('.port_sessions') / 'agent').resolve())
    scratchpad_root: Path = field(default_factory=lambda: (Path('.port_sessions') / 'scratchpad').resolve())


@dataclass(frozen=True)
class ToolExecutionResult:
    name: str
    ok: bool
    content: str
    metadata: JSONDict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunResult:
    final_output: str
    turns: int
    tool_calls: int
    transcript: tuple[JSONDict, ...]
    events: tuple[JSONDict, ...] = ()
    usage: UsageStats = field(default_factory=UsageStats)
    total_cost_usd: float = 0.0
    stop_reason: str | None = None
    file_history: tuple[JSONDict, ...] = ()
    session_id: str | None = None
    session_path: str | None = None
    scratchpad_directory: str | None = None
