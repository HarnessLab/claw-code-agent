from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.background_runtime import BackgroundSessionRecord, BackgroundSessionRuntime
from src.main import (
    _build_runtime_config,
    _build_agent,
    _run_agent_chat_loop,
    _run_background_worker,
    _render_worker_event_to_tui,
    build_parser,
    main,
)
from src.agent_types import AgentRunResult
from src.tui_supervisor import read_worker_events


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
                        '--model',
                        'test-model',
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

    def test_agent_chat_loop_can_use_worker_runner(self) -> None:
        recorded_results: list[str] = []
        recorded_lines: list[str] = []
        worker_calls: list[tuple[str, str | None]] = []
        prompts = iter(['Second prompt', '/exit'])

        def _input(prompt: str) -> str:
            return next(prompts)

        def _output(line: str) -> None:
            recorded_lines.append(line)

        def _result_printer(result, *, show_transcript: bool) -> None:  # noqa: ANN001
            recorded_results.append(result.final_output)

        def _worker_runner(prompt: str, resume_session_id: str | None):
            worker_calls.append((prompt, resume_session_id))
            session_id = resume_session_id or 'worker_session_1'
            return AgentRunResult(
                final_output=f'worker:{prompt}',
                turns=1,
                tool_calls=0,
                transcript=(),
                session_id=session_id,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            parser = build_parser()
            args = parser.parse_args(
                [
                    'agent-chat',
                    'First prompt',
                    '--model',
                    'test-model',
                    '--cwd',
                    str(workspace),
                ]
            )
            agent = _build_agent(args)
            exit_code = _run_agent_chat_loop(
                agent,
                initial_prompt=args.prompt,
                resume_session_id=None,
                show_transcript=False,
                input_func=_input,
                output_func=_output,
                result_printer=_result_printer,
                worker_runner=_worker_runner,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            worker_calls,
            [('First prompt', None), ('Second prompt', 'worker_session_1')],
        )
        self.assertEqual(
            recorded_results,
            ['worker:First prompt', 'worker:Second prompt'],
        )

    def test_background_worker_writes_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / 'background'
            runtime = BackgroundSessionRuntime(root)
            background_id = 'bg_events'
            record = BackgroundSessionRecord(
                background_id=background_id,
                pid=123,
                prompt='prompt',
                workspace_cwd=str(Path(tmp_dir)),
                model='test-model',
                mode='chat',
                status='running',
                log_path=str(runtime.log_path(background_id)),
                record_path=str(runtime.record_path(background_id)),
                started_at='2026-04-29T00:00:00+00:00',
                command=('python3', '-m', 'src.main'),
            )
            runtime.save_record(record)

            class FakeAgent:
                runtime_event_sink = None

                def run(self, prompt: str) -> AgentRunResult:
                    assert prompt == 'prompt'
                    assert self.runtime_event_sink is not None
                    self.runtime_event_sink({'type': 'content_delta', 'delta': 'live'})
                    return AgentRunResult(
                        final_output='live',
                        turns=1,
                        tool_calls=0,
                        transcript=(),
                        events=({'type': 'content_delta', 'delta': 'live'},),
                        session_id='sess_live',
                    )

            args = SimpleNamespace(
                background_root=str(root),
                background_id=background_id,
                prompt='prompt',
                resume_session_id=None,
                show_transcript=False,
            )

            with patch('src.main._build_agent', return_value=FakeAgent()):
                exit_code = _run_background_worker(args)

            events, _ = read_worker_events(root, background_id)

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, [{'type': 'content_delta', 'delta': 'live'}])

    def test_worker_state_machine_events_render_to_tui_info(self) -> None:
        calls: list[tuple[str, str]] = []

        class FakeTui:
            @staticmethod
            def info(text: str) -> None:
                calls.append(('info', text))

        renderer = _render_worker_event_to_tui(
            {
                'type': 'state_machine_decision',
                'action_kind': 'llm_call',
                'rationale': 'rule_fired: runtime_query_model',
            },
            tui=FakeTui,
            stream_renderer=None,
        )
        renderer = _render_worker_event_to_tui(
            {
                'type': 'session_checkpoint',
                'session_id': 'abcdef1234567890',
                'typed_state_checkpointed': True,
            },
            tui=FakeTui,
            stream_renderer=renderer,
        )

        self.assertIsNone(renderer)
        self.assertEqual(
            calls,
            [
                ('info', 'state-machine: llm_call - runtime_query_model'),
                ('info', 'checkpoint: abcdef123456 typed-state saved'),
            ],
        )

    def test_agent_chat_defaults_to_supervisor_for_interactive_tty(self) -> None:
        fake_agent = SimpleNamespace()

        def _worker_runner(prompt: str, resume_session_id: str | None) -> AgentRunResult:
            return AgentRunResult(
                final_output='unused',
                turns=0,
                tool_calls=0,
                transcript=(),
                session_id=resume_session_id,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(os.environ, {'LATTI_BOOT': '0'}, clear=False):
                with patch('src.main._build_agent', return_value=fake_agent):
                    with patch(
                        'src.main._build_background_chat_worker_runner',
                        return_value=_worker_runner,
                    ) as build_worker_runner:
                        with patch(
                            'src.main._run_agent_chat_loop',
                            return_value=0,
                        ) as run_chat_loop:
                            with patch('sys.stdin.isatty', return_value=True):
                                with patch('sys.stdout.isatty', return_value=True):
                                    exit_code = main(
                                        ['agent-chat', 'hello', '--cwd', tmp_dir]
                                    )

        self.assertEqual(exit_code, 0)
        build_worker_runner.assert_called_once()
        self.assertIs(run_chat_loop.call_args.kwargs['worker_runner'], _worker_runner)

    def test_agent_chat_supervisor_has_escape_hatch(self) -> None:
        fake_agent = SimpleNamespace()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    'LATTI_BOOT': '0',
                    'LATTI_USE_CHAT_SUPERVISOR': '0',
                    'LATTI_FORCE_CHAT_SUPERVISOR': '1',
                },
                clear=False,
            ):
                with patch('src.main._build_agent', return_value=fake_agent):
                    with patch(
                        'src.main._build_background_chat_worker_runner',
                    ) as build_worker_runner:
                        with patch(
                            'src.main._run_agent_chat_loop',
                            return_value=0,
                        ) as run_chat_loop:
                            with patch('sys.stdin.isatty', return_value=True):
                                with patch('sys.stdout.isatty', return_value=True):
                                    exit_code = main(
                                        ['agent-chat', 'hello', '--cwd', tmp_dir]
                                    )

        self.assertEqual(exit_code, 0)
        build_worker_runner.assert_not_called()
        self.assertIsNone(run_chat_loop.call_args.kwargs['worker_runner'])

    def test_agent_chat_supervisor_can_be_forced_for_non_tty_smoke(self) -> None:
        fake_agent = SimpleNamespace()

        def _worker_runner(prompt: str, resume_session_id: str | None) -> AgentRunResult:
            return AgentRunResult(
                final_output='unused',
                turns=0,
                tool_calls=0,
                transcript=(),
                session_id=resume_session_id,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {'LATTI_BOOT': '0', 'LATTI_FORCE_CHAT_SUPERVISOR': '1'},
                clear=False,
            ):
                with patch('src.main._build_agent', return_value=fake_agent):
                    with patch(
                        'src.main._build_background_chat_worker_runner',
                        return_value=_worker_runner,
                    ) as build_worker_runner:
                        with patch(
                            'src.main._run_agent_chat_loop',
                            return_value=0,
                        ) as run_chat_loop:
                            with patch('sys.stdin.isatty', return_value=False):
                                with patch('sys.stdout.isatty', return_value=False):
                                    exit_code = main(
                                        ['agent-chat', 'hello', '--cwd', tmp_dir]
                                    )

        self.assertEqual(exit_code, 0)
        build_worker_runner.assert_called_once()
        self.assertIs(run_chat_loop.call_args.kwargs['worker_runner'], _worker_runner)

    def test_parser_accepts_remote_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['remote-profiles', '--cwd', '.'])
        self.assertEqual(args.command, 'remote-profiles')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_account_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['account-profiles', '--cwd', '.'])
        self.assertEqual(args.command, 'account-profiles')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_ask_user_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['ask-history', '--cwd', '.'])
        self.assertEqual(args.command, 'ask-history')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_search_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['search', 'repo query', '--cwd', '.', '--provider', 'local-search'])
        self.assertEqual(args.command, 'search')
        self.assertEqual(args.query, 'repo query')
        self.assertEqual(args.provider, 'local-search')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_worktree_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['worktree-exit', '--action', 'remove', '--discard-changes', '--cwd', '.'])
        self.assertEqual(args.command, 'worktree-exit')
        self.assertEqual(args.action, 'remove')
        self.assertTrue(args.discard_changes)
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_workflow_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['workflow-run', 'review', '--arguments-json', '{"path":"src"}', '--cwd', '.'])
        self.assertEqual(args.command, 'workflow-run')
        self.assertEqual(args.workflow_name, 'review')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_remote_trigger_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['trigger-run', 'nightly', '--body-json', '{"depth":"quick"}', '--cwd', '.'])
        self.assertEqual(args.command, 'trigger-run')
        self.assertEqual(args.trigger_id, 'nightly')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_mcp_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['mcp-tools', '--cwd', '.', '--server', 'remote'])
        self.assertEqual(args.command, 'mcp-tools')
        self.assertEqual(args.server, 'remote')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_daemon_subcommands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['daemon', 'ps'])
        self.assertEqual(args.command, 'daemon')
        self.assertEqual(args.daemon_command, 'ps')

    def test_parser_accepts_config_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['config-get', 'review.mode', '--cwd', '.'])
        self.assertEqual(args.command, 'config-get')
        self.assertEqual(args.key_path, 'review.mode')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_lsp_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['lsp-definition', 'sample.py', '4', '12', '--cwd', '.'])
        self.assertEqual(args.command, 'lsp-definition')
        self.assertEqual(args.file_path, 'sample.py')
        self.assertEqual(args.line, 4)
        self.assertEqual(args.character, 12)
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_token_budget_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['token-budget', '--cwd', '.'])
        self.assertEqual(args.command, 'token-budget')
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_agents_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['agents', 'reviewer', '--cwd', '.', '--all'])
        self.assertEqual(args.command, 'agents')
        self.assertEqual(args.agent_type, 'reviewer')
        self.assertTrue(args.all)
        self.assertEqual(args.cwd, '.')

    def test_parser_accepts_agent_management_commands(self) -> None:
        parser = build_parser()
        create_args = parser.parse_args(
            ['agents-create', 'reviewer', '--cwd', '.', '--description', 'Review code', '--prompt', 'Inspect diffs']
        )
        self.assertEqual(create_args.command, 'agents-create')
        self.assertEqual(create_args.agent_type, 'reviewer')
        self.assertEqual(create_args.description, 'Review code')

        update_args = parser.parse_args(['agents-update', 'reviewer', '--cwd', '.', '--source', 'auto'])
        self.assertEqual(update_args.command, 'agents-update')
        self.assertEqual(update_args.source, 'auto')

        delete_args = parser.parse_args(['agents-delete', 'reviewer', '--cwd', '.', '--source', 'project'])
        self.assertEqual(delete_args.command, 'agents-delete')
        self.assertEqual(delete_args.source, 'project')

    def test_main_can_create_and_delete_agent_definition(self) -> None:
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            with patch.dict('os.environ', {'HOME': home_dir}):
                exit_code = main(
                    [
                        'agents-create',
                        'reviewer',
                        '--cwd',
                        str(workspace),
                        '--description',
                        'Review code carefully',
                        '--prompt',
                        'Inspect code and summarize risks.',
                    ]
                )
                self.assertEqual(exit_code, 0)
                self.assertTrue((workspace / '.claude' / 'agents' / 'reviewer.md').exists())

                exit_code = main(
                    [
                        'agents-delete',
                        'reviewer',
                        '--cwd',
                        str(workspace),
                        '--source',
                        'project',
                    ]
                )
                self.assertEqual(exit_code, 0)
                self.assertFalse((workspace / '.claude' / 'agents' / 'reviewer.md').exists())

    def test_parser_accepts_team_runtime_commands(self) -> None:
        parser = build_parser()
        args = parser.parse_args(['team-create', 'reviewers', '--member', 'alice', '--cwd', '.'])
        self.assertEqual(args.command, 'team-create')
        self.assertEqual(args.team_name, 'reviewers')
        self.assertEqual(args.member, ['alice'])
        self.assertEqual(args.cwd, '.')
