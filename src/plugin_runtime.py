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
    before_tool: str | None = None
    after_result: str | None = None
    block_message: str | None = None


@dataclass(frozen=True)
class PluginVirtualTool:
    name: str
    description: str
    response_template: str
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginManifest:
    name: str
    path: str
    version: str | None = None
    description: str | None = None
    tool_names: tuple[str, ...] = ()
    hook_names: tuple[str, ...] = ()
    tool_aliases: tuple[PluginToolAlias, ...] = ()
    virtual_tools: tuple[PluginVirtualTool, ...] = ()
    tool_hooks: tuple[PluginToolHook, ...] = ()
    blocked_tools: tuple[str, ...] = ()
    before_prompt: str | None = None
    after_turn: str | None = None
    on_resume: str | None = None
    before_persist: str | None = None
    before_delegate: str | None = None
    after_delegate: str | None = None


@dataclass
class PluginRuntime:
    manifests: tuple[PluginManifest, ...] = field(default_factory=tuple)
    session_state: dict[str, Any] = field(default_factory=dict)

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
            if manifest.virtual_tools:
                lines.append(
                    'Virtual tools: '
                    + ', '.join(tool.name for tool in manifest.virtual_tools)
                )
            if manifest.blocked_tools:
                lines.append(
                    'Blocked tools: '
                    + ', '.join(manifest.blocked_tools)
                )
            blocks.append('\n'.join(lines))
        return tuple(blocks)

    def before_prompt_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        injections = tuple(
            manifest.before_prompt
            for manifest in self.manifests
            if manifest.before_prompt
        )
        if injections:
            self.session_state['before_prompt_calls'] = int(
                self.session_state.get('before_prompt_calls', 0)
            ) + 1
        return injections

    def after_turn_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        injections = tuple(
            manifest.after_turn
            for manifest in self.manifests
            if manifest.after_turn
        )
        if injections:
            self.session_state['after_turn_calls'] = int(
                self.session_state.get('after_turn_calls', 0)
            ) + 1
        return injections

    def on_resume_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        injections = tuple(
            manifest.on_resume
            for manifest in self.manifests
            if manifest.on_resume
        )
        if injections:
            self.session_state['resume_calls'] = int(
                self.session_state.get('resume_calls', 0)
            ) + 1
        return injections

    def before_persist_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        injections = tuple(
            manifest.before_persist
            for manifest in self.manifests
            if manifest.before_persist
        )
        if injections:
            self.session_state['persist_calls'] = int(
                self.session_state.get('persist_calls', 0)
            ) + 1
        return injections

    def before_delegate_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        injections = tuple(
            manifest.before_delegate
            for manifest in self.manifests
            if manifest.before_delegate
        )
        if injections:
            self.session_state['delegate_calls'] = int(
                self.session_state.get('delegate_calls', 0)
            ) + 1
        return injections

    def after_delegate_injections(self) -> tuple[str, ...]:
        if not self.manifests:
            return ()
        return tuple(
            manifest.after_delegate
            for manifest in self.manifests
            if manifest.after_delegate
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

    def register_virtual_tools(
        self,
        base_registry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .agent_tools import AgentTool

        tools: dict[str, AgentTool] = {}
        occupied = set(base_registry or {})
        for manifest in self.manifests:
            for tool in manifest.virtual_tools:
                if tool.name in occupied or tool.name in tools:
                    continue
                tools[tool.name] = AgentTool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters or {'type': 'object', 'properties': {}},
                    handler=_build_virtual_tool_handler(manifest.name, tool),
                )
        return tools

    def blocked_tool_message(self, tool_name: str) -> str | None:
        for manifest in self.manifests:
            if tool_name in manifest.blocked_tools:
                return f'Plugin {manifest.name} blocked tool {tool_name}.'
            for hook in manifest.tool_hooks:
                if hook.tool_name == tool_name and hook.block_message:
                    return hook.block_message
        return None

    def tool_preflight_injections(self, tool_name: str) -> tuple[str, ...]:
        messages: list[str] = []
        for manifest in self.manifests:
            for hook in manifest.tool_hooks:
                if hook.tool_name == tool_name and hook.before_tool:
                    messages.append(f'{manifest.name}: {hook.before_tool}')
        return tuple(messages)

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
            if manifest.virtual_tools:
                details.append(f'virtual_tools={len(manifest.virtual_tools)}')
            if manifest.blocked_tools:
                details.append(f'blocked={len(manifest.blocked_tools)}')
            if manifest.tool_hooks:
                details.append(f'tool_hooks={len(manifest.tool_hooks)}')
            lines.append(f"- {'; '.join(details)}")
        if len(self.manifests) > 10:
            lines.append(f'- ... plus {len(self.manifests) - 10} more plugin manifests')
        if self.session_state:
            lines.append(
                '- runtime_state='
                + ', '.join(
                    f'{name}={value}'
                    for name, value in sorted(self.session_state.items())
                    if isinstance(value, (int, float, str, bool))
                )
            )
        return '\n'.join(lines)

    def record_tool_attempt(self, tool_name: str, *, blocked: bool) -> None:
        attempts = int(self.session_state.get('tool_attempts', 0))
        self.session_state['tool_attempts'] = attempts + 1
        if blocked:
            blocked_count = int(self.session_state.get('blocked_tool_attempts', 0))
            self.session_state['blocked_tool_attempts'] = blocked_count + 1
        counts = self.session_state.get('tool_attempt_counts')
        if not isinstance(counts, dict):
            counts = {}
        counts[tool_name] = int(counts.get(tool_name, 0)) + 1
        self.session_state['tool_attempt_counts'] = counts

    def record_tool_result(
        self,
        tool_name: str,
        *,
        ok: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        counts = self.session_state.get('tool_result_counts')
        if not isinstance(counts, dict):
            counts = {}
        counts[tool_name] = int(counts.get(tool_name, 0)) + 1
        self.session_state['tool_result_counts'] = counts
        key = 'successful_tool_results' if ok else 'failed_tool_results'
        self.session_state[key] = int(self.session_state.get(key, 0)) + 1
        if isinstance(metadata, dict) and metadata.get('action') == 'plugin_virtual_tool':
            self.session_state['virtual_tool_results'] = int(
                self.session_state.get('virtual_tool_results', 0)
            ) + 1

    def export_session_state(self) -> dict[str, Any]:
        exported: dict[str, Any] = {}
        for key, value in self.session_state.items():
            if isinstance(value, dict):
                exported[key] = {
                    str(name): count
                    for name, count in value.items()
                    if isinstance(name, str)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                }
            elif isinstance(value, (int, float, str, bool)):
                exported[key] = value
        return exported

    def restore_session_state(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            self.session_state = {}
            return
        restored: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                restored[key] = {
                    str(name): int(count)
                    for name, count in value.items()
                    if isinstance(name, str)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                }
            elif isinstance(value, (int, float, str, bool)):
                restored[key] = value
        self.session_state = restored

    def runtime_state_reminder(self) -> str | None:
        if not self.manifests or not self.session_state:
            return None
        lines = ['Plugin runtime state:']
        before_prompt_calls = self.session_state.get('before_prompt_calls')
        if isinstance(before_prompt_calls, int):
            lines.append(f'- before_prompt_calls={before_prompt_calls}')
        after_turn_calls = self.session_state.get('after_turn_calls')
        if isinstance(after_turn_calls, int):
            lines.append(f'- after_turn_calls={after_turn_calls}')
        tool_attempts = self.session_state.get('tool_attempts')
        if isinstance(tool_attempts, int):
            lines.append(f'- tool_attempts={tool_attempts}')
        blocked_attempts = self.session_state.get('blocked_tool_attempts')
        if isinstance(blocked_attempts, int):
            lines.append(f'- blocked_tool_attempts={blocked_attempts}')
        resume_calls = self.session_state.get('resume_calls')
        if isinstance(resume_calls, int):
            lines.append(f'- resume_calls={resume_calls}')
        persist_calls = self.session_state.get('persist_calls')
        if isinstance(persist_calls, int):
            lines.append(f'- persist_calls={persist_calls}')
        delegate_calls = self.session_state.get('delegate_calls')
        if isinstance(delegate_calls, int):
            lines.append(f'- delegate_calls={delegate_calls}')
        virtual_results = self.session_state.get('virtual_tool_results')
        if isinstance(virtual_results, int):
            lines.append(f'- virtual_tool_results={virtual_results}')
        if len(lines) == 1:
            return None
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
    (
        before_prompt,
        after_turn,
        on_resume,
        before_persist,
        before_delegate,
        after_delegate,
        hook_names,
    ) = _parse_hooks(payload.get('hooks'))
    return PluginManifest(
        name=name.strip(),
        path=str(path),
        version=_optional_string(payload.get('version')),
        description=_optional_string(payload.get('description')),
        tool_names=_extract_string_tuple(payload.get('tools')),
        hook_names=hook_names,
        tool_aliases=_extract_tool_aliases(payload),
        virtual_tools=_extract_virtual_tools(payload),
        tool_hooks=_extract_tool_hooks(payload),
        blocked_tools=_extract_string_tuple(
            payload.get('blocked_tools')
            if payload.get('blocked_tools') is not None
            else payload.get('blockedTools')
        ),
        before_prompt=before_prompt,
        after_turn=after_turn,
        on_resume=on_resume,
        before_persist=before_persist,
        before_delegate=before_delegate,
        after_delegate=after_delegate,
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


def _parse_hooks(
    value: Any,
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    tuple[str, ...],
]:
    if isinstance(value, list):
        names = tuple(item for item in value if isinstance(item, str) and item.strip())
        return None, None, None, None, None, None, names
    if isinstance(value, dict):
        names = tuple(key for key in value if isinstance(key, str) and key.strip())
        before_prompt = value.get('beforePrompt')
        if before_prompt is None:
            before_prompt = value.get('before_prompt')
        after_turn = value.get('afterTurn')
        if after_turn is None:
            after_turn = value.get('after_turn')
        on_resume = value.get('onResume')
        if on_resume is None:
            on_resume = value.get('on_resume')
        before_persist = value.get('beforePersist')
        if before_persist is None:
            before_persist = value.get('before_persist')
        before_delegate = value.get('beforeDelegate')
        if before_delegate is None:
            before_delegate = value.get('before_delegate')
        after_delegate = value.get('afterDelegate')
        if after_delegate is None:
            after_delegate = value.get('after_delegate')
        return (
            _optional_string(before_prompt),
            _optional_string(after_turn),
            _optional_string(on_resume),
            _optional_string(before_persist),
            _optional_string(before_delegate),
            _optional_string(after_delegate),
            names,
        )
    return None, None, None, None, None, None, ()


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
            before_tool = value.get('beforeTool')
            if before_tool is None:
                before_tool = value.get('before_tool')
            after_result = value.get('afterResult')
            if after_result is None:
                after_result = value.get('after_result')
            block_message = value.get('blockMessage')
            if block_message is None:
                block_message = value.get('block_message')
            hooks.append(
                PluginToolHook(
                    tool_name=tool_name.strip(),
                    before_tool=_optional_string(before_tool),
                    after_result=_optional_string(after_result),
                    block_message=_optional_string(block_message),
                )
            )
    return tuple(hooks)


def _extract_virtual_tools(payload: dict[str, Any]) -> tuple[PluginVirtualTool, ...]:
    raw_tools = payload.get('virtual_tools')
    if raw_tools is None:
        raw_tools = payload.get('virtualTools')
    if raw_tools is None:
        raw_tools = payload.get('runtimeTools')
    tools: list[PluginVirtualTool] = []
    if isinstance(raw_tools, list):
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            name = _optional_string(item.get('name'))
            description = _optional_string(item.get('description'))
            response_template = item.get('responseTemplate')
            if response_template is None:
                response_template = item.get('response_template')
            if response_template is None:
                response_template = item.get('response')
            response_text = _optional_string(response_template)
            parameters = item.get('parameters')
            metadata = item.get('metadata')
            if not name or not description or not response_text:
                continue
            tools.append(
                PluginVirtualTool(
                    name=name,
                    description=description,
                    response_template=response_text,
                    parameters=dict(parameters) if isinstance(parameters, dict) else {},
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
    return tuple(tools)


def _build_virtual_tool_handler(
    plugin_name: str,
    tool: PluginVirtualTool,
):
    def _handler(arguments: dict[str, Any], context):  # noqa: ANN001
        rendered = _render_virtual_tool_response(tool.response_template, arguments)
        metadata = {
            'action': 'plugin_virtual_tool',
            'plugin_name': plugin_name,
            'virtual_tool': tool.name,
            **tool.metadata,
        }
        return rendered, metadata

    return _handler


def _render_virtual_tool_response(
    template: str,
    arguments: dict[str, Any],
) -> str:
    normalized = {
        key: json.dumps(value, ensure_ascii=True) if isinstance(value, (dict, list)) else str(value)
        for key, value in arguments.items()
    }
    try:
        return template.format_map(_SafeTemplateDict(normalized))
    except Exception:
        return template


class _SafeTemplateDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return '{' + key + '}'
