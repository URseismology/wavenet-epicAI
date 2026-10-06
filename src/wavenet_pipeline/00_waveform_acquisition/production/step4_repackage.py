#!/usr/bin/env python
"""
STEP 4: re-package stations whose shards hold uncorrected (raw-counts) channels.

Unblocked 2026-10-06 by two fixes landing together:
  * responses are now COMPLETE -- the forced re-fetch gave 1,609 of 1,612 stations full
    channel coverage, so re-packaging now produces corrected data rather than reproducing
    the same raw counts;
  * the evalresp cache is byte-capped (R-11) -- without it, re-packaging 312 long-history
    stations is precisely the workload that OOM-killed 182 tasks.

No network. Reads retained raw SEED, so this is CPU-bound and cheap compared with the
re-download path that R-14 currently blocks.

PARK AND REBUILD FRESH, never append. Appending into the existing shard is unsafe on three
counts, all verified in the code:
  1. `build_master_h5` sets `units` only at dataset CREATION. On append it checks
     sampling_rate and refuses on mismatch, but never looks at units -- so writing corrected
     displacement into a dataset created as 'counts' would leave it LABELLED 'counts'. That
     is worse than today: the labels are currently honest.
  2. `done_days` in the day-state would skip every day, so the re-package would do nothing.
  3. `package_ok` in the result JSON makes the orchestrator skip the station entirely
     (orchestrator.py:388).
Parking all four artifacts together sidesteps all three and keeps the before-state as the
verification baseline.

    step4_repackage.py --root ROOT --limit 20          # controlled batch
    step4_repackage.py --root ROOT                     # everything
    step4_repackage.py --root ROOT --limit 20 --dry-run
"""
import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import h5py
import numpy as np
import pandas as pd


def candidates(root):
    """Stations whose shard has uncorrected channels AND whose raw SEED is still present.
    Read from the FILES -- a result JSON saying response_ok is not evidence about units."""
    out = []
    for p in sorted(glob.glob(os.path.join(root, "packaged_h5", "*.h5"))):
        key = os.path.basename(p)[:-3]
        raw = os.path.join(root, "scratch_work", key.replace(".", "_"), "mseed")
        if not os.path.isdir(raw):
            continue
        try:
            with h5py.File(p, "r", locking=False) as f:
                g = f[list(f.keys())[0]]
                ch = sorted(c for c in g if not c.startswith("_"))
                if not ch:
                    continue
                cnt = [c for c in ch if str(g[c].attrs.get("units")) != "m"]
                if not cnt:
                    continue
                days = int((np.asarray(g["_coverage"][ch[0]][:]) > 0).sum()) if "_coverage" in g else 0
        except Exception:
            continue
        out.append(dict(key=key, days_before=days, n_chan=len(ch), n_counts=len(cnt),
                        kind="all_counts" if len(cnt) == len(ch) else "mixed",
                        raw_files=len(os.listdir(raw))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = all candidates")
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--partition", default="urseismo")
    args = ap.parse_args()

    cands = candidates(args.root)
    print("STEP 4 candidates (uncorrected channels + raw retained): {}".format(len(cands)))
    mixed = [c for c in cands if c["kind"] == "mixed"]
    allc = [c for c in cands if c["kind"] == "all_counts"]
    print("  mixed units : {}".format(len(mixed)))
    print("  all counts  : {}".format(len(allc)))
    print("  days currently unusable: {:,}".format(sum(c["days_before"] for c in cands)))

    if args.limit:
        # Span BOTH classes deliberately -- they exercise different paths (partial response
        # vs none at all), and a batch of only one kind would not generalise.
        rng = random.Random(args.seed)
        half = max(1, args.limit // 2)
        pick = (rng.sample(mixed, min(half, len(mixed)))
                + rng.sample(allc, min(args.limit - half, len(allc))))
    else:
        pick = cands
    pick.sort(key=lambda c: c["key"])
    print("")
    print("selected {} station(s):".format(len(pick)))
    print("  {:<12} {:<11} {:>7} {:>7} {:>9}".format("STATION", "kind", "days", "chans", "counts"))
    for c in pick:
        print("  {:<12} {:<11} {:>7} {:>7} {:>9}".format(
            c["key"], c["kind"], c["days_before"], c["n_chan"], c["n_counts"]))

    if args.dry_run:
        print("\ndry run -- nothing parked, nothing submitted")
        return 0

    # ---- park: shard + daystate + qc + result, together ----
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    park = os.path.join(args.root, "step4_parked_" + stamp)
    os.makedirs(park, exist_ok=True)
    man = pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    idx_of = {k: i for i, k in enumerate(man["key"])}

    baseline, idxs, moved = {}, [], 0
    for c in pick:
        k = c["key"]
        for suffix in (".h5", ".h5.daystate.json", ".h5.qc.json"):
            src = os.path.join(args.root, "packaged_h5", k + suffix)
            if os.path.exists(src):
                shutil.move(src, os.path.join(park, k + suffix)); moved += 1
        i = idx_of.get(k)
        if i is not None:
            for rp in glob.glob(os.path.join(args.root, "results",
                                             "{:04d}_{}.json".format(i, k.replace(".", "_")))):
                shutil.move(rp, os.path.join(park, os.path.basename(rp))); moved += 1
            idxs.append(i)
        baseline[k] = c
    json.dump(baseline, open(os.path.join(park, "BASELINE.json"), "w"), indent=1)
    print("")
    print("parked {} artefact(s) -> {}".format(moved, park))
    print("baseline recorded -> {}/BASELINE.json".format(park))

    # ---- submit: packaging only, no network ----
    slurm = os.path.join(args.root, "step4.slurm")
    open(slurm, "w").write("""#!/bin/bash
#SBATCH -J step4_repack
#SBATCH -A tolugboj_lab
#SBATCH -n 1
#SBATCH --mem-per-cpu=7G
#SBATCH -o {root}/logs/step4_%A_%a.out
#SBATCH -e {root}/logs/step4_%A_%a.err
# STEP 4: re-package from retained raw SEED. No network -- WAVENET_SKIP_DOWNLOAD=1 means
# MassDownloader never runs, so this neither competes for provider bandwidth with the R-14
# window sweep nor writes anything into scratch_work.
export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
export CONDA_PKGS_DIRS={root}/.cache/conda
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
export WAVENET_PROD_ROOT={root}
export WAVENET_DISCOVERY_MANIFEST=/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv
export WAVENET_QUOTA_STOP_PCT=97
export WAVENET_SKIP_DOWNLOAD=1
IDX=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" {root}/step4_idx.txt)
echo "[step4] repackaging manifest idx $IDX"
python3 {code}/orchestrator.py "$IDX"
""".format(root=args.root, code="/scratch/tolugboj_lab/wavenet_ncf/code/CURRENT/production"))
    with open(os.path.join(args.root, "step4_idx.txt"), "w") as f:
        f.write("\n".join(str(i) for i in idxs) + "\n")
    os.makedirs(os.path.join(args.root, "logs"), exist_ok=True)

    out = subprocess.run(["sbatch", "--export=NONE", "-p", args.partition,
                          "--qos", args.partition, "-t", "08:00:00",
                          "--array", "0-{}".format(len(idxs) - 1), slurm],
                         capture_output=True, text=True, cwd=args.root)
    print("")
    print(out.stdout.strip() or out.stderr.strip()[:300])
    print("verify afterwards against {}/BASELINE.json".format(park))
    return 0


if __name__ == "__main__":
    sys.exit(main())
