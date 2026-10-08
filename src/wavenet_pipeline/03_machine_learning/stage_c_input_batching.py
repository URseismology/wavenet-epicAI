#!/usr/bin/env python3
"""
Stage C — Input Batching

Wraps a real build_ml_cache.py HDF5 cache and its matching splits_seed42.json
into a PyTorch DataLoader triple (train/val/test), seeded and split-leakage-
checked.

Reconciled against axon-1's actual schema (2026-09-24) and against dataset.py's
existing CachedFTANDataset (lines 94-118), which is the real Stage C/D building
block — this module does not define its own Dataset class, it only computes
per-split index arrays and hands them to CachedFTANDataset(cache_path, indices).

  Cache (single HDF5, flat top-level datasets, row i consistent across all of
  them -- no group nesting):
    X         (n, 80, 300) float32   regridded FTAN input, per-row [0,1]
    Y         (n, 80, 300) float32   binary target mask
    sim_key   (n,)         vlen str  e.g. "M01_abcd1234"
    geom_key  (n,)         vlen str  e.g. "separation_127.0km"
    sep_km    (n,)         float64
    family    (n,)         vlen str  sim_key[:3]
    split     (n,)         vlen str  "train"/"val"/"test"/"unassigned"
                                     (redundant with splits_seed42.json --
                                      kept as a cross-check, see cross_check_
                                      split_column())

  splits_seed42.json:
    {"seed": 42, "train_families": [...], "val_families": [...],
     "test_families": [...], "n_train": int, "n_val": int, "n_test": int,
     "train_keys": [...], "val_keys": [...], "test_keys": [...]}
    Split is by family (sim_key[:3]) -- a whole family, across both input
    files and all its geometries, lands in one split.

  Sample key (matches cache rows to splits_seed42.json entries):
    key = f"{sim_key}/{geom_key}"   e.g. "M01_abcd1234/separation_127.0km"

CachedFTANDataset holds one persistent HDF5 handle per worker process (see its
docstring -- the per-__getitem__ reopen was fixed in Stage D) rather than holding X/Y
in memory -- important once this points at a full ~20,000-sample production
cache rather than the 100-sample Stage A verification cache used below.

Usage:
    python -m wavenet_pipeline.03_machine_learning.stage_c_input_batching \\
        <cache.h5> <splits_seed42.json> [--batch-size 16] [--num-workers 0] [--seed 42]
"""

import argparse
import json
from pathlib import Path

import h5py
import numpy as np
import torch
from torch.utils.data import DataLoader

from .dataset import CachedFTANDataset


# ---------------------------------------------------------------------
# Cache / splits loading
# ---------------------------------------------------------------------

def load_cache_keys(cache_path):
    """Reads only sim_key/geom_key/split columns (not X/Y) -- cheap even
    against a full production cache, since key-building/cross-checking
    doesn't need the array data."""
    with h5py.File(cache_path, "r") as f:
        n = f["X"].shape[0]
        sim_key = [k.decode() if isinstance(k, bytes) else k for k in f["sim_key"][:]]
        geom_key = [k.decode() if isinstance(k, bytes) else k for k in f["geom_key"][:]]
        cache_split = [s.decode() if isinstance(s, bytes) else s for s in f["split"][:]]
    sample_keys = [f"{sk}/{gk}" for sk, gk in zip(sim_key, geom_key)]
    assert len(sample_keys) == n, f"{n} rows but {len(sample_keys)} keys built"
    return sample_keys, cache_split


def load_splits(splits_path):
    with open(splits_path) as f:
        splits = json.load(f)
    for name in ("train_keys", "val_keys", "test_keys"):
        if name not in splits:
            raise ValueError(f'splits file missing "{name}"')
    return {"train": splits["train_keys"], "val": splits["val_keys"], "test": splits["test_keys"]}


def cross_check_split_column(sample_keys, cache_split, splits):
    """
    The cache's own 'split' column (written by build_ml_cache.py's build_cache())
    and splits_seed42.json (written by splits.py's save_splits()) are two
    independent records of the same train/val/test assignment, produced in the
    same build_ml_cache.py run but by different code paths. Disagreement means
    the cache on disk was built against a different split assignment than the
    JSON now being loaded (stale cache, regenerated splits with a different
    seed, hand-edited JSON, etc.) -- a real Stage A/C boundary bug to catch
    before training silently mixes families across splits.

    Returns (mismatches, unassigned_keys):
      mismatches: list of (key, splits_json_label, cache_label) tuples, where
        cache_label is "MISSING_FROM_CACHE" if the key wasn't found at all.
      unassigned_keys: cache rows whose split column is "unassigned" (i.e.
        never given a split label at build time -- normal for a
        --tutorial-sample cache, which intentionally skips split assignment).
    """
    key_to_cache_split = dict(zip(sample_keys, cache_split))
    mismatches = []
    for split_name, keys in splits.items():
        for k in keys:
            cs = key_to_cache_split.get(k)
            if cs is None:
                mismatches.append((k, split_name, "MISSING_FROM_CACHE"))
            elif cs != split_name:
                mismatches.append((k, split_name, cs))
    unassigned_keys = [k for k, s in zip(sample_keys, cache_split) if s == "unassigned"]
    return mismatches, unassigned_keys


# ---------------------------------------------------------------------
# DataLoader construction
# ---------------------------------------------------------------------

def collate_ftan(batch):
    xs, ys, metas = zip(*batch)
    keys = [f"{m['sim_key']}/{m['geom_key']}" for m in metas]
    return torch.stack(xs), torch.stack(ys), keys


def get_dataloaders(cache_path, splits_path, batch_size=16, num_workers=0,
                     seed=42, pin_memory=False):
    sample_keys, _cache_split = load_cache_keys(cache_path)
    splits = load_splits(splits_path)
    key_to_idx = {k: i for i, k in enumerate(sample_keys)}

    generator = torch.Generator()
    generator.manual_seed(seed)

    loaders = {}
    for split_name in ("train", "val", "test"):
        split_keys = splits[split_name]
        missing = [k for k in split_keys if k not in key_to_idx]
        if missing:
            raise ValueError(
                f"{len(missing)} {split_name} keys not found in cache "
                f"(e.g. {missing[:3]}) -- cache and splits file are out of sync")

        indices = np.array([key_to_idx[k] for k in split_keys], dtype=np.int64)
        ds = CachedFTANDataset(cache_path, indices=indices)
        loaders[split_name] = DataLoader(
            ds,
            batch_size=batch_size,
            shuffle=(split_name == "train"),
            num_workers=num_workers,
            pin_memory=pin_memory,
            # Keep workers (and their per-process HDF5 handles) alive across epochs --
            # worker startup dominates otherwise, esp. under macOS's spawn start method.
            persistent_workers=num_workers > 0,
            collate_fn=collate_ftan,
            generator=generator if split_name == "train" else None,
            drop_last=False,
        )
    return loaders


# ---------------------------------------------------------------------
# Verification (mirrors Stage A house style: run against real data, report
# what was actually checked, don't just assert "it ran")
# ---------------------------------------------------------------------

def verify(cache_path, splits_path, batch_size, num_workers, seed):
    sample_keys, cache_split = load_cache_keys(cache_path)
    splits = load_splits(splits_path)

    print(f"cache: {cache_path}")
    print(f"splits: {splits_path}")
    print(f"batch_size={batch_size} num_workers={num_workers} seed={seed}\n")

    mismatches, unassigned_keys = cross_check_split_column(sample_keys, cache_split, splits)
    print(f"cache split column vs splits_seed42.json: {len(mismatches)} mismatches"
          f"{' (OK)' if not mismatches else ' -- MISMATCH, see below'}")
    if mismatches:
        for k, expected, actual in mismatches[:10]:
            print(f"  {k}: splits.json says {expected}, cache says {actual}")
    if unassigned_keys:
        print(f"  note: {len(unassigned_keys)}/{len(sample_keys)} cache rows marked "
              f"\"unassigned\" (not referenced by any split_keys list above; expected "
              f"for a --tutorial-sample cache, which skips split assignment entirely)")
    print()

    loaders = get_dataloaders(cache_path, splits_path, batch_size, num_workers, seed)

    all_seen = {}
    for split_name, loader in loaders.items():
        n_samples = len(loader.dataset)
        n_batches = len(loader)
        print(f"[{split_name}] {n_samples} samples -> {n_batches} batches")

        seen_keys = set()
        shape_ok = True
        range_ok = True
        binary_ok = True
        nan_ok = True

        for x, y, keys in loader:
            seen_keys.update(keys)
            if x.shape[1:] != (1, 80, 300) or y.shape[1:] != (1, 80, 300):
                shape_ok = False
            if x.min().item() < -1e-6 or x.max().item() > 1 + 1e-6:
                range_ok = False
            uniq = torch.unique(y)
            if not torch.all((uniq == 0) | (uniq == 1)):
                binary_ok = False
            if torch.isnan(x).any() or torch.isnan(y).any():
                nan_ok = False

        dup_check = (len(seen_keys) == n_samples)
        print(f"  shapes==(1,80,300): {shape_ok}")
        print(f"  X in [0,1]: {range_ok}")
        print(f"  Y strictly binary: {binary_ok}")
        print(f"  no NaNs: {nan_ok}")
        print(f"  every sample seen exactly once per epoch: {dup_check}")
        print()
        all_seen[split_name] = seen_keys

    overlap = ((all_seen["train"] & all_seen["val"])
               | (all_seen["train"] & all_seen["test"])
               | (all_seen["val"] & all_seen["test"]))
    print(f"train/val/test sample-key overlap: {len(overlap)} "
          f"({'OK' if not overlap else 'LEAK -- INVESTIGATE'})")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache_path")
    parser.add_argument("splits_path")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    verify(Path(args.cache_path), Path(args.splits_path), args.batch_size,
           args.num_workers, args.seed)


if __name__ == "__main__":
    main()
