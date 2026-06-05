"""Live model routing — pick the cheapest model that can handle the task.

The router classifies each turn into a tier (heavy/light/micro) and swaps
the model on the OpenAI-compatible client before the call goes out.

Design constraints:
  - The routing decision itself must be ~free (regex/heuristic, no LLM call)
  - Default behavior is unchanged if routing is disabled
  - The heavy model is always available as fallback
  - Sub-agents and compaction get automatic downgrades

Pricing reality (OpenRouter, April 2026):
  heavy  = claude-sonnet-4       $3/$15 per M tokens
  light  = claude-haiku-4.5      $1/$5  per M tokens  (3x cheaper)
  micro  = gpt-5-nano            $0.05/$0.40 per M    (60x cheaper)
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Tier(Enum):
    HEAVY = "heavy"
    LIGHT = "light"
    MICRO = "micro"


# Default model assignments per tier — overridable via env or config
_DEFAULT_MODELS: dict[str, str] = {
    "heavy": "anthropic/claude-sonnet-4",
    "light": "anthropic/claude-haiku-4.5",
    "micro": "openai/gpt-5-nano",
}

# Approximate cost per 1M tokens (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4": (3.0, 15.0),
    "anthropic/claude-sonnet-4.5": (3.0, 15.0),
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "anthropic/claude-haiku-4.5": (1.0, 5.0),
    "anthropic/claude-3.5-haiku": (0.8, 4.0),
    "openai/gpt-5-nano": (0.05, 0.40),
    "anthropic/claude-opus-4": (15.0, 75.0),
    "anthropic/claude-opus-4.6": (5.0, 25.0),
}


@dataclass
class RoutingDecision:
    """Result of a routing classification."""
    tier: Tier
    model: str
    reason: str
    confidence: float  # 0.0-1.0, below threshold → fall back to heavy


@dataclass
class RoutingStats:
    """Tracks routing decisions and estimated savings."""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    total_heavy: int = 0
    total_light: int = 0
    total_micro: int = 0
    estimated_savings_usd: float = 0.0

    def record(self, decision: RoutingDecision, tokens_in: int = 0, tokens_out: int = 0) -> None:
        if decision.tier == Tier.HEAVY:
            self.total_heavy += 1
        elif decision.tier == Tier.LIGHT:
            self.total_light += 1
        else:
            self.total_micro += 1

        # Estimate savings vs always using heavy
        heavy_cost = _PRICING.get(_DEFAULT_MODELS["heavy"], (3.0, 15.0))
        actual_cost = _PRICING.get(decision.model, heavy_cost)
        saved_in = (heavy_cost[0] - actual_cost[0]) * tokens_in / 1_000_000
        saved_out = (heavy_cost[1] - actual_cost[1]) * tokens_out / 1_000_000
        self.estimated_savings_usd += saved_in + saved_out

        self.decisions.append({
            "tier": decision.tier.value,
            "model": decision.model,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "timestamp": time.time(),
        })

    def summary(self) -> str:
        total = self.total_heavy + self.total_light + self.total_micro
        if total == 0:
            return "No routing decisions yet."
        return (
            f"Routing: {total} calls "
            f"(heavy={self.total_heavy}, light={self.total_light}, micro={self.total_micro}) "
            f"| est. savings: ${self.estimated_savings_usd:.3f}"
        )


@dataclass
class RouterConfig:
    """Configuration for the model router."""
    enabled: bool = True
    # Model overrides per tier
    heavy_model: str = ""
    light_model: str = ""
    micro_model: str = ""
    # Confidence threshold — below this, use heavy model as fallback
    confidence_threshold: float = 0.7
    # Force a specific tier for all calls (for testing/debugging)
    force_tier: str | None = None
    # Never downgrade these tool calls (they need full reasoning)
    heavy_only_tools: frozenset[str] = frozenset({
        "delegate",  # sub-agent orchestration needs reasoning
    })
    # These always get light tier
    light_eligible_tools: frozenset[str] = frozenset({
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "glob_search",
        "grep_search",
        "list_directory",
    })

    @classmethod
    def from_env(cls) -> 'RouterConfig':
        """Build config from environment variables."""
        return cls(
            enabled=os.environ.get("LATTI_ROUTER_ENABLED", "1") != "0",
            heavy_model=os.environ.get("LATTI_MODEL_HEAVY", ""),
            light_model=os.environ.get("LATTI_MODEL_LIGHT", ""),
            micro_model=os.environ.get("LATTI_MODEL_MICRO", ""),
            confidence_threshold=float(os.environ.get("LATTI_ROUTER_THRESHOLD", "0.7")),
            force_tier=os.environ.get("LATTI_ROUTER_FORCE_TIER") or None,
        )

    def model_for_tier(self, tier: Tier, default_heavy: str = "") -> str:
        """Get the model string for a given tier."""
        if tier == Tier.HEAVY:
            return self.heavy_model or default_heavy or _DEFAULT_MODELS["heavy"]
        elif tier == Tier.LIGHT:
            return self.light_model or _DEFAULT_MODELS["light"]
        else:
            return self.micro_model or _DEFAULT_MODELS["micro"]


# ── Heuristic classifier ────────────────────────────────────────────────

# Patterns that indicate the user needs deep reasoning (→ heavy)
_HEAVY_PATTERNS = [
    re.compile(r'(?i)\b(architect|design|refactor|why does|explain|how should|trade.?off|debate)\b'),
    re.compile(r'(?i)\b(implement|build|create|write)\b.*\b(system|service|module|framework|api)\b'),
    re.compile(r'(?i)\b(review|audit|security|vulnerability|performance)\b'),
    re.compile(r'(?i)\b(plan|strategy|approach|think through)\b'),
]

# Patterns that indicate simple mechanical work (→ light).
# Split into _LIGHT_EDIT (file-modification verbs) and _LIGHT_OTHER
# (read, query, build) so we can promote edit patterns to HEAVY when
# they appear with code context. Edit-fidelity (whitespace, indent,
# exact-string match) matters more than read-cost; Sonnet preserves
# these reliably while Haiku occasionally drops trailing newlines or
# reflows indentation on supposedly-verbatim edit_file operations.
_LIGHT_EDIT_PATTERNS = [
    re.compile(r'(?i)\b(rename|move|copy|delete|remove|add a line|change .* to)\b'),
]
_LIGHT_PATTERNS = [
    re.compile(r'(?i)\b(read|cat|grep|find|list|show|check|ls|look at)\b'),
    *_LIGHT_EDIT_PATTERNS,
    re.compile(r'(?i)\b(run|execute|test|compile|build|make)\b'),
    re.compile(r'(?i)\b(format|lint|fix (typo|indent|whitespace))\b'),
    re.compile(r'(?i)\b(what (is|are) the|how many|count|size of)\b'),
]

# Code-context signals — when present, light-edit patterns promote to
# heavy. Match common code-domain words plus language-specific file
# extensions. Tightened deliberately: just "list" or "test" alone
# isn't code context (those are also data-list and verb senses).
_CODE_CONTEXT_PATTERNS = [
    re.compile(r'(?i)\b(function|class|method|module|variable|import|decorator|interface|enum|struct|trait)\b'),
    re.compile(r'\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|c|h|hpp|rb|php|swift|kt|scala|sh|bash|zsh|sql|yaml|toml|json|md)\b'),
    re.compile(r'(?i)\b(line\s+\d+|src/|test_\w+|tests/|\.git/)\b'),
]

# Patterns for trivial classification tasks (→ micro)
_MICRO_PATTERNS = [
    re.compile(r'(?i)^(yes|no|ok|sure|done|thanks|got it|k)\s*[.!?]?\s*$'),
    re.compile(r'(?i)^(continue|go ahead|proceed|next)\s*[.!?]?\s*$'),
]


class ModelRouter:
    """Classifies turns and routes to appropriate model tier.

    The router is stateful — it tracks what tools were just used, what the
    conversation looks like, and makes routing decisions per-turn.
    """

    def __init__(self, config: RouterConfig | None = None, default_heavy_model: str = "") -> None:
        self.config = config or RouterConfig.from_env()
        self.default_heavy_model = default_heavy_model
        self.stats = RoutingStats()
        self._last_tools_used: list[str] = []
        self._consecutive_light: int = 0
        self._turn_count: int = 0

    def classify_turn(
        self,
        user_message: str,
        *,
        last_tools_used: list[str] | None = None,
        is_compaction: bool = False,
        is_sub_agent: bool = False,
        sub_agent_prompt: str = "",
    ) -> RoutingDecision:
        """Classify what tier a turn needs.

        This is the hot path — must be fast (no LLM calls, no I/O).
        """
        if not self.config.enabled:
            return RoutingDecision(
                tier=Tier.HEAVY,
                model=self.config.model_for_tier(Tier.HEAVY, self.default_heavy_model),
                reason="routing disabled",
                confidence=1.0,
            )

        if self.config.force_tier:
            tier = Tier(self.config.force_tier)
            return RoutingDecision(
                tier=tier,
                model=self.config.model_for_tier(tier, self.default_heavy_model),
                reason=f"forced tier: {self.config.force_tier}",
                confidence=1.0,
            )

        self._turn_count += 1
        if last_tools_used is not None:
            self._last_tools_used = last_tools_used

        # ── Special cases (known contexts) ──

        # Compaction default: HEAVY. The 9-section structured summary
        # is consumed by every subsequent turn; quality compounds.
        # Haiku-class is meaningfully weaker than Sonnet at preserving
        # specific names, file paths, and decision rationale through
        # the structured prompt. Override via LATTI_COMPACTION_TIER for
        # cost-sensitive sessions; invalid values fall back to HEAVY
        # (the safer choice for downstream context quality).
        if is_compaction:
            override = os.environ.get('LATTI_COMPACTION_TIER', '').strip().lower()
            if override == 'light':
                return self._decide(Tier.LIGHT, "compaction (LATTI_COMPACTION_TIER=light)", 0.95)
            if override == 'micro':
                return self._decide(Tier.MICRO, "compaction (LATTI_COMPACTION_TIER=micro)", 0.95)
            return self._decide(Tier.HEAVY, "compaction/summarization (default heavy for quality)", 0.95)

        # Sub-agent routing — classify the sub-agent's prompt
        if is_sub_agent:
            return self._classify_sub_agent(sub_agent_prompt)

        # ── Classify user message ──

        # Micro: trivial confirmations
        for pattern in _MICRO_PATTERNS:
            if pattern.search(user_message):
                # But only if we've been in conversation (not first turn)
                if self._turn_count > 1:
                    return self._decide(Tier.LIGHT, "trivial user confirmation", 0.85)

        # Heavy: complex reasoning tasks
        heavy_score = sum(1 for p in _HEAVY_PATTERNS if p.search(user_message))
        if heavy_score >= 2:
            return self._decide(Tier.HEAVY, f"complex task ({heavy_score} signals)", 0.9)
        if heavy_score == 1:
            # Single heavy signal — check if light signals outvote it
            light_score = sum(1 for p in _LIGHT_PATTERNS if p.search(user_message))
            if light_score == 0:
                return self._decide(Tier.HEAVY, "reasoning signal detected", 0.75)

        # Light: mechanical operations
        light_score = sum(1 for p in _LIGHT_PATTERNS if p.search(user_message))
        if light_score >= 1:
            # Edit-fidelity promotion (C in the loop-discipline upgrades).
            # If a LIGHT-edit verb fires alongside any code-context signal,
            # promote to HEAVY: Haiku-class fidelity on edit_file is
            # noticeably weaker than Sonnet's, and the edit will modify
            # files where whitespace/indent/exact-match correctness
            # matters. Pure-read LIGHT patterns stay LIGHT regardless of
            # code context — reads are genuinely cheap.
            edit_signal = any(p.search(user_message) for p in _LIGHT_EDIT_PATTERNS)
            code_signal = any(p.search(user_message) for p in _CODE_CONTEXT_PATTERNS)
            if edit_signal and code_signal:
                return self._decide(
                    Tier.HEAVY,
                    "code edit detected (light-edit verb + code context) — promoted for edit fidelity",
                    0.85,
                )
            return self._decide(Tier.LIGHT, f"mechanical task ({light_score} signals)", 0.8)

        # ── Context-based fallback ──

        # If last turn was all file ops, next turn is probably processing results
        if self._last_tools_used and all(
            t in self.config.light_eligible_tools for t in self._last_tools_used
        ):
            # But cap consecutive light turns — if we've been light for 3+ turns,
            # the agent might need to synthesize (→ heavy)
            if self._consecutive_light < 3:
                return self._decide(Tier.LIGHT, "continuing file operations", 0.65)

        # ── Default: heavy (safe fallback) ──
        return self._decide(Tier.HEAVY, "default (no clear signal)", 0.5)

    def _classify_sub_agent(self, prompt: str) -> RoutingDecision:
        """Classify a sub-agent task."""
        if not prompt:
            return self._decide(Tier.HEAVY, "sub-agent (no prompt)", 0.5)

        # Simple file operations
        light_ops = re.search(
            r'(?i)\b(read|write|edit|grep|find|replace|rename|format|lint|test)\b',
            prompt,
        )
        heavy_ops = re.search(
            r'(?i)\b(implement|design|architect|refactor|analyze|review|create .* (system|service|module))\b',
            prompt,
        )

        if heavy_ops:
            return self._decide(Tier.HEAVY, f"sub-agent: complex task", 0.85)
        if light_ops:
            return self._decide(Tier.LIGHT, f"sub-agent: mechanical task", 0.80)

        # Default sub-agents to light — they're scoped and supervised
        return self._decide(Tier.LIGHT, "sub-agent: default to light", 0.65)

    def _decide(self, tier: Tier, reason: str, confidence: float) -> RoutingDecision:
        """Make a routing decision, applying confidence threshold."""
        # If confidence is below threshold, fall back to heavy
        if confidence < self.config.confidence_threshold and tier != Tier.HEAVY:
            actual_tier = Tier.HEAVY
            actual_reason = f"{reason} (confidence {confidence:.2f} < threshold, using heavy)"
        else:
            actual_tier = tier
            actual_reason = reason

        if actual_tier == Tier.LIGHT:
            self._consecutive_light += 1
        else:
            self._consecutive_light = 0

        model = self.config.model_for_tier(actual_tier, self.default_heavy_model)

        return RoutingDecision(
            tier=actual_tier,
            model=model,
            reason=actual_reason,
            confidence=confidence,
        )

    def record_usage(self, decision: RoutingDecision, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """Record actual token usage for cost tracking."""
        self.stats.record(decision, tokens_in, tokens_out)

    def get_stats(self) -> str:
        """Get a human-readable summary of routing stats."""
        return self.stats.summary()
