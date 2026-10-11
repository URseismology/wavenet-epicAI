#!/usr/bin/env python3
"""
Stage D poster figures (white background, 300 DPI) from the final 3-seed run.

  stage_d_training_curves.png  train/val loss, IoU and val velocity RMSE per epoch,
                               one colour per seed; the selected checkpoint is marked
  stage_d_family_iou.png       IoU of the selected checkpoint per family (M02/M10 val,
                               M09 test): bar = mean over seeds, whisker = sd, dots = seeds
  stage_d_examples.png         easy / typical / hard held-out samples (90th / 50th / 10th
                               percentile per-sample IoU within their family, seed 44 = the
                               median seed): FTAN input with the true mask and the predicted
                               mask (threshold 0.5) overlaid

Inputs: a run directory holding seed_<S>/{train_log.jsonl,val_eval.json,test_eval.json}
and, for the examples figure, the heldout_analysis.npz written by heldout_analysis.py.
Needs matplotlib (not in the cluster env; run on axon-1).

Usage:
    python src/wavenet_pipeline/03_machine_learning/plot_stage_d.py <run_dir> \\
        [--heldout <run_dir>/heldout_analysis.npz] [--out docs/ml_pipeline_stages/figures]
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np

SEED_COLORS = {42: "#2a78d6", 43: "#eb6834", 44: "#1baf7a"}  # fixed categorical order
TRUE_COLOR, PRED_COLOR = "#2a78d6", "#eb6834"
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
PERIOD = 1.0 + 0.25 * np.arange(76)
VEL = 2.0 + 0.01 * np.arange(300)

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.family": "sans-serif", "font.size": 11, "axes.titlesize": 12,
    "axes.labelcolor": INK, "axes.edgecolor": INK_2, "axes.linewidth": 0.8,
    "xtick.color": INK_2, "ytick.color": INK_2, "text.color": INK,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "legend.frameon": False,
})


def load_run(run_dir):
    seeds = {}
    for d in sorted(Path(run_dir).glob("seed_*")):
        s = int(d.name.split("_")[1])
        log = [json.loads(l) for l in (d / "train_log.jsonl").read_text().splitlines()]
        val = next(iter(json.loads((d / "val_eval.json").read_text()).values()))
        test = next(iter(json.loads((d / "test_eval.json").read_text()).values()))
        seeds[s] = {"log": log, "val": val, "test": test}
    return seeds


def training_curves(seeds, out):
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.9), constrained_layout=True)
    panels = [("loss", "Loss", ("train", "val")),
              ("iou", "IoU (threshold 0.5)", ("train", "val")),
              ("velocity_rmse", "Velocity RMSE (km/s)", ("val",))]
    for ax, (key, label, parts) in zip(axes, panels):
        for s, r in seeds.items():
            ep = np.array([e["epoch"] for e in r["log"]])
            for part in parts:
                y = np.array([e[part][key] for e in r["log"]])
                ax.plot(ep, y, color=SEED_COLORS[s], lw=1.6 if part == "val" else 1.1,
                        ls="-" if part == "val" else (0, (4, 2)), alpha=1 if part == "val" else 0.85)
            best = r["val"]["epoch"]
            yb = next(e["val"][key] for e in r["log"] if e["epoch"] == best)
            ax.plot(best, yb, "o", ms=7, color=SEED_COLORS[s], mec="white", mew=1.5, zorder=5)
        ax.set_xlabel("Epoch")
        ax.set_title(label, loc="left")
    axes[0].set_yscale("log")
    axes[0].yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.2g"))
    axes[0].yaxis.set_minor_formatter(matplotlib.ticker.FormatStrFormatter("%.2g"))
    axes[2].set_ylim(0.015, 0.05)
    handles = [plt.Line2D([], [], color=SEED_COLORS[s], lw=1.8, label=f"seed {s}") for s in seeds]
    handles += [plt.Line2D([], [], color=INK_2, lw=1.6, label="validation"),
                plt.Line2D([], [], color=INK_2, lw=1.1, ls=(0, (4, 2)), label="training"),
                plt.Line2D([], [], color=INK_2, marker="o", ls="", ms=7, mec="white",
                           label="selected checkpoint")]
    fig.legend(handles=handles, loc="outside lower center", ncol=6)
    fig.savefig(out, dpi=300)
    plt.close(fig)


def family_iou(seeds, out):
    groups = [("M02", "val"), ("M10", "val"), ("M09", "test")]
    fig, ax = plt.subplots(figsize=(5.6, 4.2), constrained_layout=True)
    x = np.arange(len(groups))
    for i, (fam, split) in enumerate(groups):
        vals = np.array([r[split]["by_family"][fam]["iou"] for r in seeds.values()])
        m, sd = vals.mean(), vals.std(ddof=1)
        ax.bar(i, m, width=0.6, color="#b7d3f6", edgecolor="none", zorder=2)
        ax.errorbar(i + 0.24, m, yerr=sd, color=INK_2, capsize=6, lw=1.2, zorder=3)
        for j, (s, v) in enumerate(zip(seeds, vals)):
            ax.plot(i + (j - 1.5) * 0.13, v, "o", ms=7, color=SEED_COLORS[s], mec="white",
                    mew=1.2, zorder=4)
        ax.text(i, max(m + sd, vals.max()) + 0.025, f"{m:.3f} ± {sd:.3f}", ha="center", va="bottom",
                color=INK, fontsize=10)
    ax.set_xticks(x, [f"{f}\n({'validation' if s == 'val' else 'test'})" for f, s in groups])
    ax.set_ylim(0, 1)
    ax.set_ylabel("IoU of selected checkpoint")
    ax.grid(axis="x", visible=False)
    ax.set_title("Per-family IoU, 3 seeds (LR 3e-3)", loc="left")
    ax.legend(handles=[plt.Line2D([], [], color=SEED_COLORS[s], marker="o", ls="", ms=7,
                                  label=f"seed {s}") for s in seeds],
              loc="upper left", ncol=3, fontsize=9)
    fig.savefig(out, dpi=300)
    plt.close(fig)


def examples(npz_path, out, picks=("M10_p90", "M09_p50", "M02_p10"),
             titles=("Easy", "Typical", "Hard"), ref=2):
    z = np.load(npz_path, allow_pickle=False)
    tags = list(z["ex_tag"])
    fig, axes = plt.subplots(1, len(picks), figsize=(13, 4.1), constrained_layout=True,
                             sharey=False)
    for ax, tag, title in zip(axes, picks, titles):
        k = tags.index(tag)
        i = int(z["ex_idx"][k])
        x = z["ex_X"][k, :76].astype(np.float32)
        y = z["ex_Y"][k, :76].astype(bool)
        p = z["ex_P"][ref, k, :76].astype(np.float32) > 0.5
        iou = (p & y).sum() / max((p | y).sum(), 1)
        ext = [PERIOD[0] - 0.125, PERIOD[-1] + 0.125, VEL[0] - 0.005, VEL[-1] + 0.005]
        ax.imshow(x.T, origin="lower", aspect="auto", extent=ext, cmap="Greys",
                  vmin=0, vmax=2.0, interpolation="nearest")
        rgba = np.zeros((300, 76, 4))
        rgba[y.T] = matplotlib.colors.to_rgba(TRUE_COLOR, 0.75)
        ax.imshow(rgba, origin="lower", aspect="auto", extent=ext, interpolation="nearest")
        ax.contour(PERIOD, VEL, p.T.astype(float), levels=[0.5], colors=PRED_COLOR,
                   linewidths=1.4)
        rows = np.where(y.any(1))[0]
        centre = np.array([VEL[y[r]].mean() for r in rows])
        ax.set_ylim(max(2.0, centre.min() - 0.35), min(5.0, centre.max() + 0.35))
        ax.grid(False)
        split = str(z["split"][i])
        pct = tag.split("_p")[1]
        ax.set_title(f"{title}: {z['family'][i]} {'validation' if split == 'val' else 'test'}, "
                     f"IoU {iou:.2f}\n({pct}th percentile of {z['family'][i]}, seed 44)",
                     loc="left", fontsize=11)
        ax.set_xlabel("Period (s)")
    axes[0].set_ylabel("Group velocity (km/s)")
    handles = [matplotlib.patches.Patch(color=TRUE_COLOR, alpha=0.75, label="true mask (theory curve ±2 bins)"),
               plt.Line2D([], [], color=PRED_COLOR, lw=1.4, label="predicted mask (p > 0.5)"),
               matplotlib.patches.Patch(facecolor="#9a9a9a", label="FTAN input (darker = more energy)")]
    fig.legend(handles=handles, loc="outside lower center", ncol=3)
    fig.savefig(out, dpi=300)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--heldout")
    ap.add_argument("--out", default="docs/ml_pipeline_stages/figures")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = load_run(args.run_dir)
    training_curves(seeds, out / "stage_d_training_curves.png")
    family_iou(seeds, out / "stage_d_family_iou.png")
    if args.heldout:
        examples(args.heldout, out / "stage_d_examples.png")
    print("wrote figures to", out)


if __name__ == "__main__":
    main()
