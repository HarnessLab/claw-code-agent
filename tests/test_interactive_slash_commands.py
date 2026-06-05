from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.slash_commands import CommandContext, handle_command


def test_status_reports_state_machine_and_supervisor_modes() -> None:
    lines: list[str] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = SimpleNamespace(
            model_config=SimpleNamespace(model='test-model'),
            runtime_config=SimpleNamespace(cwd=Path(tmp_dir)),
        )
        ctx = CommandContext(
            agent=agent,
            active_session_id='sess_123',
            turn_count=2,
            cumulative_cost=0.25,
            cumulative_tokens=4096,
            use_tui=False,
            tui=None,
            tui_heal=None,
            output_func=lines.append,
            worker_supervisor_active=True,
        )

        with patch.dict(
            os.environ,
            {
                'LATTI_USE_STATE_MACHINE': '1',
                'LATTI_USE_LEGACY_LOOP': '0',
                'LATTI_USE_CHAT_SUPERVISOR': '1',
            },
            clear=False,
        ):
            result = handle_command('/status', ctx)

    output = '\n'.join(lines)
    assert result.exit_session is False
    assert 'state machine  on' in output
    assert 'supervisor     on' in output
    assert 'legacy loop    off' in output
