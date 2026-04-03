<p align="center">
  <img src="images/logo.png" alt="Claw Code Agent logo" width="420" />
</p>

<h1 align="center">Claw Code Agent</h1>

<p align="center">
  <em>A Python reimplementation of the Claude Code agent architecture — local models, full control, zero dependencies.</em>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://github.com/HarnessLab/claw-code-agent"><img src="https://img.shields.io/badge/repo-HarnessLab%2Fclaw--code--agent-181717?logo=github" alt="GitHub"></a>
  <a href="https://docs.vllm.ai/"><img src="https://img.shields.io/badge/backend-vLLM-FF6F00?logo=lightning&logoColor=white" alt="vLLM"></a>
  <a href="https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct"><img src="https://img.shields.io/badge/model-Qwen3--Coder-FFD21E?logo=huggingface&logoColor=black" alt="Qwen3-Coder"></a>
  <img src="https://img.shields.io/badge/dependencies-zero-brightgreen" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
  <img src="https://img.shields.io/badge/license-open--source-green" alt="License">
</p>

---

## 📢 What's New

> **April 2026 — Major Update**

| | Feature | Details |
|---|---------|---------|
| 🆕 | **Interactive Chat Mode** | New `agent-chat` command — multi-turn REPL with `/exit` to quit |
| 🆕 | **Streaming Output** | Token-by-token streaming with `--stream` flag |
| 🆕 | **Plugin Runtime** | Full manifest-based plugin system — hooks, tool aliases, virtual tools, tool blocking |
| 🆕 | **Nested Agent Delegation** | Delegate subtasks to child agents with dependency-aware topological batching |
| 🆕 | **Agent Manager** | Lineage tracking, group membership, batch summaries for nested agents |
| 🆕 | **Cost Tracking & Budgets** | Token budgets, cost budgets, tool-call limits, model-call limits, session-turn limits |
| 🆕 | **Structured Output** | JSON schema response mode with `--response-schema-file` |
| 🆕 | **Context Compaction** | Auto-snip, auto-compact, and reactive compaction on prompt-too-long errors |
| 🆕 | **File History Replay** | Journaling of file edits with snapshot IDs, replay summaries on session resume |
| 🆕 | **Truncation Continuation** | Automatic continuation when model response is cut off (`finish_reason=length`) |
| 🆕 | **Ollama Support** | Works out of the box with Ollama's OpenAI-compatible API |
| 🆕 | **LiteLLM Proxy Support** | Route through LiteLLM Proxy to any provider |
| 🆕 | **OpenRouter Support** | Cloud API gateway — access OpenAI, Anthropic, Google models via one endpoint |
| 🆕 | **Query Engine** | Runtime event counters, transcript summaries, orchestration reports |
| 🆕 | **Testing Guide** | Comprehensive [TESTING_GUIDE.md](TESTING_GUIDE.md) with commands for every feature |
| 🆕 | **Parity Checklist** | Full [PARITY_CHECKLIST.md](PARITY_CHECKLIST.md) tracking implementation status vs npm source |

---

## 📖 About

This repository reimplements the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) npm agent architecture **entirely in Python**, designed to run with **local open-source models** via an OpenAI-compatible API server.

Built on the public porting workspace from [instructkr/claw-code](https://github.com/instructkr/claw-code), the active development lives at [HarnessLab/claw-code-agent](https://github.com/HarnessLab/claw-code-agent).

> **Goal:** Not to ship the original npm source, but to reimplement the full agent flow in Python — prompt assembly, context building, slash commands, tool calling, session persistence, and local model execution.
>
> **Zero external dependencies** — just Python's standard library.

<p align="center">
  <img src="images/demo_2.gif" alt="Claw Code Agent demo" width="900" />
</p>

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🤖 **Agent Loop** | Full agentic coding loop with tool calling and iterative reasoning |
| 💬 **Interactive Chat** | Multi-turn REPL via `agent-chat` with session continuity |
| 🧰 **Core Tools** | File read / write / edit, glob search, grep search, shell execution |
| 🔌 **Plugin Runtime** | Manifest-based plugins with hooks, aliases, virtual tools, and tool blocking |
| 🪆 **Nested Delegation** | Delegate subtasks to child agents with dependency-aware topological batching |
| 📡 **Streaming** | Token-by-token streaming output with `--stream` |
| 💬 **Slash Commands** | Local commands: `/help`, `/context`, `/tools`, `/memory`, `/status`, `/model`, and more |
| 🧠 **Context Engine** | Automatic context building with CLAUDE.md discovery, compaction, and snipping |
| 🔄 **Session Persistence** | Save and resume agent sessions with file-history replay |
| 💰 **Cost & Budget Control** | Token budgets, cost limits, tool-call caps, model-call caps |
| 📋 **Structured Output** | JSON schema response mode for programmatic use |
| 🔐 **Permission System** | Granular control: `--allow-write`, `--allow-shell`, `--unsafe` |
| 🏗️ **OpenAI-Compatible** | Works with vLLM, Ollama, LiteLLM Proxy, OpenRouter — any OpenAI-compatible API |
| 🐉 **Qwen3-Coder** | First-class support for `Qwen3-Coder-30B-A3B-Instruct` via vLLM |
| 📦 **Zero Dependencies** | Pure Python standard library — nothing to install |

---

## 📋 Roadmap

### 📚 Documentation

| Document | Description |
|----------|-------------|
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | Step-by-step commands to verify every feature |
| [PARITY_CHECKLIST.md](PARITY_CHECKLIST.md) | Full implementation status vs the npm source |

### ✅ Done

- [x] Python CLI agent loop
- [x] Interactive chat mode (`agent-chat`) with multi-turn REPL
- [x] OpenAI-compatible local model backend
- [x] Qwen3-Coder support through vLLM with `qwen3_xml` tool parser
- [x] Ollama, LiteLLM Proxy, and OpenRouter backends
- [x] Core tools: `list_dir`, `read_file`, `write_file`, `edit_file`, `glob_search`, `grep_search`, `bash`
- [x] Context building and `/context`-style usage reporting
- [x] Slash commands: `/help`, `/context`, `/context-raw`, `/prompt`, `/permissions`, `/model`, `/tools`, `/memory`, `/status`, `/clear`
- [x] Session persistence and `agent-resume` flow
- [x] Permission system (read-only, write, shell, unsafe tiers)
- [x] Streaming token-by-token assistant output
- [x] Truncated-response continuation flow
- [x] Auto-snip and auto-compact context reduction
- [x] Reactive compaction retry on prompt-too-long errors
- [x] Cost tracking and usage budget enforcement
- [x] Token, tool-call, model-call, and session-turn budgets
- [x] Structured output / JSON schema response mode
- [x] File history journaling with snapshot IDs and replay summaries
- [x] Nested agent delegation with dependency-aware topological batching
- [x] Agent manager with lineage tracking and group membership
- [x] Plugin runtime: manifest discovery, hooks, aliases, virtual tools, tool blocking
- [x] Plugin lifecycle hooks: resume, persist, delegate phases
- [x] Plugin session-state persistence and resume restoration
- [x] Query engine facade driving the real Python runtime
- [x] Compaction metadata with lineage IDs and revision summaries
- [x] Unit tests for the Python runtime
- [x] `pyproject.toml` packaging with `setuptools`

### 🔲 In Progress

- [ ] Full MCP server support
- [ ] Full slash-command parity with npm runtime
- [ ] Full interactive REPL / TUI behavior
- [ ] Exact tokenizer-accurate context accounting
- [ ] Hooks system parity
- [ ] Remote runtime modes (SSH, teleport, deep-link)
- [ ] Voice and VIM modes
- [ ] Editor and platform integrations
- [ ] Background and team features

---

## 🏗️ Architecture

```text
claw-code/
├── README.md
├── TESTING_GUIDE.md              # How to test every feature
├── PARITY_CHECKLIST.md           # Implementation status vs npm source
├── pyproject.toml
├── .gitignore
├── images/
│   └── logo.png
├── src/                          # Python implementation (75+ modules)
│   ├── main.py                   # CLI entry point & argument parsing
│   ├── agent_runtime.py          # Core agent loop (LocalCodingAgent)
│   ├── agent_tools.py            # Tool definitions & execution engine
│   ├── agent_prompting.py        # System prompt assembly
│   ├── agent_context.py          # Context building & CLAUDE.md discovery
│   ├── agent_context_usage.py    # Context usage estimation & reporting
│   ├── agent_session.py          # Session state management
│   ├── agent_slash_commands.py   # Local slash command processing
│   ├── agent_manager.py          # Nested agent lineage & group tracking
│   ├── agent_types.py            # Shared dataclasses & type definitions
│   ├── openai_compat.py          # OpenAI-compatible API client (streaming)
│   ├── plugin_runtime.py         # Plugin manifest, hooks, aliases, virtual tools
│   ├── agent_plugin_cache.py     # Plugin discovery & prompt injection cache
│   ├── session_store.py          # Session serialization & persistence
│   ├── transcript.py             # Transcript block export & mutation tracking
│   ├── query_engine.py           # Query engine facade & runtime orchestration
│   ├── permissions.py            # Tool permission filtering
│   ├── cost_tracker.py           # Cost & budget enforcement
│   ├── tools.py                  # Mirrored tool inventory
│   ├── commands.py               # Mirrored command inventory
│   ├── plugins/                  # Plugin subsystem
│   ├── hooks/                    # Hook system (WIP)
│   ├── remote/                   # Remote runtime modes (WIP)
│   ├── voice/                    # Voice mode (WIP)
│   └── vim/                      # VIM mode (WIP)
└── tests/                        # Unit tests
    ├── test_agent_runtime.py
    ├── test_agent_context.py
    ├── test_agent_context_usage.py
    ├── test_agent_prompting.py
    ├── test_agent_slash_commands.py
    ├── test_main.py
    ├── test_query_engine_runtime.py
    └── test_porting_workspace.py
```

---

## 📦 Requirements

| Requirement | Details |
|-------------|---------|
| 🐍 Python | `3.10` or higher |
| 📚 Dependencies | **None** — pure Python standard library |
| 🖥️ Model Server | `vLLM`, `Ollama`, `LiteLLM Proxy`, or `OpenRouter`, with tool calling support |
| 🧠 Model | [`Qwen/Qwen3-Coder-30B-A3B-Instruct`](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) (recommended) |

---

## 🚀 Quick Start

### 1. Start vLLM with Qwen3-Coder

vLLM must be started with automatic tool choice enabled. Use the `qwen3_xml` parser for Qwen3-Coder tool calling:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

Verify the server is running:

```bash
curl http://127.0.0.1:8000/v1/models
```

> 📚 **References:** [vLLM Tool Calling Docs](https://docs.vllm.ai/en/v0.13.0/features/tool_calling/) · [OpenAI-Compatible Server](https://docs.vllm.ai/en/v0.13.0/serving/openai_compatible_server.html)

### Optional: Use Ollama Instead of vLLM

`claw-code-agent` can also work with Ollama because the runtime targets an OpenAI-compatible API. Use a model that supports tool calling well.

Example:

```bash
ollama serve
ollama pull qwen3
```

Then configure:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1
export OPENAI_API_KEY=ollama
export OPENAI_MODEL=qwen3
```

Notes:

- prefer tool-capable models such as `qwen3`
- plain chat-only models are not enough for full agent behavior
- Ollama does not use the `vLLM` parser flags shown above

> 📚 **References:** [Ollama OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility) · [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)

### Optional: Use LiteLLM Proxy

`claw-code-agent` can also work through LiteLLM Proxy because the runtime targets an OpenAI-compatible chat completions API. The routed model still needs to support tool calling for full agent behavior.

Quick start example:

```bash
pip install 'litellm[proxy]'
litellm --model ollama/qwen3
```

LiteLLM Proxy runs on port `4000` by default. Then configure:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:4000
export OPENAI_API_KEY=anything
export OPENAI_MODEL=ollama/qwen3
```

Notes:

- LiteLLM Proxy gives you an OpenAI-style gateway in front of many providers
- tool use still depends on the underlying routed model and provider behavior
- if you configure a LiteLLM master key, use that instead of `anything`

> 📚 **References:** [LiteLLM Docs](https://docs.litellm.ai/) · [LiteLLM Proxy Quick Start](https://docs.litellm.ai/)

### Optional: Use OpenRouter

`claw-code-agent` can also work with [OpenRouter](https://openrouter.ai/), a cloud API gateway that provides access to models from OpenAI, Anthropic, Google, Meta, and others through a single OpenAI-compatible endpoint. No local model server required.

Configure:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-or-v1-your-key-here
export OPENAI_MODEL=openai/gpt-4o-mini
```

Notes:

- sign up at [openrouter.ai](https://openrouter.ai/) and create an API key under [Keys](https://openrouter.ai/keys)
- model names use the `provider/model` format (e.g. `anthropic/claude-sonnet-4`, `openai/gpt-4o`, `google/gemini-2.5-pro`)
- tool calling support varies by model — check the [model list](https://openrouter.ai/models) for capabilities
- this sends your conversation (including file contents and shell output) to OpenRouter and the upstream provider — do not use with repos containing secrets or sensitive data

> 📚 **References:** [OpenRouter Docs](https://openrouter.ai/docs) · [Supported Models](https://openrouter.ai/models) · [API Keys](https://openrouter.ai/keys)

### 2. Configure Environment

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
```

### Use Another Model With vLLM

If you want to try another model, keep the same `vLLM` server setup and change the `--model` value when you launch `vLLM`.

Example:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model your-model-name \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser your_parser
```

Then update:

```bash
export OPENAI_MODEL=your-model-name
```

Notes:

- the documented path in this repository is `vLLM`
- the model must support tool calling well enough for agent use
- some model families require a different `--tool-call-parser`
- slash commands such as `/help`, `/context`, and `/tools` are local and do not require the model server

### 3. Run the Agent

```bash
# Read-only question
python3 -m src.main agent \
  "Read src/agent_runtime.py and summarize how the loop works." \
  --cwd .

# Write-enabled task
python3 -m src.main agent \
  "Create TEST_QWEN_AGENT.md with one line: test ok" \
  --cwd . --allow-write

# Shell-enabled task
python3 -m src.main agent \
  "Run pwd and ls src, then summarize the result." \
  --cwd . --allow-shell

# Interactive chat mode
python3 -m src.main agent-chat --cwd .

# Streaming output
python3 -m src.main agent \
  "Explain the current architecture." \
  --cwd . --stream
```

---

## 🛠️ Usage

### Agent Commands

| Command | Description |
|---------|-------------|
| `agent <prompt>` | Run the agent with a prompt |
| `agent-chat [prompt]` | Start interactive multi-turn chat mode |
| `agent-prompt` | Show the assembled system prompt |
| `agent-context` | Show estimated context usage |
| `agent-context-raw` | Show the raw context snapshot |
| `agent-resume <id> <prompt>` | Resume a saved session |

### CLI Flags

| Flag | Description |
|------|-------------|
| `--cwd <path>` | Set the workspace directory |
| `--model <name>` | Override the model name |
| `--base-url <url>` | Override the API base URL |
| `--allow-write` | Allow the agent to modify files |
| `--allow-shell` | Allow the agent to execute shell commands |
| `--unsafe` | Allow destructive shell operations |
| `--stream` | Enable token-by-token streaming output |
| `--show-transcript` | Print the full message transcript |
| `--system-prompt <text>` | Set a custom system prompt |
| `--append-system-prompt <text>` | Append to the system prompt |
| `--add-dir <path>` | Add extra directories to context |

### Budget & Limit Flags

| Flag | Description |
|------|-------------|
| `--max-total-tokens <n>` | Total token budget |
| `--max-input-tokens <n>` | Input token budget |
| `--max-output-tokens <n>` | Output token budget |
| `--max-reasoning-tokens <n>` | Reasoning token budget |
| `--max-budget-usd <n>` | Maximum cost in USD |
| `--max-tool-calls <n>` | Maximum tool calls per run |
| `--max-delegated-tasks <n>` | Maximum delegated subtasks |
| `--max-model-calls <n>` | Maximum model API calls |
| `--max-session-turns <n>` | Maximum session turns |
| `--input-cost-per-million <n>` | Input token pricing |
| `--output-cost-per-million <n>` | Output token pricing |

### Context Control Flags

| Flag | Description |
|------|-------------|
| `--auto-snip-threshold <n>` | Auto-snip older messages at this token count |
| `--auto-compact-threshold <n>` | Auto-compact at this token count |
| `--compact-preserve-messages <n>` | Messages to preserve during compaction |
| `--disable-claude-md` | Disable CLAUDE.md discovery |

### Structured Output Flags

| Flag | Description |
|------|-------------|
| `--response-schema-file <path>` | JSON schema file for structured output |
| `--response-schema-name <name>` | Schema name identifier |
| `--response-schema-strict` | Enforce strict schema validation |

### Slash Commands

These are handled **locally** before the model loop:

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/commands` | Show built-in slash commands |
| `/context` | `/usage` | Show estimated session context usage |
| `/context-raw` | `/env` | Show raw environment & context snapshot |
| `/prompt` | `/system-prompt` | Render the effective system prompt |
| `/permissions` | — | Show active tool permission mode |
| `/model` | — | Show or update the active model |
| `/tools` | — | List registered tools with permission status |
| `/memory` | — | Show loaded CLAUDE.md memory bundle |
| `/status` | `/session` | Show runtime/session status summary |
| `/clear` | — | Clear ephemeral runtime state |

```bash
python3 -m src.main agent "/help"
python3 -m src.main agent "/context" --cwd .
python3 -m src.main agent "/tools" --cwd .
python3 -m src.main agent "/status" --cwd .
```

### Utility Commands

```bash
python3 -m src.main summary            # Workspace summary
python3 -m src.main manifest           # Workspace manifest
python3 -m src.main commands --limit 10 # Command inventory
python3 -m src.main tools --limit 10    # Tool inventory
```

---

## 🔧 Built-in Tools

The agent has access to 7 core tools:

| Tool | Description | Permission |
|------|-------------|------------|
| `list_dir` | List files and directories | 🟢 Always |
| `read_file` | Read file contents (with line ranges) | 🟢 Always |
| `write_file` | Write or create files | 🟡 `--allow-write` |
| `edit_file` | Edit files via exact string matching | 🟡 `--allow-write` |
| `glob_search` | Find files by glob pattern | 🟢 Always |
| `grep_search` | Search file contents by regex | 🟢 Always |
| `bash` | Execute shell commands | 🔴 `--allow-shell` |

---

## 🔌 Plugin System

Claw Code Agent supports a **manifest-based plugin runtime**. Drop a `plugin.json` in a `plugins/` subdirectory:

```json
{
  "name": "my-plugin",
  "hooks": {
    "beforePrompt": "Inject guidance into the system prompt.",
    "afterTurn": "Run after each agent turn.",
    "onResume": "Reapply state on session resume.",
    "beforePersist": "Save state before session is saved.",
    "beforeDelegate": "Inject guidance before child agents.",
    "afterDelegate": "Process child agent results."
  },
  "toolAliases": [
    { "name": "my_read", "baseTool": "read_file", "description": "Custom read alias." }
  ],
  "virtualTools": [
    { "name": "my_tool", "description": "A virtual tool.", "responseTemplate": "result: {input}" }
  ]
}
```

> See [TESTING_GUIDE.md](TESTING_GUIDE.md) **Section 13** for full plugin testing commands.

---

## 🪆 Nested Agent Delegation

The agent can delegate subtasks to child agents with full context carryover:

```bash
python3 -m src.main agent \
  "Delegate a subtask to inspect src/agent_runtime.py and return a summary." \
  --cwd . --show-transcript
```

Features:
- Sequential and parallel subtask execution
- Dependency-aware topological batching
- Child-session save and resume
- Agent manager lineage tracking

> See [TESTING_GUIDE.md](TESTING_GUIDE.md) **Section 12** for delegation testing commands.

---

## 🔄 Session Persistence

Each `agent` run automatically saves a resumable session:

```text
session_id=4f2c8c6f9c0e4d7c9c7b1b2a3d4e5f67
session_path=.port_sessions/agent/4f2c8c6f...
```

Resume a previous session:

```bash
python3 -m src.main agent-resume \
  4f2c8c6f9c0e4d7c9c7b1b2a3d4e5f67 \
  "Continue the previous task and finish the missing parts."
```

Resume directly into interactive chat:

```bash
python3 -m src.main agent-chat \
  --resume-session-id <session-id> \
  --cwd .
```

Inspect saved sessions:

```bash
ls -lt .port_sessions/agent
```

> **Note:** Run `agent-resume` from the same `claw-code/` directory where the session was created. A resumed session continues from the saved transcript, not from scratch.

---

## 🧪 Testing

Run the full test suite:

```bash
python3 -m unittest discover -s tests -v
```

Smoke tests:

```bash
python3 -m src.main agent "/help"
python3 -m src.main agent-context --cwd .
python3 -m src.main agent \
  "Read src/agent_session.py and summarize the message flow." \
  --cwd .
```

> 📚 **Full testing guide:** See [TESTING_GUIDE.md](TESTING_GUIDE.md) for step-by-step commands covering all 16 feature areas.

---

## 🔐 Permission Model

Claw Code Agent uses a **tiered permission system** to keep the agent safe by default:

| Tier | Capability | Flag Required |
|------|-----------|---------------|
| **Read-only** | List, read, glob, grep | None (default) |
| **Write** | + file creation and editing | `--allow-write` |
| **Shell** | + shell command execution | `--allow-shell` |
| **Unsafe** | + destructive shell operations | `--unsafe` |

---

## 🔎 Parity Status

The full implementation checklist tracking parity against the npm `src` lives in [PARITY_CHECKLIST.md](PARITY_CHECKLIST.md).

It covers: core runtime, CLI modes, prompt assembly, context/memory, slash commands, tools, permissions, plugins, MCP, REPL/TUI, remote features, editor integrations, and internal subsystems.

---

## ⚠️ Disclaimer

- This repository is a **Python reimplementation** inspired by the Claude Code npm architecture.
- It does **not** ship the original npm source.
- It is **not** affiliated with or endorsed by Anthropic.

---

<p align="center">
  <sub>Built with 🐍 Python · Powered by 🐉 HarnessLab Team.</sub>
</p>
