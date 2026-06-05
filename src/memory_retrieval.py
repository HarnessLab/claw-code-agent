"""Memory retrieval for Phase 3 of ATM.

Implements semantic retrieval with query classification and reranking.
Routes queries to appropriate memory tiers based on type and budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from .session_summary import SessionSummaryIndex, TurnSummary


class QueryType(Enum):
    """Classification of query types for routing."""
    FACTUAL = "factual"  # "What did we do on turn 42?"
    REASONING = "reasoning"  # "Why did we choose this approach?"
    CODE_REVIEW = "code_review"  # "Show me the code we wrote"
    DEBUGGING = "debugging"  # "What went wrong?"
    PLANNING = "planning"  # "What should we do next?"


@dataclass
class RetrievalBudget:
    """Token budget allocation across tiers."""
    total_tokens: int = 50000
    tier1_fraction: float = 0.10  # 10% for cache
    tier2_fraction: float = 0.70  # 70% for summaries
    tier3_fraction: float = 0.20  # 20% for recent
    
    @property
    def tier1_budget(self) -> int:
        return int(self.total_tokens * self.tier1_fraction)
    
    @property
    def tier2_budget(self) -> int:
        return int(self.total_tokens * self.tier2_fraction)
    
    @property
    def tier3_budget(self) -> int:
        return int(self.total_tokens * self.tier3_fraction)


def classify_query(query: str) -> QueryType:
    """Classify query type for routing to appropriate tiers.
    
    Args:
        query: The incoming query/request
    
    Returns:
        QueryType enum value
    """
    query_lower = query.lower()
    
    # Check for reasoning keywords (check first, before planning)
    reason_keywords = ['why', 'reason', 'because', 'explain', 'rationale']
    if any(kw in query_lower for kw in reason_keywords):
        return QueryType.REASONING
    
    # Check for code review keywords
    code_keywords = ['code', 'function', 'class', 'implementation', 'show me', 'review']
    if any(kw in query_lower for kw in code_keywords):
        return QueryType.CODE_REVIEW
    
    # Check for debugging keywords
    debug_keywords = ['error', 'bug', 'fail', 'wrong', 'issue', 'problem', 'debug']
    if any(kw in query_lower for kw in debug_keywords):
        return QueryType.DEBUGGING
    
    # Check for planning keywords
    plan_keywords = ['next', 'plan', 'should', 'approach', 'strategy', 'design']
    if any(kw in query_lower for kw in plan_keywords):
        return QueryType.PLANNING
    
    # Default to factual
    return QueryType.FACTUAL


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.
    
    Args:
        a: First vector
        b: Second vector
    
    Returns:
        Cosine similarity (-1 to 1, typically 0 to 1 for embeddings)
    """
    a_arr = np.array(a)
    b_arr = np.array(b)
    
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


def bm25_score(query: str, text: str) -> float:
    """Simple BM25-like scoring (keyword matching).
    
    Args:
        query: Query text
        text: Document text
    
    Returns:
        Score 0-1 based on keyword overlap
    """
    query_words = set(query.lower().split())
    text_words = set(text.lower().split())
    
    if not query_words or not text_words:
        return 0.0
    
    overlap = len(query_words & text_words)
    return overlap / len(query_words)


def score_summary(
    query_embedding: list[float],
    summary: TurnSummary,
    query_type: QueryType,
    total_turns: int = 1,
) -> float:
    """Score a summary for relevance to a query.

    Combines:
    - Semantic similarity (embedding cosine)
    - Importance score (decisions weighted higher)
    - Recency bias (recent turns weighted higher)
    - Query-type affinity (code reviews prefer recent)

    Args:
        query_embedding: Embedding of the query
        summary: Turn summary to score
        query_type: Type of query (for weighting)
        total_turns: Total number of turns in the session (for recency normalisation)

    Returns:
        Score 0-1
    """
    # Semantic similarity mapped from [-1,1] → [0,1]
    semantic_score = (cosine_similarity(query_embedding, summary.embedding) + 1) / 2

    # Importance score (already 0-1)
    importance = summary.importance_score

    # Recency bias: turn_number / total_turns → 0 (oldest) … 1 (newest)
    recency_score = summary.turn_number / max(1, total_turns - 1) if total_turns > 1 else 1.0

    # Query-type affinity weights
    # CODE_REVIEW / DEBUGGING lean on recency; REASONING leans on semantics
    if query_type in (QueryType.CODE_REVIEW, QueryType.DEBUGGING):
        w_semantic, w_importance, w_recency = 0.4, 0.2, 0.4
    elif query_type == QueryType.REASONING:
        w_semantic, w_importance, w_recency = 0.6, 0.3, 0.1
    elif query_type == QueryType.PLANNING:
        w_semantic, w_importance, w_recency = 0.4, 0.4, 0.2
    else:  # FACTUAL and default
        w_semantic, w_importance, w_recency = 0.5, 0.3, 0.2

    score = (
        w_semantic * semantic_score
        + w_importance * importance
        + w_recency * recency_score
    )

    return min(1.0, max(0.0, score))


def retrieve_context(
    query: str,
    query_embedding: list[float],
    summary_index: SessionSummaryIndex | None,
    recent_messages: list[dict[str, Any]],
    budget: RetrievalBudget = RetrievalBudget(),
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve context within token budget.
    
    Args:
        query: The incoming query
        query_embedding: Embedding of the query
        summary_index: Summary index (Phase 2+)
        recent_messages: Recent full messages (Tier 3)
        budget: Token budget allocation
    
    Returns:
        Tuple of (context_messages, tokens_used)
    """
    query_type = classify_query(query)
    context: list[dict[str, Any]] = []
    tokens_used = 0
    
    # Tier 1: Cache (handled separately in agent_runtime.py)
    # We don't include it here as it's handled by API caching
    
    # Tier 2: Summaries (if available)
    if summary_index and summary_index.summaries:
        tier2_budget = budget.tier2_budget
        
        # Score all summaries, passing total_turns for real recency normalisation
        total_turns = len(summary_index.summaries)
        scores = []
        for i, summary in enumerate(summary_index.summaries):
            score = score_summary(query_embedding, summary, query_type, total_turns=total_turns)
            scores.append((score, i, summary))
        
        # Sort by score descending
        scores.sort(reverse=True, key=lambda x: x[0])
        
        # Greedily add summaries
        for score, idx, summary in scores:
            summary_tokens = summary.tokens_estimate
            if tokens_used + summary_tokens < tier2_budget:
                context.append({
                    'role': 'user',
                    'content': f'[Summary turn {summary.turn_number}] {summary.summary}'
                })
                tokens_used += summary_tokens
            else:
                break
    
    # Tier 3: Recent messages (always include)
    tier3_budget = budget.tier3_budget
    for msg in recent_messages[-5:]:  # Last 5 messages
        msg_tokens = len(str(msg)) // 4  # Rough estimate
        if tokens_used + msg_tokens < tier3_budget:
            context.append(msg)
            tokens_used += msg_tokens
    
    return context, tokens_used


def format_retrieval_report(
    query_type: QueryType,
    context_count: int,
    tokens_used: int,
    budget: RetrievalBudget,
) -> str:
    """Format retrieval statistics for logging.
    
    Example:
        "Retrieved 12 context items (3.2K tokens) for reasoning query"
    """
    return (
        f"Retrieved {context_count} context items ({tokens_used:,} tokens) "
        f"for {query_type.value} query (budget: {budget.total_tokens:,})"
    )
