from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .agent_types import UsageStats

JSONDict = dict[str, Any]
MAX_MUTATION_HISTORY = 8


@dataclass(frozen=True)
class AgentMessage:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[JSONDict, ...] = ()
    blocks: tuple[JSONDict, ...] = ()
    message_id: str | None = None
    state: str = 'final'
    stop_reason: str | None = None
    usage: UsageStats = field(default_factory=UsageStats)
    metadata: JSONDict = field(default_factory=dict)

    def to_openai_message(self) -> JSONDict:
        payload: JSONDict = {
            'role': self.role,
            'content': self.content,
        }
        if self.name is not None:
            payload['name'] = self.name
        if self.tool_call_id is not None:
            payload['tool_call_id'] = self.tool_call_id
        if self.tool_calls:
            payload['tool_calls'] = list(self.tool_calls)
        return payload

    def to_transcript_entry(self) -> JSONDict:
        payload = self.to_openai_message()
        blocks = self.blocks or _derive_blocks(self)
        if blocks:
            payload['blocks'] = [dict(block) for block in blocks]
        if self.message_id is not None:
            payload['message_id'] = self.message_id
        if self.state != 'final':
            payload['state'] = self.state
        if self.stop_reason is not None:
            payload['stop_reason'] = self.stop_reason
        if self.usage.total_tokens:
            payload['usage'] = self.usage.to_dict()
        if self.metadata:
            payload['metadata'] = dict(self.metadata)
        return payload

    @classmethod
    def from_openai_message(cls, payload: JSONDict) -> 'AgentMessage':
        tool_calls = payload.get('tool_calls')
        normalized_tool_calls: tuple[JSONDict, ...] = ()
        if isinstance(tool_calls, list):
            normalized_tool_calls = tuple(
                item for item in tool_calls if isinstance(item, dict)
            )
        blocks = payload.get('blocks')
        normalized_blocks: tuple[JSONDict, ...] = ()
        if isinstance(blocks, list):
            normalized_blocks = tuple(
                item for item in blocks if isinstance(item, dict)
            )
        return cls(
            role=str(payload.get('role', 'user')),
            content='' if payload.get('content') is None else str(payload.get('content', '')),
            name=str(payload['name']) if isinstance(payload.get('name'), str) else None,
            tool_call_id=str(payload['tool_call_id']) if isinstance(payload.get('tool_call_id'), str) else None,
            tool_calls=normalized_tool_calls,
            blocks=normalized_blocks,
            message_id=str(payload['message_id']) if isinstance(payload.get('message_id'), str) else None,
            state=str(payload.get('state', 'final')),
            stop_reason=str(payload['stop_reason']) if isinstance(payload.get('stop_reason'), str) else None,
            usage=_usage_from_payload(payload.get('usage')),
            metadata=(
                dict(payload['metadata'])
                if isinstance(payload.get('metadata'), dict)
                else {}
            ),
        )


@dataclass
class AgentSessionState:
    system_prompt_parts: tuple[str, ...]
    user_context: dict[str, str] = field(default_factory=dict)
    system_context: dict[str, str] = field(default_factory=dict)
    messages: list[AgentMessage] = field(default_factory=list)
    mutation_serial: int = 0

    @classmethod
    def create(
        cls,
        system_prompt_parts: list[str],
        user_prompt: str | None,
        *,
        user_context: dict[str, str] | None = None,
        system_context: dict[str, str] | None = None,
    ) -> 'AgentSessionState':
        state = cls(
            system_prompt_parts=tuple(system_prompt_parts),
            user_context=dict(user_context or {}),
            system_context=dict(system_context or {}),
        )
        state.messages.append(
            AgentMessage(
                role='system',
                content='\n\n'.join(
                    _append_system_context(system_prompt_parts, state.system_context)
                ),
                blocks=_text_blocks('\n\n'.join(_append_system_context(system_prompt_parts, state.system_context))),
                metadata=_initialize_message_metadata(
                    role='system',
                    message_id='system_0',
                ),
            )
        )
        if state.user_context:
            state.messages.append(
                AgentMessage(
                    role='user',
                    content=_render_user_context_reminder(state.user_context),
                    blocks=_text_blocks(_render_user_context_reminder(state.user_context)),
                    metadata=_initialize_message_metadata(
                        role='user',
                        message_id='user_context_0',
                    ),
                )
            )
        if user_prompt is not None:
            state.messages.append(
                AgentMessage(
                    role='user',
                    content=user_prompt,
                    blocks=_text_blocks(user_prompt),
                    metadata=_initialize_message_metadata(
                        role='user',
                        message_id='user_0',
                    ),
                )
            )
        return state

    def append_assistant(
        self,
        content: str,
        tool_calls: tuple[JSONDict, ...] = (),
        *,
        message_id: str | None = None,
        stop_reason: str | None = None,
        usage: UsageStats | None = None,
    ) -> None:
        self.messages.append(
            AgentMessage(
                role='assistant',
                content=content,
                tool_calls=tool_calls,
                blocks=_assistant_blocks(content, tool_calls),
                message_id=message_id,
                stop_reason=stop_reason,
                usage=usage or UsageStats(),
                metadata=_initialize_message_metadata(
                    role='assistant',
                    message_id=message_id or f'assistant_{len(self.messages)}',
                ),
            )
        )

    def start_assistant(
        self,
        *,
        message_id: str | None = None,
    ) -> int:
        self.messages.append(
            AgentMessage(
                role='assistant',
                content='',
                tool_calls=(),
                blocks=(),
                message_id=message_id,
                state='streaming',
                metadata=_initialize_message_metadata(
                    role='assistant',
                    message_id=message_id or f'assistant_{len(self.messages)}',
                ),
            )
        )
        return len(self.messages) - 1

    def append_assistant_delta(self, index: int, delta: str) -> None:
        message = self.messages[index]
        merged_metadata = _record_mutation(
            dict(message.metadata),
            mutation_kind='assistant_delta_append',
            previous_content=message.content,
            previous_state=message.state,
            previous_stop_reason=message.stop_reason,
            mutation_serial=self._next_mutation_serial(),
        )
        merged_metadata = _advance_lineage_revision(merged_metadata)
        self.messages[index] = replace(
            message,
            content=message.content + delta,
            blocks=_assistant_blocks(message.content + delta, message.tool_calls),
            metadata=merged_metadata,
        )

    def merge_assistant_tool_call_delta(
        self,
        index: int,
        *,
        tool_call_index: int,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments_delta: str = '',
    ) -> None:
        message = self.messages[index]
        tool_calls = [dict(item) for item in message.tool_calls]
        while len(tool_calls) <= tool_call_index:
            tool_calls.append(
                {
                    'id': None,
                    'type': 'function',
                    'function': {
                        'name': '',
                        'arguments': '',
                    },
                }
            )
        tool_call = tool_calls[tool_call_index]
        function_block = tool_call.setdefault('function', {})
        if tool_call_id:
            tool_call['id'] = tool_call_id
        if tool_name:
            function_block['name'] = tool_name
        if arguments_delta:
            current_arguments = function_block.get('arguments', '')
            function_block['arguments'] = f'{current_arguments}{arguments_delta}'
        merged_metadata = _record_mutation(
            dict(message.metadata),
            mutation_kind='assistant_tool_call_delta',
            previous_content=message.content,
            previous_state=message.state,
            previous_stop_reason=message.stop_reason,
            mutation_serial=self._next_mutation_serial(),
        )
        merged_metadata = _advance_lineage_revision(merged_metadata)
        self.messages[index] = replace(
            message,
            tool_calls=tuple(tool_calls),
            blocks=_assistant_blocks(message.content, tuple(tool_calls)),
            metadata=merged_metadata,
        )

    def finalize_assistant(
        self,
        index: int,
        *,
        finish_reason: str | None,
        usage: UsageStats | None = None,
    ) -> None:
        message = self.messages[index]
        merged_metadata = _record_mutation(
            dict(message.metadata),
            mutation_kind='assistant_finalize',
            previous_content=message.content,
            previous_state=message.state,
            previous_stop_reason=message.stop_reason,
            mutation_serial=self._next_mutation_serial(),
        )
        merged_metadata = _advance_lineage_revision(merged_metadata)
        self.messages[index] = replace(
            message,
            state='final',
            stop_reason=finish_reason,
            usage=usage or message.usage,
            blocks=_assistant_blocks(message.content, message.tool_calls),
            metadata=merged_metadata,
        )

    def append_user(
        self,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> None:
        self.messages.append(
            AgentMessage(
                role='user',
                content=content,
                blocks=_text_blocks(content),
                metadata=_initialize_message_metadata(
                    role='user',
                    message_id=message_id or f'user_{len(self.messages)}',
                    metadata=dict(metadata or {}),
                ),
                message_id=message_id,
            )
        )

    def append_tool(self, name: str, tool_call_id: str, content: str) -> None:
        self.messages.append(
            AgentMessage(
                role='tool',
                content=content,
                name=name,
                tool_call_id=tool_call_id,
                blocks=_tool_blocks(name, tool_call_id, content),
                metadata=_initialize_message_metadata(
                    role='tool',
                    message_id=f'tool_{len(self.messages)}',
                    metadata={'tool_name': name, 'tool_call_id': tool_call_id},
                ),
            )
        )

    def start_tool(
        self,
        *,
        name: str,
        tool_call_id: str,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        self.messages.append(
            AgentMessage(
                role='tool',
                content='',
                name=name,
                tool_call_id=tool_call_id,
                blocks=(),
                message_id=message_id,
                state='streaming',
                metadata=_initialize_message_metadata(
                    role='tool',
                    message_id=message_id or f'tool_{len(self.messages)}',
                    metadata={
                        'tool_name': name,
                        'tool_call_id': tool_call_id,
                        **dict(metadata or {}),
                    },
                ),
            )
        )
        return len(self.messages) - 1

    def append_tool_delta(
        self,
        index: int,
        delta: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        message = self.messages[index]
        merged_metadata = dict(message.metadata)
        merged_metadata = _record_mutation(
            merged_metadata,
            mutation_kind='tool_delta_append',
            previous_content=message.content,
            previous_state=message.state,
            previous_stop_reason=message.stop_reason,
            mutation_serial=self._next_mutation_serial(),
        )
        merged_metadata = _advance_lineage_revision(merged_metadata)
        if metadata:
            merged_metadata.update(metadata)
        self.messages[index] = replace(
            message,
            content=message.content + delta,
            blocks=_tool_blocks(message.name, message.tool_call_id, message.content + delta),
            metadata=merged_metadata,
        )

    def finalize_tool(
        self,
        index: int,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        stop_reason: str | None = None,
    ) -> None:
        message = self.messages[index]
        merged_metadata = dict(message.metadata)
        if message.content and message.content != content:
            merged_metadata.setdefault('stream_preview', message.content)
            merged_metadata = _record_mutation(
                merged_metadata,
                mutation_kind='tool_finalize_replace',
                previous_content=message.content,
                previous_state=message.state,
                previous_stop_reason=message.stop_reason,
                mutation_serial=self._next_mutation_serial(),
            )
            merged_metadata = _advance_lineage_revision(merged_metadata)
        if metadata:
            merged_metadata.update(metadata)
        self.messages[index] = replace(
            message,
            content=content,
            blocks=_tool_blocks(message.name, message.tool_call_id, content),
            state='final',
            stop_reason=stop_reason,
            metadata=merged_metadata,
        )

    def update_message(
        self,
        index: int,
        *,
        content: str | None = None,
        state: str | None = None,
        stop_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        mutation_kind: str | None = None,
    ) -> None:
        message = self.messages[index]
        merged_metadata = dict(message.metadata)
        new_content = message.content if content is None else content
        new_state = message.state if state is None else state
        new_stop_reason = message.stop_reason if stop_reason is None else stop_reason
        if mutation_kind and (
            new_content != message.content
            or new_state != message.state
            or new_stop_reason != message.stop_reason
        ):
            merged_metadata = _record_mutation(
                merged_metadata,
                mutation_kind=mutation_kind,
                previous_content=message.content,
                previous_state=message.state,
                previous_stop_reason=message.stop_reason,
                mutation_serial=self._next_mutation_serial(),
            )
            merged_metadata = _advance_lineage_revision(merged_metadata)
        if metadata:
            merged_metadata.update(metadata)
        self.messages[index] = replace(
            message,
            content=new_content,
            blocks=_derive_blocks(
                replace(
                    message,
                    content=new_content,
                    state=new_state,
                    stop_reason=new_stop_reason,
                )
            ),
            state=new_state,
            stop_reason=new_stop_reason,
            metadata=merged_metadata,
        )

    def tombstone_message(
        self,
        index: int,
        *,
        summary: str,
        metadata: dict[str, Any] | None = None,
        mutation_kind: str = 'tombstone',
        stop_reason: str | None = None,
    ) -> None:
        self.update_message(
            index,
            content=summary,
            state='tombstoned',
            stop_reason=stop_reason,
            metadata=metadata,
            mutation_kind=mutation_kind,
        )

    def to_openai_messages(self) -> list[JSONDict]:
        return [message.to_openai_message() for message in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        return tuple(message.to_transcript_entry() for message in self.messages)

    def _next_mutation_serial(self) -> int:
        self.mutation_serial += 1
        return self.mutation_serial

    @classmethod
    def from_persisted(
        cls,
        *,
        system_prompt_parts: tuple[str, ...] | list[str],
        user_context: dict[str, str] | None,
        system_context: dict[str, str] | None,
        messages: tuple[JSONDict, ...] | list[JSONDict],
    ) -> 'AgentSessionState':
        return cls(
            system_prompt_parts=tuple(system_prompt_parts),
            user_context=dict(user_context or {}),
            system_context=dict(system_context or {}),
            messages=[AgentMessage.from_openai_message(message) for message in messages],
            mutation_serial=max(
                (
                    int(message.get('metadata', {}).get('last_mutation_serial', 0))
                    for message in messages
                    if isinstance(message, dict)
                    and isinstance(message.get('metadata'), dict)
                    and isinstance(message.get('metadata', {}).get('last_mutation_serial', 0), int)
                    and not isinstance(message.get('metadata', {}).get('last_mutation_serial', 0), bool)
                ),
                default=0,
            ),
        )


def _usage_from_payload(payload: Any) -> UsageStats:
    if not isinstance(payload, dict):
        return UsageStats()

    def _as_int(name: str) -> int:
        value = payload.get(name, 0)
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return UsageStats(
        input_tokens=_as_int('input_tokens'),
        output_tokens=_as_int('output_tokens'),
        cache_creation_input_tokens=_as_int('cache_creation_input_tokens'),
        cache_read_input_tokens=_as_int('cache_read_input_tokens'),
        reasoning_tokens=_as_int('reasoning_tokens'),
    )


def _record_mutation(
    metadata: JSONDict,
    *,
    mutation_kind: str,
    previous_content: str,
    previous_state: str,
    previous_stop_reason: str | None,
    mutation_serial: int,
) -> JSONDict:
    mutations = metadata.get('mutations')
    if not isinstance(mutations, list):
        mutations = []
    else:
        mutations = [entry for entry in mutations if isinstance(entry, dict)]
    preview = ' '.join(previous_content.split())
    if len(preview) > 120:
        preview = preview[:117] + '...'
    mutations.append(
        {
            'kind': mutation_kind,
            'previous_state': previous_state,
            'previous_stop_reason': previous_stop_reason,
            'previous_content_length': len(previous_content),
            'previous_content_preview': preview or '(empty)',
            'serial': mutation_serial,
        }
    )
    if len(mutations) > MAX_MUTATION_HISTORY:
        mutations = mutations[-MAX_MUTATION_HISTORY:]
    metadata['mutations'] = mutations
    metadata['mutation_count'] = len(mutations)
    metadata['last_mutation_kind'] = mutation_kind
    metadata['last_mutation_serial'] = mutation_serial
    max_mutation_serial = metadata.get('max_mutation_serial')
    if isinstance(max_mutation_serial, bool) or not isinstance(max_mutation_serial, int):
        max_mutation_serial = 0
    metadata['max_mutation_serial'] = max(max_mutation_serial, mutation_serial)
    totals = metadata.get('mutation_totals')
    if not isinstance(totals, dict):
        totals = {}
    else:
        totals = {
            str(key): int(value)
            for key, value in totals.items()
            if isinstance(key, str) and not isinstance(value, bool) and isinstance(value, int)
        }
    totals[mutation_kind] = totals.get(mutation_kind, 0) + 1
    metadata['mutation_totals'] = totals
    return metadata


def _initialize_message_metadata(
    *,
    role: str,
    message_id: str | None,
    metadata: JSONDict | None = None,
) -> JSONDict:
    merged = dict(metadata or {})
    lineage_id = merged.get('lineage_id')
    if not isinstance(lineage_id, str) or not lineage_id:
        if isinstance(message_id, str) and message_id:
            lineage_id = message_id
        else:
            lineage_id = f'{role}_lineage'
    revision = merged.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int):
        revision = 0
    revision_count = merged.get('revision_count')
    if isinstance(revision_count, bool) or not isinstance(revision_count, int):
        revision_count = max(revision + 1, 1)
    merged['lineage_id'] = lineage_id
    merged['revision'] = revision
    merged['revision_count'] = revision_count
    merged.setdefault('message_role', role)
    return merged


def _advance_lineage_revision(metadata: JSONDict) -> JSONDict:
    normalized = _initialize_message_metadata(
        role=str(metadata.get('message_role', 'message')),
        message_id=metadata.get('lineage_id') if isinstance(metadata.get('lineage_id'), str) else None,
        metadata=metadata,
    )
    revision = normalized.get('revision', 0)
    if isinstance(revision, bool) or not isinstance(revision, int):
        revision = 0
    revision += 1
    normalized['revision'] = revision
    revision_count = normalized.get('revision_count', 1)
    if isinstance(revision_count, bool) or not isinstance(revision_count, int):
        revision_count = 1
    normalized['revision_count'] = max(revision_count, revision + 1)
    return normalized


def _text_blocks(text: str) -> tuple[JSONDict, ...]:
    if not text:
        return ()
    return ({'type': 'text', 'text': text},)


def _assistant_blocks(
    content: str,
    tool_calls: tuple[JSONDict, ...],
) -> tuple[JSONDict, ...]:
    blocks: list[JSONDict] = []
    if content:
        blocks.append({'type': 'text', 'text': content})
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function_block = tool_call.get('function')
        if not isinstance(function_block, dict):
            continue
        blocks.append(
            {
                'type': 'tool_call',
                'id': tool_call.get('id'),
                'name': function_block.get('name'),
                'arguments': function_block.get('arguments', ''),
            }
        )
    return tuple(blocks)


def _tool_blocks(
    name: str | None,
    tool_call_id: str | None,
    content: str,
) -> tuple[JSONDict, ...]:
    if not content:
        return ()
    return (
        {
            'type': 'tool_result',
            'name': name,
            'tool_call_id': tool_call_id,
            'text': content,
        },
    )


def _derive_blocks(message: AgentMessage) -> tuple[JSONDict, ...]:
    if message.blocks:
        return message.blocks
    if message.role == 'assistant':
        return _assistant_blocks(message.content, message.tool_calls)
    if message.role == 'tool':
        return _tool_blocks(message.name, message.tool_call_id, message.content)
    return _text_blocks(message.content)


def _append_system_context(
    system_prompt_parts: list[str],
    system_context: dict[str, str],
) -> list[str]:
    if not system_context:
        return list(system_prompt_parts)
    rendered = '\n'.join(
        f'{key}: {value}'
        for key, value in system_context.items()
        if value
    )
    return [*system_prompt_parts, rendered] if rendered else list(system_prompt_parts)


def _render_user_context_reminder(user_context: dict[str, str]) -> str:
    body = '\n'.join(
        f'# {key}\n{value}'
        for key, value in user_context.items()
        if value
    )
    return (
        '<system-reminder>\n'
        "As you answer the user's questions, you can use the following context:\n"
        f'{body}\n\n'
        'IMPORTANT: this context may or may not be relevant to the task. Use it when it materially helps and ignore it otherwise.\n'
        '</system-reminder>\n'
    )
