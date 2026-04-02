from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .agent_manager import AgentManager
from .agent_context import render_context_report as render_agent_context_report
from .agent_context_usage import collect_context_usage, estimate_tokens, format_context_usage
from .agent_prompting import (
    build_prompt_context,
    build_system_prompt_parts,
    render_system_prompt,
)
from .agent_session import AgentSessionState
from .agent_slash_commands import preprocess_slash_command
from .agent_tools import (
    AgentTool,
    build_tool_context,
    default_tool_registry,
    execute_tool_streaming,
    serialize_tool_result,
)
from .agent_types import (
    AgentRunResult,
    AgentPermissions,
    AgentRuntimeConfig,
    AssistantTurn,
    BudgetConfig,
    ModelConfig,
    OutputSchemaConfig,
    StreamEvent,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from .openai_compat import OpenAICompatClient, OpenAICompatError
from .plugin_runtime import PluginRuntime
from .session_store import (
    StoredAgentSession,
    save_agent_session,
    serialize_model_config,
    serialize_runtime_config,
)


@dataclass(frozen=True)
class BudgetDecision:
    exceeded: bool
    reason: str | None = None


@dataclass
class LocalCodingAgent:
    model_config: ModelConfig
    runtime_config: AgentRuntimeConfig
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    override_system_prompt: str | None = None
    tool_registry: dict[str, AgentTool] | None = None
    agent_manager: AgentManager | None = None
    parent_agent_id: str | None = None
    managed_group_id: str | None = None
    managed_child_index: int | None = None
    plugin_runtime: PluginRuntime | None = None
    last_session: AgentSessionState | None = field(default=None, init=False, repr=False)
    last_run_result: AgentRunResult | None = field(default=None, init=False, repr=False)
    active_session_id: str | None = field(default=None, init=False, repr=False)
    last_session_path: str | None = field(default=None, init=False, repr=False)
    managed_agent_id: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tool_registry is None:
            self.tool_registry = default_tool_registry()
        if self.agent_manager is None:
            self.agent_manager = AgentManager()
        if self.plugin_runtime is None:
            self.plugin_runtime = PluginRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        plugin_tools = self.plugin_runtime.register_tool_aliases(self.tool_registry)
        if plugin_tools:
            self.tool_registry = {**self.tool_registry, **plugin_tools}
        self.client = OpenAICompatClient(self.model_config)
        self.tool_context = build_tool_context(self.runtime_config)

    def set_model(self, model: str) -> None:
        self.model_config = replace(self.model_config, model=model)
        self.client = OpenAICompatClient(self.model_config)

    def clear_runtime_state(self) -> None:
        self.last_session = None
        self.last_run_result = None
        self.active_session_id = None
        self.last_session_path = None

    def build_prompt_context(self, scratchpad_directory: Path | None = None):
        return build_prompt_context(
            self.runtime_config,
            self.model_config,
            scratchpad_directory=scratchpad_directory,
        )

    def build_system_prompt_parts(self, prompt_context=None) -> list[str]:
        if prompt_context is None:
            prompt_context = self.build_prompt_context()
        return build_system_prompt_parts(
            prompt_context=prompt_context,
            runtime_config=self.runtime_config,
            tools=self.tool_registry,
            custom_system_prompt=self.custom_system_prompt,
            append_system_prompt=self.append_system_prompt,
            override_system_prompt=self.override_system_prompt,
        )

    def build_session(
        self,
        user_prompt: str | None = None,
        *,
        scratchpad_directory: Path | None = None,
    ) -> AgentSessionState:
        prompt_context = self.build_prompt_context(scratchpad_directory)
        system_prompt_parts = self.build_system_prompt_parts(prompt_context)
        return AgentSessionState.create(
            system_prompt_parts,
            user_prompt,
            user_context=prompt_context.user_context,
            system_context=prompt_context.system_context,
        )

    def run(self, prompt: str) -> AgentRunResult:
        self.managed_agent_id = None
        session_id = uuid4().hex
        scratchpad_directory = self._ensure_scratchpad_directory(session_id)
        result = self._run_prompt(
            prompt,
            base_session=None,
            session_id=session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=(),
        )
        self._finalize_managed_agent(result)
        return result

    def resume(self, prompt: str, stored_session: StoredAgentSession) -> AgentRunResult:
        self.managed_agent_id = None
        session = AgentSessionState.from_persisted(
            system_prompt_parts=stored_session.system_prompt_parts,
            user_context=stored_session.user_context,
            system_context=stored_session.system_context,
            messages=stored_session.messages,
        )
        self._append_file_history_replay_if_needed(
            session,
            stored_session.file_history,
        )
        self._append_compaction_replay_if_needed(session)
        self.active_session_id = stored_session.session_id
        self.last_session = session
        self.last_session_path = str(
            self.runtime_config.session_directory / f'{stored_session.session_id}.json'
        )
        scratchpad_directory = (
            Path(stored_session.scratchpad_directory)
            if stored_session.scratchpad_directory
            else self._ensure_scratchpad_directory(stored_session.session_id)
        )
        result = self._run_prompt(
            prompt,
            base_session=session,
            session_id=stored_session.session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=stored_session.file_history,
        )
        self._finalize_managed_agent(result)
        return result

    def _run_prompt(
        self,
        prompt: str,
        *,
        base_session: AgentSessionState | None,
        session_id: str,
        scratchpad_directory: Path | None,
        existing_file_history: tuple[dict[str, object], ...],
    ) -> AgentRunResult:
        slash_result = preprocess_slash_command(self, prompt)
        if slash_result.handled and not slash_result.should_query:
            return AgentRunResult(
                final_output=slash_result.output,
                turns=0,
                tool_calls=0,
                transcript=slash_result.transcript,
                session_id=self.active_session_id,
                session_path=self.last_session_path,
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )

        effective_prompt = self._apply_plugin_before_prompt_hooks(slash_result.prompt or prompt)
        self.managed_agent_id = self.agent_manager.start_agent(
            prompt=effective_prompt,
            parent_agent_id=self.parent_agent_id,
            group_id=self.managed_group_id,
            child_index=self.managed_child_index,
            label='root' if base_session is None else 'resume',
        )
        session = (
            base_session
            if base_session is not None
            else self.build_session(
                None,
                scratchpad_directory=scratchpad_directory,
            )
        )
        session.append_user(effective_prompt)
        self.last_session = session
        self.active_session_id = session_id
        tool_specs = [tool.to_openai_tool() for tool in self.tool_registry.values()]
        tool_calls = 0
        last_content = ''
        total_usage = UsageStats()
        total_cost_usd = 0.0
        file_history = list(existing_file_history)
        stream_events: list[dict[str, object]] = []
        assistant_response_segments: list[str] = []
        delegated_tasks = sum(
            1 for entry in file_history if entry.get('action') == 'delegate_agent'
        )

        initial_budget = self._check_budget(
            total_usage,
            total_cost_usd,
            tool_calls=tool_calls,
            delegated_tasks=delegated_tasks,
        )
        if initial_budget.exceeded:
            result = AgentRunResult(
                final_output=initial_budget.reason or 'Stopped before the first model call.',
                turns=0,
                tool_calls=0,
                transcript=session.transcript(),
                session_id=session_id,
                usage=total_usage,
                total_cost_usd=total_cost_usd,
                stop_reason='budget_exceeded',
                file_history=tuple(file_history),
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )
            result = self._persist_session(session, result)
            self.last_run_result = result
            return result

        for turn_index in range(1, self.runtime_config.max_turns + 1):
            self._snip_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._compact_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            try:
                turn, turn_events = self._query_model(session, tool_specs)
            except OpenAICompatError as exc:
                if self._is_prompt_too_long_error(exc) and self._reactive_compact_session(
                    session,
                    stream_events,
                    turn_index=turn_index,
                ):
                    try:
                        turn, turn_events = self._query_model(session, tool_specs)
                    except OpenAICompatError as retry_exc:
                        exc = retry_exc
                    else:
                        stream_events.extend(
                            {
                                'type': 'reactive_compact_retry',
                                'turn_index': turn_index,
                            }
                            for _ in [0]
                        )
                        stream_events.extend(event.to_dict() for event in turn_events)
                        total_usage = total_usage + turn.usage
                        total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                        last_content = turn.content

                        budget_after_model = self._check_budget(
                            total_usage,
                            total_cost_usd,
                            tool_calls=tool_calls,
                            delegated_tasks=delegated_tasks,
                        )
                        if budget_after_model.exceeded:
                            result = AgentRunResult(
                                final_output=(
                                    budget_after_model.reason
                                    or 'Stopped because the runtime budget was exceeded.'
                                ),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason='budget_exceeded',
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result

                        if not turn.tool_calls:
                            assistant_response_segments.append(turn.content)
                            if self._should_continue_response(turn):
                                session.append_user(
                                    self._build_continuation_prompt(),
                                    metadata={
                                        'kind': 'continuation_request',
                                        'continuation_index': len(assistant_response_segments),
                                    },
                                    message_id=f'continuation_{turn_index}',
                                )
                                stream_events.append(
                                    {
                                        'type': 'continuation_request',
                                        'reason': turn.finish_reason,
                                        'continuation_index': len(assistant_response_segments),
                                    }
                                )
                                last_content = ''.join(assistant_response_segments)
                                continue
                            result = AgentRunResult(
                                final_output=''.join(assistant_response_segments),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason=turn.finish_reason,
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result
                        # fall through to the normal tool-call branch below
                # normal error path if not recovered
                result = AgentRunResult(
                    final_output=str(exc),
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='backend_error',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_plugin_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            stream_events.extend(event.to_dict() for event in turn_events)
            total_usage = total_usage + turn.usage
            total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
            last_content = turn.content

            budget_after_model = self._check_budget(
                total_usage,
                total_cost_usd,
                tool_calls=tool_calls,
                delegated_tasks=delegated_tasks,
            )
            if budget_after_model.exceeded:
                result = AgentRunResult(
                    final_output=(
                        budget_after_model.reason
                        or 'Stopped because the runtime budget was exceeded.'
                    ),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='budget_exceeded',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            if not turn.tool_calls:
                assistant_response_segments.append(turn.content)
                if self._should_continue_response(turn):
                    session.append_user(
                        self._build_continuation_prompt(),
                        metadata={
                            'kind': 'continuation_request',
                            'continuation_index': len(assistant_response_segments),
                        },
                        message_id=f'continuation_{turn_index}',
                    )
                    stream_events.append(
                        {
                            'type': 'continuation_request',
                            'reason': turn.finish_reason,
                            'continuation_index': len(assistant_response_segments),
                        }
                    )
                    last_content = ''.join(assistant_response_segments)
                    continue
                result = AgentRunResult(
                    final_output=''.join(assistant_response_segments),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=turn.finish_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_plugin_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            for tool_call in turn.tool_calls:
                assistant_response_segments.clear()
                tool_calls += 1
                if tool_call.name == 'delegate_agent':
                    delegated_tasks += self._delegated_task_units(tool_call.arguments)
                budget_after_tool_request = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                )
                if budget_after_tool_request.exceeded:
                    stream_events.append(
                        {
                            'type': 'task_budget_exceeded',
                            'turn_index': turn_index,
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'reason': budget_after_tool_request.reason,
                        }
                    )
                    result = AgentRunResult(
                        final_output=(
                            budget_after_tool_request.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._persist_session(session, result)
                    self.last_run_result = result
                    return result
                tool_result = None
                tool_message_index = session.start_tool(
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                    message_id=f'tool_{len(session.messages)}',
                    metadata={'phase': 'starting'},
                )
                stream_events.append(
                    {
                        'type': 'tool_start',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                    }
                )
                plugin_block_message = self._plugin_block_message(tool_call.name)
                if plugin_block_message is not None:
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=plugin_block_message,
                        metadata={
                            'action': 'plugin_block',
                            'plugin_blocked': True,
                            'plugin_block_message': plugin_block_message,
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': plugin_block_message,
                        }
                    )
                if tool_call.name == 'delegate_agent':
                    if tool_result is None:
                        tool_result = self._execute_delegate_agent(tool_call.arguments)
                elif tool_result is None:
                    for update in execute_tool_streaming(
                        self.tool_registry,
                        tool_call.name,
                        tool_call.arguments,
                        self.tool_context,
                    ):
                        if update.kind == 'delta':
                            session.append_tool_delta(
                                tool_message_index,
                                update.content,
                                metadata={'last_stream': update.stream or 'tool'},
                            )
                            stream_events.append(
                                {
                                    'type': 'tool_delta',
                                    'tool_name': tool_call.name,
                                    'tool_call_id': tool_call.id,
                                    'message_id': session.messages[tool_message_index].message_id,
                                    'stream': update.stream,
                                    'delta': update.content,
                                }
                            )
                            continue
                        tool_result = update.result
                if tool_result is None:
                    raise RuntimeError(f'Tool executor returned no final result for {tool_call.name}')
                plugin_messages = self._plugin_tool_result_messages(tool_call.name)
                if plugin_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['plugin_messages'] = list(plugin_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in plugin_messages:
                        stream_events.append(
                            {
                                'type': 'plugin_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                plugin_runtime_message = self._build_plugin_tool_runtime_message(
                    tool_name=tool_call.name,
                    block_message=plugin_block_message,
                    plugin_messages=plugin_messages,
                )
                if plugin_runtime_message is not None:
                    session.append_user(
                        plugin_runtime_message,
                        metadata={
                            'kind': 'plugin_tool_runtime',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'plugin_blocked': plugin_block_message is not None,
                            'plugin_message_count': len(plugin_messages),
                        },
                        message_id=f'plugin_tool_runtime_{tool_call.id}',
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_context',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': f'plugin_tool_runtime_{tool_call.id}',
                            'blocked': plugin_block_message is not None,
                            'message_count': len(plugin_messages),
                        }
                    )
                session.finalize_tool(
                    tool_message_index,
                    content=serialize_tool_result(tool_result),
                    metadata={
                        'phase': 'completed',
                        **dict(tool_result.metadata),
                    },
                    stop_reason='tool_completed',
                )
                stream_events.append(
                    {
                        'type': 'tool_result',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                        'ok': tool_result.ok,
                        'metadata': dict(tool_result.metadata),
                    }
                )
                history_entry = self._build_file_history_entry(
                    tool_call=tool_call,
                    tool_result=tool_result,
                    turn_index=turn_index,
                )
                if history_entry is not None:
                    file_history.append(history_entry)

        result = AgentRunResult(
            final_output=(
                last_content
                or 'Stopped: max turns reached before the model produced a final answer.'
            ),
            turns=self.runtime_config.max_turns,
            tool_calls=tool_calls,
            transcript=session.transcript(),
            events=tuple(stream_events),
            usage=total_usage,
            total_cost_usd=total_cost_usd,
            stop_reason='max_turns',
            file_history=tuple(file_history),
            session_id=session_id,
            scratchpad_directory=(
                str(scratchpad_directory) if scratchpad_directory is not None else None
            ),
        )
        result = self._append_plugin_after_turn_events(
            result,
            prompt=effective_prompt,
            turn_index=self.runtime_config.max_turns,
        )
        result = self._persist_session(session, result)
        self.last_run_result = result
        return result

    def _query_model(
        self,
        session: AgentSessionState,
        tool_specs: list[dict[str, object]],
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        if not self.runtime_config.stream_model_responses:
            turn = self.client.complete(
                session.to_openai_messages(),
                tool_specs,
                output_schema=self.runtime_config.output_schema,
            )
            assistant_tool_calls = tuple(
                {
                    'id': tool_call.id,
                    'type': 'function',
                    'function': {
                        'name': tool_call.name,
                        'arguments': json.dumps(
                            tool_call.arguments,
                            ensure_ascii=True,
                        ),
                    },
                }
                for tool_call in turn.tool_calls
            )
            session.append_assistant(
                turn.content,
                assistant_tool_calls,
                message_id=f'assistant_{len(session.messages)}',
                stop_reason=turn.finish_reason,
                usage=turn.usage,
            )
            return turn, ()

        assistant_index = session.start_assistant(
            message_id=f'assistant_{len(session.messages)}'
        )
        usage = UsageStats()
        finish_reason: str | None = None
        events: list[StreamEvent] = []
        for event in self.client.stream(
            session.to_openai_messages(),
            tool_specs,
            output_schema=self.runtime_config.output_schema,
        ):
            events.append(event)
            if event.type == 'content_delta':
                session.append_assistant_delta(assistant_index, event.delta)
            elif event.type == 'tool_call_delta':
                session.merge_assistant_tool_call_delta(
                    assistant_index,
                    tool_call_index=event.tool_call_index or 0,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    arguments_delta=event.arguments_delta,
                )
            elif event.type == 'usage':
                usage = usage + event.usage
            elif event.type == 'message_stop':
                finish_reason = event.finish_reason

        session.finalize_assistant(
            assistant_index,
            finish_reason=finish_reason,
            usage=usage,
        )
        assistant_message = session.messages[assistant_index]
        turn = AssistantTurn(
            content=assistant_message.content,
            tool_calls=self._tool_calls_from_message(assistant_message.tool_calls),
            finish_reason=finish_reason,
            raw_message=assistant_message.to_openai_message(),
            usage=usage,
        )
        return turn, tuple(events)

    def _tool_calls_from_message(
        self,
        tool_calls: tuple[dict[str, object], ...],
    ) -> tuple[ToolCall, ...]:
        parsed: list[ToolCall] = []
        for index, raw_tool_call in enumerate(tool_calls):
            function_block = raw_tool_call.get('function')
            if not isinstance(function_block, dict):
                continue
            name = function_block.get('name')
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function_block.get('arguments', '')
            if isinstance(raw_arguments, str) and raw_arguments.strip():
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise OpenAICompatError(
                        f'Tool arguments must decode to an object, got {type(arguments).__name__}'
                    )
            else:
                arguments = {}
            call_id = raw_tool_call.get('id')
            if not isinstance(call_id, str) or not call_id:
                call_id = f'call_{index}'
            parsed.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(parsed)

    def _should_continue_response(self, turn: AssistantTurn) -> bool:
        return turn.finish_reason in {'length', 'max_tokens'}

    def _build_continuation_prompt(self) -> str:
        return (
            '<system-reminder>\n'
            'Your previous answer was truncated because the model stopped early. '
            'Continue exactly where you left off. Do not repeat completed text.\n'
            '</system-reminder>'
        )

    def _check_budget(
        self,
        usage: UsageStats,
        total_cost_usd: float,
        *,
        tool_calls: int,
        delegated_tasks: int,
    ) -> BudgetDecision:
        budget = self.runtime_config.budget_config
        token_reason = self._check_token_budget(usage, budget)
        if token_reason is not None:
            return BudgetDecision(exceeded=True, reason=token_reason)
        if (
            budget.max_total_cost_usd is not None
            and total_cost_usd > budget.max_total_cost_usd
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the total estimated cost '
                    f'(${total_cost_usd:.6f}) exceeded the configured budget '
                    f'(${budget.max_total_cost_usd:.6f}).'
                ),
            )
        if (
            budget.max_tool_calls is not None
            and tool_calls > budget.max_tool_calls
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the tool-call budget was exceeded '
                    f'({tool_calls} > {budget.max_tool_calls}).'
                ),
            )
        if (
            budget.max_delegated_tasks is not None
            and delegated_tasks > budget.max_delegated_tasks
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the delegated-task budget was exceeded '
                    f'({delegated_tasks} > {budget.max_delegated_tasks}).'
                ),
            )
        return BudgetDecision(exceeded=False)

    def _snip_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        threshold = self.runtime_config.auto_snip_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=False,
        )

    def _compact_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        threshold = self.runtime_config.auto_compact_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=True,
        )

    def _reactive_compact_session(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> bool:
        return self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=0,
            allow_compaction=True,
            reactive=True,
        )

    def _reduce_context_pressure(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        allow_compaction: bool,
        reactive: bool = False,
    ) -> bool:
        changed = False
        for _ in range(6):
            usage_report = collect_context_usage(
                session=session,
                model=self.model_config.model,
                strategy='reactive_compact' if reactive else 'context_pressure',
            )
            if usage_report.total_tokens <= target_tokens:
                break
            if self._snip_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                target_tokens=target_tokens,
                current_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                continue
            if allow_compaction and self._compact_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                usage_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                if reactive:
                    continue
                break
            break
        return changed

    def _snip_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        current_total: int,
        reactive: bool,
    ) -> bool:
        prefix_count = self._compact_prefix_count(session)
        tail_count = min(
            max(self.runtime_config.compact_preserve_messages, 0),
            max(len(session.messages) - prefix_count, 0),
        )
        candidate_indexes = [
            index
            for index in range(prefix_count, max(len(session.messages) - tail_count, prefix_count))
            if self._message_can_be_snipped(session.messages[index])
        ]
        if not candidate_indexes:
            return False
        snipped_count = 0
        tokens_removed = 0
        snipped_message_ids: list[str] = []
        for index in candidate_indexes:
            if current_total <= target_tokens and not reactive:
                break
            message = session.messages[index]
            original_tokens = estimate_tokens(message.content)
            replacement = self._build_snipped_message_content(message)
            replacement_tokens = estimate_tokens(replacement)
            if replacement_tokens >= original_tokens:
                continue
            session.tombstone_message(
                index,
                summary=replacement,
                stop_reason='snipped_for_context',
                mutation_kind='snip_tombstone',
                metadata={
                    'kind': 'snipped_message',
                    'original_token_estimate': original_tokens,
                    'replacement_token_estimate': replacement_tokens,
                    'snipped_turn_index': turn_index,
                },
            )
            delta = original_tokens - replacement_tokens
            current_total -= delta
            tokens_removed += delta
            snipped_count += 1
            if session.messages[index].message_id:
                snipped_message_ids.append(session.messages[index].message_id)
            if reactive and snipped_count >= 3:
                break
        if not snipped_count:
            return False
        stream_events.append(
            {
                'type': 'reactive_snip_boundary' if reactive else 'snip_boundary',
                'turn_index': turn_index,
                'snipped_message_count': snipped_count,
                'estimated_tokens_removed': tokens_removed,
                'snipped_message_ids': snipped_message_ids,
            }
        )
        return True

    def _compact_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        usage_total: int,
        reactive: bool,
    ) -> bool:
        prefix_count = self._compact_prefix_count(session)
        preserve_messages = max(self.runtime_config.compact_preserve_messages, 0)
        if reactive:
            preserve_messages = max(preserve_messages // 2, 1)
        tail_count = min(
            preserve_messages,
            max(len(session.messages) - prefix_count, 0),
        )
        compact_end = len(session.messages) - tail_count
        if compact_end <= prefix_count:
            return False
        candidates = session.messages[prefix_count:compact_end]
        if not candidates:
            return False
        compacted_tokens = sum(
            usage.tokens
            for usage in (
                collect_context_usage(
                    session=AgentSessionState(
                        system_prompt_parts=session.system_prompt_parts,
                        user_context=session.user_context,
                        system_context=session.system_context,
                        messages=list(candidates),
                    ),
                    model=self.model_config.model,
                    strategy='compacted_segment',
                ).categories
            )
            if usage.name != 'Free space'
        )
        compact_message = self._build_compact_boundary_message(
            candidates,
            turn_index=turn_index,
            estimated_tokens_before=usage_total,
            estimated_tokens_removed=compacted_tokens,
            preserved_tail_count=tail_count,
        )
        session.messages = (
            session.messages[:prefix_count]
            + [compact_message]
            + session.messages[compact_end:]
        )
        stream_events.append(
            {
                'type': 'reactive_compact_boundary' if reactive else 'compact_boundary',
                'turn_index': turn_index,
                'compacted_message_count': len(candidates),
                'estimated_tokens_before': usage_total,
                'estimated_tokens_removed': compacted_tokens,
                'preserved_tail_count': tail_count,
                'compacted_message_ids': [
                    message.message_id for message in candidates if message.message_id
                ],
            }
        )
        return True

    def _check_token_budget(
        self,
        usage: UsageStats,
        budget: BudgetConfig,
    ) -> str | None:
        if budget.max_total_tokens is not None and usage.total_tokens > budget.max_total_tokens:
            return (
                'Stopped because the total token budget was exceeded '
                f'({usage.total_tokens} > {budget.max_total_tokens}).'
            )
        if budget.max_input_tokens is not None and usage.input_tokens > budget.max_input_tokens:
            return (
                'Stopped because the input token budget was exceeded '
                f'({usage.input_tokens} > {budget.max_input_tokens}).'
            )
        if budget.max_output_tokens is not None and usage.output_tokens > budget.max_output_tokens:
            return (
                'Stopped because the output token budget was exceeded '
                f'({usage.output_tokens} > {budget.max_output_tokens}).'
            )
        if (
            budget.max_reasoning_tokens is not None
            and usage.reasoning_tokens > budget.max_reasoning_tokens
        ):
            return (
                'Stopped because the reasoning token budget was exceeded '
                f'({usage.reasoning_tokens} > {budget.max_reasoning_tokens}).'
            )
        return None

    def _build_file_history_entry(
        self,
        *,
        tool_call: ToolCall,
        tool_result,
        turn_index: int,
    ) -> dict[str, object] | None:
        if not tool_result.metadata:
            return None
        if (
            'path' not in tool_result.metadata
            and 'command' not in tool_result.metadata
            and tool_result.metadata.get('action') != 'delegate_agent'
        ):
            return None
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'turn_index': turn_index,
            'tool_call_id': tool_call.id,
            'tool_name': tool_call.name,
            'ok': tool_result.ok,
            **dict(tool_result.metadata),
        }

    def _compact_prefix_count(self, session: AgentSessionState) -> int:
        prefix_count = 0
        for message in session.messages:
            if prefix_count == 0 and message.role == 'system':
                prefix_count += 1
                continue
            if (
                prefix_count == 1
                and message.role == 'user'
                and message.content.startswith('<system-reminder>')
            ):
                prefix_count += 1
                continue
            break
        return prefix_count

    def _message_can_be_snipped(self, message) -> bool:
        if message.metadata.get('kind') in {
            'compact_boundary',
            'snipped_message',
            'file_history_replay',
        }:
            return False
        if message.role == 'tool':
            return True
        if message.role == 'assistant' and (message.tool_calls or len(message.content) > 600):
            return True
        if (
            message.role == 'user'
            and message.metadata.get('kind') in {'continuation_request', 'file_history_replay'}
        ):
            return True
        return False

    def _build_snipped_message_content(self, message) -> str:
        preview = ' '.join(message.content.split())
        if len(preview) > 120:
            preview = preview[:117] + '...'
        if message.role == 'tool':
            label = f'tool result ({message.name or "tool"})'
        elif message.role == 'assistant':
            label = 'assistant message with tool calls'
        else:
            label = message.role
        return (
            '<system-reminder>\n'
            f'Older {label} was snipped to save context.\n'
            f'Message id: {message.message_id or "(none)"}\n'
            f'Preview: {preview or "(empty)"}\n'
            '</system-reminder>'
        )

    def _build_compact_boundary_message(
        self,
        messages,
        *,
        turn_index: int,
        estimated_tokens_before: int,
        estimated_tokens_removed: int,
        preserved_tail_count: int,
    ):
        summary_lines = [
            '<system-reminder>',
            'Earlier conversation history was compacted to keep the session within the context budget.',
            '',
            'Compacted summary:',
        ]
        remaining = 24
        for message in messages:
            if remaining <= 0:
                break
            label = message.role
            if message.role == 'tool' and message.name:
                label = f'tool:{message.name}'
            snippet = ' '.join(message.content.split())
            if len(snippet) > 160:
                snippet = snippet[:157] + '...'
            if not snippet:
                snippet = '(empty)'
            summary_lines.append(f'- {label}: {snippet}')
            remaining -= 1
        if len(messages) > 24:
            summary_lines.append(f'- ... plus {len(messages) - 24} older messages')
        summary_lines.extend(
            [
                '',
                'Keep using the preserved recent tail as the active working set.',
                '</system-reminder>',
            ]
        )
        from .agent_session import AgentMessage

        return AgentMessage(
            role='system',
            content='\n'.join(summary_lines),
            message_id=f'compact_boundary_{turn_index}_{len(messages)}',
            metadata={
                'kind': 'compact_boundary',
                'turn_index': turn_index,
                'compacted_message_count': len(messages),
                'estimated_tokens_before': estimated_tokens_before,
                'estimated_tokens_removed': estimated_tokens_removed,
                'preserved_tail_count': preserved_tail_count,
                'compacted_message_ids': [
                    message.message_id for message in messages if message.message_id
                ],
            },
        )

    def _is_prompt_too_long_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        patterns = (
            'prompt is too long',
            'maximum context length',
            'context length exceeded',
            'too many tokens',
            'input too long',
            'context window',
        )
        return any(pattern in text for pattern in patterns)

    def _execute_delegate_agent(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        max_turns = arguments.get('max_turns')
        if max_turns is not None and (isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1):
            return ToolExecutionResult(
                name='delegate_agent',
                ok=False,
                content='max_turns must be an integer >= 1',
            )
        subtasks = self._normalize_delegate_subtasks(arguments)
        if not subtasks:
            return ToolExecutionResult(
                name='delegate_agent',
                ok=False,
                content='prompt must be a non-empty string or subtasks must contain at least one prompt',
            )
        child_permissions = AgentPermissions(
            allow_file_write=(
                self.runtime_config.permissions.allow_file_write
                and bool(arguments.get('allow_write', False))
            ),
            allow_shell_commands=(
                self.runtime_config.permissions.allow_shell_commands
                and bool(arguments.get('allow_shell', False))
            ),
            allow_destructive_shell_commands=False,
        )
        child_runtime_config = replace(
            self.runtime_config,
            max_turns=max_turns or min(self.runtime_config.max_turns, 6),
            permissions=child_permissions,
            auto_compact_threshold_tokens=self.runtime_config.auto_compact_threshold_tokens,
        )
        child_tools = {
            name: tool
            for name, tool in self.tool_registry.items()
            if name != 'delegate_agent'
        }
        include_parent_context = bool(arguments.get('include_parent_context', True))
        continue_on_error = bool(arguments.get('continue_on_error', True))
        child_summaries: list[dict[str, object]] = []
        child_session_ids: list[str] = []
        prior_results: list[dict[str, str]] = []
        group_id: str | None = None
        if self.agent_manager is not None and len(subtasks) > 1:
            group_id = self.agent_manager.start_group(
                label=str(arguments.get('label') or 'delegated_group'),
                parent_agent_id=self.managed_agent_id,
            )
        failed_children = 0
        child_result = None
        for index, subtask in enumerate(subtasks, start=1):
            child_agent = LocalCodingAgent(
                model_config=self.model_config,
                runtime_config=replace(
                    child_runtime_config,
                    max_turns=subtask.get('max_turns', child_runtime_config.max_turns),
                ),
                custom_system_prompt=self.custom_system_prompt,
                append_system_prompt=self.append_system_prompt,
                override_system_prompt=self.override_system_prompt,
                tool_registry=child_tools,
                agent_manager=self.agent_manager,
                parent_agent_id=self.managed_agent_id,
                managed_group_id=group_id,
                managed_child_index=index,
            )
            if group_id is not None and child_agent.managed_agent_id is not None:
                self.agent_manager.register_group_child(
                    group_id,
                    child_agent.managed_agent_id,
                    child_index=index,
                )
            child_prompt = str(subtask['prompt'])
            if include_parent_context and prior_results:
                child_prompt = self._prepend_delegate_context(child_prompt, prior_results)
            child_result = child_agent.run(child_prompt)
            if group_id is not None and child_agent.managed_agent_id is not None:
                self.agent_manager.register_group_child(
                    group_id,
                    child_agent.managed_agent_id,
                    child_index=index,
                )
            summary = {
                'index': index,
                'label': str(subtask.get('label') or f'subtask_{index}'),
                'session_id': child_result.session_id or '',
                'turns': child_result.turns,
                'tool_calls': child_result.tool_calls,
                'stop_reason': child_result.stop_reason or 'stop',
                'output_preview': self._preview_text(child_result.final_output, 220),
            }
            child_summaries.append(summary)
            if child_result.session_id:
                child_session_ids.append(child_result.session_id)
            prior_results.append(
                {
                    'label': summary['label'],
                    'output_preview': str(summary['output_preview']),
                }
            )
            if child_result.stop_reason in {'backend_error', 'budget_exceeded'}:
                failed_children += 1
                if not continue_on_error:
                    break
        assert child_result is not None
        completed_children = len(child_summaries) - failed_children
        group_status = 'completed'
        if failed_children and completed_children:
            group_status = 'partial'
        elif failed_children:
            group_status = 'failed'
        if group_id is not None and self.agent_manager is not None:
            self.agent_manager.finish_group(
                group_id,
                status=group_status,
                completed_children=completed_children,
                failed_children=failed_children,
            )
        summary_lines = [
            (
                'Delegated agent completed the subtask.'
                if len(child_summaries) == 1
                else f'Delegated agent completed {len(child_summaries)} sequential subtasks.'
            ),
        ]
        if group_id is not None:
            summary_lines.append(f'group_id={group_id}')
            summary_lines.append(f'group_status={group_status}')
            summary_lines.append('')
        for summary in child_summaries:
            summary_lines.extend(
                [
                    f"[{summary['label']}]",
                    f"session_id={summary['session_id']}",
                    f"turns={summary['turns']}",
                    f"tool_calls={summary['tool_calls']}",
                    f"stop_reason={summary['stop_reason']}",
                    f"output_preview={summary['output_preview']}",
                    '',
                ]
            )
        summary_lines.append('Final delegated output:')
        summary_lines.append(child_result.final_output)
        return ToolExecutionResult(
            name='delegate_agent',
            ok=True,
            content='\n'.join(summary_lines).strip(),
            metadata={
                'action': 'delegate_agent',
                'child_session_id': child_result.session_id,
                'child_session_ids': child_session_ids,
                'child_turns': child_result.turns,
                'child_tool_calls': child_result.tool_calls,
                'child_stop_reason': child_result.stop_reason,
                'child_results': child_summaries,
                'subtask_count': len(child_summaries),
                'group_id': group_id,
                'group_status': group_status,
                'failed_children': failed_children,
                'completed_children': completed_children,
            },
        )

    def _normalize_delegate_subtasks(
        self,
        arguments: dict[str, object],
    ) -> list[dict[str, object]]:
        subtasks: list[dict[str, object]] = []
        raw_subtasks = arguments.get('subtasks')
        if isinstance(raw_subtasks, list):
            for index, item in enumerate(raw_subtasks, start=1):
                if isinstance(item, str) and item.strip():
                    subtasks.append({'prompt': item.strip(), 'label': f'subtask_{index}'})
                    continue
                if isinstance(item, dict):
                    prompt = item.get('prompt')
                    if not isinstance(prompt, str) or not prompt.strip():
                        continue
                    label = item.get('label')
                    max_turns = item.get('max_turns')
                    task: dict[str, object] = {
                        'prompt': prompt.strip(),
                        'label': label if isinstance(label, str) and label.strip() else f'subtask_{index}',
                    }
                    if isinstance(max_turns, int) and not isinstance(max_turns, bool) and max_turns > 0:
                        task['max_turns'] = max_turns
                    subtasks.append(task)
        prompt = arguments.get('prompt')
        if isinstance(prompt, str) and prompt.strip():
            if not subtasks:
                subtasks.append({'prompt': prompt.strip(), 'label': 'subtask_1'})
        return subtasks[:8]

    def _delegated_task_units(
        self,
        arguments: dict[str, object],
    ) -> int:
        subtasks = arguments.get('subtasks')
        if isinstance(subtasks, list):
            count = sum(
                1
                for item in subtasks
                if (
                    isinstance(item, str)
                    and item.strip()
                ) or (
                    isinstance(item, dict)
                    and isinstance(item.get('prompt'), str)
                    and item.get('prompt', '').strip()
                )
            )
            if count:
                return count
        return 1

    def _prepend_delegate_context(
        self,
        prompt: str,
        prior_results: list[dict[str, str]],
    ) -> str:
        lines = [
            '<system-reminder>',
            'Prior delegated subtask summaries:',
        ]
        for result in prior_results[-4:]:
            lines.append(f"- {result['label']}: {result['output_preview']}")
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _preview_text(self, text: str, limit: int) -> str:
        normalized = ' '.join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + '...'

    def _ensure_scratchpad_directory(self, session_id: str) -> Path:
        scratchpad_directory = (self.runtime_config.scratchpad_root / session_id).resolve()
        scratchpad_directory.mkdir(parents=True, exist_ok=True)
        return scratchpad_directory

    def _append_file_history_replay_if_needed(
        self,
        session: AgentSessionState,
        file_history: tuple[dict[str, object], ...],
    ) -> None:
        if not file_history:
            return
        replay_count = len(file_history)
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'file_history_replay':
                continue
            if message.metadata.get('file_history_count') == replay_count:
                return
            break
        session.append_user(
            self._render_file_history_replay(file_history),
            metadata={
                'kind': 'file_history_replay',
                'file_history_count': replay_count,
            },
            message_id=f'file_history_replay_{replay_count}',
        )

    def _render_file_history_replay(
        self,
        file_history: tuple[dict[str, object], ...],
    ) -> str:
        lines = [
            '<system-reminder>',
            'Recent file history from this saved session:',
        ]
        for entry in file_history[-10:]:
            action = str(entry.get('action', entry.get('tool_name', 'tool')))
            turn = entry.get('turn_index')
            path = entry.get('path')
            command = entry.get('command')
            details = [f'action={action}']
            if turn is not None:
                details.append(f'turn={turn}')
            if path:
                details.append(f'path={path}')
            if command:
                details.append(f'command={command}')
            child_session_ids = entry.get('child_session_ids')
            if isinstance(child_session_ids, list) and child_session_ids:
                details.append(f'child_sessions={len(child_session_ids)}')
            lines.append(f"- {'; '.join(details)}")
            before_preview = entry.get('before_preview')
            if isinstance(before_preview, str) and before_preview:
                lines.append(f'  before: {before_preview}')
            after_preview = entry.get('after_preview')
            if isinstance(after_preview, str) and after_preview:
                lines.append(f'  after: {after_preview}')
        if len(file_history) > 10:
            lines.append(f'- ... plus {len(file_history) - 10} older file-history entries')
        lines.extend(
            [
                '',
                'Use this replayed history when continuing the task so you avoid repeating prior edits or commands.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _append_compaction_replay_if_needed(
        self,
        session: AgentSessionState,
    ) -> None:
        compact_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'compact_boundary'
        ]
        snipped_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'snipped_message'
        ]
        if not compact_messages and not snipped_messages:
            return
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'compaction_replay':
                continue
            return
        session.append_user(
            self._render_compaction_replay(compact_messages, snipped_messages),
            metadata={
                'kind': 'compaction_replay',
                'compact_boundary_count': len(compact_messages),
                'snipped_message_count': len(snipped_messages),
            },
            message_id=(
                f'compaction_replay_{len(compact_messages)}_{len(snipped_messages)}'
            ),
        )

    def _render_compaction_replay(
        self,
        compact_messages,
        snipped_messages,
    ) -> str:
        lines = [
            '<system-reminder>',
            'This resumed session already contains compacted or snipped history.',
            f'- Compact boundaries: {len(compact_messages)}',
            f'- Snipped/tombstoned messages: {len(snipped_messages)}',
        ]
        latest_boundary = compact_messages[-1] if compact_messages else None
        if latest_boundary is not None:
            lines.append(
                f"- Latest compact boundary id: {latest_boundary.message_id or '(none)'}"
            )
        if snipped_messages:
            last_ids = [
                message.message_id or '(none)'
                for message in snipped_messages[-3:]
            ]
            lines.append(f"- Recent snipped ids: {', '.join(last_ids)}")
        lines.extend(
            [
                '',
                'Use the surviving transcript plus the compacted summaries as the authoritative context when continuing.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _build_plugin_tool_runtime_message(
        self,
        *,
        tool_name: str,
        block_message: str | None,
        plugin_messages: tuple[str, ...],
    ) -> str | None:
        if block_message is None and not plugin_messages:
            return None
        lines = [
            '<system-reminder>',
            f'Plugin tool runtime guidance for `{tool_name}`:',
        ]
        if block_message is not None:
            lines.append(f'- Blocked: {block_message}')
        for message in plugin_messages:
            lines.append(f'- {message}')
        lines.extend(
            [
                '',
                'Use this plugin guidance when deciding the next tool call or assistant response.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _plugin_block_message(self, tool_name: str) -> str | None:
        if self.plugin_runtime is None:
            return None
        return self.plugin_runtime.blocked_tool_message(tool_name)

    def _plugin_tool_result_messages(self, tool_name: str) -> tuple[str, ...]:
        if self.plugin_runtime is None:
            return ()
        return self.plugin_runtime.tool_result_injections(tool_name)

    def _persist_session(
        self,
        session: AgentSessionState,
        result: AgentRunResult,
    ) -> AgentRunResult:
        if result.session_id is None:
            return result
        stored = StoredAgentSession(
            session_id=result.session_id,
            model_config=serialize_model_config(self.model_config),
            runtime_config=serialize_runtime_config(self.runtime_config),
            system_prompt_parts=session.system_prompt_parts,
            user_context=dict(session.user_context),
            system_context=dict(session.system_context),
            messages=session.transcript(),
            turns=result.turns,
            tool_calls=result.tool_calls,
            usage=result.usage.to_dict(),
            total_cost_usd=result.total_cost_usd,
            file_history=result.file_history,
            scratchpad_directory=result.scratchpad_directory,
        )
        path = save_agent_session(
            stored,
            directory=self.runtime_config.session_directory,
        )
        self.last_session_path = str(path)
        return replace(result, session_path=self.last_session_path)

    def render_system_prompt(self) -> str:
        prompt_context = self.build_prompt_context()
        parts = self.build_system_prompt_parts(prompt_context)
        return render_system_prompt(parts)

    def render_context_report(self, prompt: str | None = None) -> str:
        session = self.last_session if prompt is None else None
        strategy = 'current Python session'
        if session is None:
            session = self.build_session(prompt)
            strategy = 'one-shot Python session preview'
        report = collect_context_usage(
            session=session,
            model=self.model_config.model,
            strategy=strategy,
        )
        return format_context_usage(report)

    def render_context_snapshot_report(self) -> str:
        prompt_context = self.build_prompt_context()
        return render_agent_context_report(prompt_context, self.model_config.model)

    def render_permissions_report(self) -> str:
        permissions = self.runtime_config.permissions
        return '\n'.join(
            [
                '# Permissions',
                '',
                f'- File write tools: {"enabled" if permissions.allow_file_write else "disabled"}',
                f'- Shell commands: {"enabled" if permissions.allow_shell_commands else "disabled"}',
                f'- Destructive shell commands: {"enabled" if permissions.allow_destructive_shell_commands else "disabled"}',
            ]
        )

    def render_tools_report(self) -> str:
        permissions = self.runtime_config.permissions
        lines = ['# Tools', '']
        for tool in self.tool_registry.values():
            state = 'enabled'
            if tool.name == 'bash' and not permissions.allow_shell_commands:
                state = 'blocked by permissions'
            if tool.name in {'write_file', 'edit_file'} and not permissions.allow_file_write:
                state = 'blocked by permissions'
            lines.append(f'- `{tool.name}`: {tool.description} [{state}]')
        return '\n'.join(lines)

    def render_memory_report(self) -> str:
        prompt_context = self.build_prompt_context()
        claude_md = prompt_context.user_context.get('claudeMd')
        if not claude_md:
            return '# Memory\n\nNo CLAUDE.md memory files are currently loaded.'
        return '\n'.join(['# Memory', '', claude_md])

    def render_status_report(self) -> str:
        lines = [
            '# Status',
            '',
            f'- Model: {self.model_config.model}',
            f'- Registered tools: {len(self.tool_registry)}',
            f'- Streaming model responses: {self.runtime_config.stream_model_responses}',
            f'- Session ID: {self.active_session_id or "none"}',
            f'- Last session loaded: {"yes" if self.last_session is not None else "no"}',
        ]
        if self.last_session_path is not None:
            lines.append(f'- Session path: {self.last_session_path}')
        if self.last_run_result is not None:
            lines.extend(
                [
                    f'- Last run turns: {self.last_run_result.turns}',
                    f'- Last run tool calls: {self.last_run_result.tool_calls}',
                    f'- Last run total tokens: {self.last_run_result.usage.total_tokens}',
                    f'- Last run total cost: ${self.last_run_result.total_cost_usd:.6f}',
                ]
            )
            if self.last_run_result.scratchpad_directory is not None:
                lines.append(
                    f'- Scratchpad directory: {self.last_run_result.scratchpad_directory}'
                )
        else:
            lines.append('- Last run: none')
        if self.agent_manager is not None:
            lines.extend(self.agent_manager.summary_lines())
        return '\n'.join(lines)

    def _finalize_managed_agent(self, result: AgentRunResult) -> None:
        if self.managed_agent_id is None or self.agent_manager is None:
            return
        self.agent_manager.finish_agent(
            self.managed_agent_id,
            session_id=result.session_id,
            session_path=result.session_path,
            turns=result.turns,
            tool_calls=result.tool_calls,
            stop_reason=result.stop_reason,
        )

    def _apply_plugin_before_prompt_hooks(self, prompt: str) -> str:
        if self.plugin_runtime is None:
            return prompt
        injections = self.plugin_runtime.before_prompt_injections()
        if not injections:
            return prompt
        lines = ['<system-reminder>', 'Plugin before-prompt hooks:']
        lines.extend(f'- {entry}' for entry in injections)
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _append_plugin_after_turn_events(
        self,
        result: AgentRunResult,
        *,
        prompt: str,
        turn_index: int,
    ) -> AgentRunResult:
        if self.plugin_runtime is None:
            return result
        injections = self.plugin_runtime.after_turn_injections()
        if not injections:
            return result
        appended = list(result.events)
        for entry in injections:
            appended.append(
                {
                    'type': 'plugin_after_turn',
                    'turn_index': turn_index,
                    'message': entry,
                    'prompt_preview': self._preview_text(prompt, 120),
                    'stop_reason': result.stop_reason,
                }
            )
        return replace(result, events=tuple(appended))
