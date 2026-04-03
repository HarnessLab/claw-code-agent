from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .task import PortingTask, VALID_TASK_STATUSES


DEFAULT_TASK_RUNTIME_PATH = Path('.port_sessions') / 'task_runtime.json'


@dataclass(frozen=True)
class TaskMutation:
    task: PortingTask | None
    store_path: str
    before_sha256: str | None
    after_sha256: str
    before_preview: str | None
    after_preview: str
    before_count: int
    after_count: int


@dataclass
class TaskRuntime:
    tasks: tuple[PortingTask, ...] = field(default_factory=tuple)
    storage_path: Path = field(default_factory=lambda: DEFAULT_TASK_RUNTIME_PATH.resolve())

    @classmethod
    def from_workspace(cls, cwd: Path) -> 'TaskRuntime':
        storage_path = (cwd.resolve() / DEFAULT_TASK_RUNTIME_PATH).resolve()
        if not storage_path.exists():
            return cls(tasks=(), storage_path=storage_path)
        try:
            payload = json.loads(storage_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return cls(tasks=(), storage_path=storage_path)
        raw_tasks = payload.get('tasks')
        if not isinstance(raw_tasks, list):
            return cls(tasks=(), storage_path=storage_path)
        tasks: list[PortingTask] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            task = PortingTask.from_dict(item)
            if not task.task_id or not task.title:
                continue
            tasks.append(task)
        return cls(tasks=tuple(tasks), storage_path=storage_path)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> tuple[PortingTask, ...]:
        tasks = self.tasks
        if status:
            normalized = _normalize_status(status)
            tasks = tuple(task for task in tasks if task.status == normalized)
        if limit is not None and limit >= 0:
            tasks = tasks[:limit]
        return tasks

    def get_task(self, task_id: str) -> PortingTask | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def create_task(
        self,
        *,
        title: str,
        description: str | None = None,
        status: str = 'todo',
        priority: str | None = None,
        task_id: str | None = None,
    ) -> TaskMutation:
        task = PortingTask(
            task_id=task_id or f'task_{uuid4().hex[:10]}',
            title=title.strip(),
            description=description.strip() if isinstance(description, str) and description.strip() else None,
            status=_normalize_status(status),
            priority=priority.strip() if isinstance(priority, str) and priority.strip() else None,
        )
        return self._persist((*self.tasks, task), task=task)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> TaskMutation:
        existing = self.get_task(task_id)
        if existing is None:
            raise KeyError(task_id)
        updated = replace(
            existing,
            title=title.strip() if isinstance(title, str) and title.strip() else existing.title,
            description=(
                description.strip()
                if isinstance(description, str) and description.strip()
                else None if description == ''
                else existing.description
            ),
            status=_normalize_status(status) if status is not None else existing.status,
            priority=(
                priority.strip()
                if isinstance(priority, str) and priority.strip()
                else None if priority == ''
                else existing.priority
            ),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        tasks = tuple(updated if task.task_id == task_id else task for task in self.tasks)
        return self._persist(tasks, task=updated)

    def replace_tasks(self, items: list[dict[str, Any]]) -> TaskMutation:
        tasks: list[PortingTask] = []
        now = datetime.now(timezone.utc).isoformat()
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = item.get('title')
            if not isinstance(title, str) or not title.strip():
                continue
            task_id = item.get('task_id')
            if not isinstance(task_id, str) or not task_id.strip():
                task_id = item.get('id')
            tasks.append(
                PortingTask(
                    task_id=(
                        task_id.strip()
                        if isinstance(task_id, str) and task_id.strip()
                        else f'task_{index}_{uuid4().hex[:6]}'
                    ),
                    title=title.strip(),
                    description=(
                        item.get('description').strip()
                        if isinstance(item.get('description'), str)
                        and item.get('description').strip()
                        else None
                    ),
                    status=_normalize_status(item.get('status')),
                    priority=(
                        item.get('priority').strip()
                        if isinstance(item.get('priority'), str)
                        and item.get('priority').strip()
                        else None
                    ),
                    created_at=(
                        str(item.get('created_at'))
                        if isinstance(item.get('created_at'), str)
                        else now
                    ),
                    updated_at=now,
                )
            )
        mutation = self._persist(tuple(tasks), task=None)
        return mutation

    def render_summary(self) -> str:
        lines = [
            f'Local task runtime file: {self.storage_path}',
            f'Total tasks: {len(self.tasks)}',
        ]
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        if counts:
            lines.append(
                '- Status counts: '
                + ', '.join(f'{name}={count}' for name, count in sorted(counts.items()))
            )
        if self.tasks:
            preview = ', '.join(task.title for task in self.tasks[:4])
            if len(self.tasks) > 4:
                preview += f', ... (+{len(self.tasks) - 4} more)'
            lines.append(f'- Task preview: {preview}')
        return '\n'.join(lines)

    def render_tasks(self, *, status: str | None = None, limit: int = 50) -> str:
        tasks = self.list_tasks(status=status, limit=limit)
        if not tasks:
            return '# Tasks\n\nNo tasks are currently stored.'
        lines = ['# Tasks', '']
        for task in tasks:
            details = [task.task_id, f'status={task.status}']
            if task.priority:
                details.append(f'priority={task.priority}')
            details.append(f'title={task.title}')
            lines.append('- ' + '; '.join(details))
            if task.description:
                lines.append(f'  description: {task.description}')
        return '\n'.join(lines)

    def render_task(self, task_id: str) -> str:
        task = self.get_task(task_id)
        if task is None:
            return f'# Task\n\nUnknown task id: {task_id}'
        lines = [
            '# Task',
            '',
            f'- ID: {task.task_id}',
            f'- Status: {task.status}',
            f'- Title: {task.title}',
        ]
        if task.priority:
            lines.append(f'- Priority: {task.priority}')
        if task.description:
            lines.append(f'- Description: {task.description}')
        lines.append(f'- Updated: {task.updated_at}')
        return '\n'.join(lines)

    def _persist(
        self,
        tasks: tuple[PortingTask, ...],
        *,
        task: PortingTask | None,
    ) -> TaskMutation:
        before_text = self._serialize_payload(self.tasks)
        before_preview = _snapshot_text(before_text)
        before_sha256 = (
            hashlib.sha256(before_text.encode('utf-8')).hexdigest()
            if self.storage_path.exists() or self.tasks
            else None
        )
        payload_text = self._serialize_payload(tasks)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(payload_text, encoding='utf-8')
        self.tasks = tasks
        after_sha256 = hashlib.sha256(payload_text.encode('utf-8')).hexdigest()
        return TaskMutation(
            task=task,
            store_path=str(self.storage_path),
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            before_preview=before_preview if before_text.strip() else None,
            after_preview=_snapshot_text(payload_text),
            before_count=len(json.loads(before_text).get('tasks', [])) if before_text.strip() else 0,
            after_count=len(tasks),
        )

    def _serialize_payload(self, tasks: tuple[PortingTask, ...]) -> str:
        payload = {
            'tasks': [task.to_dict() for task in tasks],
        }
        return json.dumps(payload, ensure_ascii=True, indent=2)


def _snapshot_text(text: str, limit: int = 240) -> str:
    normalized = ' '.join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + '...'


def _normalize_status(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower().replace('-', '_').replace(' ', '_')
        aliases = {
            'complete': 'done',
            'completed': 'done',
            'open': 'todo',
        }
        lowered = aliases.get(lowered, lowered)
        if lowered in VALID_TASK_STATUSES:
            return lowered
    return 'todo'
