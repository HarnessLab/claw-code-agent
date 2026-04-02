from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManagedAgentRecord:
    agent_id: str
    prompt: str
    parent_agent_id: str | None = None
    group_id: str | None = None
    child_index: int | None = None
    label: str | None = None
    session_id: str | None = None
    session_path: str | None = None
    status: str = 'running'
    turns: int = 0
    tool_calls: int = 0
    stop_reason: str | None = None


@dataclass(frozen=True)
class ManagedAgentGroup:
    group_id: str
    label: str | None = None
    parent_agent_id: str | None = None
    child_agent_ids: tuple[str, ...] = ()
    status: str = 'running'
    completed_children: int = 0
    failed_children: int = 0


@dataclass
class AgentManager:
    records: dict[str, ManagedAgentRecord] = field(default_factory=dict)
    groups: dict[str, ManagedAgentGroup] = field(default_factory=dict)
    _counter: int = 0
    _group_counter: int = 0

    def start_agent(
        self,
        *,
        prompt: str,
        parent_agent_id: str | None = None,
        group_id: str | None = None,
        child_index: int | None = None,
        label: str | None = None,
    ) -> str:
        self._counter += 1
        agent_id = f'agent_{self._counter}'
        self.records[agent_id] = ManagedAgentRecord(
            agent_id=agent_id,
            prompt=prompt,
            parent_agent_id=parent_agent_id,
            group_id=group_id,
            child_index=child_index,
            label=label,
        )
        if group_id is not None:
            self.register_group_child(group_id, agent_id, child_index=child_index)
        return agent_id

    def start_group(
        self,
        *,
        label: str | None = None,
        parent_agent_id: str | None = None,
    ) -> str:
        self._group_counter += 1
        group_id = f'group_{self._group_counter}'
        self.groups[group_id] = ManagedAgentGroup(
            group_id=group_id,
            label=label,
            parent_agent_id=parent_agent_id,
        )
        return group_id

    def register_group_child(
        self,
        group_id: str,
        agent_id: str,
        *,
        child_index: int | None = None,
    ) -> None:
        group = self.groups.get(group_id)
        if group is None:
            return
        if agent_id in group.child_agent_ids:
            updated_children = group.child_agent_ids
        else:
            updated_children = (*group.child_agent_ids, agent_id)
            self.groups[group_id] = ManagedAgentGroup(
                group_id=group.group_id,
                label=group.label,
                parent_agent_id=group.parent_agent_id,
                child_agent_ids=updated_children,
                status=group.status,
                completed_children=group.completed_children,
                failed_children=group.failed_children,
            )
        record = self.records.get(agent_id)
        if record is None:
            return
        if record.group_id == group_id and record.child_index == child_index:
            return
        self.records[agent_id] = ManagedAgentRecord(
            agent_id=record.agent_id,
            prompt=record.prompt,
            parent_agent_id=record.parent_agent_id,
            group_id=group_id,
            child_index=child_index,
            label=record.label,
            session_id=record.session_id,
            session_path=record.session_path,
            status=record.status,
            turns=record.turns,
            tool_calls=record.tool_calls,
            stop_reason=record.stop_reason,
        )

    def finish_group(
        self,
        group_id: str,
        *,
        status: str,
        completed_children: int,
        failed_children: int,
    ) -> None:
        group = self.groups.get(group_id)
        if group is None:
            return
        self.groups[group_id] = ManagedAgentGroup(
            group_id=group.group_id,
            label=group.label,
            parent_agent_id=group.parent_agent_id,
            child_agent_ids=group.child_agent_ids,
            status=status,
            completed_children=completed_children,
            failed_children=failed_children,
        )

    def finish_agent(
        self,
        agent_id: str,
        *,
        session_id: str | None,
        session_path: str | None,
        turns: int,
        tool_calls: int,
        stop_reason: str | None,
    ) -> None:
        record = self.records.get(agent_id)
        if record is None:
            return
        self.records[agent_id] = ManagedAgentRecord(
            agent_id=record.agent_id,
            prompt=record.prompt,
            parent_agent_id=record.parent_agent_id,
            group_id=record.group_id,
            child_index=record.child_index,
            label=record.label,
            session_id=session_id,
            session_path=session_path,
            status='completed',
            turns=turns,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )

    def children_of(self, agent_id: str) -> tuple[ManagedAgentRecord, ...]:
        return tuple(
            record
            for record in self.records.values()
            if record.parent_agent_id == agent_id
        )

    def completed_records(self) -> tuple[ManagedAgentRecord, ...]:
        return tuple(
            record for record in self.records.values() if record.status == 'completed'
        )

    def summary_lines(self) -> list[str]:
        lines = [
            f'- Managed agents: {len(self.records)}',
            f'- Completed agents: {len(self.completed_records())}',
        ]
        child_count = sum(1 for record in self.records.values() if record.parent_agent_id)
        lines.append(f'- Child agents: {child_count}')
        lines.append(f'- Agent groups: {len(self.groups)}')
        completed_groups = sum(1 for group in self.groups.values() if group.status == 'completed')
        lines.append(f'- Completed groups: {completed_groups}')
        for record in sorted(self.records.values(), key=lambda item: item.agent_id)[:8]:
            label = record.label or record.agent_id
            group_bits: list[str] = []
            if record.group_id is not None:
                group_bits.append(f'group={record.group_id}')
            if record.child_index is not None:
                group_bits.append(f'child_index={record.child_index}')
            group_suffix = f" {' '.join(group_bits)}" if group_bits else ''
            lines.append(
                f'- {label}: status={record.status} turns={record.turns} '
                f'tool_calls={record.tool_calls} stop={record.stop_reason or "n/a"}{group_suffix}'
            )
        if len(self.records) > 8:
            lines.append(f'- ... plus {len(self.records) - 8} more managed agents')
        for group in sorted(self.groups.values(), key=lambda item: item.group_id)[:6]:
            label = group.label or group.group_id
            lines.append(
                f'- {label}: group_status={group.status} children={len(group.child_agent_ids)} '
                f'completed={group.completed_children} failed={group.failed_children}'
            )
        if len(self.groups) > 6:
            lines.append(f'- ... plus {len(self.groups) - 6} more agent groups')
        return lines
