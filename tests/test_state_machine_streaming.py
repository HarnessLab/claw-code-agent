"""Tests for streaming-delta preservation in the flag-on agent_runtime path.

Step 5.7: ToolCallOperator gains an optional ``delta_callback`` that mirrors
streaming deltas to session.append_tool_delta + stream_events when invoked
via _dispatch_via_state_machine with the streaming context. Without context
(unit tests, isolated runners), deltas are still collected in payload.
"""
from __future__ import annotations

from src.agent_state_machine import Action, State
from src.state_machine_operators import ToolCallOperator
from src.state_machine_runner import StateMachineRunner


# ---- ToolCallOperator delta_callback ---------------------------------------

class _StubStreamUpdate:
    def __init__(self, kind: str, content: str = '', stream: str | None = None, result=None):
        self.kind = kind
        self.content = content
        self.stream = stream
        self.result = result


class _StubResult:
    def __init__(self, name='echo', ok=True, content='final', metadata=None):
        self.name = name
        self.ok = ok
        self.content = content
        self.metadata = metadata or {}


def _make_operator_with_streaming(deltas: list[tuple[str, str | None]],
                                   final_result: _StubResult | None = None,
                                   delta_callback=None):
    op = ToolCallOperator(
        tool_registry={'echo': object()},
        tool_context=None,
        delta_callback=delta_callback,
    )
    final = final_result or _StubResult()

    def fake_stream(*_args, **_kwargs):
        for content, stream in deltas:
            yield _StubStreamUpdate('delta', content=content, stream=stream)
        yield _StubStreamUpdate('result', result=final)

    op._execute_tool_streaming = fake_stream
    return op


def test_delta_callback_invoked_for_each_delta():
    received: list[tuple[str, str | None]] = []
    op = _make_operator_with_streaming(
        [('part1 ', 'stdout'), ('part2 ', 'stdout'), ('part3', 'stderr')],
        delta_callback=lambda content, stream, action: received.append((content, stream)),
    )
    a = Action(kind='tool_call', payload={'tool_name': 'echo', 'arguments': {}})
    op.execute(a, State.fresh(session_id='s'))
    assert received == [('part1 ', 'stdout'), ('part2 ', 'stdout'), ('part3', 'stderr')]


def test_delta_callback_none_keeps_segments_in_payload():
    op = _make_operator_with_streaming(
        [('a', None), ('b', None)],
        delta_callback=None,
    )
    a = Action(kind='tool_call', payload={'tool_name': 'echo', 'arguments': {}})
    obs = op.execute(a, State.fresh(session_id='s'))
    # No callback → segments still captured in payload
    assert len(obs.payload['streamed_segments']) == 2
    assert obs.payload['streamed_segments'][0]['content'] == 'a'


def test_delta_callback_exception_does_not_break_execution():
    def boom(content, stream, action):
        raise RuntimeError('callback bug')

    op = _make_operator_with_streaming(
        [('hello', 'stdout')],
        delta_callback=boom,
    )
    a = Action(kind='tool_call', payload={'tool_name': 'echo', 'arguments': {}})
    obs = op.execute(a, State.fresh(session_id='s'))
    # Despite the callback raising, the tool still completed with success
    assert obs.kind == 'success'
    assert obs.payload['ok'] is True


# ---- agent_runtime _dispatch_via_state_machine wiring ----------------------

class _StubSession:
    def __init__(self):
        self.deltas = []
        self.messages = [type('M', (), {'message_id': 'msg_test'})()]

    def append_tool_delta(self, idx, content, metadata=None):
        self.deltas.append({'idx': idx, 'content': content, 'metadata': metadata or {}})


class _StubToolCall:
    def __init__(self, name='echo', args=None):
        self.name = name
        self.arguments = args or {}
        self.id = 'tc_test'


def _make_minimal_agent(tmp_path):
    from src.agent_runtime import LocalCodingAgent
    from src.agent_types import (
        AgentPermissions, AgentRuntimeConfig, ModelConfig, ModelPricing,
    )
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


def test_dispatch_with_streaming_context_mirrors_deltas_to_session(monkeypatch, tmp_path):
    """When _dispatch_via_state_machine is called with session+tool_message_index+stream_events,
    deltas from the operator's stream are mirrored to session.append_tool_delta in real time."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')

    target = tmp_path / 'streamed.txt'
    target.write_text('content for streaming test', encoding='utf-8')

    agent = _make_minimal_agent(tmp_path)

    # Replace the operator's stream with a controlled fake that emits 2 deltas
    from src.state_machine_operators import ToolCallOperator

    # Force-construct the runner so we can patch its operator
    agent._dispatch_via_state_machine(_StubToolCall('read_file', {'path': str(target)}))
    runner = agent._sm_runner
    op = next(o for o in runner.operators if isinstance(o, ToolCallOperator))

    def fake_stream(*_args, **_kwargs):
        yield _StubStreamUpdate('delta', content='chunk1 ', stream='tool')
        yield _StubStreamUpdate('delta', content='chunk2', stream='tool')
        yield _StubStreamUpdate('result', result=_StubResult(name='read_file', ok=True, content='final'))

    op._execute_tool_streaming = fake_stream

    session = _StubSession()
    stream_events: list = []

    result = agent._dispatch_via_state_machine(
        _StubToolCall('read_file', {'path': str(target)}),
        session=session,
        tool_message_index=0,
        stream_events=stream_events,
    )

    # The mirrored deltas should be on the session
    assert len(session.deltas) == 2
    assert session.deltas[0]['content'] == 'chunk1 '
    assert session.deltas[1]['content'] == 'chunk2'

    # And on stream_events with the expected shape
    assert len(stream_events) == 2
    assert stream_events[0]['type'] == 'tool_delta'
    assert stream_events[0]['tool_name'] == 'read_file'
    assert stream_events[0]['delta'] == 'chunk1 '
    assert stream_events[1]['delta'] == 'chunk2'

    assert result.ok is True


def test_dispatch_without_streaming_context_still_works(monkeypatch, tmp_path):
    """No session/tool_message_index/stream_events → deltas batched (legacy
    flag-on behavior). Operator callback is reset to None for clean state."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    target = tmp_path / 'nostream.txt'
    target.write_text('x', encoding='utf-8')

    agent = _make_minimal_agent(tmp_path)
    result = agent._dispatch_via_state_machine(_StubToolCall('read_file', {'path': str(target)}))
    assert result.ok is True

    # Callback should be cleared after dispatch (no leak across calls)
    from src.state_machine_operators import ToolCallOperator
    op = next(o for o in agent._sm_runner.operators if isinstance(o, ToolCallOperator))
    assert op._delta_callback is None


def test_callback_cleared_even_if_dispatch_raises(monkeypatch, tmp_path):
    """The try/finally must clear the callback even on exception so the next
    dispatch isn't poisoned by stale streaming state."""
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')

    target = tmp_path / 'a.txt'
    target.write_text('x', encoding='utf-8')

    agent = _make_minimal_agent(tmp_path)
    # Construct the runner via a benign first call
    agent._dispatch_via_state_machine(_StubToolCall('read_file', {'path': str(target)}))

    # Now make the operator raise
    from src.state_machine_operators import ToolCallOperator
    op = next(o for o in agent._sm_runner.operators if isinstance(o, ToolCallOperator))

    def boom(*args, **kwargs):
        raise RuntimeError('forced')

    op._execute_tool_streaming = boom

    session = _StubSession()
    try:
        agent._dispatch_via_state_machine(
            _StubToolCall('read_file', {'path': str(target)}),
            session=session,
            tool_message_index=0,
            stream_events=[],
        )
    except Exception:
        pass

    # Callback was cleared by the finally block even though the inner code raised.
    assert op._delta_callback is None
