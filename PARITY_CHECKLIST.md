# Parity Checklist Against npm `src`

This document tracks what is already implemented in Python and what is still missing compared with the upstream npm runtime.

This is a functionality-oriented checklist, not a line-by-line source equivalence claim. Large parts of the mirrored Python workspace still act as inventory or scaffolding, while the working Python runtime currently lives mainly in [`src/agent_runtime.py`](src/agent_runtime.py), [`src/query_engine.py`](src/query_engine.py), [`src/agent_tools.py`](src/agent_tools.py), [`src/agent_prompting.py`](src/agent_prompting.py), [`src/agent_context.py`](src/agent_context.py), [`src/agent_manager.py`](src/agent_manager.py), [`src/plugin_runtime.py`](src/plugin_runtime.py), [`src/agent_slash_commands.py`](src/agent_slash_commands.py), and [`src/openai_compat.py`](src/openai_compat.py).

## 1. Core Agent Runtime

Done:

- [x] One-shot agent loop with iterative tool calling
- [x] OpenAI-compatible `chat/completions` client
- [x] Streaming token-by-token assistant output
- [x] Local-model execution against `vLLM`
- [x] Local-model execution through `Ollama`
- [x] Local-model execution through `LiteLLM Proxy`
- [x] Transcript-aware session object for the Python runtime
- [x] Session save and resume support
- [x] Configurable max-turn execution
- [x] Permission-aware tool execution
- [x] Structured output / JSON schema request mode
- [x] Cost tracking and usage budget enforcement
- [x] Scratchpad directory integration
- [x] File history journaling for write/edit/shell tool actions
- [x] Incremental `bash` tool-result streaming events
- [x] Incremental tool-result streaming for read-only text tools
- [x] Incremental tool-result streaming across the current Python text tool surface
- [x] Mutable tool transcript updates during tool execution
- [x] Transcript mutation history for replaced/tombstoned messages
- [x] Assistant streaming and tool-call transcript mutation history
- [x] Structured transcript block export for messages, tool calls, and tool results
- [x] Resume-time file-history replay reminders
- [x] Resume-time file-history snapshot previews for file edits
- [x] File-history snapshot ids and replay summaries for file edits
- [x] File-history result previews for shell and delegated-tool entries
- [x] Truncated-response continuation flow for `finish_reason=length`
- [x] Basic snipping of older tool/tool-call messages for context control
- [x] Basic automatic compact-boundary insertion with preserved recent tail
- [x] Reactive compaction retry after prompt-too-long backend failures
- [x] Reasoning-token budget enforcement
- [x] Tool-call and delegated-task budget enforcement
- [x] Basic nested-agent delegation tool
- [x] Sequential multi-subtask delegation with parent-context carryover
- [x] Basic agent-manager lineage tracking for nested agents
- [x] Managed agent-group membership tracking with child indices
- [x] Delegated child-session resume by saved session id
- [x] Agent-manager tracking for resumed child-session lineage
- [x] Plugin-cache discovery and prompt-context injection
- [x] Manifest-based plugin runtime discovery
- [x] Manifest-defined plugin hooks for before-prompt and after-turn runtime injection
- [x] Manifest-defined plugin tool aliases over base runtime tools
- [x] Manifest-defined executable virtual tools
- [x] Manifest-defined plugin tool blocking
- [x] Manifest-defined plugin `beforeTool` guidance
- [x] Manifest-defined plugin tool-result guidance injected back into the transcript
- [x] Compaction metadata with compacted message ids
- [x] Compaction metadata with preserved-tail ids and compaction depth
- [x] Compaction metadata with compacted/preserved lineage ids and revision summaries
- [x] Snipped-message metadata with source role/kind lineage
- [x] Snipped-message metadata with source lineage id and revision
- [x] Resume-time compaction / snipping replay reminder
- [x] Query-engine facade that can drive the real Python runtime agent
- [x] Query-engine runtime event counters and transcript-kind summaries
- [x] Query-engine runtime mutation counters
- [x] Query-engine stream-level runtime summary event
- [x] Query-engine transcript-store compaction summaries
- [x] Delegate-group and delegated-subtask runtime events
- [x] Query-engine runtime orchestration summaries for group status and child stop reasons
- [x] Query-engine runtime context-reduction summaries
- [x] Query-engine runtime lineage summaries
- [x] Query-engine runtime resumed-child orchestration summaries

Missing:

- [ ] Full partial tool-result streaming parity across the complete upstream/npm tool surface
- [ ] Full rich transcript mutation behavior like the npm runtime
- [ ] Full reasoning budgets and task budgets parity
- [ ] Full multi-agent orchestration parity beyond sequential grouped delegation and resumed-child flows
- [ ] Full file history snapshots and replay flows beyond the current preview/id-based implementation
- [ ] Full executable plugin lifecycle beyond manifest-driven guidance, blocking, aliases, and virtual tools
- [ ] Full session compaction / snipping parity beyond lineage-aware summaries and replay reminders
- [ ] Full `QueryEngine.ts` parity

## 2. CLI Entrypoints And Runtime Modes

Done:

- [x] Python CLI entrypoint
- [x] `agent` command
- [x] `agent-resume` command
- [x] `agent-prompt` command
- [x] `agent-context` command
- [x] `agent-context-raw` command
- [x] Inventory/helper commands such as `summary`, `manifest`, `commands`, and `tools`

Missing:

- [ ] Daemon worker mode
- [ ] Background session mode
- [ ] Session process listing (`ps`)
- [ ] Background session logs
- [ ] Background attach flow
- [ ] Background kill flow
- [ ] Remote-control / bridge runtime mode
- [ ] Browser/native-host runtime mode
- [ ] Computer-use MCP mode
- [ ] Template job mode
- [ ] Environment runner mode
- [ ] Self-hosted runner mode
- [ ] tmux fast paths
- [ ] Worktree fast paths at the CLI entrypoint level
- [ ] Full `entrypoints/cli.tsx` and `entrypoints/init.ts` parity

## 3. Prompt Assembly

Done:

- [x] Structured Python system prompt builder
- [x] Intro/system/task/tool/tone/output sections
- [x] Session-specific prompt guidance
- [x] Environment-aware prompt sections
- [x] User context reminder injection
- [x] Custom system prompt override and append support

Missing:

- [ ] Full parity with `constants/prompts.ts`
- [ ] Hook instruction sections
- [ ] MCP instruction sections
- [ ] Model-family-specific prompt variations
- [ ] Output-style variants
- [ ] Language-control sections
- [ ] Scratchpad prompt instructions
- [ ] More exact autonomous/proactive behavior sections
- [ ] Growthbook / feature-gated prompt sections
- [ ] Cyber / risk sections used upstream

## 4. Context Building And Memory

Done:

- [x] Current working directory snapshot
- [x] Shell / platform / date capture
- [x] Git status snapshot
- [x] `CLAUDE.md` discovery
- [x] Extra directory injection through `--add-dir`
- [x] Session context usage report
- [x] Raw context inspection command
- [x] Plugin cache snapshot injection
- [x] Manifest-based plugin runtime summary injection

Missing:

- [ ] Tokenizer-accurate context accounting
- [ ] Full parity with `utils/queryContext.ts`
- [ ] Rich memory prompt loading
- [ ] Internal permission-aware memory handling
- [ ] Resume-aware prompt cache shaping used upstream
- [ ] More exact context cache invalidation rules
- [ ] Session context analysis parity
- [ ] Full memory subsystem parity

## 5. Slash Commands

Done:

- [x] `/help`
- [x] `/commands`
- [x] `/context`
- [x] `/usage`
- [x] `/context-raw`
- [x] `/env`
- [x] `/prompt`
- [x] `/system-prompt`
- [x] `/permissions`
- [x] `/model`
- [x] `/tools`
- [x] `/memory`
- [x] `/status`
- [x] `/session`
- [x] `/clear`

Missing:

- [ ] Full npm slash-command surface
- [ ] Slash commands backed by MCP integration
- [ ] Slash commands tied to task/plan systems
- [ ] Slash commands tied to remote/background sessions
- [ ] Slash commands with richer interactive behavior
- [ ] Slash commands tied to plugins and bundled skills
- [ ] Slash commands tied to settings, config, and account state

## 6. Built-in Tools

Done:

- [x] `list_dir`
- [x] `read_file`
- [x] `write_file`
- [x] `edit_file`
- [x] `glob_search`
- [x] `grep_search`
- [x] `bash`

Missing:

- [ ] Agent spawning tool
- [ ] Skill tool
- [ ] Notebook edit tool
- [ ] Web fetch tool
- [ ] Web search tool
- [ ] Todo write tool
- [ ] Ask-user-question tool
- [ ] LSP tool
- [ ] MCP resource listing tool
- [ ] MCP resource read tool
- [ ] Tool search tool
- [ ] Config tool
- [ ] Task create/get/update/list tools
- [ ] Team create/delete tools
- [ ] Send-message tool
- [ ] Terminal capture tool
- [ ] Browser tool
- [ ] Workflow tool
- [ ] Remote trigger tool
- [ ] Sleep / cron tools
- [ ] PowerShell tool parity
- [ ] Worktree enter/exit tools
- [ ] Full `tools.ts` parity

## 7. Commands And Task Systems

Done:

- [x] Basic local command dispatch for the Python runtime
- [x] Inventory view of mirrored command names

Missing:

- [ ] Real implementation of the larger upstream command tree
- [ ] Task orchestration system
- [ ] Planner / task execution parity
- [ ] Team / collaboration command flows
- [ ] Background task management
- [ ] Command-specific session behaviors
- [ ] Full `src/commands/*` parity
- [ ] Full `src/tasks/*` parity

## 8. Permissions, Hooks, And Policy

Done:

- [x] Read-only default mode
- [x] Write-gated mode
- [x] Shell-gated mode
- [x] Unsafe mode for destructive shell actions

Missing:

- [ ] Hooks runtime
- [ ] Tool-permission workflow parity
- [ ] Policy limit loading
- [ ] Managed settings loading
- [ ] Trust-gated initialization
- [ ] Safe environment loading parity
- [ ] More exact denial tracking
- [ ] Hook-config management
- [ ] Full hooks and policy parity

## 9. MCP, Plugins, And Skills

Done:

- [x] Placeholder mirrored package layout for plugins, skills, services, and remote subsystems

Missing:

- [ ] Real MCP client support
- [ ] MCP server integration
- [ ] MCP resource listing and reading
- [ ] MCP-backed tools
- [ ] Plugin discovery and loading
- [ ] Bundled plugin support
- [ ] Plugin lifecycle management
- [ ] Plugin update/cache behavior
- [ ] Skill discovery and execution parity
- [ ] Bundled skill support
- [ ] Full plugin and skill parity

## 10. Interactive UI / REPL / TUI

Done:

- [x] Non-interactive CLI execution
- [x] Transcript printing for debugging

Missing:

- [ ] Interactive REPL parity
- [ ] Ink/TUI component parity
- [ ] Screen system parity
- [ ] Keyboard interaction parity
- [ ] Interactive status panes
- [ ] Approval UI flows
- [ ] Rich incremental rendering
- [ ] Full `components`, `screens`, and `ink` parity

## 11. Remote, Background, And Team Features

Done:

- [x] Session save/resume on local disk

Missing:

- [ ] Remote execution modes
- [ ] Background agent processes
- [ ] Background attach/log/kill workflows
- [ ] Team runtime features
- [ ] Team messaging features
- [ ] Shared remote state
- [ ] Upstream proxy runtime integration
- [ ] Full `remote`, `server`, `bridge`, `upstreamproxy`, and team parity

## 12. Editor, Platform, And Native Integrations

Done:

- [x] Standard shell-based local workflow

Missing:

- [ ] Voice mode parity
- [ ] VIM mode parity
- [ ] Keybinding parity
- [ ] Notification hooks
- [ ] Native TypeScript / platform helper parity
- [ ] JetBrains/editor integration parity
- [ ] Browser/native host integrations
- [ ] Platform-specific startup/shutdown logic

## 13. Services And Internal Subsystems

Done:

- [x] Minimal internal service layer required by the current Python runtime

Missing:

- [ ] Real service implementations for the mirrored `services` package
- [ ] Config service parity
- [ ] Account/auth service parity
- [ ] Analytics/telemetry service parity
- [ ] Growthbook/feature-flag parity
- [ ] GitHub / git helper parity
- [ ] Sandbox/settings utility parity
- [ ] Todo/task utility parity
- [ ] Internal helpers used by the upstream runtime

## 14. Mirrored Workspace Versus Working Runtime

Working Python runtime today:

- [x] `src/main.py`
- [x] `src/agent_runtime.py`
- [x] `src/agent_tools.py`
- [x] `src/agent_prompting.py`
- [x] `src/agent_context.py`
- [x] `src/agent_context_usage.py`
- [x] `src/agent_session.py`
- [x] `src/agent_slash_commands.py`
- [x] `src/agent_types.py`
- [x] `src/openai_compat.py`
- [x] `src/session_store.py`
- [x] `src/permissions.py`

Mirrored inventory / scaffold areas that still need real implementation work:

- [ ] `src/commands.py`
- [ ] `src/tools.py`
- [ ] `src/query_engine.py`
- [ ] `src/runtime.py`
- [ ] `src/services/*`
- [ ] `src/plugins/*`
- [ ] `src/remote/*`
- [ ] `src/voice/*`
- [ ] `src/vim/*`
- [ ] Large parts of the rest of the mirrored package tree

## 15. High-Priority Next Steps

- [ ] Expand the real Python tool registry toward upstream `tools.ts`
- [ ] Replace more snapshot-backed mirrored modules with working runtime code
- [ ] Implement real MCP support
- [ ] Implement hooks and policy flows
- [ ] Build a real interactive REPL / TUI
- [ ] Add tokenizer-accurate context accounting
- [ ] Add background and remote session modes
- [ ] Port more of the command/task system
- [ ] Close the gap between the mirrored workspace and the working runtime
