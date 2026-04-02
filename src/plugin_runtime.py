from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginToolAlias:
    name: str
    base_tool: str
    description: str | None = None


@dataclass(frozen=True)
class PluginToolHook:
    tool_name: str
    after_result: str | None = None
    block_message: str | None = None


@dataclass(frozen=True)
class PluginManifest:
    name: str
    path: str
    version: str | None = None
    description: str | None = None
    tool_names: tuple[str, ...] = ()
    hook_names: tuple[str, ...] = ()
    tool_aliases: tuple[PluginToolAlias, ...] = ()
    tool_hooks: tuple[PluginToolHook, ...] = ()
    blocked_tools: tuple[str, ...] = ()
    before_prompt: str | None = None
    after_turn: str | None = None


@dataclass
class PluginRuntime:
    manifests: tuple[PluginManifest, ...] = field(default_factory=tuple)

    @classmethod
    def from_workspace(
        cls,
        cwd: Path,
        additional_working_directories: tuple[str, ...] = (),
    ) -> 'PluginRuntime':
        manifests: list[PluginManifest] = []
        for path in _discover_plugin_manifest_paths(cwd, additional_working_directories):
            manifest = _load_manifest(path)
            if manifest is not None:
                manifests.append(manifest)
        return cls(manifests=tuple(manifests))

    def instruction_blocks(self) -> tuple[str, ...]:
        blocks: list[str] = []
        for manifest in self.manifests:
            lines = [
                f'Plugin: {manifest.name}',
            ]
            if manifest.description:
                lines.append(f'Description: {manifest.description}')
            if manifest.tool_names:
                lines.append(f'Tools: {", ".join(manifest.tool_names)}')
            if manifest.hook_names:
                lines.append(f'Hooks: {", ".join(manifest.hook_names)}')
            if manifest.tool_aliases:
                lines.append(
                    'Tool aliases: '
                    + ', '.join(alias.name for alias in manifest.tool_aliases)
                )
            if manifest.blocked_tools:
                lines.append(
                    'Blocked tools: '
                    + ', '.join(manifest.blocked_tools)
                )
            blocks.append('\n'.join(lines))
        return tuple(blocks)

    def before_prompt_injections(self) -> tuple[str, ...]:
        return tuple(
            manifest.before_prompt
            for manifest in self.manifests
            if manifest.before_prompt
        )

    def after_turn_injections(self) -> tuple[str, ...]:
        return tuple(
            manifest.after_turn
            for manifest in self.manifests
            if manifest.after_turn
        )

    def register_tool_aliases(
        self,
        base_registry: dict[str, Any],
    ) -> dict[str, Any]:
        from .agent_tools import AgentTool

        aliases: dict[str, AgentTool] = {}
        for manifest in self.manifests:
            for alias in manifest.tool_aliases:
                base_tool = base_registry.get(alias.base_tool)
                if base_tool is None or alias.name in base_registry or alias.name in aliases:
                    continue
                aliases[alias.name] = AgentTool(
                    name=alias.name,
                    description=(
                        alias.description
                        or f'Plugin alias from {manifest.name} for base tool {alias.base_tool}.'
                    ),
                    parameters=base_tool.parameters,
                    handler=base_tool.handler,
                )
        return aliases

    def blocked_tool_message(self, tool_name: str) -> str | None:
        for manifest in self.manifests:
            if tool_name in manifest.blocked_tools:
                return f'Plugin {manifest.name} blocked tool {tool_name}.'
            for hook in manifest.tool_hooks:
                if hook.tool_name == tool_name and hook.block_message:
                    return hook.block_message
        return None

    def tool_result_injections(self, tool_name: str) -> tuple[str, ...]:
        messages: list[str] = []
        for manifest in self.manifests:
            for hook in manifest.tool_hooks:
                if hook.tool_name == tool_name and hook.after_result:
                    messages.append(f'{manifest.name}: {hook.after_result}')
        return tuple(messages)

    def render_summary(self) -> str:
        if not self.manifests:
            return 'No local plugin manifests discovered.'
        lines = [f'Local plugin manifests: {len(self.manifests)}']
        for manifest in self.manifests[:10]:
            details = [manifest.name]
            if manifest.version:
                details.append(f'version={manifest.version}')
            if manifest.tool_names:
                details.append(f'tools={len(manifest.tool_names)}')
            if manifest.hook_names:
                details.append(f'hooks={len(manifest.hook_names)}')
            if manifest.tool_aliases:
                details.append(f'aliases={len(manifest.tool_aliases)}')
            if manifest.blocked_tools:
                details.append(f'blocked={len(manifest.blocked_tools)}')
            if manifest.tool_hooks:
                details.append(f'tool_hooks={len(manifest.tool_hooks)}')
            lines.append(f"- {'; '.join(details)}")
        if len(self.manifests) > 10:
            lines.append(f'- ... plus {len(self.manifests) - 10} more plugin manifests')
        return '\n'.join(lines)


def _discover_plugin_manifest_paths(
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

    roots = _walk_upwards(cwd.resolve())
    roots.extend(Path(path).resolve() for path in additional_working_directories)
    for root in roots:
        remember(root / '.codex-plugin' / 'plugin.json')
        remember(root / '.claw-plugin' / 'plugin.json')
        plugins_dir = root / 'plugins'
        if plugins_dir.is_dir():
            for candidate in sorted(plugins_dir.glob('*/plugin.json')):
                remember(candidate)
    return tuple(candidates)


def _walk_upwards(path: Path) -> list[Path]:
    walked: list[Path] = []
    current = path
    while True:
        walked.append(current)
        if current.parent == current:
            break
        current = current.parent
    return walked


def _load_manifest(path: Path) -> PluginManifest | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get('name')
    if not isinstance(name, str) or not name.strip():
        return None
    before_prompt, after_turn, hook_names = _parse_hooks(payload.get('hooks'))
    return PluginManifest(
        name=name.strip(),
        path=str(path),
        version=_optional_string(payload.get('version')),
        description=_optional_string(payload.get('description')),
        tool_names=_extract_string_tuple(payload.get('tools')),
        hook_names=hook_names,
        tool_aliases=_extract_tool_aliases(payload),
        tool_hooks=_extract_tool_hooks(payload),
        blocked_tools=_extract_string_tuple(
            payload.get('blocked_tools')
            if payload.get('blocked_tools') is not None
            else payload.get('blockedTools')
        ),
        before_prompt=before_prompt,
        after_turn=after_turn,
    )


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str) and item.strip())
    if isinstance(value, dict):
        names = [key for key in value if isinstance(key, str) and key.strip()]
        return tuple(names)
    return ()


def _extract_tool_aliases(payload: dict[str, Any]) -> tuple[PluginToolAlias, ...]:
    raw_aliases = payload.get('tool_aliases')
    if raw_aliases is None:
        raw_aliases = payload.get('toolAliases')
    aliases: list[PluginToolAlias] = []
    if isinstance(raw_aliases, list):
        for item in raw_aliases:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            base_tool = item.get('base_tool')
            if base_tool is None:
                base_tool = item.get('baseTool')
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(base_tool, str) or not base_tool.strip():
                continue
            aliases.append(
                PluginToolAlias(
                    name=name.strip(),
                    base_tool=base_tool.strip(),
                    description=_optional_string(item.get('description')),
                )
            )
    return tuple(aliases)


def _parse_hooks(value: Any) -> tuple[str | None, str | None, tuple[str, ...]]:
    if isinstance(value, list):
        names = tuple(item for item in value if isinstance(item, str) and item.strip())
        return None, None, names
    if isinstance(value, dict):
        names = tuple(key for key in value if isinstance(key, str) and key.strip())
        before_prompt = value.get('beforePrompt')
        if before_prompt is None:
            before_prompt = value.get('before_prompt')
        after_turn = value.get('afterTurn')
        if after_turn is None:
            after_turn = value.get('after_turn')
        return (
            _optional_string(before_prompt),
            _optional_string(after_turn),
            names,
        )
    return None, None, ()


def _extract_tool_hooks(payload: dict[str, Any]) -> tuple[PluginToolHook, ...]:
    raw_hooks = payload.get('tool_hooks')
    if raw_hooks is None:
        raw_hooks = payload.get('toolHooks')
    hooks: list[PluginToolHook] = []
    if isinstance(raw_hooks, dict):
        for tool_name, value in raw_hooks.items():
            if not isinstance(tool_name, str) or not tool_name.strip():
                continue
            if isinstance(value, str):
                hooks.append(
                    PluginToolHook(
                        tool_name=tool_name.strip(),
                        after_result=value.strip() or None,
                    )
                )
                continue
            if not isinstance(value, dict):
                continue
            after_result = value.get('afterResult')
            if after_result is None:
                after_result = value.get('after_result')
            block_message = value.get('blockMessage')
            if block_message is None:
                block_message = value.get('block_message')
            hooks.append(
                PluginToolHook(
                    tool_name=tool_name.strip(),
                    after_result=_optional_string(after_result),
                    block_message=_optional_string(block_message),
                )
            )
    return tuple(hooks)
