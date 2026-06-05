"""
Scar Router: Route problems to models based on past scars.

When a new problem arrives, the router searches for similar past problems
and applies their lessons to choose the best model and configuration.
"""

from __future__ import annotations

from typing import Optional
from .scar_index import ScarIndex, Scar


def _detect_intensity(problem: str) -> str:
    """Inline intensity detection — no external dependency needed.

    Returns one of: trivial | standard | hard | research
    Mirrors the heuristics in ModelRouter.classify_turn but self-contained
    so scar_router has zero coupling to model_router.
    """
    p = problem.lower()
    heavy_signals = [
        'debug', 'refactor', 'architect', 'design', 'optimize', 'race condition',
        'memory leak', 'deadlock', 'concurrency', 'async', 'performance',
        'security', 'vulnerability', 'algorithm', 'complex', 'investigate',
        'why is', 'why does', 'explain why', 'entire', 'overhaul', 'rewrite',
    ]
    light_signals = [
        'rename', 'format', 'lint', 'typo', 'comment', 'docstring',
        'add import', 'remove import', 'sort', 'whitespace',
    ]
    heavy = sum(1 for s in heavy_signals if s in p)
    light = sum(1 for s in light_signals if s in p)
    if heavy >= 2:
        return 'hard'
    if heavy >= 1:
        return 'standard'
    if light >= 1:
        return 'trivial'
    return 'standard'


class ScarRouter:
    """Routes problems to models based on past scars."""

    def __init__(self, scar_index: Optional[ScarIndex] = None):
        self.scar_index = scar_index or ScarIndex()

    def route_problem(
        self,
        problem_description: str,
        default_intensity: Optional[str] = None,
    ) -> dict:
        """Route a problem to a model based on past scars.

        Returns dict with:
          - model: Recommended model (or None if no scar match)
          - intensity: Problem intensity
          - scar_matched: Scar ID that influenced the decision (or None)
          - lesson: The lesson from the matched scar (or None)
          - lessons_context: Multi-line string of all relevant lessons for
                             injection into the system prompt
          - reasoning: Explanation of the routing decision
        """
        similar_scars = self.scar_index.find_similar_scars(
            problem_description,
            max_results=5,
        )

        # Build lessons context from ALL similar scars (not just the best one)
        # so the model sees the full history, not just the winner.
        lessons_context = self._build_lessons_context(similar_scars)

        if not similar_scars:
            intensity = default_intensity or _detect_intensity(problem_description)
            return {
                'model': None,  # No scar match → let model_router decide
                'intensity': intensity,
                'scar_matched': None,
                'lesson': None,
                'lessons_context': '',
                'reasoning': f'No similar scars found. Deferring to model_router.',
            }

        best_scar = self._select_best_scar(similar_scars)

        if best_scar is None:
            # All similar scars were failures — still useful: avoid those models
            intensity = default_intensity or _detect_intensity(problem_description)
            return {
                'model': None,  # Let model_router decide, but inject lessons
                'intensity': intensity,
                'scar_matched': None,
                'lesson': None,
                'lessons_context': lessons_context,
                'reasoning': 'Similar scars all failed. Injecting failure lessons; deferring model choice.',
            }

        model = best_scar.model_used
        intensity = self._intensity_for_model(model)

        return {
            'model': model,
            'intensity': intensity,
            'scar_matched': best_scar.id,
            'lesson': best_scar.lesson,
            'lessons_context': lessons_context,
            'reasoning': (
                f'Scar {best_scar.id} shows {best_scar.model_used} '
                f'succeeded on similar problem. Using it.'
            ),
        }

    def _build_lessons_context(self, scars: list[Scar]) -> str:
        """Build a multi-line lessons string for system prompt injection.

        Format:
          Past experience on similar problems:
          - [success] openai/o1: "o1 succeeded on async race condition."
          - [failure] claude-sonnet-4.6: "Sonnet failed on low-level async debugging."
        """
        if not scars:
            return ''
        lines = ['Past experience on similar problems:']
        for scar in scars:
            tag = f'[{scar.outcome}]'
            lines.append(f'  - {tag} {scar.model_used}: "{scar.lesson}"')
        return '\n'.join(lines)

    def _select_best_scar(self, scars: list[Scar]) -> Optional[Scar]:
        """Select the best scar: most recent success."""
        successful = [s for s in scars if s.outcome == 'success']
        if successful:
            successful.sort(key=lambda s: s.timestamp, reverse=True)
            return successful[0]
        return None

    def _intensity_for_model(self, model: str) -> str:
        if 'o1' in model or 'o3' in model:
            return 'hard'
        return 'standard'

    def record_outcome(
        self,
        problem_description: str,
        model_used: str,
        cost: float,
        outcome: str,
        session_id: str,
        reasoning_tokens: int = 0,
    ) -> Scar:
        """Record the outcome of a problem as a scar."""
        if outcome == 'success':
            lesson = f'{model_used} succeeded on this type of problem.'
        elif outcome == 'failure':
            lesson = f'{model_used} failed on this type of problem. Try a more capable model.'
        else:
            lesson = f'{model_used} partially solved this. May need extended thinking or more turns.'

        return self.scar_index.record_scar(
            problem_description=problem_description,
            model_used=model_used,
            cost=cost,
            outcome=outcome,
            lesson=lesson,
            session_id=session_id,
            reasoning_tokens=reasoning_tokens,
        )
