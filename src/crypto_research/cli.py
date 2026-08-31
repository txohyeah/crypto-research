"""crypto-research 统一 CLI（Binance 技术分析命令）。

用法示例：
    crypto-research sync --symbol BTCUSDT --timeframe 1d --lookback-bars 600
    crypto-research rate --symbol BTCUSDT --timeframe 1d --confirm-timeframe 4h
    crypto-research chart --symbol BTCUSDT --timeframe 1d --output /tmp/btc.png
    crypto-research backtest --symbol BTCUSDT --timeframe 4h
    crypto-research paper-trade --symbol BTCUSDT --timeframe 4h

输出为单行 JSON：{"ok":true,...} / {"ok":false,"error_code":...,"error":...}
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Callable

from .config import load_settings
from .crypto import (
    CryptoRepository,
    run_crypto_chart,
    run_crypto_rate,
    run_crypto_sync,
    run_crypto_trade_chart,
)
from .crypto_signals import CryptoSignalConfig, run_crypto_signal_sync
from .crypto_trading import TRADING_STRATEGIES, CryptoTradingConfig, run_crypto_backtest, run_crypto_paper_trade
from .exceptions import StockResearchError


def _print_ok(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


def _print_error(error: BaseException) -> int:
    payload: dict[str, Any] = {"ok": False, "error": str(error)}
    if isinstance(error, StockResearchError):
        payload["error_code"] = error.error_code
        if getattr(error, "hint", None):
            payload["hint"] = error.hint
    else:
        payload["error_code"] = "internal"
    if "--debug" in sys.argv:
        traceback.print_exc(file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 1


def _cli_progress(message: str) -> None:
    print(f"[crypto-research] {message}", file=sys.stderr, flush=True)


def _add_crypto_trading_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="spot")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--strategy", choices=sorted(TRADING_STRATEGIES), default="golden_bull")
    parser.add_argument("--initial-capital", type=float, default=5000.0)
    parser.add_argument("--profit-runner-trigger-pct", type=float, default=0.30)
    parser.add_argument("--profit-drawdown-stop-pct", type=float, default=0.30)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-history-bars", type=int, default=120)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crypto-research")
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    crypto_parser = subparsers.add_parser("crypto")
    crypto_subparsers = crypto_parser.add_subparsers(dest="crypto_command", required=True)

    crypto_sync = crypto_subparsers.add_parser("sync")
    crypto_sync.add_argument("--exchange", default="binance")
    crypto_sync.add_argument("--market-type", default="spot")
    crypto_sync.add_argument("--symbol", required=True)
    crypto_sync.add_argument("--timeframe", required=True)
    crypto_sync.add_argument("--lookback-bars", type=int, default=600)
    crypto_sync.add_argument("--binance-base-url", default="https://api.binance.com")
    crypto_sync.add_argument("--env")
    crypto_sync.add_argument("--database")

    crypto_rate = crypto_subparsers.add_parser("rate")
    crypto_rate.add_argument("--exchange", default="binance")
    crypto_rate.add_argument("--market-type", default="spot")
    crypto_rate.add_argument("--symbol", required=True)
    crypto_rate.add_argument("--timeframe", default="1d")
    crypto_rate.add_argument("--lookback-bars", type=int, default=180)
    crypto_rate.add_argument("--confirm-timeframe")
    crypto_rate.add_argument("--confirm-lookback-bars", type=int, default=600)
    crypto_rate.add_argument("--output")
    crypto_rate.add_argument("--env")
    crypto_rate.add_argument("--database")

    crypto_chart = crypto_subparsers.add_parser("chart")
    crypto_chart.add_argument("--exchange", default="binance")
    crypto_chart.add_argument("--market-type", default="spot")
    crypto_chart.add_argument("--symbol", required=True)
    crypto_chart.add_argument("--timeframe", required=True)
    crypto_chart.add_argument("--lookback-bars", type=int, default=300)
    crypto_chart.add_argument("--indicators", default="ma,golden_bull")
    crypto_chart.add_argument("--output")
    crypto_chart.add_argument("--env")
    crypto_chart.add_argument("--database")

    crypto_trade_chart = crypto_subparsers.add_parser("trade-chart")
    crypto_trade_chart.add_argument("--exchange", default="binance")
    crypto_trade_chart.add_argument("--market-type", default="spot")
    crypto_trade_chart.add_argument("--symbol", required=True)
    crypto_trade_chart.add_argument("--timeframe", required=True)
    crypto_trade_chart.add_argument("--lookback-bars", type=int, default=300)
    crypto_trade_chart.add_argument("--start")
    crypto_trade_chart.add_argument("--end")
    crypto_trade_chart.add_argument("--trades", required=True)
    crypto_trade_chart.add_argument("--output")
    crypto_trade_chart.add_argument("--env")
    crypto_trade_chart.add_argument("--database")

    crypto_signal_sync = crypto_subparsers.add_parser("signal-sync")
    crypto_signal_sync.add_argument("--exchange", default="binance")
    crypto_signal_sync.add_argument("--market-type", default="spot")
    crypto_signal_sync.add_argument("--symbol", required=True)
    crypto_signal_sync.add_argument("--timeframe", default="4h")
    crypto_signal_sync.add_argument("--lookback-bars", type=int, default=720)
    crypto_signal_sync.add_argument("--min-history-bars", type=int, default=120)
    crypto_signal_sync.add_argument("--progress-every", type=int, default=50)
    crypto_signal_sync.add_argument("--refresh-all", action="store_true")
    crypto_signal_sync.add_argument("--env")
    crypto_signal_sync.add_argument("--database")

    crypto_backtest = crypto_subparsers.add_parser("backtest")
    _add_crypto_trading_args(crypto_backtest)
    crypto_backtest.add_argument("--lookback-bars", type=int, default=720)
    crypto_backtest.add_argument("--output-dir")
    crypto_backtest.add_argument("--env")
    crypto_backtest.add_argument("--database")

    crypto_paper = crypto_subparsers.add_parser("paper-trade")
    _add_crypto_trading_args(crypto_paper)
    crypto_paper.add_argument("--lookback-bars", type=int, default=240)
    crypto_paper.add_argument("--output-dir")
    crypto_paper.add_argument("--env")
    crypto_paper.add_argument("--database")

    return parser


def _crypto(args: argparse.Namespace) -> dict[str, Any]:
    if args.crypto_command == "sync":
        return run_crypto_sync(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback_bars=args.lookback_bars,
            base_url=args.binance_base_url,
            env_path=args.env,
            database=args.database,
        )
    if args.crypto_command == "rate":
        return run_crypto_rate(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback_bars=args.lookback_bars,
            confirm_timeframe=args.confirm_timeframe,
            confirm_lookback_bars=args.confirm_lookback_bars,
            output_path=args.output,
            env_path=args.env,
            database=args.database,
        )
    if args.crypto_command == "chart":
        return run_crypto_chart(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback_bars=args.lookback_bars,
            indicators=args.indicators,
            output_path=args.output,
            env_path=args.env,
            database=args.database,
        )
    if args.crypto_command == "trade-chart":
        return run_crypto_trade_chart(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback_bars=args.lookback_bars,
            start=args.start,
            end=args.end,
            trades_path=args.trades,
            output_path=args.output,
            env_path=args.env,
            database=args.database,
        )
    if args.crypto_command == "signal-sync":
        config = CryptoSignalConfig.build(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            min_history_bars=args.min_history_bars,
        )
        return run_crypto_signal_sync(
            repository=CryptoRepository(load_settings(args.env, args.database)),
            config=config,
            lookback_bars=args.lookback_bars,
            refresh_all=args.refresh_all,
            progress=_cli_progress,
            progress_every=args.progress_every,
        )
    if args.crypto_command in {"backtest", "paper-trade"}:
        config = CryptoTradingConfig.build(
            exchange=args.exchange,
            market_type=args.market_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            initial_capital=args.initial_capital,
            profit_runner_trigger_pct=args.profit_runner_trigger_pct,
            profit_drawdown_stop_pct=args.profit_drawdown_stop_pct,
            fee_rate=args.fee_rate,
            slippage_bps=args.slippage_bps,
            min_history_bars=args.min_history_bars,
            strategy=args.strategy,
        )
        repository = CryptoRepository(load_settings(args.env, args.database))
        if args.crypto_command == "backtest":
            return run_crypto_backtest(
                repository=repository,
                config=config,
                lookback_bars=args.lookback_bars,
                output_dir=args.output_dir,
            )
        return run_crypto_paper_trade(
            repository=repository,
            config=config,
            lookback_bars=args.lookback_bars,
            output_dir=args.output_dir,
        )
    raise StockResearchError("Missing crypto command")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "crypto":
            return _print_ok(_crypto(args))
        raise StockResearchError("Missing command")
    except StockResearchError as exc:
        return _print_error(exc)
    except Exception as exc:  # defensive CLI boundary
        if "--debug" in sys.argv:
            traceback.print_exc(file=sys.stderr)
        return _print_error(StockResearchError(str(exc)))


if __name__ == "__main__":
    sys.exit(main())