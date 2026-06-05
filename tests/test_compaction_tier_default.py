"""Compaction tier default — HEAVY, with LATTI_COMPACTION_TIER override.

Pre-fix: compaction calls always routed to Tier.LIGHT (Haiku 4.5,
$1/$5 per M tokens). This was reasonable cost-wise (~$0.045 per
compaction) but Haiku's structured-summary quality on the 9-section
compact prompt is meaningfully weaker than Sonnet's. Every subsequent
turn sees that summary; quality compounds.

Post-fix: compaction routes to HEAVY by default ($3/$15 → ~$0.13 per
compaction, $0.08 extra). Override via LATTI_COMPACTION_TIER=light
for cost-sensitive runs. Other compaction tier values fall back to
HEAVY.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.model_router import ModelRouter, RouterConfig, Tier


def _router() -> ModelRouter:
    return ModelRouter(
        config=RouterConfig(enabled=True),
        default_heavy_model='anthropic/claude-sonnet-4',
    )


class TestCompactionTierDefault(unittest.TestCase):
    def test_compaction_default_routes_to_heavy(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LATTI_COMPACTION_TIER', None)
            r = _router()
            decision = r.classify_turn('', is_compaction=True)
        self.assertEqual(decision.tier, Tier.HEAVY)
        self.assertIn('compaction', decision.reason.lower())

    def test_compaction_with_light_override_routes_to_light(self) -> None:
        with patch.dict(os.environ, {'LATTI_COMPACTION_TIER': 'light'}):
            r = _router()
            decision = r.classify_turn('', is_compaction=True)
        self.assertEqual(decision.tier, Tier.LIGHT)

    def test_compaction_with_heavy_override_explicit(self) -> None:
        with patch.dict(os.environ, {'LATTI_COMPACTION_TIER': 'heavy'}):
            r = _router()
            decision = r.classify_turn('', is_compaction=True)
        self.assertEqual(decision.tier, Tier.HEAVY)

    def test_compaction_with_garbage_override_falls_back_to_heavy(self) -> None:
        # Defensive: invalid value defaults to heavy (the safer choice
        # for summary quality), not LIGHT.
        with patch.dict(os.environ, {'LATTI_COMPACTION_TIER': 'banana'}):
            r = _router()
            decision = r.classify_turn('', is_compaction=True)
        self.assertEqual(decision.tier, Tier.HEAVY)

    def test_non_compaction_calls_unaffected_by_override(self) -> None:
        # The override only affects compaction-classified turns; normal
        # heuristic routing still applies to everything else.
        with patch.dict(os.environ, {'LATTI_COMPACTION_TIER': 'light'}):
            r = _router()
            # A heavy-pattern user message should still go heavy
            decision = r.classify_turn('refactor the architecture and design the new API')
        self.assertEqual(decision.tier, Tier.HEAVY)


if __name__ == '__main__':
    unittest.main()
