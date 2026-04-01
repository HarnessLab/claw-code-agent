from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class AgentMessage:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[JSONDict, ...] = ()

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

    @classmethod
    def from_openai_message(cls, payload: JSONDict) -> 'AgentMessage':
        tool_calls = payload.get('tool_calls')
        normalized_tool_calls: tuple[JSONDict, ...] = ()
        if isinstance(tool_calls, list):
            normalized_tool_calls = tuple(
                item for item in tool_calls if isinstance(item, dict)
            )
        return cls(
            role=str(payload.get('role', 'user')),
            content='' if payload.get('content') is None else str(payload.get('content', '')),
            name=str(payload['name']) if isinstance(payload.get('name'), str) else None,
            tool_call_id=str(payload['tool_call_id']) if isinstance(payload.get('tool_call_id'), str) else None,
            tool_calls=normalized_tool_calls,
        )


@dataclass
class AgentSessionState:
    system_prompt_parts: tuple[str, ...]
    user_context: dict[str, str] = field(default_factory=dict)
    system_context: dict[str, str] = field(default_factory=dict)
    messages: list[AgentMessage] = field(default_factory=list)

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
            )
        )
        if state.user_context:
            state.messages.append(
                AgentMessage(
                    role='user',
                    content=_render_user_context_reminder(state.user_context),
                )
            )
        if user_prompt is not None:
            state.messages.append(
                AgentMessage(
                    role='user',
                    content=user_prompt,
                )
            )
        return state

    def append_assistant(
        self,
        content: str,
        tool_calls: tuple[JSONDict, ...] = (),
    ) -> None:
        self.messages.append(
            AgentMessage(
                role='assistant',
                content=content,
                tool_calls=tool_calls,
            )
        )

    def append_user(self, content: str) -> None:
        self.messages.append(
            AgentMessage(
                role='user',
                content=content,
            )
        )

    def append_tool(self, name: str, tool_call_id: str, content: str) -> None:
        self.messages.append(
            AgentMessage(
                role='tool',
                content=content,
                name=name,
                tool_call_id=tool_call_id,
            )
        )

    def to_openai_messages(self) -> list[JSONDict]:
        return [message.to_openai_message() for message in self.messages]

    def transcript(self) -> tuple[JSONDict, ...]:
        return tuple(message.to_openai_message() for message in self.messages)

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
        )


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
