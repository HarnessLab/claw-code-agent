from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.main import _build_runtime_config, _build_agent, _run_agent_chat_loop, build_parser


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode('utf-8')

    def __enter__(self) -> 'FakeHTTPResponse':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def make_urlopen_side_effect(responses: list[dict[str, object]]):
    queued = [FakeHTTPResponse(payload) for payload in responses]

    def _fake_urlopen(request_obj, timeout=None):  # noqa: ANN001
        return queued.pop(0)

    return _fake_urlopen


class MainCliTests(unittest.TestCase):
    def test_build_runtime_config_parses_model_and_session_budget_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                'agent',
                'Summarize the repo',
                '--cwd',
                '.',
                '--max-model-calls',
                '3',
                '--max-session-turns',
                '5',
            ]
        )
        runtime_config = _build_runtime_config(args)
        self.assertEqual(runtime_config.budget_config.max_model_calls, 3)
        self.assertEqual(runtime_config.budget_config.max_session_turns, 5)

    def test_agent_chat_loop_runs_multiple_turns_and_reuses_session(self) -> None:
        responses = [
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'First chat reply.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2},
            },
            {
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': 'Second chat reply.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 6, 'completion_tokens': 2},
            },
        ]
        recorded_results: list[str] = []
        recorded_lines: list[str] = []
        prompts = iter(['Second prompt', '/exit'])

        def _input(prompt: str) -> str:
            return next(prompts)

        def _output(line: str) -> None:
            recorded_lines.append(line)

        def _result_printer(result, *, show_transcript: bool) -> None:  # noqa: ANN001
            recorded_results.append(result.final_output)

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            session_dir = workspace / '.port_sessions' / 'agent'
            with patch(
                'src.openai_compat.request.urlopen',
                side_effect=make_urlopen_side_effect(responses),
            ):
                parser = build_parser()
                args = parser.parse_args(
                    [
                        'agent-chat',
                        'First prompt',
                        '--cwd',
                        str(workspace),
                    ]
                )
                agent = _build_agent(args)
                agent.runtime_config = replace(
                    agent.runtime_config,
                    session_directory=session_dir,
                )
                exit_code = _run_agent_chat_loop(
                    agent,
                    initial_prompt=args.prompt,
                    resume_session_id=None,
                    show_transcript=False,
                    input_func=_input,
                    output_func=_output,
                    result_printer=_result_printer,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(recorded_results, ['First chat reply.', 'Second chat reply.'])
        self.assertIn('# Agent Chat', recorded_lines)
        self.assertIn('chat_ended=user_exit', recorded_lines)
