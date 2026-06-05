"""
Integration layer: wire CognitiveOS into the agent runtime.

This module provides adapters to use the Cognitive OS for code generation tasks
while keeping the existing agent runtime intact for other tasks.

Usage:
    from src.cognitive_os_integration import wrap_agent_for_cognitive_os
    
    agent = LocalCodingAgent(...)
    agent = wrap_agent_for_cognitive_os(agent, enable_for_all_tasks=False)
    # Now code-gen tasks automatically use the forge→gauntlet loop
"""

from __future__ import annotations

import json
from typing import Any, Optional
from dataclasses import replace

from .agent_runtime import LocalCodingAgent
from .agent_types import AssistantTurn, StreamEvent, UsageStats
from .cognitive_os import CognitiveOS
from .intent_router import classify, TaskType
from .openai_compat import OpenAICompatClient


class CognitiveOSAgentWrapper:
    """
    Wraps a LocalCodingAgent to use CognitiveOS for code-generation tasks.
    
    Intercepts _query_model calls, classifies the task, and routes code-gen
    tasks through the forge→gauntlet loop while passing other tasks through
    the normal path.
    """

    def __init__(
        self,
        agent: LocalCodingAgent,
        enable_for_all_tasks: bool = False,
        max_cycles: int = 3,
        verbose: bool = False,
    ):
        self.agent = agent
        self.enable_for_all_tasks = enable_for_all_tasks
        self.max_cycles = max_cycles
        self.verbose = verbose
        self._original_query_model = agent._query_model
        
        # Replace the agent's _query_model with our wrapper
        agent._query_model = self._query_model_wrapped

    def _query_model_wrapped(
        self,
        session: Any,
        tool_specs: list[dict[str, object]],
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        """
        Wrapped _query_model that routes through CognitiveOS for code tasks.
        """
        # Extract the last user message to classify the task
        last_user_msg = ""
        for msg in reversed(session.messages):
            if getattr(msg, "role", None) == "user":
                last_user_msg = getattr(msg, "content", "") or ""
                break

        # Classify the task
        manifest = classify(last_user_msg)

        # Decide whether to use CognitiveOS
        use_cognitive_os = (
            self.enable_for_all_tasks
            or manifest.task_type in (
                TaskType.CODE_GEN,
                TaskType.DEBUG,
                TaskType.REFACTOR,
                TaskType.CYCLIC,
                TaskType.CONSTRAINT,
            )
        )

        if not use_cognitive_os:
            # Use the normal path
            return self._original_query_model(session, tool_specs)

        # Use CognitiveOS for code tasks
        if self.verbose:
            print(f"\n[CognitiveOS] Task type: {manifest.task_type.value}")

        return self._query_model_via_cognitive_os(
            session, tool_specs, last_user_msg, manifest
        )

    def _query_model_via_cognitive_os(
        self,
        session: Any,
        tool_specs: list[dict[str, object]],
        prompt: str,
        manifest: Any,
    ) -> tuple[AssistantTurn, tuple[StreamEvent, ...]]:
        """
        Run the prompt through CognitiveOS and convert the result back to
        an AssistantTurn that the agent runtime expects.
        """
        # Create a CognitiveOS instance
        cos = CognitiveOS(
            client=self.agent.client,
            model=self.agent.model_config.model,
            max_cycles=self.max_cycles,
            system_prompt=self._build_system_prompt(session),
            verbose=self.verbose,
        )

        # Run the cognitive loop
        result = cos.run(prompt=prompt)

        if not result.succeeded:
            if self.verbose:
                print(f"[CognitiveOS] All cycles exhausted, falling back to normal path")
            # Fallback to normal path if CognitiveOS fails
            return self._original_query_model(session, tool_specs)

        # Convert the winner to an AssistantTurn
        winner = result.winner
        content = winner.raw_text

        # Extract tool calls if any (for now, assume none from code generation)
        # In a full implementation, we'd parse tool calls from the response
        tool_calls = []

        # Build the AssistantTurn
        turn = AssistantTurn(
            content=content,
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=UsageStats(
                prompt_tokens=0,  # Not tracked by CognitiveOS yet
                completion_tokens=0,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )

        if self.verbose:
            print(f"[CognitiveOS] Winner energy: {winner.total_energy:.3f}")
            print(f"[CognitiveOS] Cycles: {result.cycles}")

        # Return the turn and empty stream events (CognitiveOS is non-streaming)
        return turn, ()

    def _build_system_prompt(self, session: Any) -> str:
        """
        Extract or build a system prompt from the session.
        """
        # Look for a system message in the session
        for msg in session.messages:
            if getattr(msg, "role", None) == "system":
                return getattr(msg, "content", "") or ""
        # Fallback to agent's default system prompt
        return ""


def wrap_agent_for_cognitive_os(
    agent: LocalCodingAgent,
    enable_for_all_tasks: bool = False,
    max_cycles: int = 3,
    verbose: bool = False,
) -> LocalCodingAgent:
    """
    Wrap an agent to use CognitiveOS for code-generation tasks.

    Args:
        agent: The LocalCodingAgent to wrap
        enable_for_all_tasks: If True, use CognitiveOS for all tasks (not just code)
        max_cycles: Maximum forge→gauntlet cycles per task
        verbose: Print CognitiveOS diagnostics

    Returns:
        The same agent, now with CognitiveOS integration
    """
    wrapper = CognitiveOSAgentWrapper(
        agent=agent,
        enable_for_all_tasks=enable_for_all_tasks,
        max_cycles=max_cycles,
        verbose=verbose,
    )
    return agent
