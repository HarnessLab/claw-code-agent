from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


VALID_TASK_STATUSES = ('todo', 'in_progress', 'done', 'cancelled')


@dataclass(frozen=True)
class PortingTask:
    task_id: str
    title: str
    status: str = 'todo'
    description: str | None = None
    priority: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            'task_id': self.task_id,
            'title': self.title,
            'status': self.status,
            'description': self.description,
            'priority': self.priority,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> 'PortingTask':
        return cls(
            task_id=str(payload.get('task_id') or payload.get('id') or ''),
            title=str(payload.get('title') or ''),
            status=_normalize_task_status(payload.get('status')),
            description=(
                str(payload.get('description'))
                if isinstance(payload.get('description'), str)
                else None
            ),
            priority=(
                str(payload.get('priority'))
                if isinstance(payload.get('priority'), str)
                else None
            ),
            created_at=(
                str(payload.get('created_at'))
                if isinstance(payload.get('created_at'), str)
                else datetime.now(timezone.utc).isoformat()
            ),
            updated_at=(
                str(payload.get('updated_at'))
                if isinstance(payload.get('updated_at'), str)
                else datetime.now(timezone.utc).isoformat()
            ),
        )


def _normalize_task_status(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        aliases = {
            'in-progress': 'in_progress',
            'in progress': 'in_progress',
            'complete': 'done',
            'completed': 'done',
            'open': 'todo',
        }
        lowered = aliases.get(lowered, lowered)
        if lowered in VALID_TASK_STATUSES:
            return lowered
    return 'todo'
