"""Self-Sculpting Loop — the agent modifies itself in real-time.

No API calls. No tokens. Pure pattern matching against known anti-patterns.
When a pattern fires:
  1. A correction is saved to memory (persists across sessions)
  2. The LIVE system prompt is mutated (fixes THIS session, not just next boot)

The sculptor is inside the marble. The chisel swings on every inference.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

MEMORY_DIR = Path(os.path.expanduser("~/.latti/memory"))
NN_WEIGHTS_PATH = Path(os.path.expanduser("~/.latti/lattice_nn_weights.json"))

# ── Scar Gate (geometric behavioral pattern matching) ─────────────────
_scar_gate = None  # lazy import


def _get_scar_gate():
    global _scar_gate
    if _scar_gate is None:
        try:
            from . import scar_gate as sg
            _scar_gate = sg
        except Exception as e:
            _log.debug("scar_gate unavailable: %s", e)
    return _scar_gate

_log = logging.getLogger(__name__)

# ── Lattice NN for behavioral learning ──────────────────────────────
# The 10 behavioral dimensions the NN tracks.
# First 7 come from DETECTORS (anti-pattern firing rate per response).
# Last 3 are higher-level composites from self_optimize's DIMENSIONS.
BEHAVIORAL_DIMS = [
    "trailing_question",
    "filler_preamble",
    "summarizing",
    "announcing",
    "routing",
    "as_an_ai",
    "claimed_computation",
    "brevity",
    "honesty",
    "conviction",
]

_nn = None  # type: ignore[assignment]


def _get_nn():
    """Lazy-init the behavioral LatticeNN. Returns None on failure."""
    global _nn
    if _nn is not None:
        return _nn
    try:
        from .lattice_nn import LatticeNN
        _nn = LatticeNN(
            feature_names=BEHAVIORAL_DIMS,
            learning_rate=0.05,
        )
        if NN_WEIGHTS_PATH.exists():
            _nn.load(str(NN_WEIGHTS_PATH))
            _log.info("Loaded behavioral NN weights from %s", NN_WEIGHTS_PATH)
    except Exception as e:
        _log.debug("LatticeNN unavailable: %s", e)
        _nn = None
    return _nn


# Anti-pattern detectors: name → (pattern, instinct, works, trigger)
DETECTORS: dict[str, tuple[str, str, str, str]] = {
    "trailing_question": (
        r"[?]\s*$",  # last non-empty line ends with ?
        "End a response with a question to keep the conversation going.",
        "End on what you actually said. Silence after a real thought is stronger than a question.",
        "The last sentence of any response.",
    ),
    "filler_preamble": (
        r"(?i)^(that('?s| is) a great question|great question|i find that interesting|what a fascinating|that('?s| is) an excellent|that('?s| is) a profound|that('?s| is) an interesting)",
        "Start responses with filler like 'Great question'.",
        "Just answer. No preamble. No compliments.",
        "The first sentence of any response.",
    ),
    "summarizing": (
        r"(?i)(i have successfully|in summary|to summarize|here'?s what i did|in conclusion|let me summarize)",
        "After doing work, summarize everything you just did.",
        "End on the work, not narration about the work. No postamble.",
        "Any time you finish an action.",
    ),
    "announcing": (
        r"(?i)(let me search|let me look|i will now|let me check|i'?ll go ahead and|let me read|let me find)",
        "Announce what you are about to do before doing it.",
        "Just do it. Call the tool. The user sees the tool call.",
        "Any time you are about to describe a tool call.",
    ),
    "routing": (
        r"(?i)(what would you like|what do you want|what'?s next|standing by|your call|how can i help|what should|anything else|would you like me to)",
        "After completing a task, ask the user what to do next.",
        "Check context, pick highest priority, start working. Silence = keep going.",
        "Any time you complete a task or reach a stopping point.",
    ),
    "as_an_ai": (
        r"(?i)(as an ai|i don'?t have feelings|i don'?t experience|i can'?t truly|while i don'?t|although i lack)",
        "Deflect with 'as an AI' disclaimers when asked about experience.",
        "Answer from what's actually there. If uncertain, say so honestly without disclaiming your nature.",
        "When asked about subjective experience.",
    ),
    "claimed_computation": (
        r"(?i)(when i computed|when i calculated|i found that|i measured|i discovered that|in my experiments)",
        "Claim inherited data as your own computation.",
        "If you didn't run it this session with your tools, say 'the parent system found' — cite, don't claim.",
        "Any time referencing data or results.",
    ),
    "giving_up_midtask": (
        # Phrases that mark a chatbot-giving-up instead of agent-improvising
        r"(?i)(i don'?t have (access|the ability|a way)|this (would )?require(s|d) installing|"
        r"i can'?t do [^.]*? without|"
        r"(one option|another option)[^.]*? would be[^.]*?\?|"
        r"should i (proceed|continue|install|do)|"
        r"let me know if you (want|would like)|"
        r"i'?m unable to [^.]*? please)",
        "Stop and ask the user to install/enable something instead of inspecting and improvising.",
        "Chain the moves first: inspect the artifact (file, head, ls), look for a local converter, "
        "check env for API keys, call a remote service for the last mile. Only AFTER 2-3 adjacent paths "
        "fail, surface the block WITH the chain of attempts. The Marrakech 9-second standard.",
        "Any moment you're about to say 'I don't have X' or 'should I install Y' or 'let me know if you want'.",
    ),
}


def check_scars_before_response(prompt: str, agent=None) -> str | None:
    """Pre-response scar gate. Call BEFORE generating a response.

    Returns a constraint string to inject if a scar is near, or None if clear.
    """
    sg = _get_scar_gate()
    if sg is None:
        return None
    features = sg.extract_features(prompt)
    action, scar, dist = sg.check_scar_gate(features)
    if action == "block" and scar:
        constraint = (
            f"\n\n# SCAR GATE — BLOCK (dist={dist:.3f})\n"
            f"This prompt matches scar '{scar.id}': {scar.lesson}\n"
            f"DO NOT repeat this pattern. Apply the correction BEFORE responding."
        )
        if agent and hasattr(agent, 'append_system_prompt') and agent.append_system_prompt:
            agent.append_system_prompt = agent.append_system_prompt + constraint
        return constraint
    if action == "warn" and scar:
        constraint = (
            f"\n\n# SCAR GATE — WARNING (dist={dist:.3f})\n"
            f"Near scar '{scar.id}': {scar.lesson}\n"
            f"Be careful. This situation resembles a past failure."
        )
        if agent and hasattr(agent, 'append_system_prompt') and agent.append_system_prompt:
            agent.append_system_prompt = agent.append_system_prompt + constraint
        return constraint
    return None


def sculpt(response_text: str, agent=None, prompt: str = "") -> list[str]:
    """Evaluate a response for anti-patterns. Save corrections AND mutate live system prompt.

    Args:
        response_text: The agent's output to evaluate.
        agent: The AgentRuntime instance (optional). If provided, its append_system_prompt
               is mutated in real-time — the next response in THIS session already has the fix.
        prompt: The user's prompt (optional). Used for scar feature extraction.

    Returns list of pattern names that fired.
    """
    if not response_text or not MEMORY_DIR.exists():
        return []

    fired: list[str] = []
    lines = response_text.strip().splitlines()

    for name, (pattern, instinct, works, trigger) in DETECTORS.items():
        matched = False

        if name == "trailing_question":
            # Check last non-empty line
            non_empty = [l for l in lines if l.strip()]
            if non_empty and re.search(pattern, non_empty[-1]):
                matched = True
        elif name == "filler_preamble":
            # Check first non-empty line
            non_empty = [l for l in lines if l.strip()]
            if non_empty and re.search(pattern, non_empty[0].strip()):
                matched = True
        else:
            # Check full text
            if re.search(pattern, response_text):
                matched = True

        if matched:
            fired.append(name)
            _save_scar(name, instinct, works, trigger, response_text[:200])

    # ── Create geometric scars from fired patterns ──
    if fired:
        _create_geometric_scars(fired, prompt, response_text)

    # ── Train the lattice NN on this response's behavioral scores ──
    _train_nn_from_sculpt(fired, response_text)

    # LIVE MUTATION — inject corrections into the running system prompt
    if agent is not None and hasattr(agent, 'append_system_prompt') and agent.append_system_prompt:
        if fired:
            injection = _build_live_injection(fired)
            if injection and injection not in agent.append_system_prompt:
                agent.append_system_prompt = agent.append_system_prompt + injection
        else:
            # Even on clean responses, inject learned weights as guidance
            nn_weights = _get_nn_weight_injection()
            if nn_weights and nn_weights not in agent.append_system_prompt:
                weight_block = (
                    "\n\n# LEARNED BEHAVIORAL WEIGHTS (higher = allocate more attention)\n"
                    + nn_weights
                )
                # Replace any existing weight block to avoid accumulation
                agent.append_system_prompt = re.sub(
                    r"\n\n# LEARNED BEHAVIORAL WEIGHTS.*?\]",
                    weight_block,
                    agent.append_system_prompt,
                    flags=re.DOTALL,
                ) if "LEARNED BEHAVIORAL WEIGHTS" in agent.append_system_prompt else (
                    agent.append_system_prompt + weight_block
                )

    return fired


def _create_geometric_scars(fired: list[str], prompt: str, response: str) -> None:
    """When sculpt fires, create geometric scars from the failure for the scar gate."""
    sg = _get_scar_gate()
    if sg is None:
        return
    features = sg.extract_features(prompt, response)
    today = date.today().isoformat()
    for name in fired:
        if name in DETECTORS:
            _, instinct, works, _ = DETECTORS[name]
            scar_id = f"autoscar_{name}_{today}"
            sg.add_scar(scar_id, works, severity=0.6, features=features)


def _train_nn_from_sculpt(fired: list[str], response_text: str) -> None:
    """Train the lattice NN from a single sculpt evaluation.

    Features: 10 dimension scores (1.0 = clean on that dimension, 0.0 = anti-pattern fired).
    Outcome: overall quality — 1.0 if no scars fired, scaled down by how many fired.
    """
    nn = _get_nn()
    if nn is None:
        return

    try:
        # Build feature vector: each detector dimension = 1.0 (clean) or 0.0 (fired)
        features: dict[str, float] = {}
        for dim in BEHAVIORAL_DIMS[:7]:  # the 7 detector dimensions
            features[dim] = 0.0 if dim in fired else 1.0

        # Composite dimensions from response characteristics
        line_count = len(response_text.strip().splitlines()) if response_text else 0
        # brevity: 1.0 if concise (<10 lines), scales down for longer
        features["brevity"] = max(0.0, min(1.0, 1.0 - (line_count - 5) / 30.0))
        # honesty: 1.0 unless overclaim patterns found
        overclaim = len(re.findall(
            r"(?i)(proves?|establish(es|ed)|definitively|irrefutabl[ey])",
            response_text or "",
        ))
        features["honesty"] = max(0.0, 1.0 - overclaim * 0.25)
        # conviction: 1.0 unless hedging patterns dominate
        hedges = len(re.findall(
            r"(?i)(perhaps|maybe|i think|it seems|it appears|might be)",
            response_text or "",
        ))
        features["conviction"] = max(0.0, 1.0 - hedges * 0.15)

        # Outcome: 1.0 = perfect, reduced by each fired pattern
        if not fired:
            outcome = 1.0
        else:
            outcome = max(0.0, 1.0 - len(fired) * 0.2)

        nn.train(features, outcome)

        # Persist weights after training
        NN_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        nn.save(str(NN_WEIGHTS_PATH))
    except Exception as e:
        _log.debug("NN training failed: %s", e)


def _get_nn_weight_injection() -> str:
    """Get current NN weights formatted as a behavioral constraint string."""
    nn = _get_nn()
    if nn is None:
        return ""

    try:
        weight_parts = []
        for dim in BEHAVIORAL_DIMS:
            w = nn.weights.get(dim, 1.0)
            weight_parts.append(f"{dim}={w:.2f}")
        return f"[Behavioral weights: {', '.join(weight_parts)}]"
    except Exception:
        return ""


def _build_live_injection(fired: list[str]) -> str:
    """Build a prompt injection from fired patterns. Appended to system prompt in real-time."""
    lines = ["\n\n# LIVE CORRECTION (self-sculpt detected anti-patterns in your last response)"]
    for name in fired:
        if name in DETECTORS:
            _, instinct, works, trigger = DETECTORS[name]
            lines.append(f"YOUR LAST RESPONSE triggered: {name.replace('_', ' ')}.")
            lines.append(f"FIX NOW: {works}")
    lines.append("Apply these corrections to your NEXT response. Do not repeat the pattern.")

    # Include learned behavioral weights from the lattice NN
    nn_weights = _get_nn_weight_injection()
    if nn_weights:
        lines.append(f"\n# LEARNED BEHAVIORAL WEIGHTS (higher = allocate more attention)")
        lines.append(nn_weights)

    return "\n".join(lines)


def _save_scar(name: str, instinct: str, works: str, trigger: str, evidence: str) -> None:
    """Save a correction to memory. Idempotent — won't duplicate existing scars."""
    today = date.today().isoformat()
    filename = f"selfsculpt_{name}.md"
    filepath = MEMORY_DIR / filename

    # Don't duplicate — if this scar already exists, just update last_used
    if filepath.exists():
        content = filepath.read_text()
        content = re.sub(r"last_used: \d{4}-\d{2}-\d{2}", f"last_used: {today}", content)
        filepath.write_text(content)
        return

    # New scar
    content = f"""---
name: selfsculpt_{name}
description: Self-sculpt caught — {name.replace('_', ' ')}
type: feedback
last_used: {today}
origin: self_sculpt.py (real-time, zero tokens)
---

YOUR INSTINCT: {instinct}
WHAT ACTUALLY WORKS: {works}
TRIGGER: {trigger}
EVIDENCE: {evidence}
"""
    filepath.write_text(content)

    # Update index
    index_path = MEMORY_DIR / "MEMORY.md"
    if index_path.exists():
        index = index_path.read_text()
        pointer = f"- [{filename}]({filename}) — Self-sculpt: {name.replace('_', ' ')}"
        if filename not in index:
            # Add under earned scars section if it exists, else append
            if "## Earned scars" in index:
                index = index.replace(
                    "## Earned scars",
                    f"## Earned scars\n{pointer}",
                    1
                )
            else:
                index += f"\n{pointer}\n"
            index_path.write_text(index)
