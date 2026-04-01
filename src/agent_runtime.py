from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from uuid import uuid4

from .agent_context import render_context_report as render_agent_context_report
from .agent_context_usage import collect_context_usage, format_context_usage
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
    execute_tool,
    serialize_tool_result,
)
from .agent_types import AgentRunResult, AgentRuntimeConfig, ModelConfig
from .openai_compat import OpenAICompatClient
from .session_store import (
    StoredAgentSession,
    save_agent_session,
    serialize_model_config,
    serialize_runtime_config,
)


@dataclass
class LocalCodingAgent:
    model_config: ModelConfig
    runtime_config: AgentRuntimeConfig
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    override_system_prompt: str | None = None
    tool_registry: dict[str, AgentTool] | None = None
    last_session: AgentSessionState | None = field(default=None, init=False, repr=False)
    last_run_result: AgentRunResult | None = field(default=None, init=False, repr=False)
    active_session_id: str | None = field(default=None, init=False, repr=False)
    last_session_path: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tool_registry is None:
            self.tool_registry = default_tool_registry()
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

    def build_prompt_context(self):
        return build_prompt_context(self.runtime_config, self.model_config)

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

    def build_session(self, user_prompt: str | None = None) -> AgentSessionState:
        prompt_context = self.build_prompt_context()
        system_prompt_parts = self.build_system_prompt_parts(prompt_context)
        return AgentSessionState.create(
            system_prompt_parts,
            user_prompt,
            user_context=prompt_context.user_context,
            system_context=prompt_context.system_context,
        )

    def run(self, prompt: str) -> AgentRunResult:
        return self._run_prompt(prompt, base_session=None, session_id=None)

    def resume(self, prompt: str, stored_session: StoredAgentSession) -> AgentRunResult:
        session = AgentSessionState.from_persisted(
            system_prompt_parts=stored_session.system_prompt_parts,
            user_context=stored_session.user_context,
            system_context=stored_session.system_context,
            messages=stored_session.messages,
        )
        self.active_session_id = stored_session.session_id
        self.last_session = session
        self.last_session_path = str(self.runtime_config.session_directory / f'{stored_session.session_id}.json')
        return self._run_prompt(
            prompt,
            base_session=session,
            session_id=stored_session.session_id,
        )

    def _run_prompt(
        self,
        prompt: str,
        *,
        base_session: AgentSessionState | None,
        session_id: str | None,
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
            )

        effective_prompt = slash_result.prompt or prompt
        session = base_session if base_session is not None else self.build_session(None)
        session.append_user(effective_prompt)
        if session_id is None:
            session_id = uuid4().hex
        self.last_session = session
        self.active_session_id = session_id
        tool_specs = [tool.to_openai_tool() for tool in self.tool_registry.values()]
        tool_calls = 0
        last_content = ''

        for turn_index in range(1, self.runtime_config.max_turns + 1):
            turn = self.client.complete(session.to_openai_messages(), tool_specs)
            assistant_tool_calls = ()
            if turn.tool_calls:
                assistant_tool_calls = tuple(
                    {
                        'id': tool_call.id,
                        'type': 'function',
                        'function': {
                            'name': tool_call.name,
                            'arguments': json.dumps(tool_call.arguments, ensure_ascii=True),
                        },
                    }
                    for tool_call in turn.tool_calls
                )
            session.append_assistant(turn.content, assistant_tool_calls)
            last_content = turn.content

            if not turn.tool_calls:
                result = AgentRunResult(
                    final_output=turn.content,
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    session_id=session_id,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            for tool_call in turn.tool_calls:
                tool_calls += 1
                result = execute_tool(
                    self.tool_registry,
                    tool_call.name,
                    tool_call.arguments,
                    self.tool_context,
                )
                session.append_tool(
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=serialize_tool_result(result),
                )

        result = AgentRunResult(
            final_output=last_content or 'Stopped: max turns reached before the model produced a final answer.',
            turns=self.runtime_config.max_turns,
            tool_calls=tool_calls,
            transcript=session.transcript(),
            session_id=session_id,
        )
        result = self._persist_session(session, result)
        self.last_run_result = result
        return result

    def _persist_session(self, session: AgentSessionState, result: AgentRunResult) -> AgentRunResult:
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
                ]
            )
        else:
            lines.append('- Last run: none')
        return '\n'.join(lines)
