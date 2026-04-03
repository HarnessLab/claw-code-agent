# Testing Guide

This guide gives concrete commands you can run to verify the current Python implementation feature by feature.

All commands below assume you are inside:

```bash
cd /path/to/claw-code-agent
```

## 1. Environment Setup

### 1.1 Start `vLLM` with Qwen3-Coder

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

Verify the server:

```bash
curl http://127.0.0.1:8000/v1/models
```

Set the runtime environment:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
```

### 1.2 Run the unit test suite

```bash
python3 -m unittest discover -s tests -v
```

## 2. Installation And Basic Usage

### 2.1 Editable install

```bash
pip install -e .
```

### 2.2 Show CLI help

```bash
python3 -m src.main --help
python3 -m src.main agent --help
python3 -m src.main agent-chat --help
python3 -m src.main agent-resume --help
```

### 2.3 Run the packaged entrypoint

```bash
claw-code-agent agent "/help"
```

## 3. Slash Commands

These are handled locally and do not require the model to answer.

```bash
python3 -m src.main agent "/help"
python3 -m src.main agent "/commands"
python3 -m src.main agent "/context" --cwd ..
python3 -m src.main agent "/context-raw" --cwd ..
python3 -m src.main agent "/prompt" --cwd ..
python3 -m src.main agent "/permissions" --cwd ..
python3 -m src.main agent "/tools" --cwd ..
python3 -m src.main agent "/memory" --cwd ..
python3 -m src.main agent "/status" --cwd ..
python3 -m src.main agent "/clear" --cwd ..
```

## 4. Context And Prompt Inspection

### 4.1 System prompt rendering

```bash
python3 -m src.main agent-prompt --cwd ..
```

### 4.2 Context usage accounting

```bash
python3 -m src.main agent-context --cwd ..
```

### 4.3 Raw context snapshot

```bash
python3 -m src.main agent-context-raw --cwd ..
```

### 4.4 Additional working directories

```bash
python3 -m src.main agent-context --cwd .. --add-dir /path/to/directory
```

### 4.5 Disable `CLAUDE.md` discovery

```bash
python3 -m src.main agent-context --cwd .. --disable-claude-md
```

## 5. Core Agent Loop

### 5.1 Read-only run

```bash
python3 -m src.main agent \
  "Read claw-code-agent/src/agent_runtime.py and summarize how the loop works." \
  --cwd ..
```

### 5.2 Show transcript output

```bash
python3 -m src.main agent \
  "Read claw-code-agent/src/agent_session.py and summarize the session model." \
  --cwd .. \
  --show-transcript
```

### 5.3 Streaming model responses

```bash
python3 -m src.main agent \
  "Inspect the current repository and summarize the architecture." \
  --cwd .. \
  --stream \
  --show-transcript
```

### 5.4 Interactive chat loop

```bash
python3 -m src.main agent-chat --cwd ..
```

Optional first prompt:

```bash
python3 -m src.main agent-chat \
  "Inspect the repository and tell me where the runtime loop lives." \
  --cwd ..
```

Inside chat mode:

- type normal prompts to continue the same session
- use `/exit` or `/quit` to leave

### 5.5 Resume directly into chat mode

```bash
python3 -m src.main agent-chat \
  --resume-session-id <session-id> \
  --cwd ..
```

## 6. Tool Execution

### 6.1 Read files

```bash
python3 -m src.main agent \ 
  "Read claw-code-agent/src/agent_tools.py and summarize each tool." \
  --cwd ..
```

### 6.2 Write files

```bash
python3 -m src.main agent \
  "Create TEST_WRITE.md in the current directory with one line: write test ok" \
  --cwd ./test_cases \
  --allow-write
```

### 6.3 Edit files

```bash
python3 -m src.main agent \
  "Create demo.txt with 'hello world', then replace 'world' with 'agent'." \
  --cwd ./test_cases \
  --allow-write
```

### 6.4 Glob and grep

```bash
python3 -m src.main agent \
  "Find Python files in the current directory, then search for 'LocalCodingAgent' and summarize the matches." \
  --cwd ..
```

### 6.5 Shell commands

```bash
python3 -m src.main agent \
  "Run pwd and ls in the current working directory, then summarize the result." \
  --cwd .. \
  --allow-shell \
  --show-transcript
```

### 6.6 Unsafe shell mode

Use only if you intentionally want destructive-shell permission enabled.

```bash
python3 -m src.main agent \
  "Explain whether destructive shell commands are currently allowed." \
  --cwd .. \
  --allow-shell \
  --unsafe
```

## 7. Session Persistence And Resume

### 7.1 Create a saved session

```bash
python3 -m src.main agent \
  "Create a short TODO file in the current directory and explain what you wrote." \
  --cwd ./test_cases \
  --allow-write
```

At the end of the run, note:

```text
session_id=...
session_path=...
```

### 7.2 Resume a saved session

```bash
python3 -m src.main agent-resume \
  <session-id> \
  "Continue the previous task and improve the file." \
  --allow-write \
  --show-transcript
```

### 7.3 Inspect saved sessions

```bash
ls -lt .port_sessions/agent
```

## 8. Structured Output / JSON Schema

Create a schema file:

```bash
cat > /tmp/claw_schema.json <<'EOF'
{
  "type": "object",
  "properties": {
    "status": { "type": "string" },
    "summary": { "type": "string" }
  },
  "required": ["status", "summary"],
  "additionalProperties": false
}
EOF
```

Run the agent with schema mode:

```bash
python3 -m src.main agent \
  "Inspect the current repository and respond in the requested JSON format." \
  --cwd .. \
  --response-schema-file /tmp/claw_schema.json \
  --response-schema-name claw_summary \
  --response-schema-strict
```

## 9. Budgets And Limits

### 9.1 Total token budget

```bash
python3 -m src.main agent \
  "Give a very long answer about the current repository." \
  --cwd .. \
  --max-total-tokens 50
```

### 9.2 Input / output token budgets

```bash
python3 -m src.main agent \
  "Read several files and explain them in detail." \
  --cwd .. \
  --max-input-tokens 80 \
  --max-output-tokens 80
```

### 9.3 Reasoning-token budget

```bash
python3 -m src.main agent \
  "Solve a multi-step task and explain the result." \
  --cwd .. \
  --max-reasoning-tokens 10
```

### 9.4 Tool-call budget

```bash
python3 -m src.main agent \
  "Read multiple files, search for symbols, and summarize the repo." \
  --cwd .. \
  --max-tool-calls 1
```

### 9.5 Delegated-task budget

```bash
python3 -m src.main agent \
  "Delegate two subtasks to inspect and summarize the repo." \
  --cwd .. \
  --max-delegated-tasks 1
```

### 9.6 Cost budget

```bash
python3 -m src.main agent \
  "Inspect the current repository and summarize it." \
  --cwd .. \
  --input-cost-per-million 0.15 \
  --output-cost-per-million 0.60 \
  --max-budget-usd 0.000001
```

### 9.7 Model-call budget

```bash
python3 -m src.main agent \
  "Continue inspecting the repository until you are done." \
  --cwd .. \
  --max-model-calls 1
```

### 9.8 Session-turn budget

```bash
python3 -m src.main agent \
  "Work through the repository across multiple turns and keep going." \
  --cwd .. \
  --max-session-turns 1
```

## 10. Streaming, Continuation, And Context Reduction

### 10.1 Streaming assistant output

```bash
python3 -m src.main agent \
  "Produce a long explanation of the current repository architecture." \
  --cwd .. \
  --stream \
  --show-transcript
```

### 10.2 Automatic continuation after truncation

Use a small output budget so the backend is more likely to stop early:

```bash
python3 -m src.main agent \
  "Write a long, structured explanation of the current repository." \
  --cwd .. \
  --max-output-tokens 32 \
  --show-transcript
```

### 10.3 Snipping older context

```bash
python3 -m src.main agent \
  "Read claw-code-agent/src/agent_runtime.py, claw-code-agent/src/agent_session.py, claw-code-agent/src/query_engine.py, and claw-code-agent/src/plugin_runtime.py, then summarize all of them in detail." \
  --cwd .. \
  --auto-snip-threshold 120 \
  --compact-preserve-messages 0 \
  --show-transcript
```

### 10.4 Compaction boundaries

```bash
python3 -m src.main agent \
  "Read several large files from claw-code-agent/src and keep explaining the repository until the context gets compacted." \
  --cwd .. \
  --auto-compact-threshold 120 \
  --compact-preserve-messages 1 \
  --show-transcript
```

## 11. File History Replay

### 11.1 Create file history

```bash
python3 -m src.main agent \
  "Create notes.txt with one line, then update that line to mention file history." \
  --cwd ./test_cases \
  --allow-write
```

### 11.2 Resume and inspect replay

```bash
python3 -m src.main agent-resume \
  <session-id> \
  "Continue the previous work and tell me what files were changed before this turn." \
  --allow-write \
  --show-transcript
```

Look for `file_history_replay` messages in the transcript.

## 12. Nested Delegation

### 12.1 Basic delegated subtask

```bash
python3 -m src.main agent \
  "Delegate a subtask to inspect claw-code-agent/src/agent_runtime.py and return the summary." \
  --cwd .. \
  --show-transcript
```

### 12.2 Multiple delegated subtasks

```bash
python3 -m src.main agent \
  "Delegate one subtask to scan the repository and another to summarize it after the scan." \
  --cwd .. \
  --show-transcript
```

### 12.3 Resume a delegated child session

1. Seed a normal saved session:

```bash
python3 -m src.main agent \
  "Inspect claw-code-agent/src/agent_tools.py and give a short summary." \
  --cwd ..
```

2. Copy that `session_id`, then run:

```bash
python3 -m src.main agent \
  "Delegate a subtask that resumes session <session-id> and continues it." \
  --cwd .. \
  --show-transcript
```

### 12.4 Topological dependency batches

```bash
python3 -m src.main agent \
  "Delegate two subtasks: one named scan, and one named summarize that depends on scan. Use topological batching and then return the final summary." \
  --cwd .. \
  --show-transcript
```

Look for:

- `delegate_batch_result`
- `delegate_group_result`
- `batch_index=...`

## 13. Plugin Runtime

Create a local plugin manifest:

```bash
mkdir -p ./test_cases/plugins/demo
cat > ./test_cases/plugins/demo/plugin.json <<'EOF'
{
  "name": "demo-plugin",
  "hooks": {
    "beforePrompt": "Inject plugin prompt guidance.",
    "afterTurn": "Attach plugin after-turn guidance.",
    "onResume": "Reapply plugin state on resume.",
    "beforePersist": "Persist plugin state before saving.",
    "beforeDelegate": "Add plugin guidance before delegated children run.",
    "afterDelegate": "Add plugin guidance after delegated children finish."
  },
  "toolAliases": [
    {
      "name": "plugin_read",
      "baseTool": "read_file",
      "description": "Plugin alias for reading files."
    }
  ],
  "virtualTools": [
    {
      "name": "demo_virtual",
      "description": "Return a rendered plugin response.",
      "responseTemplate": "plugin topic: {topic}"
    }
  ],
  "toolHooks": {
    "read_file": {
      "beforeTool": "Validate the path before reading.",
      "afterResult": "Summarize the file before the next action."
    }
  }
}
EOF
```

### 13.1 Plugin prompt/context discovery

```bash
python3 -m src.main agent-prompt --cwd ./test_cases
python3 -m src.main agent-context-raw --cwd ./test_cases
```

### 13.2 Plugin alias tool

```bash
echo "hello plugin" > ./test_cases/hello.txt
python3 -m src.main agent \
  "Use the plugin_read tool to read hello.txt and summarize it." \
  --cwd ./test_cases \
  --show-transcript
```

### 13.3 Plugin virtual tool

```bash
python3 -m src.main agent \
  "Use the demo_virtual tool with topic plugins and return the result." \
  --cwd ./test_cases \
  --show-transcript
```

### 13.4 Plugin before/after tool guidance

```bash
python3 -m src.main agent \
  "Read hello.txt and follow the plugin guidance around the read_file tool." \
  --cwd ./test_cases \
  --show-transcript
```

### 13.5 Plugin lifecycle with resume/persist

1. Start a session:

```bash
python3 -m src.main agent \
  "Use the plugin system and create a saved session." \
  --cwd ./test_cases
```

2. Resume it:

```bash
python3 -m src.main agent-resume \
  <session-id> \
  "Continue and mention any plugin lifecycle guidance you received." \
  --show-transcript
```

Look for:

- `plugin_before_persist`
- `Plugin resume hooks:`
- `Plugin runtime state:`

## 14. Query Engine And Workspace Commands

### 14.1 Workspace inventory

```bash
python3 -m src.main summary
python3 -m src.main manifest
python3 -m src.main subsystems --limit 20
python3 -m src.main commands --limit 20
python3 -m src.main tools --limit 20
```

### 14.2 Query routing and bootstrap reports

```bash
python3 -m src.main route "inspect the runtime and tools" --limit 10
python3 -m src.main bootstrap "inspect the runtime and tools" --limit 10
python3 -m src.main turn-loop "inspect the runtime and tools" --limit 5 --max-turns 3
```

### 14.3 Session flushing for the mirrored workspace

```bash
python3 -m src.main flush-transcript "store a temporary transcript"
python3 -m src.main load-session <session-id>
```

## 15. Remote/Direct Mode Simulations

These are mirrored workspace simulation commands, not the real agent runtime:

```bash
python3 -m src.main remote-mode demo-target
python3 -m src.main ssh-mode demo-target
python3 -m src.main teleport-mode demo-target
python3 -m src.main direct-connect-mode demo-target
python3 -m src.main deep-link-mode demo-target
```

## 16. Parity Tracking Workflow

Use this every time a new feature lands:

```bash
python3 -m unittest discover -s tests -v
```

Then update:

- `PARITY_CHECKLIST.md`
- `TESTING_GUIDE.md`

Rule for future work:

- every new implemented feature should add a checked item in `PARITY_CHECKLIST.md`
- every user-testable feature should add at least one concrete command example in `TESTING_GUIDE.md`
