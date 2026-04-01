# Claw Code Python

<p align="center">
  <img src="images/logo.png" alt="Claw Code Python logo" width="500" />
</p>

Implementation of the Claude Code npm source architecture in Python.

This repository builds on the public porting workspace from [instructkr/claw-code](https://github.com/instructkr/claw-code) and extends it into a usable Python local-model agent. The goal is not to ship the npm source itself, but to reimplement the agent flow in Python: prompt assembly, context building, slash commands, tool calling, and local model execution.

## Status

The Python runtime is working, but it is not full feature parity with the npm implementation yet.

Implemented now:

- Python CLI agent loop
- OpenAI-compatible local model backend
- Qwen3-Coder support through `vLLM`
- core tools: file read/write/edit, glob, grep, shell
- context building and `/context`-style usage reporting
- local slash commands such as `/help`, `/context`, `/tools`, `/memory`, `/status`
- unit tests for the Python runtime

Not complete yet:

- full plugin and MCP parity
- complete slash-command parity
- full interactive REPL/session persistence parity
- tokenizer-accurate context accounting

## Repository Layout

```text
claw-code/
├── README.md
├── .gitignore
├── src/
└── tests/
```

`src/` contains the Python implementation.

`tests/` contains the unit tests for the Python runtime.

## Requirements

- Python 3.10+
- a local OpenAI-compatible model server
- recommended model: `Qwen/Qwen3-Coder-30B-A3B-Instruct`

## Start `vLLM` With Qwen3-Coder

For Qwen3-Coder tool calling, `vLLM` must be started with automatic tool choice enabled. The official `vLLM` tool-calling docs describe `--enable-auto-tool-choice` and `--tool-call-parser`, and Qwen3-Coder should use the `qwen3_xml` parser:

- https://docs.vllm.ai/en/v0.13.0/features/tool_calling/
- https://docs.vllm.ai/en/v0.13.0/serving/openai_compatible_server.html

Example:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

Check that the server is up:

```bash
curl http://127.0.0.1:8000/v1/models
```

## Environment Setup

From the `claw-code/` directory:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
```

## How To Run

Show the agent system prompt:

```bash
python3 -m src.main agent-prompt --cwd .
```

Show estimated context usage:

```bash
python3 -m src.main agent-context --cwd .
```

Show the raw context snapshot:

```bash
python3 -m src.main agent-context-raw --cwd .
```

Run a read-only repo question:

```bash
python3 -m src.main agent \
  "Read src/agent_runtime.py and summarize how the loop works." \
  --cwd .
```

Run a write-enabled task:

```bash
python3 -m src.main agent \
  "Create TEST_QWEN_AGENT.md with one line: test ok" \
  --cwd . \
  --allow-write
```

Run a shell-enabled task:

```bash
python3 -m src.main agent \
  "Run pwd and ls src, then summarize the result." \
  --cwd . \
  --allow-shell
```

Show the full transcript:

```bash
python3 -m src.main agent \
  "Explain the current tool registry." \
  --cwd . \
  --show-transcript
```

Each real `agent` run now saves a resumable session and prints:

```text
session_id=...
session_path=...
```

Resume a previous session:

```bash
python3 -m src.main agent-resume \
  <session-id> \
  "Continue the previous task and finish the missing implementation."
```

## Resume A Previous Session

Each real `agent` run saves a resumable session automatically.

After a run finishes, the CLI prints:

```text
session_id=...
session_path=...
```

You can continue the same task later by passing the saved session id:

```bash
python3 -m src.main agent-resume \
  <session-id> \
  "Continue the previous task and finish the missing parts."
```

Example:

```bash
python3 -m src.main agent-resume \
  4f2c8c6f9c0e4d7c9c7b1b2a3d4e5f67 \
  "Continue building the customer e-commerce project and complete the checkout flow."
```

Session files are stored under:

```text
.port_sessions/agent/
```

You can inspect saved sessions with:

```bash
ls -lt .port_sessions/agent
```

Important notes:

- Run `agent-resume` from the same `claw-code/` repository where the session was created.
- If you use `--show-transcript`, the session id is printed after the run output.
- A resumed session continues from the saved transcript, not from scratch.

## Slash Command Examples

These are handled locally before the model loop:

```bash
python3 -m src.main agent "/help"
python3 -m src.main agent "/context" --cwd .
python3 -m src.main agent "/context-raw" --cwd .
python3 -m src.main agent "/tools" --cwd .
python3 -m src.main agent "/memory" --cwd .
python3 -m src.main agent "/status" --cwd .
```

## How To Test

Run the full Python test suite:

```bash
python3 -m unittest discover -s tests -v
```

Optional smoke tests:

```bash
python3 -m src.main agent "/help"
python3 -m src.main agent-context --cwd .
python3 -m src.main agent \
  "Read src/agent_session.py and summarize the message flow." \
  --cwd .
```

## Useful Commands

Workspace summary:

```bash
python3 -m src.main summary
```

Workspace manifest:

```bash
python3 -m src.main manifest
```

Mirrored command inventory:

```bash
python3 -m src.main commands --limit 10
```

Mirrored tool inventory:

```bash
python3 -m src.main tools --limit 10
```

## Notes

- The Python agent is still CLI-based, but sessions are now persisted and can be resumed with `agent-resume`.
- `--allow-write` is required before the agent can modify files.
- `--allow-shell` is required before the agent can execute shell commands.
- `--unsafe` additionally allows destructive shell operations.

## Disclaimer

- This repository is a Python implementation effort inspired by the Claude Code npm architecture.
- It does not ship the original npm source.
- It is not affiliated with or endorsed by Anthropic.
