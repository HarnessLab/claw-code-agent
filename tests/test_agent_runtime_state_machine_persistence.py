from __future__ import annotations

from pathlib import Path

from src.agent_runtime import LocalCodingAgent
from src.agent_state_machine import Observation, State
from src.agent_types import (
    AgentPermissions,
    AgentRuntimeConfig,
    AgentRunResult,
    AssistantTurn,
    ModelConfig,
    ModelPricing,
    UsageStats,
)
from src.session_store import StoredAgentSession, load_agent_session


def _make_agent(tmp_path: Path, session_dir: Path) -> LocalCodingAgent:
    return LocalCodingAgent(
        model_config=ModelConfig(
            model='gpt-4o-mini',
            api_key='test-key',
            base_url='http://localhost:0/unused',
            pricing=ModelPricing(),
        ),
        runtime_config=AgentRuntimeConfig(
            cwd=tmp_path,
            session_directory=session_dir,
            permissions=AgentPermissions(
                allow_file_write=True,
                allow_shell_commands=False,
            ),
        ),
    )


def test_run_persists_typed_state_into_stored_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    session_dir = tmp_path / '.port_sessions' / 'agent'
    agent = _make_agent(tmp_path, session_dir)
    monkeypatch.setattr(agent, '_check_rotation_gate', lambda result: None)

    def fake_complete(messages, tools, *, output_schema=None, model_override=None):
        return AssistantTurn(
            content='persist typed state',
            finish_reason='stop',
            usage=UsageStats(input_tokens=4, output_tokens=2),
        )

    monkeypatch.setattr(agent.client, 'complete', fake_complete)

    result = agent.run('persist this turn')
    stored = load_agent_session(result.session_id or '', directory=session_dir)

    assert stored.typed_state['session_id'] == result.session_id
    assert stored.typed_state['last_observation']['payload']['content'] == 'persist typed state'


def test_resume_restores_persisted_typed_state_before_prompt_execution(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('LATTI_USE_STATE_MACHINE', '1')
    session_dir = tmp_path / '.port_sessions' / 'agent'
    agent = _make_agent(tmp_path, session_dir)
    seen: dict[str, object] = {}

    monkeypatch.setattr(agent, '_accumulate_usage', lambda result: None)
    monkeypatch.setattr(agent, '_finalize_managed_agent', lambda result: None)

    def fake_run_prompt(prompt, *, base_session, session_id, scratchpad_directory, existing_file_history):
        seen['state'] = agent._sm_state
        return AgentRunResult(
            final_output='ok',
            turns=0,
            tool_calls=0,
            transcript=(),
            session_id=session_id,
            scratchpad_directory=str(scratchpad_directory) if scratchpad_directory else None,
        )

    monkeypatch.setattr(agent, '_run_prompt', fake_run_prompt)

    persisted_state = State.fresh(
        session_id='stored_session_456',
        available_tools=('read_file',),
        budget_usd=1.5,
    ).next_turn(
        observation=Observation(
            action_id='act_1',
            kind='success',
            payload={'content': 'restored from disk'},
        )
    ).to_dict()

    stored = StoredAgentSession(
        session_id='stored_session_456',
        model_config={},
        runtime_config={},
        system_prompt_parts=('system',),
        user_context={},
        system_context={},
        messages=(),
        turns=0,
        tool_calls=0,
        usage={},
        total_cost_usd=0.0,
        file_history=(),
        budget_state={},
        plugin_state={},
        typed_state=persisted_state,
        scratchpad_directory=None,
    )

    agent.resume('continue', stored)

    assert isinstance(seen['state'], State)
    assert seen['state'].session_id == 'stored_session_456'
    assert seen['state'].last_observation is not None
    assert seen['state'].last_observation.payload['content'] == 'restored from disk'
