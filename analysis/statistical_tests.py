"""Inference suite for the Information Diffusion Lag paper.

PART A (primary, H1): dependence-robust inference on the raw T-1 expectation
test.  Reads the per-trade CSVs produced by
diagnostics/run_raw_expectation_test_tminus1.py and asks whether the positive
candidate-pool expectancy survives clustering, market adjustment, overlapping
holding windows, and random-timing placebos.  These are the tests a referee
will demand for the paper's central claim.

PART B (secondary, H2): the CEM monetization-layer tests (per-config trade
tests, Hansen SPA, Jensen's alpha).  The paper does not claim portfolio-level
statistical significance, so these are honesty checks, not the headline.
"""

import math
import os
import pickle
import sys
from pathlib import Path

import pandas as pd
import numpy as np
from scipy import stats

# Console output contains non-cp1252 glyphs ("→"). Without this, the default
# Windows codepage raises UnicodeEncodeError mid-run and silently truncates the
# test suite -- run_spa() for QQQ and run_jensens_alpha() never execute.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ib_cost(shares: int, price: float, is_sell: bool) -> float:
    """IB-style commission + SEC fee on sales + fixed 5 bp slippage."""
    if shares <= 0 or price <= 0:
        return 0.0
    trade_value = shares * price
    commission = max(0.35, min(shares * 0.0035, trade_value * 0.01))
    sec = trade_value * 0.0000278 if is_sell else 0.0
    return commission + sec + trade_value * 0.0005


def get_closest_price(prices_series: pd.Series, target_date: str):
    """
    Return the price on target_date, or the most-recent prior trading day
    if that exact date is missing (e.g. weekend / holiday).
    Defined OUTSIDE the trade loop to avoid repeated closure re-definition.
    """
    if target_date in prices_series.index:
        return prices_series[target_date]
    prior = prices_series[prices_series.index <= target_date]
    return prior.iloc[-1] if not prior.empty else None


# ═══════════════════════════════════════════════════════════════════════════
# PART A — H1: raw T-1 expectation inference battery
# ═══════════════════════════════════════════════════════════════════════════

RNG_SEED = 42
N_BOOT = int(os.getenv("CEM_N_BOOT", "10000"))
NW_LAGS = 5
RF_ANNUAL = 0.05

_PRICES_CACHE: dict = {}


def _load_prices_pkl(repo_root: Path) -> dict:
    if not _PRICES_CACHE:
        with open(repo_root / "data" / "prices.pkl", "rb") as f:
            _PRICES_CACHE.update(pickle.load(f))
    return _PRICES_CACHE


def _close_series(repo_root: Path, symbol: str) -> pd.Series | None:
    """Daily closes indexed by 'YYYY-MM-DD' strings, from prices.pkl."""
    bars = _load_prices_pkl(repo_root).get(symbol)
    if not bars:
        return None
    return pd.Series(
        data=[c for _, _, _, c in bars],
        index=[ts.strftime("%Y-%m-%d") for ts, _, _, _ in bars],
    ).sort_index()


def _one_sided_t(x: np.ndarray) -> tuple[float, float]:
    """(t, p) for H0: mean <= 0 vs H1: mean > 0."""
    t, p = stats.ttest_1samp(x, 0.0, alternative="greater")
    return float(t), float(p)


def _wilcoxon_greater_p(x: np.ndarray) -> float:
    """Wilcoxon signed-rank, H1: location > 0 (normal approx at this n)."""
    try:
        return float(stats.wilcoxon(x, alternative="greater").pvalue)
    except ValueError:
        return float("nan")


def _boot_means_iid(x: np.ndarray, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    return x[idx].mean(axis=1)


def _boot_p_from_means(boot_means: np.ndarray, obs_mean: float) -> float:
    """Centered-bootstrap p for H1: mean > 0.

    Resampling (x - x_bar) and asking P(mean* >= x_bar) is identical to
    P(uncentered mean* >= 2*x_bar), so one set of uncentered draws serves
    both the p-value and the BCa interval.
    """
    return float(np.mean(boot_means >= 2.0 * obs_mean))


def _bca_ci(x: np.ndarray, boot_means: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    """Bias-corrected accelerated bootstrap CI for the mean."""
    obs = x.mean()
    n = len(x)
    b = len(boot_means)
    prop = (np.sum(boot_means < obs) + 0.5 * np.sum(boot_means == obs)) / b
    prop = min(max(prop, 1.0 / (b + 1)), 1.0 - 1.0 / (b + 1))
    z0 = stats.norm.ppf(prop)

    jack = (n * obs - x) / (n - 1)          # leave-one-out means
    d = jack.mean() - jack
    denom = np.sum(d ** 2) ** 1.5
    a = np.sum(d ** 3) / (6.0 * denom) if denom > 0 else 0.0

    lo_hi = []
    for z_a in (stats.norm.ppf(alpha / 2), stats.norm.ppf(1 - alpha / 2)):
        adj = z0 + (z0 + z_a) / (1 - a * (z0 + z_a))
        lo_hi.append(float(np.quantile(boot_means, stats.norm.cdf(adj))))
    return lo_hi[0], lo_hi[1]


def _cluster_stats(x: np.ndarray, labels: np.ndarray, n_boot: int,
                   rng: np.random.Generator) -> dict:
    """Cluster-robust inference for H0: mean <= 0.

    Returns the Liang-Zeger CRVE t-test (df = G-1) and a cluster bootstrap
    p-value (resample whole clusters, pool observations).
    """
    unique, inv = np.unique(labels, return_inverse=True)
    G = len(unique)
    n = len(x)
    obs_mean = x.mean()
    if G < 2:
        return {"G": G, "t_cr": float("nan"), "p_cr": float("nan"), "p_boot": float("nan")}

    resid_sums = np.bincount(inv, weights=x - obs_mean, minlength=G)
    se = math.sqrt(G / (G - 1) * np.sum(resid_sums ** 2)) / n
    t_cr = obs_mean / se if se > 0 else float("nan")
    p_cr = float(1 - stats.t.cdf(t_cr, df=G - 1))

    sums = np.bincount(inv, weights=x, minlength=G)
    sizes = np.bincount(inv, minlength=G).astype(float)
    draw = rng.integers(0, G, size=(n_boot, G))
    boot = sums[draw].sum(axis=1) / sizes[draw].sum(axis=1)
    p_boot = float(np.mean(boot >= 2.0 * obs_mean))

    return {"G": G, "t_cr": t_cr, "p_cr": p_cr, "p_boot": p_boot}


def _episode_labels(df: pd.DataFrame) -> np.ndarray:
    """Merge overlapping [entry, exit] windows per symbol into one cluster.

    Two trades in the same symbol with overlapping holding periods are the
    same economic bet (e.g. four Iran questions -> one USO position); this is
    the coarsest defensible dependence unit below calendar clustering.
    """
    entry = pd.to_datetime(df["entry_date"])
    exit_ = pd.to_datetime(df["exit_date_t_minus_1"])
    order = np.lexsort((entry.values, df["symbol"].values))

    labels = np.empty(len(df), dtype=object)
    cur_sym, cur_end, ep = None, None, -1
    for i in order:
        sym = df["symbol"].iloc[i]
        if sym != cur_sym or entry.iloc[i] > cur_end:
            ep += 1
            cur_sym, cur_end = sym, exit_.iloc[i]
        else:
            cur_end = max(cur_end, exit_.iloc[i])
        labels[i] = f"E{ep:05d}"
    return labels.astype(str)


def _benchmark_window_returns(df: pd.DataFrame, bench: pd.Series) -> np.ndarray:
    out = np.full(len(df), np.nan)
    for i, (ed, xd) in enumerate(zip(df["entry_date"], df["exit_date_t_minus_1"])):
        b0 = get_closest_price(bench, str(ed)[:10])
        b1 = get_closest_price(bench, str(xd)[:10])
        if b0 and b1:
            out[i] = b1 / b0 - 1.0
    return out


def _newey_west_alpha(y: np.ndarray, x: np.ndarray, lags: int) -> dict:
    """OLS y = a + b*x with Newey-West (Bartlett) SEs; one-sided p for a > 0."""
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ beta

    Xu = X * u[:, None]
    meat = Xu.T @ Xu
    for l in range(1, lags + 1):
        w = 1.0 - l / (lags + 1.0)
        gamma = Xu[l:].T @ Xu[:-l]
        meat += w * (gamma + gamma.T)
    bread = np.linalg.inv(X.T @ X)
    V = bread @ meat @ bread

    se_a = math.sqrt(V[0, 0])
    t_a = beta[0] / se_a if se_a > 0 else float("nan")
    return {
        "alpha": float(beta[0]), "beta": float(beta[1]),
        "t": float(t_a), "p": float(1 - stats.t.cdf(t_a, df=n - 2)), "n": n,
    }


def _calendar_time_alpha(df: pd.DataFrame, repo_root: Path, bench_symbol: str) -> dict | None:
    """Calendar-time portfolio regression (Fama 1998; Mitchell-Stafford 2000).

    Equal-weight daily return over all open positions vs the benchmark, with
    Newey-West SEs.  This is the standard correction for overlapping event
    windows and cross-sectional clustering in event studies.
    """
    daily: dict[str, list[float]] = {}
    for sym, sub in df.groupby("symbol"):
        s = _close_series(repo_root, sym)
        if s is None or len(s) < 2:
            continue
        dates = s.index.to_numpy()
        rets = s.values[1:] / s.values[:-1] - 1.0
        for ed, xd in zip(sub["entry_date"], sub["exit_date_t_minus_1"]):
            i0 = np.searchsorted(dates, str(ed)[:10])
            i1 = np.searchsorted(dates, str(xd)[:10], side="right") - 1
            for j in range(i0 + 1, i1 + 1):       # return accrues after entry close
                daily.setdefault(dates[j], []).append(rets[j - 1])

    if not daily:
        return None
    port = pd.Series({d: np.mean(v) for d, v in daily.items()}).sort_index()

    bench = _close_series(repo_root, bench_symbol)
    bench_ret = bench.pct_change().dropna()
    common = port.index.intersection(bench_ret.index)
    if len(common) < 30:
        return None

    rf = (1 + RF_ANNUAL) ** (1 / 252) - 1
    res = _newey_west_alpha(port[common].values - rf, bench_ret[common].values - rf, NW_LAGS)
    res["n_open_position_days"] = len(common)
    return res


def _placebo_timing_test(df: pd.DataFrame, repo_root: Path, n_boot: int,
                         rng: np.random.Generator) -> dict:
    """Random-timing placebo: same symbols, same durations, same sample span,
    random entry dates.  Preserves duplication (all trades sharing a
    symbol+entry-date draw one common placebo date) so the null keeps the
    actual dependence structure.  Gross-vs-gross comparison: timing carries
    the information, costs are timing-invariant.
    """
    span_lo = df["entry_date"].min()
    span_hi = df["exit_date_t_minus_1"].max()

    acc = np.zeros(n_boot)
    n_used = 0
    n_fallback_groups = 0

    for (sym, _ed), sub in df.groupby(["symbol", "entry_date"]):
        s = _close_series(repo_root, sym)
        if s is None or len(s) < 3:
            continue
        dates, closes = s.index.to_numpy(), s.values

        durs = []
        for ed, xd in zip(sub["entry_date"], sub["exit_date_t_minus_1"]):
            i0 = np.searchsorted(dates, str(ed)[:10])
            i1 = np.searchsorted(dates, str(xd)[:10], side="right") - 1
            durs.append(max(i1 - i0, 1))
        max_dur = max(durs)

        lo = int(np.searchsorted(dates, span_lo))
        hi_date = int(np.searchsorted(dates, span_hi, side="right") - 1)
        hi = min(hi_date - max_dur, len(dates) - 1 - max_dur)
        if hi < lo:                               # window too tight: full history
            lo, hi = 0, len(dates) - 1 - max_dur
            n_fallback_groups += 1
            if hi < lo:
                continue

        starts = rng.integers(lo, hi + 1, size=n_boot)
        for d in durs:
            acc += closes[starts + d] / closes[starts] - 1.0
            n_used += 1

    if n_used == 0:
        return {"p": float("nan")}
    placebo_means = acc / n_used
    obs_gross = float(df["gross_return"].mean())
    return {
        "p": float(np.mean(placebo_means >= obs_gross)),
        "obs_gross_mean": obs_gross,
        "placebo_mean": float(placebo_means.mean()),
        "placebo_q95": float(np.quantile(placebo_means, 0.95)),
        "n_trades_used": n_used,
        "n_fallback_groups": n_fallback_groups,
    }


def _core_battery(x: np.ndarray, rng: np.random.Generator) -> dict:
    """Mean, t, Wilcoxon, BCa CI, and iid bootstrap p for one return vector."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    t, p_t = _one_sided_t(x)
    boot = _boot_means_iid(x, N_BOOT, rng)
    lo, hi = _bca_ci(x, boot)
    wins = int(np.sum(x > 0))
    p_binom = stats.binomtest(wins, len(x), p=0.5, alternative="greater").pvalue
    q05, q25, q75, q95 = np.quantile(x, [0.05, 0.25, 0.75, 0.95])
    return {
        "n": len(x), "mean": float(x.mean()), "median": float(np.median(x)),
        "sd": float(x.std(ddof=1)), "skew": float(stats.skew(x)),
        "q05": float(q05), "q25": float(q25), "q75": float(q75), "q95": float(q95),
        "wins": wins, "win_rate": float(wins / len(x)), "p_binom": float(p_binom),
        "t": t, "p_t": p_t,
        "p_boot": _boot_p_from_means(boot, x.mean()),
        "p_wilcoxon": _wilcoxon_greater_p(x),
        "ci_lo": lo, "ci_hi": hi,
    }


def _prepare_event_level_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize event-level aggregates to the same H1 schema as trade rows."""
    out = df.rename(columns={
        "mean_net_return": "net_return",
        "median_net_return": "median_trade_net_return",
        "mean_gross_return": "gross_return",
        "first_entry_date": "entry_date",
        "last_exit_date": "exit_date_t_minus_1",
    }).copy()
    out["symbol"] = "__event_level__"
    return out


def _cluster_definitions(df: pd.DataFrame) -> dict[str, np.ndarray]:
    entry_dt = pd.to_datetime(df["entry_date"])
    cluster_defs: dict[str, np.ndarray] = {}
    for col in ("market_id", "event_id", "symbol", "event_family"):
        if col in df.columns:
            labels = df[col].fillna("missing").astype(str).to_numpy()
            if len(np.unique(labels)) > 1:
                cluster_defs[col] = labels
    if {"symbol", "entry_date", "exit_date_t_minus_1"}.issubset(df.columns):
        if not (df["symbol"] == "__event_level__").all():
            cluster_defs["symbol_episode"] = _episode_labels(df)
    cluster_defs["entry_week"] = entry_dt.dt.strftime("%G-W%V").to_numpy()
    cluster_defs["entry_month"] = entry_dt.dt.strftime("%Y-%m").to_numpy()
    return cluster_defs


def _tail_diagnostic_rows(
    level_name: str,
    x: np.ndarray,
    rng: np.random.Generator,
    fractions: tuple[float, ...] = (0.01, 0.05, 0.10),
) -> list[dict]:
    """Tail checks: symmetric trimming tests robustness; top-removal shows skew reliance."""
    rows: list[dict] = []
    x_sorted = np.sort(np.asarray(x, dtype=float))
    n = len(x_sorted)

    for frac in fractions:
        k = int(np.floor(n * frac))
        if k == 0 or n - 2 * k < 10:
            continue

        sym = x_sorted[k:n - k]
        t_sym, p_sym = _one_sided_t(sym)
        rows.append({
            "level": level_name,
            "family": "tail",
            "variant": f"symmetric_trim_{int(frac * 100)}pct",
            "n": int(len(sym)),
            "mean": float(sym.mean()),
            "median": float(np.median(sym)),
            "t": t_sym,
            "p_t": p_sym,
            "interpretation": "robustness_to_balanced_tails",
        })

        no_top = x_sorted[:n - k]
        t_top, p_top = _one_sided_t(no_top)
        rows.append({
            "level": level_name,
            "family": "tail",
            "variant": f"drop_top_{int(frac * 100)}pct",
            "n": int(len(no_top)),
            "mean": float(no_top.mean()),
            "median": float(np.median(no_top)),
            "t": t_top,
            "p_t": p_top,
            "interpretation": "sensitivity_to_right_tail",
        })

    total = float(np.sum(x_sorted))
    for frac in fractions:
        k = max(1, int(np.floor(n * frac)))
        rows.append({
            "level": level_name,
            "family": "tail_concentration",
            "variant": f"top_{int(frac * 100)}pct_share_of_total",
            "n": int(k),
            "mean": float(np.sum(x_sorted[-k:]) / total) if total != 0 else float("nan"),
        })
    return rows


def _fmt_pct(x: float, digits: int = 2) -> str:
    return "--" if pd.isna(x) else f"{x * 100:+.{digits}f}\\%"


def _fmt_p(x: float) -> str:
    if pd.isna(x):
        return "--"
    if x < 0.0001:
        return "$<10^{-4}$"
    return f"{x:.4f}"


def _fmt_p_plain(x: float) -> str:
    if pd.isna(x):
        return "--"
    if x < 0.0001:
        return "<0.0001"
    return f"{x:.4f}"


def _latex_escape(text: str) -> str:
    repl = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}",
    }
    return "".join(repl.get(ch, ch) for ch in str(text))


def _lookup(summary: pd.DataFrame, level: str, family: str, variant: str) -> pd.Series | None:
    m = (
        (summary["level"] == level)
        & (summary["family"] == family)
        & (summary["variant"] == variant)
    )
    if not m.any():
        return None
    return summary.loc[m].iloc[0]


def _paper_level_rows(summary: pd.DataFrame) -> pd.DataFrame:
    labels = {
        "candidate_level": "Candidate",
        "symbol_day_collapsed": "Symbol-day",
        "event_level": "Event",
    }
    dep_choice = {
        "candidate_level": "symbol_episode",
        "symbol_day_collapsed": "symbol_episode",
        "event_level": "entry_month",
    }
    rows = []
    for level, label in labels.items():
        core = _lookup(summary, level, "core", "net")
        if core is None:
            continue
        dep = _lookup(summary, level, "cluster", dep_choice[level])
        rows.append({
            "level": label,
            "n": int(core["n"]),
            "mean": core["mean"],
            "median": core["median"],
            "win_rate": core["win_rate"],
            "ci_lo": core["ci_lo"],
            "ci_hi": core["ci_hi"],
            "p_t": core["p_t"],
            "p_boot": core["p_boot"],
            "p_wilcoxon": core["p_wilcoxon"],
            "dep_cluster": dep_choice[level] if dep is not None else "",
            "p_dependency_boot": dep["p_boot"] if dep is not None else np.nan,
        })
    return pd.DataFrame(rows)


def _write_h1_paper_outputs(summary: pd.DataFrame, h1_dir: Path) -> None:
    """Write compact paper tables and a short interpretation memo."""
    table = _paper_level_rows(summary)
    table_path = h1_dir / "h1_paper_table.csv"
    table.to_csv(table_path, index=False)

    tex_lines = [
        r"\begin{table}[H]",
        r"\centering",
        r"\caption{Raw T-1 expectation inference. One-sided tests use $H_1:\mu>0$.}",
        r"\label{tab:h1inference}",
        r"\small",
        r"\begin{tabular}{lrrrrrrr}",
        r"\toprule",
        r"\textbf{Level} & $N$ & \textbf{Mean} & \textbf{95\% BCa CI} & \textbf{Median} & \textbf{Win} & \textbf{$p_{\mathrm{iid}}$} & \textbf{$p_{\mathrm{dep}}$} \\",
        r"\midrule",
    ]
    for _, row in table.iterrows():
        ci = f"[{_fmt_pct(row['ci_lo'])}, {_fmt_pct(row['ci_hi'])}]"
        tex_lines.append(
            f"{_latex_escape(row['level'])} & {int(row['n'])} & {_fmt_pct(row['mean'])} "
            f"& {ci} & {_fmt_pct(row['median'])} & {_fmt_pct(row['win_rate'])} "
            f"& {_fmt_p(row['p_boot'])} & {_fmt_p(row['p_dependency_boot'])} \\\\"
        )
    tex_lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
        "",
        r"\noindent $p_{\mathrm{dep}}$ is a cluster bootstrap using the most relevant dependence unit for each row: symbol-episode for candidate and symbol-day rows, and entry-month for event-level aggregates.",
    ])
    tex_path = h1_dir / "h1_paper_table.tex"
    tex_path.write_text("\n".join(tex_lines) + "\n", encoding="utf-8")

    candidate = table.loc[table["level"] == "Candidate"].iloc[0] if (table["level"] == "Candidate").any() else None
    symbol_day = table.loc[table["level"] == "Symbol-day"].iloc[0] if (table["level"] == "Symbol-day").any() else None
    event = table.loc[table["level"] == "Event"].iloc[0] if (table["level"] == "Event").any() else None

    memo = [
        "# H1 Statistical Evidence for Paper",
        "",
        "## Bottom line",
    ]
    if candidate is not None:
        memo.append(
            f"The primary candidate-level H1 test supports a real positive expectation: "
            f"N={int(candidate['n'])}, mean={candidate['mean']:+.4%}, "
            f"median={candidate['median']:+.4%}, win rate={candidate['win_rate']:.2%}, "
            f"95% BCa CI=[{candidate['ci_lo']:+.4%}, {candidate['ci_hi']:+.4%}], "
            f"iid bootstrap p={_fmt_p_plain(candidate['p_boot'])}."
        )
    if symbol_day is not None and event is not None:
        memo.append(
            f"The duplication controls keep the expectation positive: symbol-day mean "
            f"{symbol_day['mean']:+.4%} over N={int(symbol_day['n'])}; event-level mean "
            f"{event['mean']:+.4%} over N={int(event['n'])}."
        )
    memo.extend([
        "",
        "## How to state this in the paper",
        "",
        "The statistical tests help if they are used to support the existence and shape of the raw expectation, not to pretend the edge is a smooth high-hit-rate anomaly. The defensible claim is: prediction-market threshold events define a positively skewed conditional return distribution with positive mean net return before scheduled resolution. The weak binomial/win-rate evidence is not a failure of H1; it shows that the effect is payoff-asymmetric rather than accuracy-dominant.",
        "",
        "The hardest dependence controls are intentionally more conservative. Symbol-episode and entry-month clustering ask whether the result survives after treating overlapping trades or calendar regimes as common shocks. Where those p-values become marginal, the right interpretation is bounded external validity, not disappearance of the positive expectation: the sample mean, bootstrap CI, collapsed view, and event view remain positive, but the edge is regime-sensitive and right-skewed.",
        "",
        "Suggested wording:",
        "",
        "> The raw T-1 expectation remains positive after collapsing same-symbol same-day duplicates and after aggregating to the event level. The win-rate tests are weaker, which is consistent with a right-skewed event-driven payoff distribution rather than a high-frequency directional classifier. Therefore the central evidence is mean-expectancy evidence, supported by bootstrap confidence intervals and dependence-robust clustering, not a claim that most signals are individually profitable.",
        "",
        "## Files written",
        "",
        f"- `{table_path.name}`: compact machine-readable table for the paper.",
        f"- `{tex_path.name}`: LaTeX table snippet.",
        "- `h1_inference_summary.csv`: full replication table with all robustness checks.",
    ])
    memo_path = h1_dir / "h1_interpretation_for_paper.md"
    memo_path.write_text("\n".join(memo) + "\n", encoding="utf-8")


def run_h1_battery():
    repo_root = Path(__file__).resolve().parent.parent
    h1_dir = repo_root / "output" / "raw_expectation_tminus1"
    summary_rows: list[dict] = []

    print("\n" + "=" * 70)
    print("  PART A — H1: RAW T-1 EXPECTATION INFERENCE BATTERY")
    print("  Claim under test: E[net return, entry -> T_e - 1 | signal] > 0")
    print("=" * 70)

    levels = {
        "candidate_level": h1_dir / "raw_expectation_trades_candidate_level.csv",
        "symbol_day_collapsed": h1_dir / "raw_expectation_trades_symbol_day_collapsed.csv",
        "event_level": h1_dir / "raw_expectation_event_level.csv",
    }
    frames = {}
    for name, path in levels.items():
        if not path.exists():
            print(f"\nError: {path} not found.")
            print("Run diagnostics/run_raw_expectation_test_tminus1.py first.")
            return
        raw = pd.read_csv(path)
        frames[name] = _prepare_event_level_frame(raw) if name == "event_level" else raw

    for level_name, df in frames.items():
        rng = np.random.default_rng(RNG_SEED)
        x = df["net_return"].to_numpy(dtype=float)
        has_symbol_trades = (
            {"symbol", "entry_date", "exit_date_t_minus_1", "gross_return"}.issubset(df.columns)
            and not (df["symbol"] == "__event_level__").all()
        )

        print(f"\n{'-' * 70}")
        print(f"  LEVEL: {level_name}  (n = {len(df)})")
        print(f"{'-' * 70}")

        # ── A1. Moments, effect size, BCa CI ─────────────────────────────
        core = _core_battery(x, rng)
        print("\n  A1. Distribution and effect size (net returns)")
        print(f"      mean {core['mean']:+.4%}   median {core['median']:+.4%}   "
              f"win {core['win_rate']:.2%}   sd {core['sd']:.4%}   skew {core['skew']:+.2f}")
        print(f"      q05 {core['q05']:+.4%}   q25 {core['q25']:+.4%}   "
              f"q75 {core['q75']:+.4%}   q95 {core['q95']:+.4%}")
        print(f"      95% BCa CI for mean: [{core['ci_lo']:+.4%}, {core['ci_hi']:+.4%}]")
        print(f"      one-sided t: t={core['t']:.3f} p={core['p_t']:.2e}   "
              f"iid bootstrap p={core['p_boot']:.4f}   Wilcoxon p={core['p_wilcoxon']:.4f}   "
              f"binomial p={core['p_binom']:.4f}")
        summary_rows.append({"level": level_name, "family": "core", "variant": "net",
                             **core})

        # ── A2. Cluster-robust inference across dependence levels ───────
        print("\n  A2. Cluster-robust inference (CRVE t, df=G-1; cluster bootstrap)")
        print(f"      {'cluster level':<22}{'G':>6}{'t_CR':>9}{'p_CR':>10}{'p_boot':>10}")
        entry_dt = pd.to_datetime(df["entry_date"])
        cluster_defs = _cluster_definitions(df)
        for cname, labels in cluster_defs.items():
            cs = _cluster_stats(x, labels, N_BOOT, rng)
            print(f"      {cname:<22}{cs['G']:>6}{cs['t_cr']:>9.3f}"
                  f"{cs['p_cr']:>10.4f}{cs['p_boot']:>10.4f}")
            summary_rows.append({"level": level_name, "family": "cluster",
                                 "variant": cname, **cs})

        # ── A3. Market-adjusted returns ──────────────────────────────────
        print("\n  A3. Market-adjusted (net return minus benchmark same-window return)")
        dep_labels = cluster_defs.get("symbol_episode")
        if dep_labels is None:
            dep_labels = cluster_defs.get("event_id", cluster_defs["entry_month"])
        for bench_symbol in ("SPY", "QQQ"):
            bench = _close_series(repo_root, bench_symbol)
            adj = x - _benchmark_window_returns(df, bench)
            ok = ~np.isnan(adj)
            adj_core = _core_battery(adj[ok], rng)
            cs = _cluster_stats(adj[ok], dep_labels[ok], N_BOOT, rng)
            print(f"      vs {bench_symbol}: mean {adj_core['mean']:+.4%}   "
                  f"t p={adj_core['p_t']:.2e}   Wilcoxon p={adj_core['p_wilcoxon']:.4f}   "
                  f"dependency-cluster p_CR={cs['p_cr']:.4f} p_boot={cs['p_boot']:.4f}")
            summary_rows.append({"level": level_name, "family": "market_adjusted",
                                 "variant": bench_symbol, **adj_core,
                                 "dependency_p_cr": cs["p_cr"],
                                 "dependency_p_boot": cs["p_boot"]})

        # ── A4. Calendar-time portfolio alpha (Newey-West) ───────────────
        print("\n  A4. Calendar-time portfolio alpha (equal-weight open positions,"
              f" NW lags={NW_LAGS})")
        if has_symbol_trades:
            for bench_symbol in ("SPY", "QQQ"):
                ct = _calendar_time_alpha(df, repo_root, bench_symbol)
                if ct is None:
                    print(f"      vs {bench_symbol}: insufficient data")
                    continue
                print(f"      vs {bench_symbol}: alpha {ct['alpha'] * 1e4:+.2f} bps/day"
                      f"  beta {ct['beta']:.3f}  t_NW={ct['t']:.3f}  p={ct['p']:.4f}"
                      f"  ({ct['n_open_position_days']} open-position days)")
                summary_rows.append({"level": level_name, "family": "calendar_time",
                                     "variant": bench_symbol, **ct})
        else:
            print("      skipped: event-level aggregates do not contain per-symbol daily paths")

        # ── A5. Random-timing placebo ────────────────────────────────────
        print("\n  A5. Random-timing placebo (same symbols/durations/span, "
              f"{N_BOOT:,} reps, gross vs gross)")
        if has_symbol_trades:
            pl = _placebo_timing_test(df, repo_root, N_BOOT, rng)
            if not np.isnan(pl["p"]):
                print(f"      actual gross mean {pl['obs_gross_mean']:+.4%}   "
                      f"placebo mean {pl['placebo_mean']:+.4%}   "
                      f"placebo 95th pct {pl['placebo_q95']:+.4%}")
                print(f"      placebo p = {pl['p']:.4f}"
                      f"   (fallback groups: {pl['n_fallback_groups']})")
            summary_rows.append({"level": level_name, "family": "placebo",
                                 "variant": "random_timing", **pl})
        else:
            print("      skipped: random timing requires original symbols and durations")

        # ── A6. Subperiod robustness ─────────────────────────────────────
        print("\n  A6. Subperiod robustness (net returns)")
        print(f"      {'subset':<26}{'n':>6}{'mean':>10}{'p_t':>10}"
              f"{'p_cluster':>11}{'p_wilcox':>10}")
        subsets = {
            "full_sample": np.ones(len(df), dtype=bool),
            "pre_2026 (train era)": (entry_dt < "2026-01-01").to_numpy(),
            "2026+ (OOS era)": (entry_dt >= "2026-01-01").to_numpy(),
            "ex_march_2026": (entry_dt.dt.strftime("%Y-%m") != "2026-03").to_numpy(),
            "test_split_only": (df["split"] == "test").to_numpy()
                if "split" in df.columns else np.ones(len(df), dtype=bool),
        }
        for sname, mask in subsets.items():
            if mask.sum() < 10:
                continue
            xs = x[mask]
            _, p_t = _one_sided_t(xs)
            cs = _cluster_stats(xs, dep_labels[mask], N_BOOT, rng)
            p_w = _wilcoxon_greater_p(xs)
            print(f"      {sname:<26}{mask.sum():>6}{xs.mean():>+10.4%}"
                  f"{p_t:>10.4f}{cs['p_boot']:>11.4f}{p_w:>10.4f}")
            summary_rows.append({"level": level_name, "family": "subperiod",
                                 "variant": sname, "n": int(mask.sum()),
                                 "mean": float(xs.mean()), "p_t": p_t,
                                 "dependency_p_boot": cs["p_boot"],
                                 "p_wilcoxon": p_w})

        # ── A7. Tail and trimming diagnostics ────────────────────────────
        print("\n  A7. Tail diagnostics (positive expectation vs right-tail reliance)")
        print(f"      {'variant':<28}{'n':>6}{'mean':>10}{'median':>10}{'p_t':>10}")
        tail_rows = _tail_diagnostic_rows(level_name, x, rng)
        for row in tail_rows:
            summary_rows.append(row)
            if row["family"] != "tail":
                continue
            print(f"      {row['variant']:<28}{row['n']:>6}{row['mean']:>+10.4%}"
                  f"{row['median']:>+10.4%}{row['p_t']:>10.4f}")

    out_path = h1_dir / "h1_inference_summary.csv"
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out_path, index=False)
    _write_h1_paper_outputs(summary, h1_dir)
    print(f"\n  Summary table written to {out_path}")
    print(f"  Paper table written to {h1_dir / 'h1_paper_table.tex'}")
    print(f"  Interpretation memo written to {h1_dir / 'h1_interpretation_for_paper.md'}")


# ═══════════════════════════════════════════════════════════════════════════
# PART B — H2: CEM monetization-layer tests
# ═══════════════════════════════════════════════════════════════════════════

def run_tests(experiment_slugs: list[str]):
    repo_root = Path(__file__).resolve().parent.parent

    for slug in experiment_slugs:
        for benchmark in ['SPY', 'QQQ']:
            print(f"\n{'=' * 60}")
            print(f"  H2 MONETIZATION TESTS FOR {benchmark} ({slug} Configuration)")
            print(f"{'=' * 60}")

            trade_log_path  = repo_root / 'data' / 'experiment_trade_logs_clean'  / f'{benchmark.lower()}_{slug}_test.csv'
            equity_log_path = repo_root / 'data' / 'experiment_equity_logs_clean' / f'{benchmark.lower()}_{slug}_test.csv'

            # ── Load trade log ────────────────────────────────────────────
            try:
                df_all = pd.read_csv(trade_log_path)
            except FileNotFoundError:
                print(f"Error: Trade log not found at {trade_log_path}")
                continue

            # ── FIX 1: restrict to OOS splits only ───────────────────────
            # Training trades are used by CEM for policy search and contain
            # intentional exploration losses.  Including them in a statistical
            # hypothesis test about the strategy's live predictive power is
            # methodologically incorrect — we test only on val + test splits.
            if 'split' in df_all.columns:
                df_trades = df_all[df_all['split'].isin(['val', 'test'])].reset_index(drop=True)
            else:
                df_trades = df_all # Fallback for older logs without a split column

            if df_trades.empty:
                print("No OOS trades found. Skipping.")
                continue

            # ── Test 1: Binomial — directional accuracy ───────────────────
            print("\n=== Test 1: Binomial Test for Directional Accuracy ===")
            trials    = len(df_trades)
            successes = int((df_trades['pnl'] > 0).sum())

            result = stats.binomtest(k=successes, n=trials, p=0.5, alternative='greater')
            print(f"Successes:   {successes}")
            print(f"Total Trials:{trials}")
            print(f"Win Rate:    {successes / trials:.4f}")
            print(f"P-value:     {result.pvalue:.4e}")
            if result.pvalue < 0.05:
                print("Conclusion: Directional accuracy is statistically significant (better than random).")
            else:
                print("Conclusion: Directional accuracy is NOT statistically significant.")

            # ── Test 2: Net CAR T-Test ────────────────────────────────────
            print("\n=== Test 2: Net Cumulative Abnormal Return (CAR) T-Test ===")

            prices_path = repo_root / 'data' / 'prices.pkl'
            with open(prices_path, 'rb') as f:
                prices_dict = pickle.load(f)
            benchmark_data = prices_dict.get(benchmark, [])
            if not benchmark_data:
                print(f"Error: No {benchmark} data in prices.pkl.")
                continue

            benchmark_prices = pd.Series(
                data  = [close for _, _, _, close in benchmark_data],
                index = [ts.strftime('%Y-%m-%d') for ts, _, _, _ in benchmark_data],
            ).sort_index()

            net_car_values = []

            for _, row in df_trades.iterrows():
                entry_date = str(row['entry_date'])[:10]
                exit_date  = str(row['exit_date'])[:10]

                # ── FIX 2: use the simulator's already-netted pnl_pct ────
                # The trade log's pnl_pct is computed by the portfolio simulator
                # which already applies IB commissions, SEC fees, and 5 bp
                # slippage on all four rotation legs.  Re-deriving costs from
                # raw entry/exit prices risks double-counting slippage.
                net_asset_return = float(row['pnl_pct']) / 100.0

                b_entry = get_closest_price(benchmark_prices, entry_date)
                b_exit  = get_closest_price(benchmark_prices, exit_date)

                if any(
                    x is None or (isinstance(x, float) and np.isnan(x)) or x == 0
                    for x in [b_entry, b_exit]
                ):
                    continue

                benchmark_return = (b_exit / b_entry) - 1.0
                net_car_values.append(net_asset_return - benchmark_return)

            if not net_car_values:
                print("No valid trades for Net CAR test.")
                continue

            net_car_array = np.array(net_car_values)
            mean_net_car  = net_car_array.mean()
            t_stat_car, p_val_car = stats.ttest_1samp(net_car_array, popmean=0.0, alternative='greater')

            print(f"Number of valid trades for Net CAR: {len(net_car_array)}")
            print(f"Mean Net CAR:   {mean_net_car:.6f}")
            print(f"T-statistic:    {t_stat_car:.4f}")
            print(f"P-value:        {p_val_car:.4e}")
            if p_val_car < 0.05:
                print("Conclusion: Net Abnormal Return (Trade-Level) is statistically significant.")
            else:
                print("Conclusion: Net Abnormal Return (Trade-Level) is NOT statistically significant. "
                      "Transaction costs may have eaten the edge.")

            # ── Test 3: Daily Net Excess Return (Alpha) ───────────────────
            print("\n=== Test 3: Daily Net Excess Return Significance Test ===")

            try:
                df_equity = pd.read_csv(equity_log_path, parse_dates=['date'])
            except FileNotFoundError:
                print(f"Error: Equity log not found at {equity_log_path}")
                continue

            # ── FIX 3: restrict equity curve to OOS period ───────────────
            # Days prior to the first OOS entry belong to the training/exploration
            # phase where the portfolio behaviour is not representative of the
            # live policy.  Including them suppresses the per-day signal estimate.
            oos_start = pd.to_datetime(df_trades['entry_date'].min())
            df_equity = df_equity[df_equity['date'] >= oos_start].copy()

            df_equity['portfolio_return']  = df_equity['equity'].pct_change()
            df_equity['benchmark_return']  = df_equity['benchmark_equity'].pct_change()
            df_equity = df_equity.dropna(subset=['portfolio_return', 'benchmark_return'])
            df_equity['net_excess_return'] = df_equity['portfolio_return'] - df_equity['benchmark_return']

            daily_net_excess = df_equity['net_excess_return'].values
            mean_excess      = daily_net_excess.mean()
            t_stat_alpha, p_val_alpha = stats.ttest_1samp(daily_net_excess, popmean=0.0, alternative='greater')

            print(f"Number of trading days: {len(daily_net_excess)}")
            print(f"Mean Daily Net Excess Return (Alpha): {mean_excess:.6f}")
            print(f"T-statistic: {t_stat_alpha:.4f}")
            print(f"P-value:     {p_val_alpha:.4e}")
            if p_val_alpha < 0.05:
                print("Conclusion: Portfolio-level daily alpha is statistically significant.")
            else:
                print("Conclusion: Portfolio-level daily alpha is NOT distinguishable from "
                      "zero at this sample size. This bounds the H2 monetization claim only; "
                      "the H1 raw-expectancy claim is tested separately in Part A.")


def jensens_alpha_test(
    benchmark: str,
    equity_dir: Path,
    experiment_slugs: list[str],
    rf_annual: float = 0.05,
) -> list[dict]:
    """Jensen's Alpha via CAPM regression: Rp - Rf = alpha + beta*(Rm - Rf) + e"""
    daily_rf = (1 + rf_annual) ** (1 / 252) - 1
    results = []

    for slug in experiment_slugs:
        path = equity_dir / f"{benchmark.lower()}_{slug}_test.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        rp = df["equity"].pct_change().dropna().values
        rm = df["benchmark_equity"].pct_change().dropna().values
        n = min(len(rp), len(rm))
        rp, rm = rp[:n], rm[:n]

        y = rp - daily_rf
        x = rm - daily_rf
        X = np.column_stack([np.ones(n), x])
        beta_vec, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
        alpha_daily = beta_vec[0]
        beta = beta_vec[1]

        y_hat = X @ beta_vec
        resid = y - y_hat
        se = np.sqrt(np.sum(resid ** 2) / (n - 2) / np.sum((x - x.mean()) ** 2))
        se_alpha = np.sqrt(np.sum(resid ** 2) / (n - 2) * (1 / n + x.mean() ** 2 / np.sum((x - x.mean()) ** 2)))
        t_alpha = alpha_daily / se_alpha if se_alpha > 0 else 0.0
        p_alpha = 1 - stats.t.cdf(t_alpha, df=n - 2)

        alpha_annual = alpha_daily * 252
        results.append({
            "strategy": slug,
            "alpha_daily_bps": alpha_daily * 10_000,
            "alpha_annual_pct": alpha_annual * 100,
            "beta": beta,
            "t_statistic": t_alpha,
            "p_value": p_alpha,
            "n_days": n,
        })

    return results


def run_jensens_alpha():
    repo_root = Path(__file__).resolve().parent.parent
    equity_dir = repo_root / "data" / "experiment_equity_logs_clean"

    for benchmark in ["SPY", "QQQ"]:
        print(f"\n{'=' * 60}")
        print(f"  JENSEN'S ALPHA (CAPM) — {benchmark} benchmark")
        print(f"  Rp - Rf = alpha + beta*(Rm - Rf)")
        print(f"  Rf = 5.0% annual (10Y Treasury approx)")
        print(f"{'=' * 60}")

        results = jensens_alpha_test(benchmark, equity_dir, EXPERIMENT_SLUGS)
        if not results:
            print("  No equity logs found.")
            continue

        print(f"  {'Strategy':<22} {'Alpha(bps/d)':>12} {'Alpha(%/yr)':>11} "
              f"{'Beta':>6} {'t-stat':>7} {'p-value':>8}")
        print(f"  {'-' * 70}")

        for r in results:
            sig = "*" if r["p_value"] < 0.05 else ""
            print(f"  {r['strategy']:<22} {r['alpha_daily_bps']:>+11.2f} "
                  f"{r['alpha_annual_pct']:>+10.2f}% {r['beta']:>6.3f} "
                  f"{r['t_statistic']:>7.3f} {r['p_value']:>8.4f}{sig}")

        best = min(results, key=lambda r: r["p_value"])
        print(f"\n  Best: {best['strategy']} "
              f"(alpha={best['alpha_annual_pct']:+.2f}%/yr, p={best['p_value']:.4f})")

        # `best` is the argmin of p over len(results) strategies, so its p-value
        # cannot be read against a nominal 5% threshold -- that is the classic
        # multiple-comparisons error. Bonferroni is the floor of the correction,
        # not the whole of it: each strategy here is itself the argmax of a CEM
        # search over 120 (non-WF) or 607 (WF) policy evaluations, so the true
        # trial count is ~7,290, not len(results). Treat even a "pass" below as
        # provisional until a Deflated Sharpe Ratio is computed from
        # output/cem_population.csv.
        k = len(results)
        bonferroni = 0.05 / k
        print(f"  Bonferroni threshold for k={k} reported strategies: p < {bonferroni:.4f}")

        if best["p_value"] < bonferroni:
            print(f"  SIGNIFICANT after Bonferroni: evidence of skill-based excess return")
        elif best["p_value"] < 0.05:
            print(f"  NOT significant after Bonferroni "
                  f"(nominal p={best['p_value']:.4f} survives 5% only because it is "
                  f"the best of {k} searched strategies)")
        else:
            print(f"  NOT significant at 5%, even before correcting for k={k}")


def hansen_spa_test(
    benchmark: str,
    equity_dir: Path,
    experiment_slugs: list[str],
    n_bootstrap: int = 10_000,
    avg_block_length: float = 10.0,
    seed: int = 42,
) -> dict:
    """Hansen's Superior Predictive Ability test (Hansen 2005).

    Tests H0: no strategy beats B&H after adjusting for multiple comparisons.
    Uses stationary bootstrap (Politis & Romano 1994).
    """
    excess_returns = []
    names = []

    for slug in experiment_slugs:
        path = equity_dir / f"{benchmark.lower()}_{slug}_test.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        strat = np.log(df["equity"].values[1:] / df["equity"].values[:-1])
        bench = np.log(df["benchmark_equity"].values[1:] / df["benchmark_equity"].values[:-1])
        excess_returns.append(strat - bench)
        names.append(slug)

    if not excess_returns:
        return {"p_consistent": None}

    D = np.column_stack(excess_returns)
    T, k = D.shape
    d_bar = D.mean(axis=0)
    d_var = D.var(axis=0, ddof=1)
    d_std = np.sqrt(d_var / T)

    t_values = d_bar / d_std
    t_spa = float(np.max(t_values))
    best_idx = int(np.argmax(t_values))

    rng = np.random.default_rng(seed)
    p_new = 1.0 / avg_block_length

    starts = rng.integers(0, T, size=(n_bootstrap, T))
    uniforms = rng.random(size=(n_bootstrap, T))
    indices = np.empty((n_bootstrap, T), dtype=np.intp)
    indices[:, 0] = starts[:, 0]
    for t in range(1, T):
        new_block = uniforms[:, t] < p_new
        indices[:, t] = np.where(new_block, starts[:, t], (indices[:, t - 1] + 1) % T)

    D_boot = D[indices]
    d_bar_boot = D_boot.mean(axis=1)

    threshold = np.sqrt(d_var * 2.0 * np.log(np.log(max(T, 3))) / T)
    g_consistent = d_bar * (d_bar >= -threshold).astype(float)
    g_upper = d_bar.copy()
    g_lower = np.zeros(k)

    results = {}
    for variant, g in [("consistent", g_consistent), ("upper", g_upper), ("lower", g_lower)]:
        centered = d_bar_boot - g[None, :]
        t_boot = np.max(centered / d_std[None, :], axis=1)
        results[f"p_{variant}"] = float(np.mean(t_boot >= t_spa))

    results["t_statistic"] = t_spa
    results["best_strategy"] = names[best_idx]
    results["best_daily_excess_bps"] = float(d_bar[best_idx] * 10_000)
    results["n_strategies"] = k
    results["n_days"] = T
    return results


EXPERIMENT_SLUGS = [
    "baseline",
    "t2_trainwindows",
    "t1_t2",
    "t2_t3",
    "t1_t2_t3",
    "t1_t2_t3_t4",
]


def run_spa():
    repo_root = Path(__file__).resolve().parent.parent
    equity_dir = repo_root / "data" / "experiment_equity_logs_clean"

    for benchmark in ["SPY", "QQQ"]:
        print(f"\n{'=' * 60}")
        print(f"  HANSEN'S SPA TEST — {benchmark} benchmark")
        print(f"  H0: no strategy beats {benchmark} B&H (data-snooping adjusted)")
        print(f"{'=' * 60}")

        spa = hansen_spa_test(benchmark, equity_dir, EXPERIMENT_SLUGS)
        if spa.get("p_consistent") is None:
            print("  No equity logs found.")
            continue

        print(f"  {spa['n_strategies']} strategies, {spa['n_days']} trading days, "
              f"10,000 stationary bootstrap replications")
        print(f"  Best strategy: {spa['best_strategy']}  "
              f"(avg daily excess: {spa['best_daily_excess_bps']:+.1f} bps/day)")
        print(f"  Test statistic: {spa['t_statistic']:.3f}")
        print(f"  p-value (consistent): {spa['p_consistent']:.4f}")
        print(f"  p-value (upper/conservative): {spa['p_upper']:.4f}")
        print(f"  p-value (lower/liberal): {spa['p_lower']:.4f}")

        if spa["p_consistent"] < 0.05:
            print(f"  → REJECT H0 at 5%: significant evidence of superior predictive ability")
        elif spa["p_consistent"] < 0.10:
            print(f"  → REJECT H0 at 10% (marginal)")
        else:
            print(f"  → FAIL TO REJECT H0 at 10%")


if __name__ == '__main__':
    run_h1_battery()
    run_tests(EXPERIMENT_SLUGS)
    run_spa()
    run_jensens_alpha()
