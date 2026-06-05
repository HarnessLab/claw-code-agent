"""Prompt caching integration for Claude API.

Implements Phase 1 of Adaptive Tiered Memory (ATM):
- Wraps system prompts with cache_control directives
- Tracks cache hits/misses in cost ledger
- Provides utilities for cache-aware API calls
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CacheStats:
    """Track cache performance across requests."""
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    regular_input_tokens: int = 0
    
    @property
    def total_input_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens + self.regular_input_tokens
    
    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens that were cache hits."""
        if self.total_input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.total_input_tokens
    
    def cache_savings_usd(self, rate_per_mtok: float = 0.0003) -> float:
        """Estimate USD saved by cache hits (vs full price).
        
        Cache reads cost 90% less than regular input.
        Savings = (regular_rate - cache_rate) * cache_read_tokens
        = regular_rate * 0.9 * cache_read_tokens
        """
        cache_rate = rate_per_mtok * 0.1  # 90% discount
        regular_rate = rate_per_mtok
        savings_per_token = regular_rate - cache_rate
        return (savings_per_token * self.cache_read_tokens) / 1_000_000


def wrap_system_prompt_for_caching(system_prompt: str) -> list[dict[str, Any]]:
    """Convert system prompt string to cacheable block format.
    
    Args:
        system_prompt: The system prompt text
    
    Returns:
        List with single dict containing text + cache_control directive
    
    Example:
        >>> prompt = "You are a helpful assistant."
        >>> blocks = wrap_system_prompt_for_caching(prompt)
        >>> blocks[0]['cache_control']
        {'type': 'ephemeral'}
    """
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"}
        }
    ]


def extract_cache_stats(usage: Any) -> CacheStats:
    """Extract cache statistics from API response usage object.
    
    Args:
        usage: Response.usage object from Claude API
    
    Returns:
        CacheStats with cache_creation, cache_read, and regular tokens
    """
    return CacheStats(
        cache_creation_tokens=int(getattr(usage, 'cache_creation_input_tokens', 0) or 0),
        cache_read_tokens=int(getattr(usage, 'cache_read_input_tokens', 0) or 0),
        regular_input_tokens=int(getattr(usage, 'input_tokens', 0) or 0),
    )


def format_cache_stats_for_logging(stats: CacheStats) -> str:
    """Format cache stats as human-readable string.
    
    Example:
        "cache: 1.2K read (45% hit rate) | 2.1K regular | 0.09 USD saved"
    """
    hit_rate_pct = stats.cache_hit_rate * 100
    savings = stats.cache_savings_usd(rate_per_mtok=0.0003)
    
    return (
        f"cache: {stats.cache_read_tokens:,} read ({hit_rate_pct:.1f}% hit) | "
        f"{stats.regular_input_tokens:,} regular | "
        f"${savings:.4f} saved"
    )
