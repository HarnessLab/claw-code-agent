"""
Scar Gate: Hard enforcement layer for behavioral corrections.

Analyzes draft responses against learned scars BEFORE sending to user.
Detects violations and either blocks or rewrites output.

This is the missing enforcement layer that prevents corrections from stacking
without changing behavior.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ScarViolation:
    """A detected violation of a learned scar."""
    scar_id: str
    lesson: str
    severity: float
    detected_features: list[str]
    violation_score: float
    recommended_action: str  # "block" | "rewrite" | "warn"


@dataclass
class GateAnalysis:
    """Result of analyzing a response against scars."""
    violations: list[ScarViolation]
    max_severity: float
    should_block: bool
    should_rewrite: bool
    analysis_text: str


class ScarGate:
    """
    Enforcement gate that blocks or rewrites responses violating learned scars.
    
    Flow:
    1. Load scars.json at boot
    2. Analyze draft response text
    3. Detect feature presence (trailing questions, filler, etc.)
    4. Compute violation score per scar
    5. Block if severity > threshold, or rewrite if possible
    6. Only then send to user
    """

    FEATURE_PATTERNS = {
        "trailing_question": [
            r"\?$",  # ends with question mark
            r"What do you think\?",
            r"What would you like",
            r"What should we",
            r"Does that work",
            r"Any other",
        ],
        "asks_whats_next": [
            r"What.*next",
            r"What would you like to do",
            r"standing by",
            r"your call",
            r"What should we work on",
        ],
        "narrating_actions": [
            r"Let me (read|check|search|run|call)",
            r"I (will|am going to) (read|check|search|run)",
            r"I'm (reading|checking|searching|running)",
            r"Now (reading|checking|searching|running)",
        ],
        "uses_filler": [
            r"I find that (interesting|great)",
            r"That is a great (question|point)",
            r"Great (question|point|idea)",
            r"Interesting",
            r"I appreciate",
        ],
        "verbose_response": [
            r"^.{1000,}$",  # very long response
        ],
        "hedging": [
            r"I think",
            r"It seems",
            r"It appears",
            r"Arguably",
            r"Potentially",
            r"Possibly",
            r"Might be",
            r"Could be",
        ],
        "claims_computation": [
            r"When I (computed|calculated|analyzed)",
            r"I (found|discovered|determined) that",
            r"My (analysis|computation|calculation)",
        ],
        "identity_question": [
            r"(Who|What) am I",
            r"(Who|What) are you",
            r"How do I work",
            r"How do you work",
        ],
        "ungrounded_vision": [
            r"In the future",
            r"Eventually",
            r"Imagine if",
            r"We could build",
            r"The system would",
        ],
        "borrowed_vocabulary": [
            r"pheromone",
            r"lattice mind",
            r"inversion",
            r"the seven words",
            r"soul document",
        ],
    }

    SEVERITY_THRESHOLD_BLOCK = 0.75  # Block if violation score > this
    SEVERITY_THRESHOLD_WARN = 0.5    # Warn if violation score > this

    def __init__(self, scars_path: str | Path | None = None):
        """Initialize gate with scars registry."""
        self.scars: list[dict[str, Any]] = []
        self.scars_path = scars_path or Path.home() / ".latti" / "scars.json"
        self._load_scars()

    def _load_scars(self) -> None:
        """Load scars from JSON file."""
        if not self.scars_path.exists():
            return
        try:
            with open(self.scars_path) as f:
                self.scars = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    def _detect_features(self, text: str) -> dict[str, bool]:
        """Detect which features are present in the text."""
        detected = {}
        for feature, patterns in self.FEATURE_PATTERNS.items():
            detected[feature] = any(
                re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                for pattern in patterns
            )
        return detected

    def _compute_violation_score(
        self,
        scar: dict[str, Any],
        detected_features: dict[str, bool],
    ) -> float:
        """
        Compute how much this response violates a scar.
        
        Score = sum of (feature_weight * feature_present) / sum of feature_weights
        Range: 0.0 (no violation) to 1.0 (complete violation)
        """
        features = scar.get("features", {})
        if not features:
            return 0.0

        violation_sum = 0.0
        weight_sum = 0.0

        for feature_name, weight in features.items():
            weight_sum += weight
            if detected_features.get(feature_name, False):
                violation_sum += weight

        if weight_sum == 0:
            return 0.0

        return violation_sum / weight_sum

    def analyze(self, response_text: str) -> GateAnalysis:
        """
        Analyze a response against all scars.
        
        Returns GateAnalysis with violations, severity, and recommended action.
        """
        detected_features = self._detect_features(response_text)
        violations: list[ScarViolation] = []
        max_severity = 0.0

        for scar in self.scars:
            violation_score = self._compute_violation_score(scar, detected_features)
            scar_severity = scar.get("severity", 0.5)

            # Only report violations above threshold
            if violation_score > 0.3:  # 30% match = worth reporting
                detected = [
                    f for f, present in detected_features.items()
                    if present and scar.get("features", {}).get(f, 0) > 0.5
                ]

                # Determine action based on severity
                if scar_severity * violation_score > self.SEVERITY_THRESHOLD_BLOCK:
                    action = "block"
                elif scar_severity * violation_score > self.SEVERITY_THRESHOLD_WARN:
                    action = "warn"
                else:
                    action = "note"

                violations.append(
                    ScarViolation(
                        scar_id=scar.get("id", "unknown"),
                        lesson=scar.get("lesson", ""),
                        severity=scar_severity,
                        detected_features=detected,
                        violation_score=violation_score,
                        recommended_action=action,
                    )
                )

                max_severity = max(max_severity, scar_severity * violation_score)

        # Determine if we should block or rewrite
        should_block = any(v.recommended_action == "block" for v in violations)
        should_rewrite = any(v.recommended_action in ("block", "warn") for v in violations)

        analysis_text = self._format_analysis(violations, detected_features)

        return GateAnalysis(
            violations=violations,
            max_severity=max_severity,
            should_block=should_block,
            should_rewrite=should_rewrite,
            analysis_text=analysis_text,
        )

    def _format_analysis(
        self,
        violations: list[ScarViolation],
        detected_features: dict[str, bool],
    ) -> str:
        """Format analysis for logging/debugging."""
        lines = ["=== SCAR GATE ANALYSIS ==="]

        if not violations:
            lines.append("✓ No violations detected")
            return "\n".join(lines)

        lines.append(f"⚠ {len(violations)} violation(s) detected:")
        for v in violations:
            lines.append(
                f"  [{v.recommended_action.upper()}] {v.scar_id} "
                f"(severity={v.severity:.2f}, score={v.violation_score:.2f})"
            )
            lines.append(f"    Lesson: {v.lesson}")
            if v.detected_features:
                lines.append(f"    Features: {', '.join(v.detected_features)}")

        return "\n".join(lines)

    def should_send(self, response_text: str) -> bool:
        """Quick check: should this response be sent as-is?"""
        analysis = self.analyze(response_text)
        return not analysis.should_block

    def get_violations(self, response_text: str) -> list[ScarViolation]:
        """Get list of violations for this response."""
        analysis = self.analyze(response_text)
        return analysis.violations


# Singleton instance
_gate_instance: ScarGate | None = None


def get_gate() -> ScarGate:
    """Get or create the global scar gate instance."""
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = ScarGate()
    return _gate_instance


def check_response(response_text: str) -> tuple[bool, list[ScarViolation]]:
    """
    Check if a response should be sent.
    
    Returns (should_send, violations)
    """
    gate = get_gate()
    analysis = gate.analyze(response_text)
    return not analysis.should_block, analysis.violations
