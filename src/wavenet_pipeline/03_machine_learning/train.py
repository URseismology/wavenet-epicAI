#!/usr/bin/env python3
"""
Stage D — Training Process

Trains UNetSeg (Stage B) with CombinedLoss on the Stage C DataLoaders built from a
build_ml_cache.py cache + its splits JSON. Device-agnostic (CUDA if available, else
CPU) so the same entrypoint serves the local axon-1 diagnostic and the Bluehive
SLURM job (see src/wavenet_pipeline/03_machine_learning/slurm/train_bluehive.sbatch).

Outputs, all under --out-dir:
  config.json      resolved args + environment (torch version, device, git commit)
  train_log.jsonl  one JSON record per epoch (machine-readable)
  train_log.txt    same, one human-readable line per epoch
  best.pt          checkpoint with the highest val IoU so far
  last.pt          checkpoint after the most recent epoch (for --resume after
                   preemption / wall-time kill)

Validation metrics are dataset-level, not a mean of per-batch values: IoU/Dice
accumulate intersection/union pixel counts over the whole val split at threshold 0.5
(same definition as metrics.iou_score/dice_coefficient, just not batch-averaged).
velocity_rmse is metrics.velocity_error averaged over batches, weighted by batch size.

LR: Stage B's 1-sample overfit did NOT converge at the originally-planned lr=1e-4
(needed 3e-3 / ~800 iters), so --lr has no silent default inherited from the plan —
see docs/ml_pipeline_stages/stage_d_training_process.md for the LR evidence used.

Usage:
    PYTHONPATH=src python -m wavenet_pipeline.03_machine_learning.train \\
        <cache.h5> <splits.json> --out-dir runs/<name> --lr 1e-3 --epochs 100
"""

import argparse
import json
import math
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np
import torch

from .losses import CombinedLoss
from .metrics import velocity_error
from .model import UNetSeg
from .stage_c_input_batching import get_dataloaders


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=Path(__file__).parent, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _family(key):
    """'M02_0000/separation_127.0km' -> 'M02'."""
    return key.split("_", 1)[0]


def _summarize(inter, union, pred_pos, tgt_pos, vel_sum, n):
    return {
        "iou": inter / union if union else 1.0,
        "dice": 2 * inter / (pred_pos + tgt_pos) if (pred_pos + tgt_pos) else 1.0,
        "velocity_rmse": vel_sum / n,
        "pred_pos_frac_over_target": pred_pos / tgt_pos if tgt_pos else float("nan"),
        "n_samples": n,
    }


def run_epoch(model, loader, criterion, device, optimizer=None, max_batches=None,
              grad_clip=None):
    """One pass over `loader`. Trains if `optimizer` is given, else evaluates under
    no_grad. Returns dataset-level loss/IoU/Dice/velocity_rmse; in eval mode also a
    `by_family` breakdown (same definitions, restricted to each family's samples).
    With `grad_clip`, gradients are clipped to that global L2 norm and the epoch's
    mean pre-clip norm and fraction of clipped steps are reported."""
    train = optimizer is not None
    model.train(train)
    loss_sum = vel_sum = 0.0
    n = 0
    inter = union = pred_pos = tgt_pos = 0.0
    fam = {}  # family -> [inter, union, pred_pos, tgt_pos, vel_sum, n]
    gnorm_sum, n_clipped, n_steps = 0.0, 0, 0

    with torch.set_grad_enabled(train):
        for b, (x, y, keys) in enumerate(loader):
            if max_batches is not None and b >= max_batches:
                break
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            logits = model(x)
            loss = criterion(logits, y)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"non-finite loss {loss.item()} at batch {b}")
            if train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                if grad_clip is not None:
                    gn = torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip).item()
                    gnorm_sum += gn
                    n_clipped += gn > grad_clip
                    n_steps += 1
                optimizer.step()

            bs = x.shape[0]
            loss_sum += loss.item() * bs
            vel_sum += velocity_error(logits.detach(), y) * bs
            n += bs
            pb = torch.sigmoid(logits.detach()) > 0.5
            tb = y > 0.5
            inter += (pb & tb).sum().item()
            union += (pb | tb).sum().item()
            pred_pos += pb.sum().item()
            tgt_pos += tb.sum().item()

            if not train:
                fams = [_family(k) for k in keys]
                for f in set(fams):
                    m = torch.tensor([g == f for g in fams], device=device)
                    pf, tf = pb[m], tb[m]
                    acc = fam.setdefault(f, [0.0] * 5 + [0])
                    acc[0] += (pf & tf).sum().item()
                    acc[1] += (pf | tf).sum().item()
                    acc[2] += pf.sum().item()
                    acc[3] += tf.sum().item()
                    acc[4] += velocity_error(logits.detach()[m], y[m]) * int(m.sum())
                    acc[5] += int(m.sum())

    out = {"loss": loss_sum / n,
           **_summarize(inter, union, pred_pos, tgt_pos, vel_sum, n)}
    if fam:
        out["by_family"] = {f: _summarize(*fam[f]) for f in sorted(fam)}
    if n_steps:
        out["grad_norm_mean"] = gnorm_sum / n_steps
        out["clip_frac"] = n_clipped / n_steps
    return out


def save_checkpoint(path, model, optimizer, scheduler, epoch, best_val_iou, args):
    tmp = Path(str(path) + ".tmp")
    torch.save({
        "epoch": epoch,
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scheduler_state": scheduler.state_dict() if scheduler else None,
        "best_val_iou": best_val_iou,
        "args": vars(args),
    }, tmp)
    os.replace(tmp, path)  # atomic: a wall-time kill mid-save can't corrupt last.pt


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cache_path")
    p.add_argument("splits_path")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--lr", type=float, required=True,
                   help="required on purpose -- see module docstring re: Stage B LR finding")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--num-workers", type=int, default=4)
    p.add_argument("--weight-decay", type=float, default=1e-4)
    p.add_argument("--plateau-patience", type=int, default=5,
                   help="epochs without val-IoU improvement before LR is halved")
    p.add_argument("--early-stop-patience", type=int, default=20,
                   help="epochs without val-IoU improvement before stopping")
    p.add_argument("--min-lr", type=float, default=1e-6)
    p.add_argument("--grad-clip", type=float, default=None,
                   help="clip gradients to this global L2 norm (default: no clipping)")
    p.add_argument("--smooth-window", type=int, default=1,
                   help="track the trailing mean of val IoU over this many epochs; it drives "
                        "best.pt selection, the plateau scheduler and early stopping "
                        "(1 = raw val IoU)")
    p.add_argument("--max-train-batches", type=int, default=None,
                   help="debug only: cap train batches per epoch")
    p.add_argument("--max-val-batches", type=int, default=None,
                   help="debug only: cap val batches per epoch")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--resume", action="store_true",
                   help="continue from <out-dir>/last.pt if it exists")
    args = p.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available()
                          else ("cpu" if args.device == "auto" else args.device))
    if device.type == "cuda":
        torch.backends.cudnn.benchmark = True

    loaders = get_dataloaders(args.cache_path, args.splits_path,
                              batch_size=args.batch_size, num_workers=args.num_workers,
                              seed=args.seed, pin_memory=(device.type == "cuda"))
    model = UNetSeg(in_channels=1, out_channels=1).to(device)
    criterion = CombinedLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=args.plateau_patience,
        min_lr=args.min_lr)

    # best_val_iou holds the *tracked* score: raw val IoU, or its trailing mean over
    # --smooth-window epochs. iou_hist feeds that mean.
    start_epoch, best_val_iou, epochs_since_best, iou_hist = 0, -math.inf, 0, []
    last_ckpt = out / "last.pt"
    if args.resume and last_ckpt.exists():
        ck = torch.load(last_ckpt, map_location=device)
        model.load_state_dict(ck["model_state"])
        optimizer.load_state_dict(ck["optimizer_state"])
        if ck["scheduler_state"]:
            scheduler.load_state_dict(ck["scheduler_state"])
        start_epoch = ck["epoch"] + 1
        best_val_iou = ck["best_val_iou"]
        # epochs_since_best is recomputed from the log so early stopping survives resume
        log_path = out / "train_log.jsonl"
        if log_path.exists():
            recs = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
            recs = [r for r in recs if r["epoch"] < start_epoch]
            iou_hist = [r["val"]["iou"] for r in recs]
            best_ep = max((r["epoch"] for r in recs
                           if r.get("val_iou_tracked", r["val"]["iou"]) >= best_val_iou),
                          default=start_epoch - 1)
            epochs_since_best = start_epoch - 1 - best_ep
        print(f"resumed from {last_ckpt} at epoch {start_epoch} "
              f"(best val IoU {best_val_iou:.4f})", flush=True)

    config = {
        **vars(args),
        "device": str(device),
        "cuda_device_name": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "torch": torch.__version__,
        "python": platform.python_version(),
        "host": platform.node(),
        "git_commit": _git_commit(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "n_params": sum(p.numel() for p in model.parameters()),
        "n_train": len(loaders["train"].dataset),
        "n_val": len(loaders["val"].dataset),
    }
    (out / ("config_resume.json" if start_epoch else "config.json")).write_text(
        json.dumps(config, indent=2))
    print(json.dumps(config, indent=2), flush=True)

    with open(out / "train_log.jsonl", "a") as jlog, open(out / "train_log.txt", "a") as tlog:
        for epoch in range(start_epoch, args.epochs):
            t0 = time.time()
            tr = run_epoch(model, loaders["train"], criterion, device, optimizer,
                           args.max_train_batches, grad_clip=args.grad_clip)
            t1 = time.time()
            va = run_epoch(model, loaders["val"], criterion, device, None,
                           args.max_val_batches)
            t2 = time.time()
            lr_now = optimizer.param_groups[0]["lr"]
            iou_hist.append(va["iou"])
            tracked = float(np.mean(iou_hist[-args.smooth_window:]))
            scheduler.step(tracked)

            improved = tracked > best_val_iou
            if improved:
                best_val_iou, epochs_since_best = tracked, 0
                save_checkpoint(out / "best.pt", model, optimizer, scheduler, epoch,
                                best_val_iou, args)
            else:
                epochs_since_best += 1
            save_checkpoint(last_ckpt, model, optimizer, scheduler, epoch, best_val_iou, args)

            rec = {"epoch": epoch, "lr": lr_now, "train": tr, "val": va,
                   "train_s": round(t1 - t0, 1), "val_s": round(t2 - t1, 1),
                   "val_iou_tracked": tracked, "best_val_iou": best_val_iou,
                   "improved": improved, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
            jlog.write(json.dumps(rec) + "\n"); jlog.flush()
            line = (f"epoch {epoch:3d} lr {lr_now:.2e} | train loss {tr['loss']:.4f} "
                    f"IoU {tr['iou']:.4f} Dice {tr['dice']:.4f} | val loss {va['loss']:.4f} "
                    f"IoU {va['iou']:.4f} Dice {va['dice']:.4f} vRMSE {va['velocity_rmse']:.3f} "
                    f"pred/tgt {va['pred_pos_frac_over_target']:.2f}"
                    + (f" IoU-ma{args.smooth_window} {tracked:.4f}" if args.smooth_window > 1 else "")
                    + (f" | gnorm {tr['grad_norm_mean']:.2f} clip {tr['clip_frac']:.0%}"
                       if "clip_frac" in tr else "")
                    + f" | {t1 - t0:.0f}s+{t2 - t1:.0f}s{' *best' if improved else ''}")
            if "by_family" in va:
                line += " | " + " ".join(
                    f"{f} IoU {m['iou']:.4f} vRMSE {m['velocity_rmse']:.3f}"
                    for f, m in va["by_family"].items())
            tlog.write(line + "\n"); tlog.flush()
            print(line, flush=True)

            if epochs_since_best >= args.early_stop_patience:
                print(f"early stop: no tracked val IoU improvement for {epochs_since_best} "
                      f"epochs", flush=True)
                break

    print(f"done. best tracked val IoU (window {args.smooth_window}) {best_val_iou:.4f} "
          f"-> {out / 'best.pt'}", flush=True)


if __name__ == "__main__":
    main()
