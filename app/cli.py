"""Command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.bootstrap import build_context
from app.domain.models import RuntimeMode, TradeIntent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--config-dir", default="config", help="Directory with YAML config files.")
    parser.add_argument("--env-file", default=".env", help="Optional .env file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a single image and stop before execution.")
    analyze.add_argument("image_path")

    run_once = subparsers.add_parser("run-once", help="Run a single image through the configured pipeline.")
    run_once.add_argument("image_path")

    execute = subparsers.add_parser("execute", help="Execute a prepared trade_intent JSON.")
    execute.add_argument("trade_intent_json")

    subparsers.add_parser("test-accounts", help="Run a test-only full pass over configured MT5 accounts.")
    subparsers.add_parser("run-bot", help="Run Telegram ingestion.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the CLI command."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    config_dir = Path(args.config_dir)
    env_file = Path(args.env_file) if args.env_file else None
    context = build_context(config_dir=config_dir, env_file=env_file)

    if args.command == "run-bot":
        if context.telegram_capture is None:
            parser.error("telegram adapter is disabled in config")
        context.telegram_capture.run(context.orchestrator.process_capture_event)
        return 0

    if args.command == "execute":
        payload = Path(args.trade_intent_json).read_text(encoding="utf-8")
        trade_intent = TradeIntent.model_validate_json(payload)
        summary = context.orchestrator.execute_trade_intent(trade_intent)
        print(summary.model_dump_json(indent=2))
        return 0

    if args.command == "test-accounts":
        result = context.executor.test_accounts()
        print(result.model_dump_json(indent=2))
        return 0

    if args.command == "analyze":
        capture_event = context.local_capture.capture(
            Path(args.image_path),
            source_name="local_file",
            allow_duplicate=True,
        )
        result = context.analyzer.analyze(capture_event)
        print(result.model_dump_json(indent=2))
        return 0

    capture_event = context.local_capture.capture(Path(args.image_path), source_name="local_file")
    summary = context.orchestrator.process_capture_event(capture_event)
    print(summary.model_dump_json(indent=2))
    return 0
