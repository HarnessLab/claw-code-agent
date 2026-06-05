"""Unbreak agent.run() — _inject_next_priority was referenced but never defined.

Commit 84bc6a7 ("Add response finalization context injection to AgentRuntime")
added a call site at agent_runtime.py:448:

    # Layer 4: Inject next priority before response generation
    # This prevents "what next?" routing by making the next action explicit
    self._inject_next_priority()

…but never defined `_inject_next_priority` on LocalCodingAgent. Every
call to agent.run() raised AttributeError. In production this surfaced
as repeated "Worker exited before returning a result. status=failed
stop_reason=worker_failed" — every chat turn's worker subprocess
crashed on this AttributeError before producing a result file, and the
parent's synthesize_worker_failure_result fired.

This pins the defined-method contract: agent.run() must not raise
AttributeError because of `_inject_next_priority`. The method body is
a no-op for now — the actual injection logic is whatever 84bc6a7's
follow-up commit was meant to ship; the priority here is unblocking
the user's chat loop.

Reproduced live in three consecutive worker logs at
~/V5/claw-code-agent/.port_sessions/background/bg_*.log on 2026-05-03.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    ModelConfig,
)


def _make_agent(tmp_path: Path) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=ModelConfig(
            model='gpt-4o-mini',
            api_key='test-key',
            base_url='http://localhost:0/unused',
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=False,
            ),
        ),
    )


def test_inject_next_priority_is_callable(tmp_path: Path) -> None:
    """The method must exist so agent.run() doesn't AttributeError."""
    agent = _make_agent(tmp_path)
    # Must not raise.
    agent._inject_next_priority()


def test_inject_next_priority_is_a_no_op(tmp_path: Path) -> None:
    """Documented intent today: no-op stub. Returns None.

    A future commit may fill in real logic; until then the contract
    is "callable, returns None, no observable side effects." This
    test pins that minimum so a regression that re-removes the
    method or makes it raise is caught immediately.
    """
    agent = _make_agent(tmp_path)
    result = agent._inject_next_priority()
    assert result is None
