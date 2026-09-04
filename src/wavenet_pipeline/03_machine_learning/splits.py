"""
Family-aware 3-way train/val/test split, computed across the union of all input HDF5
files by family (M01-M10) — not per-file. Verified (2026-09-04, direct h5py check on
terravibranium, 10/10 sampled models) that the same Model_ID shares an identical
velocity_profile across the sep=127km and sep=100km files, so splitting by family across
files is valid: the same underlying Earth model never ends up in two different splits
just because it appears under two different station geometries.

With only 10 families total, a 3-way split is necessarily coarse. Default here is
7 train / 2 val / 1 test families (70/20/10) — a judgment call, not derived; confirm
during Stage A review (see docs/ml_pipeline_stages/stage_a_data_definition.md).
"""

import json
from pathlib import Path

import numpy as np

from .dataset import SampleRef


def family_split(samples: list[SampleRef], seed: int = 42,
                  train_families: int = 7, val_families: int = 2, test_families: int = 1
                  ) -> tuple[list[SampleRef], list[SampleRef], list[SampleRef]]:
    families = sorted({s.family for s in samples})
    if train_families + val_families + test_families != len(families):
        raise ValueError(
            f"train/val/test family counts ({train_families}/{val_families}/{test_families}) "
            f"must sum to the number of distinct families found ({len(families)}: {families})"
        )
    rng = np.random.default_rng(seed)
    shuffled = list(families)
    rng.shuffle(shuffled)

    train_fams = set(shuffled[:train_families])
    val_fams = set(shuffled[train_families:train_families + val_families])
    test_fams = set(shuffled[train_families + val_families:])

    train = [s for s in samples if s.family in train_fams]
    val = [s for s in samples if s.family in val_fams]
    test = [s for s in samples if s.family in test_fams]
    return train, val, test


def save_splits(path: Path, seed: int, train, val, test) -> None:
    def _keys(samples):
        return [f"{s.sim_key}/{s.geom_key}" for s in samples]

    payload = {
        "seed": seed,
        "train_families": sorted({s.family for s in train}),
        "val_families": sorted({s.family for s in val}),
        "test_families": sorted({s.family for s in test}),
        "n_train": len(train),
        "n_val": len(val),
        "n_test": len(test),
        "train_keys": _keys(train),
        "val_keys": _keys(val),
        "test_keys": _keys(test),
    }
    Path(path).write_text(json.dumps(payload, indent=2))
