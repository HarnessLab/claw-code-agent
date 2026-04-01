from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .agent_types import AssistantTurn, ModelConfig, ToolCall


class OpenAICompatError(RuntimeError):
    """Raised when the local OpenAI-compatible backend returns an invalid response."""


def _join_url(base_url: str, suffix: str) -> str:
    base = base_url.rstrip('/')
    return f'{base}/{suffix.lstrip("/")}'


def _normalize_content(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            if item.get('type') == 'text' and isinstance(item.get('text'), str):
                parts.append(item['text'])
                continue
            if isinstance(item.get('text'), str):
                parts.append(item['text'])
                continue
            parts.append(json.dumps(item, ensure_ascii=True))
        return ''.join(parts)
    return str(content)


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        raw_arguments = raw_arguments.strip()
        if not raw_arguments:
            return {}
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise OpenAICompatError(
                f'Invalid tool arguments returned by model: {raw_arguments!r}'
            ) from exc
        if not isinstance(parsed, dict):
            raise OpenAICompatError(
                f'Tool arguments must decode to an object, got {type(parsed).__name__}'
            )
        return parsed
    raise OpenAICompatError(
        f'Unsupported tool arguments payload: {type(raw_arguments).__name__}'
    )


class OpenAICompatClient:
    """Minimal OpenAI-compatible chat client for local model servers."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        payload = {
            'model': self.config.model,
            'messages': messages,
            'tools': tools,
            'tool_choice': 'auto',
            'temperature': self.config.temperature,
            'stream': False,
        }
        body = json.dumps(payload).encode('utf-8')
        req = request.Request(
            _join_url(self.config.base_url, '/chat/completions'),
            data=body,
            headers={
                'Authorization': f'Bearer {self.config.api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise OpenAICompatError(
                f'HTTP {exc.code} from local model backend: {detail}'
            ) from exc
        except error.URLError as exc:
            raise OpenAICompatError(
                f'Unable to reach local model backend at {self.config.base_url}: {exc.reason}'
            ) from exc

        try:
            payload = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise OpenAICompatError('Local model backend returned invalid JSON') from exc

        choices = payload.get('choices')
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatError('Local model backend returned no choices')
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenAICompatError('Local model backend returned malformed choice data')

        message = first_choice.get('message')
        if not isinstance(message, dict):
            raise OpenAICompatError('Local model backend returned no assistant message')

        content = _normalize_content(message.get('content'))
        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get('tool_calls')
        if isinstance(raw_tool_calls, list):
            for idx, raw_call in enumerate(raw_tool_calls):
                if not isinstance(raw_call, dict):
                    raise OpenAICompatError('Malformed tool call payload from model')
                function_block = raw_call.get('function') or {}
                if not isinstance(function_block, dict):
                    raise OpenAICompatError('Malformed tool call function payload from model')
                name = function_block.get('name')
                if not isinstance(name, str) or not name:
                    raise OpenAICompatError('Tool call missing function name')
                call_id = raw_call.get('id')
                if not isinstance(call_id, str) or not call_id:
                    call_id = f'call_{idx}'
                arguments = _parse_tool_arguments(function_block.get('arguments'))
                tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        elif isinstance(message.get('function_call'), dict):
            function_call = message['function_call']
            name = function_call.get('name')
            if not isinstance(name, str) or not name:
                raise OpenAICompatError('Function call missing name')
            arguments = _parse_tool_arguments(function_call.get('arguments'))
            tool_calls.append(ToolCall(id='call_0', name=name, arguments=arguments))

        finish_reason = first_choice.get('finish_reason')
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

        return AssistantTurn(
            content=content,
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            raw_message=message,
        )
