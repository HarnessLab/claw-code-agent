from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .agent_types import AgentRuntimeConfig

MAX_STATUS_CHARS = 2000
MAX_MEMORY_CHARACTER_COUNT = 40000
MEMORY_INSTRUCTION_PROMPT = (
    'Codebase and user instructions are shown below. Be sure to adhere to '
    'these instructions. IMPORTANT: These instructions override default '
    'behavior when they directly apply to the task.'
)

_SYSTEM_PROMPT_INJECTION: str | None = None


@dataclass(frozen=True)
class AgentContextSnapshot:
    cwd: Path
    shell: str
    platform_name: str
    os_version: str
    current_date: str
    is_git_repo: bool
    is_git_worktree: bool
    additional_working_directories: tuple[str, ...]
    user_context: dict[str, str]
    system_context: dict[str, str]


def clear_context_caches() -> None:
    _get_git_status_cached.cache_clear()
    _get_system_context_cached.cache_clear()
    _get_user_context_cached.cache_clear()


def get_system_prompt_injection() -> str | None:
    return _SYSTEM_PROMPT_INJECTION


def set_system_prompt_injection(value: str | None) -> None:
    global _SYSTEM_PROMPT_INJECTION
    _SYSTEM_PROMPT_INJECTION = value
    clear_context_caches()


def build_context_snapshot(runtime_config: AgentRuntimeConfig) -> AgentContextSnapshot:
    cwd = runtime_config.cwd.resolve()
    additional_dirs = tuple(
        str(path.resolve()) for path in runtime_config.additional_working_directories
    )
    return AgentContextSnapshot(
        cwd=cwd,
        shell=os.environ.get('SHELL', 'unknown'),
        platform_name=platform.system().lower() or os.name,
        os_version=_get_os_version(),
        current_date=date.today().isoformat(),
        is_git_repo=_is_git_repo(cwd),
        is_git_worktree=_is_git_worktree(cwd),
        additional_working_directories=additional_dirs,
        user_context=get_user_context(
            cwd,
            additional_dirs,
            runtime_config.disable_claude_md_discovery,
        ),
        system_context=get_system_context(cwd),
    )


def get_git_status(cwd: Path) -> str | None:
    return _get_git_status_cached(str(cwd.resolve()))


def get_system_context(cwd: Path) -> dict[str, str]:
    return dict(_get_system_context_cached(str(cwd.resolve())))


def get_user_context(
    cwd: Path,
    additional_working_directories: tuple[str, ...] = (),
    disable_claude_md_discovery: bool = False,
) -> dict[str, str]:
    normalized_dirs = tuple(
        str(Path(path).resolve()) for path in additional_working_directories
    )
    return dict(
        _get_user_context_cached(
            str(cwd.resolve()),
            normalized_dirs,
            disable_claude_md_discovery,
        )
    )


def render_context_report(snapshot: AgentContextSnapshot, model: str) -> str:
    lines = [
        '# Context',
        '',
        '## Environment',
        f'- Primary working directory: {snapshot.cwd}',
        f'- Model: {model}',
        f'- Shell: {Path(snapshot.shell).name or snapshot.shell}',
        f'- Platform: {snapshot.platform_name}',
        f'- OS Version: {snapshot.os_version}',
        f'- Is a git repository: {snapshot.is_git_repo}',
        f'- Is a git worktree: {snapshot.is_git_worktree}',
        f'- Current date: {snapshot.current_date}',
    ]
    if snapshot.additional_working_directories:
        lines.extend(
            [
                '',
                '## Additional Working Directories',
                *[f'- {path}' for path in snapshot.additional_working_directories],
            ]
        )
    if snapshot.user_context:
        lines.extend(['', '## User Context'])
        for key, value in snapshot.user_context.items():
            lines.extend([f'### {key}', value, ''])
        while lines and not lines[-1]:
            lines.pop()
    if snapshot.system_context:
        lines.extend(['', '## System Context'])
        for key, value in snapshot.system_context.items():
            lines.extend([f'### {key}', value, ''])
        while lines and not lines[-1]:
            lines.pop()
    return '\n'.join(lines)


@lru_cache(maxsize=32)
def _get_system_context_cached(cwd: str) -> dict[str, str]:
    context: dict[str, str] = {}
    git_status = _get_git_status_cached(cwd)
    if git_status is not None:
        context['gitStatus'] = git_status
    injection = get_system_prompt_injection()
    if injection:
        context['cacheBreaker'] = f'[CACHE_BREAKER: {injection}]'
    return context


@lru_cache(maxsize=32)
def _get_user_context_cached(
    cwd: str,
    additional_working_directories: tuple[str, ...],
    disable_claude_md_discovery: bool,
) -> dict[str, str]:
    context: dict[str, str] = {
        'currentDate': f"Today's date is {date.today().isoformat()}.",
    }
    if disable_claude_md_discovery:
        return context

    memory_bundle = _load_memory_bundle(Path(cwd), additional_working_directories)
    if memory_bundle:
        context['claudeMd'] = memory_bundle
    return context


@lru_cache(maxsize=32)
def _get_git_status_cached(cwd: str) -> str | None:
    root = Path(cwd)
    if not _is_git_repo(root):
        return None

    branch = _run_command(['git', 'branch', '--show-current'], root)
    main_branch = _detect_default_branch(root)
    status = _run_command(['git', '--no-optional-locks', 'status', '--short'], root) or ''
    log = _run_command(['git', '--no-optional-locks', 'log', '--oneline', '-n', '5'], root) or '(none)'
    user_name = _run_command(['git', 'config', 'user.name'], root)

    if len(status) > MAX_STATUS_CHARS:
        status = (
            status[:MAX_STATUS_CHARS]
            + '\n... (truncated because it exceeds 2k characters. Use bash for full git status.)'
        )

    parts = [
        'This is the git status at the start of the conversation. It is a snapshot and does not update automatically during the run.',
        f'Current branch: {branch or "(unknown)"}',
        f'Main branch: {main_branch or "(unknown)"}',
    ]
    if user_name:
        parts.append(f'Git user: {user_name}')
    parts.extend(
        [
            f'Status:\n{status or "(clean)"}',
            f'Recent commits:\n{log}',
        ]
    )
    return '\n\n'.join(parts)


def _load_memory_bundle(cwd: Path, additional_working_directories: tuple[str, ...]) -> str | None:
    discovered: list[Path] = []
    seen: set[Path] = set()

    for candidate in _discover_global_memory_files():
        _remember_path(candidate, discovered, seen)

    for directory in _walk_upwards(cwd):
        for candidate in _discover_memory_files_for_directory(directory):
            _remember_path(candidate, discovered, seen)

    for raw_path in additional_working_directories:
        for candidate in _discover_memory_files_for_directory(Path(raw_path)):
            _remember_path(candidate, discovered, seen)

    if not discovered:
        return None

    blocks = [MEMORY_INSTRUCTION_PROMPT]
    for path in discovered:
        try:
            content = path.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            continue
        if not content:
            continue
        if len(content) > MAX_MEMORY_CHARACTER_COUNT:
            content = (
                content[:MAX_MEMORY_CHARACTER_COUNT]
                + '\n... (truncated because it exceeds the memory size limit)'
            )
        blocks.append(f'## {path}\n{content}')
    if len(blocks) == 1:
        return None
    return '\n\n'.join(blocks)


def _discover_global_memory_files() -> list[Path]:
    home_memory = Path.home() / '.claude' / 'CLAUDE.md'
    return [home_memory] if home_memory.is_file() else []


def _discover_memory_files_for_directory(directory: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in (
        directory / 'CLAUDE.md',
        directory / '.claude' / 'CLAUDE.md',
        directory / 'CLAUDE.local.md',
    ):
        if candidate.is_file():
            files.append(candidate.resolve())

    rules_dir = directory / '.claude' / 'rules'
    if rules_dir.is_dir():
        files.extend(
            path.resolve()
            for path in sorted(rules_dir.glob('*.md'))
            if path.is_file()
        )
    return files


def _walk_upwards(cwd: Path) -> list[Path]:
    parents = list(cwd.resolve().parents)
    parents.reverse()
    return [*parents, cwd.resolve()]


def _remember_path(path: Path, discovered: list[Path], seen: set[Path]) -> None:
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    discovered.append(resolved)


def _detect_default_branch(cwd: Path) -> str | None:
    origin_head = _run_command(
        ['git', 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'],
        cwd,
    )
    if origin_head and '/' in origin_head:
        return origin_head.split('/', 1)[1]

    for candidate in ('main', 'master'):
        try:
            completed = subprocess.run(
                ['git', 'show-ref', '--verify', f'refs/heads/{candidate}'],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except OSError:
            return None
        if completed.returncode == 0:
            return candidate
    return None


@lru_cache(maxsize=32)
def _is_git_repo(cwd: Path) -> bool:
    if (cwd / '.git').exists():
        return True
    try:
        completed = subprocess.run(
            ['git', 'rev-parse', '--is-inside-work-tree'],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == 'true'


def _is_git_worktree(cwd: Path) -> bool:
    if not _is_git_repo(cwd):
        return False
    git_dir = _run_command(['git', 'rev-parse', '--git-dir'], cwd)
    git_common_dir = _run_command(['git', 'rev-parse', '--git-common-dir'], cwd)
    return bool(git_dir and git_common_dir and git_dir != git_common_dir)


def _run_command(command: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip()
    return output or None


def _get_os_version() -> str:
    system = platform.system()
    release = platform.release()
    if system and release:
        return f'{system} {release}'
    return platform.platform()
