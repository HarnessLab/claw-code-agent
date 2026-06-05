"""
Cognitive OS — Orchestrator.

Wires the three layers together:
  1. Intent Router  → classify prompt → IntentManifest
  2. Forge          → generate K candidates
  3. Gauntlet       → validate each candidate → GauntletResult
  4. Selection      → pick min(G) survivor
  5. Reflective Mutator → if all dead, refine prompt and retry

This is the "Sovereign Cognitive OS" loop. It doesn't trust the LLM.
It trusts the Gauntlet.

Usage:
    from src.cognitive_os import CognitiveOS

    cos = CognitiveOS(client=my_openai_client, model="anthropic/claude-haiku-4.5")
    result = cos.run(prompt="Write a weekly schedule rotation that wraps Sunday to Monday")
    print(result.winner.extracted_code)
    print(f"Energy: {result.winner.total_energy:.3f}")
    print(f"Cycles: {result.cycles}")
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import intent_router as _ir
from . import gauntlet as _gauntlet
from . import forge as _forge


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CycleReport:
    """Report for one forge→gauntlet cycle."""
    cycle: int
    candidates_generated: int
    candidates_survived: int
    best_energy: float
    best_candidate_id: int
    mutated_prompt: Optional[str]  # None if no mutation needed


@dataclass
class COSResult:
    """Final result from the Cognitive OS."""
    winner: Optional[_gauntlet.GauntletResult]  # None if all cycles exhausted
    manifest: _ir.IntentManifest
    cycles: int
    cycle_reports: list[CycleReport]
    total_latency_ms: float
    exhausted: bool  # True if all cycles failed to produce a survivor

    @property
    def succeeded(self) -> bool:
        return self.winner is not None and self.winner.survived


# ---------------------------------------------------------------------------
# Reflective Mutator
# ---------------------------------------------------------------------------

def _build_mutation(
    original_prompt: str,
    failed_results: list[_gauntlet.GauntletResult],
    manifest: _ir.IntentManifest,
    cycle: int,
) -> str:
    """
    Build a refined prompt from the failure reasons of the previous cycle.

    This is the "Error Back-Propagation" step. We extract the most
    informative failure reasons and inject them as constraints into the
    next prompt.

    Real implementation — no fake "manifold distance" framing.
    """
    # Collect the most informative failure reasons
    failure_reasons: list[str] = []
    for result in failed_results:
        for wall in result.wall_results:
            if not wall.passed and wall.detail not in ("ok", "skipped (weight=0)"):
                failure_reasons.append(f"[{wall.wall}] {wall.detail}")

    if not failure_reasons:
        # No specific failures — just ask for a different approach
        return (
            f"{original_prompt}\n\n"
            f"[Attempt {cycle + 1}: Previous attempt failed validation. "
            f"Please provide a complete, syntactically correct implementation.]"
        )

    # Deduplicate and take the top 3 most informative
    seen = set()
    unique_reasons = []
    for r in failure_reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)
        if len(unique_reasons) >= 3:
            break

    correction_block = "\n".join(f"  - {r}" for r in unique_reasons)

    # Task-type specific guidance
    task_guidance = ""
    if manifest.task_type == _ir.TaskType.CYCLIC:
        task_guidance = (
            "\n  - Ensure modular arithmetic wraps correctly "
            "(e.g., (day + 1) % 7 for weekly cycles)"
        )
    elif manifest.task_type == _ir.TaskType.CONSTRAINT:
        task_guidance = (
            "\n  - Ensure all constraints are explicitly enforced with assertions or guards"
        )
    elif manifest.task_type == _ir.TaskType.DEBUG:
        task_guidance = (
            "\n  - Focus on the specific error; provide a minimal, complete fix"
        )

    return (
        f"{original_prompt}\n\n"
        f"[Attempt {cycle + 1}: Previous attempt failed with these issues:\n"
        f"{correction_block}{task_guidance}\n"
        f"Please address all of these in your implementation.]"
    )


# ---------------------------------------------------------------------------
# Cognitive OS
# ---------------------------------------------------------------------------

class CognitiveOS:
    """
    The Sovereign Cognitive OS.

    Runs the full forge→gauntlet→select→mutate loop.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        max_cycles: int = 3,
        system_prompt: str = "",
        verbose: bool = False,
    ):
        """
        client: OpenAICompatClient instance
        model: model identifier
        max_cycles: maximum forge→gauntlet cycles before giving up
        system_prompt: optional system prompt for the model
        verbose: print cycle reports to stdout
        """
        self.forge = _forge.Forge(client=client, model=model)
        self.model = model
        self.max_cycles = max_cycles
        self.system_prompt = system_prompt
        self.verbose = verbose

    def run(
        self,
        prompt: str,
        extra_context: str = "",
    ) -> COSResult:
        """
        Run the full cognitive loop.

        Returns a COSResult. Check result.succeeded before using result.winner.
        """
        t0 = time.monotonic()

        # Step 1: Classify intent
        manifest = _ir.classify(prompt)
        if self.verbose:
            print(f"[COS] Intent: {manifest.task_type.value} | {manifest.rationale}")
            print(f"[COS] K={manifest.k_candidates} | T={manifest.temperature} | Z3={manifest.z3_enabled}")

        cycle_reports: list[CycleReport] = []
        current_prompt = prompt
        all_results: list[_gauntlet.GauntletResult] = []

        for cycle in range(self.max_cycles):
            if self.verbose:
                print(f"\n[COS] Cycle {cycle + 1}/{self.max_cycles}")

            # Step 2: Forge — generate K candidates
            candidates = self.forge.generate(
                prompt=current_prompt,
                manifest=manifest,
                system_prompt=self.system_prompt,
                extra_context=extra_context,
            )

            if self.verbose:
                print(f"[COS]   Generated {len(candidates)} candidates")

            # Step 3: Gauntlet — validate each candidate
            cycle_results: list[_gauntlet.GauntletResult] = []
            for candidate in candidates:
                result = _gauntlet.run(
                    candidate_id=candidate.candidate_id,
                    raw_text=candidate.raw_text,
                    prompt=prompt,  # always score against original prompt
                    manifest=manifest,
                )
                cycle_results.append(result)
                all_results.append(result)

                if self.verbose:
                    status = "✓" if result.survived else "✗"
                    walls = " | ".join(
                        f"{w.wall}={w.energy_contribution:.2f}" for w in result.wall_results
                    )
                    print(f"[COS]   [{status}] candidate {candidate.candidate_id}: G={result.total_energy:.3f} | {walls}")

            # Step 4: Select min(G) survivor
            survivors = [r for r in cycle_results if r.survived]

            if survivors:
                winner = min(survivors, key=lambda r: r.total_energy)
                latency_ms = (time.monotonic() - t0) * 1000

                cycle_reports.append(CycleReport(
                    cycle=cycle,
                    candidates_generated=len(candidates),
                    candidates_survived=len(survivors),
                    best_energy=winner.total_energy,
                    best_candidate_id=winner.candidate_id,
                    mutated_prompt=None,
                ))

                if self.verbose:
                    print(f"\n[COS] ✓ Winner: candidate {winner.candidate_id} | G={winner.total_energy:.3f}")

                return COSResult(
                    winner=winner,
                    manifest=manifest,
                    cycles=cycle + 1,
                    cycle_reports=cycle_reports,
                    total_latency_ms=latency_ms,
                    exhausted=False,
                )

            # Step 5: All dead — reflective mutation
            failed = [r for r in cycle_results if not r.survived]
            mutated_prompt = _build_mutation(
                original_prompt=prompt,
                failed_results=failed,
                manifest=manifest,
                cycle=cycle,
            )

            cycle_reports.append(CycleReport(
                cycle=cycle,
                candidates_generated=len(candidates),
                candidates_survived=0,
                best_energy=min(
                    (r.total_energy for r in cycle_results if not math.isinf(r.total_energy)),
                    default=math.inf
                ),
                best_candidate_id=-1,
                mutated_prompt=mutated_prompt,
            ))

            if self.verbose:
                print(f"[COS]   All candidates dead. Mutating prompt for cycle {cycle + 2}...")

            current_prompt = mutated_prompt

        # All cycles exhausted
        latency_ms = (time.monotonic() - t0) * 1000

        # Return the best non-infinite result we found, even if it didn't fully pass
        finite_results = [r for r in all_results if not math.isinf(r.total_energy)]
        best_partial = min(finite_results, key=lambda r: r.total_energy) if finite_results else None

        if self.verbose:
            print(f"\n[COS] ✗ All {self.max_cycles} cycles exhausted.")
            if best_partial:
                print(f"[COS]   Best partial: G={best_partial.total_energy:.3f}")

        return COSResult(
            winner=best_partial,
            manifest=manifest,
            cycles=self.max_cycles,
            cycle_reports=cycle_reports,
            total_latency_ms=latency_ms,
            exhausted=True,
        )


# ---------------------------------------------------------------------------
# Standalone runner (for testing without the full agent stack)
# ---------------------------------------------------------------------------

def run_standalone(
    prompt: str,
    base_url: str,
    api_key: str,
    model: str = "anthropic/claude-haiku-4.5",
    max_cycles: int = 3,
    verbose: bool = True,
) -> COSResult:
    """
    Run the Cognitive OS without the full agent stack.
    Useful for testing and benchmarking.
    """
    # Minimal mock client that carries base_url and api_key
    class _MinimalClient:
        def __init__(self, base_url: str, api_key: str):
            self.base_url = base_url
            self.api_key = api_key

    client = _MinimalClient(base_url=base_url, api_key=api_key)
    cos = CognitiveOS(client=client, model=model, max_cycles=max_cycles, verbose=verbose)
    return cos.run(prompt)
