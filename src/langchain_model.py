from __future__ import annotations

import json
from typing import Any, Iterator

from .agent_types import AssistantTurn, ModelConfig, OutputSchemaConfig, StreamEvent, ToolCall, UsageStats
from .openai_compat import (
    OpenAICompatError,
    _build_response_format,
    _normalize_content,
    _parse_tool_arguments,
)

try:
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - optional dependency
    AIMessage = None  # type: ignore[misc, assignment]
    AIMessageChunk = None  # type: ignore[misc, assignment]
    HumanMessage = None  # type: ignore[misc, assignment]
    SystemMessage = None  # type: ignore[misc, assignment]
    ToolMessage = None  # type: ignore[misc, assignment]
    ChatOpenAI = None  # type: ignore[misc, assignment]
    _LANGCHAIN_IMPORT_ERROR = True
else:
    _LANGCHAIN_IMPORT_ERROR = False


def _require_langchain() -> None:
    if _LANGCHAIN_IMPORT_ERROR:
        raise ImportError(
            'LangChain backend requires optional dependencies. Install with: '
            "pip install 'claw-code-agent[langchain]'"
        ) from None


def _stringify_message_content(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return _normalize_content(content)


def openai_chat_messages_to_langchain(messages: list[dict[str, Any]]) -> list[Any]:
    """Convert OpenAI-format chat messages to LangChain message objects."""
    _require_langchain()
    assert SystemMessage is not None and HumanMessage is not None
    assert AIMessage is not None and ToolMessage is not None
    out: list[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get('role', 'user'))
        content = _stringify_message_content(msg.get('content'))
        if role == 'system':
            out.append(SystemMessage(content=content))
        elif role == 'user':
            out.append(HumanMessage(content=content))
        elif role == 'assistant':
            lc_tool_calls: list[dict[str, Any]] = []
            raw_tool_calls = msg.get('tool_calls')
            if isinstance(raw_tool_calls, list):
                for tc in raw_tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get('function')
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get('name')
                    if not isinstance(name, str) or not name:
                        continue
                    tid = tc.get('id')
                    call_id = tid if isinstance(tid, str) and tid else f'call_{len(lc_tool_calls)}'
                    args = _parse_tool_arguments(fn.get('arguments'))
                    lc_tool_calls.append({'name': name, 'args': args, 'id': call_id})
            kwargs: dict[str, Any] = {'content': content}
            if lc_tool_calls:
                kwargs['tool_calls'] = lc_tool_calls
            out.append(AIMessage(**kwargs))
        elif role == 'tool':
            tid = msg.get('tool_call_id')
            tool_call_id = tid if isinstance(tid, str) else ''
            out.append(ToolMessage(content=content, tool_call_id=tool_call_id))
        else:
            out.append(HumanMessage(content=content))
    return out


def _usage_metadata_to_stats(metadata: Any) -> UsageStats:
    if not isinstance(metadata, dict):
        return UsageStats()

    def _as_int(key: str) -> int:
        v = metadata.get(key, 0)
        if isinstance(v, bool):
            return 0
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    details_in = metadata.get('input_token_details')
    details_out = metadata.get('output_token_details')
    reasoning = 0
    if isinstance(details_out, dict):
        reasoning = _as_int('reasoning')
        if not reasoning:
            r = details_out.get('reasoning')
            if isinstance(r, int):
                reasoning = r
    return UsageStats(
        input_tokens=_as_int('input_tokens'),
        output_tokens=_as_int('output_tokens'),
        cache_creation_input_tokens=_as_int('cache_creation_input_tokens'),
        cache_read_input_tokens=_as_int('cache_read_input_tokens'),
        reasoning_tokens=reasoning or _as_int('reasoning_tokens'),
    )


def _assistant_turn_from_ai_message(response: Any) -> AssistantTurn:
    tool_calls: list[ToolCall] = []
    for idx, tc in enumerate(response.tool_calls or []):
        if not isinstance(tc, dict):
            continue
        name = tc.get('name')
        if not isinstance(name, str) or not name:
            continue
        raw_id = tc.get('id')
        call_id = raw_id if isinstance(raw_id, str) and raw_id else f'call_{idx}'
        args = tc.get('args')
        arguments: dict[str, Any] = args if isinstance(args, dict) else {}
        tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

    meta = getattr(response, 'response_metadata', None) or {}
    finish_reason = meta.get('finish_reason') if isinstance(meta, dict) else None
    if finish_reason is not None and not isinstance(finish_reason, str):
        finish_reason = str(finish_reason)

    raw_message: dict[str, Any] = {
        'role': 'assistant',
        'content': _stringify_message_content(response.content),
    }
    if tool_calls:
        raw_message['tool_calls'] = [
            {
                'id': tc.id,
                'type': 'function',
                'function': {
                    'name': tc.name,
                    'arguments': json.dumps(tc.arguments, ensure_ascii=True),
                },
            }
            for tc in tool_calls
        ]

    usage = _usage_metadata_to_stats(getattr(response, 'usage_metadata', None))

    return AssistantTurn(
        content=_stringify_message_content(response.content),
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        raw_message=raw_message,
        usage=usage,
    )


class LangChainChatClient:
    """OpenAI-compatible chat completions via LangChain ``ChatOpenAI``."""

    def __init__(self, config: ModelConfig) -> None:
        _require_langchain()
        self.config = config

    def _chat_model(
        self,
        *,
        output_schema: OutputSchemaConfig | None,
        stream: bool,
    ) -> Any:
        assert ChatOpenAI is not None
        model_kwargs: dict[str, Any] = {}
        response_format = _build_response_format(output_schema)
        if response_format is not None:
            model_kwargs['response_format'] = response_format
        return ChatOpenAI(
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            request_timeout=self.config.timeout_seconds,
            model_kwargs=model_kwargs or {},
            streaming=stream,
            stream_usage=stream,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> AssistantTurn:
        llm = self._chat_model(output_schema=output_schema, stream=False)
        if tools:
            llm = llm.bind_tools(tools)  # type: ignore[union-attr]
        lc_messages = openai_chat_messages_to_langchain(messages)
        try:
            response = llm.invoke(lc_messages)
        except Exception as exc:
            raise OpenAICompatError(f'LangChain model request failed: {exc}') from exc
        if AIMessage is None or not isinstance(response, AIMessage):
            raise OpenAICompatError(
                f'LangChain returned unexpected message type: {type(response).__name__}'
            )
        return _assistant_turn_from_ai_message(response)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        output_schema: OutputSchemaConfig | None = None,
    ) -> Iterator[StreamEvent]:
        llm = self._chat_model(output_schema=output_schema, stream=True)
        if tools:
            llm = llm.bind_tools(tools)  # type: ignore[union-attr]
        lc_messages = openai_chat_messages_to_langchain(messages)
        yield StreamEvent(type='message_start')
        gathered: Any = None
        try:
            for chunk in llm.stream(lc_messages):
                if AIMessageChunk is None or not isinstance(chunk, AIMessageChunk):
                    continue
                gathered = chunk if gathered is None else gathered + chunk
                delta_text = chunk.content
                if isinstance(delta_text, str) and delta_text:
                    yield StreamEvent(
                        type='content_delta',
                        delta=delta_text,
                        raw_event={'content': delta_text},
                    )
                for tcc in chunk.tool_call_chunks or []:
                    if not isinstance(tcc, dict):
                        continue
                    idx = tcc.get('index')
                    args_delta = tcc.get('args')
                    yield StreamEvent(
                        type='tool_call_delta',
                        tool_call_index=idx if isinstance(idx, int) else 0,
                        tool_call_id=tcc.get('id') if isinstance(tcc.get('id'), str) else None,
                        tool_name=tcc.get('name') if isinstance(tcc.get('name'), str) else None,
                        arguments_delta=args_delta if isinstance(args_delta, str) else '',
                        raw_event=dict(tcc),
                    )
        except Exception as exc:
            raise OpenAICompatError(f'LangChain streaming request failed: {exc}') from exc

        usage = _usage_metadata_to_stats(
            getattr(gathered, 'usage_metadata', None) if gathered is not None else None
        )
        if usage.total_tokens:
            yield StreamEvent(type='usage', usage=usage, raw_event=usage.to_dict())

        finish_reason: str | None = None
        meta: dict[str, Any] = {}
        if gathered is not None:
            raw_meta = getattr(gathered, 'response_metadata', None)
            if isinstance(raw_meta, dict):
                meta = dict(raw_meta)
                fr = meta.get('finish_reason')
                if isinstance(fr, str):
                    finish_reason = fr
                elif fr is not None:
                    finish_reason = str(fr)

        yield StreamEvent(
            type='message_stop',
            finish_reason=finish_reason,
            raw_event=meta,
        )
