"""Tests for StreamingLLMOperator wrapping OpenAICompatClient.stream()."""
from __future__ import annotations

import pytest

from src.agent_state_machine import Action, Operator, State
from src.agent_types import ModelPricing, UsageStats
from src.state_machine_operators import StreamingLLMOperator


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _StubConfig:
    def __init__(self, pricing=None):
        self.pricing = pricing or ModelPricing(
            input_cost_per_million_tokens_usd=1.0,
            output_cost_per_million_tokens_usd=5.0,
        )


class _StreamingStubClient:
    def __init__(self, events):
        self._events = events
        self.config = _StubConfig()
        self.last_call = None

    def stream(self, messages, tools, *, model_override=None):
        self.last_call = {'messages': messages, 'tools': tools, 'model_override': model_override}
        for ev in self._events:
            yield ev


@pytest.fixture
def fresh_state():
    return State.fresh(session_id='stream_test')


def test_streaming_llm_satisfies_protocol():
    op = StreamingLLMOperator(_StreamingStubClient([]))
    assert isinstance(op, Operator)
    assert op.kind == 'llm_call'


def test_accumulates_content_deltas(fresh_state):
    events = [
        _Event('content_delta', delta='Hello '),
        _Event('content_delta', delta='world'),
        _Event('message_stop', finish_reason='stop'),
        _Event('usage', usage=UsageStats(input_tokens=10, output_tokens=2)),
    ]
    client = _StreamingStubClient(events)
    op = StreamingLLMOperator(client)
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'hi'}]})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'success'
    assert obs.payload['content'] == 'Hello world'
    assert obs.payload['finish_reason'] == 'stop'


def test_token_callback_fires_per_delta(fresh_state):
    received: list[str] = []
    events = [
        _Event('content_delta', delta='a'),
        _Event('content_delta', delta='b'),
        _Event('content_delta', delta='c'),
        _Event('message_stop', finish_reason='stop'),
    ]
    client = _StreamingStubClient(events)
    op = StreamingLLMOperator(client, token_callback=lambda d, action: received.append(d))
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    op.execute(a, fresh_state)
    assert received == ['a', 'b', 'c']


def test_callback_exception_does_not_break_execution(fresh_state):
    events = [
        _Event('content_delta', delta='x'),
        _Event('message_stop', finish_reason='stop'),
    ]
    op = StreamingLLMOperator(
        _StreamingStubClient(events),
        token_callback=lambda d, a: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'success'
    assert obs.payload['content'] == 'x'


def test_assembles_tool_calls_from_streaming_events(fresh_state):
    events = [
        _Event('tool_call_start', tool_call_id='tc1', tool_name='read_file'),
        _Event('tool_call_delta', delta='{"path":'),
        _Event('tool_call_delta', delta='"/tmp/x"}'),
        _Event('message_stop', finish_reason='tool_calls'),
    ]
    op = StreamingLLMOperator(_StreamingStubClient(events))
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'do it'}]})
    obs = op.execute(a, fresh_state)
    assert len(obs.payload['tool_calls']) == 1
    tc = obs.payload['tool_calls'][0]
    assert tc['name'] == 'read_file'
    assert tc['arguments'] == {'path': '/tmp/x'}


def test_assembles_tool_calls_from_real_tool_call_delta_shape(fresh_state):
    events = [
        _Event('tool_call_delta', tool_call_id='tc1', tool_name='read_file', arguments_delta='{"path":'),
        _Event('tool_call_delta', tool_call_index=0, arguments_delta='"/tmp/y"}'),
        _Event('message_stop', finish_reason='tool_calls'),
    ]
    op = StreamingLLMOperator(_StreamingStubClient(events))
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'do it'}]})
    obs = op.execute(a, fresh_state)
    assert len(obs.payload['tool_calls']) == 1
    tc = obs.payload['tool_calls'][0]
    assert tc['name'] == 'read_file'
    assert tc['arguments'] == {'path': '/tmp/y'}


def test_returns_partial_content_on_stream_failure(fresh_state):
    class BoomClient:
        config = _StubConfig()
        def stream(self, *a, **kw):
            yield _Event('content_delta', delta='partial...')
            raise RuntimeError('connection dropped')

    op = StreamingLLMOperator(BoomClient())
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    obs = op.execute(a, fresh_state)
    assert obs.kind == 'error'
    assert 'connection dropped' in obs.payload['error']
    assert obs.payload['partial_content'] == 'partial...'


def test_error_when_messages_missing(fresh_state):
    op = StreamingLLMOperator(_StreamingStubClient([]))
    obs = op.execute(Action(kind='llm_call', payload={}), fresh_state)
    assert obs.kind == 'error'


def test_malformed_tool_call_json_falls_back_to_raw(fresh_state):
    events = [
        _Event('tool_call_start', tool_call_id='tc1', tool_name='f'),
        _Event('tool_call_delta', delta='{this is not json'),
        _Event('message_stop', finish_reason='tool_calls'),
    ]
    op = StreamingLLMOperator(_StreamingStubClient(events))
    a = Action(kind='llm_call', payload={'messages': [{'role': 'user', 'content': 'x'}]})
    obs = op.execute(a, fresh_state)
    tc = obs.payload['tool_calls'][0]
    assert '_raw' in tc['arguments']
