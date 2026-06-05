"""Verdict→action wiring: 'replan' verdict injects a State-layer reminder.

Today (pre-fix), evaluator verdicts are threaded into
state.runtime['last_verdict'] but no controller acts on them. The
ConsecutiveErrorEvaluator says 'replan' on the LLM's error step and
the loop just keeps going — the verdict is descriptive telemetry, not
prescriptive governance.

This test pins the v2 close: when last_verdict='replan', the
RuntimeLoopController augments the next llm_call action's messages
payload with a typed system-reminder from the State layer telling the
model the last step was flagged. The reminder is single-shot —
last_verdict is cleared after consumption so the next turn doesn't
double-inject.
"""
from __future__ import annotations

import unittest

from src.agent_state_machine import State
from src.state_machine_controllers import RuntimeLoopController


def _runtime_state(runtime: dict) -> State:
    """Build a minimal State whose runtime dict has the fields the controller reads."""
    return State(
        session_id='sess_test',
        turn_id=1,
        runtime=runtime,
    )


class TestReplanVerdictWiring(unittest.TestCase):
    def test_no_verdict_returns_normal_llm_action(self) -> None:
        ctrl = RuntimeLoopController()
        st = _runtime_state({
            'awaiting_model': True,
            'next_llm_action': {
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [],
            },
        })
        decision = ctrl.pick(st)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.chose.kind, 'llm_call')
        # Messages should pass through unchanged
        self.assertEqual(
            decision.chose.payload['messages'],
            [{'role': 'user', 'content': 'hi'}],
        )

    def test_replan_verdict_injects_reminder(self) -> None:
        ctrl = RuntimeLoopController()
        st = _runtime_state({
            'awaiting_model': True,
            'next_llm_action': {
                'messages': [{'role': 'user', 'content': 'do something'}],
                'tools': [],
            },
            'last_verdict': 'replan',
        })
        decision = ctrl.pick(st)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.chose.kind, 'llm_call')
        msgs = decision.chose.payload['messages']
        # The injected reminder must be present
        all_text = ' '.join(
            m.get('content', '') if isinstance(m.get('content'), str) else ''
            for m in msgs
        )
        self.assertIn(
            'replan',
            all_text.lower(),
            f'replan reminder missing from injected messages: {msgs!r}',
        )
        # Original user message preserved
        roles_seen = [m['role'] for m in msgs]
        self.assertIn('user', roles_seen)
        # Decision rationale flags this as verdict-driven
        self.assertIn('replan', decision.rationale.lower())

    def test_continue_verdict_does_not_inject(self) -> None:
        ctrl = RuntimeLoopController()
        st = _runtime_state({
            'awaiting_model': True,
            'next_llm_action': {
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [],
            },
            'last_verdict': 'continue',
        })
        decision = ctrl.pick(st)
        self.assertEqual(
            decision.chose.payload['messages'],
            [{'role': 'user', 'content': 'hi'}],
        )

    def test_escalate_verdict_halts(self) -> None:
        # 'escalate' is the State layer saying "stop the loop, this needs
        # human attention". Controller returns None to halt.
        ctrl = RuntimeLoopController()
        st = _runtime_state({
            'awaiting_model': True,
            'next_llm_action': {
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [],
            },
            'last_verdict': 'escalate',
        })
        decision = ctrl.pick(st)
        self.assertIsNone(decision, 'escalate verdict must halt the loop')

    def test_replan_does_not_inject_when_pending_tool_calls(self) -> None:
        # If there are pending tool_calls, we're not awaiting the model;
        # the reminder is for LLM steps only. Pending tool execution wins.
        ctrl = RuntimeLoopController()
        st = _runtime_state({
            'awaiting_model': False,
            'pending_tool_calls': [{'name': 'bash', 'arguments': {'command': 'ls'}, 'id': 't1'}],
            'last_verdict': 'replan',
        })
        decision = ctrl.pick(st)
        self.assertEqual(decision.chose.kind, 'tool_call')


if __name__ == '__main__':
    unittest.main()
