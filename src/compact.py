"""Conversation compaction service.

Mirrors the npm ``src/services/compact/compact.ts`` and
``src/services/compact/prompt.ts`` modules.  Provides:

- The 9-section summarisation prompt (``get_compact_prompt``).
- XML-tag formatting/stripping  (``format_compact_summary``).
- The post-compact user summary message builder
  (``get_compact_user_summary_message``).
- The core ``compact_conversation`` entry point that an
  ``/compact`` slash command or auto-compact subsystem can call.
- PTL retry loop: drops oldest API-round groups when the compact
  request itself hits prompt-too-long (up to ``MAX_PTL_RETRIES``).
- Circuit-breaker tracking for consecutive failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .agent_context_usage import estimate_tokens
from .agent_types import UsageStats
from .agent_session import AgentMessage

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTOCOMPACT_BUFFER_TOKENS = 13_000
"""How many tokens to reserve below the effective context window before
auto-compact fires (same as the npm ``AUTOCOMPACT_BUFFER_TOKENS``)."""

ERROR_NOT_ENOUGH_MESSAGES = 'Not enough messages to compact.'
ERROR_INCOMPLETE_RESPONSE = (
    'The summary response was incomplete.  '
    'The conversation was not compacted.'
)
ERROR_USER_ABORT = 'Compaction canceled.'
ERROR_PROMPT_TOO_LONG = (
    'The compact request itself was too long even after retry truncation.'
)

MAX_COMPACT_FAILURES = 3
"""Circuit-breaker – stop retrying auto-compact after this many consecutive
failures (mirrors the npm implementation)."""

MAX_PTL_RETRIES = 3
"""Maximum number of prompt-too-long retry attempts during compaction."""

PTL_RETRY_MARKER = '[compact_ptl_retry_marker]'
"""Synthetic user message prepended when dropping the first API-round group
leaves an assistant message at position 0."""

# ---------------------------------------------------------------------------
# Prompt construction  (npm ``src/services/compact/prompt.ts``)
# ---------------------------------------------------------------------------

_NO_TOOLS_PREAMBLE = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

_DETAILED_ANALYSIS_INSTRUCTION = """\
Before providing your final summary, wrap your analysis in <analysis> tags to \
organize your thoughts and ensure you've covered all necessary points. In your \
analysis process:

1. Chronologically analyze each message and section of the conversation. \
For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, \
especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each \
required element thoroughly."""

_BASE_COMPACT_PROMPT = f"""\
Your task is to create a detailed summary of the conversation so far, paying \
close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, \
and architectural decisions that would be essential for continuing development \
work without losing context.

{_DETAILED_ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests \
and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, \
and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, \
modified, or created. Pay special attention to the most recent messages and \
include full code snippets where applicable and include a summary of why this \
file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. \
Pay special attention to specific user feedback that you received, especially if \
the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting \
efforts.
6. All user messages: List ALL user messages that are not tool results. These \
are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked \
to work on.
8. Current Work: Describe in detail precisely what was being worked on \
immediately before this summary request, paying special attention to the most \
recent messages from both user and assistant. Include file names and code \
snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to \
the most recent work you were doing. IMPORTANT: ensure that this step is \
DIRECTLY in line with the user's most recent explicit requests, and the task \
you were working on immediately before this summary request. If your last task \
was concluded, then only list next steps if they are explicitly in line with the \
users request. Do not start on tangential requests or really old requests that \
were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the \
most recent conversation showing exactly what task you were working on and where \
you left off. This should be verbatim to ensure there's no drift in task \
interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this \
structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included \
context. If so, remember to follow these instructions when creating the above \
summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also \
remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. \
Include file reads verbatim.
</example>
"""

_NO_TOOLS_TRAILER = (
    '\n\nREMINDER: Do NOT call any tools. Respond with plain text only — '
    'an <analysis> block followed by a <summary> block. '
    'Tool calls will be rejected and you will fail the task.'
)


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """Build the full compact prompt, optionally appending user instructions."""
    prompt = _NO_TOOLS_PREAMBLE + _BASE_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f'\n\nAdditional Instructions:\n{custom_instructions}'
    prompt += _NO_TOOLS_TRAILER
    return prompt


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

def format_compact_summary(summary: str) -> str:
    """Strip the ``<analysis>`` scratchpad and unwrap ``<summary>`` tags.

    Mirrors the npm ``formatCompactSummary`` helper.
    """
    formatted = re.sub(r'<analysis>[\s\S]*?</analysis>', '', summary)

    match = re.search(r'<summary>([\s\S]*?)</summary>', formatted)
    if match:
        content = match.group(1).strip()
        formatted = re.sub(
            r'<summary>[\s\S]*?</summary>',
            f'Summary:\n{content}',
            formatted,
        )

    # Collapse runs of blank lines.
    formatted = re.sub(r'\n\n+', '\n\n', formatted)
    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    *,
    suppress_follow_up: bool = False,
    transcript_path: str | None = None,
) -> str:
    """Build the user-facing summary that replaces compacted messages.

    Mirrors the npm ``getCompactUserSummaryMessage`` helper.
    """
    formatted = format_compact_summary(summary)

    base = (
        'This session is being continued from a previous conversation that '
        'ran out of context. The summary below covers the earlier portion '
        f'of the conversation.\n\n{formatted}'
    )

    if transcript_path:
        base += (
            '\n\nIf you need specific details from before compaction '
            '(like exact code snippets, error messages, or content you '
            'generated), read the full transcript at: '
            f'{transcript_path}'
        )

    if suppress_follow_up:
        base += (
            '\nContinue the conversation from where it left off without '
            'asking the user any further questions. Resume directly — do '
            'not acknowledge the summary, do not recap what was happening, '
            'do not preface with "I\'ll continue" or similar. Pick up the '
            'last task as if the break never happened.'
        )

    return base


# ---------------------------------------------------------------------------
# API-round grouping (mirrors npm groupMessagesByApiRound)
# ---------------------------------------------------------------------------

def group_messages_by_api_round(
    messages: list[AgentMessage],
) -> list[list[AgentMessage]]:
    """Group messages at API-round boundaries.

    A new group starts when an assistant message with a different
    ``message_id`` than the previous assistant message is encountered.
    """
    groups: list[list[AgentMessage]] = []
    current: list[AgentMessage] = []
    last_assistant_id: str | None = None

    for msg in messages:
        if (
            msg.role == 'assistant'
            and msg.message_id != last_assistant_id
            and current
        ):
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)
        if msg.role == 'assistant':
            last_assistant_id = msg.message_id

    if current:
        groups.append(current)
    return groups


def truncate_head_for_ptl_retry(
    messages: list[AgentMessage],
    model: str = '',
    token_gap: int | None = None,
) -> list[AgentMessage] | None:
    """Drop oldest API-round groups to free space for the compact request.

    If *token_gap* is provided, drops enough groups to cover that many
    tokens.  Otherwise falls back to dropping ~20% of groups.

    Returns ``None`` if nothing can be dropped without emptying the list.
    """
    # Strip a previous PTL_RETRY_MARKER to prevent stalling
    working = list(messages)
    if (
        working
        and working[0].role == 'user'
        and working[0].content == PTL_RETRY_MARKER
    ):
        working = working[1:]

    groups = group_messages_by_api_round(working)
    if len(groups) < 2:
        return None

    if token_gap is not None and token_gap > 0:
        accumulated = 0
        drop_count = 0
        for group in groups:
            group_tokens = sum(estimate_tokens(m.content, model) for m in group)
            accumulated += group_tokens
            drop_count += 1
            if accumulated >= token_gap:
                break
    else:
        # Fallback: drop ~20% of groups
        drop_count = max(1, len(groups) // 5)

    drop_count = min(drop_count, len(groups) - 1)
    if drop_count < 1:
        return None

    remaining: list[AgentMessage] = []
    for group in groups[drop_count:]:
        remaining.extend(group)

    if not remaining:
        return None

    # If the first remaining message is an assistant message, prepend a
    # synthetic user marker so the API contract is satisfied.
    if remaining[0].role == 'assistant':
        marker = AgentMessage(
            role='user',
            content=PTL_RETRY_MARKER,
            message_id='ptl_retry_marker',
            metadata={'kind': 'ptl_retry_marker', 'is_meta': True},
        )
        remaining = [marker] + remaining

    return remaining


# ---------------------------------------------------------------------------
# Compaction result
# ---------------------------------------------------------------------------

@dataclass
class CompactionResult:
    """Outcome of a ``compact_conversation`` call."""

    boundary_message: AgentMessage
    summary_messages: list[AgentMessage] = field(default_factory=list)
    messages_to_keep: list[AgentMessage] = field(default_factory=list)
    pre_compact_token_count: int = 0
    post_compact_token_count: int = 0
    true_post_compact_token_count: int = 0
    summary_text: str = ''
    usage: UsageStats = field(default_factory=UsageStats)
    error: str | None = None
    ptl_retries: int = 0


# ---------------------------------------------------------------------------
# Core compaction logic
# ---------------------------------------------------------------------------

def _is_prompt_too_long_response(content: str) -> bool:
    """Check if a model response indicates a prompt-too-long error.

    Some models embed the error in the response text rather than raising.
    """
    lower = content.lower()
    return (
        'prompt is too long' in lower
        or 'prompt_too_long' in lower
        or 'context_length_exceeded' in lower
    )


def _call_compact_model(
    agent: 'LocalCodingAgent',
    api_messages: list[dict[str, Any]],
) -> tuple[str | None, 'UsageStats', str | None]:
    """Call the model for compaction, returning (content, usage, error).

    Returns (None, usage, error_string) on failure.
    """
    try:
        turn = agent.client.complete(api_messages, tools=[])
    except Exception as exc:
        error_str = str(exc)
        if 'prompt' in error_str.lower() and 'long' in error_str.lower():
            return None, UsageStats(), 'prompt_too_long'
        return None, UsageStats(), error_str

    raw = turn.content or ''
    if not raw.strip():
        return None, turn.usage, 'empty_response'
    if _is_prompt_too_long_response(raw):
        return None, turn.usage, 'prompt_too_long'
    return raw, turn.usage, None


def compact_conversation(
    agent: 'LocalCodingAgent',
    custom_instructions: str | None = None,
) -> CompactionResult:
    """Perform conversation compaction.

    Tries session-memory-based compaction first (free, no API call),
    then falls back to LLM-backed compaction.

    1. If no custom instructions, try session memory compact.
    2. Otherwise, build the compact prompt (9-section template).
    3. Collect the session messages to summarise.
    4. Send them + the compact prompt to the model.
    5. On prompt-too-long, retry by dropping oldest API-round groups
       (up to ``MAX_PTL_RETRIES`` attempts).
    6. Parse ``<summary>`` from the response.
    7. Replace session messages with:
       boundary marker → summary user message → preserved tail.

    Returns a :class:`CompactionResult` with diagnostics.
    """
    session = agent.last_session
    if session is None or len(session.messages) == 0:
        return CompactionResult(
            boundary_message=_build_boundary('No session to compact.'),
            error=ERROR_NOT_ENOUGH_MESSAGES,
        )

    # --- Try session-memory-based compact first (no API call) ---
    if custom_instructions is None:
        from .session_memory_compact import try_session_memory_compaction

        last_summarized_id = getattr(agent, '_last_summarized_message_id', None)
        sm_result = try_session_memory_compaction(
            messages=list(session.messages),
            model=agent.model_config.model,
            last_summarized_message_id=last_summarized_id,
        )
        if sm_result is not None:
            # Apply the session-memory compaction to the session
            prefix_count = 0
            for msg in session.messages:
                if msg.metadata.get('kind') == 'compact_boundary':
                    prefix_count += 1
                else:
                    break
            session.messages = (
                session.messages[:prefix_count]
                + [sm_result.boundary_message]
                + sm_result.summary_messages
                + sm_result.messages_to_keep
            )
            # Reset the summarized ID
            agent._last_summarized_message_id = None
            return sm_result

    # ---- Determine which messages to compact vs preserve ----
    preserve_count = max(
        getattr(agent.runtime_config, 'compact_preserve_messages', 4), 1
    )

    # Identify the prefix count: previous compaction artifacts at the
    # head of the session that must NOT be re-summarized. We protect
    # both 'compact_boundary' and 'compact_summary' messages — without
    # this, every additional compaction would re-summarize the previous
    # summaries into a single increasingly-blurry one (compound blur,
    # exponential information loss). With this, successive compactions
    # produce a chronological stack of summaries: oldest first, newest
    # last, then anchored mission/correction messages, then verbatim
    # tail. This is the message-layer analog of DeepSeek's HCA layers
    # — heavily compressed history preserved (not re-compressed) when
    # the model revisits.
    _PROTECTED_PREFIX_KINDS = {'compact_boundary', 'compact_summary'}
    prefix_count = 0
    for msg in session.messages:
        if msg.metadata.get('kind') in _PROTECTED_PREFIX_KINDS:
            prefix_count += 1
        else:
            break

    total = len(session.messages)
    tail_count = min(preserve_count, max(total - prefix_count, 0))
    compact_end = total - tail_count

    # 2026-04-27: orphan-tool_result fix (re-applied after refactor reverted).
    # Walk compact_end forward past any leading tool_result messages so the
    # preserved tail never starts with an orphan. Handles 3 shapes:
    # role='tool', role='user' + tool_call_id, role='user' + content[*].type='tool_result'.
    def _msg_is_tool_result(m) -> bool:
        if m.role == 'tool':
            return True
        if m.role == 'user' and m.tool_call_id is not None:
            return True
        if m.role == 'user' and m.blocks:
            for block in m.blocks:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    return True
        return False

    while compact_end < total and _msg_is_tool_result(session.messages[compact_end]):
        compact_end += 1

    # Symmetric pair integrity (atomic tool-pair compaction).
    # The walk above only handles tool_result AT the boundary cut. When
    # a non-tool-result message intervenes — e.g. assistant_tool_use →
    # user (interjection) → tool_result — the walk misses it, the
    # assistant_tool_use folds into the summary, and the tool_result
    # becomes an orphan in the preserved tail (later 400'd by Anthropic).
    # Track open tool_use IDs in candidates and extend compact_end forward
    # by ID match, absorbing intervening messages, until every tool_use
    # in candidates has its tool_result alongside it.
    open_ids = _collect_open_tool_use_ids(session.messages[prefix_count:compact_end])
    while open_ids and compact_end < total:
        m = session.messages[compact_end]
        compact_end += 1
        if m.role == 'assistant' and m.tool_calls:
            for tc in m.tool_calls:
                if isinstance(tc, dict) and isinstance(tc.get('id'), str):
                    open_ids.add(tc['id'])
        elif _msg_is_tool_result(m):
            cid = _tool_call_id_of(m)
            if cid is not None:
                open_ids.discard(cid)

    if compact_end <= prefix_count:
        return CompactionResult(
            boundary_message=_build_boundary('Not enough messages after prefix.'),
            error=ERROR_NOT_ENOUGH_MESSAGES,
        )

    candidates_with_anchors = session.messages[prefix_count:compact_end]
    preserved_tail = list(session.messages[compact_end:])

    # Anchor sinks: messages flagged metadata['anchor']=True are excluded
    # from the summarizer input AND survive the rebuild verbatim. Mission
    # directives, hard user corrections, and load-bearing decisions get
    # the same persistent-attention guarantee that DeepSeek V4's sink
    # logits provide at the transformer layer. Tested by
    # tests/test_compact_anchors.py.
    anchored = [m for m in candidates_with_anchors if _is_anchor(m)]
    candidates = [m for m in candidates_with_anchors if not _is_anchor(m)]

    if not candidates:
        return CompactionResult(
            boundary_message=_build_boundary('Nothing to compact.'),
            error=ERROR_NOT_ENOUGH_MESSAGES,
        )

    # ---- Estimate pre-compact token count ----
    model = agent.model_config.model
    pre_tokens = sum(estimate_tokens(m.content, model) for m in session.messages)

    # ---- Build the compact request ----
    compact_prompt = get_compact_prompt(custom_instructions)

    # ---- PTL retry loop ----
    messages_to_summarize = candidates
    ptl_retries = 0
    total_usage = UsageStats()
    raw_summary: str | None = None

    for attempt in range(MAX_PTL_RETRIES + 1):
        api_messages: list[dict[str, Any]] = []

        for part in session.system_prompt_parts:
            if part.strip():
                api_messages.append({'role': 'system', 'content': part})

        for msg in messages_to_summarize:
            api_messages.append(msg.to_openai_message())

        api_messages.append({'role': 'user', 'content': compact_prompt})

        content, usage, error = _call_compact_model(agent, api_messages)
        total_usage = total_usage + usage

        if error != 'prompt_too_long':
            raw_summary = content
            break

        # PTL error — try truncating oldest API-round groups
        ptl_retries += 1
        if attempt >= MAX_PTL_RETRIES:
            return CompactionResult(
                boundary_message=_build_boundary(
                    f'Compact request was too long after {ptl_retries} retries.'
                ),
                error=ERROR_PROMPT_TOO_LONG,
                usage=total_usage,
                ptl_retries=ptl_retries,
            )

        truncated = truncate_head_for_ptl_retry(
            messages_to_summarize, model=model,
        )
        if truncated is None:
            return CompactionResult(
                boundary_message=_build_boundary(
                    'Cannot truncate further for compact retry.'
                ),
                error=ERROR_PROMPT_TOO_LONG,
                usage=total_usage,
                ptl_retries=ptl_retries,
            )
        messages_to_summarize = truncated

    if raw_summary is None:
        error_msg = error or ERROR_INCOMPLETE_RESPONSE
        return CompactionResult(
            boundary_message=_build_boundary(f'Compaction failed: {error_msg}'),
            error=error_msg,
            usage=total_usage,
            ptl_retries=ptl_retries,
        )

    # ---- Format the summary ----
    summary_text = format_compact_summary(raw_summary)
    user_summary_content = get_compact_user_summary_message(raw_summary)

    # ---- Build post-compact messages ----
    boundary = _build_boundary(
        f'Earlier conversation ({len(candidates)} messages, ~{pre_tokens} tokens) '
        f'was compacted.',
    )

    summary_msg = AgentMessage(
        role='user',
        content=user_summary_content,
        message_id='compact_summary',
        metadata={'kind': 'compact_summary', 'is_compact_summary': True},
    )

    # Replace session messages in-place. Anchors (if any) sit AFTER the
    # boundary+summary and BEFORE the preserved tail, so they read like
    # persistent system reminders that survive every compaction cycle.
    session.messages = (
        session.messages[:prefix_count]
        + [boundary, summary_msg]
        + anchored
        + preserved_tail
    )

    # ---- Post-compact token estimate ----
    post_tokens = sum(estimate_tokens(m.content, model) for m in session.messages)

    return CompactionResult(
        boundary_message=boundary,
        summary_messages=[summary_msg],
        messages_to_keep=preserved_tail,
        pre_compact_token_count=pre_tokens,
        post_compact_token_count=post_tokens,
        true_post_compact_token_count=sum(
            estimate_tokens(m.content, model)
            for m in [boundary, summary_msg] + preserved_tail
        ),
        summary_text=summary_text,
        usage=total_usage,
        ptl_retries=ptl_retries,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_call_id_of(msg: AgentMessage) -> str | None:
    """Best-effort extraction of the tool_call_id from a tool-result message.

    Handles the three persisted shapes:
      - role='tool' with tool_call_id field
      - role='user' with tool_call_id field
      - role='user' with blocks=[{'type':'tool_result','tool_call_id':...}]
    """
    if msg.tool_call_id is not None:
        return msg.tool_call_id
    if msg.role == 'user' and msg.blocks:
        for block in msg.blocks:
            if isinstance(block, dict) and block.get('type') == 'tool_result':
                cid = block.get('tool_call_id') or block.get('tool_use_id')
                if isinstance(cid, str):
                    return cid
    return None


def _collect_open_tool_use_ids(msgs: list[AgentMessage]) -> set[str]:
    """Tool_use ids announced by assistants in `msgs` whose matching
    tool_result is NOT also in `msgs` — i.e. unsatisfied pairs that would
    leave an orphan if the tail were cut here.
    """
    open_ids: set[str] = set()
    for m in msgs:
        if m.role == 'assistant' and m.tool_calls:
            for tc in m.tool_calls:
                if isinstance(tc, dict) and isinstance(tc.get('id'), str):
                    open_ids.add(tc['id'])
        else:
            cid = _tool_call_id_of(m)
            if cid is not None:
                open_ids.discard(cid)
    return open_ids


def _is_anchor(msg: AgentMessage) -> bool:
    """True if a message is marked as an anchor sink (never compacted)."""
    return msg.metadata.get('anchor') is True


def mark_as_anchor(msg: AgentMessage) -> AgentMessage:
    """Return a copy of `msg` with metadata['anchor']=True.

    Use for mission directives, persistent user corrections, and
    load-bearing decisions that must survive every compaction. Anchors
    are excluded from the summarizer input and re-spliced verbatim into
    the post-compact session immediately after the summary.
    """
    new_meta = dict(msg.metadata)
    new_meta['anchor'] = True
    return replace(msg, metadata=new_meta)


def _build_boundary(note: str) -> AgentMessage:
    """Create a compact-boundary system message."""
    return AgentMessage(
        role='user',
        content=f'<system-reminder>\n{note}\n</system-reminder>',
        message_id='compact_boundary',
        metadata={'kind': 'compact_boundary'},
    )
