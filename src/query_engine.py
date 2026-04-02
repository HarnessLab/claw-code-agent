from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .agent_runtime import LocalCodingAgent
from .commands import build_command_backlog
from .models import PermissionDenial, UsageSummary
from .plugin_runtime import PluginRuntime
from .port_manifest import PortManifest, build_port_manifest
from .session_store import StoredSession, load_agent_session, load_session, save_session
from .tools import build_tool_backlog
from .transcript import TranscriptStore


@dataclass(frozen=True)
class QueryEngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 2000
    compact_after_turns: int = 12
    structured_output: bool = False
    structured_retry_limit: int = 2
    use_runtime_agent: bool = False


@dataclass(frozen=True)
class TurnResult:
    prompt: str
    output: str
    matched_commands: tuple[str, ...]
    matched_tools: tuple[str, ...]
    permission_denials: tuple[PermissionDenial, ...]
    usage: UsageSummary
    stop_reason: str
    session_id: str | None = None
    session_path: str | None = None
    tool_calls: int = 0
    total_cost_usd: float = 0.0
    events: tuple[dict[str, object], ...] = ()
    transcript: tuple[dict[str, object], ...] = ()


@dataclass
class QueryEnginePort:
    manifest: PortManifest
    config: QueryEngineConfig = field(default_factory=QueryEngineConfig)
    session_id: str = field(default_factory=lambda: uuid4().hex)
    mutable_messages: list[str] = field(default_factory=list)
    permission_denials: list[PermissionDenial] = field(default_factory=list)
    total_usage: UsageSummary = field(default_factory=UsageSummary)
    transcript_store: TranscriptStore = field(default_factory=TranscriptStore)
    runtime_agent: LocalCodingAgent | None = None
    plugin_runtime: PluginRuntime | None = None
    runtime_event_counts: dict[str, int] = field(default_factory=dict)
    runtime_message_kind_counts: dict[str, int] = field(default_factory=dict)
    runtime_transcript_size: int = 0
    last_turn: TurnResult | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_workspace(cls) -> 'QueryEnginePort':
        return cls(
            manifest=build_port_manifest(),
            plugin_runtime=PluginRuntime.from_workspace(Path.cwd()),
        )

    @classmethod
    def from_saved_session(cls, session_id: str) -> 'QueryEnginePort':
        stored = load_session(session_id)
        transcript = TranscriptStore(entries=list(stored.messages), flushed=True)
        return cls(
            manifest=build_port_manifest(),
            session_id=stored.session_id,
            mutable_messages=list(stored.messages),
            total_usage=UsageSummary(stored.input_tokens, stored.output_tokens),
            transcript_store=transcript,
            plugin_runtime=PluginRuntime.from_workspace(Path.cwd()),
        )

    @classmethod
    def from_runtime_agent(
        cls,
        agent: LocalCodingAgent,
        *,
        manifest: PortManifest | None = None,
    ) -> 'QueryEnginePort':
        return cls(
            manifest=manifest or build_port_manifest(),
            config=QueryEngineConfig(use_runtime_agent=True),
            session_id=agent.active_session_id or uuid4().hex,
            runtime_agent=agent,
            plugin_runtime=PluginRuntime.from_workspace(
                agent.runtime_config.cwd,
                tuple(str(path) for path in agent.runtime_config.additional_working_directories),
            ),
        )

    def submit_message(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ) -> TurnResult:
        if self.config.use_runtime_agent and self.runtime_agent is not None:
            result = self._submit_runtime_message(prompt)
            turn = TurnResult(
                prompt=prompt,
                output=result.final_output,
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=UsageSummary(
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                ),
                stop_reason=result.stop_reason or 'completed',
                session_id=result.session_id,
                session_path=result.session_path,
                tool_calls=result.tool_calls,
                total_cost_usd=result.total_cost_usd,
                events=result.events,
                transcript=result.transcript,
            )
            self._record_turn(prompt, turn, denied_tools)
            return turn

        if len(self.mutable_messages) >= self.config.max_turns:
            output = f'Max turns reached before processing prompt: {prompt}'
            return TurnResult(
                prompt=prompt,
                output=output,
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=self.total_usage,
                stop_reason='max_turns_reached',
            )

        summary_lines = [
            f'Prompt: {prompt}',
            f'Matched commands: {", ".join(matched_commands) if matched_commands else "none"}',
            f'Matched tools: {", ".join(matched_tools) if matched_tools else "none"}',
            f'Permission denials: {len(denied_tools)}',
        ]
        output = self._format_output(summary_lines)
        projected_usage = self.total_usage.add_turn(prompt, output)
        stop_reason = 'completed'
        if projected_usage.input_tokens + projected_usage.output_tokens > self.config.max_budget_tokens:
            stop_reason = 'max_budget_reached'
        turn = TurnResult(
            prompt=prompt,
            output=output,
            matched_commands=matched_commands,
            matched_tools=matched_tools,
            permission_denials=denied_tools,
            usage=projected_usage,
            stop_reason=stop_reason,
        )
        self._record_turn(prompt, turn, denied_tools)
        self.compact_messages_if_needed()
        return turn

    def stream_submit_message(
        self,
        prompt: str,
        matched_commands: tuple[str, ...] = (),
        matched_tools: tuple[str, ...] = (),
        denied_tools: tuple[PermissionDenial, ...] = (),
    ):
        yield {'type': 'message_start', 'session_id': self.session_id, 'prompt': prompt}
        if matched_commands:
            yield {'type': 'command_match', 'commands': matched_commands}
        if matched_tools:
            yield {'type': 'tool_match', 'tools': matched_tools}
        if denied_tools:
            yield {'type': 'permission_denial', 'denials': [denial.tool_name for denial in denied_tools]}
        result = self.submit_message(prompt, matched_commands, matched_tools, denied_tools)
        if self.config.use_runtime_agent:
            for event in result.events:
                yield event
            yield {
                'type': 'message_stop',
                'usage': {
                    'input_tokens': result.usage.input_tokens,
                    'output_tokens': result.usage.output_tokens,
                },
                'stop_reason': result.stop_reason,
                'session_id': result.session_id,
                'transcript_size': len(result.transcript),
            }
            return
        yield {'type': 'message_delta', 'text': result.output}
        yield {
            'type': 'message_stop',
            'usage': {'input_tokens': result.usage.input_tokens, 'output_tokens': result.usage.output_tokens},
            'stop_reason': result.stop_reason,
            'transcript_size': len(self.transcript_store.entries),
        }

    def compact_messages_if_needed(self) -> None:
        if len(self.mutable_messages) > self.config.compact_after_turns:
            self.mutable_messages[:] = self.mutable_messages[-self.config.compact_after_turns :]
        self.transcript_store.compact(self.config.compact_after_turns)

    def replay_user_messages(self) -> tuple[str, ...]:
        return self.transcript_store.replay()

    def flush_transcript(self) -> None:
        self.transcript_store.flush()

    def persist_session(self) -> str:
        if self.config.use_runtime_agent and self.last_turn is not None and self.last_turn.session_path:
            return self.last_turn.session_path
        self.flush_transcript()
        path = save_session(
            StoredSession(
                session_id=self.session_id,
                messages=tuple(self.mutable_messages),
                input_tokens=self.total_usage.input_tokens,
                output_tokens=self.total_usage.output_tokens,
            )
        )
        return str(path)

    def render_summary(self) -> str:
        command_backlog = build_command_backlog()
        tool_backlog = build_tool_backlog()
        sections = [
            '# Python Porting Workspace Summary',
            '',
            self.manifest.to_markdown(),
            '',
            f'Command surface: {len(command_backlog.modules)} mirrored entries',
            *command_backlog.summary_lines()[:10],
            '',
            f'Tool surface: {len(tool_backlog.modules)} mirrored entries',
            *tool_backlog.summary_lines()[:10],
            '',
            f'Session id: {self.session_id}',
            f'Conversation turns stored: {len(self.mutable_messages)}',
            f'Permission denials tracked: {len(self.permission_denials)}',
            f'Usage totals: in={self.total_usage.input_tokens} out={self.total_usage.output_tokens}',
            f'Max turns: {self.config.max_turns}',
            f'Max budget tokens: {self.config.max_budget_tokens}',
            f'Transcript flushed: {self.transcript_store.flushed}',
            f'Real runtime agent mode: {self.config.use_runtime_agent}',
        ]
        if self.plugin_runtime is not None:
            sections.extend(['', '## Plugin Runtime', self.plugin_runtime.render_summary()])
        if self.runtime_agent is not None and self.runtime_agent.agent_manager is not None:
            sections.extend(['', '## Agent Manager', *self.runtime_agent.agent_manager.summary_lines()])
        if self.runtime_event_counts:
            sections.extend(['', '## Runtime Events'])
            sections.extend(
                f'- {name}={count}'
                for name, count in sorted(self.runtime_event_counts.items())
            )
            sections.append(f'- runtime_transcript_size={self.runtime_transcript_size}')
        if self.runtime_message_kind_counts:
            sections.extend(['', '## Runtime Message Kinds'])
            sections.extend(
                f'- {name}={count}'
                for name, count in sorted(self.runtime_message_kind_counts.items())
            )
        if self.last_turn is not None:
            sections.extend(
                [
                    '',
                    '## Last Turn',
                    f'- stop_reason={self.last_turn.stop_reason}',
                    f'- tool_calls={self.last_turn.tool_calls}',
                    f'- session_id={self.last_turn.session_id or "none"}',
                    f'- transcript_messages={len(self.last_turn.transcript)}',
                ]
            )
        return '\n'.join(sections)

    def _format_output(self, summary_lines: list[str]) -> str:
        if self.config.structured_output:
            payload = {
                'summary': summary_lines,
                'session_id': self.session_id,
            }
            return self._render_structured_output(payload)
        return '\n'.join(summary_lines)

    def _render_structured_output(self, payload: dict[str, object]) -> str:
        last_error: Exception | None = None
        for _ in range(self.config.structured_retry_limit):
            try:
                return json.dumps(payload, indent=2)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive branch
                last_error = exc
                payload = {'summary': ['structured output retry'], 'session_id': self.session_id}
        raise RuntimeError('structured output rendering failed') from last_error

    def _record_turn(
        self,
        prompt: str,
        turn: TurnResult,
        denied_tools: tuple[PermissionDenial, ...],
    ) -> None:
        self.mutable_messages.append(prompt)
        self.transcript_store.append(prompt)
        self.transcript_store.append(turn.output)
        if self.config.use_runtime_agent:
            self._record_runtime_turn(turn)
        self.permission_denials.extend(denied_tools)
        self.total_usage = turn.usage
        self.last_turn = turn
        if turn.session_id is not None:
            self.session_id = turn.session_id

    def _submit_runtime_message(self, prompt: str):
        assert self.runtime_agent is not None
        if self.last_turn is None or not self.last_turn.session_id:
            return self.runtime_agent.run(prompt)
        stored = load_agent_session(
            self.last_turn.session_id,
            directory=self.runtime_agent.runtime_config.session_directory,
        )
        return self.runtime_agent.resume(prompt, stored)

    def _record_runtime_turn(self, turn: TurnResult) -> None:
        self.runtime_transcript_size = len(turn.transcript)
        event_counts: dict[str, int] = {}
        for event in turn.events:
            event_type = event.get('type')
            if not isinstance(event_type, str) or not event_type:
                continue
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            self.runtime_event_counts[event_type] = (
                self.runtime_event_counts.get(event_type, 0) + 1
            )
        kind_counts: dict[str, int] = {}
        for entry in turn.transcript:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get('metadata')
            if not isinstance(metadata, dict):
                continue
            kind = metadata.get('kind')
            if not isinstance(kind, str) or not kind:
                continue
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            self.runtime_message_kind_counts[kind] = (
                self.runtime_message_kind_counts.get(kind, 0) + 1
            )
        summary = self._summarize_runtime_turn(event_counts, kind_counts, len(turn.transcript))
        if summary:
            self.transcript_store.append(summary)

    def _summarize_runtime_turn(
        self,
        event_counts: dict[str, int],
        kind_counts: dict[str, int],
        transcript_size: int,
    ) -> str:
        parts = [f'runtime_transcript={transcript_size}']
        if event_counts:
            parts.append(
                'events='
                + ', '.join(
                    f'{name}:{count}'
                    for name, count in sorted(event_counts.items())
                )
            )
        if kind_counts:
            parts.append(
                'kinds='
                + ', '.join(
                    f'{name}:{count}'
                    for name, count in sorted(kind_counts.items())
                )
            )
        return '[runtime] ' + ' | '.join(parts)
