from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PLUGIN_LINES = 12
MAX_PLUGIN_PREVIEW_CHARS = 4000


@dataclass(frozen=True)
class PluginCacheEntry:
    name: str
    enabled: bool = True
    version: str | None = None
    source: str | None = None


def load_plugin_cache_summary(
    cwd: Path,
    additional_working_directories: tuple[str, ...] = (),
) -> str | None:
    snapshot = discover_plugin_cache(cwd, additional_working_directories)
    if snapshot is None:
        return None
    return snapshot


def discover_plugin_cache(
    cwd: Path,
    additional_working_directories: tuple[str, ...] = (),
) -> str | None:
    for path in _discover_candidate_paths(cwd, additional_working_directories):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        entries = _extract_entries(payload)
        if not entries:
            continue
        lines = [
            f'Plugin cache loaded from: {path}',
            f'Plugin entries discovered: {len(entries)}',
        ]
        enabled = [entry for entry in entries if entry.enabled]
        disabled = [entry for entry in entries if not entry.enabled]
        lines.append(f'Enabled plugins: {len(enabled)}')
        if disabled:
            lines.append(f'Disabled plugins: {len(disabled)}')
        for entry in entries[:MAX_PLUGIN_LINES]:
            details = [entry.name]
            if entry.version:
                details.append(f'version={entry.version}')
            if entry.source:
                details.append(f'source={entry.source}')
            if not entry.enabled:
                details.append('disabled')
            lines.append(f"- {'; '.join(details)}")
        if len(entries) > MAX_PLUGIN_LINES:
            lines.append(f'- ... plus {len(entries) - MAX_PLUGIN_LINES} more plugin entries')
        rendered = '\n'.join(lines)
        if len(rendered) > MAX_PLUGIN_PREVIEW_CHARS:
            rendered = rendered[: MAX_PLUGIN_PREVIEW_CHARS - 3] + '...'
        return rendered
    return None


def _discover_candidate_paths(
    cwd: Path,
    additional_working_directories: tuple[str, ...],
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    relative_paths = (
        '.port_sessions/plugin_cache.json',
        '.port_sessions/plugins.json',
        '.claude/plugins/cache.json',
        '.claw/plugins/cache.json',
        'plugins/cache.json',
        '.plugins/cache.json',
    )

    def remember(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists() or not resolved.is_file():
            return
        seen.add(resolved)
        candidates.append(resolved)

    for root in _walk_upwards(cwd.resolve()):
        for relative in relative_paths:
            remember(root / relative)

    for raw_path in additional_working_directories:
        directory = Path(raw_path).resolve()
        for relative in relative_paths:
            remember(directory / relative)

    return candidates


def _walk_upwards(path: Path) -> list[Path]:
    current = path
    walked: list[Path] = []
    while True:
        walked.append(current)
        if current.parent == current:
            break
        current = current.parent
    return walked


def _extract_entries(payload: Any) -> list[PluginCacheEntry]:
    entries: list[PluginCacheEntry] = []
    raw_entries: list[Any] = []
    if isinstance(payload, list):
        raw_entries = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get('plugins'), list):
            raw_entries = payload['plugins']
        elif isinstance(payload.get('entries'), list):
            raw_entries = payload['entries']
        else:
            raw_entries = [
                {'name': key, **value}
                for key, value in payload.items()
                if isinstance(value, dict)
            ]

    for item in raw_entries:
        entry = _coerce_entry(item)
        if entry is not None:
            entries.append(entry)
    return entries


def _coerce_entry(item: Any) -> PluginCacheEntry | None:
    if isinstance(item, str) and item.strip():
        return PluginCacheEntry(name=item.strip())
    if not isinstance(item, dict):
        return None
    name = item.get('name') or item.get('plugin') or item.get('id')
    if not isinstance(name, str) or not name.strip():
        return None
    source = item.get('source') or item.get('path') or item.get('module')
    version = item.get('version')
    enabled = item.get('enabled')
    return PluginCacheEntry(
        name=name.strip(),
        enabled=True if enabled is None else bool(enabled),
        version=version if isinstance(version, str) and version else None,
        source=source if isinstance(source, str) and source else None,
    )
