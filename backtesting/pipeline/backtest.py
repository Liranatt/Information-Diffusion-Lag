"""Evaluation and backtest orchestration."""
import asyncio
import sys
from pathlib import Path

import pandas as pd

DEFAULT_PARQUET = Path("data/candidates.parquet")
RELEVANCE_COL = "feat_connection_strength"
from pipeline.data_loader import load_price_prob_paths

from pipeline.portfolio_manager import (
    cem_search,
    reward_sharpe_dd,
)
from pipeline.strategy import DEFAULT_POLICY, run_backtest


def print_split_results(tdf: pd.DataFrame):
    """Print performance stats by dataset split."""
    print("\n  split     n   mean_ret   win%   median   sharpe")
    print("  " + "-" * 50)

    if tdf.empty:
        return

    from pipeline.portfolio_manager import score_sharpe_per_day
    for sp in ("train", "val", "test", "WF_OOS"):
        s = tdf[tdf["split"] == sp] if sp != "WF_OOS" else tdf
        n = len(s)
        if n == 0:
            continue
        
        m = s["return_pct"].mean()
        w = (s["return_pct"] > 0).mean() * 100
        med = s["return_pct"].median()
        
        shp = score_sharpe_per_day(s)
        
        print(f"  {sp:5} {n:5d} {m:+8.2f}% {w:5.0f}% {med:+8.2f}% {shp:+8.3f}")

    if "exit_reason" in tdf.columns:
        counts = tdf["exit_reason"].value_counts().to_dict()
        print(f"\n  exit reasons: {counts}")


async def run(
    action: str = "backtest",
    parquet_path: str | None = None,
    from_db: bool = False,
):
    """Main backtest entry point."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Load candidates
    if from_db:
        from pipeline.data_loader import build_dataset_from_db
        df = await build_dataset_from_db(output_path=str(DEFAULT_PARQUET))
    else:
        path = parquet_path or str(DEFAULT_PARQUET)
        df = pd.read_parquet(path)

    df = df[df[RELEVANCE_COL].astype(float) > 0.5].copy()
    print(f"[{action}] loaded {len(df)} candidates (relevance > 0.5)")

    # Load price/prob paths from DB
    prices, probs = await load_price_prob_paths(df)

    if action == "optimize":
        print("\n" + "=" * 60)
        print("  CEM POLICY SEARCH (Sharpe + DD penalty)")
        print("=" * 60)
        best_policy = cem_search(
            df, prices, probs,
            reward_fn=reward_sharpe_dd,
            n_iter=10,
            pop_size=40,
            seed=42,
        )
        print("\n  Optimization complete. You can update DEFAULT_POLICY in strategy.py with these values.")
        
    elif action == "backtest":
        print("\n" + "=" * 60)
        print("  CEM BACKTEST (DEFAULT_POLICY)")
        print("=" * 60)
        tdf_baseline = run_backtest(df, prices, probs, DEFAULT_POLICY)
        print_split_results(tdf_baseline)
        
        # Save trades
        if not tdf_baseline.empty:
            out_path = Path("data/backtest_trades.csv")
            tdf_baseline.to_csv(out_path, index=False)
            print(f"\n  Trades saved to {out_path}")

    elif action == "walkforward":
        from pipeline.walkforward import create_expanding_wf_folds
        print("\n" + "=" * 60)
        print("  WALK-FORWARD OPTIMIZATION (CEM)")
        print("=" * 60)
        
        folds = create_expanding_wf_folds(df)
        print(f"\n  Created {len(folds)} expanding walk-forward folds.")
        
        all_oos_trades = []
        for f in folds:
            print(f"\n  [Fold {f['fold']}/{len(folds)}] Fit cutoff: {f['fit_cutoff'].date()}  Eval: {f['eval_start'].date()} to {f['eval_end'].date()}")
            
            best_policy = cem_search(
                f["fit_df"], prices, probs,
                reward_fn=reward_sharpe_dd,
                n_iter=5, # Reduced iterations for speed during WF
                pop_size=40,
                seed=42,
            )
            
            tdf_oos = run_backtest(f["eval_df"], prices, probs, best_policy)
            if not tdf_oos.empty:
                all_oos_trades.append(tdf_oos)
        
        if all_oos_trades:
            final_tdf = pd.concat(all_oos_trades, ignore_index=True)
            print("\n" + "=" * 60)
            print("  WALK-FORWARD OUT-OF-SAMPLE RESULTS")
            print("=" * 60)
            final_tdf["split"] = "WF_OOS"
            print_split_results(final_tdf)
            
            out_path = Path("data/wf_trades.csv")
            final_tdf.to_csv(out_path, index=False)
            print(f"\n  Walk-forward trades saved to {out_path}")
        else:
            print("\n  No OOS trades generated across any folds.")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backtest", "optimize", "walkforward"], help="Action to perform")
    parser.add_argument("--from-db", action="store_true", help="Rebuild dataset from DB")
    parser.add_argument("parquet", nargs="?", help="Path to parquet file")
    
    args, _ = parser.parse_known_args()
    
    asyncio.run(run(
        action=args.action,
        parquet_path=args.parquet,
        from_db=args.from_db,
    ))


if __name__ == "__main__":
    main()
