#!/usr/bin/env python3
"""
Stage D diagnostic: re-score saved checkpoints on the val split, broken down by
family. Uses train.run_epoch, so overall IoU/Dice/vRMSE are the same definitions
logged during training and should match the logged val numbers for the checkpoint's
epoch (printed side by side as a sanity check).

Usage:
    PYTHONPATH=src python -m wavenet_pipeline.03_machine_learning.eval_by_family \\
        <cache.h5> <splits.json> <ckpt.pt> [<ckpt.pt> ...] [--json out.json]
"""

import argparse
import json
from pathlib import Path

import torch

from .losses import CombinedLoss
from .model import UNetSeg
from .stage_c_input_batching import get_dataloaders
from .train import run_epoch


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cache_path")
    p.add_argument("splits_path")
    p.add_argument("checkpoints", nargs="+")
    p.add_argument("--split", default="val", choices=["val", "test"])
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--num-workers", type=int, default=4)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--json", help="also write all results to this file")
    args = p.parse_args()

    device = torch.device(args.device)
    loader = get_dataloaders(args.cache_path, args.splits_path,
                             batch_size=args.batch_size, num_workers=args.num_workers,
                             pin_memory=(device.type == "cuda"))[args.split]
    results = {}

    for ck_path in args.checkpoints:
        ck = torch.load(ck_path, map_location=device)
        model = UNetSeg(in_channels=1, out_channels=1).to(device)
        model.load_state_dict(ck["model_state"])
        # loss only; checkpoints from before --pos-weight existed trained with 15
        pos_weight = ck["args"].get("pos_weight", 15.0)
        criterion = CombinedLoss(pos_weight=pos_weight)
        r = run_epoch(model, loader, criterion, device)

        logged = None
        log = Path(ck_path).parent / "train_log.jsonl"
        if log.exists():
            for line in log.read_text().splitlines():
                rec = json.loads(line)
                if rec["epoch"] == ck["epoch"]:
                    logged = rec["val"]

        print(f"\n{ck_path}  (epoch {ck['epoch']}, lr {ck['args']['lr']:g}, pos_weight {pos_weight:g})")
        print(f"  {'all':5s} IoU {r['iou']:.4f} Dice {r['dice']:.4f} "
              f"vRMSE {r['velocity_rmse']:.4f} pred/tgt {r['pred_pos_frac_over_target']:.2f} "
              f"n={r['n_samples']}")
        if logged and args.split == "val":
            print(f"  {'log':5s} IoU {logged['iou']:.4f} Dice {logged['dice']:.4f} "
                  f"vRMSE {logged['velocity_rmse']:.4f}  (as logged during training)")
        for f, m in r["by_family"].items():
            print(f"  {f:5s} IoU {m['iou']:.4f} Dice {m['dice']:.4f} "
                  f"vRMSE {m['velocity_rmse']:.4f} pred/tgt {m['pred_pos_frac_over_target']:.2f} "
                  f"n={m['n_samples']}")
        for f, m in [("all", r), *r["by_family"].items()]:
            print(f"  {f:5s} vRMSE soft per-batch {m['velocity_rmse']:.4f} pooled "
                  f"{m['velocity_rmse_pooled']:.4f} | peak per-batch {m['velocity_rmse_peak']:.4f} "
                  f"pooled {m['velocity_rmse_peak_pooled']:.4f}")
        results[ck_path] = {"epoch": ck["epoch"], "lr": ck["args"]["lr"],
                            "pos_weight": pos_weight, **r}

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
