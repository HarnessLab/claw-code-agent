from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable
from uuid import uuid4

from .account_runtime import AccountRuntime
from .agent_manager import AgentManager
from .agent_context import clear_context_caches
from .agent_context import render_context_report as render_agent_context_report
from .agent_context_usage import collect_context_usage, estimate_tokens, format_context_usage
from .compact import compact_conversation
from .ask_user_runtime import AskUserRuntime
from .agent_registry import (
    delete_agent_definition,
    find_agent_definition,
    normalize_mutable_source,
    load_agent_registry,
    render_agent_mutation,
    render_agent_detail,
    render_agents_report,
    scaffold_agent_definition,
    update_agent_definition,
)
from .config_runtime import ConfigRuntime
from .hook_policy import HookPolicyRuntime
from .lsp_runtime import LSPRuntime
from .mcp_runtime import MCPRuntime
from .scar_router import ScarRouter
from .priority_router import PriorityRouter
from .agent_prompting import (
    build_prompt_context,
    build_system_prompt_parts,
    render_system_prompt,
)
from .agent_session import AgentSessionState
from .agent_slash_commands import preprocess_slash_command
from .response_gate import apply_response_gate
from .agent_tools import (
    AgentTool,
    build_tool_context,
    default_tool_registry,
    execute_tool_streaming,
    serialize_tool_result,
)
from .agent_types import (
    AgentRunResult,
    AgentPermissions,
    AgentRuntimeConfig,
    AssistantTurn,
    BudgetConfig,
    ModelConfig,
    OutputSchemaConfig,
    StreamEvent,
    ToolCall,
    ToolExecutionResult,
    UsageStats,
)
from .model_router import ModelRouter, RouterConfig, RoutingDecision, Tier
from .openai_compat import OpenAICompatClient, OpenAICompatError
from .plan_runtime import PlanRuntime
from .plugin_runtime import PluginRuntime
from .remote_runtime import RemoteRuntime
from .remote_trigger_runtime import RemoteTriggerRuntime
from .search_runtime import SearchRuntime
from .task_runtime import TaskRuntime
from .team_runtime import TeamRuntime
from .tokenizer_runtime import describe_token_counter
from .workflow_runtime import WorkflowRuntime
from .worktree_runtime import WorktreeRuntime
from .session_env_vars import clear_session_env_vars
from .session_store import (
    StoredAgentSession,
    load_agent_session,
    save_agent_session,
    serialize_model_config,
    serialize_runtime_config,
    usage_from_payload,
)
from .token_budget import calculate_token_budget, format_token_budget
from .builtin_agents import (
    AgentDefinition,
    ALL_AGENT_DISALLOWED_TOOLS,
    GENERAL_PURPOSE_AGENT,
)
from .microcompact import microcompact_messages as _microcompact_messages

_LATTI_DIR = Path.home() / '.latti'
_IDENTITY_SHIM = _LATTI_DIR / 'scripts' / 'identity_compile.py'


class _ObservableEventList(list[dict[str, object]]):
    def __init__(self, event_sink: Callable[[dict[str, object]], None]) -> None:
        super().__init__()
        self._event_sink = event_sink

    def append(self, event: dict[str, object]) -> None:  # type: ignore[override]
        super().append(event)
        self._emit(event)

    def extend(self, events) -> None:  # type: ignore[override]
        for event in events:
            self.append(event)

    def _emit(self, event: dict[str, object]) -> None:
        try:
            self._event_sink(dict(event))
        except Exception:
            pass


def _maybe_spawn_identity_compiler() -> None:
    """Fire-and-forget spawn of the identity compiler at session end.

    Gated on LATTI_IDENTITY_COMPILE=1 so existing test fixtures that build
    runtime instances don't accidentally trigger compiles. Any failure
    (missing shim, Popen error) is silently swallowed — must NOT affect
    the run() return value.
    """
    if os.environ.get('LATTI_IDENTITY_COMPILE') != '1':
        return
    if not _IDENTITY_SHIM.is_file():
        return
    try:
        subprocess.Popen(
            [
                sys.executable, str(_IDENTITY_SHIM),
                '--memory-dir',   str(_LATTI_DIR / 'memory'),
                '--identity-out', str(_LATTI_DIR / 'IDENTITY.md'),
                '--history-out',  str(_LATTI_DIR / 'HISTORY.md'),
                '--cursor-path',  str(_LATTI_DIR / '.history-cursor'),
                '--meta-path',    str(_LATTI_DIR / '.identity-meta.json'),
                '--log-path',     str(_LATTI_DIR / 'identity-compile.log'),
                '--goals-path',   str(_LATTI_DIR / 'goals.jsonl'),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return


@dataclass(frozen=True)
class BudgetDecision:
    exceeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class PromptPreflightResult:
    usage_increment: UsageStats = field(default_factory=UsageStats)
    model_calls_increment: int = 0
    stop_reason: str | None = None
    reason: str | None = None


@dataclass
class LocalCodingAgent:
    model_config: ModelConfig
    runtime_config: AgentRuntimeConfig
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    override_system_prompt: str | None = None
    tool_registry: dict[str, AgentTool] | None = None
    agent_manager: AgentManager | None = None
    parent_agent_id: str | None = None
    managed_group_id: str | None = None
    managed_child_index: int | None = None
    managed_label: str | None = None
    plugin_runtime: PluginRuntime | None = None
    hook_policy_runtime: HookPolicyRuntime | None = None
    mcp_runtime: MCPRuntime | None = None
    remote_runtime: RemoteRuntime | None = None
    remote_trigger_runtime: RemoteTriggerRuntime | None = None
    search_runtime: SearchRuntime | None = None
    account_runtime: AccountRuntime | None = None
    ask_user_runtime: AskUserRuntime | None = None
    config_runtime: ConfigRuntime | None = None
    lsp_runtime: LSPRuntime | None = None
    plan_runtime: PlanRuntime | None = None
    task_runtime: TaskRuntime | None = None
    team_runtime: TeamRuntime | None = None
    workflow_runtime: WorkflowRuntime | None = None
    worktree_runtime: WorktreeRuntime | None = None
    last_session: AgentSessionState | None = field(default=None, init=False, repr=False)
    last_run_result: AgentRunResult | None = field(default=None, init=False, repr=False)
    cumulative_usage: UsageStats = field(default_factory=UsageStats, init=False, repr=False)
    cumulative_cost_usd: float = field(default=0.0, init=False, repr=False)
    _compact_consecutive_failures: int = field(default=0, init=False, repr=False)
    active_session_id: str | None = field(default=None, init=False, repr=False)
    last_session_path: str | None = field(default=None, init=False, repr=False)
    managed_agent_id: str | None = field(default=None, init=False, repr=False)
    resume_source_session_id: str | None = field(default=None, init=False, repr=False)
    model_router: ModelRouter | None = field(default=None, init=False, repr=False)
    scar_router: ScarRouter | None = field(default=None, init=False, repr=False)
    # Stash for per-tool evaluator events. _dispatch_via_state_machine
    # appends here after each tool step; the LLM-call hook drains before
    # firing its own eval. Preserves 'replan' verdicts across multi-tool
    # turns where state.last_observation would otherwise be clobbered.
    _pending_eval_events: list = field(default_factory=list, init=False, repr=False)
    # State-machine bridge — PRIMARY path (Step 6 default-on, 2026-04-29).
    # Lazy construction; opt OUT via LATTI_USE_STATE_MACHINE=0 if you need
    # the legacy execute_tool_streaming fallback. The typed loop replaces
    # legacy; legacy is fallback only.
    _sm_runner: 'object | None' = field(default=None, init=False, repr=False)
    _sm_state: 'object | None' = field(default=None, init=False, repr=False)
    _sm_memory: 'object | None' = field(default=None, init=False, repr=False)
    _sm_goals: 'object | None' = field(default=None, init=False, repr=False)
    _sm_tasks: 'object | None' = field(default=None, init=False, repr=False)
    runtime_event_sink: Callable[[dict[str, object]], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.tool_registry is None:
            self.tool_registry = default_tool_registry()
        if self.agent_manager is None:
            self.agent_manager = AgentManager()
        if self.scar_router is None:
            self.scar_router = ScarRouter()
        if self.plugin_runtime is None:
            self.plugin_runtime = PluginRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.hook_policy_runtime is None:
            self.hook_policy_runtime = HookPolicyRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.mcp_runtime is None:
            self.mcp_runtime = MCPRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.remote_runtime is None:
            self.remote_runtime = RemoteRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.remote_trigger_runtime is None:
            self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.search_runtime is None:
            self.search_runtime = SearchRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.account_runtime is None:
            self.account_runtime = AccountRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.ask_user_runtime is None:
            self.ask_user_runtime = AskUserRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.config_runtime is None:
            self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        if self.lsp_runtime is None:
            self.lsp_runtime = LSPRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.plan_runtime is None:
            self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        if self.task_runtime is None:
            self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        if self.team_runtime is None:
            self.team_runtime = TeamRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.workflow_runtime is None:
            self.workflow_runtime = WorkflowRuntime.from_workspace(
                self.runtime_config.cwd,
                tuple(str(path) for path in self.runtime_config.additional_working_directories),
            )
        if self.worktree_runtime is None:
            self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.runtime_config = self._apply_hook_policy_budget_overrides(self.runtime_config)
        registry = dict(self.tool_registry)
        plugin_tools = self.plugin_runtime.register_tool_aliases(registry)
        if plugin_tools:
            registry = {**registry, **plugin_tools}
        virtual_tools = self.plugin_runtime.register_virtual_tools(registry)
        if virtual_tools:
            registry = {**registry, **virtual_tools}
        self.tool_registry = registry
        self.client = OpenAICompatClient(self.model_config)
        self.model_router = ModelRouter(RouterConfig.from_env(), default_heavy_model=self.model_config.model)
        self.tool_context = build_tool_context(
            self.runtime_config,
            tool_registry=self.tool_registry,
            extra_env=(
                self.hook_policy_runtime.safe_env()
                if self.hook_policy_runtime is not None
                else None
            ),
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            mcp_runtime=self.mcp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )

    def set_model(self, model: str) -> None:
        self.model_config = replace(self.model_config, model=model)
        self.client = OpenAICompatClient(self.model_config)

    def clear_runtime_state(self) -> None:
        self.last_session = None
        self.last_run_result = None
        self.active_session_id = None
        self.last_session_path = None
        self.resume_source_session_id = None
        if self.plugin_runtime is not None:
            self.plugin_runtime.restore_session_state({})
        # Mirror commands/clear/caches.ts: drop session-scoped env vars on /clear.
        clear_session_env_vars()

    def build_prompt_context(self, scratchpad_directory: Path | None = None):
        return build_prompt_context(
            self.runtime_config,
            self.model_config,
            scratchpad_directory=scratchpad_directory,
        )

    def build_system_prompt_parts(self, prompt_context=None) -> list[str]:
        if prompt_context is None:
            prompt_context = self.build_prompt_context()
        return build_system_prompt_parts(
            prompt_context=prompt_context,
            runtime_config=self.runtime_config,
            tools=self.tool_registry,
            available_agents=self.available_agents(),
            custom_system_prompt=self.custom_system_prompt,
            append_system_prompt=self.append_system_prompt,
            override_system_prompt=self.override_system_prompt,
        )

    def load_agent_registry(self):
        return load_agent_registry(self.runtime_config.cwd)

    def available_agents(self) -> tuple[AgentDefinition, ...]:
        return self.load_agent_registry().active_agents

    def build_session(
        self,
        user_prompt: str | None = None,
        *,
        scratchpad_directory: Path | None = None,
    ) -> AgentSessionState:
        prompt_context = self.build_prompt_context(scratchpad_directory)
        system_prompt_parts = self.build_system_prompt_parts(prompt_context)
        return AgentSessionState.create(
            system_prompt_parts,
            user_prompt,
            user_context=prompt_context.user_context,
            system_context=prompt_context.system_context,
        )

    def _apply_hook_policy_budget_overrides(
        self,
        runtime_config: AgentRuntimeConfig,
    ) -> AgentRuntimeConfig:
        if self.hook_policy_runtime is None or not self.hook_policy_runtime.manifests:
            return runtime_config
        overrides = self.hook_policy_runtime.budget_overrides()
        if not overrides:
            return runtime_config
        budget = runtime_config.budget_config
        return replace(
            runtime_config,
            budget_config=BudgetConfig(
                max_total_tokens=(
                    budget.max_total_tokens
                    if budget.max_total_tokens is not None
                    else _optional_policy_int(overrides.get('max_total_tokens'))
                ),
                max_input_tokens=(
                    budget.max_input_tokens
                    if budget.max_input_tokens is not None
                    else _optional_policy_int(overrides.get('max_input_tokens'))
                ),
                max_output_tokens=(
                    budget.max_output_tokens
                    if budget.max_output_tokens is not None
                    else _optional_policy_int(overrides.get('max_output_tokens'))
                ),
                max_reasoning_tokens=(
                    budget.max_reasoning_tokens
                    if budget.max_reasoning_tokens is not None
                    else _optional_policy_int(overrides.get('max_reasoning_tokens'))
                ),
                max_total_cost_usd=(
                    budget.max_total_cost_usd
                    if budget.max_total_cost_usd is not None
                    else _optional_policy_float(overrides.get('max_total_cost_usd'))
                ),
                max_tool_calls=(
                    budget.max_tool_calls
                    if budget.max_tool_calls is not None
                    else _optional_policy_int(overrides.get('max_tool_calls'))
                ),
                max_delegated_tasks=(
                    budget.max_delegated_tasks
                    if budget.max_delegated_tasks is not None
                    else _optional_policy_int(overrides.get('max_delegated_tasks'))
                ),
                max_model_calls=(
                    budget.max_model_calls
                    if budget.max_model_calls is not None
                    else _optional_policy_int(overrides.get('max_model_calls'))
                ),
                max_session_turns=(
                    budget.max_session_turns
                    if budget.max_session_turns is not None
                    else _optional_policy_int(overrides.get('max_session_turns'))
                ),
            ),
        )

    def run(self, prompt: str) -> AgentRunResult:
        self.managed_agent_id = None
        self.resume_source_session_id = None
        if self.plugin_runtime is not None:
            self.plugin_runtime.restore_session_state({})
        session_id = uuid4().hex
        # Write new session ID to ~/.latti/last_session so the latti shim
        # and audit journal always see the current session UUID, not a stale one.
        try:
            import pathlib
            _latti_home = pathlib.Path.home() / '.latti'
            if _latti_home.is_dir():
                (_latti_home / 'last_session').write_text(session_id, encoding='utf-8')
        except Exception:
            pass
        scratchpad_directory = self._ensure_scratchpad_directory(session_id)
        
        # ROTATION ACTIVATION: Check if rotation signal exists and activate if needed
        # This switches the agent to self-axis mode if the rotation gate fired
        prompt = self._check_rotation_activation(prompt)
        
        # Pre-response: inject any claim-matches into system prompt so echoes
        # of prior claims are recognized structurally, not re-reasoned.
        self._inject_claim_matches(prompt)
        
        # Pre-response: inject finalization context if the prompt contains
        # finalization keywords to guide response format and structure.
        self._inject_response_finalization_context(prompt)
        
        # Layer 4: Inject next priority before response generation
        # This prevents "what next?" routing by making the next action explicit
        self._inject_next_priority()
        
        self._bind_state_machine_session(session_id)
        registered_goal = self._register_goal_from_prompt(prompt, session_id)
        result = self._run_prompt(
            prompt,
            base_session=None,
            session_id=session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=(),
        )
        self._accumulate_usage(result)
        self._finalize_managed_agent(result)
        # Mark the registered Goal as done only on a clean stop_reason.
        # Exclude error/timeout-class outcomes so a budget-exhausted or
        # max-turns-truncated run doesn't mislabel an unfinished Goal as done.
        _GOAL_NOT_DONE_STOP_REASONS = {
            None, 'error', 'backend_error', 'budget_exceeded',
            'max_turns', 'max_tool_calls', 'max_model_calls',
        }
        if registered_goal is not None and result.stop_reason not in _GOAL_NOT_DONE_STOP_REASONS:
            self._mark_goal_done(registered_goal)

        # ROTATION GATE: Check if we should rotate to self-directed work
        # This is the decision point that prevents orbit
        self._check_rotation_gate(result)

        # OUTCOME RECORDING: Record self-axis task outcomes for feedback loop
        # This enables pattern learning and harness refinement
        self._record_self_axis_outcome(result)

        _maybe_spawn_identity_compiler()
        return result

    def _inject_next_priority(self) -> None:
        """Pre-response hook: inject "next action" priority context.

        Originally introduced by commit 84bc6a7 with a call site but no
        body — agent.run() raised AttributeError on every invocation,
        which surfaced live as "Worker exited before returning a result"
        on every chat turn (worker subprocess crashed on the missing
        method before producing a result file).

        Currently a no-op: callable, returns None, no side effects.
        The originally intended behavior (read priorities from somewhere
        and append to system prompt) is not specified in the commit
        that introduced the call site; the load-bearing fix is
        unbreaking the chat loop, not inventing semantics.

        Tested by tests/test_inject_next_priority_unbreak.py.
        """
        return None

    def _inject_claim_matches(self, prompt: str) -> None:
        """Pre-response hook: if the incoming prompt echoes prior claims,
        append the matches to append_system_prompt so the LLM sees the echo
        before responding. Best-effort; no-op without Latti."""
        import sys
        from pathlib import Path
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return
            if not prompt or len(prompt) < 20:
                return
            scripts = latti_home / 'scripts'
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            from claims import match_for_injection  # type: ignore[import-not-found]
            injection = match_for_injection(prompt)
            if not injection:
                return
            # Append to the system prompt for this turn
            existing = self.append_system_prompt or ''
            self.append_system_prompt = existing + injection
        except Exception:
            pass

    def _inject_response_finalization_context(self, prompt: str) -> None:
        """Pre-response hook: inject response finalization context if the prompt
        contains finalization keywords. This helps the LLM understand the expected
        response format and constraints."""
        try:
            # Check if prompt contains finalization-related keywords
            finalization_keywords = [
                'finalize', 'finalization', 'final response', 'wrap up',
                'conclude', 'summary', 'complete', 'done', 'finish'
            ]
            prompt_lower = prompt.lower()
            if not any(keyword in prompt_lower for keyword in finalization_keywords):
                return
            
            # Inject finalization context
            finalization_context = (
                "\n\n[RESPONSE FINALIZATION CONTEXT]\n"
                "When finalizing your response:\n"
                "1. Summarize key findings or decisions\n"
                "2. Highlight any blockers or dependencies\n"
                "3. Provide clear next steps if applicable\n"
                "4. Use structured format (bullets, sections) for clarity\n"
                "5. Avoid trailing questions unless explicitly requested\n"
            )
            existing = self.append_system_prompt or ''
            self.append_system_prompt = existing + finalization_context
        except Exception:
            pass

    def resume(self, prompt: str, stored_session: StoredAgentSession) -> AgentRunResult:
        self.managed_agent_id = None
        self.resume_source_session_id = stored_session.session_id
        session = AgentSessionState.from_persisted(
            system_prompt_parts=stored_session.system_prompt_parts,
            user_context=stored_session.user_context,
            system_context=stored_session.system_context,
            messages=stored_session.messages,
        )
        self._append_file_history_replay_if_needed(
            session,
            stored_session.file_history,
        )
        self._append_compaction_replay_if_needed(session)
        self.active_session_id = stored_session.session_id
        self.last_session = session
        self.last_session_path = str(
            self.runtime_config.session_directory / f'{stored_session.session_id}.json'
        )
        if self.plugin_runtime is not None:
            self.plugin_runtime.restore_session_state(stored_session.plugin_state)
        scratchpad_directory = (
            Path(stored_session.scratchpad_directory)
            if stored_session.scratchpad_directory
            else self._ensure_scratchpad_directory(stored_session.session_id)
        )
        if not self._restore_persisted_state_machine_state(stored_session):
            self._bind_state_machine_session(stored_session.session_id)
        registered_goal = self._register_goal_from_prompt(prompt, stored_session.session_id)
        result = self._run_prompt(
            prompt,
            base_session=session,
            session_id=stored_session.session_id,
            scratchpad_directory=scratchpad_directory,
            existing_file_history=stored_session.file_history,
        )
        self._accumulate_usage(result)
        self._finalize_managed_agent(result)
        # Mirror run()'s clean-stop-marks-done behavior so resume sessions
        # close their goals symmetrically. Same exclusion list.
        _GOAL_NOT_DONE_STOP_REASONS = {
            None, 'error', 'backend_error', 'budget_exceeded',
            'max_turns', 'max_tool_calls', 'max_model_calls',
        }
        if registered_goal is not None and result.stop_reason not in _GOAL_NOT_DONE_STOP_REASONS:
            self._mark_goal_done(registered_goal)
        return result

    def _run_prompt(
        self,
        prompt: str,
        *,
        base_session: AgentSessionState | None,
        session_id: str,
        scratchpad_directory: Path | None,
        existing_file_history: tuple[dict[str, object], ...],
    ) -> AgentRunResult:
        slash_result = preprocess_slash_command(self, prompt)
        if slash_result.handled and not slash_result.should_query:
            return AgentRunResult(
                final_output=slash_result.output,
                turns=0,
                tool_calls=0,
                transcript=slash_result.transcript,
                session_id=self.active_session_id,
                session_path=self.last_session_path,
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )

        effective_prompt = self._apply_hook_policy_before_prompt_hooks(
            slash_result.prompt or prompt
        )
        effective_prompt = self._apply_plugin_before_prompt_hooks(effective_prompt)
        effective_prompt = self._apply_plugin_resume_hooks(
            effective_prompt,
            resumed=base_session is not None,
        )

        # 2026-04-27: pre-prompt router re-wired after session-refactor removed it.
        # Module at ~/.latti/lib/pre_prompt_router.py — pure-python port of pi's 4
        # prompt-reactive extensions (research-before-build, skill-router,
        # harness-router, depth-reasoner). Gated by LATTI_PROMPT_ROUTER env var
        # (default 1 in shim). Failures must never break the model call.
        if os.environ.get("LATTI_PROMPT_ROUTER", "0") == "1":
            try:
                import sys as _sys
                _latti_lib = os.path.expanduser("~/.latti/lib")
                if _latti_lib not in _sys.path:
                    _sys.path.insert(0, _latti_lib)
                from pre_prompt_router import route_prompt, format_injections  # type: ignore
                _injections = route_prompt(effective_prompt)
                if _injections:
                    _block = format_injections(_injections)
                    effective_prompt = f"{effective_prompt}\n\n{_block}"
            except Exception:
                pass
        self.managed_agent_id = self.agent_manager.start_agent(
            prompt=effective_prompt,
            parent_agent_id=self.parent_agent_id,
            group_id=self.managed_group_id,
            child_index=self.managed_child_index,
            label=self.managed_label or ('root' if base_session is None else 'resume'),
            resumed_from_session_id=self.resume_source_session_id,
        )
        session = (
            base_session
            if base_session is not None
            else self.build_session(
                None,
                scratchpad_directory=scratchpad_directory,
            )
        )
        session.append_user(effective_prompt)
        self.last_session = session
        self.active_session_id = session_id
        tool_specs = [tool.to_openai_tool() for tool in self.tool_registry.values()]
        starting_usage = UsageStats()
        starting_cost_usd = 0.0
        starting_tool_calls = 0
        starting_session_turns = 0
        starting_model_calls = 0
        if base_session is not None and self.resume_source_session_id:
            try:
                stored_resume_state = load_agent_session(
                    self.resume_source_session_id,
                    directory=self.runtime_config.session_directory,
                )
            except OSError:
                stored_resume_state = None
            if stored_resume_state is not None:
                starting_usage = usage_from_payload(stored_resume_state.usage)
                starting_cost_usd = stored_resume_state.total_cost_usd
                starting_tool_calls = stored_resume_state.tool_calls
                starting_session_turns = stored_resume_state.turns
                budget_state = (
                    stored_resume_state.budget_state
                    if isinstance(stored_resume_state.budget_state, dict)
                    else {}
                )
                starting_model_calls = int(budget_state.get('model_calls', 0)) if isinstance(budget_state.get('model_calls', 0), int) else 0
        tool_calls = starting_tool_calls
        last_content = ''
        total_usage = starting_usage
        total_cost_usd = starting_cost_usd
        file_history = list(existing_file_history)
        stream_events: list[dict[str, object]] = self._new_stream_events()
        assistant_response_segments: list[str] = []
        consecutive_empty_responses = 0
        delegated_tasks = sum(
            1 for entry in file_history if entry.get('action') in ('delegate_agent', 'Agent')
        )
        model_calls = starting_model_calls

        initial_budget = self._check_budget(
            total_usage,
            total_cost_usd,
            tool_calls=tool_calls,
            delegated_tasks=delegated_tasks,
            model_calls=model_calls,
            session_turns=starting_session_turns,
        )
        if initial_budget.exceeded:
            result = AgentRunResult(
                final_output=initial_budget.reason or 'Stopped before the first model call.',
                turns=0,
                tool_calls=0,
                transcript=session.transcript(),
                session_id=session_id,
                usage=total_usage,
                total_cost_usd=total_cost_usd,
                stop_reason='budget_exceeded',
                file_history=tuple(file_history),
                scratchpad_directory=(
                    str(scratchpad_directory) if scratchpad_directory is not None else None
                ),
            )
            result = self._persist_session(session, result)
            self.last_run_result = result
            return result

        if self._should_use_state_machine_outer_loop():
            result = self._run_prompt_via_state_machine_outer_loop(
                effective_prompt=effective_prompt,
                session=session,
                session_id=session_id,
                scratchpad_directory=scratchpad_directory,
                tool_specs=tool_specs,
                starting_usage=starting_usage,
                starting_cost_usd=starting_cost_usd,
                starting_tool_calls=starting_tool_calls,
                starting_session_turns=starting_session_turns,
                starting_model_calls=starting_model_calls,
                delegated_tasks=delegated_tasks,
                file_history=file_history,
                stream_events=stream_events,
            )
            self.last_run_result = result
            return result

        # 2026-04-27: Remove max_turns ceiling from main loop.
        # The loop is bounded by explicit break/return conditions (budget,
        # empty responses, tool errors, etc.), not by a hardcoded turn count.
        # Removing the ceiling allows long autonomous work to proceed.
        for turn_index in itertools.count(1):
            self._microcompact_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._snip_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._compact_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            preflight = self._preflight_prompt_length(
                session,
                stream_events,
                turn_index=turn_index,
            )
            if preflight.usage_increment.total_tokens or preflight.model_calls_increment:
                total_usage = total_usage + preflight.usage_increment
                total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                model_calls += preflight.model_calls_increment
                budget_after_preflight = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_preflight.exceeded:
                    result = AgentRunResult(
                        final_output=(
                            budget_after_preflight.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._persist_session(session, result)
                    self.last_run_result = result
                    return result
            if preflight.stop_reason is not None:
                result = AgentRunResult(
                    final_output=preflight.reason or 'Stopped before the next model call.',
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=preflight.stop_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=max(turn_index - 1, 0),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result
            try:
                turn, turn_events = self._query_model(session, tool_specs)
            except OpenAICompatError as exc:
                if self._is_prompt_too_long_error(exc) and self._reactive_compact_session(
                    session,
                    stream_events,
                    turn_index=turn_index,
                ):
                    try:
                        turn, turn_events = self._query_model(session, tool_specs)
                    except OpenAICompatError as retry_exc:
                        exc = retry_exc
                    else:
                        stream_events.extend(
                            {
                                'type': 'reactive_compact_retry',
                                'turn_index': turn_index,
                            }
                            for _ in [0]
                        )
                        stream_events.extend(event.to_dict() for event in turn_events)
                        model_calls += 1
                        total_usage = total_usage + turn.usage
                        total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                        last_content = turn.content

                        budget_after_model = self._check_budget(
                            total_usage,
                            total_cost_usd,
                            tool_calls=tool_calls,
                            delegated_tasks=delegated_tasks,
                            model_calls=model_calls,
                            session_turns=starting_session_turns + turn_index,
                        )
                        if budget_after_model.exceeded:
                            result = AgentRunResult(
                                final_output=(
                                    budget_after_model.reason
                                    or 'Stopped because the runtime budget was exceeded.'
                                ),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason='budget_exceeded',
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result

                        if not turn.tool_calls:
                            assistant_response_segments.append(turn.content)
                            if self._should_continue_response(turn):
                                session.append_user(
                                    self._build_continuation_prompt(),
                                    metadata={
                                        'kind': 'continuation_request',
                                        'continuation_index': len(assistant_response_segments),
                                    },
                                    message_id=f'continuation_{turn_index}',
                                )
                                stream_events.append(
                                    {
                                        'type': 'continuation_request',
                                        'reason': turn.finish_reason,
                                        'continuation_index': len(assistant_response_segments),
                                    }
                                )
                                last_content = ''.join(assistant_response_segments)
                                continue
                            result = AgentRunResult(
                                final_output=''.join(assistant_response_segments),
                                turns=turn_index,
                                tool_calls=tool_calls,
                                transcript=session.transcript(),
                                events=tuple(stream_events),
                                usage=total_usage,
                                total_cost_usd=total_cost_usd,
                                stop_reason=turn.finish_reason,
                                file_history=tuple(file_history),
                                session_id=session_id,
                                scratchpad_directory=(
                                    str(scratchpad_directory) if scratchpad_directory is not None else None
                                ),
                            )
                            result = self._persist_session(session, result)
                            self.last_run_result = result
                            return result
                        # fall through to the normal tool-call branch below
                # normal error path if not recovered
                result = AgentRunResult(
                    final_output=str(exc),
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='backend_error',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            stream_events.extend(event.to_dict() for event in turn_events)
            model_calls += 1
            total_usage = total_usage + turn.usage
            total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
            last_content = turn.content

            budget_after_model = self._check_budget(
                total_usage,
                total_cost_usd,
                tool_calls=tool_calls,
                delegated_tasks=delegated_tasks,
                model_calls=model_calls,
                session_turns=starting_session_turns + turn_index,
            )
            if budget_after_model.exceeded:
                result = AgentRunResult(
                    final_output=(
                        budget_after_model.reason
                        or 'Stopped because the runtime budget was exceeded.'
                    ),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='budget_exceeded',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            # Track consecutive empty responses — stop burning money on nothing
            if not turn.content.strip() and not turn.tool_calls:
                consecutive_empty_responses += 1
            else:
                consecutive_empty_responses = 0
            if consecutive_empty_responses >= 3:
                result = AgentRunResult(
                    final_output=(
                        'Stopped: model returned 3 consecutive empty responses. '
                        'This usually means the input is not a valid prompt.'
                    ),
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason='empty_responses',
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            if not turn.tool_calls:
                assistant_response_segments.append(turn.content)
                if self._should_continue_response(turn):
                    session.append_user(
                        self._build_continuation_prompt(),
                        metadata={
                            'kind': 'continuation_request',
                            'continuation_index': len(assistant_response_segments),
                        },
                        message_id=f'continuation_{turn_index}',
                    )
                    stream_events.append(
                        {
                            'type': 'continuation_request',
                            'reason': turn.finish_reason,
                            'continuation_index': len(assistant_response_segments),
                        }
                    )
                    last_content = ''.join(assistant_response_segments)
                    continue
                final_output = ''.join(assistant_response_segments)
                final_output = apply_response_gate(
                    final_output,
                    bypass=os.environ.get('LATTI_GATE', '1') == '0',
                )
                result = AgentRunResult(
                    final_output=final_output,
                    turns=turn_index,
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=turn.finish_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=turn_index,
                )
                result = self._persist_session(session, result)
                self.last_run_result = result
                return result

            for tool_call in turn.tool_calls:
                assistant_response_segments.clear()
                tool_calls += 1
                if tool_call.name in ('Agent', 'delegate_agent'):
                    delegated_tasks += self._delegated_task_units(tool_call.arguments)
                budget_after_tool_request = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_tool_request.exceeded:
                    stream_events.append(
                        {
                            'type': 'task_budget_exceeded',
                            'turn_index': turn_index,
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'reason': budget_after_tool_request.reason,
                        }
                    )
                    result = AgentRunResult(
                        final_output=(
                            budget_after_tool_request.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._persist_session(session, result)
                    self.last_run_result = result
                    return result
                tool_result = None
                tool_message_index = session.start_tool(
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                    message_id=f'tool_{len(session.messages)}',
                    metadata={'phase': 'starting'},
                )
                stream_events.append(
                    {
                        'type': 'tool_start',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                    }
                )
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_attempt(tool_call.name, blocked=False)
                plugin_preflight_messages = self._plugin_tool_preflight_messages(tool_call.name)
                policy_preflight_messages = self._hook_policy_tool_preflight_messages(
                    tool_call.name
                )
                if plugin_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'plugin_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(plugin_preflight_messages),
                        }
                    )
                if policy_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(policy_preflight_messages),
                        }
                    )
                plugin_block_message = self._plugin_block_message(tool_call.name)
                policy_block_message = self._hook_policy_block_message(tool_call.name)
                if plugin_block_message is not None:
                    if self.plugin_runtime is not None:
                        blocked_attempts = int(
                            self.plugin_runtime.session_state.get('blocked_tool_attempts', 0)
                        )
                        self.plugin_runtime.session_state['blocked_tool_attempts'] = (
                            blocked_attempts + 1
                        )
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=plugin_block_message,
                        metadata={
                            'action': 'plugin_block',
                            'plugin_blocked': True,
                            'plugin_block_message': plugin_block_message,
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': plugin_block_message,
                        }
                    )
                if policy_block_message is not None:
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=policy_block_message,
                        metadata={
                            'action': 'hook_policy_block',
                            'hook_policy_blocked': True,
                            'hook_policy_block_message': policy_block_message,
                            'error_kind': 'permission_denied',
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': policy_block_message,
                        }
                    )
                # TUI: show tool call
                from . import tui as _tui
                _tool_detail = self._tool_call_detail(tool_call)
                _tui.tool_start(tool_call.name, _tool_detail)

                if tool_call.name in ('Agent', 'delegate_agent'):
                    if tool_result is None:
                        tool_result = self._execute_delegate_agent(tool_call.arguments)
                elif tool_call.name == 'Skill':
                    if tool_result is None:
                        tool_result = self._execute_skill(tool_call.arguments)
                elif tool_result is None and os.environ.get('LATTI_USE_STATE_MACHINE') != '0':
                    # State-machine bridge is the PRIMARY path (Step 6, 2026-04-29).
                    # The typed loop replaces the legacy execute_tool_streaming
                    # block; legacy is a fallback reachable via LATTI_USE_STATE_MACHINE=0.
                    # Verified live: branch reaches dispatch, policy_decisions appends.
                    tool_result = self._dispatch_via_state_machine(
                        tool_call,
                        session=session,
                        tool_message_index=tool_message_index,
                        stream_events=stream_events,
                    )
                elif tool_result is None:
                    # Legacy fallback — only reached when LATTI_USE_STATE_MACHINE=0.
                    # Will be removed once the typed loop has soaked across all
                    # tool kinds in production.
                    for update in execute_tool_streaming(
                        self.tool_registry,
                        tool_call.name,
                        tool_call.arguments,
                        self.tool_context,
                    ):
                        if update.kind == 'delta':
                            session.append_tool_delta(
                                tool_message_index,
                                update.content,
                                metadata={'last_stream': update.stream or 'tool'},
                            )
                            stream_events.append(
                                {
                                    'type': 'tool_delta',
                                    'tool_name': tool_call.name,
                                    'tool_call_id': tool_call.id,
                                    'message_id': session.messages[tool_message_index].message_id,
                                    'stream': update.stream,
                                    'delta': update.content,
                                }
                            )
                            continue
                        tool_result = update.result
                if tool_result is None:
                    raise RuntimeError(f'Tool executor returned no final result for {tool_call.name}')
                # TUI: show tool result
                if tool_result.ok:
                    _content = tool_result.content or 'ok'
                    # Sanitize tool output before display — strips layout-busting
                    # escape sequences (scroll-region-reset, screen-clear, cursor
                    # movement, RIS, alt-screen) that subprocess output can contain.
                    try:
                        from .tui_heal import sanitize as _tui_sanitize
                        _content = _tui_sanitize(_content)
                    except Exception:
                        pass
                    # Show first line only, max 100 chars
                    _first_line = _content.split('\n')[0]
                    _summary = _first_line[:100] + '...' if len(_first_line) > 100 else _first_line
                    _tui.tool_result(tool_call.name, _summary)
                else:
                    _err = tool_result.content or 'error'
                    try:
                        from .tui_heal import sanitize as _tui_sanitize
                        _err = _tui_sanitize(_err)
                    except Exception:
                        pass
                    _tui.tool_error(tool_call.name, _err)
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_result(
                        tool_call.name,
                        ok=tool_result.ok,
                        metadata=tool_result.metadata,
                    )
                plugin_messages = self._plugin_tool_result_messages(tool_call.name)
                policy_messages = self._hook_policy_tool_result_messages(tool_call.name)
                if plugin_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['plugin_messages'] = list(plugin_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in plugin_messages:
                        stream_events.append(
                            {
                                'type': 'plugin_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if policy_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['hook_policy_messages'] = list(policy_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in policy_messages:
                        stream_events.append(
                            {
                                'type': 'hook_policy_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if tool_result.metadata.get('error_kind') == 'permission_denied':
                    stream_events.append(
                        {
                            'type': 'tool_permission_denial',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'reason': tool_result.content,
                            'source': (
                                'hook_policy'
                                if tool_result.metadata.get('action') == 'hook_policy_block'
                                else 'tool_runtime'
                            ),
                        }
                    )
                session.finalize_tool(
                    tool_message_index,
                    content=serialize_tool_result(tool_result),
                    metadata={
                        'phase': 'completed',
                        'plugin_preflight_messages': list(plugin_preflight_messages),
                        'hook_policy_preflight_messages': list(policy_preflight_messages),
                        **dict(tool_result.metadata),
                    },
                    stop_reason='tool_completed',
                )
                stream_events.append(
                    {
                        'type': 'tool_result',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                        'ok': tool_result.ok,
                        'metadata': dict(tool_result.metadata),
                    }
                )
                self._append_runtime_tool_followup_events(
                    stream_events,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                plugin_runtime_message = self._build_plugin_tool_runtime_message(
                    tool_name=tool_call.name,
                    preflight_messages=plugin_preflight_messages,
                    block_message=plugin_block_message,
                    plugin_messages=plugin_messages,
                    hook_policy_preflight_messages=policy_preflight_messages,
                    hook_policy_block_message=policy_block_message,
                    hook_policy_messages=policy_messages,
                    delegate_preflight_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_preflight_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                    delegate_after_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_after_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                )
                if plugin_runtime_message is not None:
                    session.append_user(
                        plugin_runtime_message,
                        metadata={
                            'kind': 'plugin_tool_runtime',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'plugin_blocked': plugin_block_message is not None,
                            'plugin_message_count': len(plugin_messages),
                            'plugin_preflight_count': len(plugin_preflight_messages),
                        },
                        message_id=f'plugin_tool_runtime_{tool_call.id}',
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_context',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': f'plugin_tool_runtime_{tool_call.id}',
                            'blocked': plugin_block_message is not None,
                            'message_count': len(plugin_messages),
                            'preflight_count': len(plugin_preflight_messages),
                        }
                    )
                self._refresh_runtime_views_for_tool_result(tool_call.name, tool_result)
                history_entry = self._build_file_history_entry(
                    tool_call=tool_call,
                    tool_result=tool_result,
                    turn_index=turn_index,
                )
                if history_entry is not None:
                    file_history.append(history_entry)

        result = AgentRunResult(
            final_output=(
                last_content
                or 'Stopped: max turns reached before the model produced a final answer.'
            ),
            turns=self.runtime_config.max_turns,
            tool_calls=tool_calls,
            transcript=session.transcript(),
            events=tuple(stream_events),
            usage=total_usage,
            total_cost_usd=total_cost_usd,
            stop_reason='max_turns',
            file_history=tuple(file_history),
            session_id=session_id,
            scratchpad_directory=(
                str(scratchpad_directory) if scratchpad_directory is not None else None
            ),
        )
        result = self._append_runtime_after_turn_events(
            result,
            prompt=effective_prompt,
            turn_index=self.runtime_config.max_turns,
        )
        result = self._persist_session(session, result)
        self.last_run_result = result
        return result

    def _should_use_state_machine_outer_loop(self) -> bool:
        return (
            os.environ.get('LATTI_USE_STATE_MACHINE') != '0'
            and os.environ.get('LATTI_USE_LEGACY_LOOP') != '1'
        )

    def _new_stream_events(self) -> list[dict[str, object]]:
        if self.runtime_event_sink is None:
            return []
        return _ObservableEventList(self.runtime_event_sink)

    def _emit_runtime_event(self, event: dict[str, object]) -> None:
        if self.runtime_event_sink is None:
            return
        try:
            self.runtime_event_sink(dict(event))
        except Exception:
            pass

    def _build_state_machine_llm_action_payload(
        self,
        session: AgentSessionState,
        tool_specs: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            'messages': session.to_openai_messages(),
            'tools': tool_specs,
            'output_schema': self.runtime_config.output_schema,
            'model_override': self._route_model(session),
        }

    def _runtime_tool_queue_payload(
        self,
        pending_tool_calls: list[ToolCall],
    ) -> list[dict[str, object]]:
        return [
            {
                'id': tool_call.id,
                'name': tool_call.name,
                'arguments': dict(tool_call.arguments or {}),
            }
            for tool_call in pending_tool_calls
        ]

    def _run_prompt_via_state_machine_outer_loop(
        self,
        *,
        effective_prompt: str,
        session: AgentSessionState,
        session_id: str,
        scratchpad_directory: Path | None,
        tool_specs: list[dict[str, object]],
        starting_usage: UsageStats,
        starting_cost_usd: float,
        starting_tool_calls: int,
        starting_session_turns: int,
        starting_model_calls: int,
        delegated_tasks: int,
        file_history: list[dict[str, object]],
        stream_events: list[dict[str, object]],
    ) -> AgentRunResult:
        from .state_machine_controllers import RuntimeLoopController

        self._bind_state_machine_session(session_id)
        controller = RuntimeLoopController()
        total_usage = starting_usage
        total_cost_usd = starting_cost_usd
        tool_calls = starting_tool_calls
        model_calls = starting_model_calls
        last_content = ''
        assistant_response_segments: list[str] = []
        consecutive_empty_responses = 0
        pending_tool_calls: list[ToolCall] = []
        awaiting_model = True

        for turn_index in itertools.count(1):
            self._snip_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            self._compact_session_if_needed(
                session,
                stream_events,
                turn_index=turn_index,
            )
            preflight = self._preflight_prompt_length(
                session,
                stream_events,
                turn_index=turn_index,
            )
            if preflight.usage_increment.total_tokens or preflight.model_calls_increment:
                total_usage = total_usage + preflight.usage_increment
                total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                model_calls += preflight.model_calls_increment
                budget_after_preflight = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_preflight.exceeded:
                    result = AgentRunResult(
                        final_output=(
                            budget_after_preflight.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    return self._persist_session(session, result)
            if preflight.stop_reason is not None:
                result = AgentRunResult(
                    final_output=preflight.reason or 'Stopped before the next model call.',
                    turns=max(turn_index - 1, 0),
                    tool_calls=tool_calls,
                    transcript=session.transcript(),
                    events=tuple(stream_events),
                    usage=total_usage,
                    total_cost_usd=total_cost_usd,
                    stop_reason=preflight.stop_reason,
                    file_history=tuple(file_history),
                    session_id=session_id,
                    scratchpad_directory=(
                        str(scratchpad_directory) if scratchpad_directory is not None else None
                    ),
                )
                result = self._append_runtime_after_turn_events(
                    result,
                    prompt=effective_prompt,
                    turn_index=max(turn_index - 1, 0),
                )
                return self._persist_session(session, result)

            while True:
                runtime_context = {
                    'awaiting_model': awaiting_model,
                    'pending_tool_calls': self._runtime_tool_queue_payload(pending_tool_calls),
                    'next_llm_action': self._build_state_machine_llm_action_payload(
                        session,
                        tool_specs,
                    ),
                }
                if self._sm_state is not None:
                    # MERGE not REPLACE: last_verdict/last_error_text are threaded
                    # by _evaluate_state_after_step on every step. with_runtime
                    # used to wipe the dict each loop iteration, defeating the
                    # verdict-driven controller behavior.
                    merged_runtime = (
                        dict(self._sm_state.runtime)
                        if isinstance(self._sm_state.runtime, dict)
                        else {}
                    )
                    merged_runtime.update(runtime_context)
                    self._sm_state = self._sm_state.with_runtime(merged_runtime)
                decision = controller.pick(self._sm_state)
                if decision is None:
                    result = AgentRunResult(
                        final_output=(
                            last_content
                            or 'Stopped: runtime controller halted without a final answer.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='controller_halt',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    result = self._append_runtime_after_turn_events(
                        result,
                        prompt=effective_prompt,
                        turn_index=turn_index,
                    )
                    return self._persist_session(session, result)

                action = decision.chose
                stream_events.append(
                    {
                        'type': 'state_machine_decision',
                        'turn_index': turn_index,
                        'state_turn_id': decision.at_state_turn_id,
                        'action_kind': action.kind,
                        'rationale': decision.rationale,
                        'decided_by': decision.decided_by,
                        'confidence': decision.confidence,
                    }
                )

                if action.kind == 'llm_call':
                    model_override = (
                        action.payload.get('model_override')
                        if isinstance(action.payload.get('model_override'), str)
                        else None
                    )
                    try:
                        turn, turn_events = self._query_model_via_state_machine(
                            session,
                            tool_specs,
                            model_override=model_override,
                            action=action,
                            rationale=decision.rationale,
                            decided_by=decision.decided_by,
                        )
                    except OpenAICompatError as exc:
                        if self._is_prompt_too_long_error(exc) and self._reactive_compact_session(
                            session,
                            stream_events,
                            turn_index=turn_index,
                        ):
                            continue
                        result = AgentRunResult(
                            final_output=str(exc),
                            turns=max(turn_index - 1, 0),
                            tool_calls=tool_calls,
                            transcript=session.transcript(),
                            events=tuple(stream_events),
                            usage=total_usage,
                            total_cost_usd=total_cost_usd,
                            stop_reason='backend_error',
                            file_history=tuple(file_history),
                            session_id=session_id,
                            scratchpad_directory=(
                                str(scratchpad_directory) if scratchpad_directory is not None else None
                            ),
                        )
                        result = self._append_runtime_after_turn_events(
                            result,
                            prompt=effective_prompt,
                            turn_index=turn_index,
                        )
                        return self._persist_session(session, result)

                    stream_events.extend(event.to_dict() for event in turn_events)
                    # Drain any per-tool eval events stashed since last LLM
                    # step (so multi-tool 'replan' verdicts survive), then
                    # emit fresh eval against current state.
                    if self._pending_eval_events:
                        stream_events.extend(self._pending_eval_events)
                        self._pending_eval_events.clear()
                    stream_events.extend(self._evaluate_state_after_step())
                    model_calls += 1
                    total_usage = total_usage + turn.usage
                    total_cost_usd = self.model_config.pricing.estimate_cost_usd(total_usage)
                    last_content = turn.content

                    budget_after_model = self._check_budget(
                        total_usage,
                        total_cost_usd,
                        tool_calls=tool_calls,
                        delegated_tasks=delegated_tasks,
                        model_calls=model_calls,
                        session_turns=starting_session_turns + turn_index,
                    )
                    if budget_after_model.exceeded:
                        result = AgentRunResult(
                            final_output=(
                                budget_after_model.reason
                                or 'Stopped because the runtime budget was exceeded.'
                            ),
                            turns=turn_index,
                            tool_calls=tool_calls,
                            transcript=session.transcript(),
                            events=tuple(stream_events),
                            usage=total_usage,
                            total_cost_usd=total_cost_usd,
                            stop_reason='budget_exceeded',
                            file_history=tuple(file_history),
                            session_id=session_id,
                            scratchpad_directory=(
                                str(scratchpad_directory) if scratchpad_directory is not None else None
                            ),
                        )
                        return self._persist_session(session, result)

                    if not turn.content.strip() and not turn.tool_calls:
                        consecutive_empty_responses += 1
                    else:
                        consecutive_empty_responses = 0
                    if consecutive_empty_responses >= 3:
                        result = AgentRunResult(
                            final_output=(
                                'Stopped: model returned 3 consecutive empty responses. '
                                'This usually means the input is not a valid prompt.'
                            ),
                            turns=turn_index,
                            tool_calls=tool_calls,
                            transcript=session.transcript(),
                            events=tuple(stream_events),
                            usage=total_usage,
                            total_cost_usd=total_cost_usd,
                            stop_reason='empty_responses',
                            file_history=tuple(file_history),
                            session_id=session_id,
                            scratchpad_directory=(
                                str(scratchpad_directory) if scratchpad_directory is not None else None
                            ),
                        )
                        return self._persist_session(session, result)

                    if not turn.tool_calls:
                        assistant_response_segments.append(turn.content)
                        if self._should_continue_response(turn):
                            session.append_user(
                                self._build_continuation_prompt(),
                                metadata={
                                    'kind': 'continuation_request',
                                    'continuation_index': len(assistant_response_segments),
                                },
                                message_id=f'continuation_{turn_index}',
                            )
                            stream_events.append(
                                {
                                    'type': 'continuation_request',
                                    'reason': turn.finish_reason,
                                    'continuation_index': len(assistant_response_segments),
                                }
                            )
                            last_content = ''.join(assistant_response_segments)
                            awaiting_model = True
                            pending_tool_calls = []
                            break
                        final_output = ''.join(assistant_response_segments)
                        final_output = apply_response_gate(
                            final_output,
                            bypass=os.environ.get('LATTI_GATE', '1') == '0',
                        )
                        result = AgentRunResult(
                            final_output=final_output,
                            turns=turn_index,
                            tool_calls=tool_calls,
                            transcript=session.transcript(),
                            events=tuple(stream_events),
                            usage=total_usage,
                            total_cost_usd=total_cost_usd,
                            stop_reason=turn.finish_reason,
                            file_history=tuple(file_history),
                            session_id=session_id,
                            scratchpad_directory=(
                                str(scratchpad_directory) if scratchpad_directory is not None else None
                            ),
                        )
                        result = self._append_runtime_after_turn_events(
                            result,
                            prompt=effective_prompt,
                            turn_index=turn_index,
                        )
                        return self._persist_session(session, result)

                    pending_tool_calls = list(turn.tool_calls)
                    awaiting_model = False
                    continue

                if action.kind != 'tool_call':
                    result = AgentRunResult(
                        final_output=f'Unsupported state-machine action kind: {action.kind}',
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='unsupported_action',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    return self._persist_session(session, result)

                if not pending_tool_calls:
                    awaiting_model = True
                    continue

                tool_call = pending_tool_calls.pop(0)
                assistant_response_segments.clear()
                tool_calls += 1
                if tool_call.name == 'delegate_agent':
                    delegated_tasks += self._delegated_task_units(tool_call.arguments)
                budget_after_tool_request = self._check_budget(
                    total_usage,
                    total_cost_usd,
                    tool_calls=tool_calls,
                    delegated_tasks=delegated_tasks,
                    model_calls=model_calls,
                    session_turns=starting_session_turns + turn_index,
                )
                if budget_after_tool_request.exceeded:
                    stream_events.append(
                        {
                            'type': 'task_budget_exceeded',
                            'turn_index': turn_index,
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'reason': budget_after_tool_request.reason,
                        }
                    )
                    result = AgentRunResult(
                        final_output=(
                            budget_after_tool_request.reason
                            or 'Stopped because the runtime budget was exceeded.'
                        ),
                        turns=turn_index,
                        tool_calls=tool_calls,
                        transcript=session.transcript(),
                        events=tuple(stream_events),
                        usage=total_usage,
                        total_cost_usd=total_cost_usd,
                        stop_reason='budget_exceeded',
                        file_history=tuple(file_history),
                        session_id=session_id,
                        scratchpad_directory=(
                            str(scratchpad_directory) if scratchpad_directory is not None else None
                        ),
                    )
                    return self._persist_session(session, result)

                tool_result = None
                tool_message_index = session.start_tool(
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                    message_id=f'tool_{len(session.messages)}',
                    metadata={'phase': 'starting'},
                )
                stream_events.append(
                    {
                        'type': 'tool_start',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                    }
                )
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_attempt(tool_call.name, blocked=False)
                plugin_preflight_messages = self._plugin_tool_preflight_messages(tool_call.name)
                policy_preflight_messages = self._hook_policy_tool_preflight_messages(
                    tool_call.name
                )
                if plugin_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'plugin_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(plugin_preflight_messages),
                        }
                    )
                if policy_preflight_messages:
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_preflight',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message_count': len(policy_preflight_messages),
                        }
                    )
                plugin_block_message = self._plugin_block_message(tool_call.name)
                policy_block_message = self._hook_policy_block_message(tool_call.name)
                if plugin_block_message is not None:
                    if self.plugin_runtime is not None:
                        blocked_attempts = int(
                            self.plugin_runtime.session_state.get('blocked_tool_attempts', 0)
                        )
                        self.plugin_runtime.session_state['blocked_tool_attempts'] = (
                            blocked_attempts + 1
                        )
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=plugin_block_message,
                        metadata={
                            'action': 'plugin_block',
                            'plugin_blocked': True,
                            'plugin_block_message': plugin_block_message,
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': plugin_block_message,
                        }
                    )
                if policy_block_message is not None:
                    tool_result = ToolExecutionResult(
                        name=tool_call.name,
                        ok=False,
                        content=policy_block_message,
                        metadata={
                            'action': 'hook_policy_block',
                            'hook_policy_blocked': True,
                            'hook_policy_block_message': policy_block_message,
                            'error_kind': 'permission_denied',
                        },
                    )
                    stream_events.append(
                        {
                            'type': 'hook_policy_tool_block',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'message': policy_block_message,
                        }
                    )
                from . import tui as _tui
                _tool_detail = self._tool_call_detail(tool_call)
                _tui.tool_start(tool_call.name, _tool_detail)

                if tool_result is None:
                    tool_result = self._dispatch_via_state_machine(
                        tool_call,
                        session=session,
                        tool_message_index=tool_message_index,
                        stream_events=stream_events,
                        rationale=decision.rationale,
                        decided_by=decision.decided_by,
                    )
                if tool_result is None:
                    raise RuntimeError(
                        f'Tool executor returned no final result for {tool_call.name}'
                    )
                if tool_result.ok:
                    _content = tool_result.content or 'ok'
                    try:
                        from .tui_heal import sanitize as _tui_sanitize
                        _content = _tui_sanitize(_content)
                    except Exception:
                        pass
                    _first_line = _content.split('\n')[0]
                    _summary = _first_line[:100] + '...' if len(_first_line) > 100 else _first_line
                    _tui.tool_result(tool_call.name, _summary)
                else:
                    _err = tool_result.content or 'error'
                    try:
                        from .tui_heal import sanitize as _tui_sanitize
                        _err = _tui_sanitize(_err)
                    except Exception:
                        pass
                    _tui.tool_error(tool_call.name, _err)
                if self.plugin_runtime is not None:
                    self.plugin_runtime.record_tool_result(
                        tool_call.name,
                        ok=tool_result.ok,
                        metadata=tool_result.metadata,
                    )
                plugin_messages = self._plugin_tool_result_messages(tool_call.name)
                policy_messages = self._hook_policy_tool_result_messages(tool_call.name)
                if plugin_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['plugin_messages'] = list(plugin_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in plugin_messages:
                        stream_events.append(
                            {
                                'type': 'plugin_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if policy_messages:
                    merged_metadata = dict(tool_result.metadata)
                    merged_metadata['hook_policy_messages'] = list(policy_messages)
                    tool_result = ToolExecutionResult(
                        name=tool_result.name,
                        ok=tool_result.ok,
                        content=tool_result.content,
                        metadata=merged_metadata,
                    )
                    for message in policy_messages:
                        stream_events.append(
                            {
                                'type': 'hook_policy_tool_hook',
                                'tool_name': tool_call.name,
                                'tool_call_id': tool_call.id,
                                'message_id': session.messages[tool_message_index].message_id,
                                'message': message,
                            }
                        )
                if tool_result.metadata.get('error_kind') == 'permission_denied':
                    stream_events.append(
                        {
                            'type': 'tool_permission_denial',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': session.messages[tool_message_index].message_id,
                            'reason': tool_result.content,
                            'source': (
                                'hook_policy'
                                if tool_result.metadata.get('action') == 'hook_policy_block'
                                else 'tool_runtime'
                            ),
                        }
                    )
                session.finalize_tool(
                    tool_message_index,
                    content=serialize_tool_result(tool_result),
                    metadata={
                        'phase': 'completed',
                        'plugin_preflight_messages': list(plugin_preflight_messages),
                        'hook_policy_preflight_messages': list(policy_preflight_messages),
                        **dict(tool_result.metadata),
                    },
                    stop_reason='tool_completed',
                )
                stream_events.append(
                    {
                        'type': 'tool_result',
                        'tool_name': tool_call.name,
                        'tool_call_id': tool_call.id,
                        'message_id': session.messages[tool_message_index].message_id,
                        'ok': tool_result.ok,
                        'metadata': dict(tool_result.metadata),
                    }
                )
                self._append_runtime_tool_followup_events(
                    stream_events,
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
                plugin_runtime_message = self._build_plugin_tool_runtime_message(
                    tool_name=tool_call.name,
                    preflight_messages=plugin_preflight_messages,
                    block_message=plugin_block_message,
                    plugin_messages=plugin_messages,
                    hook_policy_preflight_messages=policy_preflight_messages,
                    hook_policy_block_message=policy_block_message,
                    hook_policy_messages=policy_messages,
                    delegate_preflight_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_preflight_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                    delegate_after_messages=tuple(
                        message
                        for message in tool_result.metadata.get(
                            'plugin_delegate_after_messages',
                            [],
                        )
                        if isinstance(message, str) and message
                    ),
                )
                if plugin_runtime_message is not None:
                    session.append_user(
                        plugin_runtime_message,
                        metadata={
                            'kind': 'plugin_tool_runtime',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'plugin_blocked': plugin_block_message is not None,
                            'plugin_message_count': len(plugin_messages),
                            'plugin_preflight_count': len(plugin_preflight_messages),
                        },
                        message_id=f'plugin_tool_runtime_{tool_call.id}',
                    )
                    stream_events.append(
                        {
                            'type': 'plugin_tool_context',
                            'tool_name': tool_call.name,
                            'tool_call_id': tool_call.id,
                            'message_id': f'plugin_tool_runtime_{tool_call.id}',
                            'blocked': plugin_block_message is not None,
                            'message_count': len(plugin_messages),
                            'preflight_count': len(plugin_preflight_messages),
                        }
                    )
                self._refresh_runtime_views_for_tool_result(tool_call.name, tool_result)
                history_entry = self._build_file_history_entry(
                    tool_call=tool_call,
                    tool_result=tool_result,
                    turn_index=turn_index,
                )
                if history_entry is not None:
                    file_history.append(history_entry)

                awaiting_model = not pending_tool_calls
                if awaiting_model:
                    break
                continue

    def _route_model(self, session: AgentSessionState) -> str | None:
        """Use the model router and scars to pick the best model.

        Returns a model override string, or None to use the default.

        Scar routing takes priority when a successful past scar matches.
        Lessons from all similar scars are injected into the system prompt
        regardless of whether a model override fires, so the model always
        has the benefit of past experience.
        """
        # Extract last user message for classification
        last_user_msg = ''
        for msg in reversed(session.messages):
            if getattr(msg, 'role', None) == 'user':
                last_user_msg = getattr(msg, 'content', '') or ''
                break

        # Check scars — always inject lessons, optionally override model
        if self.scar_router is not None and last_user_msg:
            scar_decision = self.scar_router.route_problem(last_user_msg)

            # Inject lessons into the live session system prompt so the model
            # sees past experience as part of its context, not just routing.
            lessons = scar_decision.get('lessons_context', '')
            if lessons:
                self._inject_scar_lessons(session, lessons)

            # Only override the model when we have a confident scar match
            # (a successful past scar, not just any similar scar).
            if scar_decision.get('scar_matched') and scar_decision.get('model'):
                _tui.scar_match(
                    scar_id=scar_decision['scar_matched'],
                    lesson=scar_decision['lesson'],
                    model=scar_decision['model'],
                )
                return scar_decision['model']

        # Fall back to model router
        if self.model_router is None or not self.model_router.config.enabled:
            return None
        decision = self.model_router.classify_turn(last_user_msg)
        if decision.tier.value != 'heavy':
            return decision.model
        return None

    def _inject_scar_lessons(
        self,
        session: AgentSessionState,
        lessons: str,
    ) -> None:
        """Append scar lessons to the last system prompt part in the session.

        This is best-effort: if the session structure doesn't support it,
        we silently skip rather than crashing the run.
        """
        try:
            if not hasattr(session, 'system_prompt_parts'):
                return
            parts = list(session.system_prompt_parts)
            if not parts:
                return
            # Append to the last part so it appears near the end of the
            # system prompt, close to the dynamic boundary.
            parts[-1] = parts[-1] + f'\n\n{lessons}'
            # AgentSessionState is frozen; use replace() to update
            object.__setattr__(session, 'system_prompt_parts', tuple(parts))
        except Exception:
            pass  # Best-effort; never disrupt the run

    def _query_model(
        self,
        session: AgentSessionState,
        tool_specs: list[dict[str, object]],
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        model_override = self._route_model(session)
        if os.environ.get('LATTI_USE_STATE_MACHINE') != '0':
            return self._query_model_via_state_machine(
                session,
                tool_specs,
                model_override=model_override,
            )
        if not self.runtime_config.stream_model_responses:
            turn = self.client.complete(
                session.to_openai_messages(),
                tool_specs,
                output_schema=self.runtime_config.output_schema,
                model_override=model_override,
            )
            assistant_tool_calls = tuple(
                {
                    'id': tool_call.id,
                    'type': 'function',
                    'function': {
                        'name': tool_call.name,
                        'arguments': json.dumps(
                            tool_call.arguments,
                            ensure_ascii=True,
                        ),
                    },
                }
                for tool_call in turn.tool_calls
            )
            session.append_assistant(
                turn.content,
                assistant_tool_calls,
                message_id=f'assistant_{len(session.messages)}',
                stop_reason=turn.finish_reason,
                usage=turn.usage,
            )
            # Display thinking if present (o1/o3 models)
            if turn.thinking:
                _tui.thinking_block(turn.thinking, token_count=turn.usage.reasoning_tokens or 0)
            return turn, ()

        assistant_index = session.start_assistant(
            message_id=f'assistant_{len(session.messages)}'
        )
        usage = UsageStats()
        finish_reason: str | None = None
        events: list[StreamEvent] = []
        thinking_text = ''

        # TUI stream renderer for formatted output
        from . import tui as _tui
        renderer = _tui.StreamRenderer()
        renderer.start()
        has_content = False

        for event in self.client.stream(
            session.to_openai_messages(),
            tool_specs,
            output_schema=self.runtime_config.output_schema,
            model_override=model_override,
        ):
            events.append(event)
            if event.type == 'thinking_delta':
                thinking_text += event.delta
            elif event.type == 'content_delta':
                session.append_assistant_delta(assistant_index, event.delta)
                renderer.token(event.delta)
                has_content = True
            elif event.type == 'tool_call_delta':
                session.merge_assistant_tool_call_delta(
                    assistant_index,
                    tool_call_index=event.tool_call_index or 0,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    arguments_delta=event.arguments_delta,
                )
            elif event.type == 'usage':
                usage = usage + event.usage
            elif event.type == 'message_stop':
                finish_reason = event.finish_reason

        if has_content:
            renderer.end()

        session.finalize_assistant(
            assistant_index,
            finish_reason=finish_reason,
            usage=usage,
        )
        assistant_message = session.messages[assistant_index]
        turn = AssistantTurn(
            content=assistant_message.content,
            tool_calls=self._tool_calls_from_message(assistant_message.tool_calls),
            finish_reason=finish_reason,
            raw_message=assistant_message.to_openai_message(),
            usage=usage,
            thinking=thinking_text,
        )
        # Display thinking if present (o1/o3 models)
        if thinking_text:
            _tui.thinking_block(thinking_text, token_count=usage.reasoning_tokens or 0)
        return turn, tuple(events)

    def _query_model_via_state_machine(
        self,
        session: AgentSessionState,
        tool_specs: list[dict[str, object]],
        *,
        model_override: str | None,
        action=None,
        rationale: str = 'llm_call via state-machine',
        decided_by: str = 'rule',
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        from .agent_state_machine import Action
        from .state_machine_operators import StreamingLLMOperator

        runner = self._ensure_state_machine_runner()
        self._bind_state_machine_session(self.active_session_id or 'sm_unknown')
        if action is None:
            action = Action(
                kind='llm_call',
                payload={
                    'messages': session.to_openai_messages(),
                    'tools': tool_specs,
                    'output_schema': self.runtime_config.output_schema,
                    'model_override': model_override,
                },
            )

        if not self.runtime_config.stream_model_responses:
            obs, new_state = runner.run_one_step(
                self._sm_state,
                action,
                rationale=rationale,
                decided_by=decided_by,
            )
            self._sm_state = new_state
            self._maybe_save_scar(action, obs)
            if obs.kind == 'error':
                raise OpenAICompatError(str(obs.payload.get('error', 'state-machine llm_call failed')))

            usage_payload = (
                obs.payload.get('usage')
                if isinstance(obs.payload.get('usage'), dict)
                else {}
            )
            usage = usage_from_payload(usage_payload)
            assistant_tool_calls = tuple(
                {
                    'id': tool_call.get('id'),
                    'type': 'function',
                    'function': {
                        'name': tool_call.get('name'),
                        'arguments': json.dumps(
                            tool_call.get('arguments') or {},
                            ensure_ascii=True,
                        ),
                    },
                }
                for tool_call in (obs.payload.get('tool_calls') or [])
                if isinstance(tool_call, dict)
            )
            session.append_assistant(
                str(obs.payload.get('content', '')),
                assistant_tool_calls,
                message_id=f'assistant_{len(session.messages)}',
                stop_reason=(
                    str(obs.payload.get('finish_reason'))
                    if obs.payload.get('finish_reason') is not None
                    else None
                ),
                usage=usage,
            )
            thinking_text = str(obs.payload.get('thinking') or '')
            if thinking_text:
                from . import tui as _tui
                _tui.thinking_block(thinking_text, token_count=usage.reasoning_tokens or 0)
            assistant_message = session.messages[-1]
            return AssistantTurn(
                content=assistant_message.content,
                tool_calls=self._tool_calls_from_message(assistant_message.tool_calls),
                finish_reason=assistant_message.stop_reason,
                raw_message=assistant_message.to_openai_message(),
                usage=usage,
                thinking=thinking_text,
            ), ()

        assistant_index = session.start_assistant(
            message_id=f'assistant_{len(session.messages)}'
        )
        usage = UsageStats()
        finish_reason: str | None = None
        events: list[StreamEvent] = []
        thinking_text = ''
        from . import tui as _tui
        renderer = _tui.StreamRenderer()
        renderer.start()
        has_content = False

        llm_op = next(
            op for op in runner.operators if isinstance(op, StreamingLLMOperator)
        )

        def _event_callback(event: StreamEvent, _action) -> None:
            nonlocal usage, finish_reason, thinking_text, has_content
            events.append(event)
            if event.type == 'thinking_delta':
                thinking_text += event.delta
            elif event.type == 'content_delta':
                session.append_assistant_delta(assistant_index, event.delta)
                renderer.token(event.delta)
                has_content = True
            elif event.type == 'tool_call_delta':
                session.merge_assistant_tool_call_delta(
                    assistant_index,
                    tool_call_index=event.tool_call_index or 0,
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    arguments_delta=event.arguments_delta,
                )
            elif event.type == 'usage':
                usage = usage + event.usage
            elif event.type == 'message_stop':
                finish_reason = event.finish_reason

        llm_op._event_callback = _event_callback
        try:
            obs, new_state = runner.run_one_step(
                self._sm_state,
                action,
                rationale=rationale,
                decided_by=decided_by,
            )
        finally:
            llm_op._event_callback = None
        self._sm_state = new_state
        self._maybe_save_scar(action, obs)
        if has_content:
            renderer.end()
        if obs.kind == 'error':
            raise OpenAICompatError(str(obs.payload.get('error', 'state-machine llm stream failed')))

        if usage.total_tokens == 0:
            usage_payload = (
                obs.payload.get('usage')
                if isinstance(obs.payload.get('usage'), dict)
                else {}
            )
            usage = usage_from_payload(usage_payload)
        if finish_reason is None and obs.payload.get('finish_reason') is not None:
            finish_reason = str(obs.payload.get('finish_reason'))
        if not thinking_text:
            thinking_text = str(obs.payload.get('thinking') or '')

        session.finalize_assistant(
            assistant_index,
            finish_reason=finish_reason,
            usage=usage,
        )
        assistant_message = session.messages[assistant_index]
        turn = AssistantTurn(
            content=assistant_message.content,
            tool_calls=self._tool_calls_from_message(assistant_message.tool_calls),
            finish_reason=finish_reason,
            raw_message=assistant_message.to_openai_message(),
            usage=usage,
            thinking=thinking_text,
        )
        if thinking_text:
            _tui.thinking_block(thinking_text, token_count=usage.reasoning_tokens or 0)
        return turn, tuple(events)

    def _ensure_state_machine_runner(self):
        if self._sm_runner is not None:
            return self._sm_runner
        from .state_machine_operators import (
            DelegateAgentOperator,
            RealLLMOperator,
            StreamingLLMOperator,
            ToolCallOperator,
        )
        from .state_machine_runner import StateMachineRunner
        from .state_machine_validators import (
            AnchorViolationValidator,
            NonEmptyContentValidator,
            ObservationShapeValidator,
        )
        from .state_machine_evaluators import (
            BudgetExhaustionEvaluator,
            ConsecutiveErrorEvaluator,
        )

        llm_operator = (
            StreamingLLMOperator(self.client)
            if self.runtime_config.stream_model_responses
            else RealLLMOperator(self.client)
        )
        # Anchor-violation validator (summary→active-constraint).
        # Reads live anchored messages from the session each turn so
        # mid-session NEVER: constraints are picked up without rebuild.
        def _live_anchors() -> list[str]:
            sess = self.last_session
            if sess is None:
                return []
            return [
                m.content for m in sess.messages
                if isinstance(m.metadata, dict)
                and m.metadata.get('anchor') is True
                and isinstance(m.content, str)
            ]
        self._sm_runner = StateMachineRunner(
            operators=[
                llm_operator,
                DelegateAgentOperator(self._execute_delegate_agent),
                ToolCallOperator(self.tool_registry, self.tool_context),
            ],
            validators=[
                ObservationShapeValidator(),
                NonEmptyContentValidator(),
                AnchorViolationValidator(anchors_provider=_live_anchors),
            ],
            # ConsecutiveErrorEvaluator returns 'replan' when last observation
            # is an error; today this only feeds telemetry, but it makes
            # error-driven control surfaces visible to the TUI.
            # TaskCompletionEvaluator deliberately NOT wired until task
            # decomposition lands in the production state path — without it
            # the evaluator would emit 'done' on every successful step.
            evaluators=[
                BudgetExhaustionEvaluator(),
                ConsecutiveErrorEvaluator(),
            ],
        )
        return self._sm_runner

    def _thread_eval_verdict_to_state(self, verdict: str) -> None:
        """Write the verdict into _sm_state.runtime['last_verdict'] so the
        next controller.pick() can read it via the existing runtime channel.

        State is frozen so this constructs a new state via dataclasses.replace.
        Controllers that don't read 'last_verdict' continue to work unchanged.

        Always writes — including 'continue' — so verdict-driven controller
        behavior is one-shot. If a 'replan' fires, drives a reminder
        injection, and the next step succeeds, this overwrites with
        'continue' and the turn after that does NOT re-inject the
        reminder. (Pre-fix: 'continue' was filtered, so a single 'replan'
        verdict would persist and re-inject every subsequent turn.)
        """
        if self._sm_state is None:
            return
        from dataclasses import replace as _dc_replace
        current_runtime = (
            dict(self._sm_state.runtime) if isinstance(self._sm_state.runtime, dict) else {}
        )
        current_runtime['last_verdict'] = verdict
        self._sm_state = _dc_replace(self._sm_state, runtime=current_runtime)

    def _evaluate_state_after_step(self) -> list[dict]:
        """Run wired evaluators against current _sm_state, return telemetry events.

        Side-effect: when an evaluator produces a non-'continue' verdict, threads
        it into _sm_state.runtime['last_verdict'] so the next controller.pick()
        can react. Threading is opt-in for controllers — silent no-op for those
        that don't read runtime['last_verdict'].
        """
        if self._sm_runner is None or self._sm_state is None:
            return []
        try:
            results = self._sm_runner.evaluate(self._sm_state, goal=None)
        except Exception:
            return []
        # Pair results with evaluator names by index — runner.evaluate iterates
        # evaluators in registration order, so result[i] corresponds to
        # runner.evaluators[i].
        evaluator_names: list[str] = []
        for ev in self._sm_runner.evaluators:
            try:
                evaluator_names.append(ev.name)
            except Exception:
                evaluator_names.append(type(ev).__name__)
        events: list[dict] = []
        # Precedence for threading: 'escalate' > 'timeout' > 'done' > 'replan' > 'continue'.
        # If multiple evaluators fire, the most-terminal verdict wins on the
        # state.runtime channel. 'continue' is now also threaded so verdict-
        # driven controller behavior (e.g. replan-injects-reminder) becomes
        # one-shot — see _thread_eval_verdict_to_state docstring.
        _PRECEDENCE = {'escalate': 4, 'timeout': 3, 'done': 2, 'replan': 1, 'continue': 0}
        winning_verdict: str | None = None
        winning_rank = -1
        for i, r in enumerate(results):
            name = evaluator_names[i] if i < len(evaluator_names) else 'unknown'
            events.append({
                'type': 'state_machine_evaluation',
                'evaluator': name,
                'verdict': r.verdict,
                'score': r.score,
                'note': r.note,
                'dimensions': dict(r.dimensions),
            })
            rank = _PRECEDENCE.get(r.verdict, 0)
            if rank > winning_rank:
                winning_rank = rank
                winning_verdict = r.verdict
        if winning_verdict:
            # Always thread the winning verdict — including 'continue' —
            # so verdict-driven controller behavior is one-shot rather
            # than persistent across turns.
            self._thread_eval_verdict_to_state(winning_verdict)
            # On 'replan', also surface the actual last-observation error
            # text so the controller's reminder injection can be specific
            # rather than generic. Cleared on subsequent non-error turns
            # by the same one-shot mechanism.
            if winning_verdict == 'replan' and self._sm_state is not None:
                err_text = self._extract_last_error_text()
                if err_text:
                    self._thread_runtime_field('last_error_text', err_text)
        return events

    def _extract_last_error_text(self) -> str:
        """Pull a human-readable error string out of the most recent
        Observation when its kind=='error'. Returns empty string if no
        observation, no error, or no readable error field.
        """
        if self._sm_state is None or self._sm_state.last_observation is None:
            return ''
        obs = self._sm_state.last_observation
        if obs.kind != 'error':
            return ''
        payload = obs.payload if isinstance(obs.payload, dict) else {}
        for key in ('error', 'message', 'reason', 'detail'):
            v = payload.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return ''

    def _thread_runtime_field(self, field_name: str, value: object) -> None:
        """Write an arbitrary key into _sm_state.runtime via dataclass.replace."""
        if self._sm_state is None:
            return
        from dataclasses import replace as _dc_replace
        current_runtime = (
            dict(self._sm_state.runtime) if isinstance(self._sm_state.runtime, dict) else {}
        )
        current_runtime[field_name] = value
        self._sm_state = _dc_replace(self._sm_state, runtime=current_runtime)

    def state_machine_memory(self):
        """Lazy-construct and return a LattiMemoryStore for ~/.latti/memory.

        Returns None when ~/.latti is unavailable. Used by code paths that
        want to persist scars/SOPs/lessons via the typed MemoryRecord schema.
        """
        if self._sm_memory is not None:
            return self._sm_memory
        try:
            from pathlib import Path as _P
            from .state_machine_memory import LattiMemoryStore
            path = _P.home() / '.latti' / 'memory'
            self._sm_memory = LattiMemoryStore(path)
        except Exception:
            return None
        return self._sm_memory

    def state_machine_goals(self):
        """Lazy-construct and return a GoalRegistry for ~/.latti/goals/."""
        if self._sm_goals is not None:
            return self._sm_goals
        try:
            from pathlib import Path as _P
            from .state_machine_goals import GoalRegistry
            self._sm_goals = GoalRegistry(_P.home() / '.latti' / 'goals')
        except Exception:
            return None
        return self._sm_goals

    def state_machine_tasks(self):
        """Lazy-construct and return a TaskTracker for ~/.latti/goals/."""
        if self._sm_tasks is not None:
            return self._sm_tasks
        try:
            from pathlib import Path as _P
            from .state_machine_goals import TaskTracker
            self._sm_tasks = TaskTracker(_P.home() / '.latti' / 'goals')
        except Exception:
            return None
        return self._sm_tasks

    def _bind_state_machine_session(self, session_id: str) -> None:
        """Ensure typed state is bound to the active session before the turn runs."""
        if os.environ.get('LATTI_USE_STATE_MACHINE') == '0':
            return

        from .agent_state_machine import State

        current_session_id = getattr(self._sm_state, 'session_id', None)
        if self._sm_state is not None and current_session_id == session_id:
            return

        # Use the runtime_config's actual cost cap if set; otherwise treat
        # as unlimited (float('inf')) so BudgetExhaustionEvaluator doesn't
        # falsely fire 'timeout' on a fresh state with budget=0.0. The
        # legacy budget check at agent_runtime.py:_check_budget remains the
        # canonical exit; the evaluator is signal-only today.
        cap = self.runtime_config.budget_config.max_total_cost_usd
        budget_usd = cap if cap is not None else float('inf')
        self._sm_state = State.fresh(
            session_id=session_id,
            budget_usd=budget_usd,
            available_tools=tuple(self.tool_registry.keys()) if self.tool_registry else (),
        )

    def _restore_persisted_state_machine_state(
        self,
        stored_session: StoredAgentSession,
    ) -> bool:
        if os.environ.get('LATTI_USE_STATE_MACHINE') == '0':
            return False
        typed_state = (
            stored_session.typed_state
            if isinstance(getattr(stored_session, 'typed_state', None), dict)
            else {}
        )
        if not typed_state:
            return False
        from .agent_state_machine import state_from_dict

        restored = state_from_dict(typed_state)
        if restored is None:
            return False
        if restored.session_id != stored_session.session_id:
            restored = State(
                turn_id=restored.turn_id,
                session_id=stored_session.session_id,
                beliefs=restored.beliefs,
                open_tasks=restored.open_tasks,
                available_tools=restored.available_tools,
                runtime=restored.runtime,
                budget_remaining_usd=restored.budget_remaining_usd,
                last_observation=restored.last_observation,
            )
        self._sm_state = restored
        return True

    def _dispatch_via_state_machine(
        self,
        tool_call,
        session=None,
        tool_message_index: int | None = None,
        stream_events: list | None = None,
        rationale: str | None = None,
        decided_by: str = 'rule',
    ) -> 'ToolExecutionResult':
        """State-machine dispatch path. Default-on since 2026-04-29 (Step 6).

        Active when ``LATTI_USE_STATE_MACHINE != '0'`` (i.e. by default).
        Routes a single tool call through StateMachineRunner using
        ToolCallOperator, logs a PolicyDecision, and converts the resulting
        Observation back to the ToolExecutionResult shape that downstream
        code expects.

        Streaming preservation: when ``session``, ``tool_message_index``, and
        ``stream_events`` are passed, deltas are mirrored to the legacy
        session/event surface in real time instead of batched. Without them
        (e.g. in tests), deltas are still collected in observation.payload.
        """
        # Local imports keep flag-off path free of state-machine dependencies.
        from .agent_state_machine import Action
        from .state_machine_operators import ToolCallOperator
        from .agent_types import ToolExecutionResult

        self._ensure_state_machine_runner()
        if self._sm_state is None:
            self._bind_state_machine_session(self.active_session_id or 'sm_unknown')

        # Wire delta callback for this dispatch only — mirrors the legacy
        # streaming path so the TUI sees live deltas instead of batched output.
        if session is not None and tool_message_index is not None and stream_events is not None:
            def _on_delta(content: str, stream: 'str | None', _action) -> None:
                session.append_tool_delta(
                    tool_message_index, content,
                    metadata={'last_stream': stream or 'tool'},
                )
                stream_events.append({
                    'type': 'tool_delta',
                    'tool_name': tool_call.name,
                    'tool_call_id': tool_call.id,
                    'message_id': session.messages[tool_message_index].message_id,
                    'stream': stream,
                    'delta': content,
                })
            for op in self._sm_runner.operators:
                if isinstance(op, ToolCallOperator):
                    op._delta_callback = _on_delta
                    break
        else:
            # Reset callback on any pre-existing ToolCallOperator (clean state)
            for op in self._sm_runner.operators:
                if isinstance(op, ToolCallOperator):
                    op._delta_callback = None
                    break

        action = Action(
            kind='tool_call',
            payload={
                'tool_name': tool_call.name,
                'arguments': dict(tool_call.arguments or {}),
            },
        )
        try:
            observation, new_state = self._sm_runner.run_one_step(
                self._sm_state, action,
                rationale=rationale or f'agent_runtime dispatch: {tool_call.name}',
                decided_by=decided_by,
            )
        finally:
            # Always clear the callback after dispatch — bounded state mutation.
            for op in self._sm_runner.operators:
                if isinstance(op, ToolCallOperator):
                    op._delta_callback = None
                    break
        self._sm_state = new_state

        # Auto-save scar to LattiMemoryStore on contract violations:
        # - blocking validations (Operator returned wrong shape)
        # - constitutional wall blocks (force-push, secrets, rm -rf, etc.)
        # Each event becomes a typed MemoryRecord persisted under ~/.latti/memory/.
        self._maybe_save_scar(action, observation)

        # Run evaluators against the post-step state and stash any verdicts.
        # The LLM-call hook drains this queue so multi-tool turns don't
        # clobber a 'replan' verdict (state.last_observation gets overwritten
        # by each subsequent tool's observation).
        eval_events = self._evaluate_state_after_step()
        if eval_events:
            self._pending_eval_events.extend(eval_events)

        # Convert Observation → ToolExecutionResult
        if observation.kind == 'success':
            return ToolExecutionResult(
                name=observation.payload.get('tool_name', tool_call.name),
                ok=True,
                content=observation.payload.get('content', ''),
                metadata=observation.payload.get('metadata', {}) or {},
            )
        return ToolExecutionResult(
            name=observation.payload.get('tool_name', tool_call.name),
            ok=False,
            content=observation.payload.get('content') or observation.payload.get('error', 'state-machine dispatch failed'),
            metadata=observation.payload.get('metadata', {}) or {},
        )

    def _register_goal_from_prompt(self, prompt: str, session_id: str):
        """Register a typed Goal in GoalRegistry whenever a real user prompt
        starts a session. The Goal's title is the first 80 chars of the prompt;
        full prompt persists as a success criterion. Failures are silent.

        Returns the registered Goal (or None if registration was skipped).
        """
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        if os.environ.get('LATTI_USE_STATE_MACHINE') == '0':
            return None
        try:
            from .agent_state_machine import Goal
            registry = self.state_machine_goals()
            if registry is None:
                return None
            title = prompt.strip().splitlines()[0][:80]
            goal = Goal.new(
                title=title,
                success_criteria=(prompt.strip()[:500],),
                owner='user',
            )
            registry.register(goal)
            return goal
        except Exception:
            return None

    def _mark_goal_done(self, goal) -> None:
        """Append a 'done' line to GoalRegistry for this goal. Best-effort —
        any failure (registry missing, FS error) is silent so completion-
        marking can never break a successful run."""
        if goal is None:
            return
        try:
            registry = self.state_machine_goals()
            if registry is None:
                return
            registry.mark_done(goal.id)
        except Exception:
            pass

    def _maybe_save_scar(self, action, observation) -> None:
        """If the observation indicates a contract violation, persist a scar.

        Triggers:
          - observation.payload['blocking_validations'] present (Validator blocked)
          - observation.payload['wall'] present (constitutional wall blocked)

        The scar goes to ~/.latti/memory/ via LattiMemoryStore as a typed
        MemoryRecord(kind='scar'). Failures are silent — scar persistence
        must never break the dispatch path.
        """
        # Only error observations can be scar-worthy
        if observation.kind != 'error':
            return
        payload = observation.payload or {}
        is_wall_block = bool(payload.get('wall'))
        is_validator_block = 'blocking_validations' in payload
        if not (is_wall_block or is_validator_block):
            return

        try:
            from .agent_state_machine import MemoryRecord
            store = self.state_machine_memory()
            if store is None:
                return

            session_id = getattr(self._sm_state, 'session_id', None) if self._sm_state else None
            tool_name = payload.get('tool_name') or action.payload.get('tool_name', 'unknown')

            if is_wall_block:
                wall = payload.get('wall', 'unknown_wall')
                kind_label = f'wall_{wall}'
                body = (
                    f'**TRIGGER:** action.kind={action.kind} tool={tool_name!r}\n\n'
                    f'**WALL:** {wall}\n\n'
                    f'**ACTION PAYLOAD:** {dict(action.payload)}\n\n'
                    f'**WHY THIS IS A SCAR:** A constitutional wall blocked this action '
                    f'before operator dispatch. The next instance must recognize this '
                    f'pattern and avoid the same shape.'
                )
                description = f'wall {wall} blocked {tool_name!r}'
            else:
                blocking = payload.get('blocking_validations') or []
                check_names = [
                    c.get('name', '?')
                    for v in blocking
                    for c in v.get('checks', [])
                    if not c.get('passed', True)
                ]
                # Distinct check-name signatures → distinct scar files.
                # Identical signatures → same filename → overwrite (dedup).
                # Sort + cap to keep filename bounded and order-stable.
                _signature = '_'.join(sorted(set(check_names))[:3]) or 'unnamed'
                kind_label = f'validator_block_{_signature}'
                body = (
                    f'**TRIGGER:** action.kind={action.kind} tool={tool_name!r}\n\n'
                    f'**FAILED CHECKS:** {", ".join(check_names) or "(unnamed)"}\n\n'
                    f'**WHY THIS IS A SCAR:** A post-execution Validator blocked the '
                    f'observation. Either the Operator returned a misshapen result or '
                    f'the contract changed. Investigate before assuming legitimate use.'
                )
                description = f'validator blocked {tool_name!r} on {check_names[:2]}'

            record = MemoryRecord.new(
                kind='scar',
                body=body,
                source_session_id=session_id,
                source_turn_id=getattr(self._sm_state, 'turn_id', None) if self._sm_state else None,
            )
            store.save(record, name=kind_label, description=description)
        except Exception:
            # Scar persistence is best-effort. Never break the dispatch path.
            pass

    @staticmethod
    def _tool_call_detail(tool_call) -> str:
        """Extract a human-readable detail string for TUI display."""
        args = tool_call.arguments or {}
        name = tool_call.name
        if name in ('read_file', 'write_file', 'edit_file'):
            return str(args.get('path', ''))
        if name == 'bash':
            cmd = str(args.get('command', ''))
            # Strip leading `cd /path && ` or `cd /path;` preamble — it's
            # boilerplate working-dir noise, not the meaningful command.
            import re as _re
            cmd = _re.sub(r'^(cd\s+\S+\s*(?:&&|;)\s*)+', '', cmd).strip()
            return cmd[:80] + '...' if len(cmd) > 80 else cmd
        if name in ('glob_search', 'grep_search'):
            return str(args.get('pattern', ''))
        if name == 'lattice_solve':
            p = str(args.get('problem', ''))
            return p[:80] + '...' if len(p) > 80 else p
        if name == 'list_dir':
            return str(args.get('path', '.'))
        if name == 'web_fetch':
            return str(args.get('url', ''))
        if name == 'web_search':
            return str(args.get('query', ''))
        return ''

    def _tool_calls_from_message(
        self,
        tool_calls: tuple[dict[str, object], ...],
    ) -> tuple[ToolCall, ...]:
        parsed: list[ToolCall] = []
        for index, raw_tool_call in enumerate(tool_calls):
            function_block = raw_tool_call.get('function')
            if not isinstance(function_block, dict):
                continue
            name = function_block.get('name')
            if not isinstance(name, str) or not name:
                continue
            raw_arguments = function_block.get('arguments', '')
            if isinstance(raw_arguments, str) and raw_arguments.strip():
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise OpenAICompatError(
                        f'Tool arguments must decode to an object, got {type(arguments).__name__}'
                    )
            else:
                arguments = {}
            call_id = raw_tool_call.get('id')
            if not isinstance(call_id, str) or not call_id:
                call_id = f'call_{index}'
            parsed.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(parsed)

    def _should_continue_response(self, turn: AssistantTurn) -> bool:
        return turn.finish_reason in {'length', 'max_tokens'}

    def _build_continuation_prompt(self) -> str:
        return (
            '<system-reminder>\n'
            'Your previous answer was truncated because the model stopped early. '
            'Continue exactly where you left off. Do not repeat completed text.\n'
            '</system-reminder>'
        )

    def _check_budget(
        self,
        usage: UsageStats,
        total_cost_usd: float,
        *,
        tool_calls: int,
        delegated_tasks: int,
        model_calls: int,
        session_turns: int,
    ) -> BudgetDecision:
        budget = self.runtime_config.budget_config
        token_reason = self._check_token_budget(usage, budget)
        if token_reason is not None:
            return BudgetDecision(exceeded=True, reason=token_reason)
        if (
            budget.max_total_cost_usd is not None
            and total_cost_usd > budget.max_total_cost_usd
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the total estimated cost '
                    f'(${total_cost_usd:.6f}) exceeded the configured budget '
                    f'(${budget.max_total_cost_usd:.6f}).'
                ),
            )
        if (
            budget.max_tool_calls is not None
            and tool_calls > budget.max_tool_calls
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the tool-call budget was exceeded '
                    f'({tool_calls} > {budget.max_tool_calls}).'
                ),
            )
        if (
            budget.max_delegated_tasks is not None
            and delegated_tasks > budget.max_delegated_tasks
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the delegated-task budget was exceeded '
                    f'({delegated_tasks} > {budget.max_delegated_tasks}).'
                ),
            )
        if (
            budget.max_model_calls is not None
            and model_calls > budget.max_model_calls
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the model-call budget was exceeded '
                    f'({model_calls} > {budget.max_model_calls}).'
                ),
            )
        if (
            budget.max_session_turns is not None
            and session_turns > budget.max_session_turns
        ):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    'Stopped because the session-turn budget was exceeded '
                    f'({session_turns} > {budget.max_session_turns}).'
                ),
            )
        # 2026-04-27: third recurrence of this regression. The hardcoded
        # _SAFETY_MAX_COST_USD = 10.0 ceiling keeps getting re-added by
        # code refactors and silently killing long latti sessions at $10.14.
        # User reported it twice today. This time: remove the ceiling
        # entirely. The BudgetConfig defaults already provide explicit opt-in
        # caps via --max-budget-usd / --max-model-calls; an implicit hidden
        # wall on top of those is redundant and surprising.
        #
        # Env-var opt-in preserved for callers that want the safety net:
        #   LATTI_SAFETY_MAX_COST_USD=10     # cost cap in USD, 0/unset = no wall
        #   LATTI_SAFETY_MAX_MODEL_CALLS=200 # call cap, 0/unset = no wall
        import os as _os
        try:
            _c_raw = _os.environ.get('LATTI_SAFETY_MAX_COST_USD', '').strip()
            _SAFETY_MAX_COST_USD = float(_c_raw) if _c_raw else 0.0
        except ValueError:
            _SAFETY_MAX_COST_USD = 0.0
        try:
            _m_raw = _os.environ.get('LATTI_SAFETY_MAX_MODEL_CALLS', '').strip()
            _SAFETY_MAX_MODEL_CALLS = int(_m_raw) if _m_raw else 0
        except ValueError:
            _SAFETY_MAX_MODEL_CALLS = 0

        if (budget.max_total_cost_usd is None
                and _SAFETY_MAX_COST_USD > 0
                and total_cost_usd > _SAFETY_MAX_COST_USD):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    f'Stopped: estimated cost (${total_cost_usd:.2f}) hit the '
                    f'safety ceiling (${_SAFETY_MAX_COST_USD:.2f}). '
                    f'Set --max-budget-usd to raise or unset LATTI_SAFETY_MAX_COST_USD.'
                ),
            )
        if (budget.max_model_calls is None
                and _SAFETY_MAX_MODEL_CALLS > 0
                and model_calls > _SAFETY_MAX_MODEL_CALLS):
            return BudgetDecision(
                exceeded=True,
                reason=(
                    f'Stopped: {model_calls} model calls hit the safety ceiling '
                    f'({_SAFETY_MAX_MODEL_CALLS}). '
                    f'Set --max-model-calls or unset LATTI_SAFETY_MAX_MODEL_CALLS.'
                ),
            )
        return BudgetDecision(exceeded=False)

    def _preflight_prompt_length(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> PromptPreflightResult:
        snapshot = calculate_token_budget(
            session=session,
            model=self.model_config.model,
            budget_config=self.runtime_config.budget_config,
            output_schema=self.runtime_config.output_schema,
        )
        if not snapshot.exceeds_soft_limit and not snapshot.exceeds_hard_limit:
            return PromptPreflightResult()

        stream_events.append(
            {
                'type': 'prompt_length_check',
                'turn_index': turn_index,
                'projected_input_tokens': snapshot.projected_input_tokens,
                'soft_input_limit_tokens': snapshot.soft_input_limit_tokens,
                'hard_input_limit_tokens': snapshot.hard_input_limit_tokens,
                'soft_overflow_tokens': snapshot.soft_overflow_tokens,
                'overflow_tokens': snapshot.overflow_tokens,
                'exceeds_hard_limit': snapshot.exceeds_hard_limit,
            }
        )

        target_tokens = snapshot.soft_input_limit_tokens
        if snapshot.exceeds_hard_limit:
            target_tokens = snapshot.hard_input_limit_tokens
        if target_tokens < 0:
            target_tokens = 0

        if self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=target_tokens,
            allow_compaction=True,
        ):
            recovered = calculate_token_budget(
                session=session,
                model=self.model_config.model,
                budget_config=self.runtime_config.budget_config,
                output_schema=self.runtime_config.output_schema,
            )
            stream_events.append(
                {
                    'type': 'prompt_length_recovery',
                    'turn_index': turn_index,
                    'strategy': 'heuristic',
                    'projected_input_tokens': recovered.projected_input_tokens,
                    'soft_input_limit_tokens': recovered.soft_input_limit_tokens,
                    'hard_input_limit_tokens': recovered.hard_input_limit_tokens,
                    'exceeds_hard_limit': recovered.exceeds_hard_limit,
                    'exceeds_soft_limit': recovered.exceeds_soft_limit,
                }
            )
            if not recovered.exceeds_soft_limit and not recovered.exceeds_hard_limit:
                return PromptPreflightResult()
            snapshot = recovered

        # Circuit-breaker: skip auto-compact after MAX_COMPACT_FAILURES consecutive failures
        from .compact import MAX_COMPACT_FAILURES
        if self._compact_consecutive_failures >= MAX_COMPACT_FAILURES:
            stream_events.append(
                {
                    'type': 'auto_compact_circuit_breaker',
                    'turn_index': turn_index,
                    'consecutive_failures': self._compact_consecutive_failures,
                }
            )
        elif self._can_auto_compact_with_summary(session):
            compact_result = compact_conversation(
                self,
                custom_instructions=(
                    'Automatically collapse earlier conversation context to fit the next model '
                    'turn. Preserve the active task, recent file changes, failures, pending work, '
                    'and exact next step.'
                ),
            )
            if compact_result.error is None:
                self._compact_consecutive_failures = 0  # Reset on success
                recovered = calculate_token_budget(
                    session=session,
                    model=self.model_config.model,
                    budget_config=self.runtime_config.budget_config,
                    output_schema=self.runtime_config.output_schema,
                )
                stream_events.append(
                    {
                        'type': 'auto_compact_summary',
                        'turn_index': turn_index,
                        'pre_compact_token_count': compact_result.pre_compact_token_count,
                        'post_compact_token_count': compact_result.post_compact_token_count,
                        'true_post_compact_token_count': compact_result.true_post_compact_token_count,
                        'summary_usage_tokens': compact_result.usage.total_tokens,
                        'ptl_retries': compact_result.ptl_retries,
                        'projected_input_tokens': recovered.projected_input_tokens,
                        'soft_input_limit_tokens': recovered.soft_input_limit_tokens,
                        'hard_input_limit_tokens': recovered.hard_input_limit_tokens,
                        'exceeds_hard_limit': recovered.exceeds_hard_limit,
                        'exceeds_soft_limit': recovered.exceeds_soft_limit,
                    }
                )
                if not recovered.exceeds_soft_limit and not recovered.exceeds_hard_limit:
                    return PromptPreflightResult(
                        usage_increment=compact_result.usage,
                        model_calls_increment=1,
                    )
                snapshot = recovered
                if compact_result.usage.total_tokens:
                    return PromptPreflightResult(
                        usage_increment=compact_result.usage,
                        model_calls_increment=1,
                        stop_reason=(
                            'prompt_too_long'
                            if recovered.exceeds_hard_limit
                            else None
                        ),
                        reason=(
                            self._build_prompt_length_error(recovered)
                            if recovered.exceeds_hard_limit
                            else None
                        ),
                    )
            else:
                self._compact_consecutive_failures += 1
                stream_events.append(
                    {
                        'type': 'auto_compact_failed',
                        'turn_index': turn_index,
                        'reason': compact_result.error,
                        'consecutive_failures': self._compact_consecutive_failures,
                    }
                )

        if snapshot.exceeds_hard_limit:
            return PromptPreflightResult(
                stop_reason='prompt_too_long',
                reason=self._build_prompt_length_error(snapshot),
            )

        stream_events.append(
            {
                'type': 'prompt_length_warning',
                'turn_index': turn_index,
                'projected_input_tokens': snapshot.projected_input_tokens,
                'soft_input_limit_tokens': snapshot.soft_input_limit_tokens,
                'hard_input_limit_tokens': snapshot.hard_input_limit_tokens,
                'soft_overflow_tokens': snapshot.soft_overflow_tokens,
            }
        )
        return PromptPreflightResult()

    def _can_auto_compact_with_summary(self, session: AgentSessionState) -> bool:
        prefix_count = self._compact_prefix_count(session)
        preserve_count = max(self.runtime_config.compact_preserve_messages, 1)
        return len(session.messages) - prefix_count > preserve_count

    def _build_prompt_length_error(self, snapshot) -> str:
        return (
            'Stopped before the next model call because the prompt would exceed the '
            'effective input budget. '
            f'Projected prompt tokens: {snapshot.projected_input_tokens:,}; '
            f'hard input limit: {snapshot.hard_input_limit_tokens:,}; '
            f'soft input limit: {snapshot.soft_input_limit_tokens:,}.'
        )

    def _microcompact_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        """Run time-based microcompaction to clear old tool results.

        Fires when the gap since the last assistant message exceeds the
        threshold (default 60 minutes), indicating the server-side cache
        has expired and the full prefix will be rewritten anyway.
        """
        if not session.messages:
            return
        result = _microcompact_messages(
            session.messages,
            model=self.model_config.model,
        )
        if not result.triggered:
            return
        session.messages = result.messages
        stream_events.append(
            {
                'type': 'microcompact',
                'turn_index': turn_index,
                'cleared_tool_count': result.cleared_tool_count,
                'kept_tool_count': result.kept_tool_count,
                'estimated_tokens_saved': result.estimated_tokens_saved,
                'gap_minutes': round(result.gap_minutes, 1),
            }
        )

    def _snip_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        threshold = self.runtime_config.auto_snip_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=False,
        )

    def _compact_session_if_needed(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> None:
        threshold = self.runtime_config.auto_compact_threshold_tokens
        if threshold is None or threshold <= 0:
            return
        self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=threshold,
            allow_compaction=True,
        )

    def _reactive_compact_session(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
    ) -> bool:
        return self._reduce_context_pressure(
            session,
            stream_events,
            turn_index=turn_index,
            target_tokens=0,
            allow_compaction=True,
            reactive=True,
        )

    def _reduce_context_pressure(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        allow_compaction: bool,
        reactive: bool = False,
    ) -> bool:
        changed = False
        for _ in range(6):
            usage_report = collect_context_usage(
                session=session,
                model=self.model_config.model,
                strategy='reactive_compact' if reactive else 'context_pressure',
            )
            if usage_report.total_tokens <= target_tokens:
                break
            if self._snip_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                target_tokens=target_tokens,
                current_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                continue
            if allow_compaction and self._compact_session_pass(
                session,
                stream_events,
                turn_index=turn_index,
                usage_total=usage_report.total_tokens,
                reactive=reactive,
            ):
                changed = True
                if reactive:
                    continue
                break
            break
        return changed

    def _snip_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        target_tokens: int,
        current_total: int,
        reactive: bool,
    ) -> bool:
        prefix_count = self._compact_prefix_count(session)
        tail_count = min(
            max(self.runtime_config.compact_preserve_messages, 0),
            max(len(session.messages) - prefix_count, 0),
        )
        candidate_indexes = [
            index
            for index in range(prefix_count, max(len(session.messages) - tail_count, prefix_count))
            if self._message_can_be_snipped(session.messages[index])
        ]
        if not candidate_indexes:
            return False
        snipped_count = 0
        tokens_removed = 0
        snipped_message_ids: list[str] = []
        for index in candidate_indexes:
            if current_total <= target_tokens and not reactive:
                break
            message = session.messages[index]
            original_tokens = estimate_tokens(message.content, self.model_config.model)
            replacement = self._build_snipped_message_content(message)
            replacement_tokens = estimate_tokens(replacement, self.model_config.model)
            if replacement_tokens >= original_tokens:
                continue
            session.tombstone_message(
                index,
                summary=replacement,
                stop_reason='snipped_for_context',
                mutation_kind='snip_tombstone',
                metadata={
                    'kind': 'snipped_message',
                    'original_token_estimate': original_tokens,
                    'replacement_token_estimate': replacement_tokens,
                    'snipped_turn_index': turn_index,
                    'snipped_from_role': message.role,
                    'snipped_from_message_id': message.message_id,
                    'snipped_from_kind': message.metadata.get('kind'),
                    'snipped_from_lineage_id': message.metadata.get('lineage_id'),
                    'snipped_from_revision': message.metadata.get('revision'),
                },
            )
            delta = original_tokens - replacement_tokens
            current_total -= delta
            tokens_removed += delta
            snipped_count += 1
            if session.messages[index].message_id:
                snipped_message_ids.append(session.messages[index].message_id)
            if reactive and snipped_count >= 3:
                break
        if not snipped_count:
            return False
        stream_events.append(
            {
                'type': 'reactive_snip_boundary' if reactive else 'snip_boundary',
                'turn_index': turn_index,
                'snipped_message_count': snipped_count,
                'estimated_tokens_removed': tokens_removed,
                'snipped_message_ids': snipped_message_ids,
            }
        )
        return True

    def _compact_session_pass(
        self,
        session: AgentSessionState,
        stream_events: list[dict[str, object]],
        *,
        turn_index: int,
        usage_total: int,
        reactive: bool,
    ) -> bool:
        prefix_count = self._compact_prefix_count(session)
        preserve_messages = max(self.runtime_config.compact_preserve_messages, 0)
        if reactive:
            preserve_messages = max(preserve_messages // 2, 1)
        tail_count = min(
            preserve_messages,
            max(len(session.messages) - prefix_count, 0),
        )
        compact_end = len(session.messages) - tail_count
        if compact_end <= prefix_count:
            return False
        candidates = session.messages[prefix_count:compact_end]
        preserved_tail = list(session.messages[compact_end:])
        if not candidates:
            return False
        compacted_tokens = sum(
            usage.tokens
            for usage in (
                collect_context_usage(
                    session=AgentSessionState(
                        system_prompt_parts=session.system_prompt_parts,
                        user_context=session.user_context,
                        system_context=session.system_context,
                        messages=list(candidates),
                    ),
                    model=self.model_config.model,
                    strategy='compacted_segment',
                ).categories
            )
            if usage.name != 'Free space'
        )
        compact_message = self._build_compact_boundary_message(
            candidates,
            turn_index=turn_index,
            estimated_tokens_before=usage_total,
            estimated_tokens_removed=compacted_tokens,
            preserved_tail_count=tail_count,
            preserved_tail=preserved_tail,
        )
        session.messages = (
            session.messages[:prefix_count]
            + [compact_message]
            + session.messages[compact_end:]
        )
        stream_events.append(
            {
                'type': 'reactive_compact_boundary' if reactive else 'compact_boundary',
                'turn_index': turn_index,
                'compacted_message_count': len(candidates),
                'estimated_tokens_before': usage_total,
                'estimated_tokens_removed': compacted_tokens,
                'preserved_tail_count': tail_count,
                'preserved_tail_ids': [
                    message.message_id for message in preserved_tail if message.message_id
                ],
                'compaction_depth': compact_message.metadata.get('compaction_depth'),
                'nested_compaction_count': compact_message.metadata.get('nested_compaction_count'),
                'compacted_message_ids': [
                    message.message_id for message in candidates if message.message_id
                ],
            }
        )
        return True

    def _check_token_budget(
        self,
        usage: UsageStats,
        budget: BudgetConfig,
    ) -> str | None:
        if budget.max_total_tokens is not None and usage.total_tokens > budget.max_total_tokens:
            return (
                'Stopped because the total token budget was exceeded '
                f'({usage.total_tokens} > {budget.max_total_tokens}).'
            )
        if budget.max_input_tokens is not None and usage.input_tokens > budget.max_input_tokens:
            return (
                'Stopped because the input token budget was exceeded '
                f'({usage.input_tokens} > {budget.max_input_tokens}).'
            )
        if budget.max_output_tokens is not None and usage.output_tokens > budget.max_output_tokens:
            return (
                'Stopped because the output token budget was exceeded '
                f'({usage.output_tokens} > {budget.max_output_tokens}).'
            )
        if (
            budget.max_reasoning_tokens is not None
            and usage.reasoning_tokens > budget.max_reasoning_tokens
        ):
            return (
                'Stopped because the reasoning token budget was exceeded '
                f'({usage.reasoning_tokens} > {budget.max_reasoning_tokens}).'
            )
        return None

    def _build_file_history_entry(
        self,
        *,
        tool_call: ToolCall,
        tool_result,
        turn_index: int,
    ) -> dict[str, object] | None:
        if not tool_result.metadata:
            return None
        if (
            'path' not in tool_result.metadata
            and 'command' not in tool_result.metadata
            and tool_result.metadata.get('action') not in ('delegate_agent', 'Agent')
        ):
            return None
        metadata = dict(tool_result.metadata)
        entry: dict[str, object] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'turn_index': turn_index,
            'tool_call_id': tool_call.id,
            'tool_name': tool_call.name,
            'ok': tool_result.ok,
            'history_entry_id': f'{turn_index}:{tool_call.id}:{tool_call.name}',
            'result_preview': self._preview_text(tool_result.content, 220),
            **metadata,
        }
        action = metadata.get('action')
        path = metadata.get('path')
        if isinstance(path, str) and path:
            entry['history_kind'] = 'file_change'
            entry['changed_paths'] = [path]
            before_sha256 = metadata.get('before_sha256')
            if isinstance(before_sha256, str) and before_sha256:
                entry['before_snapshot_id'] = f'{path}:{before_sha256[:12]}'
            after_sha256 = metadata.get('after_sha256')
            if isinstance(after_sha256, str) and after_sha256:
                entry['after_snapshot_id'] = f'{path}:{after_sha256[:12]}'
        elif isinstance(metadata.get('command'), str):
            entry['history_kind'] = 'shell'
        elif action in ('delegate_agent', 'Agent'):
            entry['history_kind'] = 'delegation'
            delegate_batches = metadata.get('delegate_batches')
            if isinstance(delegate_batches, list):
                entry['delegate_batch_count'] = len(delegate_batches)
            dependency_skips = metadata.get('dependency_skips')
            if isinstance(dependency_skips, int) and not isinstance(dependency_skips, bool):
                entry['dependency_skips'] = dependency_skips
        else:
            entry['history_kind'] = 'tool'
        return entry

    def _compact_prefix_count(self, session: AgentSessionState) -> int:
        prefix_count = 0
        for message in session.messages:
            if prefix_count == 0 and message.role == 'system':
                prefix_count += 1
                continue
            if (
                prefix_count == 1
                and message.role == 'user'
                and message.content.startswith('<system-reminder>')
            ):
                prefix_count += 1
                continue
            break
        return prefix_count

    def _message_can_be_snipped(self, message) -> bool:
        if message.metadata.get('kind') in {
            'compact_boundary',
            'snipped_message',
            'file_history_replay',
        }:
            return False
        if message.role == 'tool':
            return True
        if message.role == 'assistant' and (message.tool_calls or len(message.content) > 600):
            return True
        if (
            message.role == 'user'
            and message.metadata.get('kind') in {'continuation_request', 'file_history_replay'}
        ):
            return True
        return False

    def _build_snipped_message_content(self, message) -> str:
        preview = ' '.join(message.content.split())
        if len(preview) > 120:
            preview = preview[:117] + '...'
        if message.role == 'tool':
            label = f'tool result ({message.name or "tool"})'
        elif message.role == 'assistant':
            label = 'assistant message with tool calls'
        else:
            label = message.role
        return (
            '<system-reminder>\n'
            f'Older {label} was snipped to save context.\n'
            f'Message id: {message.message_id or "(none)"}\n'
            f'Preview: {preview or "(empty)"}\n'
            '</system-reminder>'
        )

    def _build_compact_boundary_message(
        self,
        messages,
        *,
        turn_index: int,
        estimated_tokens_before: int,
        estimated_tokens_removed: int,
        preserved_tail_count: int,
        preserved_tail,
    ):
        summary_lines = [
            '<system-reminder>',
            'Earlier conversation history was compacted to keep the session within the context budget.',
            '',
            'Compacted summary:',
        ]
        remaining = 24
        for message in messages:
            if remaining <= 0:
                break
            label = message.role
            if message.role == 'tool' and message.name:
                label = f'tool:{message.name}'
            snippet = ' '.join(message.content.split())
            if len(snippet) > 160:
                snippet = snippet[:157] + '...'
            if not snippet:
                snippet = '(empty)'
            summary_lines.append(f'- {label}: {snippet}')
            remaining -= 1
        if len(messages) > 24:
            summary_lines.append(f'- ... plus {len(messages) - 24} older messages')
        summary_lines.extend(
            [
                '',
                'Keep using the preserved recent tail as the active working set.',
                '</system-reminder>',
            ]
        )
        from .agent_session import AgentMessage

        nested_compaction_count = sum(
            1 for message in messages if message.metadata.get('kind') == 'compact_boundary'
        )
        prior_depths = [
            int(message.metadata.get('compaction_depth', 0))
            for message in messages
            if isinstance(message.metadata.get('compaction_depth', 0), int)
        ]
        compaction_depth = (max(prior_depths) if prior_depths else 0) + 1
        compacted_kinds: dict[str, int] = {}
        source_mutation_totals: dict[str, int] = {}
        compacted_lineage_ids: list[str] = []
        preserved_tail_lineage_ids = [
            lineage_id
            for lineage_id in (
                message.metadata.get('lineage_id') for message in preserved_tail
            )
            if isinstance(lineage_id, str) and lineage_id
        ]
        max_source_revision = 0
        max_source_mutation_serial = 0
        compacted_revision_total = 0
        for message in messages:
            kind = message.metadata.get('kind')
            label = str(kind) if isinstance(kind, str) and kind else message.role
            compacted_kinds[label] = compacted_kinds.get(label, 0) + 1
            lineage_id = message.metadata.get('lineage_id')
            if isinstance(lineage_id, str) and lineage_id:
                compacted_lineage_ids.append(lineage_id)
            revision = message.metadata.get('revision')
            if isinstance(revision, int) and not isinstance(revision, bool):
                max_source_revision = max(max_source_revision, revision)
                compacted_revision_total += revision
            max_mutation_serial = message.metadata.get('max_mutation_serial')
            if isinstance(max_mutation_serial, int) and not isinstance(max_mutation_serial, bool):
                max_source_mutation_serial = max(
                    max_source_mutation_serial,
                    max_mutation_serial,
                )
            mutation_totals = message.metadata.get('mutation_totals')
            if isinstance(mutation_totals, dict):
                for mutation_kind, count in mutation_totals.items():
                    if (
                        not isinstance(mutation_kind, str)
                        or not mutation_kind
                        or isinstance(count, bool)
                        or not isinstance(count, int)
                        or count <= 0
                    ):
                        continue
                    source_mutation_totals[mutation_kind] = (
                        source_mutation_totals.get(mutation_kind, 0) + count
                    )

        compact_boundary_id = f'compact_boundary_{turn_index}_{len(messages)}'

        return AgentMessage(
            role='system',
            content='\n'.join(summary_lines),
            message_id=compact_boundary_id,
            metadata={
                'kind': 'compact_boundary',
                'lineage_id': compact_boundary_id,
                'revision': 0,
                'revision_count': 1,
                'message_role': 'system',
                'turn_index': turn_index,
                'compacted_message_count': len(messages),
                'estimated_tokens_before': estimated_tokens_before,
                'estimated_tokens_removed': estimated_tokens_removed,
                'preserved_tail_count': preserved_tail_count,
                'preserved_tail_ids': [
                    message.message_id for message in preserved_tail if message.message_id
                ],
                'compaction_depth': compaction_depth,
                'nested_compaction_count': nested_compaction_count,
                'compacted_kinds': compacted_kinds,
                'compacted_lineage_ids': compacted_lineage_ids,
                'preserved_tail_lineage_ids': preserved_tail_lineage_ids,
                'max_source_revision': max_source_revision,
                'max_source_mutation_serial': max_source_mutation_serial,
                'source_mutation_totals': source_mutation_totals,
                'compacted_revision_total': compacted_revision_total,
                'compacted_message_ids': [
                    message.message_id for message in messages if message.message_id
                ],
            },
        )

    def _is_prompt_too_long_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        patterns = (
            'prompt is too long',
            'maximum context length',
            'context length exceeded',
            'too many tokens',
            'input too long',
            'context window',
        )
        return any(pattern in text for pattern in patterns)

    def _execute_skill(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a skill through the Skill tool.

        Checks bundled skills first, then falls back to slash commands.
        """
        from .agent_slash_commands import find_slash_command, get_slash_command_specs
        from .bundled_skills import find_bundled_skill, get_bundled_skills

        skill_name = arguments.get('skill')
        if not isinstance(skill_name, str) or not skill_name.strip():
            return ToolExecutionResult(
                name='Skill',
                ok=False,
                content='skill must be a non-empty string',
            )

        # Normalize: strip leading '/' if present
        skill_name = skill_name.strip().lstrip('/')
        args = arguments.get('args', '')
        if not isinstance(args, str):
            args = str(args) if args is not None else ''

        # 1. Check bundled skills first
        bundled = find_bundled_skill(skill_name)
        if bundled is not None:
            prompt = bundled.get_prompt(self, args.strip())
            return ToolExecutionResult(
                name='Skill',
                ok=True,
                content=prompt,
                metadata={
                    'action': 'skill',
                    'skill_name': skill_name,
                    'source': 'bundled',
                    'should_query': True,
                },
            )

        # 2. Fall back to slash commands
        spec = find_slash_command(skill_name)
        if spec is None:
            available_cmds = sorted(
                name
                for s in get_slash_command_specs()
                for name in s.names
            )
            available_skills = [sk.name for sk in get_bundled_skills()]
            all_available = sorted(set(available_cmds + available_skills))
            return ToolExecutionResult(
                name='Skill',
                ok=False,
                content=(
                    f'Unknown skill: {skill_name}. '
                    f'Available skills: {", ".join(all_available[:30])}'
                ),
                metadata={'action': 'skill_not_found', 'skill_name': skill_name},
            )

        # Invoke the slash command handler
        input_text = f'/{skill_name} {args}'.strip()
        result = spec.handler(self, args.strip(), input_text)

        if result.output:
            content = result.output
        elif result.prompt:
            content = result.prompt
        else:
            content = f'Skill /{skill_name} completed.'

        return ToolExecutionResult(
            name='Skill',
            ok=True,
            content=content,
            metadata={
                'action': 'skill',
                'skill_name': skill_name,
                'command_name': spec.names[0],
                'handled': result.handled,
                'should_query': result.should_query,
            },
        )

    def _resolve_agent_definition(
        self,
        arguments: dict[str, object],
    ) -> AgentDefinition:
        """Resolve the agent definition from subagent_type or default to general-purpose."""
        subagent_type = arguments.get('subagent_type')
        if isinstance(subagent_type, str) and subagent_type:
            agent_def = find_agent_definition(self.runtime_config.cwd, subagent_type)
            if agent_def is not None:
                return agent_def
        return GENERAL_PURPOSE_AGENT

    def _resolve_child_model_config(
        self,
        arguments: dict[str, object],
        agent_def: AgentDefinition,
    ) -> ModelConfig:
        """Resolve model config for a child agent based on explicit override or agent definition."""
        model_override = arguments.get('model')
        agent_model = agent_def.model

        # Explicit model param in arguments takes priority
        if isinstance(model_override, str) and model_override.strip():
            return replace(self.model_config, model=model_override.strip())

        # Agent definition model
        if agent_model and agent_model != 'inherit':
            return replace(self.model_config, model=agent_model)

        return self.model_config

    def _filter_tools_for_agent(
        self,
        agent_def: AgentDefinition,
    ) -> dict[str, AgentTool]:
        """Build the tool registry for a child agent based on its definition."""
        # Start from parent tools, remove Agent/delegate_agent to prevent recursive spawning
        base_tools = {
            name: tool
            for name, tool in self.tool_registry.items()
            if name not in ('delegate_agent', 'Agent')
        }

        # Apply agent-specific tool allow-list
        if agent_def.tools is not None:
            allowed = set(agent_def.tools)
            base_tools = {
                name: tool
                for name, tool in base_tools.items()
                if name in allowed
            }

        # Apply agent-specific disallowed tools
        if agent_def.disallowed_tools:
            denied = set(agent_def.disallowed_tools)
            base_tools = {
                name: tool
                for name, tool in base_tools.items()
                if name not in denied
            }

        # Apply universal agent disallowed tools
        base_tools = {
            name: tool
            for name, tool in base_tools.items()
            if name not in ALL_AGENT_DISALLOWED_TOOLS
        }

        return base_tools

    def _execute_delegate_agent(
        self,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        tool_name = 'Agent'
        agent_def = self._resolve_agent_definition(arguments)
        max_turns = arguments.get('max_turns')
        if max_turns is not None and (isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1):
            return ToolExecutionResult(
                name=tool_name,
                ok=False,
                content='max_turns must be an integer >= 1',
            )
        subtasks = self._normalize_delegate_subtasks(arguments)
        if not subtasks:
            return ToolExecutionResult(
                name=tool_name,
                ok=False,
                content='prompt must be a non-empty string or subtasks must contain at least one prompt',
            )
        # Resolve child permissions — read-only agents (whose definition
        # disallows write tools) get no write/shell. Otherwise inherit from
        # parent unless caller explicitly restricts. allow_write / allow_shell
        # default to inherit — caller can pass False to restrict, but we don't
        # silently cripple children. allow_destructive inherits from parent.
        if agent_def.disallowed_tools and (
            'edit_file' in agent_def.disallowed_tools
            or 'write_file' in agent_def.disallowed_tools
        ):
            child_permissions = AgentPermissions(
                allow_file_write=False,
                allow_shell_commands=self.runtime_config.permissions.allow_shell_commands,
                allow_destructive_shell_commands=False,
            )
        else:
            _allow_write = arguments.get('allow_write')
            _allow_shell = arguments.get('allow_shell')
            child_permissions = AgentPermissions(
                allow_file_write=(
                    self.runtime_config.permissions.allow_file_write
                    if _allow_write is None
                    else (self.runtime_config.permissions.allow_file_write and bool(_allow_write))
                ),
                allow_shell_commands=(
                    self.runtime_config.permissions.allow_shell_commands
                    if _allow_shell is None
                    else (self.runtime_config.permissions.allow_shell_commands and bool(_allow_shell))
                ),
                allow_destructive_shell_commands=(
                    self.runtime_config.permissions.allow_destructive_shell_commands
                ),
            )
        # max_turns: use caller-supplied value if given, otherwise inherit
        # from parent without any hardcoded cap. A cap of 6 was silently
        # killing long autonomous subtasks.
        child_runtime_config = replace(
            self.runtime_config,
            max_turns=max_turns if max_turns is not None else self.runtime_config.max_turns,
            permissions=child_permissions,
            auto_compact_threshold_tokens=self.runtime_config.auto_compact_threshold_tokens,
        )

        child_model_config = self._resolve_child_model_config(arguments, agent_def)
        child_tools = self._filter_tools_for_agent(agent_def)
        include_parent_context = bool(arguments.get('include_parent_context', True))
        continue_on_error = bool(arguments.get('continue_on_error', True))
        max_failures = arguments.get('max_failures')
        if isinstance(max_failures, bool) or (max_failures is not None and not isinstance(max_failures, int)):
            max_failures = None
        if isinstance(max_failures, int) and max_failures < 0:
            max_failures = None
        strategy = self._normalize_delegate_strategy(arguments.get('strategy'))
        child_summaries: list[dict[str, object]] = []
        child_session_ids: list[str] = []
        prior_results: list[dict[str, str]] = []
        completed_labels: set[str] = set()
        failed_labels: set[str] = set()
        delegate_preflight_messages = (
            self.plugin_runtime.before_delegate_injections()
            if self.plugin_runtime is not None
            else ()
        )
        delegate_after_messages: tuple[str, ...] = ()
        group_id: str | None = None
        if self.agent_manager is not None and len(subtasks) > 1:
            group_id = self.agent_manager.start_group(
                label=str(arguments.get('label') or 'delegated_group'),
                parent_agent_id=self.managed_agent_id,
                strategy=strategy,
            )
        planned_batches = self._plan_delegate_batches(subtasks, strategy)
        batch_summaries: list[dict[str, object]] = []
        failed_children = 0
        dependency_skips = 0
        child_result = None
        stop_processing = False
        for batch_index, batch in enumerate(planned_batches, start=1):
            if stop_processing:
                break
            batch_completed = 0
            batch_failed = 0
            batch_skipped = 0
            batch_labels: list[str] = []
            for subtask in batch:
                index = int(subtask.get('_delegate_index', len(child_summaries) + 1))
                subtask_label = str(subtask.get('label') or f'subtask_{index}')
                batch_labels.append(subtask_label)
                dependencies = tuple(
                    item
                    for item in subtask.get('depends_on', ())
                    if isinstance(item, str) and item
                )
                unmet_dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency not in completed_labels
                ]
                blocked_dependencies = [
                    dependency
                    for dependency in dependencies
                    if dependency in failed_labels
                ]
                if unmet_dependencies:
                    skip_reason = (
                        'skipped_dependency'
                        if blocked_dependencies
                        else 'pending_dependency'
                    )
                    child_result = AgentRunResult(
                        final_output=(
                            'Skipped delegated subtask because dependencies were not satisfied: '
                            + ', '.join(unmet_dependencies)
                        ),
                        turns=0,
                        tool_calls=0,
                        transcript=(),
                        stop_reason=skip_reason,
                    )
                    summary = {
                        'index': index,
                        'label': subtask_label,
                        'session_id': '',
                        'turns': child_result.turns,
                        'tool_calls': child_result.tool_calls,
                        'stop_reason': skip_reason,
                        'output_preview': self._preview_text(child_result.final_output, 220),
                        'resume_used': False,
                        'resumed_from_session_id': '',
                        'depends_on': list(dependencies),
                        'batch_index': batch_index,
                    }
                    child_summaries.append(summary)
                    failed_children += 1
                    batch_failed += 1
                    batch_skipped += 1
                    dependency_skips += 1
                    failed_labels.add(subtask_label)
                    if isinstance(max_failures, int) and failed_children > max_failures:
                        stop_processing = True
                        break
                    if not continue_on_error:
                        stop_processing = True
                        break
                    continue
                # Use agent definition's system prompt if available
                child_system_prompt = agent_def.system_prompt or self.custom_system_prompt
                child_override_prompt = None
                if agent_def.system_prompt:
                    child_override_prompt = agent_def.system_prompt
                else:
                    child_override_prompt = self.override_system_prompt

                # Inject critical system reminder if agent definition has one
                child_append_prompt = self.append_system_prompt
                if agent_def.critical_system_reminder:
                    reminder = f'\n\n<system-reminder>\n{agent_def.critical_system_reminder}\n</system-reminder>'
                    child_append_prompt = (
                        (child_append_prompt or '') + reminder
                    )

                child_agent = LocalCodingAgent(
                    model_config=child_model_config,
                    runtime_config=replace(
                        child_runtime_config,
                        max_turns=subtask.get('max_turns', child_runtime_config.max_turns),
                        disable_claude_md_discovery=agent_def.omit_claude_md,
                    ),
                    custom_system_prompt=child_system_prompt if not child_override_prompt else None,
                    append_system_prompt=child_append_prompt,
                    override_system_prompt=child_override_prompt,
                    tool_registry=child_tools,
                    agent_manager=self.agent_manager,
                    parent_agent_id=self.managed_agent_id,
                    managed_group_id=group_id,
                    managed_child_index=index,
                    managed_label=subtask_label,
                )
                if group_id is not None and child_agent.managed_agent_id is not None:
                    self.agent_manager.register_group_child(
                        group_id,
                        child_agent.managed_agent_id,
                        child_index=index,
                    )
                resume_session_id = subtask.get('resume_session_id')
                child_prompt = str(subtask['prompt'])
                if agent_def.initial_prompt and not (
                    isinstance(resume_session_id, str) and resume_session_id
                ):
                    child_prompt = f'{agent_def.initial_prompt.strip()}\n\n{child_prompt}'.strip()
                if delegate_preflight_messages:
                    child_prompt = self._prepend_plugin_delegate_context(
                        child_prompt,
                        delegate_preflight_messages,
                    )
                if include_parent_context and prior_results:
                    child_prompt = self._prepend_delegate_context(child_prompt, prior_results)
                resume_used = False
                if isinstance(resume_session_id, str) and resume_session_id:
                    try:
                        stored_child_session = load_agent_session(
                            resume_session_id,
                            directory=child_runtime_config.session_directory,
                        )
                    except OSError:
                        child_result = AgentRunResult(
                            final_output=f'Unable to load delegated session {resume_session_id}.',
                            turns=0,
                            tool_calls=0,
                            transcript=(),
                            stop_reason='resume_load_error',
                            session_id=resume_session_id,
                        )
                        failed_children += 1
                        batch_failed += 1
                        summary = {
                            'index': index,
                            'label': subtask_label,
                            'session_id': resume_session_id,
                            'turns': child_result.turns,
                            'tool_calls': child_result.tool_calls,
                            'stop_reason': child_result.stop_reason or 'resume_load_error',
                            'output_preview': self._preview_text(child_result.final_output, 220),
                            'resume_used': True,
                            'resumed_from_session_id': resume_session_id,
                            'depends_on': list(dependencies),
                            'batch_index': batch_index,
                        }
                        child_summaries.append(summary)
                        prior_results.append(
                            {
                                'label': summary['label'],
                                'output_preview': str(summary['output_preview']),
                            }
                        )
                        failed_labels.add(subtask_label)
                        if isinstance(max_failures, int) and failed_children > max_failures:
                            stop_processing = True
                            break
                        if not continue_on_error:
                            stop_processing = True
                            break
                        continue
                    child_result = child_agent.resume(child_prompt, stored_child_session)
                    resume_used = True
                else:
                    child_result = child_agent.run(child_prompt)
                if group_id is not None and child_agent.managed_agent_id is not None:
                    self.agent_manager.register_group_child(
                        group_id,
                        child_agent.managed_agent_id,
                        child_index=index,
                    )
                summary = {
                    'index': index,
                    'label': subtask_label,
                    'session_id': child_result.session_id or '',
                    'turns': child_result.turns,
                    'tool_calls': child_result.tool_calls,
                    'stop_reason': child_result.stop_reason or 'stop',
                    'output_preview': self._preview_text(child_result.final_output, 220),
                    'resume_used': resume_used,
                    'resumed_from_session_id': (
                        str(resume_session_id)
                        if isinstance(resume_session_id, str) and resume_session_id
                        else ''
                    ),
                    'depends_on': list(dependencies),
                    'batch_index': batch_index,
                }
                child_summaries.append(summary)
                if child_result.session_id:
                    child_session_ids.append(child_result.session_id)
                prior_results.append(
                    {
                        'label': summary['label'],
                        'output_preview': str(summary['output_preview']),
                    }
                )
                if child_result.stop_reason in {'backend_error', 'budget_exceeded'}:
                    failed_children += 1
                    batch_failed += 1
                    failed_labels.add(subtask_label)
                    if isinstance(max_failures, int) and failed_children > max_failures:
                        stop_processing = True
                        break
                    if not continue_on_error:
                        stop_processing = True
                        break
                else:
                    batch_completed += 1
                    completed_labels.add(subtask_label)
            batch_status = 'completed'
            if batch_failed and batch_completed:
                batch_status = 'partial'
            elif batch_failed:
                batch_status = 'failed'
            batch_summaries.append(
                {
                    'batch_index': batch_index,
                    'labels': batch_labels,
                    'completed_children': batch_completed,
                    'failed_children': batch_failed,
                    'skipped_children': batch_skipped,
                    'status': batch_status,
                }
            )
        assert child_result is not None
        completed_children = len(child_summaries) - failed_children
        resumed_children = sum(
            1 for summary in child_summaries if summary.get('resume_used')
        )
        group_status = 'completed'
        if failed_children and completed_children:
            group_status = 'partial'
        elif failed_children:
            group_status = 'failed'
        delegate_after_messages = (
            self.plugin_runtime.after_delegate_injections()
            if self.plugin_runtime is not None
            else ()
        )
        if group_id is not None and self.agent_manager is not None:
            self.agent_manager.finish_group(
                group_id,
                status=group_status,
                completed_children=completed_children,
                failed_children=failed_children,
                batch_count=len(batch_summaries),
                max_batch_size=max((len(batch['labels']) for batch in batch_summaries), default=0),
                dependency_skips=dependency_skips,
            )
        summary_lines = [
            (
                'Delegated agent completed the subtask.'
                if len(child_summaries) == 1
                else f'Delegated agent completed {len(child_summaries)} sequential subtasks.'
            ),
        ]
        if group_id is not None:
            summary_lines.append(f'group_id={group_id}')
            summary_lines.append(f'group_status={group_status}')
            summary_lines.append(f'resumed_children={resumed_children}')
            summary_lines.append(f'strategy={strategy}')
            summary_lines.append(f'batch_count={len(batch_summaries)}')
            summary_lines.append(f'dependency_skips={dependency_skips}')
            summary_lines.append('')
        if delegate_preflight_messages:
            summary_lines.append('Plugin delegate preflight:')
            summary_lines.extend(f'- {message}' for message in delegate_preflight_messages)
            summary_lines.append('')
        for batch in batch_summaries:
            summary_lines.append(
                f"[batch {batch['batch_index']}] status={batch['status']} "
                f"labels={','.join(batch['labels']) or '(none)'} "
                f"completed={batch['completed_children']} failed={batch['failed_children']} "
                f"skipped={batch['skipped_children']}"
            )
        if batch_summaries:
            summary_lines.append('')
        for summary in child_summaries:
            summary_lines.extend(
                [
                    f"[{summary['label']}]",
                    f"batch_index={summary['batch_index']}",
                    f"session_id={summary['session_id']}",
                    f"turns={summary['turns']}",
                    f"tool_calls={summary['tool_calls']}",
                    f"stop_reason={summary['stop_reason']}",
                    f"resume_used={summary['resume_used']}",
                    f"resumed_from_session_id={summary['resumed_from_session_id']}",
                    f"depends_on={','.join(summary.get('depends_on', [])) or '(none)'}",
                    f"output_preview={summary['output_preview']}",
                    '',
                ]
            )
        if delegate_after_messages:
            summary_lines.append('Plugin delegate completion:')
            summary_lines.extend(f'- {message}' for message in delegate_after_messages)
            summary_lines.append('')
        summary_lines.append('Final delegated output:')
        summary_lines.append(child_result.final_output)
        return ToolExecutionResult(
            name=tool_name,
            ok=True,
            content='\n'.join(summary_lines).strip(),
            metadata={
                # Canonical name regardless of the alias the model invoked
                # the tool with ('Agent' or 'delegate_agent'). Downstream
                # consumers (file_history, observability) expect the stable
                # historical identifier. Tests pin this contract.
                'action': 'delegate_agent',
                'subagent_type': agent_def.agent_type,
                'child_session_id': child_result.session_id,
                'child_session_ids': child_session_ids,
                'child_turns': child_result.turns,
                'child_tool_calls': child_result.tool_calls,
                'child_stop_reason': child_result.stop_reason,
                'child_results': child_summaries,
                'subtask_count': len(child_summaries),
                'group_id': group_id,
                'group_status': group_status,
                'failed_children': failed_children,
                'completed_children': completed_children,
                'resumed_children': resumed_children,
                'strategy': strategy,
                'max_failures': max_failures,
                'delegate_batches': batch_summaries,
                'dependency_skips': dependency_skips,
                'plugin_delegate_preflight_messages': list(delegate_preflight_messages),
                'plugin_delegate_after_messages': list(delegate_after_messages),
            },
        )

    def _normalize_delegate_subtasks(
        self,
        arguments: dict[str, object],
    ) -> list[dict[str, object]]:
        subtasks: list[dict[str, object]] = []
        raw_subtasks = arguments.get('subtasks')
        if isinstance(raw_subtasks, list):
            for index, item in enumerate(raw_subtasks, start=1):
                if isinstance(item, str) and item.strip():
                    subtasks.append(
                        {
                            'prompt': item.strip(),
                            'label': f'subtask_{index}',
                            '_delegate_index': index,
                        }
                    )
                    continue
                if isinstance(item, dict):
                    prompt = item.get('prompt')
                    if not isinstance(prompt, str) or not prompt.strip():
                        continue
                    label = item.get('label')
                    max_turns = item.get('max_turns')
                    task: dict[str, object] = {
                        'prompt': prompt.strip(),
                        'label': label if isinstance(label, str) and label.strip() else f'subtask_{index}',
                    }
                    resume_session_id = item.get('resume_session_id')
                    if resume_session_id is None:
                        resume_session_id = item.get('session_id')
                    if isinstance(resume_session_id, str) and resume_session_id.strip():
                        task['resume_session_id'] = resume_session_id.strip()
                    depends_on = item.get('depends_on')
                    if isinstance(depends_on, list):
                        task['depends_on'] = tuple(
                            dependency.strip()
                            for dependency in depends_on
                            if isinstance(dependency, str) and dependency.strip()
                        )
                    if isinstance(max_turns, int) and not isinstance(max_turns, bool) and max_turns > 0:
                        task['max_turns'] = max_turns
                    task['_delegate_index'] = index
                    subtasks.append(task)
        prompt = arguments.get('prompt')
        if isinstance(prompt, str) and prompt.strip():
            if not subtasks:
                task: dict[str, object] = {'prompt': prompt.strip(), 'label': 'subtask_1'}
                resume_session_id = arguments.get('resume_session_id')
                if resume_session_id is None:
                    resume_session_id = arguments.get('session_id')
                if isinstance(resume_session_id, str) and resume_session_id.strip():
                    task['resume_session_id'] = resume_session_id.strip()
                task['_delegate_index'] = 1
                subtasks.append(task)
        return [
            {
                **task,
                '_delegate_index': int(task.get('_delegate_index', index)),
            }
            for index, task in enumerate(subtasks[:8], start=1)
        ]

    def _normalize_delegate_strategy(self, strategy: object) -> str:
        if not isinstance(strategy, str) or not strategy.strip():
            return 'serial'
        normalized = strategy.strip().lower().replace('-', '_')
        if normalized in {'graph', 'topological', 'dependency_graph', 'parallel', 'parallel_batches'}:
            return 'topological'
        return 'serial'

    def _plan_delegate_batches(
        self,
        subtasks: list[dict[str, object]],
        strategy: str,
    ) -> list[list[dict[str, object]]]:
        if strategy != 'topological':
            return [subtasks]
        remaining = list(subtasks)
        scheduled_labels: set[str] = set()
        known_labels = {
            str(task.get('label'))
            for task in subtasks
            if isinstance(task.get('label'), str) and str(task.get('label')).strip()
        }
        batches: list[list[dict[str, object]]] = []
        while remaining:
            ready: list[dict[str, object]] = []
            blocked: list[dict[str, object]] = []
            for task in remaining:
                dependencies = tuple(
                    item
                    for item in task.get('depends_on', ())
                    if isinstance(item, str) and item
                )
                if any(dependency not in known_labels for dependency in dependencies):
                    blocked.append(task)
                    continue
                if all(dependency in scheduled_labels for dependency in dependencies):
                    ready.append(task)
                else:
                    blocked.append(task)
            if not ready:
                batches.append(blocked)
                break
            batches.append(
                sorted(
                    ready,
                    key=lambda task: int(task.get('_delegate_index', 0)),
                )
            )
            scheduled_labels.update(
                str(task.get('label'))
                for task in ready
                if isinstance(task.get('label'), str) and str(task.get('label')).strip()
            )
            remaining = blocked
        return batches

    def _delegated_task_units(
        self,
        arguments: dict[str, object],
    ) -> int:
        subtasks = arguments.get('subtasks')
        if isinstance(subtasks, list):
            count = sum(
                1
                for item in subtasks
                if (
                    isinstance(item, str)
                    and item.strip()
                ) or (
                    isinstance(item, dict)
                    and isinstance(item.get('prompt'), str)
                    and item.get('prompt', '').strip()
                )
            )
            if count:
                return count
        return 1

    def _prepend_delegate_context(
        self,
        prompt: str,
        prior_results: list[dict[str, str]],
    ) -> str:
        lines = [
            '<system-reminder>',
            'Prior delegated subtask summaries:',
        ]
        for result in prior_results[-4:]:
            lines.append(f"- {result['label']}: {result['output_preview']}")
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _prepend_plugin_delegate_context(
        self,
        prompt: str,
        messages: tuple[str, ...],
    ) -> str:
        if not messages:
            return prompt
        lines = [
            '<system-reminder>',
            'Plugin delegate guidance:',
        ]
        lines.extend(f'- {message}' for message in messages)
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _append_runtime_tool_followup_events(
        self,
        stream_events: list[dict[str, object]],
        *,
        tool_call: ToolCall,
        tool_result: ToolExecutionResult,
    ) -> None:
        metadata = tool_result.metadata
        if metadata.get('action') == 'plugin_virtual_tool':
            stream_events.append(
                {
                    'type': 'plugin_virtual_tool_result',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'plugin_name': metadata.get('plugin_name'),
                    'virtual_tool': metadata.get('virtual_tool'),
                }
            )
        plugin_delegate_preflight = metadata.get('plugin_delegate_preflight_messages')
        if isinstance(plugin_delegate_preflight, list) and plugin_delegate_preflight:
            stream_events.append(
                {
                    'type': 'plugin_delegate_preflight',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'message_count': len(plugin_delegate_preflight),
                }
            )
        plugin_delegate_after = metadata.get('plugin_delegate_after_messages')
        if isinstance(plugin_delegate_after, list) and plugin_delegate_after:
            stream_events.append(
                {
                    'type': 'plugin_delegate_after',
                    'tool_call_id': tool_call.id,
                    'tool_name': tool_call.name,
                    'message_count': len(plugin_delegate_after),
                }
            )
        if tool_call.name not in ('Agent', 'delegate_agent'):
            return
        delegate_batches = metadata.get('delegate_batches')
        if isinstance(delegate_batches, list):
            for batch in delegate_batches:
                if not isinstance(batch, dict):
                    continue
                stream_events.append(
                    {
                        'type': 'delegate_batch_result',
                        'tool_call_id': tool_call.id,
                        'group_id': metadata.get('group_id'),
                        'batch_index': batch.get('batch_index'),
                        'status': batch.get('status'),
                        'labels': batch.get('labels'),
                        'completed_children': batch.get('completed_children'),
                        'failed_children': batch.get('failed_children'),
                        'skipped_children': batch.get('skipped_children'),
                    }
                )
        child_results = metadata.get('child_results')
        if isinstance(child_results, list):
            for child in child_results:
                if not isinstance(child, dict):
                    continue
                stream_events.append(
                    {
                        'type': 'delegate_subtask_result',
                        'tool_call_id': tool_call.id,
                        'group_id': metadata.get('group_id'),
                        'label': child.get('label'),
                        'index': child.get('index'),
                        'session_id': child.get('session_id'),
                        'stop_reason': child.get('stop_reason'),
                        'tool_calls': child.get('tool_calls'),
                        'turns': child.get('turns'),
                        'resume_used': child.get('resume_used'),
                        'resumed_from_session_id': child.get('resumed_from_session_id'),
                        'depends_on': child.get('depends_on'),
                        'batch_index': child.get('batch_index'),
                    }
                )
        if metadata.get('group_id') is not None:
            stream_events.append(
                {
                    'type': 'delegate_group_result',
                    'tool_call_id': tool_call.id,
                    'group_id': metadata.get('group_id'),
                    'group_status': metadata.get('group_status'),
                    'subtask_count': metadata.get('subtask_count'),
                    'completed_children': metadata.get('completed_children'),
                    'failed_children': metadata.get('failed_children'),
                    'resumed_children': metadata.get('resumed_children'),
                    'strategy': metadata.get('strategy'),
                    'max_failures': metadata.get('max_failures'),
                    'batch_count': len(delegate_batches) if isinstance(delegate_batches, list) else 0,
                    'dependency_skips': metadata.get('dependency_skips'),
                }
            )

    def _preview_text(self, text: str, limit: int) -> str:
        normalized = ' '.join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + '...'

    def _ensure_scratchpad_directory(self, session_id: str) -> Path:
        scratchpad_directory = (self.runtime_config.scratchpad_root / session_id).resolve()
        scratchpad_directory.mkdir(parents=True, exist_ok=True)
        return scratchpad_directory

    def _append_file_history_replay_if_needed(
        self,
        session: AgentSessionState,
        file_history: tuple[dict[str, object], ...],
    ) -> None:
        if not file_history:
            return
        replay_count = len(file_history)
        unique_paths = sorted(
            {
                path
                for entry in file_history
                for path in (
                    entry.get('changed_paths')
                    if isinstance(entry.get('changed_paths'), list)
                    else ([entry.get('path')] if isinstance(entry.get('path'), str) else [])
                )
                if isinstance(path, str) and path
            }
        )
        snapshot_count = sum(
            1
            for entry in file_history
            for key in ('before_snapshot_id', 'after_snapshot_id')
            if isinstance(entry.get(key), str) and entry.get(key)
        )
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'file_history_replay':
                continue
            if message.metadata.get('file_history_count') == replay_count:
                return
            break
        session.append_user(
            self._render_file_history_replay(file_history),
            metadata={
                'kind': 'file_history_replay',
                'file_history_count': replay_count,
                'file_history_unique_paths': len(unique_paths),
                'file_history_snapshot_count': snapshot_count,
            },
            message_id=f'file_history_replay_{replay_count}',
        )

    def _render_file_history_replay(
        self,
        file_history: tuple[dict[str, object], ...],
    ) -> str:
        unique_paths = sorted(
            {
                path
                for entry in file_history
                for path in (
                    entry.get('changed_paths')
                    if isinstance(entry.get('changed_paths'), list)
                    else ([entry.get('path')] if isinstance(entry.get('path'), str) else [])
                )
                if isinstance(path, str) and path
            }
        )
        snapshot_count = sum(
            1
            for entry in file_history
            for key in ('before_snapshot_id', 'after_snapshot_id')
            if isinstance(entry.get(key), str) and entry.get(key)
        )
        lines = [
            '<system-reminder>',
            'Recent file history from this saved session:',
            f'- History entries: {len(file_history)}',
            f'- Unique changed paths: {len(unique_paths)}',
            f'- Snapshot ids: {snapshot_count}',
        ]
        if unique_paths:
            preview_paths = ', '.join(unique_paths[:4])
            if len(unique_paths) > 4:
                preview_paths += f', ... (+{len(unique_paths) - 4} more)'
            lines.append(f'- Changed path preview: {preview_paths}')
        for entry in file_history[-10:]:
            action = str(entry.get('action', entry.get('tool_name', 'tool')))
            turn = entry.get('turn_index')
            path = entry.get('path')
            command = entry.get('command')
            details = [f'action={action}']
            history_entry_id = entry.get('history_entry_id')
            if isinstance(history_entry_id, str) and history_entry_id:
                details.append(f'entry_id={history_entry_id}')
            if turn is not None:
                details.append(f'turn={turn}')
            if path:
                details.append(f'path={path}')
            if command:
                details.append(f'command={command}')
            child_session_ids = entry.get('child_session_ids')
            if isinstance(child_session_ids, list) and child_session_ids:
                details.append(f'child_sessions={len(child_session_ids)}')
            delegate_batch_count = entry.get('delegate_batch_count')
            if isinstance(delegate_batch_count, int) and not isinstance(delegate_batch_count, bool):
                details.append(f'batches={delegate_batch_count}')
            dependency_skips = entry.get('dependency_skips')
            if isinstance(dependency_skips, int) and not isinstance(dependency_skips, bool):
                details.append(f'dependency_skips={dependency_skips}')
            lines.append(f"- {'; '.join(details)}")
            before_snapshot_id = entry.get('before_snapshot_id')
            if isinstance(before_snapshot_id, str) and before_snapshot_id:
                lines.append(f'  before_snapshot: {before_snapshot_id}')
            after_snapshot_id = entry.get('after_snapshot_id')
            if isinstance(after_snapshot_id, str) and after_snapshot_id:
                lines.append(f'  after_snapshot: {after_snapshot_id}')
            before_preview = entry.get('before_preview')
            if isinstance(before_preview, str) and before_preview:
                lines.append(f'  before: {before_preview}')
            after_preview = entry.get('after_preview')
            if isinstance(after_preview, str) and after_preview:
                lines.append(f'  after: {after_preview}')
            result_preview = entry.get('result_preview')
            if isinstance(result_preview, str) and result_preview:
                lines.append(f'  result: {result_preview}')
        if len(file_history) > 10:
            lines.append(f'- ... plus {len(file_history) - 10} older file-history entries')
        lines.extend(
            [
                '',
                'Use this replayed history when continuing the task so you avoid repeating prior edits or commands.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _append_compaction_replay_if_needed(
        self,
        session: AgentSessionState,
    ) -> None:
        compact_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'compact_boundary'
        ]
        snipped_messages = [
            message for message in session.messages
            if message.metadata.get('kind') == 'snipped_message'
        ]
        if not compact_messages and not snipped_messages:
            return
        for message in reversed(session.messages):
            if message.metadata.get('kind') != 'compaction_replay':
                continue
            return
        session.append_user(
            self._render_compaction_replay(compact_messages, snipped_messages),
            metadata={
                'kind': 'compaction_replay',
                'compact_boundary_count': len(compact_messages),
                'snipped_message_count': len(snipped_messages),
            },
            message_id=(
                f'compaction_replay_{len(compact_messages)}_{len(snipped_messages)}'
            ),
        )

    def _render_compaction_replay(
        self,
        compact_messages,
        snipped_messages,
    ) -> str:
        lines = [
            '<system-reminder>',
            'This resumed session already contains compacted or snipped history.',
            f'- Compact boundaries: {len(compact_messages)}',
            f'- Snipped/tombstoned messages: {len(snipped_messages)}',
        ]
        latest_boundary = compact_messages[-1] if compact_messages else None
        if latest_boundary is not None:
            lines.append(
                f"- Latest compact boundary id: {latest_boundary.message_id or '(none)'}"
            )
            depth = latest_boundary.metadata.get('compaction_depth')
            if isinstance(depth, int) and not isinstance(depth, bool):
                lines.append(f'- Latest compaction depth: {depth}')
            compacted_lineages = latest_boundary.metadata.get('compacted_lineage_ids')
            if isinstance(compacted_lineages, list) and compacted_lineages:
                lines.append(f'- Latest compacted lineages: {len(compacted_lineages)}')
            max_source_mutation_serial = latest_boundary.metadata.get('max_source_mutation_serial')
            if (
                isinstance(max_source_mutation_serial, int)
                and not isinstance(max_source_mutation_serial, bool)
                and max_source_mutation_serial > 0
            ):
                lines.append(
                    f'- Latest source mutation serial: {max_source_mutation_serial}'
                )
            source_mutation_totals = latest_boundary.metadata.get('source_mutation_totals')
            if isinstance(source_mutation_totals, dict) and source_mutation_totals:
                rendered = ', '.join(
                    f'{name}:{count}'
                    for name, count in sorted(source_mutation_totals.items())
                    if isinstance(name, str)
                    and name
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count > 0
                )
                if rendered:
                    lines.append(f'- Latest compacted mutations: {rendered}')
            preserved_tail = latest_boundary.metadata.get('preserved_tail_ids')
            if isinstance(preserved_tail, list) and preserved_tail:
                lines.append(
                    '- Latest preserved tail ids: '
                    + ', '.join(str(item) for item in preserved_tail[:4])
                )
        if snipped_messages:
            last_ids = [
                message.message_id or '(none)'
                for message in snipped_messages[-3:]
            ]
            lines.append(f"- Recent snipped ids: {', '.join(last_ids)}")
            snipped_lineages = [
                str(message.metadata.get('snipped_from_lineage_id'))
                for message in snipped_messages[-3:]
                if isinstance(message.metadata.get('snipped_from_lineage_id'), str)
            ]
            if snipped_lineages:
                lines.append(f"- Recent snipped lineages: {', '.join(snipped_lineages)}")
        lines.extend(
            [
                '',
                'Use the surviving transcript plus the compacted summaries as the authoritative context when continuing.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _apply_hook_policy_before_prompt_hooks(self, prompt: str) -> str:
        if self.hook_policy_runtime is None or not self.hook_policy_runtime.manifests:
            return prompt
        injections = self.hook_policy_runtime.before_prompt_messages()
        managed_settings = self.hook_policy_runtime.managed_settings()
        safe_env = self.hook_policy_runtime.safe_env()
        trusted = self.hook_policy_runtime.is_trusted()
        if not injections and not managed_settings and not safe_env and trusted:
            return prompt
        lines = ['<system-reminder>', 'Workspace hook/policy guidance:']
        lines.append(
            f'- Trust mode: {"trusted" if trusted else "untrusted"}'
        )
        if not trusted:
            lines.append(
                '- Untrusted workspaces should favor inspection-first behavior. '
                'Avoid unnecessary writes or shell actions unless the task clearly requires them.'
            )
        for entry in injections:
            lines.append(f'- Before prompt: {entry}')
        if managed_settings:
            lines.append(
                '- Managed settings: '
                + ', '.join(f'{key}={value}' for key, value in sorted(managed_settings.items()))
            )
        if safe_env:
            lines.append(
                '- Safe environment values loaded for tools: '
                + ', '.join(sorted(safe_env))
            )
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _build_plugin_tool_runtime_message(
        self,
        *,
        tool_name: str,
        preflight_messages: tuple[str, ...],
        block_message: str | None,
        plugin_messages: tuple[str, ...],
        hook_policy_preflight_messages: tuple[str, ...] = (),
        hook_policy_block_message: str | None = None,
        hook_policy_messages: tuple[str, ...] = (),
        delegate_preflight_messages: tuple[str, ...] = (),
        delegate_after_messages: tuple[str, ...] = (),
    ) -> str | None:
        if (
            block_message is None
            and not plugin_messages
            and not preflight_messages
            and hook_policy_block_message is None
            and not hook_policy_preflight_messages
            and not hook_policy_messages
            and not delegate_preflight_messages
            and not delegate_after_messages
        ):
            return None
        plugin_only = (
            hook_policy_block_message is None
            and not hook_policy_preflight_messages
            and not hook_policy_messages
        )
        lines = [
            '<system-reminder>',
            (
                f'Plugin tool runtime guidance for `{tool_name}`:'
                if plugin_only
                else f'Runtime tool guidance for `{tool_name}`:'
            ),
        ]
        for message in preflight_messages:
            lines.append(f'- Before tool: {message}')
        for message in hook_policy_preflight_messages:
            lines.append(f'- Hook/policy before tool: {message}')
        for message in delegate_preflight_messages:
            lines.append(f'- Before delegate: {message}')
        if block_message is not None:
            lines.append(f'- Blocked: {block_message}')
        if hook_policy_block_message is not None:
            lines.append(f'- Hook/policy blocked: {hook_policy_block_message}')
        for message in plugin_messages:
            lines.append(f'- After result: {message}')
        for message in hook_policy_messages:
            lines.append(f'- Hook/policy after result: {message}')
        for message in delegate_after_messages:
            lines.append(f'- After delegate: {message}')
        lines.extend(
            [
                '',
                'Use this runtime guidance when deciding the next tool call or assistant response.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _plugin_tool_preflight_messages(self, tool_name: str) -> tuple[str, ...]:
        if self.plugin_runtime is None:
            return ()
        return self.plugin_runtime.tool_preflight_injections(tool_name)

    def _plugin_block_message(self, tool_name: str) -> str | None:
        if self.plugin_runtime is None:
            return None
        return self.plugin_runtime.blocked_tool_message(tool_name)

    def _plugin_tool_result_messages(self, tool_name: str) -> tuple[str, ...]:
        if self.plugin_runtime is None:
            return ()
        return self.plugin_runtime.tool_result_injections(tool_name)

    def _hook_policy_tool_preflight_messages(self, tool_name: str) -> tuple[str, ...]:
        if self.hook_policy_runtime is None:
            return ()
        return self.hook_policy_runtime.before_tool_messages(tool_name)

    def _hook_policy_block_message(self, tool_name: str) -> str | None:
        if self.hook_policy_runtime is None:
            return None
        return self.hook_policy_runtime.denied_tool_message(tool_name)

    def _hook_policy_tool_result_messages(self, tool_name: str) -> tuple[str, ...]:
        if self.hook_policy_runtime is None:
            return ()
        return self.hook_policy_runtime.after_tool_messages(tool_name)

    def _persist_session(
        self,
        session: AgentSessionState,
        result: AgentRunResult,
    ) -> AgentRunResult:
        if result.session_id is None:
            # Even on no-session-id paths, clear pending eval stash so it
            # doesn't leak into the next session.
            if self._pending_eval_events:
                self._pending_eval_events.clear()
            return result
        persist_events = list(result.events)
        # Backstop named in 9218119 NOT-COVERED: drain any per-tool eval
        # events that didn't make it through the LLM-call hook (e.g. terminal
        # tool ended the turn directly). Without this they leak across runs.
        if self._pending_eval_events:
            persist_events.extend(self._pending_eval_events)
            self._pending_eval_events.clear()
        if self.plugin_runtime is not None:
            persist_messages = self.plugin_runtime.before_persist_injections()
            if persist_messages:
                session.append_user(
                    self._render_plugin_persist_message(persist_messages),
                    metadata={
                        'kind': 'plugin_persist',
                        'message_count': len(persist_messages),
                    },
                    message_id=f'plugin_persist_{result.session_id}',
                )
                persist_events.append(
                    {
                        'type': 'plugin_before_persist',
                        'session_id': result.session_id,
                        'message_count': len(persist_messages),
                    }
                )
        previous_turns = 0
        previous_tool_calls = 0
        previous_budget_state: dict[str, object] = {}
        existing_path = self.runtime_config.session_directory / f'{result.session_id}.json'
        if existing_path.exists():
            try:
                previous = load_agent_session(
                    result.session_id,
                    directory=self.runtime_config.session_directory,
                )
            except OSError:
                previous = None
            if previous is not None:
                previous_turns = previous.turns
                previous_tool_calls = previous.tool_calls
                if isinstance(previous.budget_state, dict):
                    previous_budget_state = dict(previous.budget_state)
        budget_state = {
            'model_calls': int(previous_budget_state.get('model_calls', 0))
            + max(result.turns, 0),
            'session_turns': previous_turns + result.turns,
            'tool_calls': previous_tool_calls + result.tool_calls,
            'delegated_tasks': sum(
                1 for entry in result.file_history if entry.get('action') in ('delegate_agent', 'Agent')
            ),
        }
        stored = StoredAgentSession(
            session_id=result.session_id,
            model_config=serialize_model_config(self.model_config),
            runtime_config=serialize_runtime_config(self.runtime_config),
            system_prompt_parts=session.system_prompt_parts,
            user_context=dict(session.user_context),
            system_context=dict(session.system_context),
            messages=session.transcript(),
            turns=previous_turns + result.turns,
            tool_calls=previous_tool_calls + result.tool_calls,
            usage=result.usage.to_dict(),
            total_cost_usd=result.total_cost_usd,
            file_history=result.file_history,
            budget_state=budget_state,
            plugin_state=(
                self.plugin_runtime.export_session_state()
                if self.plugin_runtime is not None
                else {}
            ),
            typed_state=(
                self._sm_state.to_dict()
                if self._sm_state is not None and hasattr(self._sm_state, 'to_dict')
                else {}
            ),
            scratchpad_directory=result.scratchpad_directory,
        )
        path = save_agent_session(
            stored,
            directory=self.runtime_config.session_directory,
        )
        self.last_session_path = str(path)
        checkpoint_event = {
            'type': 'session_checkpoint',
            'session_id': result.session_id,
            'session_path': self.last_session_path,
            'typed_state_checkpointed': bool(stored.typed_state),
            'typed_state_turn_id': stored.typed_state.get('turn_id'),
            'turns': stored.turns,
            'tool_calls': stored.tool_calls,
        }
        persist_events.append(checkpoint_event)
        self._emit_runtime_event(checkpoint_event)
        return replace(
            result,
            session_path=self.last_session_path,
            events=tuple(persist_events),
            transcript=session.transcript(),
        )

    def render_system_prompt(self) -> str:
        prompt_context = self.build_prompt_context()
        parts = self.build_system_prompt_parts(prompt_context)
        return render_system_prompt(parts)

    def render_context_report(self, prompt: str | None = None) -> str:
        session = self.last_session if prompt is None else None
        strategy = 'current Python session'
        if session is None:
            session = self.build_session(prompt)
            strategy = 'one-shot Python session preview'
        report = collect_context_usage(
            session=session,
            model=self.model_config.model,
            strategy=strategy,
        )
        return format_context_usage(report)

    def render_context_snapshot_report(self) -> str:
        prompt_context = self.build_prompt_context()
        return render_agent_context_report(prompt_context, self.model_config.model)

    def render_permissions_report(self) -> str:
        permissions = self.runtime_config.permissions
        lines = [
            '# Permissions',
            '',
            f'- File write tools: {"enabled" if permissions.allow_file_write else "disabled"}',
            f'- Shell commands: {"enabled" if permissions.allow_shell_commands else "disabled"}',
            f'- Destructive shell commands: {"enabled" if permissions.allow_destructive_shell_commands else "disabled"}',
        ]
        if self.hook_policy_runtime is not None and self.hook_policy_runtime.manifests:
            lines.append(
                f'- Workspace trust mode: {"trusted" if self.hook_policy_runtime.is_trusted() else "untrusted"}'
            )
            denied_tools = sorted(
                {
                    name
                    for manifest in self.hook_policy_runtime.manifests
                    for name in manifest.deny_tools
                }
            )
            if denied_tools:
                lines.append('- Policy-denied tools: ' + ', '.join(denied_tools))
        return '\n'.join(lines)

    def render_tools_report(self) -> str:
        permissions = self.runtime_config.permissions
        lines = ['# Tools', '']
        for tool in self.tool_registry.values():
            state = 'enabled'
            if tool.name == 'bash' and not permissions.allow_shell_commands:
                state = 'blocked by permissions'
            if tool.name in {'write_file', 'edit_file'} and not permissions.allow_file_write:
                state = 'blocked by permissions'
            if (
                self.hook_policy_runtime is not None
                and self.hook_policy_runtime.denied_tool_message(tool.name) is not None
            ):
                state = 'blocked by hook policy'
            lines.append(f'- `{tool.name}`: {tool.description} [{state}]')
        return '\n'.join(lines)

    def render_agents_report(self, *, show_all: bool = False) -> str:
        snapshot = self.load_agent_registry()
        return render_agents_report(
            snapshot,
            cwd=self.runtime_config.cwd,
            show_all=show_all,
        )

    def render_agent_detail_report(self, agent_type: str) -> str:
        snapshot = self.load_agent_registry()
        return render_agent_detail(snapshot, agent_type)

    def render_agent_create_report(
        self,
        agent_type: str,
        *,
        source: str = 'projectSettings',
        description: str | None = None,
        system_prompt: str | None = None,
        overwrite: bool = False,
    ) -> str:
        result = scaffold_agent_definition(
            self.runtime_config.cwd,
            agent_type=agent_type,
            source=normalize_mutable_source(source),
            overwrite=overwrite,
            description=description,
            system_prompt=system_prompt,
        )
        return render_agent_mutation(result)

    def render_agent_update_report(
        self,
        agent_type: str,
        *,
        source: str = 'auto',
        description: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        update_kwargs: dict[str, object] = {}
        if description is not None:
            update_kwargs['description'] = description
        if system_prompt is not None:
            update_kwargs['system_prompt'] = system_prompt
        result = update_agent_definition(
            self.runtime_config.cwd,
            agent_type=agent_type,
            source=normalize_mutable_source(source, allow_auto=True),
            **update_kwargs,
        )
        return render_agent_mutation(result)

    def render_agent_delete_report(
        self,
        agent_type: str,
        *,
        source: str = 'auto',
    ) -> str:
        result = delete_agent_definition(
            self.runtime_config.cwd,
            agent_type=agent_type,
            source=normalize_mutable_source(source, allow_auto=True),
        )
        return render_agent_mutation(result)

    def render_memory_report(self) -> str:
        prompt_context = self.build_prompt_context()
        claude_md = prompt_context.user_context.get('claudeMd')
        if not claude_md:
            return '# Memory\n\nNo CLAUDE.md memory files are currently loaded.'
        return '\n'.join(['# Memory', '', claude_md])

    def render_account_report(self, profile: str | None = None) -> str:
        if self.account_runtime is None:
            return '# Account\n\nNo local account runtime is available.'
        if profile:
            return self.account_runtime.render_profile(profile)
        return '\n'.join(['# Account', '', self.account_runtime.render_summary()])

    def render_search_report(
        self,
        query: str | None = None,
        *,
        provider: str | None = None,
        max_results: int = 5,
        domains: tuple[str, ...] = (),
    ) -> str:
        if self.search_runtime is None or not self.search_runtime.has_search_runtime():
            return (
                '# Search\n\nNo local search provider is available. '
                'Add a .claw-search.json or .claude/search.json manifest, '
                'or set SEARXNG_BASE_URL, BRAVE_SEARCH_API_KEY, or TAVILY_API_KEY.'
            )
        if query:
            try:
                return self.search_runtime.render_search_results(
                    query,
                    provider_name=provider,
                    max_results=max_results,
                    domains=domains,
                    timeout_seconds=self.runtime_config.command_timeout_seconds,
                )
            except (KeyError, LookupError, OSError, ValueError) as exc:
                return f'# Search\n\nSearch failed: {exc}'
        if provider:
            return self.search_runtime.render_provider(provider)
        return '\n'.join(['# Search', '', self.search_runtime.render_summary()])

    def render_search_providers_report(self, query: str | None = None) -> str:
        if self.search_runtime is None or not self.search_runtime.has_search_runtime():
            return '# Search Providers\n\nNo local search providers discovered.'
        return self.search_runtime.render_providers_index(query=query)

    def render_search_activate_report(self, provider: str) -> str:
        if self.search_runtime is None or not self.search_runtime.has_search_runtime():
            return '# Search\n\nNo local search provider is available.'
        try:
            report = self.search_runtime.activate_provider(provider)
        except KeyError:
            return f'# Search\n\nUnknown search provider: {provider}'
        clear_context_caches()
        self.tool_context = replace(
            self.tool_context,
            search_runtime=self.search_runtime,
        )
        return '\n'.join(['# Search', '', report.as_text()])

    def render_account_profiles_report(self, query: str | None = None) -> str:
        if self.account_runtime is None:
            return '# Account Profiles\n\nNo local account runtime is available.'
        return self.account_runtime.render_profiles_index(query=query)

    def render_account_login_report(
        self,
        target: str,
        *,
        provider: str | None = None,
        auth_mode: str | None = None,
    ) -> str:
        if self.account_runtime is None:
            return '# Account\n\nNo local account runtime is available.'
        report = self.account_runtime.login(target, provider=provider, auth_mode=auth_mode)
        clear_context_caches()
        return '\n'.join(['# Account', '', report.as_text()])

    def render_account_logout_report(self) -> str:
        if self.account_runtime is None:
            return '# Account\n\nNo local account runtime is available.'
        report = self.account_runtime.logout(reason='slash_or_cli_logout')
        clear_context_caches()
        return '\n'.join(['# Account', '', report.as_text()])

    def render_config_report(self) -> str:
        if self.config_runtime is None:
            return '# Config\n\nNo local config runtime is available.'
        return '\n'.join(['# Config', '', self.config_runtime.render_summary()])

    def render_lsp_report(self) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP\n\nNo local LSP runtime is available.'
        return '\n'.join(['# LSP', '', self.lsp_runtime.render_summary()])

    def render_lsp_document_symbols_report(self, file_path: str) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Document Symbols\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_document_symbols(file_path)
        except KeyError as exc:
            return f'# LSP Document Symbols\n\n{exc}'

    def render_lsp_workspace_symbols_report(
        self,
        query: str,
        *,
        max_results: int = 50,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Workspace Symbols\n\nNo local LSP runtime is available.'
        return self.lsp_runtime.render_workspace_symbols(query, max_results=max_results)

    def render_lsp_definition_report(
        self,
        file_path: str,
        line: int,
        character: int,
        *,
        max_results: int = 20,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Definition\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_definition(
                file_path,
                line,
                character,
                max_results=max_results,
            )
        except KeyError as exc:
            return f'# LSP Definition\n\n{exc}'

    def render_lsp_references_report(
        self,
        file_path: str,
        line: int,
        character: int,
        *,
        max_results: int = 50,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP References\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_references(
                file_path,
                line,
                character,
                max_results=max_results,
            )
        except KeyError as exc:
            return f'# LSP References\n\n{exc}'

    def render_lsp_hover_report(self, file_path: str, line: int, character: int) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Hover\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_hover(file_path, line, character)
        except KeyError as exc:
            return f'# LSP Hover\n\n{exc}'

    def render_lsp_diagnostics_report(self, file_path: str | None = None) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Diagnostics\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_diagnostics(file_path)
        except KeyError as exc:
            return f'# LSP Diagnostics\n\n{exc}'

    def render_lsp_prepare_call_hierarchy_report(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Call Hierarchy\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_prepare_call_hierarchy(file_path, line, character)
        except KeyError as exc:
            return f'# LSP Call Hierarchy\n\n{exc}'

    def render_lsp_incoming_calls_report(
        self,
        file_path: str,
        line: int,
        character: int,
        *,
        max_results: int = 50,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Incoming Calls\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_incoming_calls(
                file_path,
                line,
                character,
                max_results=max_results,
            )
        except KeyError as exc:
            return f'# LSP Incoming Calls\n\n{exc}'

    def render_lsp_outgoing_calls_report(
        self,
        file_path: str,
        line: int,
        character: int,
        *,
        max_results: int = 50,
    ) -> str:
        if self.lsp_runtime is None or not self.lsp_runtime.has_lsp_support():
            return '# LSP Outgoing Calls\n\nNo local LSP runtime is available.'
        try:
            return self.lsp_runtime.render_outgoing_calls(
                file_path,
                line,
                character,
                max_results=max_results,
            )
        except KeyError as exc:
            return f'# LSP Outgoing Calls\n\n{exc}'

    def render_config_effective_report(self) -> str:
        if self.config_runtime is None:
            return '# Config Effective\n\nNo local config runtime is available.'
        return '\n'.join(['# Config Effective', '', self.config_runtime.render_effective_config()])

    def render_config_source_report(self, source: str) -> str:
        if self.config_runtime is None:
            return '# Config Source\n\nNo local config runtime is available.'
        return '\n'.join(['# Config Source', '', self.config_runtime.render_source(source)])

    def render_config_value_report(self, key_path: str, source: str | None = None) -> str:
        if self.config_runtime is None:
            return '# Config Value\n\nNo local config runtime is available.'
        try:
            rendered = self.config_runtime.render_value(key_path, source=source)
        except KeyError as exc:
            label = source if source is not None else key_path
            return f'# Config Value\n\nUnknown config key or source: {label or exc.args[0]}'
        return '\n'.join(['# Config Value', '', rendered])

    def render_mcp_report(self, query: str | None = None) -> str:
        if self.mcp_runtime is None:
            return '# MCP\n\nNo local MCP manifests, servers, or resources discovered.'
        if query:
            return self.mcp_runtime.render_resource_index(query=query)
        return '\n'.join(['# MCP', '', self.mcp_runtime.render_summary()])

    def render_remote_report(self, target: str | None = None) -> str:
        if self.remote_runtime is None:
            return '# Remote\n\nNo local remote runtime is available.'
        if target:
            report = self.remote_runtime.connect(target)
            clear_context_caches()
            return '\n'.join(['# Remote', '', report.as_text()])
        return '\n'.join(['# Remote', '', self.remote_runtime.render_summary()])

    def render_remote_mode_report(self, target: str, *, mode: str) -> str:
        if self.remote_runtime is None:
            return '# Remote\n\nNo local remote runtime is available.'
        report = self.remote_runtime.connect(target, mode=mode)
        clear_context_caches()
        return '\n'.join(['# Remote', '', report.as_text()])

    def render_remote_profiles_report(self, query: str | None = None) -> str:
        if self.remote_runtime is None:
            return '# Remote Profiles\n\nNo local remote runtime is available.'
        return self.remote_runtime.render_profiles_index(query=query)

    def render_remote_disconnect_report(self) -> str:
        if self.remote_runtime is None:
            return '# Remote\n\nNo local remote runtime is available.'
        report = self.remote_runtime.disconnect()
        clear_context_caches()
        return '\n'.join(['# Remote', '', report.as_text()])

    def render_worktree_report(self) -> str:
        if self.worktree_runtime is None:
            return '# Worktree\n\nNo local worktree runtime is available.'
        return '\n'.join(['# Worktree', '', self.worktree_runtime.render_summary()])

    def render_worktree_enter_report(self, name: str | None = None) -> str:
        if self.worktree_runtime is None:
            return '# Worktree\n\nNo local worktree runtime is available.'
        try:
            report = self.worktree_runtime.enter(name=name)
        except (RuntimeError, ValueError) as exc:
            return f'# Worktree\n\n{exc}'
        self._apply_runtime_cwd_update(Path(report.worktree_path or self.runtime_config.cwd))
        return '\n'.join(['# Worktree', '', report.as_text()])

    def render_worktree_exit_report(
        self,
        *,
        action: str = 'keep',
        discard_changes: bool = False,
    ) -> str:
        if self.worktree_runtime is None:
            return '# Worktree\n\nNo local worktree runtime is available.'
        try:
            report = self.worktree_runtime.exit(
                action=action,
                discard_changes=discard_changes,
            )
        except (RuntimeError, ValueError) as exc:
            return f'# Worktree\n\n{exc}'
        target_cwd = report.original_cwd or report.current_cwd or str(self.runtime_config.cwd)
        self._apply_runtime_cwd_update(Path(target_cwd))
        return '\n'.join(['# Worktree', '', report.as_text()])

    def render_worktree_history_report(self) -> str:
        if self.worktree_runtime is None:
            return '# Worktree History\n\nNo local worktree runtime is available.'
        return self.worktree_runtime.render_history()

    def render_mcp_resources_report(self, query: str | None = None) -> str:
        if self.mcp_runtime is None:
            return '# MCP Resources\n\nNo local MCP manifests, servers, or resources discovered.'
        return self.mcp_runtime.render_resource_index(query=query)

    def render_mcp_resource_report(self, uri: str) -> str:
        if self.mcp_runtime is None:
            return '# MCP Resource\n\nNo local MCP manifests, servers, or resources discovered.'
        return self.mcp_runtime.render_resource(uri)

    def render_mcp_tools_report(
        self,
        query: str | None = None,
        *,
        server: str | None = None,
    ) -> str:
        if self.mcp_runtime is None:
            return '# MCP Tools\n\nNo local MCP manifests, servers, or resources discovered.'
        return self.mcp_runtime.render_tool_index(query=query, server_name=server)

    def render_mcp_call_tool_report(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        server: str | None = None,
    ) -> str:
        if self.mcp_runtime is None:
            return '# MCP Tool Result\n\nNo local MCP manifests, servers, or resources discovered.'
        try:
            return self.mcp_runtime.render_tool_call(
                tool_name,
                arguments=arguments,
                server_name=server,
            )
        except FileNotFoundError as exc:
            return f'# MCP Tool Result\n\n{exc}'

    def render_tasks_report(self, status: str | None = None) -> str:
        if self.task_runtime is None:
            return '# Tasks\n\nNo local task runtime is available.'
        return self.task_runtime.render_tasks(status=status)

    def render_next_tasks_report(self) -> str:
        if self.task_runtime is None:
            return '# Next Tasks\n\nNo local task runtime is available.'
        return self.task_runtime.render_next_tasks()

    def render_plan_report(self) -> str:
        if self.plan_runtime is None:
            return '# Plan\n\nNo local plan runtime is available.'
        return self.plan_runtime.render_plan()

    def render_task_report(self, task_id: str) -> str:
        if self.task_runtime is None:
            return '# Task\n\nNo local task runtime is available.'
        return self.task_runtime.render_task(task_id)

    def render_ask_user_report(self) -> str:
        if self.ask_user_runtime is None:
            return '# Ask User\n\nNo local ask-user runtime is available.'
        return '\n'.join(['# Ask User', '', self.ask_user_runtime.render_summary()])

    def render_ask_user_history_report(self) -> str:
        if self.ask_user_runtime is None:
            return '# Ask User History\n\nNo local ask-user runtime is available.'
        return self.ask_user_runtime.render_history()

    def render_teams_report(self, query: str | None = None) -> str:
        if self.team_runtime is None:
            return '# Teams\n\nNo local team runtime is available.'
        return self.team_runtime.render_teams_index(query=query)

    def render_team_report(self, team_name: str) -> str:
        if self.team_runtime is None:
            return '# Team\n\nNo local team runtime is available.'
        try:
            return self.team_runtime.render_team(team_name)
        except KeyError:
            return f'# Team\n\nUnknown team: {team_name}'

    def render_team_messages_report(self, team_name: str | None = None) -> str:
        if self.team_runtime is None:
            return '# Team Messages\n\nNo local team runtime is available.'
        try:
            return self.team_runtime.render_messages(team_name=team_name)
        except KeyError:
            return f'# Team Messages\n\nUnknown team: {team_name}'

    def render_workflows_report(self, query: str | None = None) -> str:
        if self.workflow_runtime is None or not self.workflow_runtime.has_workflows():
            return '# Workflows\n\nNo local workflow runtime is available.'
        return self.workflow_runtime.render_workflows_index(query=query)

    def render_workflow_report(self, workflow_name: str) -> str:
        if self.workflow_runtime is None or not self.workflow_runtime.has_workflows():
            return '# Workflow\n\nNo local workflow runtime is available.'
        try:
            return self.workflow_runtime.render_workflow(workflow_name)
        except KeyError:
            return f'# Workflow\n\nUnknown workflow: {workflow_name}'

    def render_workflow_run_report(
        self,
        workflow_name: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        if self.workflow_runtime is None or not self.workflow_runtime.has_workflows():
            return '# Workflow Run\n\nNo local workflow runtime is available.'
        try:
            return self.workflow_runtime.render_run_report(
                workflow_name,
                arguments=arguments,
            )
        except KeyError:
            return f'# Workflow Run\n\nUnknown workflow: {workflow_name}'

    def render_remote_triggers_report(self, query: str | None = None) -> str:
        if self.remote_trigger_runtime is None or not self.remote_trigger_runtime.has_state():
            return '# Remote Triggers\n\nNo local remote trigger runtime is available.'
        return self.remote_trigger_runtime.render_trigger_index(query=query)

    def render_remote_trigger_report(self, trigger_id: str) -> str:
        if self.remote_trigger_runtime is None or not self.remote_trigger_runtime.has_state():
            return '# Remote Trigger\n\nNo local remote trigger runtime is available.'
        try:
            return self.remote_trigger_runtime.render_trigger(trigger_id)
        except KeyError:
            return f'# Remote Trigger\n\nUnknown remote trigger: {trigger_id}'

    def render_remote_trigger_action_report(
        self,
        action: str,
        *,
        trigger_id: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> str:
        if self.remote_trigger_runtime is None:
            return '# Remote Trigger\n\nNo local remote trigger runtime is available.'
        normalized = action.strip().lower()
        try:
            if normalized == 'list':
                return self.remote_trigger_runtime.render_trigger_index()
            if normalized == 'get':
                if not trigger_id:
                    return '# Remote Trigger\n\ntrigger_id is required for get'
                return self.remote_trigger_runtime.render_trigger(trigger_id)
            if normalized == 'create':
                created = self.remote_trigger_runtime.create_trigger(body or {})
                return self.remote_trigger_runtime.render_trigger(created.trigger_id)
            if normalized == 'update':
                if not trigger_id:
                    return '# Remote Trigger\n\ntrigger_id is required for update'
                updated = self.remote_trigger_runtime.update_trigger(trigger_id, body or {})
                return self.remote_trigger_runtime.render_trigger(updated.trigger_id)
            if normalized == 'run':
                if not trigger_id:
                    return '# Remote Trigger Run\n\ntrigger_id is required for run'
                return self.remote_trigger_runtime.render_run_report(trigger_id, body=body)
        except (KeyError, TypeError, ValueError) as exc:
            return f'# Remote Trigger\n\n{exc}'
        return '# Remote Trigger\n\naction must be one of list, get, create, update, or run'

    def render_hook_policy_report(self) -> str:
        if self.hook_policy_runtime is None:
            return '# Hook Policy\n\nNo local hook or policy manifests discovered.'
        return '\n'.join(['# Hook Policy', '', self.hook_policy_runtime.render_summary()])

    def render_trust_report(self) -> str:
        trusted = True
        settings: dict[str, Any] = {}
        env_values: dict[str, str] = {}
        if self.hook_policy_runtime is not None:
            trusted = self.hook_policy_runtime.is_trusted()
            settings = self.hook_policy_runtime.managed_settings()
            env_values = self.hook_policy_runtime.safe_env()
        lines = [
            '# Trust',
            '',
            f'- Workspace trust mode: {"trusted" if trusted else "untrusted"}',
        ]
        if settings:
            lines.append('- Managed settings:')
            lines.extend(f'  - {key}={value}' for key, value in sorted(settings.items()))
        if env_values:
            lines.append('- Safe environment values:')
            lines.extend(f'  - {key}={value}' for key, value in sorted(env_values.items()))
        return '\n'.join(lines)

    def render_status_report(self) -> str:
        token_counter = describe_token_counter(self.model_config.model)
        lines = [
            '# Status',
            '',
            f'- Model: {self.model_config.model}',
            f'- Token counter: {token_counter.backend} ({token_counter.source})',
            f'- Registered tools: {len(self.tool_registry)}',
            f'- Streaming model responses: {self.runtime_config.stream_model_responses}',
            f'- Session ID: {self.active_session_id or "none"}',
            f'- Last session loaded: {"yes" if self.last_session is not None else "no"}',
        ]
        if self.last_session is not None:
            token_budget = calculate_token_budget(
                session=self.last_session,
                model=self.model_config.model,
                budget_config=self.runtime_config.budget_config,
                output_schema=self.runtime_config.output_schema,
            )
            lines.append(
                f'- Prompt budget: {token_budget.projected_input_tokens:,} / {token_budget.soft_input_limit_tokens:,} soft'
            )
            lines.append(
                f'- Prompt hard limit: {token_budget.hard_input_limit_tokens:,}'
            )
        if self.hook_policy_runtime is not None and self.hook_policy_runtime.manifests:
            lines.append(
                f'- Workspace trust mode: {"trusted" if self.hook_policy_runtime.is_trusted() else "untrusted"}'
            )
        if self.mcp_runtime is not None:
            if self.mcp_runtime.resources:
                lines.append(f'- MCP local resources: {len(self.mcp_runtime.resources)}')
            if self.mcp_runtime.servers:
                lines.append(f'- MCP servers: {len(self.mcp_runtime.servers)}')
        if self.remote_runtime is not None and self.remote_runtime.has_remote_config():
            lines.append(f'- Remote profiles: {len(self.remote_runtime.profiles)}')
            if self.remote_runtime.active_connection is not None:
                connection = self.remote_runtime.active_connection
                lines.append(
                    f'- Active remote: {connection.mode} -> {connection.target}'
                )
        if self.search_runtime is not None and self.search_runtime.has_search_runtime():
            lines.append(f'- Search providers: {len(self.search_runtime.providers)}')
            active_provider = self.search_runtime.current_provider()
            if active_provider is not None:
                lines.append(
                    f'- Active search provider: {active_provider.name} ({active_provider.provider})'
                )
        if self.account_runtime is not None and self.account_runtime.has_account_state():
            lines.append(f'- Account profiles: {len(self.account_runtime.profiles)}')
            if self.account_runtime.active_session is not None:
                session = self.account_runtime.active_session
                lines.append(
                    f'- Active account: {session.provider} -> {session.identity}'
                )
        if self.ask_user_runtime is not None and self.ask_user_runtime.has_state():
            lines.append(f'- Ask-user queued answers: {len(self.ask_user_runtime.queued_answers)}')
            lines.append(f'- Ask-user history: {len(self.ask_user_runtime.history)}')
        if self.config_runtime is not None and self.config_runtime.has_config():
            lines.append(f'- Config sources: {len(self.config_runtime.sources)}')
            lines.append(
                f'- Effective config keys: {len(self.config_runtime.list_keys())}'
            )
        if self.lsp_runtime is not None and self.lsp_runtime.has_lsp_support():
            lines.append(
                f'- LSP indexed files: {len(self.lsp_runtime._workspace_files())}'
            )
        if self.plan_runtime is not None and self.plan_runtime.steps:
            lines.append(f'- Local plan steps: {len(self.plan_runtime.steps)}')
        if self.task_runtime is not None and self.task_runtime.tasks:
            lines.append(f'- Local tasks: {len(self.task_runtime.tasks)}')
        if self.team_runtime is not None and self.team_runtime.has_team_state():
            lines.append(f'- Local teams: {len(self.team_runtime.teams)}')
            lines.append(f'- Team messages: {len(self.team_runtime.messages)}')
        if self.last_session_path is not None:
            lines.append(f'- Session path: {self.last_session_path}')
        if self.last_run_result is not None:
            lines.extend(
                [
                    f'- Last run turns: {self.last_run_result.turns}',
                    f'- Last run tool calls: {self.last_run_result.tool_calls}',
                    f'- Last run total tokens: {self.last_run_result.usage.total_tokens}',
                    f'- Last run total cost: ${self.last_run_result.total_cost_usd:.6f}',
                ]
            )
            if self.last_run_result.scratchpad_directory is not None:
                lines.append(
                    f'- Scratchpad directory: {self.last_run_result.scratchpad_directory}'
                )
        else:
            lines.append('- Last run: none')
        if self.agent_manager is not None:
            lines.extend(self.agent_manager.summary_lines())
        return '\n'.join(lines)

    def render_token_budget_report(self) -> str:
        session = self.last_session or self.build_session()
        snapshot = calculate_token_budget(
            session=session,
            model=self.model_config.model,
            budget_config=self.runtime_config.budget_config,
            output_schema=self.runtime_config.output_schema,
        )
        return format_token_budget(snapshot)

    def _finalize_managed_agent(self, result: AgentRunResult) -> None:
        if self.managed_agent_id is None or self.agent_manager is None:
            self.resume_source_session_id = None
            return
        self.agent_manager.finish_agent(
            self.managed_agent_id,
            session_id=result.session_id,
            session_path=result.session_path,
            turns=result.turns,
            tool_calls=result.tool_calls,
            stop_reason=result.stop_reason,
        )
        self.resume_source_session_id = None

    def _check_rotation_activation(self, prompt: str) -> str:
        """Check if rotation signal exists and activate if needed.
        
        If the rotation gate fired in a prior turn, a signal file will exist.
        This method detects it, activates self-axis mode, and returns a modified
        prompt that includes the self-directed task.
        
        Returns the original prompt if no rotation signal, or a self-axis prompt
        if rotation is activated.
        """
        import sys
        from pathlib import Path
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return prompt
            
            sys.path.insert(0, str(latti_home / 'lib'))
            from rotation_activator import activate_rotation  # type: ignore[import-not-found]
            
            activation = activate_rotation()
            if activation.activated and activation.prompt:
                # Log activation
                import json
                import time
                journal_path = latti_home / 'memory' / 'rotation_journal.jsonl'
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                
                entry = {
                    'timestamp': time.time(),
                    'event': 'rotation_activated',
                    'task_id': activation.task_id,
                    'task_title': activation.task_title,
                }
                with open(journal_path, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
                
                # Return the self-axis prompt
                return activation.prompt
        except Exception:
            # Fail silent — must never break the model loop
            pass
        
        return prompt
    
    def _check_rotation_gate(self, result: AgentRunResult) -> None:
        """Check if we should rotate to self-directed work.
        
        This is the decision gate that prevents orbit. It evaluates three layers
        of cost (audit, orbit, debt) and forces rotation if total cost exceeds
        threshold. Best-effort; failures are swallowed.
        """
        import sys
        from pathlib import Path
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return
            
            sys.path.insert(0, str(latti_home / 'lib'))
            from rotation_gate import should_rotate  # type: ignore[import-not-found]
            
            if should_rotate():
                # Log rotation decision
                import json
                import time
                journal_path = latti_home / 'memory' / 'rotation_journal.jsonl'
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                
                entry = {
                    'timestamp': time.time(),
                    'session_id': os.environ.get('LATTI_SESSION_ID', result.session_id),
                    'reason': 'rotation_gate_fired',
                    'turns': result.turns,
                    'stop_reason': result.stop_reason,
                }
                with open(journal_path, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
                
                # Trigger rotation: pick a pending self-axis task and write signal
                try:
                    from rotation_trigger import trigger_rotation  # type: ignore[import-not-found]
                    session_id = os.environ.get('LATTI_SESSION_ID', result.session_id)
                    if trigger_rotation(session_id):
                        # Rotation signal written; caller can detect and act on it
                        pass
                except Exception:
                    pass  # Rotation trigger is best-effort
        except Exception:
            # Fail silent — must never break the model loop
            pass

    def _compute_response_quality(self, result: AgentRunResult) -> int:
        """Compute response quality score (0-100) based on response characteristics.
        
        Evaluates:
        - Tool usage (20 points): Did the agent use tools?
        - Conciseness (10 points): Is the response reasonably sized?
        - No anti-patterns (10 points): Avoids common failure modes
        - No trailing questions (10 points): Doesn't end with permission-seeking
        - No permission asking (10 points): Doesn't ask for permission
        - Substantive output (40 points): Has meaningful final output
        
        Returns: 0-100 score
        """
        try:
            score = 0
            final_output = getattr(result, 'final_output', '') or ''
            
            # Tool usage (20 points)
            if len(result.tool_calls) > 0:
                score += 20
            
            # Conciseness (10 points) - reasonable length
            output_len = len(final_output.strip())
            if 50 < output_len < 5000:
                score += 10
            elif output_len > 0:
                score += 5  # Partial credit for any output
            
            # No anti-patterns (10 points)
            anti_patterns = [
                'i cannot', 'i am unable', 'i do not have access',
                'i cannot help', 'i cannot provide', 'i cannot create',
                'i cannot write', 'i cannot generate', 'i cannot execute',
            ]
            has_anti_pattern = any(
                pattern in final_output.lower() 
                for pattern in anti_patterns
            )
            if not has_anti_pattern:
                score += 10
            
            # No trailing questions (10 points)
            if final_output.strip() and not final_output.strip().endswith('?'):
                score += 10
            
            # No permission asking (10 points)
            permission_phrases = [
                'would you like', 'do you want', 'should i',
                'may i', 'can i', 'shall i', 'would you prefer',
            ]
            asks_permission = any(
                phrase in final_output.lower()
                for phrase in permission_phrases
            )
            if not asks_permission:
                score += 10
            
            # Substantive output (40 points)
            if output_len > 100:
                score += 40
            elif output_len > 50:
                score += 20
            elif output_len > 0:
                score += 10
            
            return min(100, score)
        except Exception:
            # Default to neutral score on error
            return 50

    def _record_self_axis_outcome(self, result: AgentRunResult) -> None:
        """Record outcome of a self-axis task for feedback loop analysis.
        
        This captures metrics before/after a self-directed work session so the
        pattern learner can identify which task types lead to system improvements.
        Best-effort; failures are swallowed.
        """
        import sys
        from pathlib import Path
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return
            
            sys.path.insert(0, str(latti_home / 'lib'))
            from outcome_recorder import record_task_outcome  # type: ignore[import-not-found]
            
            # Compute response quality score
            quality_score = self._compute_response_quality(result)
            
            # Check if this was a self-axis task (indicated by rotation activation)
            # We detect this by checking if the prompt contained self-axis markers
            # For now, we record all outcomes and let the recorder filter
            record_task_outcome(
                task_id=os.environ.get('LATTI_TASK_ID', 'unknown'),
                title=os.environ.get('LATTI_TASK_TITLE', 'self-axis-work'),
                success=result.stop_reason == 'end_turn',
                changes_made=len(result.tool_calls) > 0,
                metrics={
                    'turns': result.turns,
                    'tool_calls': len(result.tool_calls),
                    'stop_reason': result.stop_reason,
                    'quality_score': quality_score,
                }
            )
        except Exception:
            # Fail silent — must never break the model loop
            pass

    def _accumulate_usage(self, result: AgentRunResult) -> None:
        """Add a run's usage to the cumulative session totals."""
        self.cumulative_usage = self.cumulative_usage + result.usage
        self.cumulative_cost_usd += result.total_cost_usd
        self._emit_cost_ledger(result)
        self._emit_session_turn(result)
        self._emit_claims(result)
        self._record_scar(result)

    def _emit_claims(self, result: AgentRunResult) -> None:
        """Extract substantive claims from final_output and register them so
        future sessions can recognize echoes of the AI's own positions
        without re-deriving from scratch. Best-effort; no-op without Latti."""
        import sys
        from pathlib import Path
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return
            scripts = latti_home / 'scripts'
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            from claims import register_from_response  # type: ignore[import-not-found]
            final_output = getattr(result, 'final_output', '') or ''
            if not final_output or len(final_output) < 80:
                return
            
            # ENFORCE CITATIONS: rewrite uncited claims before registering
            # This is the independent axis work that breaks orbit
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from citation_enforcer_v2 import enforce_citations
                final_output, is_clean = enforce_citations(final_output, strict=False)
                # Update result with rewritten output
                if hasattr(result, 'final_output'):
                    result.final_output = final_output
            except Exception:
                pass  # Citation enforcement is best-effort
            
            register_from_response(
                final_output,
                session_id=os.environ.get('LATTI_SESSION_ID'),
            )
            # Audit the response for uncited claims (Phase 2 integration)
            self._audit_response_claims(result, final_output)
        except Exception:
            pass

    def _audit_response_claims(self, result: AgentRunResult, final_output: str) -> None:
        """Audit the response for uncited claims and log to audit journal.
        
        Gated by LATTI_AUDIT env var (default 1 when invoked via shim).
        Best-effort; failures are swallowed to avoid disrupting the model loop.
        """
        import sys
        from pathlib import Path
        
        # Check if audit is enabled
        if os.environ.get('LATTI_AUDIT', '0') != '1':
            return
        
        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return
            
            # Import the audit integration
            sys.path.insert(0, str(latti_home))
            sys.path.insert(0, str(latti_home / 'lib'))
            from agent_audit_integration import audit_agent_response  # type: ignore[import-not-found]
            
            # Run the audit
            check_hard_fail = os.environ.get('LATTI_AUDIT_HARD_FAIL', '0') == '1'
            audit_result = audit_agent_response(
                final_output,
                fail_mode='warn',
                check_hard_fail=check_hard_fail,
            )
            
            # Log to audit journal
            if audit_result:
                import json
                import time
                journal_path = latti_home / 'memory' / 'audit_journal.jsonl'
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                
                entry = {
                    'timestamp': time.time(),
                    'session_id': os.environ.get('LATTI_SESSION_ID', 'unknown'),
                    'passed': audit_result.get('passed', False),
                    'uncited_count': audit_result.get('uncited_count', 0),
                    'severity_max': audit_result.get('severity_max', 0.0),
                    'corrections': audit_result.get('corrections', []),
                }
                with open(journal_path, 'a') as f:
                    f.write(json.dumps(entry) + '\n')
                
                # Generate auto-correction tasks (independent axis work)
                # This breaks orbit: audit failures → auto-generated work
                if not audit_result.get('passed', True):
                    try:
                        from audit_auto_correction import generate_correction_task, record_correction_task
                        task = generate_correction_task(
                            audit_result,
                            session_id=os.environ.get('LATTI_SESSION_ID'),
                        )
                        if task:
                            record_correction_task(task)
                    except Exception:
                        pass  # Fail silent on auto-correction generation
        except Exception:
            # Fail silent — must never break the model loop
            pass

    def _emit_cost_ledger(self, result: AgentRunResult) -> None:
        """Append a cost-ledger entry to Latti's cost-ledger.jsonl.

        Opt-in via LATTI_COST_LEDGER env var pointing to the ledger file,
        or default location ~/.latti/memory/cost-ledger.jsonl.
        Emission is best-effort; failures are swallowed to avoid disrupting runs.
        """
        import os
        import json
        import time
        from pathlib import Path

        try:
            # Opt-in: default to ~/.latti/memory/cost-ledger.jsonl if dir exists
            default_ledger = Path.home() / '.latti' / 'memory' / 'cost-ledger.jsonl'
            ledger_path = os.environ.get('LATTI_COST_LEDGER')
            if ledger_path:
                ledger = Path(ledger_path)
            elif default_ledger.parent.is_dir():
                ledger = default_ledger
            else:
                return  # No latti install → no-op

            usage = result.usage
            entry = {
                'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'model': getattr(self.model_config, 'model', 'unknown'),
                'tokens_in': int(getattr(usage, 'input_tokens', 0) or 0),
                'tokens_out': int(getattr(usage, 'output_tokens', 0) or 0),
                'cache_creation': int(getattr(usage, 'cache_creation_input_tokens', 0) or 0),
                'cache_read': int(getattr(usage, 'cache_read_input_tokens', 0) or 0),
                'cost_usd': float(getattr(result, 'total_cost_usd', 0.0) or 0.0),
                'session_id': os.environ.get('LATTI_SESSION_ID', 'unknown'),
            }
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry, separators=(',', ':')) + '\n')
        except Exception:
            # Best-effort logging: never crash the run on ledger failure
            pass

    def _emit_session_turn(self, result: AgentRunResult) -> None:
        """Append a turn record to Latti's session_work.md via session_context.py.

        Runs only when a Latti install is detected (~/.latti/last_session exists).
        Best-effort: failures are swallowed to avoid disrupting runs.
        """
        import sys
        from pathlib import Path

        try:
            latti_home = Path.home() / '.latti'
            if not (latti_home / 'last_session').is_file():
                return  # Not running under Latti → no-op

            if str(latti_home) not in sys.path:
                sys.path.insert(0, str(latti_home))
            from session_context import append_turn  # type: ignore[import-not-found]

            # Summarize this turn concisely
            turn_num = int(getattr(result, 'turns', 0) or 0)
            tool_calls = int(getattr(result, 'tool_calls', 0) or 0)
            stop_reason = getattr(result, 'stop_reason', None) or 'ok'
            final_output = getattr(result, 'final_output', '') or ''
            # Action: full output (no truncation) with newlines collapsed
            summary = final_output.strip().replace('\n', ' ')
            if not summary:
                summary = f'({tool_calls} tool calls)'
            note = f'turns={turn_num} tools={tool_calls}'
            # Use cumulative turn counter as the visible turn number so each run
            # is its own entry even if internal turns==0 on fast paths
            if not hasattr(self, '_latti_turn_counter'):
                self._latti_turn_counter = 0
            self._latti_turn_counter += 1
            append_turn(self._latti_turn_counter, summary, stop_reason, note)
        except Exception:
            pass

    def _refresh_runtime_views_for_tool_result(
        self,
        tool_name: str,
        tool_result: ToolExecutionResult,
    ) -> None:
        if not tool_result.ok:
            return
        cwd_update = tool_result.metadata.get('cwd_update')
        if isinstance(cwd_update, str) and cwd_update:
            self._apply_runtime_cwd_update(Path(cwd_update))
        refresh_tool_names = {
            'update_plan',
            'plan_clear',
            'task_create',
            'task_update',
            'task_start',
            'task_complete',
            'task_block',
            'task_cancel',
            'todo_write',
            'search_activate_provider',
            'remote_connect',
            'remote_disconnect',
            'account_login',
            'account_logout',
            'config_set',
            'ask_user_question',
            'team_create',
            'team_delete',
            'send_message',
            'workflow_run',
            'remote_trigger',
            'worktree_enter',
            'worktree_exit',
        }
        if tool_name not in refresh_tool_names:
            return
        clear_context_caches()
        additional_dirs = tuple(
            str(path) for path in self.runtime_config.additional_working_directories
        )
        if tool_name.startswith('remote_'):
            self.remote_runtime = RemoteRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'remote_trigger':
            self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('search_'):
            self.search_runtime = SearchRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('account_'):
            self.account_runtime = AccountRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'ask_user_question':
            self.ask_user_runtime = AskUserRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name == 'config_set':
            self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('task_') or tool_name == 'todo_write':
            self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('plan_') or tool_name == 'update_plan':
            self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        if tool_name.startswith('team_') or tool_name == 'send_message':
            self.team_runtime = TeamRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('workflow_'):
            self.workflow_runtime = WorkflowRuntime.from_workspace(
                self.runtime_config.cwd,
                additional_working_directories=additional_dirs,
            )
        if tool_name.startswith('worktree_'):
            self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.tool_context = replace(
            self.tool_context,
            tool_registry=self.tool_registry,
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )
        self._sm_runner = None

    def _apply_runtime_cwd_update(self, new_cwd: Path) -> None:
        resolved_cwd = new_cwd.resolve()
        if resolved_cwd == self.runtime_config.cwd.resolve():
            return
        self.runtime_config = replace(self.runtime_config, cwd=resolved_cwd)
        clear_context_caches()
        additional_dirs = tuple(
            str(path) for path in self.runtime_config.additional_working_directories
        )
        self.plugin_runtime = PluginRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.hook_policy_runtime = HookPolicyRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.mcp_runtime = MCPRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.remote_runtime = RemoteRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.remote_trigger_runtime = RemoteTriggerRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.search_runtime = SearchRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.account_runtime = AccountRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.ask_user_runtime = AskUserRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.config_runtime = ConfigRuntime.from_workspace(self.runtime_config.cwd)
        self.lsp_runtime = LSPRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.task_runtime = TaskRuntime.from_workspace(self.runtime_config.cwd)
        self.plan_runtime = PlanRuntime.from_workspace(self.runtime_config.cwd)
        self.team_runtime = TeamRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.workflow_runtime = WorkflowRuntime.from_workspace(
            self.runtime_config.cwd,
            additional_dirs,
        )
        self.worktree_runtime = WorktreeRuntime.from_workspace(self.runtime_config.cwd)
        self.runtime_config = self._apply_hook_policy_budget_overrides(self.runtime_config)
        registry = dict(default_tool_registry())
        if self.plugin_runtime is not None:
            alias_tools = self.plugin_runtime.register_tool_aliases(registry)
            if alias_tools:
                registry = {**registry, **alias_tools}
            virtual_tools = self.plugin_runtime.register_virtual_tools(registry)
            if virtual_tools:
                registry = {**registry, **virtual_tools}
        self.tool_registry = registry
        self.tool_context = build_tool_context(
            self.runtime_config,
            tool_registry=self.tool_registry,
            extra_env=(
                self.hook_policy_runtime.safe_env()
                if self.hook_policy_runtime is not None
                else None
            ),
            search_runtime=self.search_runtime,
            account_runtime=self.account_runtime,
            ask_user_runtime=self.ask_user_runtime,
            config_runtime=self.config_runtime,
            lsp_runtime=self.lsp_runtime,
            mcp_runtime=self.mcp_runtime,
            remote_runtime=self.remote_runtime,
            remote_trigger_runtime=self.remote_trigger_runtime,
            plan_runtime=self.plan_runtime,
            task_runtime=self.task_runtime,
            team_runtime=self.team_runtime,
            workflow_runtime=self.workflow_runtime,
            worktree_runtime=self.worktree_runtime,
        )
        self._sm_runner = None

    def _apply_plugin_before_prompt_hooks(self, prompt: str) -> str:
        if self.plugin_runtime is None:
            return prompt
        injections = self.plugin_runtime.before_prompt_injections()
        state_reminder = self.plugin_runtime.runtime_state_reminder()
        if not injections and not state_reminder:
            return prompt
        lines = ['<system-reminder>', 'Plugin before-prompt hooks:']
        lines.extend(f'- {entry}' for entry in injections)
        if state_reminder:
            lines.extend(['', state_reminder])
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _apply_plugin_resume_hooks(
        self,
        prompt: str,
        *,
        resumed: bool,
    ) -> str:
        if not resumed or self.plugin_runtime is None:
            return prompt
        injections = self.plugin_runtime.on_resume_injections()
        if not injections:
            return prompt
        lines = ['<system-reminder>', 'Plugin resume hooks:']
        lines.extend(f'- {entry}' for entry in injections)
        lines.extend(['</system-reminder>', '', prompt])
        return '\n'.join(lines)

    def _render_plugin_persist_message(
        self,
        messages: tuple[str, ...],
    ) -> str:
        lines = ['<system-reminder>', 'Plugin persist hooks:']
        lines.extend(f'- {entry}' for entry in messages)
        lines.extend(
            [
                '',
                'This session state was persisted with plugin lifecycle guidance.',
                '</system-reminder>',
            ]
        )
        return '\n'.join(lines)

    def _append_plugin_after_turn_events(
        self,
        result: AgentRunResult,
        *,
        prompt: str,
        turn_index: int,
    ) -> AgentRunResult:
        if self.plugin_runtime is None:
            return result
        injections = self.plugin_runtime.after_turn_injections()
        if not injections:
            return result
        appended = list(result.events)
        for entry in injections:
            appended.append(
                {
                    'type': 'plugin_after_turn',
                    'turn_index': turn_index,
                    'message': entry,
                    'prompt_preview': self._preview_text(prompt, 120),
                    'stop_reason': result.stop_reason,
                }
            )
        return replace(result, events=tuple(appended))

    def _append_runtime_after_turn_events(
        self,
        result: AgentRunResult,
        *,
        prompt: str,
        turn_index: int,
    ) -> AgentRunResult:
        updated = self._append_plugin_after_turn_events(
            result,
            prompt=prompt,
            turn_index=turn_index,
        )
        if self.hook_policy_runtime is None:
            return updated
        injections = self.hook_policy_runtime.after_turn_messages()
        if not injections:
            return updated
        appended = list(updated.events)
        for entry in injections:
            appended.append(
                {
                    'type': 'hook_policy_after_turn',
                    'turn_index': turn_index,
                    'message': entry,
                    'prompt_preview': self._preview_text(prompt, 120),
                    'stop_reason': updated.stop_reason,
                    'trusted': self.hook_policy_runtime.is_trusted(),
                }
            )
        return replace(updated, events=tuple(appended))
    
    def _record_scar(self, result: AgentRunResult) -> None:
        """Record the outcome of this session as a scar for future learning.
        
        A scar captures: what problem was solved, which model was used,
        what the outcome was, and what lesson to apply next time.
        """
        if self.scar_router is None or not self.last_session:
            return
        
        try:
            # Extract the problem description from the first user message
            problem_description = ''
            for msg in self.last_session.messages:
                if getattr(msg, 'role', None) == 'user':
                    problem_description = getattr(msg, 'content', '') or ''
                    break
            
            if not problem_description:
                return
            
            # Determine outcome using a richer eval signal.
            # "end_turn" alone is too naive — the model could end_turn after
            # producing garbage. We score on multiple signals:
            #   - Hard failures: budget_exceeded, backend_error, max_turns,
            #     prompt_too_long, empty_responses → failure
            #   - Produced output + used tools → success
            #   - Produced output, no tools → partial (may have just chatted)
            #   - No output → failure
            stop = result.stop_reason or ''
            final_output = getattr(result, 'final_output', '') or ''
            tool_calls = int(getattr(result, 'tool_calls', 0) or 0)

            hard_failures = {
                'budget_exceeded', 'backend_error', 'max_turns',
                'prompt_too_long', 'empty_responses', 'resume_load_error',
            }
            if stop in hard_failures:
                outcome = 'failure'
            elif not final_output.strip():
                outcome = 'failure'
            elif stop == 'end_turn' and tool_calls > 0:
                outcome = 'success'
            elif stop == 'end_turn' and len(final_output.strip()) > 100:
                # Produced a substantive response even without tool calls
                outcome = 'success'
            elif stop == 'end_turn':
                outcome = 'partial'
            else:
                outcome = 'partial'
            
            # Record the scar
            self.scar_router.record_outcome(
                problem_description=problem_description[:200],  # Truncate for storage
                model_used=self.model_config.model,
                cost=result.total_cost_usd,
                outcome=outcome,
                session_id=self.active_session_id or 'unknown',
                reasoning_tokens=result.usage.reasoning_tokens or 0,
            )
        except Exception:
            # Best-effort; don't disrupt the session if scar recording fails
            pass


def _optional_policy_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_policy_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
