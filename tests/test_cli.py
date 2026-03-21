"""Tests for CLI commands."""

from types import SimpleNamespace

from app.cli import main
from app.domain.models import AccountExecutionResult, AccountSwitchTestResult, ExecutionStatus


def test_cli_test_accounts_calls_executor(monkeypatch, capsys) -> None:
    result = AccountSwitchTestResult(
        run_id="run-123",
        status="success",
        accounts=[
            AccountExecutionResult(
                account_alias="main_1",
                status=ExecutionStatus.SUCCESS,
                order_submit_detected=False,
                technical_log=["account switch verified"],
            )
        ],
    )

    monkeypatch.setattr(
        "app.cli.build_context",
        lambda config_dir, env_file: SimpleNamespace(executor=SimpleNamespace(test_accounts=lambda: result)),
    )

    exit_code = main(["test-accounts"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"run_id": "run-123"' in captured.out
