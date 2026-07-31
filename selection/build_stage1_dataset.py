"""CLI wrapper for the stage-one legal T_e-1 selection dataset."""

from .decision_dataset import build_stage1_datasets


if __name__ == "__main__":
    for name, path in build_stage1_datasets().items():
        print(f"{name}: {path}")
