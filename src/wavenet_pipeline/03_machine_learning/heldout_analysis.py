#!/usr/bin/env python3
"""
Stage D diagnostic: per-sample scores of saved checkpoints on the held-out splits
(val + test), plus a few saved example predictions for figures.

For every checkpoint and every val/test sample it records IoU, Dice, predicted and
target positive-pixel counts (threshold 0.5) and the sum of squared row-velocity errors
under exactly the metrics.velocity_error definition (soft centroid of sigmoid
probabilities vs centroid of the target mask, curve rows 0:PERIOD_BINS). From those it
reports two velocity RMSEs side by side:

  logged : mean of per-batch RMSEs, weighted by batch size (what train.py logs)
  pooled : sqrt of the mean squared row error over every row of every sample

The two differ only in aggregation; pooled >= logged by Jensen's inequality.

Examples: using the reference checkpoint (--ref), it picks per family the samples at the 10th, 50th and 90th
percentile of per-sample IoU and saves X, Y and every checkpoint's probabilities for
them.

Usage:
    PYTHONPATH=src python -m wavenet_pipeline.03_machine_learning.heldout_analysis \\
        <cache.h5> <splits.json> <ckpt.pt> [<ckpt.pt> ...] --ref <ckpt.pt> --out out.npz
"""

import argparse
import json

import numpy as np
import torch

from .ftan_grid import PERIOD_BINS, VEL_BINS, VEL_MIN, VEL_MAX
from .metrics import velocity_error
from .model import UNetSeg
from .stage_c_input_batching import get_dataloaders
from .train import _family


def row_velocity_sq_err(logits, y):
    """Per-sample sum over curve rows of squared centroid-velocity error (km/s)^2,
    same centroid definition as metrics.velocity_error."""
    p = torch.sigmoid(logits)[:, 0, :PERIOD_BINS]
    t = y[:, 0, :PERIOD_BINS]
    bins = torch.arange(VEL_BINS, dtype=torch.float32, device=p.device)
    scale = (VEL_MAX - VEL_MIN) / VEL_BINS
    pv = (p / (p.sum(-1, keepdim=True) + 1e-8) * bins).sum(-1) * scale + VEL_MIN
    tv = (t / (t.sum(-1, keepdim=True) + 1e-8) * bins).sum(-1) * scale + VEL_MIN
    return ((pv - tv) ** 2).sum(-1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cache_path")
    ap.add_argument("splits_path")
    ap.add_argument("checkpoints", nargs="+")
    ap.add_argument("--ref", required=True, help="checkpoint used to pick examples")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--num-workers", type=int, default=4)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    loaders = get_dataloaders(args.cache_path, args.splits_path, batch_size=args.batch_size,
                              num_workers=args.num_workers, pin_memory=device.type == "cuda")
    models = {}
    for ck_path in args.checkpoints:
        ck = torch.load(ck_path, map_location=device, weights_only=False)
        m = UNetSeg(in_channels=1, out_channels=1).to(device)
        m.load_state_dict(ck["model_state"])
        models[ck_path] = m.eval()

    keys, split_of = [], []
    per = {c: {k: [] for k in ("iou", "dice", "pred_pos", "tgt_pos", "vel_sq")}
           for c in models}
    batch_rmse = {c: {} for c in models}  # split -> [sum(rmse*bs), n]
    with torch.no_grad():
        for split in ("val", "test"):
            for x, y, ks in loaders[split]:
                keys += ks
                split_of += [split] * len(ks)
                x, y = x.to(device), y.to(device)
                tb = y > 0.5
                for c, m in models.items():
                    logits = m(x)
                    pb = torch.sigmoid(logits) > 0.5
                    inter = (pb & tb).flatten(1).sum(1).float()
                    union = (pb | tb).flatten(1).sum(1).float()
                    pp, tp = pb.flatten(1).sum(1).float(), tb.flatten(1).sum(1).float()
                    r = per[c]
                    r["iou"] += (inter / union.clamp(min=1)).tolist()
                    r["dice"] += (2 * inter / (pp + tp).clamp(min=1)).tolist()
                    r["pred_pos"] += pp.tolist()
                    r["tgt_pos"] += tp.tolist()
                    r["vel_sq"] += row_velocity_sq_err(logits, y).tolist()
                    acc = batch_rmse[c].setdefault(split, [0.0, 0])
                    acc[0] += velocity_error(logits, y) * len(ks)
                    acc[1] += len(ks)

    keys = np.array(keys)
    split_of = np.array(split_of)
    fams = np.array([_family(k) for k in keys])
    summary = {}
    for c in models:
        r = {k: np.array(v) for k, v in per[c].items()}
        s = {}
        for split in ("val", "test"):
            groups = {"all": split_of == split}
            groups.update({f: (split_of == split) & (fams == f) for f in np.unique(fams[split_of == split])})
            for g, sel in groups.items():
                s[f"{split}/{g}"] = {
                    "n": int(sel.sum()),
                    "iou_sample_mean": float(r["iou"][sel].mean()),
                    "iou_sample_p10": float(np.percentile(r["iou"][sel], 10)),
                    "iou_sample_p90": float(np.percentile(r["iou"][sel], 90)),
                    "pred_over_tgt": float(r["pred_pos"][sel].sum() / r["tgt_pos"][sel].sum()),
                    "vrmse_pooled": float(np.sqrt(r["vel_sq"][sel].sum() / (sel.sum() * PERIOD_BINS))),
                }
            acc = batch_rmse[c][split]
            s[f"{split}/all"]["vrmse_logged_def"] = acc[0] / acc[1]
        summary[c] = s
        print(c)
        for g, v in s.items():
            print(f"  {g:10s} n={v['n']:5d} IoU(sample mean) {v['iou_sample_mean']:.4f} "
                  f"p10 {v['iou_sample_p10']:.3f} p90 {v['iou_sample_p90']:.3f} "
                  f"pred/tgt {v['pred_over_tgt']:.3f} vRMSE pooled {v['vrmse_pooled']:.4f}"
                  + (f" logged-def {v['vrmse_logged_def']:.4f}" if "vrmse_logged_def" in v else ""))

    # Examples from the reference checkpoint: p10 / p50 / p90 per-sample IoU per family.
    ref_iou = np.array(per[args.ref]["iou"])
    ex_idx, ex_tag = [], []
    for f in np.unique(fams):
        sel = np.where(fams == f)[0]
        order = sel[np.argsort(ref_iou[sel])]
        for q in (10, 50, 90):
            ex_idx.append(int(order[int(round(q / 100 * (len(order) - 1)))]))
            ex_tag.append(f"{f}_p{q}")

    # Re-read the chosen samples by position within their split.
    pos_in_split = {}
    for split in ("val", "test"):
        idx = np.where(split_of == split)[0]
        pos_in_split.update({int(i): (split, j) for j, i in enumerate(idx)})
    X, Y, P = [], [], {c: [] for c in models}
    with torch.no_grad():
        for i in ex_idx:
            split, j = pos_in_split[i]
            x, y, meta = loaders[split].dataset[j]
            assert f"{meta['sim_key']}/{meta['geom_key']}" == keys[i]
            X.append(x[0].numpy())
            Y.append(y[0].numpy())
            for c, m in models.items():
                P[c].append(torch.sigmoid(m(x[None].to(device)))[0, 0].cpu().numpy())

    out = {
        "keys": keys, "split": split_of, "family": fams,
        "ckpts": np.array(list(models)),
        "ex_idx": np.array(ex_idx), "ex_tag": np.array(ex_tag),
        "ex_X": np.stack(X).astype(np.float16), "ex_Y": np.stack(Y).astype(np.uint8),
        "ex_P": np.stack([np.stack(P[c]) for c in models]).astype(np.float16),
    }
    for ci, c in enumerate(models):
        for k, v in per[c].items():
            out[f"{k}_{ci}"] = np.array(v, dtype=np.float32)
    np.savez_compressed(args.out, **out)
    with open(args.out.replace(".npz", ".json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
