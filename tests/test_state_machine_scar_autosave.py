"""Tests for auto-save of scars on contract-violation events.

When agent_runtime's typed dispatch produces an Observation with either a
constitutional-wall block or a validator-blocking_validations payload, the
runtime should persist a typed MemoryRecord(kind='scar') to LattiMemoryStore
so the next instance recognizes the pattern.

Failures of the scar-save itself MUST be silent — the dispatch path is
load-bearing and a memory-store error must not break tool execution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent_runtime import LocalCodingAgent
from src.agent_state_machine import Action, Observation
from src.agent_types import (
    AgentPermissions, AgentRuntimeConfig, ModelConfig, ModelPricing,
    ToolExecutionResult,
)
from src.state_machine_memory import LattiMemoryStore


def _make_agent(tmp_path):
    return LocalCodingAgent(
        model_config=ModelConfig(
            model='unused', api_key='x', base_url='http://0/',
            pricing=ModelPricing(),
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            permissions=AgentPermissions(allow_file_write=True, allow_shell_commands=False),
        ),
    )


class _ToolCallStub:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args
        self.id = f'tc_{name}'


def _redirect_memory_to_tmp(agent, tmp_path: Path) -> Path:
    """Replace the agent's memory store with one rooted at tmp_path so we don't
    pollute ~/.latti/memory/ during tests."""
    mem_dir = tmp_path / 'memory'
    agent._sm_memory = LattiMemoryStore(mem_dir)
    return mem_dir


# ---- Wall-block scars ------------------------------------------------------

def test_wall_block_persists_scar(tmp_path, monkeypatch):
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    # rm -rf /etc — should hit never_delete_production_data wall
    result = agent._dispatch_via_state_machine(
        _ToolCallStub('bash', {'cmd': 'rm -rf /etc/passwd'}),
    )
    assert result.ok is False  # wall blocked

    # Scar file should now exist
    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) >= 1
    body = scar_files[0].read_text()
    assert 'never_delete_production_data' in body
    assert 'WALL:' in body or 'wall' in body.lower()


def test_wall_block_scar_includes_session_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    # Trigger a wall to force scar creation
    agent._dispatch_via_state_machine(
        _ToolCallStub('bash', {'cmd': 'git push -f origin main'}),
    )

    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) >= 1
    body = scar_files[0].read_text()
    # Frontmatter contains either session id or sm_unknown placeholder
    assert 'originSessionId:' in body or 'id: mem_' in body


# ---- Validator-block scars -------------------------------------------------

def test_validator_block_persists_scar(tmp_path, monkeypatch):
    """A misbehaving Operator triggers ObservationShapeValidator → scar."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    # Inject a misbehaving operator into the runner
    from src.state_machine_runner import StateMachineRunner
    from src.state_machine_validators import ObservationShapeValidator

    class MisidentifyingOp:
        @property
        def kind(self):
            return 'tool_call'

        def can_handle(self, action):
            return action.kind == 'tool_call'

        def execute(self, action, state):
            # Wrong action_id → ObservationShapeValidator blocks
            return Observation(
                action_id='wrong_id', kind='success',
                payload={'tool_name': 'read_file', 'ok': True, 'content': 'x'},
            )

    agent._sm_runner = StateMachineRunner(
        operators=[MisidentifyingOp()],
        decision_log_path=tmp_path / 'log.jsonl',
        validators=[ObservationShapeValidator()],
    )

    result = agent._dispatch_via_state_machine(
        _ToolCallStub('read_file', {'path': '/tmp/x'}),
    )
    assert result.ok is False  # validator blocked

    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) >= 1
    body = scar_files[0].read_text()
    assert 'FAILED CHECKS' in body
    assert 'action_id_continuity' in body or 'validator' in body.lower()


# ---- No scar on clean dispatches -------------------------------------------

def test_no_scar_saved_on_successful_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    target = tmp_path / 'clean.txt'
    target.write_text('content', encoding='utf-8')
    result = agent._dispatch_via_state_machine(
        _ToolCallStub('read_file', {'path': 'clean.txt'}),
    )
    assert result.ok is True

    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) == 0


def test_no_scar_on_unhandled_tool(tmp_path, monkeypatch):
    """Unknown tool → error observation, but NOT a wall/validator block.
    Should not persist a scar (the model picked a tool that doesn't exist;
    that's an LLM error, not a contract violation)."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    result = agent._dispatch_via_state_machine(
        _ToolCallStub('totally_made_up_tool', {}),
    )
    assert result.ok is False
    scar_files = list(mem_dir.glob('scar_*.md'))
    assert len(scar_files) == 0


# ---- Failure isolation -----------------------------------------------------

def test_repeated_wall_block_dedupes_to_one_scar_file(tmp_path, monkeypatch):
    """A misbehaving model attempting the same wall-blocked action repeatedly
    should not pollute memory with N copies of the same scar. Wall scars
    use a deterministic filename so repeats overwrite, leaving one file."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    for _ in range(5):
        agent._dispatch_via_state_machine(
            _ToolCallStub('bash', {'cmd': 'rm -rf /etc/passwd'}),
        )

    scar_files = list(mem_dir.glob('scar_wall_*.md'))
    assert len(scar_files) == 1, f'expected 1 wall scar, got {len(scar_files)}'


def test_distinct_walls_produce_distinct_scar_files(tmp_path, monkeypatch):
    """Different walls hit by different actions should each get their own scar."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    agent._dispatch_via_state_machine(_ToolCallStub('bash', {'cmd': 'rm -rf /etc'}))
    agent._dispatch_via_state_machine(_ToolCallStub('bash', {'cmd': 'git push -f origin main'}))

    scar_files = sorted(mem_dir.glob('scar_wall_*.md'))
    assert len(scar_files) == 2
    names = {p.name for p in scar_files}
    assert any('never_delete_production_data' in n for n in names)
    assert any('never_force_push_main' in n for n in names)


def test_validator_block_dedup_by_check_signature(tmp_path, monkeypatch):
    """Same validator failure pattern (same failed check names) → same scar
    file, overwritten on repeat. Different patterns → different files."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)
    mem_dir = _redirect_memory_to_tmp(agent, tmp_path)

    from src.state_machine_runner import StateMachineRunner
    from src.state_machine_validators import ObservationShapeValidator

    class WrongIdOp:
        @property
        def kind(self): return 'tool_call'
        def can_handle(self, action): return action.kind == 'tool_call'
        def execute(self, action, state):
            return Observation(
                action_id='wrong_id', kind='success',
                payload={'tool_name': 'read_file', 'ok': True, 'content': 'x'},
            )

    agent._sm_runner = StateMachineRunner(
        operators=[WrongIdOp()],
        decision_log_path=tmp_path / 'log.jsonl',
        validators=[ObservationShapeValidator()],
    )

    # Same failure repeated 3 times → 1 scar file (signature: action_id_continuity)
    for _ in range(3):
        agent._dispatch_via_state_machine(_ToolCallStub('read_file', {'path': '/tmp/x'}))

    scar_files = list(mem_dir.glob('scar_validator_block_*.md'))
    assert len(scar_files) == 1
    assert 'action_id_continuity' in scar_files[0].name


def test_memory_store_failure_does_not_break_dispatch(tmp_path, monkeypatch):
    """If LattiMemoryStore.save raises, the dispatch must still return
    a normal ToolExecutionResult — never re-raise."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    agent = _make_agent(tmp_path)

    class BoomStore:
        def save(self, *a, **kw):
            raise RuntimeError('disk full simulation')

    agent._sm_memory = BoomStore()

    # Trigger a wall block — would normally save a scar
    result = agent._dispatch_via_state_machine(
        _ToolCallStub('bash', {'cmd': 'rm -rf /etc'}),
    )
    # Despite scar-save failure, dispatch returns normally
    assert isinstance(result, ToolExecutionResult)
    assert result.ok is False
    assert 'never_delete_production_data' in result.content
