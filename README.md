<p align="center">
  <img src="images/logo.png" alt="Claw Code Agent logo" width="420" />
</p>

<h1 align="center">Claw Code Agent</h1>

<p align="center">
  <em>A Python reimplementation of the Claude Code agent architecture — local models, full control.</em>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://github.com/HarnessLab/claw-code-agent"><img src="https://img.shields.io/badge/repo-HarnessLab%2Fclaw--code--agent-181717?logo=github" alt="GitHub"></a>
  <a href="https://docs.vllm.ai/"><img src="https://img.shields.io/badge/backend-vLLM-FF6F00?logo=lightning&logoColor=white" alt="vLLM"></a>
  <a href="https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct"><img src="https://img.shields.io/badge/model-Qwen3--Coder-FFD21E?logo=huggingface&logoColor=black" alt="Qwen3-Coder"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha">
  <img src="https://img.shields.io/badge/license-open--source-green" alt="License">
</p>

---

## 📖 About

This repository reimplements the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) npm agent architecture **entirely in Python**, designed to run with **local open-source models** via an OpenAI-compatible API server.

Built on the public porting workspace from [instructkr/claw-code](https://github.com/instructkr/claw-code), the active development lives at [HarnessLab/claw-code-agent](https://github.com/HarnessLab/claw-code-agent).

> **Goal:** Not to ship the original npm source, but to reimplement the full agent flow in Python — prompt assembly, context building, slash commands, tool calling, session persistence, and local model execution.

<p align="center">
  <img src="images/demo1.gif" alt="Claw Code Agent demo" width="900" />
</p>

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🤖 **Agent Loop** | Full agentic coding loop with tool calling and iterative reasoning |
| 🧰 **Core Tools** | File read / write / edit, glob search, grep search, shell execution |
| 💬 **Slash Commands** | Local commands: `/help`, `/context`, `/tools`, `/memory`, `/status`, `/model`, and more |
| 🧠 **Context Engine** | Automatic context building with CLAUDE.md discovery and usage reporting |
| 🔄 **Session Persistence** | Save and resume agent sessions across runs |
| 🔐 **Permission System** | Granular control: `--allow-write`, `--allow-shell`, `--unsafe` |
| 🏗️ **OpenAI-Compatible Runtime** | Python client targets an OpenAI-compatible API, with `vLLM` as the documented setup |
| 🐉 **Qwen3-Coder** | First-class support for `Qwen3-Coder-30B-A3B-Instruct` via vLLM |

---

## 📋 Roadmap

### Done

- [x] Python CLI agent loop
- [x] OpenAI-compatible local model backend
- [x] Qwen3-Coder support through vLLM with `qwen3_xml` tool parser
- [x] Core tools: `list_dir`, `read_file`, `write_file`, `edit_file`, `glob_search`, `grep_search`, `bash`
- [x] Context building and `/context`-style usage reporting
- [x] Slash commands: `/help`, `/context`, `/context-raw`, `/prompt`, `/permissions`, `/model`, `/tools`, `/memory`, `/status`, `/clear`
- [x] Session persistence and `agent-resume` flow
- [x] Permission system (read-only, write, shell, unsafe tiers)
- [x] Unit tests for the Python runtime
- [x] `pyproject.toml` packaging with `setuptools`

### In Progress

- [ ] Full MCP support
- [ ] Full plugin system
- [ ] Full slash-command parity
- [ ] Full interactive REPL / TUI behavior
- [ ] Exact tokenizer / context accounting
- [ ] Hooks parity
- [ ] Remote modes parity
- [ ] Voice / VIM parity
- [ ] Some deeper runtime details from the npm source
- [ ] Cost tracking and budget limits

---

## 🏗️ Architecture

```text
claw-code/
├── README.md
├── pyproject.toml
├── .gitignore
├── images/
│   └── logo.png
├── src/                          # Python implementation
│   ├── main.py                   # CLI entry point & argument parsing
│   ├── agent_runtime.py          # Core agent loop (LocalCodingAgent)
│   ├── agent_tools.py            # Tool definitions & execution engine
│   ├── agent_prompting.py        # System prompt assembly
│   ├── agent_context.py          # Context building & CLAUDE.md discovery
│   ├── agent_context_usage.py    # Context usage estimation & reporting
│   ├── agent_session.py          # Session state management
│   ├── agent_slash_commands.py   # Local slash command processing
│   ├── agent_types.py            # Shared dataclasses & type definitions
│   ├── openai_compat.py          # OpenAI-compatible API client
│   ├── session_store.py          # Session serialization & persistence
│   ├── permissions.py            # Tool permission filtering
│   ├── tools.py                  # Mirrored tool inventory
│   ├── commands.py               # Mirrored command inventory
│   ├── ...                       # 75+ modules across 30+ packages
│   ├── plugins/                  # Plugin subsystem (WIP)
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
    └── test_porting_workspace.py
```

---

## 📦 Requirements

| Requirement | Details |
|-------------|---------|
| 🐍 Python | `3.10` or higher |
| 🖥️ Model Server | `vLLM`, `Ollama`, or `LiteLLM Proxy`, with tool calling support |
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
```

---

## 🛠️ Usage

### Agent Commands

| Command | Description |
|---------|-------------|
| `agent <prompt>` | Run the agent with a prompt |
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
| `--show-transcript` | Print the full message transcript |
| `--system-prompt <text>` | Set a custom system prompt |
| `--append-system-prompt <text>` | Append to the system prompt |
| `--add-dir <path>` | Add extra directories to context |

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

## ⚠️ Disclaimer

- This repository is a **Python reimplementation** inspired by the Claude Code npm architecture.
- It does **not** ship the original npm source.
- It is **not** affiliated with or endorsed by Anthropic.

---

<p align="center">
  <sub>Built with 🐍 Python · Powered by 🐉  HarnessLab Team.</sub>
</p>
