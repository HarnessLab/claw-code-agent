"""Slash-command handler for Latti's interactive TUI.

Commands are intercepted BEFORE the LLM sees the input.
Each command performs real work and returns control to the prompt loop.

Usage (from main.py):
    from .commands import handle_command, is_command
    if is_command(user_input):
        result = handle_command(user_input, ctx)
        if result.exit_session:
            break
        continue   # don't send to LLM
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Command result
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    exit_session: bool = False   # True → exit the chat loop
    new_session:  bool = False   # True → drop current session, start fresh


# ---------------------------------------------------------------------------
# Context passed in from main.py
# ---------------------------------------------------------------------------

@dataclass
class CommandContext:
    agent:              Any           # Agent instance
    active_session_id:  str | None
    turn_count:         int
    cumulative_cost:    float
    cumulative_tokens:  int
    use_tui:            bool
    tui:                Any           # tui module
    tui_heal:           Any           # tui_heal module
    output_func:        Any           # callable(str)
    worker_supervisor_active: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, dict] = {}


def _cmd(name: str, aliases: list[str] = [], help: str = '', usage: str = ''):
    def decorator(fn):
        entry = {'fn': fn, 'help': help, 'usage': usage or f'/{name}', 'name': name}
        _COMMANDS[name] = entry
        for a in aliases:
            _COMMANDS[a] = entry
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _out(ctx: CommandContext, text: str) -> None:
    """Write to TUI info or output_func."""
    if ctx.use_tui:
        for line in text.splitlines():
            ctx.tui.info(line)
    else:
        ctx.output_func(text)


def _heading(ctx: CommandContext, text: str) -> None:
    if ctx.use_tui:
        from . import tui as _tui
        _tui._w(f'\n{_tui.G_BRIGHT}{_tui.BOLD}  {text}{_tui.RESET}\n')
    else:
        ctx.output_func(f'\n=== {text} ===')


def _divider(ctx: CommandContext) -> None:
    if ctx.use_tui:
        ctx.tui.divider()


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1_000:
        return f'{n/1_000:.1f}k'
    return str(n)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@_cmd('help', aliases=['?'], help='Show all available commands', usage='/help [command]')
def _help(args: list[str], ctx: CommandContext) -> CommandResult:
    if args:
        name = args[0].lstrip('/')
        entry = _COMMANDS.get(name)
        if not entry:
            _out(ctx, f'Unknown command: /{name}  (try /help)')
            return CommandResult()
        _out(ctx, f'  {entry["usage"]}')
        _out(ctx, f'  {entry["help"]}')
        return CommandResult()

    _heading(ctx, 'Latti Commands')

    groups = [
        ('Session',  ['status', 'cost', 'history', 'clear', 'new', 'compact']),
        ('Model',    ['model', 'models']),
        ('Memory',   ['memory', 'forget']),
        ('Tools',    ['tools', 'run']),
        ('Git',      ['git', 'diff', 'log', 'commit']),
        ('Debug',    ['doctor', 'heal', 'version']),
        ('Exit',     ['exit', 'quit']),
    ]

    seen = set()
    for group, names in groups:
        _out(ctx, f'\n  {group}')
        for name in names:
            entry = _COMMANDS.get(name)
            if entry and entry['name'] not in seen:
                seen.add(entry['name'])
                _out(ctx, f'    /{entry["usage"]:<30}  {entry["help"]}')

    # Show runtime-level commands that fall through to agent_slash_commands
    _out(ctx, '\n  Runtime (pass-through to agent)')
    runtime_cmds = [
        'context', 'mcp', 'lsp', 'worktree', 'config', 'search',
        'remote', 'account', 'files', 'copy', 'export', 'stats',
        'branch', 'effort', 'trust',
    ]
    _out(ctx, f'    {"  ".join("/" + c for c in runtime_cmds)}')
    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

@_cmd('status', aliases=['s'], help='Show current session status, model, cost, context')
def _status(args: list[str], ctx: CommandContext) -> CommandResult:
    agent = ctx.agent
    model = getattr(agent.model_config, 'model', '?')
    cwd   = str(getattr(agent.runtime_config, 'cwd', '.'))
    home  = os.path.expanduser('~')
    cwd   = cwd.replace(home, '~')

    # git branch
    branch = ''
    try:
        branch = subprocess.check_output(
            ['git', 'branch', '--show-current'],
            cwd=cwd.replace('~', home), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        pass

    _heading(ctx, 'Status')
    _out(ctx, f'  model       {model}')
    _out(ctx, f'  cwd         {cwd}' + (f'  ({branch})' if branch else ''))
    _out(ctx, f'  session     {ctx.active_session_id or "none"}')
    _out(ctx, f'  turns       {ctx.turn_count}')
    _out(ctx, f'  tokens      {_fmt_tokens(ctx.cumulative_tokens)}')
    _out(ctx, f'  cost        ${ctx.cumulative_cost:.4f}')
    state_machine_on = (
        os.environ.get('LATTI_USE_STATE_MACHINE', '1') != '0'
        and os.environ.get('LATTI_USE_LEGACY_LOOP', '0') != '1'
    )
    legacy_loop_on = os.environ.get('LATTI_USE_LEGACY_LOOP', '0') == '1'
    _out(ctx, f'  state machine  {"on" if state_machine_on else "off"}')
    _out(ctx, f'  supervisor     {"on" if ctx.worker_supervisor_active else "off"}')
    _out(ctx, f'  legacy loop    {"on" if legacy_loop_on else "off"}')

    # context %
    pct = getattr(ctx.tui, '_state', {}).get('context_pct', 0)
    bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
    _out(ctx, f'  context     {bar} {pct}%')

    # session file size
    if ctx.active_session_id:
        try:
            from .agent_session import _session_path
            sp = pathlib.Path(_session_path(ctx.active_session_id))
            if sp.exists():
                _out(ctx, f'  session file  {sp.stat().st_size // 1024}KB')
        except Exception:
            pass

    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /cost
# ---------------------------------------------------------------------------

@_cmd('cost', help='Show cost breakdown for this session')
def _cost(args: list[str], ctx: CommandContext) -> CommandResult:
    _heading(ctx, 'Cost')
    _out(ctx, f'  total       ${ctx.cumulative_cost:.4f}')
    _out(ctx, f'  tokens      {_fmt_tokens(ctx.cumulative_tokens)}')
    _out(ctx, f'  turns       {ctx.turn_count}')
    if ctx.turn_count > 0:
        per_turn = ctx.cumulative_cost / ctx.turn_count
        _out(ctx, f'  per turn    ${per_turn:.4f}')
    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------

@_cmd('clear', aliases=['cls'], help='Clear the screen (keeps session)')
def _clear(args: list[str], ctx: CommandContext) -> CommandResult:
    if ctx.use_tui:
        ctx.tui.banner()
        ctx.tui.set_state()  # redraw with current state
        ctx.tui.status_footer()
    else:
        os.system('clear')
    return CommandResult()


# ---------------------------------------------------------------------------
# /new
# ---------------------------------------------------------------------------

@_cmd('new', help='Drop current session and start a fresh one')
def _new(args: list[str], ctx: CommandContext) -> CommandResult:
    _out(ctx, 'Starting fresh session…')
    return CommandResult(new_session=True)


# ---------------------------------------------------------------------------
# /compact
# ---------------------------------------------------------------------------

@_cmd('compact', help='Force-compact the current session context now')
def _compact(args: list[str], ctx: CommandContext) -> CommandResult:
    if not ctx.active_session_id:
        _out(ctx, 'No active session to compact.')
        return CommandResult()
    try:
        from .agent_session import load_agent_session
        from .session_compact import compact_stored_session
        stored = load_agent_session(ctx.active_session_id)
        before = getattr(stored.usage, 'input_tokens', 0) or 0
        compacted, dropped = compact_stored_session(stored)
        after = int(compacted.usage.get('input_tokens', 0) or 0)
        _out(ctx, f'compacted: {_fmt_tokens(before)} → {_fmt_tokens(after)} tokens  ({dropped} messages dropped)')
    except Exception as e:
        _out(ctx, f'compact failed: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

@_cmd('history', aliases=['h'], help='Show recent turn summaries', usage='history [n=10]')
def _history(args: list[str], ctx: CommandContext) -> CommandResult:
    if not ctx.active_session_id:
        _out(ctx, 'No active session.')
        return CommandResult()
    limit = int(args[0]) if args else 10
    try:
        from .agent_session import load_agent_session
        stored = load_agent_session(ctx.active_session_id)
        msgs = stored.messages or []
        # Show last `limit` user/assistant pairs
        pairs = []
        for m in msgs:
            role = getattr(m, 'role', '') or (m.get('role', '') if isinstance(m, dict) else '')
            content = getattr(m, 'content', '') or (m.get('content', '') if isinstance(m, dict) else '')
            if isinstance(content, list):
                content = ' '.join(
                    (b.get('text', '') if isinstance(b, dict) else str(b)) for b in content
                )
            content = str(content)[:120].replace('\n', ' ')
            if role in ('user', 'assistant'):
                pairs.append((role, content))
        _heading(ctx, f'History (last {min(limit, len(pairs))} messages)')
        for role, content in pairs[-limit:]:
            prefix = '  ❯ ' if role == 'user' else '  ◆ '
            _out(ctx, f'{prefix}{content}')
        _out(ctx, '')
    except Exception as e:
        _out(ctx, f'history error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /model
# ---------------------------------------------------------------------------

@_cmd('model', help='Show or switch the active model', usage='model [name]')
def _model(args: list[str], ctx: CommandContext) -> CommandResult:
    current = getattr(ctx.agent.model_config, 'model', '?')
    if not args:
        _out(ctx, f'  current model: {current}')
        _out(ctx, '  use /models to list available models')
        return CommandResult()
    new_model = args[0]
    try:
        from dataclasses import replace
        ctx.agent.model_config = replace(ctx.agent.model_config, model=new_model)
        ctx.tui.set_state(model=new_model)
        ctx.tui.status_footer()
        _out(ctx, f'  switched: {current} → {new_model}')
    except Exception as e:
        _out(ctx, f'  failed to switch model: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /models
# ---------------------------------------------------------------------------

@_cmd('models', help='List available models from the provider')
def _models(args: list[str], ctx: CommandContext) -> CommandResult:
    _heading(ctx, 'Models')
    try:
        # Try to get from agent's configured provider
        base_url = getattr(ctx.agent.model_config, 'base_url', '') or ''
        api_key  = getattr(ctx.agent.model_config, 'api_key', '') or ''
        if 'anthropic' in base_url or 'claude' in getattr(ctx.agent.model_config, 'model', '').lower():
            models = [
                'anthropic/claude-sonnet-4-6',
                'anthropic/claude-sonnet-4-5',
                'anthropic/claude-opus-4-5',
                'anthropic/claude-haiku-4-5',
                'anthropic/claude-3-5-sonnet-20241022',
            ]
        elif 'openai' in base_url or 'gpt' in getattr(ctx.agent.model_config, 'model', '').lower():
            models = ['gpt-4o', 'gpt-4o-mini', 'o1', 'o3-mini']
        else:
            # OpenRouter — try API
            try:
                import urllib.request, json
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/models',
                    headers={'Authorization': f'Bearer {api_key}'},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                models = [m['id'] for m in data.get('data', [])][:30]
            except Exception:
                models = ['(could not fetch — check API key)']

        current = getattr(ctx.agent.model_config, 'model', '')
        for m in models:
            prefix = '→ ' if m == current else '  '
            _out(ctx, f'{prefix}{m}')
    except Exception as e:
        _out(ctx, f'error: {e}')
    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /memory
# ---------------------------------------------------------------------------

@_cmd('memory', aliases=['mem'], help='List, read, or prune memory entries', usage='memory [key|prune [days]]')
def _memory(args: list[str], ctx: CommandContext) -> CommandResult:
    mem_dir = pathlib.Path.home() / '.latti' / 'memory'

    # /memory prune [days=30]
    if args and args[0] == 'prune':
        days = int(args[1]) if len(args) > 1 else 30
        return _memory_prune(ctx, mem_dir, days)

    if not args:
        _heading(ctx, 'Memory')
        if not mem_dir.exists() or not list(mem_dir.glob('*.md')):
            _out(ctx, '  (empty — use memory_write tool to store things)')
        else:
            entries = sorted(mem_dir.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
            _out(ctx, f'  {len(entries)} entries  (newest first)')
            for p in entries:
                import time
                age_days = (time.time() - p.stat().st_mtime) / 86400
                age_s = f'{age_days:.0f}d'
                _out(ctx, f'  {p.stem:<36}  {p.stat().st_size:>6}B  {age_s:>4} ago')
        _out(ctx, '')
        _out(ctx, '  /memory prune [days]  — delete entries older than N days (default 30)')
        _out(ctx, '')
        return CommandResult()

    key  = args[0]
    safe = re.sub(r'[^a-zA-Z0-9_\-.]', '_', key)
    p    = mem_dir / f'{safe}.md'
    if not p.exists():
        _out(ctx, f'  memory:{key} — not found')
    else:
        _heading(ctx, f'memory:{key}')
        for line in p.read_text(encoding='utf-8').splitlines():
            _out(ctx, f'  {line}')
        _out(ctx, '')
    return CommandResult()


def _memory_prune(ctx: CommandContext, mem_dir: pathlib.Path, days: int) -> CommandResult:
    import time
    if not mem_dir.exists():
        _out(ctx, '  no memory directory')
        return CommandResult()
    cutoff = time.time() - days * 86400
    entries = list(mem_dir.glob('*.md'))
    old = [p for p in entries if p.stat().st_mtime < cutoff]
    if not old:
        _out(ctx, f'  nothing older than {days}d ({len(entries)} entries kept)')
        return CommandResult()
    _heading(ctx, f'Pruning {len(old)} entries older than {days}d')
    for p in sorted(old, key=lambda x: x.stat().st_mtime):
        age = (time.time() - p.stat().st_mtime) / 86400
        _out(ctx, f'  deleted  {p.stem}  ({age:.0f}d old)')
        p.unlink()
    _out(ctx, f'\n  {len(entries) - len(old)} entries remain')
    return CommandResult()


# ---------------------------------------------------------------------------
# /forget
# ---------------------------------------------------------------------------

@_cmd('forget', help='Delete a memory entry', usage='forget <key>')
def _forget(args: list[str], ctx: CommandContext) -> CommandResult:
    if not args:
        _out(ctx, 'usage: /forget <key>')
        return CommandResult()
    key  = args[0]
    safe = re.sub(r'[^a-zA-Z0-9_\-.]', '_', key)
    p    = pathlib.Path.home() / '.latti' / 'memory' / f'{safe}.md'
    if not p.exists():
        _out(ctx, f'  memory:{key} — not found')
    else:
        p.unlink()
        _out(ctx, f'  deleted memory:{key}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /tools
# ---------------------------------------------------------------------------

@_cmd('tools', help='List all tools or show a tool description', usage='tools [name]')
def _tools(args: list[str], ctx: CommandContext) -> CommandResult:
    try:
        from .agent_tools import default_tool_registry
        registry = default_tool_registry()
    except Exception as e:
        _out(ctx, f'error loading tools: {e}')
        return CommandResult()

    if args:
        name = args[0]
        tool = registry.get(name)
        if not tool:
            _out(ctx, f'  tool not found: {name}')
            return CommandResult()
        _heading(ctx, f'tool: {name}')
        _out(ctx, f'  {tool.description}')
        params = tool.parameters or {}
        props  = params.get('properties', {})
        req    = set(params.get('required', []))
        for pname, pdef in props.items():
            r = ' (required)' if pname in req else ''
            _out(ctx, f'    {pname:<20}  {pdef.get("type","?")}  {pdef.get("description","")}{r}')
        _out(ctx, '')
        return CommandResult()

    _heading(ctx, f'Tools ({len(registry)} total)')
    # Group by category
    groups = {
        'File':    ['read_file','write_file','edit_file','patch_file','move_file','delete_file','make_dir','glob_search','grep_search','list_dir','notebook_edit'],
        'Git':     ['git_status','git_diff','git_log','git_commit'],
        'Shell':   ['bash','run_tests','sleep'],
        'Web':     ['web_fetch','web_search','search_status','search_list_providers','search_activate_provider'],
        'Memory':  ['memory_write','memory_read','memory_list','todo_write'],
        'Lattice': ['lattice_solve','lattice_boolean_solve','lattice_sector_solve','lattice_maxent','lattice_nn_predict'],
        'Agent':   ['delegate_agent','self_score','ask_user_question','image_read'],
        'Tasks':   ['task_create','task_list','task_get','task_update','task_start','task_complete','task_block','task_cancel','task_next'],
        'Plan':    ['plan_get','update_plan','plan_clear'],
        'Team':    ['team_list','team_get','team_create','team_delete','send_message','team_messages'],
        'Other':   [],
    }
    assigned = set(t for g in groups.values() for t in g)
    groups['Other'] = [n for n in sorted(registry) if n not in assigned]

    for group, names in groups.items():
        available = [n for n in names if n in registry]
        if not available:
            continue
        _out(ctx, f'\n  {group}')
        for name in available:
            _out(ctx, f'    /{name}')
    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /git
# ---------------------------------------------------------------------------

@_cmd('git', help='Quick git status')
def _git(args: list[str], ctx: CommandContext) -> CommandResult:
    cwd = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    try:
        rc = subprocess.run(
            ['git', 'status', '--short', '--branch'],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        out = rc.stdout.strip()
        _heading(ctx, 'Git Status')
        for line in out.splitlines():
            _out(ctx, f'  {line}')
        _out(ctx, '')
    except Exception as e:
        _out(ctx, f'git error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /diff
# ---------------------------------------------------------------------------

@_cmd('diff', help='Show unstaged git diff', usage='diff [path]')
def _diff(args: list[str], ctx: CommandContext) -> CommandResult:
    cwd  = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    cmd  = ['git', 'diff', '--'] + (args or [])
    try:
        rc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)
        out = rc.stdout.strip()
        if not out:
            _out(ctx, '  no unstaged changes')
        else:
            lines = out.splitlines()[:200]
            _heading(ctx, 'Diff')
            for line in lines:
                _out(ctx, f'  {line}')
            if len(out.splitlines()) > 200:
                _out(ctx, f'  … ({len(out.splitlines()) - 200} more lines)')
        _out(ctx, '')
    except Exception as e:
        _out(ctx, f'diff error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /log
# ---------------------------------------------------------------------------

@_cmd('log', help='Show recent git log', usage='log [n=15]')
def _log(args: list[str], ctx: CommandContext) -> CommandResult:
    cwd   = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    limit = args[0] if args else '15'
    try:
        rc = subprocess.run(
            ['git', 'log', '--oneline', f'-{limit}'],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        _heading(ctx, f'Log (last {limit})')
        for line in rc.stdout.strip().splitlines():
            _out(ctx, f'  {line}')
        _out(ctx, '')
    except Exception as e:
        _out(ctx, f'log error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /commit
# ---------------------------------------------------------------------------

@_cmd('commit', help='Quick commit with message', usage='commit <message>')
def _commit(args: list[str], ctx: CommandContext) -> CommandResult:
    if not args:
        _out(ctx, 'usage: /commit <message>')
        return CommandResult()
    msg = ' '.join(args)
    cwd = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    try:
        subprocess.run(['git', 'add', '-u'], cwd=cwd, check=True, capture_output=True)
        rc = subprocess.run(
            ['git', 'commit', '-m', msg],
            cwd=cwd, capture_output=True, text=True,
        )
        out = rc.stdout.strip() or rc.stderr.strip()
        _out(ctx, out)
    except Exception as e:
        _out(ctx, f'commit error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /run
# ---------------------------------------------------------------------------

@_cmd('run', help='Run tests', usage='run [path] [-- -k pattern]')
def _run(args: list[str], ctx: CommandContext) -> CommandResult:
    cwd     = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    path    = args[0] if args else 'tests/'
    k_args  = []
    if '--' in args:
        k_args = args[args.index('--') + 1:]
        path   = args[0] if args.index('--') > 0 else 'tests/'

    cmd = ['python3', '-m', 'pytest', '-v', '--tb=short', '-q', path] + k_args
    _heading(ctx, f'Tests: {path}')
    try:
        rc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
        out = rc.stdout + rc.stderr
        # Show last 60 lines
        lines = out.strip().splitlines()
        for line in lines[-60:]:
            _out(ctx, f'  {line}')
        _out(ctx, '')
    except subprocess.TimeoutExpired:
        _out(ctx, '  tests timed out (120s)')
    except Exception as e:
        _out(ctx, f'  error: {e}')
    return CommandResult()


# ---------------------------------------------------------------------------
# /doctor
# ---------------------------------------------------------------------------

@_cmd('doctor', help='Check Latti setup and dependencies')
def _doctor(args: list[str], ctx: CommandContext) -> CommandResult:
    _heading(ctx, 'Doctor')

    checks = []

    # Python version
    pv = sys.version.split()[0]
    checks.append(('python', pv, True))

    # git
    try:
        gv = subprocess.check_output(['git', '--version'], text=True).strip()
        checks.append(('git', gv, True))
    except Exception:
        checks.append(('git', 'not found', False))

    # patch (for patch_file tool)
    pv2 = shutil.which('patch')
    checks.append(('patch', pv2 or 'not found', bool(pv2)))

    # API key
    model = getattr(ctx.agent.model_config, 'model', '')
    api_key = getattr(ctx.agent.model_config, 'api_key', '') or ''
    key_ok = bool(api_key and len(api_key) > 10)
    checks.append(('api_key', f'{"set" if key_ok else "missing"} ({model})', key_ok))

    # memory dir
    mem_dir = pathlib.Path.home() / '.latti' / 'memory'
    mem_ok  = mem_dir.exists() or True  # it gets created on first write
    n_entries = len(list(mem_dir.glob('*.md'))) if mem_dir.exists() else 0
    checks.append(('memory', f'{n_entries} entries in ~/.latti/memory/', True))

    # verra kernel
    try:
        import urllib.request
        urllib.request.urlopen('http://localhost:8400/health', timeout=2)
        checks.append(('verra kernel', 'running :8400', True))
    except Exception:
        checks.append(('verra kernel', 'offline (optional)', None))

    # session
    checks.append(('session', ctx.active_session_id or 'none', True))
    checks.append(('turns', str(ctx.turn_count), True))
    checks.append(('cost', f'${ctx.cumulative_cost:.4f}', True))

    for name, value, ok in checks:
        if ok is True:
            icon = '✓'
        elif ok is False:
            icon = '✗'
        else:
            icon = '~'
        _out(ctx, f'  {icon}  {name:<20}  {value}')

    _out(ctx, '')
    return CommandResult()


# ---------------------------------------------------------------------------
# /heal
# ---------------------------------------------------------------------------

@_cmd('heal', help='Manually trigger TUI layout heal (re-pin footer)')
def _heal(args: list[str], ctx: CommandContext) -> CommandResult:
    if ctx.use_tui:
        ctx.tui_heal.heal()
        _out(ctx, '  TUI healed')
    else:
        _out(ctx, '  not in TUI mode')
    return CommandResult()


# ---------------------------------------------------------------------------
# /version
# ---------------------------------------------------------------------------

@_cmd('version', aliases=['ver'], help='Show Latti version and git revision')
def _version(args: list[str], ctx: CommandContext) -> CommandResult:
    cwd = str(getattr(ctx.agent.runtime_config, 'cwd', '.'))
    _heading(ctx, 'Version')
    try:
        rev = subprocess.check_output(
            ['git', 'log', '--oneline', '-1'],
            cwd=cwd, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        branch = subprocess.check_output(
            ['git', 'branch', '--show-current'],
            cwd=cwd, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        _out(ctx, f'  branch   {branch}')
        _out(ctx, f'  commit   {rev}')
    except Exception:
        _out(ctx, '  (git info unavailable)')
    _out(ctx, f'  python   {sys.version.split()[0]}')
    _out(ctx, f'  tools    {_count_tools()} registered')
    _out(ctx, '')
    return CommandResult()


def _count_tools() -> int:
    try:
        from .agent_tools import default_tool_registry
        return len(default_tool_registry())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# /exit  /quit
# ---------------------------------------------------------------------------

@_cmd('exit', aliases=['quit', 'q'], help='Exit Latti')
def _exit(args: list[str], ctx: CommandContext) -> CommandResult:
    return CommandResult(exit_session=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_command(text: str) -> bool:
    """Return True only if text is a slash command registered in OUR handler.

    Unknown /commands fall through to agent_slash_commands (runtime level)
    which handles /mcp, /worktree, /lsp, /context, /config, /remote etc.
    Previously this returned True for ALL /x which silently swallowed those.
    """
    parts = text.strip().lstrip('/').split()
    if not parts:
        return False
    return parts[0].lower() in _COMMANDS


def handle_command(text: str, ctx: CommandContext) -> CommandResult:
    """Parse and execute a slash command.  Never raises."""
    parts = text.strip().lstrip('/').split()
    if not parts:
        return CommandResult()

    name = parts[0].lower()
    args = parts[1:]

    entry = _COMMANDS.get(name)
    if not entry:
        _out(ctx, f'  unknown command: /{name}  (try /help)')
        return CommandResult()

    try:
        return entry['fn'](args, ctx) or CommandResult()
    except Exception as e:
        _out(ctx, f'  /{name} error: {e}')
        return CommandResult()
