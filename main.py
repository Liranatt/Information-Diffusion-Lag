"""Entry point for the trading pipeline.

Usage:
    python main.py scan                     # Scan Polymarket + Gemini evaluate
    python main.py backtest                 # Backtest with CEM default policy
    python main.py optimize                 # Run CEM policy search
    python main.py walkforward              # Run expanding walk-forward optimization
    python main.py backtest --from-db       # Build dataset from DB, then backtest
    python main.py live --paper             # Live scan + paper trading
"""
from __future__ import annotations

import asyncio
import sys


def cmd_scan():
    from pipeline.evaluator import scan_and_evaluate
    results = asyncio.run(scan_and_evaluate())
    relevant = [r for r in results if r.question_relevance >= 0.5 and r.assets]
    print(f"\n  {len(results)} markets evaluated, {len(relevant)} relevant with assets:")
    for r in relevant:
        syms = ", ".join(a["symbol"] for a in r.assets[:5])
        print(f"    rel={r.question_relevance:.2f}  {syms:30s}  {r.market.question[:60]}")


def cmd_pipeline(action: str, args: list[str]):
    from pipeline.backtest import main as bt_main
    sys.argv = ["backtest", action] + args
    bt_main()


async def cmd_live_paper():
    from pipeline.evaluator import scan_and_evaluate
    from pipeline.data_loader import NUM_FEATURES, CAT_FEATURES
    from pipeline.strategy import DEFAULT_POLICY, simulate_one
    from pipeline.executor import IBExecutor

    print("[live] scanning Polymarket for new markets...")
    evaluated = await scan_and_evaluate()
    relevant = [r for r in evaluated if r.question_relevance >= 0.5 and r.assets]
    if not relevant:
        print("[live] no new relevant markets found")
        return

    print(f"[live] {len(relevant)} relevant markets found")

    # TODO: build features for live candidates, run strategy, submit orders
    # For now, just report what was found
    executor = IBExecutor()
    try:
        await executor.connect()
        summary = await executor.get_account_summary()
        positions = await executor.get_positions()
        print(f"[live] account: {summary.get('NetLiquidation', 'N/A')}")
        print(f"[live] {len(positions)} open positions")
    except Exception as e:
        print(f"[live] IB connection failed: {e}")
        print("[live] make sure IB Gateway is running on port 4002")
    finally:
        await executor.disconnect()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "scan":
        cmd_scan()
    elif cmd in ("backtest", "optimize", "walkforward"):
        cmd_pipeline(cmd, rest)
    elif cmd == "live" and "--paper" in rest:
        asyncio.run(cmd_live_paper())
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
