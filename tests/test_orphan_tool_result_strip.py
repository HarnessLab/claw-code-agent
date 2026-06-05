"""Strip orphan tool_result messages before they reach the provider.

Anthropic's API requires every tool_result/tool_use_id block to follow a
matching tool_use in the previous assistant message. After auto-compaction
on long Latti sessions, the assistant message that announced a tool_use
can be dropped while the tool_result it produced is kept — leaving an
orphan tool_result. Resuming such a session sends a payload whose
`messages[0]` is the orphan, and the provider returns:

  HTTP 400  invalid_request_error
  messages.0.content.0: unexpected `tool_use_id` found in `tool_result`
  blocks: <id>. Each `tool_result` block must have a corresponding
  `tool_use` block in the previous message.

Reproduced live in session 7c77bcb2dd394 (2026-05-03).

Fix: walk the messages on the way out, drop role=tool entries whose
tool_call_id was never announced by a prior assistant message.
"""
from __future__ import annotations

from src.agent_session import AgentMessage, AgentSessionState


def _build(messages):
    state = AgentSessionState(system_prompt_parts=())
    state.messages = [AgentMessage(role=m['role'], **{k: v for k, v in m.items() if k != 'role'}) for m in messages]
    return state


def test_normal_pair_is_kept():
    state = _build([
        {'role': 'user', 'content': 'hi'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': ({'id': 'toolu_1', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{}'}},),
        },
        {'role': 'tool', 'content': 'ok', 'tool_call_id': 'toolu_1'},
    ])
    out = state.to_openai_messages()
    assert len(out) == 3
    assert out[2]['role'] == 'tool'
    assert out[2]['tool_call_id'] == 'toolu_1'


def test_orphan_tool_result_is_stripped():
    # The exact shape that produced HTTP 400 in session 7c77bcb2dd394.
    state = _build([
        {'role': 'tool', 'content': 'orphan output', 'tool_call_id': 'toolu_bdrk_orphan'},
        {'role': 'assistant', 'content': 'I finished'},
    ])
    out = state.to_openai_messages()
    roles = [m['role'] for m in out]
    assert 'tool' not in roles, f'orphan tool_result should be stripped, got: {roles}'
    assert len(out) == 1
    assert out[0]['role'] == 'assistant'


def test_multiple_orphans_all_stripped():
    state = _build([
        {'role': 'tool', 'content': 'a', 'tool_call_id': 'toolu_a'},
        {'role': 'tool', 'content': 'b', 'tool_call_id': 'toolu_b'},
        {'role': 'user', 'content': 'continue'},
    ])
    out = state.to_openai_messages()
    assert [m['role'] for m in out] == ['user']


def test_valid_pair_kept_orphan_dropped():
    state = _build([
        {'role': 'tool', 'content': 'orphan', 'tool_call_id': 'toolu_orphan'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': ({'id': 'toolu_real', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}},),
        },
        {'role': 'tool', 'content': 'real output', 'tool_call_id': 'toolu_real'},
    ])
    out = state.to_openai_messages()
    # orphan dropped, valid pair preserved
    tool_msgs = [m for m in out if m['role'] == 'tool']
    assert len(tool_msgs) == 1
    assert tool_msgs[0]['tool_call_id'] == 'toolu_real'


def test_no_messages_returns_empty():
    state = AgentSessionState(system_prompt_parts=())
    assert state.to_openai_messages() == []


def test_session_without_tool_messages_unchanged():
    state = _build([
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'hello'},
        {'role': 'user', 'content': 'bye'},
    ])
    out = state.to_openai_messages()
    assert len(out) == 3
    assert [m['role'] for m in out] == ['user', 'assistant', 'user']
