"""Deployment preflight: prove the configured live policy can be loaded."""
from __future__ import annotations

import json

from live.config import CONFIG
from live.policy import REQUIRED_KEYS, load_live_policy


def main() -> None:
    policy = load_live_policy(CONFIG)
    missing = [key for key in REQUIRED_KEYS if key not in policy]
    if missing:
        raise RuntimeError(f"live policy is missing required keys: {missing}")
    print(json.dumps({
        "ok": True,
        "experiment": CONFIG.experiment,
        "benchmark": CONFIG.benchmark,
        "max_concurrent": int(policy["max_concurrent"]),
        "position_size_pct": float(policy["position_size_pct"]),
        "theta_out": float(policy["theta_out"]),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
