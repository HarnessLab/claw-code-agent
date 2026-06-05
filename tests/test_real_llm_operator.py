"""Tests for RealLLMOperator — wrapping OpenAICompatClient through the typed loop.

Step 5.6 of the runway in ``~/.latti/STATE_MACHINE.md``: replace the EchoLLMOperator
stub with a real operator that calls a chat-completion client. Mocked unit tests
here; live OpenRouter smoke is run separately.
"""
from __future__ import annotations

import pytest

from src.agent_state_machine import Action, Observation, Operator, State
from src.agent_types import (
    AssistantTurn,
    ModelPricing,
    ToolCall,
    UsageStats,
)
from src.state_machine_operators import RealLLMOperator


class _StubConfig:
    """Duck-typed config with .pricing.estimate_cost_usd."""

    def __init__(self, pricing: ModelPricing | None = None):
        self.pricing = pricing or ModelPricing(
            input_cost_per_million_tokens_usd=1.0,
            output_cost_per_million_tokens_usd=5.0,
        )


class _StubClient:
    """Records the last .complete() call and returns a configurable AssistantTurn."""

    def __init__(self, turn: AssistantTurn, pricing: ModelPricing | None = None):
        self._turn = turn
        self.config = _StubConfig(pricing)
        self.last_call = None

    def complete(self, messages, tools, *, model_override=None):
        self.last_call = {
            'messages': messages,
            'tools': tools,
            'model_override': model_override,
        }
        return self._turn


class _RaisingClient:
    """Always raises from .complete — exercises the operator's error path."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.config = _StubConfig()

    def complete(self, messages, tools, *, model_override=None):
        raise self._exc


@pytest.fixture
def fresh_state():
    return State.fresh(session_id='real_llm_test')


def _make_turn(content: str = 'hi', tool_calls: tuple[ToolCall, ...] = (),
               finish: str = 'stop',
               usage: UsageStats | None = None) -> AssistantTurn:
    return AssistantTurn(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish,
        usage=usage or UsageStats(input_tokens=100, output_tokens=20),
    )


# ---- Protocol -------------------------------------------------------------

def test_real_llm_operator_satisfies_operator_protocol():
    op = RealLLMOperator(_StubClient(_make_turn()))
    assert isinstance(op, Operator)
    assert op.kind == 'llm_call'


def test_can_handle_only_llm_call_with_messages_list():
    op = RealLLMOperator(_StubClient(_make_turn()))
    assert op.can_handle(Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]}))
    assert not op.can_handle(Action(kind='llm_call', payload={}))  # no messages
    assert not op.can_handle(Action(kind='llm_call', payload={'messages': 'string'}))  # wrong type
    assert not op.can_handle(Action(kind='tool_call', payload={'messages': []}))  # wrong kind


# ---- execute happy path ---------------------------------------------------

def test_execute_returns_success_observation_with_content(fresh_state):
    client = _StubClient(_make_turn(content='hello world'))
    op = RealLLMOperator(client)
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'hi'}]})
    obs = op.execute(a, fresh_state)

    assert obs.kind == 'success'
    assert obs.payload['content'] == 'hello world'
    assert obs.payload['finish_reason'] == 'stop'
    assert obs.payload['tool_calls'] == []
    assert obs.tokens == 120  # 100 + 20


def test_execute_calculates_cost_via_pricing(fresh_state):
    # 100 input @ $1/M = $0.0001; 20 output @ $5/M = $0.0001 → total $0.0002
    client = _StubClient(_make_turn())
    op = RealLLMOperator(client)
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    obs = op.execute(a, fresh_state)
    assert abs(obs.cost_usd - 0.0002) < 1e-9


def test_execute_serializes_tool_calls(fresh_state):
    tcs = (
        ToolCall(id='tc1', name='read_file', arguments={'path': '/etc/hosts'}),
        ToolCall(id='tc2', name='write_file', arguments={'path': '/tmp/x', 'content': 'y'}),
    )
    client = _StubClient(_make_turn(content='', tool_calls=tcs, finish='tool_calls'))
    op = RealLLMOperator(client)
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'do things'}]})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'success'
    assert len(obs.payload['tool_calls']) == 2
    assert obs.payload['tool_calls'][0]['name'] == 'read_file'
    assert obs.payload['tool_calls'][0]['arguments']['path'] == '/etc/hosts'
    assert obs.payload['finish_reason'] == 'tool_calls'


# ---- execute error paths --------------------------------------------------

def test_execute_returns_error_when_messages_missing(fresh_state):
    op = RealLLMOperator(_StubClient(_make_turn()))
    a = Action(kind='llm_call', payload={})  # no messages
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'error'
    assert 'messages' in obs.payload['error'].lower()


def test_execute_returns_error_when_messages_empty_list(fresh_state):
    op = RealLLMOperator(_StubClient(_make_turn()))
    a = Action(kind='llm_call', payload={'messages': []})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'error'


def test_execute_returns_error_when_client_raises(fresh_state):
    op = RealLLMOperator(_RaisingClient(RuntimeError('network down')))
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'error'
    assert 'LLM call failed' in obs.payload['error']
    assert 'network down' in obs.payload['error']


# ---- model override forwarding -------------------------------------------

def test_model_override_at_construction_forwards_to_client(fresh_state):
    client = _StubClient(_make_turn())
    op = RealLLMOperator(client, model_override='openrouter/auto')
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    op.execute(a, fresh_state)
    assert client.last_call['model_override'] == 'openrouter/auto'


def test_model_override_in_action_payload_wins_over_constructor(fresh_state):
    client = _StubClient(_make_turn())
    op = RealLLMOperator(client, model_override='constructor-default')
    a = Action(kind='llm_call', payload={
        'messages': [{'role': 'user', 'content': 'x'}],
        'model_override': 'action-specific',
    })
    op.execute(a, fresh_state)
    assert client.last_call['model_override'] == 'action-specific'


def test_tools_forwarded_to_client(fresh_state):
    client = _StubClient(_make_turn())
    op = RealLLMOperator(client)
    fake_tools = [{'type': 'function', 'function': {'name': 'read_file'}}]
    a = Action(kind='llm_call', payload={
        'messages': [{'role': 'user', 'content': 'x'}],
        'tools': fake_tools,
    })
    op.execute(a, fresh_state)
    assert client.last_call['tools'] == fake_tools
