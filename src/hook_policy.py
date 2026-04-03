from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HookPolicyManifest:
    path: str
    trusted: bool | None = None
    managed_settings: dict[str, Any] = field(default_factory=dict)
    safe_env_names: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    deny_tool_prefixes: tuple[str, ...] = ()
    before_prompt: tuple[str, ...] = ()
    after_turn: tuple[str, ...] = ()
    before_tool: dict[str, tuple[str, ...]] = field(default_factory=dict)
    after_tool: dict[str, tuple[str, ...]] = field(default_factory=dict)
    budget_overrides: dict[str, int | float] = field(default_factory=dict)


@dataclass
class HookPolicyRuntime:
    manifests: tuple[HookPolicyManifest, ...] = field(default_factory=tuple)

    @classmethod
    def from_workspace(
        cls,
        cwd: Path,
        additional_working_directories: tuple[str, ...] = (),
    ) -> 'HookPolicyRuntime':
        manifests: list[HookPolicyManifest] = []
        for path in _discover_policy_paths(cwd, additional_working_directories):
            manifest = _load_policy_manifest(path)
            if manifest is not None:
                manifests.append(manifest)
        return cls(manifests=tuple(manifests))

    def is_trusted(self) -> bool:
        explicit = [
            manifest.trusted
            for manifest in self.manifests
            if manifest.trusted is not None
        ]
        if not explicit:
            return True
        return explicit[-1]

    def managed_settings(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for manifest in self.manifests:
            merged.update(manifest.managed_settings)
        return merged

    def safe_env(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for manifest in self.manifests:
            for name in manifest.safe_env_names:
                value = os.environ.get(name)
                if value is not None:
                    values[name] = value
        return values

    def budget_overrides(self) -> dict[str, int | float]:
        merged: dict[str, int | float] = {}
        for manifest in self.manifests:
            merged.update(manifest.budget_overrides)
        return merged

    def before_prompt_messages(self) -> tuple[str, ...]:
        return tuple(
            message
            for manifest in self.manifests
            for message in manifest.before_prompt
        )

    def after_turn_messages(self) -> tuple[str, ...]:
        return tuple(
            message
            for manifest in self.manifests
            for message in manifest.after_turn
        )

    def before_tool_messages(self, tool_name: str) -> tuple[str, ...]:
        return self._tool_messages(tool_name, attr='before_tool')

    def after_tool_messages(self, tool_name: str) -> tuple[str, ...]:
        return self._tool_messages(tool_name, attr='after_tool')

    def denied_tool_message(self, tool_name: str) -> str | None:
        lowered = tool_name.lower()
        for manifest in self.manifests:
            if lowered in manifest.deny_tools:
                return (
                    f'Workspace hook policy blocked tool {tool_name}. '
                    f'Policy file: {manifest.path}'
                )
            if any(lowered.startswith(prefix) for prefix in manifest.deny_tool_prefixes):
                return (
                    f'Workspace hook policy blocked tool prefix match for {tool_name}. '
                    f'Policy file: {manifest.path}'
                )
        return None

    def render_summary(self) -> str:
        if not self.manifests:
            return 'No local hook or policy manifests discovered.'
        lines = [f'Local hook/policy manifests: {len(self.manifests)}']
        lines.append(f'- trusted={self.is_trusted()}')
        settings = self.managed_settings()
        if settings:
            lines.append(
                '- managed_settings='
                + ', '.join(f'{key}={value}' for key, value in sorted(settings.items()))
            )
        env_values = self.safe_env()
        if env_values:
            lines.append(
                '- safe_env='
                + ', '.join(f'{key}={value}' for key, value in sorted(env_values.items()))
            )
        for manifest in self.manifests:
            details = [Path(manifest.path).name]
            if manifest.trusted is not None:
                details.append(f'trusted={manifest.trusted}')
            if manifest.deny_tools:
                details.append(f'deny_tools={len(manifest.deny_tools)}')
            if manifest.deny_tool_prefixes:
                details.append(f'deny_prefixes={len(manifest.deny_tool_prefixes)}')
            if manifest.before_prompt:
                details.append(f'before_prompt={len(manifest.before_prompt)}')
            if manifest.after_turn:
                details.append(f'after_turn={len(manifest.after_turn)}')
            if manifest.before_tool:
                details.append(f'before_tool={len(manifest.before_tool)}')
            if manifest.after_tool:
                details.append(f'after_tool={len(manifest.after_tool)}')
            if manifest.budget_overrides:
                details.append(f'budget_overrides={len(manifest.budget_overrides)}')
            lines.append(f"- {'; '.join(details)}")
        return '\n'.join(lines)

    def _tool_messages(self, tool_name: str, *, attr: str) -> tuple[str, ...]:
        lowered = tool_name.lower()
        messages: list[str] = []
        for manifest in self.manifests:
            mapping = getattr(manifest, attr)
            if not isinstance(mapping, dict):
                continue
            wildcard = mapping.get('*', ())
            exact = mapping.get(lowered, ())
            messages.extend(message for message in wildcard if message)
            messages.extend(message for message in exact if message)
        return tuple(messages)


def _discover_policy_paths(
    cwd: Path,
    additional_working_directories: tuple[str, ...],
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def remember(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(resolved)
        candidates.append(resolved)

    roots: list[Path] = []
    current = cwd.resolve()
    while True:
        roots.append(current)
        if current.parent == current:
            break
        current = current.parent
    roots.extend(Path(path).resolve() for path in additional_working_directories)

    for root in roots:
        remember(root / '.claw-policy.json')
        remember(root / '.codex-policy.json')
        remember(root / '.claw-hooks.json')
    return tuple(candidates)


def _load_policy_manifest(path: Path) -> HookPolicyManifest | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    hooks = payload.get('hooks')
    if not isinstance(hooks, dict):
        hooks = {}
    return HookPolicyManifest(
        path=str(path),
        trusted=(
            payload.get('trusted')
            if isinstance(payload.get('trusted'), bool)
            else None
        ),
        managed_settings=(
            dict(payload.get('managedSettings'))
            if isinstance(payload.get('managedSettings'), dict)
            else dict(payload.get('managed_settings', {}))
            if isinstance(payload.get('managed_settings'), dict)
            else {}
        ),
        safe_env_names=_extract_string_tuple(
            payload.get('safeEnv')
            if payload.get('safeEnv') is not None
            else payload.get('safe_env')
        ),
        deny_tools=tuple(
            item.lower()
            for item in _extract_string_tuple(
                payload.get('denyTools')
                if payload.get('denyTools') is not None
                else payload.get('deny_tools')
            )
        ),
        deny_tool_prefixes=tuple(
            item.lower()
            for item in _extract_string_tuple(
                payload.get('denyToolPrefixes')
                if payload.get('denyToolPrefixes') is not None
                else payload.get('deny_tool_prefixes')
            )
        ),
        before_prompt=_extract_hook_messages(
            hooks.get('beforePrompt')
            if hooks.get('beforePrompt') is not None
            else hooks.get('before_prompt')
        ),
        after_turn=_extract_hook_messages(
            hooks.get('afterTurn')
            if hooks.get('afterTurn') is not None
            else hooks.get('after_turn')
        ),
        before_tool=_extract_tool_hook_messages(
            hooks.get('beforeTool')
            if hooks.get('beforeTool') is not None
            else hooks.get('before_tool')
        ),
        after_tool=_extract_tool_hook_messages(
            hooks.get('afterTool')
            if hooks.get('afterTool') is not None
            else hooks.get('after_tool')
        ),
        budget_overrides=_extract_budget_overrides(payload.get('budget')),
    )


def _extract_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )


def _extract_hook_messages(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, list):
        return tuple(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    return ()


def _extract_tool_hook_messages(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    extracted: dict[str, tuple[str, ...]] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            continue
        messages = _extract_hook_messages(raw)
        if messages:
            extracted[key.strip().lower()] = messages
    return extracted


def _extract_budget_overrides(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        'max_total_tokens',
        'max_input_tokens',
        'max_output_tokens',
        'max_reasoning_tokens',
        'max_total_cost_usd',
        'max_tool_calls',
        'max_delegated_tasks',
        'max_model_calls',
        'max_session_turns',
        'maxTotalTokens',
        'maxInputTokens',
        'maxOutputTokens',
        'maxReasoningTokens',
        'maxTotalCostUsd',
        'maxToolCalls',
        'maxDelegatedTasks',
        'maxModelCalls',
        'maxSessionTurns',
    }
    normalized: dict[str, int | float] = {}
    key_map = {
        'maxTotalTokens': 'max_total_tokens',
        'maxInputTokens': 'max_input_tokens',
        'maxOutputTokens': 'max_output_tokens',
        'maxReasoningTokens': 'max_reasoning_tokens',
        'maxTotalCostUsd': 'max_total_cost_usd',
        'maxToolCalls': 'max_tool_calls',
        'maxDelegatedTasks': 'max_delegated_tasks',
        'maxModelCalls': 'max_model_calls',
        'maxSessionTurns': 'max_session_turns',
    }
    for key, raw in value.items():
        if key not in allowed_keys:
            continue
        normalized_key = key_map.get(key, key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            normalized[normalized_key] = raw
        elif isinstance(raw, float):
            normalized[normalized_key] = raw
    return normalized
