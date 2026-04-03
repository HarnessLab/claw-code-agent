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
    runtime_cumulative_usage: UsageSummary = field(default_factory=UsageSummary)
    runtime_event_counts: dict[str, int] = field(default_factory=dict)
    runtime_message_kind_counts: dict[str, int] = field(default_factory=dict)
    runtime_mutation_counts: dict[str, int] = field(default_factory=dict)
    runtime_group_status_counts: dict[str, int] = field(default_factory=dict)
    runtime_child_stop_reason_counts: dict[str, int] = field(default_factory=dict)
    runtime_resumed_children: int = 0
    runtime_context_reduction: dict[str, int] = field(default_factory=dict)
    runtime_lineage_stats: dict[str, int] = field(default_factory=dict)
    runtime_transcript_size: int = 0
    _runtime_seen_lineages: set[str] = field(default_factory=set, init=False, repr=False)
    _runtime_revised_lineages: set[str] = field(default_factory=set, init=False, repr=False)
    _runtime_tombstoned_lineages: set[str] = field(default_factory=set, init=False, repr=False)
    _runtime_compacted_lineages: set[str] = field(default_factory=set, init=False, repr=False)
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
            runtime_cumulative_usage=UsageSummary(stored.input_tokens, stored.output_tokens),
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
            cumulative_usage = UsageSummary(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            usage = cumulative_usage
            if self.runtime_cumulative_usage.input_tokens or self.runtime_cumulative_usage.output_tokens:
                usage = UsageSummary(
                    input_tokens=max(
                        cumulative_usage.input_tokens - self.runtime_cumulative_usage.input_tokens,
                        0,
                    ),
                    output_tokens=max(
                        cumulative_usage.output_tokens - self.runtime_cumulative_usage.output_tokens,
                        0,
                    ),
                )
            else:
                usage = UsageSummary(
                    input_tokens=cumulative_usage.input_tokens,
                    output_tokens=cumulative_usage.output_tokens,
                )
            turn = TurnResult(
                prompt=prompt,
                output=result.final_output,
                matched_commands=matched_commands,
                matched_tools=matched_tools,
                permission_denials=denied_tools,
                usage=usage,
                stop_reason=result.stop_reason or 'completed',
                session_id=result.session_id,
                session_path=result.session_path,
                tool_calls=result.tool_calls,
                total_cost_usd=result.total_cost_usd,
                events=result.events,
                transcript=result.transcript,
            )
            self._record_turn(
                prompt,
                turn,
                denied_tools,
                runtime_cumulative_usage=cumulative_usage,
            )
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
            yield self._runtime_summary_event()
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
                messages=self.transcript_store.replay(),
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
        sections.extend(['', '## Transcript Store', *self.transcript_store.summary_lines()])
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
        if self.runtime_mutation_counts:
            sections.extend(['', '## Runtime Mutations'])
            sections.extend(
                f'- {name}={count}'
                for name, count in sorted(self.runtime_mutation_counts.items())
            )
        if self.runtime_group_status_counts or self.runtime_child_stop_reason_counts:
            sections.extend(['', '## Runtime Orchestration'])
            if self.runtime_group_status_counts:
                sections.extend(
                    f'- group_status:{name}={count}'
                    for name, count in sorted(self.runtime_group_status_counts.items())
                )
            if self.runtime_child_stop_reason_counts:
                sections.extend(
                    f'- child_stop:{name}={count}'
                    for name, count in sorted(self.runtime_child_stop_reason_counts.items())
                )
            if self.runtime_resumed_children:
                sections.append(f'- resumed_children={self.runtime_resumed_children}')
        if self.runtime_context_reduction:
            sections.extend(['', '## Runtime Context Reduction'])
            sections.extend(
                f'- {name}={count}'
                for name, count in sorted(self.runtime_context_reduction.items())
            )
        if self.runtime_lineage_stats:
            sections.extend(['', '## Runtime Lineage'])
            sections.extend(
                f'- {name}={count}'
                for name, count in sorted(self.runtime_lineage_stats.items())
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
        runtime_cumulative_usage: UsageSummary | None = None,
    ) -> None:
        self.mutable_messages.append(prompt)
        self.transcript_store.append(prompt, kind='prompt')
        self.transcript_store.append(turn.output, kind='output')
        if self.config.use_runtime_agent:
            self._record_runtime_turn(turn)
        self.permission_denials.extend(denied_tools)
        if runtime_cumulative_usage is not None:
            self.runtime_cumulative_usage = runtime_cumulative_usage
            self.total_usage = runtime_cumulative_usage
        else:
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
            if event_type == 'delegate_group_result':
                group_status = event.get('group_status')
                if isinstance(group_status, str) and group_status:
                    self.runtime_group_status_counts[group_status] = (
                        self.runtime_group_status_counts.get(group_status, 0) + 1
                    )
            elif event_type == 'delegate_subtask_result':
                stop_reason = event.get('stop_reason')
                if isinstance(stop_reason, str) and stop_reason:
                    self.runtime_child_stop_reason_counts[stop_reason] = (
                        self.runtime_child_stop_reason_counts.get(stop_reason, 0) + 1
                    )
                if bool(event.get('resume_used')):
                    self.runtime_resumed_children += 1
        kind_counts: dict[str, int] = {}
        mutation_counts: dict[str, int] = {}
        for entry in turn.transcript:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get('metadata')
            if not isinstance(metadata, dict):
                continue
            kind = metadata.get('kind')
            if isinstance(kind, str) and kind:
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
                self.runtime_message_kind_counts[kind] = (
                    self.runtime_message_kind_counts.get(kind, 0) + 1
                )
            mutation_totals = metadata.get('mutation_totals')
            if isinstance(mutation_totals, dict):
                for mutation_kind, count in mutation_totals.items():
                    if (
                        not isinstance(mutation_kind, str)
                        or not mutation_kind
                        or isinstance(count, bool)
                        or not isinstance(count, int)
                        or count <= 0
                    ):
                        continue
                    mutation_counts[mutation_kind] = mutation_counts.get(mutation_kind, 0) + count
                    self.runtime_mutation_counts[mutation_kind] = (
                        self.runtime_mutation_counts.get(mutation_kind, 0) + count
                    )
            else:
                mutations = metadata.get('mutations')
                if isinstance(mutations, list):
                    for mutation in mutations:
                        if not isinstance(mutation, dict):
                            continue
                        mutation_kind = mutation.get('kind')
                        if not isinstance(mutation_kind, str) or not mutation_kind:
                            continue
                        mutation_counts[mutation_kind] = mutation_counts.get(mutation_kind, 0) + 1
                        self.runtime_mutation_counts[mutation_kind] = (
                            self.runtime_mutation_counts.get(mutation_kind, 0) + 1
                        )
            self._record_context_reduction(metadata)
            self._record_lineage(metadata)
        summary = self._summarize_runtime_turn(
            event_counts,
            kind_counts,
            mutation_counts,
            len(turn.transcript),
        )
        if summary:
            self.transcript_store.append(
                summary,
                kind='runtime_summary',
                metadata={
                    'runtime_event_counts': dict(event_counts),
                    'runtime_kind_counts': dict(kind_counts),
                    'runtime_mutation_counts': dict(mutation_counts),
                    'runtime_transcript_size': len(turn.transcript),
                },
            )
        if event_counts:
            self.transcript_store.append(
                json.dumps(event_counts, sort_keys=True),
                kind='runtime_events',
                metadata={'counts': dict(event_counts)},
            )
        if kind_counts:
            self.transcript_store.append(
                json.dumps(kind_counts, sort_keys=True),
                kind='runtime_message_kinds',
                metadata={'counts': dict(kind_counts)},
            )

    def _summarize_runtime_turn(
        self,
        event_counts: dict[str, int],
        kind_counts: dict[str, int],
        mutation_counts: dict[str, int],
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
        if mutation_counts:
            parts.append(
                'mutations='
                + ', '.join(
                    f'{name}:{count}'
                    for name, count in sorted(mutation_counts.items())
                )
            )
        return '[runtime] ' + ' | '.join(parts)

    def _runtime_summary_event(self) -> dict[str, object]:
        return {
            'type': 'runtime_summary',
            'runtime_event_counts': dict(self.runtime_event_counts),
            'runtime_message_kind_counts': dict(self.runtime_message_kind_counts),
            'runtime_mutation_counts': dict(self.runtime_mutation_counts),
            'runtime_group_status_counts': dict(self.runtime_group_status_counts),
            'runtime_child_stop_reason_counts': dict(self.runtime_child_stop_reason_counts),
            'runtime_resumed_children': self.runtime_resumed_children,
            'runtime_context_reduction': dict(self.runtime_context_reduction),
            'runtime_lineage_stats': dict(self.runtime_lineage_stats),
            'runtime_transcript_size': self.runtime_transcript_size,
            'transcript_store_entries': len(self.transcript_store.entries),
            'transcript_store_compactions': self.transcript_store.compaction_count,
        }

    def _record_context_reduction(self, metadata: dict[str, object]) -> None:
        kind = metadata.get('kind')
        if kind == 'compact_boundary':
            self.runtime_context_reduction['compact_boundaries'] = (
                self.runtime_context_reduction.get('compact_boundaries', 0) + 1
            )
            depth = metadata.get('compaction_depth')
            if isinstance(depth, int) and not isinstance(depth, bool):
                current = self.runtime_context_reduction.get('max_compaction_depth', 0)
                self.runtime_context_reduction['max_compaction_depth'] = max(current, depth)
            nested = metadata.get('nested_compaction_count')
            if isinstance(nested, int) and not isinstance(nested, bool):
                self.runtime_context_reduction['nested_compaction_count'] = (
                    self.runtime_context_reduction.get('nested_compaction_count', 0) + nested
                )
            preserved_tail_count = metadata.get('preserved_tail_count')
            if isinstance(preserved_tail_count, int) and not isinstance(preserved_tail_count, bool):
                self.runtime_context_reduction['preserved_tail_messages'] = (
                    self.runtime_context_reduction.get('preserved_tail_messages', 0)
                    + preserved_tail_count
                )
            max_source_mutation_serial = metadata.get('max_source_mutation_serial')
            if (
                isinstance(max_source_mutation_serial, int)
                and not isinstance(max_source_mutation_serial, bool)
            ):
                current = self.runtime_context_reduction.get('max_source_mutation_serial', 0)
                self.runtime_context_reduction['max_source_mutation_serial'] = max(
                    current,
                    max_source_mutation_serial,
                )
            compacted_lineage_ids = metadata.get('compacted_lineage_ids')
            if isinstance(compacted_lineage_ids, list):
                self.runtime_context_reduction['compacted_lineages'] = (
                    self.runtime_context_reduction.get('compacted_lineages', 0)
                    + len(
                        [
                            lineage_id for lineage_id in compacted_lineage_ids
                            if isinstance(lineage_id, str) and lineage_id
                        ]
                    )
                )
        elif kind == 'snipped_message':
            self.runtime_context_reduction['snipped_messages'] = (
                self.runtime_context_reduction.get('snipped_messages', 0) + 1
            )
            revision = metadata.get('snipped_from_revision')
            if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
                self.runtime_context_reduction['snipped_revised_messages'] = (
                    self.runtime_context_reduction.get('snipped_revised_messages', 0) + 1
                )

    def _record_lineage(self, metadata: dict[str, object]) -> None:
        lineage_id = metadata.get('lineage_id')
        if isinstance(lineage_id, str) and lineage_id:
            self._runtime_seen_lineages.add(lineage_id)
            revision = metadata.get('revision')
            if isinstance(revision, int) and not isinstance(revision, bool):
                current = self.runtime_lineage_stats.get('max_revision', 0)
                self.runtime_lineage_stats['max_revision'] = max(current, revision)
                if revision > 0:
                    self._runtime_revised_lineages.add(lineage_id)
            revision_count = metadata.get('revision_count')
            if isinstance(revision_count, int) and not isinstance(revision_count, bool):
                current = self.runtime_lineage_stats.get('max_revision_count', 0)
                self.runtime_lineage_stats['max_revision_count'] = max(current, revision_count)
            max_mutation_serial = metadata.get('max_mutation_serial')
            if (
                isinstance(max_mutation_serial, int)
                and not isinstance(max_mutation_serial, bool)
            ):
                current = self.runtime_lineage_stats.get('max_mutation_serial', 0)
                self.runtime_lineage_stats['max_mutation_serial'] = max(
                    current,
                    max_mutation_serial,
                )

        kind = metadata.get('kind')
        if kind == 'snipped_message':
            source_lineage = metadata.get('snipped_from_lineage_id')
            if isinstance(source_lineage, str) and source_lineage:
                self._runtime_tombstoned_lineages.add(source_lineage)
        elif kind == 'compact_boundary':
            compacted_lineage_ids = metadata.get('compacted_lineage_ids')
            if isinstance(compacted_lineage_ids, list):
                for source_lineage in compacted_lineage_ids:
                    if isinstance(source_lineage, str) and source_lineage:
                        self._runtime_compacted_lineages.add(source_lineage)
            max_source_revision = metadata.get('max_source_revision')
            if (
                isinstance(max_source_revision, int)
                and not isinstance(max_source_revision, bool)
            ):
                current = self.runtime_lineage_stats.get('max_source_revision', 0)
                self.runtime_lineage_stats['max_source_revision'] = max(
                    current,
                    max_source_revision,
                )

        self.runtime_lineage_stats['seen_lineages'] = len(self._runtime_seen_lineages)
        self.runtime_lineage_stats['revised_lineages'] = len(self._runtime_revised_lineages)
        self.runtime_lineage_stats['tombstoned_lineages'] = len(
            self._runtime_tombstoned_lineages
        )
        self.runtime_lineage_stats['compacted_lineages'] = len(
            self._runtime_compacted_lineages
        )
