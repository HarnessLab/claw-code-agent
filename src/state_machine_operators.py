"""Concrete Operator implementations for the state machine.

First thin slice — see ``~/.latti/STATE_MACHINE.md``. These operators give the
state machine a real call path before agent_runtime.py is migrated. They are
intentionally minimal and self-contained: no dependency on agent_runtime or
the full tool registry. Future passes will replace these with operators that
wrap the real claw-code-agent tools.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.agent_state_machine import (
    Action,
    ActionKind,
    Observation,
    State,
    ValidationCheck,
    ValidationResult,
)


class ReadFileOperator:
    """Reads a UTF-8 text file. Wraps Path.read_text in the Operator interface.

    Action shape:
        Action(kind='tool_call',
               payload={'tool_name': 'read_file', 'path': <abs or rel>,
                        'max_bytes': <int, optional>})
    """

    @property
    def kind(self) -> ActionKind:
        return 'tool_call'

    def can_handle(self, action: Action) -> bool:
        return (
            action.kind == 'tool_call'
            and action.payload.get('tool_name') == 'read_file'
        )

    def execute(self, action: Action, state: State) -> Observation:
        del state  # unused in this minimal implementation
        path_str = action.payload.get('path')
        if not isinstance(path_str, str) or not path_str:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': 'missing or invalid "path" in action.payload'},
            )
        max_bytes = action.payload.get('max_bytes')
        path = Path(path_str).expanduser()
        if not path.exists():
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'file not found: {path}', 'path': str(path)},
            )
        if not path.is_file():
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'not a file: {path}', 'path': str(path)},
            )
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError as exc:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'utf-8 decode failed: {exc}', 'path': str(path)},
            )
        truncated = False
        if isinstance(max_bytes, int) and max_bytes > 0 and len(content) > max_bytes:
            content = content[:max_bytes]
            truncated = True
        return Observation(
            action_id=action.id, kind='success',
            payload={'content': content, 'path': str(path), 'truncated': truncated},
        )


class JSONSchemaValidator:
    """Minimal JSON-shape validator. No external jsonschema dependency.

    Action shape:
        Action(kind='validation',
               payload={'value': <any>, 'required_keys': [<str>, ...],
                        'forbidden_keys': [<str>, ...], 'name': <str optional>})

    Observation.payload contains a serialized ValidationResult.
    """

    @property
    def kind(self) -> ActionKind:
        return 'validation'

    def can_handle(self, action: Action) -> bool:
        return action.kind == 'validation'

    def execute(self, action: Action, state: State) -> Observation:
        del state
        value = action.payload.get('value')
        required = tuple(action.payload.get('required_keys') or ())
        forbidden = tuple(action.payload.get('forbidden_keys') or ())
        name = action.payload.get('name', 'json_shape')

        checks: list[ValidationCheck] = []
        all_passed = True

        if not isinstance(value, dict):
            checks.append(ValidationCheck(
                name='is_dict', passed=False,
                evidence=f'expected dict, got {type(value).__name__}',
            ))
            all_passed = False
        else:
            for key in required:
                present = key in value
                checks.append(ValidationCheck(
                    name=f'required:{key}', passed=present,
                    evidence='present' if present else 'missing',
                ))
                if not present:
                    all_passed = False
            for key in forbidden:
                absent = key not in value
                checks.append(ValidationCheck(
                    name=f'forbidden:{key}', passed=absent,
                    evidence='absent' if absent else 'present (should be absent)',
                ))
                if not absent:
                    all_passed = False

        result = ValidationResult(
            action_id=action.id, passed=all_passed,
            checks=tuple(checks),
            severity='block' if not all_passed else 'info',
        )
        return Observation(
            action_id=action.id,
            kind='success' if all_passed else 'error',
            payload={'validation': result.to_dict(), 'name': name},
        )


class ToolCallOperator:
    """Real tool dispatcher — wraps execute_tool_streaming.

    Bridges the typed-state-machine path to claw-code-agent's actual tool
    registry. Use this when you want a real tool (read_file, write_file,
    bash, glob_search, …) executed via the runner.

    Constructor takes a tool_registry + tool_context (as built by
    ``build_tool_context()``). The operator collapses the streaming output
    of ``execute_tool_streaming`` into a single Observation, preserving the
    individual stream segments under ``observation.payload['streamed_segments']``
    so callers that care about deltas can still inspect them.

    Action shape:
        Action(kind='tool_call',
               payload={'tool_name': <str>, 'arguments': <dict>})
    """

    def __init__(
        self,
        tool_registry: dict,
        tool_context: Any,
        delta_callback: 'Callable[[str, str | None, Action], None] | None' = None,
    ) -> None:
        # Local import to avoid a top-level dependency on agent_tools when this
        # module is imported in lightweight test contexts.
        from src.agent_tools import execute_tool_streaming
        self._tool_registry = tool_registry
        self._tool_context = tool_context
        self._execute_tool_streaming = execute_tool_streaming
        # Optional callback invoked for every streaming delta. Signature:
        #     delta_callback(content: str, stream: str | None, action: Action)
        # Used to mirror legacy TUI/session behavior in flag-on agent_runtime
        # so users see live tool output instead of batched payload.
        self._delta_callback = delta_callback

    @property
    def kind(self) -> ActionKind:
        return 'tool_call'

    def can_handle(self, action: Action) -> bool:
        if action.kind != 'tool_call':
            return False
        name = action.payload.get('tool_name')
        return isinstance(name, str) and name in self._tool_registry

    def execute(self, action: Action, state: State) -> Observation:
        del state
        name = action.payload.get('tool_name')
        arguments = action.payload.get('arguments') or {}
        if not isinstance(name, str) or name not in self._tool_registry:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'unknown tool: {name!r}'},
            )

        segments: list[dict[str, Any]] = []
        final_result = None
        for update in self._execute_tool_streaming(
            self._tool_registry, name, arguments, self._tool_context,
        ):
            if update.kind == 'delta':
                segments.append({'stream': update.stream, 'content': update.content})
                if self._delta_callback is not None:
                    try:
                        self._delta_callback(update.content, update.stream, action)
                    except Exception:
                        # A buggy callback must not break tool execution.
                        pass
            elif update.kind == 'result':
                final_result = update.result

        if final_result is None:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'tool {name!r} returned no final result',
                         'streamed_segments': segments},
            )

        return Observation(
            action_id=action.id,
            kind='success' if final_result.ok else 'error',
            payload={
                'tool_name': final_result.name,
                'ok': final_result.ok,
                'content': final_result.content,
                'metadata': dict(final_result.metadata),
                'streamed_segments': segments,
            },
        )


class DelegateAgentOperator:
    """Typed operator for the runtime-managed ``delegate_agent`` tool.

    ``delegate_agent`` is registered in the tool schema but intentionally uses a
    placeholder handler in ``agent_tools`` because the real execution path lives
    on ``LocalCodingAgent``. This operator keeps that special runtime behavior
    while moving the action itself onto the typed runner.
    """

    def __init__(self, delegate_callable: Callable[[dict[str, Any]], Any]) -> None:
        self._delegate_callable = delegate_callable

    @property
    def kind(self) -> ActionKind:
        return 'tool_call'

    def can_handle(self, action: Action) -> bool:
        return (
            action.kind == 'tool_call'
            and action.payload.get('tool_name') == 'delegate_agent'
        )

    def execute(self, action: Action, state: State) -> Observation:
        del state
        arguments = action.payload.get('arguments') or {}
        if not isinstance(arguments, dict):
            return Observation(
                action_id=action.id,
                kind='error',
                payload={'error': 'delegate_agent arguments must be an object'},
            )

        try:
            result = self._delegate_callable(arguments)
        except Exception as exc:
            return Observation(
                action_id=action.id,
                kind='error',
                payload={
                    'tool_name': 'delegate_agent',
                    'error': f'delegate_agent raised: {exc!r}',
                    'metadata': {'action': 'delegate_agent'},
                },
            )

        return Observation(
            action_id=action.id,
            kind='success' if result.ok else 'error',
            payload={
                'tool_name': result.name,
                'ok': result.ok,
                'content': result.content,
                'metadata': dict(result.metadata),
                'streamed_segments': [],
            },
        )


class RealLLMOperator:
    """Real LLM operator wrapping ``OpenAICompatClient``.

    Replaces the EchoLLMOperator stub. Converts an Action into a model.complete
    call, calculates cost via the client's ModelPricing, returns a typed
    Observation with content, tool_calls, finish_reason, tokens, and cost_usd.

    Action shape:
        Action(kind='llm_call', payload={
            'messages': [{'role': ..., 'content': ...}, ...],
            'tools':    [{...openai tool spec...}, ...],     # optional
            'output_schema': {...},                          # optional
            'model_override': '<model id>',                   # optional
        })

    Observation payload on success:
        {
            'content': <str>,
            'tool_calls': [{'id', 'name', 'arguments'}, ...],
            'finish_reason': <str | None>,
        }
    """

    def __init__(self, client: Any, *, model_override: str | None = None) -> None:
        # Local-typed; we duck-type ``client.complete(messages, tools, model_override=...)``
        # and ``client.config.pricing.estimate_cost_usd(usage)``.
        self._client = client
        self._model_override = model_override

    @property
    def kind(self) -> ActionKind:
        return 'llm_call'

    def can_handle(self, action: Action) -> bool:
        if action.kind != 'llm_call':
            return False
        return isinstance(action.payload.get('messages'), list)

    def execute(self, action: Action, state: State) -> Observation:
        del state
        messages = action.payload.get('messages')
        tools = action.payload.get('tools') or []
        output_schema = action.payload.get('output_schema')
        model_override = action.payload.get('model_override') or self._model_override

        if not isinstance(messages, list) or not messages:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': 'messages must be a non-empty list'},
            )

        try:
            kwargs: dict[str, Any] = {'model_override': model_override}
            if output_schema is not None:
                kwargs['output_schema'] = output_schema
            turn = self._client.complete(
                messages=messages, tools=tools, **kwargs,
            )
        except Exception as exc:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'LLM call failed: {exc!r}'},
            )

        # Estimate cost via the client's pricing config (if present).
        cost = 0.0
        try:
            cost = self._client.config.pricing.estimate_cost_usd(turn.usage)
        except Exception:
            pass

        tool_calls_serialized = [
            {'id': tc.id, 'name': tc.name, 'arguments': dict(getattr(tc, 'arguments', {}) or {})}
            for tc in (turn.tool_calls or ())
        ]

        return Observation(
            action_id=action.id, kind='success',
            payload={
                'content': turn.content,
                'tool_calls': tool_calls_serialized,
                'finish_reason': turn.finish_reason,
                'thinking': turn.thinking,
                'usage': turn.usage.to_dict(),
            },
            cost_usd=cost,
            tokens=turn.usage.total_tokens if turn.usage else None,
        )


class StreamingLLMOperator:
    """LLM operator wrapping ``OpenAICompatClient.stream()``.

    Streams tokens from the model in real time. Optional ``token_callback``
    fires per text-delta so the TUI can render live output.

    Action shape: same as RealLLMOperator. Observation payload:
        {'content': <accumulated str>, 'tool_calls': [...], 'finish_reason': ...}
    """

    def __init__(
        self,
        client: Any,
        *,
        model_override: str | None = None,
        token_callback: Callable[[str, Action], None] | None = None,
        event_callback: Callable[[Any, Action], None] | None = None,
    ) -> None:
        self._client = client
        self._model_override = model_override
        self._token_callback = token_callback
        self._event_callback = event_callback

    @property
    def kind(self) -> ActionKind:
        return 'llm_call'

    def can_handle(self, action: Action) -> bool:
        if action.kind != 'llm_call':
            return False
        return isinstance(action.payload.get('messages'), list)

    def execute(self, action: Action, state: State) -> Observation:
        del state
        messages = action.payload.get('messages')
        tools = action.payload.get('tools') or []
        output_schema = action.payload.get('output_schema')
        model_override = action.payload.get('model_override') or self._model_override

        if not isinstance(messages, list) or not messages:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': 'messages must be a non-empty list'},
            )

        accumulated: list[str] = []
        tool_calls_raw: list[dict[str, Any]] = []
        finish_reason: str | None = None
        usage_total = None
        thinking_text = ''

        try:
            kwargs: dict[str, Any] = {'model_override': model_override}
            if output_schema is not None:
                kwargs['output_schema'] = output_schema
            stream = self._client.stream(
                messages=messages, tools=tools, **kwargs,
            )
            for event in stream:
                etype = getattr(event, 'type', None)
                if self._event_callback is not None:
                    try:
                        self._event_callback(event, action)
                    except Exception:
                        pass
                if etype == 'content_delta':
                    delta = getattr(event, 'delta', '')
                    if delta:
                        accumulated.append(delta)
                        if self._token_callback is not None:
                            try:
                                self._token_callback(delta, action)
                            except Exception:
                                pass
                elif etype == 'thinking_delta':
                    delta = getattr(event, 'delta', '')
                    if delta:
                        thinking_text += delta
                elif etype == 'tool_call_start':
                    tc_id = getattr(event, 'tool_call_id', None)
                    name = getattr(event, 'tool_name', None)
                    tool_calls_raw.append({'id': tc_id, 'name': name, 'arguments_json': ''})
                elif etype == 'tool_call_delta':
                    delta = getattr(event, 'delta', '')
                    if not isinstance(delta, str) or not delta:
                        delta = getattr(event, 'arguments_delta', '')
                    index = getattr(event, 'tool_call_index', None)
                    tc_id = getattr(event, 'tool_call_id', None)
                    name = getattr(event, 'tool_name', None)

                    if isinstance(index, int):
                        while len(tool_calls_raw) <= index:
                            tool_calls_raw.append({'id': None, 'name': None, 'arguments_json': ''})
                        target = tool_calls_raw[index]
                    else:
                        if not tool_calls_raw:
                            tool_calls_raw.append({'id': None, 'name': None, 'arguments_json': ''})
                        target = tool_calls_raw[-1]

                    if tc_id is not None:
                        target['id'] = tc_id
                    if name is not None:
                        target['name'] = name
                    if isinstance(delta, str) and delta:
                        target['arguments_json'] += delta
                elif etype == 'message_stop':
                    finish_reason = getattr(event, 'finish_reason', None)
                elif etype == 'usage':
                    usage_total = getattr(event, 'usage', None)
        except Exception as exc:
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': f'LLM stream failed: {exc!r}',
                         'partial_content': ''.join(accumulated)},
            )

        # Parse accumulated tool_call argument JSON. Drop entries with bad JSON.
        parsed_tool_calls: list[dict[str, Any]] = []
        for tc in tool_calls_raw:
            args = {}
            if tc.get('arguments_json'):
                try:
                    args = json.loads(tc['arguments_json'])
                except json.JSONDecodeError:
                    args = {'_raw': tc['arguments_json']}
            parsed_tool_calls.append({'id': tc.get('id'), 'name': tc.get('name'), 'arguments': args})

        cost = 0.0
        if usage_total is not None:
            try:
                cost = self._client.config.pricing.estimate_cost_usd(usage_total)
            except Exception:
                pass

        return Observation(
            action_id=action.id, kind='success',
            payload={
                'content': ''.join(accumulated),
                'tool_calls': parsed_tool_calls,
                'finish_reason': finish_reason,
                'thinking': thinking_text,
                'usage': usage_total.to_dict() if usage_total is not None else {},
            },
            cost_usd=cost,
            tokens=usage_total.total_tokens if usage_total else None,
        )


class EchoLLMOperator:
    """Stub LLM operator. Echoes the prompt back as the completion.

    A real LLM operator will wrap openai_compat.OpenAIClient. This stub exists
    so the runner has an llm_call branch to dispatch to without networking
    until the real wrapper is wired in a later pass.

    Action shape:
        Action(kind='llm_call', payload={'prompt': <str>})
    """

    @property
    def kind(self) -> ActionKind:
        return 'llm_call'

    def can_handle(self, action: Action) -> bool:
        return action.kind == 'llm_call'

    def execute(self, action: Action, state: State) -> Observation:
        del state
        prompt = action.payload.get('prompt')
        if not isinstance(prompt, str):
            return Observation(
                action_id=action.id, kind='error',
                payload={'error': 'missing or invalid "prompt" in action.payload'},
            )
        # Stub: returns the prompt prefixed. Real implementation would call the model.
        completion = f'echo: {prompt}'
        return Observation(
            action_id=action.id, kind='success',
            payload={'completion': completion, 'is_stub': True},
            tokens=len(prompt.split()) + len(completion.split()),
        )
