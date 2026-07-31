# RL / PPO experiment assessment (2026-07-14)

## Result of the artifact search

No reinforcement-learning artifacts exist in the current repository or in the
final experiment bundle. Searched:

- word-boundary source patterns `\bppo\b`, `reinforcement`, `stable[-_]baselines`,
  `gymnasium`, `policy[-_]gradient`, `\bdqn\b`, `actor[-_]critic` across
  `analysis/`, `backtesting/`, `core/`, `diagnostics/`, `docs/`, `live/`,
  `database/`, `ingest/`, `testing/`, `scripts/`, `docker/`, `output/`
  (`.py`, `.md`, `.ipynb`, `.json`) — zero true matches (initial substring hits
  were words like "su**ppo**rt");
- no `rl/`, `RL/`, or `*ppo*` files or directories anywhere outside `.venv`;
- no RL-related git branches (`git branch -a`: `main`, two `codex/*` analysis
  branches) and no RL commits in history;
- `requirements.txt` / `pyproject.toml` contain no RL dependencies
  (no torch, stable-baselines3, gymnasium, ray);
- the `tmp/` directory listed at repository snapshot time no longer exists.

**Exact missing artifacts:** PPO/RL training code, environment definition,
training logs, checkpoint files, evaluation trade logs, and train/eval split
definitions. None of these files exist under
`C:\Users\Liran\PycharmProjects\cem_clean_repo` or
`C:\Users\Liran\PycharmProjects\cem_clean_repo\output\final_icaif_run_20260714_170817`.

## Comparability determination

Not determinable: with no code, logs, or result files, none of the required
comparability checks (candidate universe, chronological information rules,
transaction costs, benchmark mechanics, train/evaluation boundaries, seeds,
episode counts, policy-collapse behavior) can be verified. A single small
reproducibility run is impossible because there is nothing to reproduce.

## Consequence for the manuscript

**Update (author confirmation, 2026-07-14):** the author confirmed that the
PPO experiments were performed by them during development and that the code
and logs were deliberately deleted when the repository was cleaned. On that
basis the paper now includes a qualitative negative-result paragraph in the
Discussion: exploratory PPO agents did not learn a stable replacement for the
low-dimensional CEM policy (unreliable entry/exit/stop/sizing/portfolio
behavior), the ~500 label-complete training candidates appear insufficient for
a high-capacity sequential policy (stated as the most plausible interpretation,
not a proven cause), and the artifacts were not retained — so it is presented
as preliminary negative evidence, not a formal CEM benchmark. **No numerical
RL claims appear anywhere in the paper**, because no number can be traced to a
surviving artifact. The paragraph does not attribute the failure to the ~50%
win rate, and it does not claim RL is generally unsuitable for
prediction-market trading.

If the deleted runs are ever restored, commit the code, logs, and result files
and rerun this assessment; the qualitative paragraph can then be replaced with
verified numbers (episodes, evaluation trades, per-seed excess/Sharpe/MaxDD,
policy-collapse diagnostics).
