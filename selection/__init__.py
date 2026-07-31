"""Small, leakage-audited selection research components."""

__all__ = [
    "EX_ANTE_FEATURES",
    "TARGET_COLUMNS",
    "build_stage1_datasets",
    "evaluate_stage1_baselines",
]


def __getattr__(name):
    """Load submodules lazily so their ``python -m`` CLIs stay warning-free."""
    if name in {"EX_ANTE_FEATURES", "TARGET_COLUMNS", "build_stage1_datasets"}:
        from .decision_dataset import EX_ANTE_FEATURES, TARGET_COLUMNS, build_stage1_datasets

        return {
            "EX_ANTE_FEATURES": EX_ANTE_FEATURES,
            "TARGET_COLUMNS": TARGET_COLUMNS,
            "build_stage1_datasets": build_stage1_datasets,
        }[name]
    if name == "evaluate_stage1_baselines":
        from .baselines import evaluate_stage1_baselines

        return evaluate_stage1_baselines
    raise AttributeError(name)

__all__ = [
    "EX_ANTE_FEATURES",
    "TARGET_COLUMNS",
    "build_stage1_datasets",
    "evaluate_stage1_baselines",
]
