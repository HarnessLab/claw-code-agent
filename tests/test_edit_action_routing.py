"""(C) Code-edit operations route to HEAVY when code context is detected.

Pre-fix: _LIGHT_PATTERNS bundled file-modification verbs (rename, move,
copy, delete, remove, add a line, change X to) into the LIGHT tier.
A user typing "rename the foo function" got routed to Haiku, which
has noticeably weaker fidelity on whitespace/indentation in edit_file
operations than Sonnet.

Post-fix: when a LIGHT-edit pattern fires AND the user message also
contains code-context signals (function/class/method/module/file/
language extension/test_/line N), promote to HEAVY. Pure-read LIGHT
patterns (read/grep/list/show/cat) stay LIGHT regardless of code
context — those are genuinely cheap operations.

False-positive cost: "rename foo.txt to bar.txt" without code context
stays LIGHT. "delete the third item from the list" without code
context stays LIGHT. The promotion only fires on EDIT + CODE.
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


class TestEditActionRouting(unittest.TestCase):
    def test_rename_function_routes_to_heavy(self) -> None:
        # 'rename' is a LIGHT-edit verb; 'function' is a code-context
        # signal. Combination should promote to HEAVY.
        decision = _router().classify_turn('rename the foo function in main.py')
        self.assertEqual(decision.tier, Tier.HEAVY,
                         f'expected HEAVY for code edit; got {decision.tier} (reason={decision.reason!r})')

    def test_change_variable_in_file_routes_to_heavy(self) -> None:
        decision = _router().classify_turn('change the timeout variable in agent_runtime.py to 30')
        self.assertEqual(decision.tier, Tier.HEAVY)

    def test_delete_class_method_routes_to_heavy(self) -> None:
        decision = _router().classify_turn('delete the unused method in ToolRegistry class')
        self.assertEqual(decision.tier, Tier.HEAVY)

    def test_rename_plain_file_stays_light(self) -> None:
        # Plain file rename with no code context — LIGHT is correct.
        decision = _router().classify_turn('rename foo.txt to bar.txt')
        self.assertEqual(decision.tier, Tier.LIGHT,
                         f'expected LIGHT for non-code rename; got {decision.tier} (reason={decision.reason!r})')

    def test_remove_item_from_list_stays_light(self) -> None:
        # 'remove' is LIGHT-edit but 'list' here is data-list, not code-context.
        decision = _router().classify_turn('remove the third item from the list')
        # Word 'list' in light-pattern overlap; no code signal. Stays LIGHT.
        self.assertEqual(decision.tier, Tier.LIGHT)

    def test_pure_read_with_code_context_stays_light(self) -> None:
        # 'show' is a LIGHT-read verb; 'function' is code-context. But
        # reads don't need HEAVY's edit-fidelity — only edits do.
        decision = _router().classify_turn('show me the foo function in main.py')
        self.assertEqual(decision.tier, Tier.LIGHT,
                         f'pure read should stay LIGHT even with code context; '
                         f'got {decision.tier} (reason={decision.reason!r})')

    def test_grep_with_code_context_stays_light(self) -> None:
        decision = _router().classify_turn('grep for usages of MyClass in src/')
        self.assertEqual(decision.tier, Tier.LIGHT)

    def test_routing_reason_names_promotion(self) -> None:
        # When the promotion fires, the decision's reason must explicitly
        # say so — otherwise the audit log can't distinguish promoted
        # routes from naturally-heavy ones.
        decision = _router().classify_turn('rename the bar method')
        self.assertIn('edit', decision.reason.lower())
        self.assertIn('code', decision.reason.lower())

    def test_dot_extension_counts_as_code_context(self) -> None:
        for ext in ('.py', '.ts', '.js', '.go', '.rs', '.java'):
            decision = _router().classify_turn(f'rename the helper in main{ext}')
            self.assertEqual(
                decision.tier, Tier.HEAVY,
                f'extension {ext} should be code-context; got {decision.tier}',
            )

    def test_explicit_force_heavy_via_env_still_works(self) -> None:
        # The promotion shouldn't break the existing force-tier override.
        with patch.dict(os.environ, {'LATTI_FORCE_TIER': 'light'}):
            r = ModelRouter(
                config=RouterConfig(enabled=True, force_tier='light'),
                default_heavy_model='anthropic/claude-sonnet-4',
            )
            decision = r.classify_turn('rename the foo function')
        self.assertEqual(decision.tier, Tier.LIGHT, 'force_tier should still override promotion')


if __name__ == '__main__':
    unittest.main()
