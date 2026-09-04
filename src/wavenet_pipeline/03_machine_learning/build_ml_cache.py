#!/usr/bin/env python3
"""
One-time precompute of the regridded FTAN input + mask target cache from the raw CPS
HDF5 dataset(s), plus the family-aware train/val/test split.

The real per-epoch training bottleneck is scipy.interpolate.RegularGridInterpolator on
the raw (85, 1001) FTAN, not I/O or VRAM — this script pays that cost once so training-
time __getitem__ (CachedFTANDataset) is a pure array index.

Usage:
    python build_ml_cache.py --h5 wavenetv2_dataset_10k_full.h5 wavenetv2_dataset_10k_sep100km.h5 \
        --out ftan_ml_cache_v1.h5 --splits-out splits_seed42.json

    # small, seeded, family-stratified subset for tutorial notebooks / local testing:
    python build_ml_cache.py --h5 <files...> --out tutorial_subset.h5 \
        --tutorial-sample --n 40 --seed 42
"""

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np

from . import ftan_grid
from .dataset import enumerate_samples, FTANDispersionDataset, SampleRef
from .splits import family_split, save_splits


def assert_cross_file_model_identity(h5_paths: list[Path], samples: list[SampleRef],
                                      n_check: int = 10, seed: int = 42) -> None:
    """
    Permanent safeguard: assert that a shared Model_ID across multiple input files has
    an identical velocity_profile (H_km, VP_kms) — i.e. the same underlying Earth model,
    different station geometry only. Verified true for a 10-sample spot check on
    2026-09-04 (direct h5py diff on terravibranium); this re-checks it every cache build
    rather than trusting that spot check to hold for the full dataset forever.
    """
    if len(h5_paths) < 2:
        return  # nothing to cross-check with a single input file

    by_key: dict[str, list[SampleRef]] = {}
    for s in samples:
        by_key.setdefault(s.sim_key, []).append(s)
    shared_keys = [k for k, refs in by_key.items() if len({r.h5_path for r in refs}) > 1]
    if not shared_keys:
        print("  [assert_cross_file_model_identity] no sim_key appears in >1 file — nothing to check")
        return

    rng = np.random.default_rng(seed)
    check_keys = rng.choice(shared_keys, size=min(n_check, len(shared_keys)), replace=False)

    handles = {p: h5py.File(p, "r") for p in h5_paths}
    try:
        for key in check_keys:
            refs = by_key[key]
            paths = sorted({r.h5_path for r in refs}, key=str)
            base_vp = handles[paths[0]]["simulations"][key]["velocity_profile"]
            base_h, base_vpv = base_vp["H_km"][:], base_vp["VP_kms"][:]
            for other_path in paths[1:]:
                other_vp = handles[other_path]["simulations"][key]["velocity_profile"]
                other_h, other_vpv = other_vp["H_km"][:], other_vp["VP_kms"][:]
                assert base_h.shape == other_h.shape, (
                    f"{key}: H_km shape mismatch {base_h.shape} vs {other_h.shape} "
                    f"between {paths[0].name} and {other_path.name}"
                )
                assert np.allclose(base_h, other_h) and np.allclose(base_vpv, other_vpv), (
                    f"{key}: velocity_profile differs between {paths[0].name} and "
                    f"{other_path.name} — cross-file family split is NOT valid, stop here"
                )
    finally:
        for h in handles.values():
            h.close()
    print(f"  [assert_cross_file_model_identity] OK — {len(check_keys)}/{len(shared_keys)} "
          f"shared Model_IDs checked, velocity_profile identical across files")


def build_cache(samples: list[SampleRef], out_path: Path, split_label: dict[str, str] | None = None) -> None:
    ds = FTANDispersionDataset(samples)
    n = len(ds)
    if n == 0:
        raise ValueError("No samples to cache (enumerate_samples returned empty list)")

    xy_shape = (n, ftan_grid.PERIOD_BINS + ftan_grid.PAD_ROWS, ftan_grid.VEL_BINS)
    with h5py.File(out_path, "w") as out:
        X = out.create_dataset("X", shape=xy_shape, dtype="float32")
        Y = out.create_dataset("Y", shape=xy_shape, dtype="float32")
        sim_key_ds = out.create_dataset("sim_key", shape=(n,), dtype=h5py.string_dtype())
        geom_key_ds = out.create_dataset("geom_key", shape=(n,), dtype=h5py.string_dtype())
        sep_km_ds = out.create_dataset("sep_km", shape=(n,), dtype="float64")
        family_ds = out.create_dataset("family", shape=(n,), dtype=h5py.string_dtype())
        split_ds = out.create_dataset("split", shape=(n,), dtype=h5py.string_dtype())

        for i in range(n):
            ftan_t, mask_t, meta = ds[i]
            X[i] = ftan_t.squeeze(0).numpy()
            Y[i] = mask_t.squeeze(0).numpy()
            sim_key_ds[i] = meta["sim_key"]
            geom_key_ds[i] = meta["geom_key"]
            sep_km_ds[i] = meta["sep_km"]
            family_ds[i] = meta["family"]
            key = f"{meta['sim_key']}/{meta['geom_key']}"
            split_ds[i] = (split_label or {}).get(key, "unassigned")
            if (i + 1) % max(1, n // 20) == 0 or i == n - 1:
                print(f"  [{i + 1}/{n}] cached")

    print(f"Wrote cache: {out_path} ({n} samples, "
          f"{xy_shape} X + {xy_shape} Y, "
          f"{out_path.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--h5", nargs="+", required=True, type=Path, help="Input CPS HDF5 file(s)")
    parser.add_argument("--out", required=True, type=Path, help="Output cache HDF5 path")
    parser.add_argument("--splits-out", type=Path, default=None,
                         help="Write family split JSON here (skipped for --tutorial-sample)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tutorial-sample", action="store_true",
                         help="Build a small, seeded, family-stratified subset instead of the full cache")
    parser.add_argument("--n", type=int, default=40, help="Sample count for --tutorial-sample")
    parser.add_argument("--skip-identity-check", action="store_true",
                         help="Skip the cross-file Model_ID identity assertion (not recommended)")
    args = parser.parse_args()

    for p in args.h5:
        if not p.exists():
            print(f"ERROR: input file not found: {p}", file=sys.stderr)
            sys.exit(1)

    print(f"Enumerating samples from {len(args.h5)} file(s)...")
    samples = enumerate_samples(args.h5)
    print(f"  {len(samples)} populated (model, geometry) samples found "
          f"across {len({s.family for s in samples})} families")

    if not args.skip_identity_check:
        assert_cross_file_model_identity(args.h5, samples, seed=args.seed)

    if args.tutorial_sample:
        rng = np.random.default_rng(args.seed)
        families = sorted({s.family for s in samples})
        per_family = max(1, args.n // len(families))
        picked = []
        for fam in families:
            fam_samples = [s for s in samples if s.family == fam]
            rng.shuffle(fam_samples)
            picked.extend(fam_samples[:per_family])
        picked = picked[:args.n]
        print(f"Tutorial sample: {len(picked)} samples across {len({s.family for s in picked})} families")
        build_cache(picked, args.out)
        return

    train, val, test = family_split(samples, seed=args.seed)
    print(f"Split — train: {len(train)} ({sorted({s.family for s in train})})  "
          f"val: {len(val)} ({sorted({s.family for s in val})})  "
          f"test: {len(test)} ({sorted({s.family for s in test})})")

    split_label = {}
    for s in train:
        split_label[f"{s.sim_key}/{s.geom_key}"] = "train"
    for s in val:
        split_label[f"{s.sim_key}/{s.geom_key}"] = "val"
    for s in test:
        split_label[f"{s.sim_key}/{s.geom_key}"] = "test"

    if args.splits_out:
        save_splits(args.splits_out, args.seed, train, val, test)
        print(f"Wrote split manifest: {args.splits_out}")

    build_cache(samples, args.out, split_label=split_label)


if __name__ == "__main__":
    main()
