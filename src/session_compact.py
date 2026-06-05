"""Session compaction — shrink an over-context StoredAgentSession in place
instead of discarding it for a forced-fresh start.

Triggered from main.py when a resume target has crossed the context ceiling
but is still inside the cost budget. The old behavior dropped the entire
message history and the user lost every turn of context. The new behavior
preserves the system prompt, prepends a synthetic compaction marker, and
keeps the tail of the conversation (most recent turns) up to target_tokens.

Token estimation uses a 4-chars-per-token heuristic. This is coarse but
adequate for a soft ceiling — the agent's real tokenizer runs server-side
on the next request and will emit a fresh usage number that replaces the
estimate. The heuristic's only job is to pick a cut point that lands the
compacted history comfortably below the model context limit.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from typing import Any

from .session_store import StoredAgentSession


# 4 chars ≈ 1 token. Conservative (real BPE often fits slightly more
# characters per token on English prose, but tool call / JSON content is
# closer to 3-4). Using 4 keeps us on the safe side of the limit.
CHARS_PER_TOKEN_ESTIMATE = 4

# Default target: compact to ~120K tokens which leaves ~70K headroom
# below the 200K model ceiling for the next turn + tool results.
DEFAULT_TARGET_TOKENS = 120_000

# Always preserve at least this many messages from the tail regardless of
# token math. Protects the immediate back-and-forth that the user just
# finished, which is the context they most likely expect to continue.
MIN_TAIL_MESSAGES = 8


def _estimate_tokens(message: dict[str, Any]) -> int:
    """Cheap char-count-based token estimate for a single message dict."""
    try:
        payload = json.dumps(message, ensure_ascii=False)
    except (TypeError, ValueError):
        # Fallback: sum string-like field lengths
        total = 0
        for value in message.values():
            if isinstance(value, str):
                total += len(value)
        return max(1, total // CHARS_PER_TOKEN_ESTIMATE)
    return max(1, len(payload) // CHARS_PER_TOKEN_ESTIMATE)


def _compaction_marker(dropped_count: int, dropped_tokens: int) -> dict[str, Any]:
    """A synthetic user-role message that stands in for the dropped prefix.
    Inserted at the head of the compacted message list so the model sees
    explicit evidence that history exists beyond what's currently visible.
    The user role is used (not system) because system_prompt_parts already
    handles the permanent instructions; this marker is conversational
    context, not a directive.
    """
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    text = (
        f'[compacted at {ts}: {dropped_count} earlier messages '
        f'(~{dropped_tokens:,} tokens) elided to keep context under limit. '
        f'Treat the state before this marker as given; if you need a '
        f'specific earlier turn, ask and it can be restored from the '
        f'scratchpad.]'
    )
    return {'role': 'user', 'content': text}


def compact_stored_session(
    stored: StoredAgentSession,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
) -> tuple[StoredAgentSession, int]:
    """Return a new StoredAgentSession with messages trimmed to fit
    target_tokens, plus the number of messages actually dropped.

    Preserves:
      - system_prompt_parts (lives outside messages)
      - session_id, cost, turn/tool counts (continuity)
      - the MIN_TAIL_MESSAGES most recent messages unconditionally

    Drops from the head of the message list. Prepends a single synthetic
    marker so the model knows compaction happened.

    If the session already fits, returns it unmodified (drop count = 0).
    """
    messages = list(stored.messages)
    if not messages:
        return stored, 0

    # Walk from end, accumulate tokens, cut when limit reached — but always
    # keep at least MIN_TAIL_MESSAGES.
    keep: list[dict[str, Any]] = []
    running = 0
    for msg in reversed(messages):
        tokens = _estimate_tokens(msg)
        if len(keep) >= MIN_TAIL_MESSAGES and running + tokens > target_tokens:
            break
        keep.append(msg)
        running += tokens

    keep.reverse()

    # 2026-04-27: fix for orphan tool_result after in-place compaction.
    # Anthropic's API rejects requests where the first kept message is a
    # `tool_result` without its matching `tool_use` in the prior message.
    # The naive tail-slice above can sever a tool-use / tool-result pair,
    # dropping the tool_use into the compacted prefix and leaving the
    # tool_result orphaned at the head of `keep`. This triggered HTTP 400
    # errors in latti session 439c96ad31ac on 2026-04-26.
    #
    # Three tool_result shapes to detect:
    #   - OpenAI/generic:   role='tool', tool_call_id set
    #   - OpenAI-on-user:   role='user', tool_call_id set
    #   - Anthropic native: role='user', content[*].type='tool_result'
    def _is_tool_result(m: dict[str, Any]) -> bool:
        role = m.get('role')
        if role == 'tool':
            return True
        if role == 'user':
            if m.get('tool_call_id') is not None:
                return True
            content = m.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        return True
        return False

    while keep and _is_tool_result(keep[0]):
        keep.pop(0)

    dropped = len(messages) - len(keep)
    if dropped <= 0:
        return stored, 0

    dropped_tokens = sum(
        _estimate_tokens(m) for m in messages[:dropped]
    )
    marker = _compaction_marker(dropped, dropped_tokens)
    new_messages = [marker] + keep

    # Usage dict: reset input_tokens estimate so the stale over-limit figure
    # doesn't immediately re-trigger the guard on the next resume check.
    # The server will populate the real number on the next completion.
    new_usage = dict(stored.usage) if stored.usage else {}
    new_usage['input_tokens'] = running
    new_usage['_compacted_at'] = datetime.now(timezone.utc).isoformat(
        timespec='seconds'
    )
    new_usage['_compacted_dropped_messages'] = dropped
    new_usage['_compacted_dropped_tokens_est'] = dropped_tokens

    return dataclasses.replace(
        stored,
        messages=tuple(new_messages),
        usage=new_usage,
    ), dropped
