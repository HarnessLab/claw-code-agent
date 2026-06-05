"""(b) Replan reminder includes the actual last-observation error text.

Pre-fix, the replan reminder was a static string ("the evaluator
flagged the previous step"). The LLM only knew what specifically went
wrong because the conversation context already had the error in it
(tool output messages). Without that prior error in context, the
reminder was content-free.

Post-fix: when the State layer writes last_verdict='replan' to the
runtime channel, it ALSO writes last_error_text extracted from
state.last_observation.payload['error']. RuntimeLoopController reads
both and the injected reminder now contains the specific failure
reason. The State layer's notice is now substantively informative,
not just a prod.
"""
from __future__ import annotations

import unittest

from src.agent_state_machine import State
from src.state_machine_controllers import RuntimeLoopController, _inject_replan_reminder


class TestErrorAwareReplanReminder(unittest.TestCase):
    def test_inject_helper_includes_error_text(self) -> None:
        payload = {
            'messages': [{'role': 'user', 'content': 'hi'}],
            'tools': [],
        }
        out = _inject_replan_reminder(payload, last_error_text='Permission denied: /etc/passwd')
        all_text = ' '.join(
            m.get('content', '') for m in out['messages']
            if isinstance(m.get('content'), str)
        )
        self.assertIn('Permission denied', all_text)
        self.assertIn('/etc/passwd', all_text)

    def test_inject_helper_omits_when_no_error_text(self) -> None:
        # Backwards compatibility: caller may pass empty string. The
        # reminder still appears (as before) but without an error block.
        payload = {
            'messages': [{'role': 'user', 'content': 'hi'}],
            'tools': [],
        }
        out = _inject_replan_reminder(payload, last_error_text='')
        all_text = ' '.join(
            m.get('content', '') for m in out['messages']
            if isinstance(m.get('content'), str)
        )
        self.assertIn('replan', all_text.lower())
        self.assertIn('STATE-LAYER NOTICE', all_text)

    def test_controller_reads_error_text_from_runtime(self) -> None:
        ctrl = RuntimeLoopController()
        st = State(
            session_id='sess', turn_id=1,
            runtime={
                'awaiting_model': True,
                'next_llm_action': {
                    'messages': [{'role': 'user', 'content': 'try again'}],
                    'tools': [],
                },
                'last_verdict': 'replan',
                'last_error_text': 'EACCES: permission denied, open /tmp/lock',
            },
        )
        decision = ctrl.pick(st)
        msgs = decision.chose.payload['messages']
        all_text = ' '.join(
            m.get('content', '') for m in msgs
            if isinstance(m.get('content'), str)
        )
        self.assertIn('EACCES', all_text)
        self.assertIn('permission denied', all_text.lower())

    def test_controller_handles_missing_error_text_gracefully(self) -> None:
        ctrl = RuntimeLoopController()
        st = State(
            session_id='sess', turn_id=1,
            runtime={
                'awaiting_model': True,
                'next_llm_action': {
                    'messages': [{'role': 'user', 'content': 'hi'}],
                    'tools': [],
                },
                'last_verdict': 'replan',
                # last_error_text intentionally absent
            },
        )
        decision = ctrl.pick(st)
        # Still injects the reminder, just without specific error text.
        msgs = decision.chose.payload['messages']
        all_text = ' '.join(
            m.get('content', '') for m in msgs
            if isinstance(m.get('content'), str)
        )
        self.assertIn('STATE-LAYER NOTICE', all_text)


class TestEvaluateAfterStepThreadsErrorText(unittest.TestCase):
    """When verdict='replan' is threaded, the last error text from
    state.last_observation must also be written to runtime channel.
    """

    def test_evaluate_threads_error_text_when_replan(self) -> None:
        import tempfile
        from pathlib import Path
        from src.agent_runtime import LocalCodingAgent
        from src.agent_state_machine import Observation
        from src.agent_types import AgentRuntimeConfig, ModelConfig

        with tempfile.TemporaryDirectory() as tmp:
            agent = LocalCodingAgent(
                model_config=ModelConfig(model='test-model'),
                runtime_config=AgentRuntimeConfig(cwd=Path(tmp)),
            )
            agent._ensure_state_machine_runner()
            from src.agent_state_machine import State
            err_obs = Observation(
                action_id='a1', kind='error',
                payload={'error': 'EACCES: permission denied, open /etc/sudoers'},
            )
            agent._sm_state = State(
                session_id='s', turn_id='t1',
                last_observation=err_obs,
                budget_remaining_usd=10.0,
            )
            agent._evaluate_state_after_step()
            self.assertEqual(
                agent._sm_state.runtime.get('last_verdict'), 'replan',
            )
            self.assertIn(
                'EACCES',
                agent._sm_state.runtime.get('last_error_text', ''),
            )


if __name__ == '__main__':
    unittest.main()
