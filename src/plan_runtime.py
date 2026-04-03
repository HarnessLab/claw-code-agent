from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task_runtime import TaskRuntime


DEFAULT_PLAN_RUNTIME_PATH = Path('.port_sessions') / 'plan_runtime.json'
VALID_PLAN_STATUSES = ('pending', 'in_progress', 'completed')


@dataclass(frozen=True)
class PlanStep:
    step: str
    status: str = 'pending'
    task_id: str | None = None
    description: str | None = None
    priority: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'step': self.step,
            'status': self.status,
            'task_id': self.task_id,
            'description': self.description,
            'priority': self.priority,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> 'PlanStep':
        return cls(
            step=str(payload.get('step') or ''),
            status=_normalize_plan_status(payload.get('status')),
            task_id=(
                str(payload.get('task_id'))
                if isinstance(payload.get('task_id'), str) and payload.get('task_id')
                else None
            ),
            description=(
                str(payload.get('description'))
                if isinstance(payload.get('description'), str)
                and payload.get('description').strip()
                else None
            ),
            priority=(
                str(payload.get('priority'))
                if isinstance(payload.get('priority'), str)
                and payload.get('priority').strip()
                else None
            ),
        )


@dataclass(frozen=True)
class PlanMutation:
    explanation: str | None
    store_path: str
    before_sha256: str | None
    after_sha256: str
    before_preview: str | None
    after_preview: str
    before_count: int
    after_count: int
    synced_tasks: int = 0
    synced_task_store_path: str | None = None
    synced_task_sha256: str | None = None


@dataclass
class PlanRuntime:
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)
    explanation: str | None = None
    updated_at: str | None = None
    storage_path: Path = field(default_factory=lambda: DEFAULT_PLAN_RUNTIME_PATH.resolve())

    @classmethod
    def from_workspace(cls, cwd: Path) -> 'PlanRuntime':
        storage_path = (cwd.resolve() / DEFAULT_PLAN_RUNTIME_PATH).resolve()
        if not storage_path.exists():
            return cls(storage_path=storage_path)
        try:
            payload = json.loads(storage_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return cls(storage_path=storage_path)
        raw_steps = payload.get('steps')
        if not isinstance(raw_steps, list):
            return cls(storage_path=storage_path)
        steps: list[PlanStep] = []
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            step = PlanStep.from_dict(item)
            if step.step:
                steps.append(step)
        explanation = payload.get('explanation')
        updated_at = payload.get('updated_at')
        return cls(
            steps=tuple(steps),
            explanation=(
                explanation.strip()
                if isinstance(explanation, str) and explanation.strip()
                else None
            ),
            updated_at=str(updated_at) if isinstance(updated_at, str) and updated_at else None,
            storage_path=storage_path,
        )

    def update_plan(
        self,
        items: list[dict[str, Any]],
        *,
        explanation: str | None = None,
        task_runtime: TaskRuntime | None = None,
        sync_tasks: bool = True,
    ) -> PlanMutation:
        normalized_steps: list[PlanStep] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            step_text = item.get('step')
            if not isinstance(step_text, str) or not step_text.strip():
                continue
            task_id = item.get('task_id')
            normalized_steps.append(
                PlanStep(
                    step=step_text.strip(),
                    status=_normalize_plan_status(item.get('status')),
                    task_id=(
                        task_id.strip()
                        if isinstance(task_id, str) and task_id.strip()
                        else f'plan_{index}'
                    ),
                    description=(
                        item.get('description').strip()
                        if isinstance(item.get('description'), str)
                        and item.get('description').strip()
                        else None
                    ),
                    priority=(
                        item.get('priority').strip()
                        if isinstance(item.get('priority'), str)
                        and item.get('priority').strip()
                        else None
                    ),
                )
            )
        mutation = self._persist(
            tuple(normalized_steps),
            explanation=(
                explanation.strip()
                if isinstance(explanation, str) and explanation.strip()
                else None
            ),
        )
        if sync_tasks and task_runtime is not None:
            task_items = [
                {
                    'task_id': step.task_id or f'plan_{index}',
                    'title': step.step,
                    'description': step.description,
                    'status': _plan_status_to_task_status(step.status),
                    'priority': step.priority,
                }
                for index, step in enumerate(self.steps, start=1)
            ]
            task_mutation = task_runtime.replace_tasks(task_items)
            return PlanMutation(
                explanation=mutation.explanation,
                store_path=mutation.store_path,
                before_sha256=mutation.before_sha256,
                after_sha256=mutation.after_sha256,
                before_preview=mutation.before_preview,
                after_preview=mutation.after_preview,
                before_count=mutation.before_count,
                after_count=mutation.after_count,
                synced_tasks=task_mutation.after_count,
                synced_task_store_path=task_mutation.store_path,
                synced_task_sha256=task_mutation.after_sha256,
            )
        return mutation

    def clear_plan(self, *, task_runtime: TaskRuntime | None = None) -> PlanMutation:
        mutation = self._persist((), explanation=None)
        if task_runtime is not None:
            task_mutation = task_runtime.replace_tasks([])
            return PlanMutation(
                explanation=None,
                store_path=mutation.store_path,
                before_sha256=mutation.before_sha256,
                after_sha256=mutation.after_sha256,
                before_preview=mutation.before_preview,
                after_preview=mutation.after_preview,
                before_count=mutation.before_count,
                after_count=mutation.after_count,
                synced_tasks=task_mutation.after_count,
                synced_task_store_path=task_mutation.store_path,
                synced_task_sha256=task_mutation.after_sha256,
            )
        return mutation

    def render_summary(self) -> str:
        lines = [
            f'Local plan runtime file: {self.storage_path}',
            f'Total plan steps: {len(self.steps)}',
        ]
        if self.explanation:
            lines.append(f'- Explanation: {self.explanation}')
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.status] = counts.get(step.status, 0) + 1
        if counts:
            lines.append(
                '- Status counts: '
                + ', '.join(f'{name}={count}' for name, count in sorted(counts.items()))
            )
        if self.updated_at:
            lines.append(f'- Updated: {self.updated_at}')
        return '\n'.join(lines)

    def render_plan(self) -> str:
        if not self.steps:
            return '# Plan\n\nNo stored plan is currently available.'
        lines = ['# Plan', '']
        if self.explanation:
            lines.extend(['## Explanation', self.explanation, ''])
        lines.append('## Steps')
        for index, step in enumerate(self.steps, start=1):
            details = [f'{index}. {step.step}', f'status={step.status}']
            if step.task_id:
                details.append(f'task_id={step.task_id}')
            if step.priority:
                details.append(f'priority={step.priority}')
            lines.append('- ' + '; '.join(details))
            if step.description:
                lines.append(f'  description: {step.description}')
        return '\n'.join(lines)

    def _persist(
        self,
        steps: tuple[PlanStep, ...],
        *,
        explanation: str | None,
    ) -> PlanMutation:
        before_count = len(self.steps)
        before_text = self._serialize_payload(self.steps, self.explanation, self.updated_at)
        before_preview = _snapshot_text(before_text)
        before_sha256 = (
            hashlib.sha256(before_text.encode('utf-8')).hexdigest()
            if self.storage_path.exists() or self.steps or self.explanation
            else None
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        payload_text = self._serialize_payload(steps, explanation, updated_at)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(payload_text, encoding='utf-8')
        self.steps = steps
        self.explanation = explanation
        self.updated_at = updated_at
        after_sha256 = hashlib.sha256(payload_text.encode('utf-8')).hexdigest()
        return PlanMutation(
            explanation=explanation,
            store_path=str(self.storage_path),
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            before_preview=before_preview if before_text.strip() else None,
            after_preview=_snapshot_text(payload_text),
            before_count=before_count if before_text.strip() else 0,
            after_count=len(steps),
        )

    def _serialize_payload(
        self,
        steps: tuple[PlanStep, ...],
        explanation: str | None,
        updated_at: str | None,
    ) -> str:
        payload = {
            'explanation': explanation,
            'updated_at': updated_at,
            'steps': [step.to_dict() for step in steps],
        }
        return json.dumps(payload, ensure_ascii=True, indent=2)


def _normalize_plan_status(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        aliases = {
            'todo': 'pending',
            'open': 'pending',
            'done': 'completed',
            'complete': 'completed',
            'in-progress': 'in_progress',
            'in progress': 'in_progress',
        }
        lowered = aliases.get(lowered, lowered)
        if lowered in VALID_PLAN_STATUSES:
            return lowered
    return 'pending'


def _plan_status_to_task_status(status: str) -> str:
    if status == 'completed':
        return 'done'
    if status == 'in_progress':
        return 'in_progress'
    return 'todo'


def _snapshot_text(text: str, limit: int = 240) -> str:
    normalized = ' '.join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + '...'
