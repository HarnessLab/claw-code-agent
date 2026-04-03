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
- [x] Session-wide mutation serial tracking across transcript updates
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
- [x] Resume-aware cumulative model-call budgets
- [x] Resume-aware cumulative session usage/cost persistence
- [x] Basic nested-agent delegation tool
- [x] Sequential multi-subtask delegation with parent-context carryover
- [x] Dependency-aware delegated subtasks
- [x] Topological dependency-batch delegation planning
- [x] Basic agent-manager lineage tracking for nested agents
- [x] Managed agent-group membership tracking with child indices
- [x] Agent-manager strategy and batch summary tracking for delegated groups
- [x] Delegated child-session resume by saved session id
- [x] Agent-manager tracking for resumed child-session lineage
- [x] Plugin-cache discovery and prompt-context injection
- [x] Manifest-based plugin runtime discovery
- [x] Manifest-defined plugin hooks for before-prompt and after-turn runtime injection
- [x] Manifest-defined plugin lifecycle hooks for resume, persist, and delegate phases
- [x] Manifest-defined plugin tool aliases over base runtime tools
- [x] Manifest-defined executable virtual tools
- [x] Manifest-defined plugin tool blocking
- [x] Manifest-defined plugin `beforeTool` guidance
- [x] Manifest-defined plugin tool-result guidance injected back into the transcript
- [x] Plugin runtime session-state persistence and resume restoration
- [x] Manifest-based hook/policy runtime discovery
- [x] Hook/policy before-prompt runtime injection
- [x] Hook/policy after-turn runtime events
- [x] Hook/policy tool preflight guidance
- [x] Hook/policy tool blocking
- [x] Hook/policy after-tool guidance
- [x] Hook/policy budget override loading
- [x] Hook/policy safe-environment overlay for shell tools
- [x] Local manifest-backed MCP resource discovery
- [x] Local MCP resource listing and reading
- [x] MCP-backed runtime tools for local resource access
- [x] Local persistent task runtime discovery
- [x] Local task create/get/list/update runtime flows
- [x] Local todo-list replacement runtime flow
- [x] Local persistent plan runtime discovery
- [x] Local plan get/update/clear runtime flows
- [x] Local plan-to-task sync flow
- [x] Compaction metadata with compacted message ids
- [x] Compaction metadata with preserved-tail ids and compaction depth
- [x] Compaction metadata with compacted/preserved lineage ids and revision summaries
- [x] Compaction metadata with source mutation serials and mutation totals
- [x] Snipped-message metadata with source role/kind lineage
- [x] Snipped-message metadata with source lineage id and revision
- [x] Resume-time compaction / snipping replay reminder
- [x] Resume-time compaction replay of source mutation summaries
- [x] Query-engine facade that can drive the real Python runtime agent
- [x] Query-engine runtime event counters and transcript-kind summaries
- [x] Query-engine runtime mutation counters
- [x] Query-engine stream-level runtime summary event
- [x] Query-engine transcript-store compaction summaries
- [x] Delegate-group and delegated-subtask runtime events
- [x] Delegate-batch runtime events and summaries
- [x] Query-engine runtime orchestration summaries for group status and child stop reasons
- [x] Query-engine runtime context-reduction summaries
- [x] Query-engine runtime lineage summaries
- [x] Query-engine runtime resumed-child orchestration summaries

Missing:

- [ ] Full partial tool-result streaming parity across the complete upstream/npm tool surface
- [ ] Full rich transcript mutation behavior like the npm runtime beyond the current lineage, counters, block export, and mutation-serial tracking
- [ ] Full reasoning budgets and task budgets parity beyond the current cumulative model/tool/delegation/session-call enforcement
- [ ] Full multi-agent orchestration parity beyond dependency-aware batched delegation, resumed-child flows, and current agent-manager summaries
- [ ] Full file history snapshots and replay flows beyond the current preview/id-based implementation and delegated-batch replay metadata
- [ ] Full executable plugin lifecycle beyond manifest-driven prompt/tool/session hooks, blocking, aliases, virtual tools, and persisted runtime state
- [ ] Full session compaction / snipping parity beyond lineage-aware summaries, mutation-serial compaction metadata, and replay reminders
- [ ] Full `QueryEngine.ts` parity

## 2. CLI Entrypoints And Runtime Modes

Done:

- [x] Python CLI entrypoint
- [x] `agent` command
- [x] `agent-chat` command
- [x] `agent-resume` command
- [x] `agent-prompt` command
- [x] `agent-context` command
- [x] `agent-context-raw` command
- [x] Local background session mode
- [x] Local background session listing (`agent-ps`)
- [x] Local background session logs (`agent-logs`)
- [x] Local background attach snapshot (`agent-attach`)
- [x] Local background kill flow (`agent-kill`)
- [x] Inventory/helper commands such as `summary`, `manifest`, `commands`, and `tools`

Missing:

- [ ] Daemon worker mode
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
- [x] Local hook/policy guidance section in the Python system prompt
- [x] Local MCP guidance section in the Python system prompt
- [x] Local planning guidance section in the Python system prompt
- [x] Local task guidance section in the Python system prompt

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
- [x] Manifest-based hook/policy summary injection
- [x] Trust-mode, managed-settings, and safe-env context injection
- [x] Manifest-based MCP runtime summary injection
- [x] Manifest-based plan runtime summary injection
- [x] Manifest-based task runtime summary injection

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
- [x] `/mcp`
- [x] `/resources`
- [x] `/resource`
- [x] `/plan`
- [x] `/planner`
- [x] `/tasks`
- [x] `/todo`
- [x] `/task`
- [x] `/prompt`
- [x] `/system-prompt`
- [x] `/permissions`
- [x] `/hooks`
- [x] `/policy`
- [x] `/trust`
- [x] `/model`
- [x] `/tools`
- [x] `/memory`
- [x] `/status`
- [x] `/session`
- [x] `/clear`

Missing:

- [ ] Full npm slash-command surface
- [ ] Slash commands backed by MCP integration
- [ ] Slash commands tied to task/plan systems beyond the current local `/plan`, `/tasks`, and `/task` flows
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
- [x] `mcp_list_resources`
- [x] `mcp_read_resource`
- [x] `plan_get`
- [x] `update_plan`
- [x] `plan_clear`
- [x] `delegate_agent`
- [x] `task_list`
- [x] `task_get`
- [x] `task_create`
- [x] `task_update`
- [x] `todo_write`

Missing:

- [ ] Agent spawning tool parity beyond the current `delegate_agent` runtime tool
- [ ] Skill tool
- [ ] Notebook edit tool
- [ ] Web fetch tool
- [ ] Web search tool
- [ ] Ask-user-question tool
- [ ] LSP tool
- [ ] Tool search tool
- [ ] Config tool
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
- [x] Local persistent task runtime with create/get/list/update flows
- [x] Local todo-list replacement flow
- [x] Local persistent plan runtime with get/update/clear flows
- [x] Local plan-to-task sync flow
- [x] Local background task management for agent worker sessions

Missing:

- [ ] Real implementation of the larger upstream command tree
- [ ] Task orchestration system beyond the current local plan/task sync runtime
- [ ] Planner / task execution parity beyond the current local plan persistence and sync flow
- [ ] Team / collaboration command flows
- [ ] Command-specific session behaviors
- [ ] Full `src/commands/*` parity
- [ ] Full `src/tasks/*` parity

## 8. Permissions, Hooks, And Policy

Done:

- [x] Read-only default mode
- [x] Write-gated mode
- [x] Shell-gated mode
- [x] Unsafe mode for destructive shell actions
- [x] Local hook/policy manifest discovery
- [x] Hook before-prompt and after-turn runtime handling
- [x] Hook/policy tool preflight, deny, and after-tool handling
- [x] Policy budget override loading
- [x] Managed settings loading and reporting
- [x] Safe environment loading for shell tool context
- [x] Trust reporting and hook/policy slash commands
- [x] Permission-denial runtime events for policy/tool blocks

Missing:

- [ ] Tool-permission workflow parity
- [ ] Trust-gated initialization
- [ ] Hook-config management
- [ ] Full hooks and policy parity

## 9. MCP, Plugins, And Skills

Done:

- [x] Placeholder mirrored package layout for plugins, skills, services, and remote subsystems
- [x] Local manifest-backed MCP discovery
- [x] Local MCP resource listing and reading
- [x] MCP-backed runtime tools for local resource access

Missing:

- [ ] Real MCP client support
- [ ] MCP server integration
- [ ] Full MCP-backed tool parity beyond the current local resource list/read tools
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
- [x] Basic interactive REPL-style agent chat loop
- [x] Transcript printing for debugging

Missing:

- [ ] Interactive REPL parity beyond the current basic `agent-chat` loop
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
- [x] Local background agent processes
- [x] Local background attach/log/kill workflows

Missing:

- [ ] Remote execution modes
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
- [x] `src/mcp_runtime.py`
- [x] `src/plan_runtime.py`
- [x] `src/plugin_runtime.py`
- [x] `src/hook_policy.py`
- [x] `src/background_runtime.py`
- [x] `src/task.py`
- [x] `src/task_runtime.py`
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
- [ ] Expand hooks and policy parity beyond the current manifest/runtime implementation
- [ ] Build a real interactive REPL / TUI
- [ ] Add tokenizer-accurate context accounting
- [ ] Expand background session parity beyond the current local worker/log/attach model
- [ ] Add real remote session modes
- [ ] Port more of the command/task system
- [ ] Close the gap between the mirrored workspace and the working runtime
