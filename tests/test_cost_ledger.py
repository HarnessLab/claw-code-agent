from __future__ import annotations

from pathlib import Path

from src.agent_types import UsageStats
from src.cost_ledger import log_api_call


def test_log_api_call_ignores_directory_creation_error(monkeypatch) -> None:
    def boom_mkdir(self, parents=False, exist_ok=False):
        raise PermissionError('sandbox denied mkdir')

    monkeypatch.setattr(Path, 'mkdir', boom_mkdir)

    log_api_call(
        'claude-3-5-sonnet',
        UsageStats(input_tokens=10, output_tokens=5),
    )


def test_log_api_call_ignores_permission_error(monkeypatch) -> None:
    monkeypatch.setattr(Path, 'mkdir', lambda self, parents=False, exist_ok=False: None)

    def boom_open(*args, **kwargs):
        raise PermissionError('sandbox denied write')

    monkeypatch.setattr('builtins.open', boom_open)

    log_api_call(
        'claude-3-5-sonnet',
        UsageStats(input_tokens=10, output_tokens=5),
    )
