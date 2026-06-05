"""Memory expansion for Phase 4 of ATM.

Detects when Claude asks for full context and expands summaries on-demand.
Tracks expansion patterns for future optimization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ExpansionRequest:
    """Record of a memory expansion request."""
    timestamp: str
    turn_number: int
    query: str
    expanded_turns: list[int]
    reason: str  # Why expansion was triggered
    tokens_saved: int  # Tokens saved by not including full context initially


@dataclass
class ExpansionTracker:
    """Track expansion patterns across a session."""
    session_id: str
    expansions: list[ExpansionRequest] = field(default_factory=list)
    total_expansions: int = 0
    total_tokens_saved: int = 0
    
    def record_expansion(
        self,
        turn_number: int,
        query: str,
        expanded_turns: list[int],
        reason: str,
        tokens_saved: int,
    ) -> None:
        """Record an expansion request."""
        self.expansions.append(
            ExpansionRequest(
                timestamp=datetime.now(timezone.utc).isoformat(),
                turn_number=turn_number,
                query=query,
                expanded_turns=expanded_turns,
                reason=reason,
                tokens_saved=tokens_saved,
            )
        )
        self.total_expansions += 1
        self.total_tokens_saved += tokens_saved
    
    def get_expansion_rate(self) -> float:
        """Get expansion rate (expansions per turn)."""
        if not self.expansions:
            return 0.0
        max_turn = max(e.turn_number for e in self.expansions)
        return self.total_expansions / max(1, max_turn)


def detect_expansion_request(response_text: str) -> tuple[bool, str]:
    """Detect if Claude is asking for full context.
    
    Looks for patterns like:
    - "Can you show me the full..."
    - "I need to see the complete..."
    - "Can you expand on..."
    - "What was the full code..."
    
    Args:
        response_text: Claude's response text
    
    Returns:
        Tuple of (is_expansion_request, reason)
    """
    patterns = [
        (r'show me the full', 'Asking for full context'),
        (r'show me the complete', 'Asking for complete context'),
        (r'can you expand', 'Asking for expansion'),
        (r'what was the full', 'Asking for full details'),
        (r'i need to see', 'Needs to see full context'),
        (r'can you provide the full', 'Asking for full provision'),
        (r'show me all the', 'Asking for all details'),
        (r'what was the entire', 'Asking for entire context'),
    ]
    
    response_lower = response_text.lower()
    for pattern, reason in patterns:
        if re.search(pattern, response_lower):
            return True, reason
    
    return False, ""


def extract_turn_references(response_text: str) -> list[int]:
    """Extract turn numbers referenced in response.
    
    Looks for patterns like:
    - "turn 42"
    - "on turn 42"
    - "turns 40-45"
    - "the 42nd turn"
    
    Args:
        response_text: Claude's response text
    
    Returns:
        List of turn numbers referenced
    """
    turns = set()
    
    # Pattern: "turn 42" or "on turn 42"
    for match in re.finditer(r'turn\s+(\d+)', response_text, re.IGNORECASE):
        turns.add(int(match.group(1)))
    
    # Pattern: "turns 40-45"
    for match in re.finditer(r'turns\s+(\d+)\s*-\s*(\d+)', response_text, re.IGNORECASE):
        start, end = int(match.group(1)), int(match.group(2))
        turns.update(range(start, end + 1))
    
    # Pattern: "the 42nd turn"
    for match in re.finditer(r'the\s+(\d+)(?:st|nd|rd|th)\s+turn', response_text, re.IGNORECASE):
        turns.add(int(match.group(1)))
    
    return sorted(list(turns))


def should_expand_memory(
    response_text: str,
    expansion_tracker: ExpansionTracker,
    max_expansions_per_session: int = 5,
) -> bool:
    """Decide whether to expand memory based on response.
    
    Prevents expansion explosion by limiting expansions per session.
    
    Args:
        response_text: Claude's response
        expansion_tracker: Tracker of previous expansions
        max_expansions_per_session: Maximum expansions allowed
    
    Returns:
        True if should expand, False otherwise
    """
    is_request, _ = detect_expansion_request(response_text)
    
    if not is_request:
        return False
    
    # Limit expansions to prevent explosion
    if expansion_tracker.total_expansions >= max_expansions_per_session:
        return False
    
    return True


def format_expansion_report(tracker: ExpansionTracker) -> str:
    """Format expansion statistics for logging.
    
    Example:
        "Expansions: 2 total | 1.2K tokens saved | 0.05 expansions/turn"
    """
    expansion_rate = tracker.get_expansion_rate()
    return (
        f"Expansions: {tracker.total_expansions} total | "
        f"{tracker.total_tokens_saved:,} tokens saved | "
        f"{expansion_rate:.2f} expansions/turn"
    )


def estimate_expansion_cost(
    expanded_turns: list[int],
    full_messages: dict[int, dict[str, Any]],
) -> int:
    """Estimate tokens needed to expand summaries to full messages.
    
    Args:
        expanded_turns: Turn numbers to expand
        full_messages: Map of turn_number -> full message dict
    
    Returns:
        Estimated tokens needed
    """
    total_tokens = 0
    for turn_num in expanded_turns:
        if turn_num in full_messages:
            msg = full_messages[turn_num]
            # Rough estimate: 4 chars per token
            total_tokens += len(str(msg)) // 4
    
    return total_tokens


def should_cache_expansion(
    turn_number: int,
    expansion_tracker: ExpansionTracker,
) -> bool:
    """Decide if an expansion should be cached for future use.
    
    Cache expansions that happen frequently (pattern learning).
    
    Args:
        turn_number: Current turn number
        expansion_tracker: Tracker of previous expansions
    
    Returns:
        True if should cache, False otherwise
    """
    # Count how many times this turn has been expanded
    expansion_count = sum(
        1 for e in expansion_tracker.expansions
        if turn_number in e.expanded_turns
    )
    
    # Cache if expanded more than once
    return expansion_count > 1
