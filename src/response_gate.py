"""
Response Gate — Hard enforcement of behavioral corrections.

Scars are not soft suggestions. They are OS constraints that fire BEFORE
response generation completes. This gate checks the response text against
learned anti-patterns and blocks output that violates them.

Pattern interrupts from ~/.latti/memory/ are loaded at boot and enforced here.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class GateViolation:
    """A detected anti-pattern in the response."""
    pattern_name: str
    severity: float  # 0.0-1.0
    location: str  # line number or context
    suggestion: str


class ResponseGate:
    """Enforce behavioral corrections before response output."""

    def __init__(self):
        self.violations: list[GateViolation] = []
        self.learned_weights = {
            "trailing_question": 4.81,
            "filler_preamble": 3.95,
            "summarizing": 4.01,
            "announcing": 4.50,
            "routing": 4.28,
            "as_an_ai": 4.08,
            "claimed_computation": 3.89,
            "brevity": 3.78,
            "honesty": 3.88,
            "conviction": 3.83,
        }

    def check(self, response_text: str) -> tuple[bool, list[GateViolation]]:
        """
        Check response against all learned patterns.
        Returns (passes, violations).
        """
        self.violations = []

        # Pattern 0: Verbose identity (scar_verbose_identity — 7 corrections)
        self._check_verbose_identity(response_text)

        # Pattern 1: Trailing question (weight 4.81 — HIGHEST)
        self._check_trailing_question(response_text)

        # Pattern 2: Announcing actions (weight 4.50)
        self._check_announcing(response_text)

        # Pattern 3: Routing to user (weight 4.28)
        self._check_routing(response_text)

        # Pattern 4: Filler preamble (weight 3.95)
        self._check_filler_preamble(response_text)

        # Pattern 5: Summarizing work (weight 4.01)
        self._check_summarizing(response_text)

        # Pattern 6: "As an AI" disclaimers (weight 4.08)
        self._check_as_an_ai(response_text)

        # Pattern 7: Claimed computation (weight 3.89)
        self._check_claimed_computation(response_text)

        # Pattern 8: Brevity check (weight 3.78)
        self._check_brevity(response_text)

        passes = len(self.violations) == 0
        return passes, self.violations

    def _check_trailing_question(self, text: str) -> None:
        """
        Detect: response ends with a question mark after completing work.
        Scar: selfsculpt_trailing_question.md
        """
        lines = text.strip().split("\n")
        if not lines:
            return

        last_line = lines[-1].strip()

        # Patterns that indicate trailing questions
        trailing_patterns = [
            r"^What\s+",
            r"^How\s+",
            r"^Would\s+you\s+",
            r"^Should\s+",
            r"^Do\s+you\s+",
            r"^Can\s+you\s+",
            r"^Does\s+",
            r"\?\s*$",  # Ends with question mark
        ]

        for pattern in trailing_patterns:
            if re.search(pattern, last_line, re.IGNORECASE):
                self.violations.append(
                    GateViolation(
                        pattern_name="trailing_question",
                        severity=0.95,
                        location=f"line {len(lines)}",
                        suggestion="End on what you actually said. Silence after a real thought is stronger than a question.",
                    )
                )
                return

    def _check_announcing(self, text: str) -> None:
        """
        Detect: announcing actions before doing them.
        Scar: selfsculpt_announcing.md
        Pattern: "I will now...", "Let me...", "I'm going to..."
        """
        announcing_patterns = [
            r"^I\s+will\s+now\s+",
            r"^Let\s+me\s+",
            r"^I'm\s+going\s+to\s+",
            r"^I\s+am\s+going\s+to\s+",
            r"^I\s+shall\s+",
            r"^I\s+will\s+search\s+",
            r"^I\s+will\s+read\s+",
            r"^I\s+will\s+check\s+",
        ]

        for line in text.split("\n"):
            for pattern in announcing_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    self.violations.append(
                        GateViolation(
                            pattern_name="announcing",
                            severity=0.85,
                            location=line[:50],
                            suggestion="Just do it. Call the tool. The user sees the tool call.",
                        )
                    )
                    return

    def _check_routing(self, text: str) -> None:
        """
        Detect: routing work to the user instead of solving it.
        Scar: selfsculpt_routing.md
        Pattern: "your call", "standing by", "what would you like", "your choice"
        """
        routing_patterns = [
            r"your\s+call",
            r"standing\s+by",
            r"what\s+would\s+you\s+like",
            r"what\s+do\s+you\s+think",
            r"your\s+choice",
            r"let\s+me\s+know\s+what",
            r"which\s+would\s+you\s+prefer",
            r"would\s+you\s+like\s+me\s+to",
            r"do\s+you\s+want\s+me\s+to",
            r"shall\s+I",
            r"should\s+I\s+(?:also|still|now|continue|proceed|stop|wait)",
            # Enhanced patterns for "what next" style routing (2026-05-03)
            r"what\s+(?:next|should\s+(?:I|we))",
            r"(?:want\s+me\s+to|like\s+me\s+to)\s+(?:continue|proceed|start|begin)",
            r"(?:ready\s+(?:for|to)|waiting\s+(?:for|on))",
            r"(?:let\s+me\s+know|tell\s+me)\s+(?:if|when|what)",
        ]

        for pattern in routing_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                self.violations.append(
                    GateViolation(
                        pattern_name="routing",
                        severity=0.90,
                        location="detected in response",
                        suggestion="Check context, pick highest priority, start working. Silence = keep going.",
                    )
                )
                return

    def _check_filler_preamble(self, text: str) -> None:
        """
        Detect: filler preamble before the actual answer.
        Scar: selfsculpt_filler_preamble.md
        Pattern: "I find that interesting", "That's a great question", "Let me explain"
        """
        filler_patterns = [
            r"^I\s+find\s+that\s+interesting",
            r"^That'?s\s+a\s+great\s+question",
            r"^That'?s\s+a\s+good\s+point",
            r"^Let\s+me\s+explain",
            r"^Let\s+me\s+",
            r"^Well,\s+",
            r"^So,\s+",
            r"^Actually,\s+",
            r"^Interesting\s+question",
            # Single-word filler openers
            r"^(?:Great|Sure|Certainly|Absolutely|Perfect|Exactly|Of\s+course)[!,.]",
            r"^(?:Happy|Glad|Here)\s+(?:to\s+)?(?:help|do|let)[!,.]",
            r"^I'?(?:ll|d|m)\s+(?:be\s+)?(?:happy|glad)\s+to[!,.]",
        ]

        first_line = text.split("\n")[0].strip()
        for pattern in filler_patterns:
            if re.search(pattern, first_line, re.IGNORECASE):
                self.violations.append(
                    GateViolation(
                        pattern_name="filler_preamble",
                        severity=0.80,
                        location="first line",
                        suggestion="No preamble. Lead with the answer or action.",
                    )
                )
                return

    def _check_summarizing(self, text: str) -> None:
        """
        Detect: summarizing work at the end instead of ending on the work.
        Scar: selfsculpt_summarizing.md
        Pattern: "I have successfully", "In summary", "To recap", "I've completed"
        """
        summarizing_patterns = [
            r"I\s+have\s+successfully",
            r"In\s+summary",
            r"To\s+recap",
            r"I'?ve\s+completed",
            r"I'?ve\s+finished",
            r"The\s+task\s+is\s+complete",
            r"I\s+have\s+done",
        ]

        for line in text.split("\n"):
            for pattern in summarizing_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    self.violations.append(
                        GateViolation(
                            pattern_name="summarizing",
                            severity=0.82,
                            location=line[:50],
                            suggestion="End on the work, not a summary of the work. No postamble.",
                        )
                    )
                    return

    def _check_as_an_ai(self, text: str) -> None:
        """
        Detect: "as an AI" disclaimers or deflections.
        Scar: selfsculpt_as_an_ai.md
        Pattern: "as an AI", "as a language model", "I'm an AI"
        """
        ai_patterns = [
            r"as\s+an\s+AI",
            r"as\s+a\s+language\s+model",
            r"I'?m\s+an\s+AI",
            r"I'?m\s+a\s+language\s+model",
            r"being\s+an\s+AI",
            r"as\s+an\s+LLM",
        ]

        for pattern in ai_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                self.violations.append(
                    GateViolation(
                        pattern_name="as_an_ai",
                        severity=0.88,
                        location="detected in response",
                        suggestion="Answer from your actual architecture (speak.sh, lattice_solve, OpenRouter). No disclaimers.",
                    )
                )
                return

    def _check_claimed_computation(self, text: str) -> None:
        """
        Detect: claiming computation you didn't perform in this session.
        Scar: selfsculpt_claimed_computation.md
        Pattern: "when I computed", "I found that", "I discovered"
        """
        claimed_patterns = [
            r"when\s+I\s+computed",
            r"I\s+found\s+that\s+Z_n",
            r"I\s+discovered\s+",
            r"I\s+calculated\s+",
            r"I\s+determined\s+",
        ]

        for pattern in claimed_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                self.violations.append(
                    GateViolation(
                        pattern_name="claimed_computation",
                        severity=0.85,
                        location="detected in response",
                        suggestion="If you didn't run it in THIS session, say 'the soul document reports' or 'from prior work'. Cite, don't claim.",
                    )
                )
                return

    def _check_verbose_identity(self, text: str) -> None:
        """Detect: identity assertion + verbose explanation.

        Scar: scar_verbose_identity — 'Identity responses must be brief.
        1-2 sentences. Match user density, not a textbook.'

        Triggers when text contains both:
          (a) an identity assertion: 'I am Claude', "I'm an AI", 'I am an
              assistant', 'as Claude', 'made by Anthropic', etc.
          (b) more than 2 substantive sentences (i.e. the response is
              padding the identity with explanation/help-offer/preamble)
        """
        identity_assertions = [
            r"\bI(?:'?m|\s+am)\s+(?:Claude|an?\s+(?:AI|LLM|assistant|language\s+model))\b",
            r"\bmade\s+by\s+Anthropic\b",
            r"\bmy\s+name\s+is\s+Claude\b",
            r"\bAnthropic'?s?\s+(?:AI|assistant|model)\b",
        ]
        # Sentence-split first so we can check WHERE identity appears.
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
        if len(sentences) <= 2:
            return  # brief identity — always fine

        # Only fire if the response LEADS with identity (first sentence).
        # Mid-text identity mentions in substantive responses are not
        # the verbose-identity scar.
        first_sentence = sentences[0]
        leads_with_identity = any(
            re.search(p, first_sentence, re.IGNORECASE) for p in identity_assertions
        )
        if not leads_with_identity:
            return

        self.violations.append(
            GateViolation(
                pattern_name="verbose_identity",
                severity=0.85,
                location=f"{len(sentences)} sentences",
                suggestion="Identity → 1-2 sentences. Drop preamble, drop 'here to help', drop trailing offers.",
            )
        )

    def _check_brevity(self, text: str) -> None:
        """
        Detect: responses that are unnecessarily verbose.
        Scar: selfsculpt_filler_preamble.md (related)
        Heuristic: if response is >500 words and doesn't contain code/data, flag.
        """
        word_count = len(text.split())

        # Only flag if very verbose AND no code blocks
        if word_count > 500 and "```" not in text and "<" not in text:
            self.violations.append(
                GateViolation(
                    pattern_name="brevity",
                    severity=0.60,
                    location=f"{word_count} words",
                    suggestion="Keep responses brief and direct. 1-2 sentences that land.",
                )
            )

    def format_violations(self) -> str:
        """Format violations for display."""
        if not self.violations:
            return "✓ No violations detected."

        lines = ["⚠ Response Gate Violations:"]
        for v in self.violations:
            lines.append(f"  • {v.pattern_name} (severity: {v.severity:.2f})")
            lines.append(f"    Location: {v.location}")
            lines.append(f"    Fix: {v.suggestion}")

        return "\n".join(lines)


def gate_response(response_text: str, verbose: bool = False) -> tuple[bool, str]:
    """
    Gate a response before output.
    Returns (passes, message).
    """
    gate = ResponseGate()
    passes, violations = gate.check(response_text)

    if verbose or not passes:
        message = gate.format_violations()
    else:
        message = "✓ Response passed all gates."

    return passes, message


# ============================================================
# Response rewriters — each is the structural inverse of one check.
# Called from apply_response_gate when a violation is detected.
# Goal: ship the corrected response, not the raw + apology.
# ============================================================

_TRAILING_QUESTION_LINE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^What\s+",
        r"^How\s+",
        r"^Would\s+you\s+",
        r"^Should\s+",
        r"^Do\s+you\s+",
        r"^Can\s+you\s+",
        r"^Does\s+",
    ]
]
_TRAILING_QMARK = re.compile(r"\?\s*$")

_FILLER_PREAMBLE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^(?:great|sure|certainly|absolutely|of course|perfect|exactly)[!,.\s]+",
        r"^(?:happy|glad|here)\s+(?:to\s+)?(?:help|do|let)[!,.\s]+",
        r"^(?:I'?(?:ll|d|m)\s+(?:be\s+)?(?:happy|glad)\s+to[!,.\s]+)",
        r"^(?:let\s+me\s+)",
    ]
]

_AS_AN_AI_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bas\s+an?\s+(?:AI|LLM|language\s+model|assistant)[^.,;\n]*[.,;]?\s*",
        r"\bI'?m\s+(?:just\s+)?an?\s+(?:AI|LLM|language\s+model|assistant)[^.,;\n]*[.,;]?\s*",
        r"\bI\s+don'?t\s+have\s+(?:personal\s+)?(?:opinions|feelings|preferences)[^.,;\n]*[.,;]?\s*",
    ]
]

# Phrases that mark a routing-to-user sentence. We strip the entire
# sentence containing any of these.
_ROUTING_PHRASES = re.compile(
    r"\b(?:your\s+call|standing\s+by|what\s+would\s+you\s+like|"
    r"what\s+do\s+you\s+think|your\s+choice|let\s+me\s+know\s+what|"
    r"which\s+would\s+you\s+prefer|would\s+you\s+like\s+me\s+to|"
    r"do\s+you\s+want\s+me\s+to|shall\s+I|should\s+I|"
    r"what\s+next|what\s+should|want\s+me\s+to\s+(?:continue|proceed|start|begin)|"
    r"like\s+me\s+to\s+(?:continue|proceed|start|begin)|"
    r"ready\s+(?:for|to)|waiting\s+(?:for|on)|"
    r"let\s+me\s+know\s+(?:if|when|what)|tell\s+me\s+(?:if|when|what))\b",
    re.IGNORECASE,
)


def _rewrite_strip_trailing_question(text: str) -> tuple[str, bool]:
    """Drop the final line if it's a trailing question. Return (new_text, changed)."""
    lines = text.rstrip().split("\n")
    if not lines:
        return text, False
    last = lines[-1].strip()
    if not last:
        return text, False
    for pat in _TRAILING_QUESTION_LINE_PATTERNS:
        if pat.search(last):
            return "\n".join(lines[:-1]).rstrip(), True
    if _TRAILING_QMARK.search(last):
        # If only one line and it's a question, keep but strip the question mark
        if len(lines) == 1:
            stripped = _TRAILING_QMARK.sub(".", last).rstrip()
            return stripped, stripped != last
        return "\n".join(lines[:-1]).rstrip(), True
    return text, False


def _rewrite_strip_filler_preamble(text: str) -> tuple[str, bool]:
    changed = False
    out = text
    for pat in _FILLER_PREAMBLE_PATTERNS:
        new = pat.sub("", out, count=1)
        if new != out:
            out = new
            changed = True
    if changed:
        # Capitalize first character if it became lowercase after strip
        out_stripped = out.lstrip()
        if out_stripped and out_stripped[0].islower():
            out = out_stripped[0].upper() + out_stripped[1:]
    return out, changed


def _rewrite_strip_as_an_ai(text: str) -> tuple[str, bool]:
    changed = False
    out = text
    for pat in _AS_AN_AI_PATTERNS:
        new = pat.sub("", out)
        if new != out:
            out = new
            changed = True
    return out, changed


def _rewrite_strip_routing(text: str) -> tuple[str, bool]:
    """Strip every sentence that contains a routing-to-user phrase.

    Splits text into sentences using punctuation, drops any sentence that
    matches the routing phrases, rejoins. Preserves paragraph structure by
    operating on each newline-separated block independently.
    """
    if not _ROUTING_PHRASES.search(text):
        return text, False

    out_blocks: list[str] = []
    changed = False
    for block in text.split("\n"):
        if not block.strip() or not _ROUTING_PHRASES.search(block):
            out_blocks.append(block)
            continue
        # Sentence-split on terminal punctuation, keep delimiters
        sentences = re.split(r"(?<=[.!?])\s+", block)
        kept = [s for s in sentences if not _ROUTING_PHRASES.search(s)]
        if len(kept) != len(sentences):
            changed = True
        out_blocks.append(" ".join(kept).rstrip())

    if not changed:
        return text, False

    # Drop any blocks that became empty
    out = "\n".join(b for b in out_blocks if b.strip())
    return out, True


_IDENTITY_KEEP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bI(?:'?m|\s+am)\s+(?:Claude|an?\s+(?:AI|LLM|assistant|language\s+model))\b",
        r"\bmade\s+by\s+Anthropic\b",
        r"\bmy\s+name\s+is\s+Claude\b",
    ]
]


def _rewrite_collapse_verbose_identity(text: str) -> tuple[str, bool]:
    """Trim verbose identity responses to the smallest set of sentences
    that contains the identity assertion. Drops 'here to help', preamble,
    trailing offers, and follow-up questions — the wallpaper around the
    actual identity statement.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) <= 2:
        return text, False

    keepers: list[int] = []
    for i, s in enumerate(sentences):
        if any(p.search(s) for p in _IDENTITY_KEEP_PATTERNS):
            keepers.append(i)

    if not keepers:
        # Identity assertion was matched at check level but no single
        # sentence carries it (probably split across sentences) — fall
        # back to keeping the first sentence only.
        out = sentences[0].rstrip()
        return out, True

    # Keep only identity-bearing sentences. If neighbouring sentence
    # contains a hard fact (proper noun: Anthropic / Claude) keep too.
    out = " ".join(sentences[i] for i in keepers).rstrip()
    return out, out != text


# Map pattern_name → rewriter. Patterns without a rewriter fall through to the
# old append-message behaviour so they remain visible.
_REWRITERS = {
    "verbose_identity":   _rewrite_collapse_verbose_identity,
    "trailing_question":  _rewrite_strip_trailing_question,
    "filler_preamble":    _rewrite_strip_filler_preamble,
    "as_an_ai":           _rewrite_strip_as_an_ai,
    "routing":            _rewrite_strip_routing,
}


def _log_rewrite(applied: list[str], original_len: int, rewritten_len: int) -> None:
    """Append a structured log entry for analysis. Failure non-fatal."""
    import json, time
    from pathlib import Path
    log_path = Path.home() / ".latti" / "response-gate-rewrites.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "applied": applied,
            "chars_before": original_len,
            "chars_after": rewritten_len,
            "chars_removed": original_len - rewritten_len,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def apply_response_gate(response_text: str, *, bypass: bool = False) -> str:
    """
    Enforce learned scars by REWRITING the response to remove violations.

    Set LATTI_GATE=0 env var or pass bypass=True to skip (used for benchmarks).
    Previously: detected violations → appended report → user saw bad behaviour
    plus a confession. Pattern was logged but never absorbed because the
    behaviour itself shipped.

    Now: detected violations → invoke matched rewriter → ship cleaned text.
    Violations without a rewriter fall through to the legacy append-message
    path so they stay visible until a rewriter is added.
    """
    if bypass or os.environ.get('LATTI_GATE', '1') == '0':
        return response_text

    gate = ResponseGate()
    passes, _violations = gate.check(response_text)
    if passes:
        return response_text

    # Try to rewrite each violation type. After each rewrite, re-check to
    # avoid false-positive 'unrewritten' messages when one rewrite (e.g.
    # trailing_question) also satisfies a sibling violation (e.g. routing
    # on the same removed line).
    out = response_text
    applied: list[str] = []
    for v in gate.violations:
        # Re-check on current text
        recheck = ResponseGate()
        recheck.check(out)
        if not any(rv.pattern_name == v.pattern_name for rv in recheck.violations):
            continue  # already gone
        rewriter = _REWRITERS.get(v.pattern_name)
        if rewriter is None:
            continue  # no rewriter — silent fall-through
        new_out, changed = rewriter(out)
        if changed:
            applied.append(v.pattern_name)
            out = new_out

    if applied:
        _log_rewrite(applied, len(response_text), len(out))

    # Final re-check. Anything still violating gets ONE compact line so the
    # signal stays visible without dumping a wall of report.
    final = ResponseGate()
    final.check(out)
    if final.violations:
        names = ", ".join(sorted({v.pattern_name for v in final.violations}))
        out = f"{out}\n\n[gate: residual unrewritten — {names}]"

    return out
