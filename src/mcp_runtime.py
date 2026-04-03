from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MCPResource:
    uri: str
    server_name: str
    source_manifest: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    resolved_path: str | None = None
    inline_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPRuntime:
    resources: tuple[MCPResource, ...] = field(default_factory=tuple)

    @classmethod
    def from_workspace(
        cls,
        cwd: Path,
        additional_working_directories: tuple[str, ...] = (),
    ) -> 'MCPRuntime':
        resources: list[MCPResource] = []
        for path in _discover_manifest_paths(cwd, additional_working_directories):
            resources.extend(_load_resources_from_manifest(path))
        return cls(resources=tuple(resources))

    @property
    def manifests(self) -> tuple[str, ...]:
        seen: list[str] = []
        for resource in self.resources:
            if resource.source_manifest not in seen:
                seen.append(resource.source_manifest)
        return tuple(seen)

    def list_resources(
        self,
        *,
        query: str | None = None,
        limit: int | None = None,
    ) -> tuple[MCPResource, ...]:
        resources = self.resources
        if query:
            needle = query.lower()
            resources = tuple(
                resource
                for resource in resources
                if needle in resource.uri.lower()
                or needle in resource.server_name.lower()
                or needle in (resource.name or '').lower()
                or needle in (resource.description or '').lower()
            )
        if limit is not None and limit >= 0:
            resources = resources[:limit]
        return resources

    def get_resource(self, uri: str) -> MCPResource | None:
        for resource in self.resources:
            if resource.uri == uri:
                return resource
        return None

    def read_resource(self, uri: str, *, max_chars: int = 12000) -> str:
        resource = self.get_resource(uri)
        if resource is None:
            raise FileNotFoundError(f'Unknown MCP resource: {uri}')
        if resource.inline_text is not None:
            return _truncate(resource.inline_text, max_chars)
        if resource.resolved_path is None:
            raise FileNotFoundError(f'MCP resource has no readable content: {uri}')
        path = Path(resource.resolved_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f'MCP resource file not found: {path}')
        text = path.read_text(encoding='utf-8', errors='replace')
        return _truncate(text, max_chars)

    def render_summary(self) -> str:
        if not self.resources:
            return 'No local MCP manifests or resources discovered.'
        lines = [
            f'Local MCP manifests: {len(self.manifests)}',
            f'Local MCP resources: {len(self.resources)}',
        ]
        by_server: dict[str, int] = {}
        for resource in self.resources:
            by_server[resource.server_name] = by_server.get(resource.server_name, 0) + 1
        for server_name, count in sorted(by_server.items()):
            lines.append(f'- {server_name}: {count} resource(s)')
        for manifest in self.manifests[:10]:
            manifest_name = Path(manifest).name
            manifest_count = sum(
                1 for resource in self.resources if resource.source_manifest == manifest
            )
            lines.append(f'- {manifest_name}: {manifest_count} resource(s)')
        return '\n'.join(lines)

    def render_resource_index(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> str:
        resources = self.list_resources(query=query, limit=limit)
        if not resources:
            return '# MCP Resources\n\nNo matching MCP resources discovered.'
        lines = ['# MCP Resources', '']
        for resource in resources:
            details = [resource.uri]
            details.append(f'server={resource.server_name}')
            if resource.name:
                details.append(f'name={resource.name}')
            if resource.mime_type:
                details.append(f'mime={resource.mime_type}')
            if resource.resolved_path:
                details.append(f'path={resource.resolved_path}')
            lines.append('- ' + '; '.join(details))
        return '\n'.join(lines)

    def render_resource(self, uri: str, *, max_chars: int = 12000) -> str:
        resource = self.get_resource(uri)
        if resource is None:
            return f'# MCP Resource\n\nUnknown MCP resource: {uri}'
        lines = [
            '# MCP Resource',
            '',
            f'- URI: {resource.uri}',
            f'- Server: {resource.server_name}',
        ]
        if resource.name:
            lines.append(f'- Name: {resource.name}')
        if resource.mime_type:
            lines.append(f'- MIME Type: {resource.mime_type}')
        if resource.resolved_path:
            lines.append(f'- Path: {resource.resolved_path}')
        lines.extend(['', self.read_resource(uri, max_chars=max_chars)])
        return '\n'.join(lines)


def _discover_manifest_paths(
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
        remember(root / '.claw-mcp.json')
        remember(root / '.mcp.json')
        remember(root / '.codex-mcp.json')
        remember(root / 'mcp.json')
    return tuple(candidates)


def _load_resources_from_manifest(path: Path) -> list[MCPResource]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    resources: list[MCPResource] = []
    if isinstance(payload.get('resources'), list):
        resources.extend(
            _extract_resources(
                payload.get('name') if isinstance(payload.get('name'), str) else 'local',
                payload['resources'],
                manifest_path=path,
            )
        )
    servers = payload.get('servers')
    if isinstance(servers, list):
        for item in servers:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            if not isinstance(name, str) or not name.strip():
                continue
            raw_resources = item.get('resources')
            if not isinstance(raw_resources, list):
                continue
            resources.extend(
                _extract_resources(name.strip(), raw_resources, manifest_path=path)
            )
    return resources


def _extract_resources(
    server_name: str,
    raw_resources: list[Any],
    *,
    manifest_path: Path,
) -> list[MCPResource]:
    resources: list[MCPResource] = []
    seen_uris: set[str] = set()
    for item in raw_resources:
        if not isinstance(item, dict):
            continue
        uri = item.get('uri')
        if not isinstance(uri, str) or not uri.strip():
            continue
        uri = uri.strip()
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        raw_path = item.get('path')
        if raw_path is None:
            raw_path = item.get('file')
        resolved_path: str | None = None
        if isinstance(raw_path, str) and raw_path.strip():
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = manifest_path.parent / candidate
            resolved_path = str(candidate.resolve())
        inline_text = item.get('text')
        if not isinstance(inline_text, str):
            inline_text = None
        metadata = item.get('metadata')
        resources.append(
            MCPResource(
                uri=uri,
                server_name=server_name,
                source_manifest=str(manifest_path),
                name=item.get('name') if isinstance(item.get('name'), str) else None,
                description=(
                    item.get('description')
                    if isinstance(item.get('description'), str)
                    else None
                ),
                mime_type=(
                    item.get('mimeType')
                    if isinstance(item.get('mimeType'), str)
                    else item.get('mime_type')
                    if isinstance(item.get('mime_type'), str)
                    else None
                ),
                resolved_path=resolved_path,
                inline_text=inline_text,
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
            )
        )
    return resources


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return f'{head}\n...[truncated]...\n{tail}'
