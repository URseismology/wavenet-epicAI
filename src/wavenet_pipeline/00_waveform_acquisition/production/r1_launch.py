#!/usr/bin/env python
"""
R-1: re-download stations whose history was truncated by year-wide download windows.

CONCURRENCY IS THE WHOLE DESIGN. EarthScope publishes a limit of 5 concurrent connections,
enforced by TCP RESET (docs/ncf_pipeline_stages/PROVIDER_LIMITS.md). Two settings together
put us exactly at it:

    WAVENET_DOWNLOAD_THREADS=1     obspy opens 3 per task by DEFAULT -- an array at %23 was
                                   69 connections, not 23, which is what made the first
                                   attempt deliver 0.70x of what we already held
    --array=...%5                  SLURM's array throttle: at most 5 tasks run at once

The %5 throttle is PER ARRAY. Two arrays at %5 each is ten connections, so this submits ONE
array, never one per partition. That also makes partition choice irrelevant for throughput:
we only ever need 5 slots, so `standard` is ample and urseismo is not required.

Stations are ordered SMALLEST TARGET FIRST, so short stations finish early and progress is
visible long before the multi-year tail.

The 168 stations that STEP 4 is currently repackaging are held back behind a SLURM dependency
rather than skipped: R-1 downloading into scratch_work while STEP 4 rebuilds that same shard
from it would be two writers on one station.

GEOFON and the other centres publish no numbers -- GEOFON says only "arrange to send them
slowly" -- so they inherit the same cap rather than being assumed more permissive.

    r1_launch.py --root ROOT --dry-run
    r1_launch.py --root ROOT --step4-job 2087784

TESTED BEFORE DEPLOY (2026-10-08), both branches, through this exact submission path:

  plumbing   3 tasks resolved 3 DIFFERENT manifest indices (149/162/167) from the station
             list; WAVENET_R1_LIST survived --export=NONE; provider pinning active.
  no-data    ZA.A20, XA.S11, ZA.A11 -- 0 bytes, handled cleanly, task COMPLETED. These are
             known contradiction-set stations, and sorting smallest-first had put all three
             at the head of the list, so the first test proved the plumbing but NOT a
             successful download. Worth recording: a passing test on an unrepresentative
             sample is how the first canary misled us.
  with data  8Q.P22 1.10 GB / 25 days, XE.ES31 0.98 GB / 49 days, XM.NM24 0.35 GB / 82 days,
             all package_ok=True, ZERO throttling at the capped concurrency.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--step4-job", default=None,
                    help="STEP 4 job id; the overlapping stations wait on it")
    ap.add_argument("--partition", default="standard")
    ap.add_argument("--concurrency", type=int, default=5,
                    help="connections to the provider; 5 is EarthScope's published cap")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    R = args.root

    man = pd.read_csv(os.path.join(R, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    target = dict(zip(man["key"], man["days"]))
    idx_of = {k: i for i, k in enumerate(man["key"])}

    have = {}
    for p in glob.glob(os.path.join(R, "packaged_h5", "*.h5.daystate.json")):
        k = os.path.basename(p)[:-len(".h5.daystate.json")]
        try:
            have[k] = len(json.load(open(p)))
        except Exception:
            pass

    step4 = set()
    for b in glob.glob(os.path.join(R, "step4_parked_*", "BASELINE.json")):
        try:
            step4 |= set(json.load(open(b)).keys())
        except Exception:
            pass

    # routable only -- unrouted stations cannot be fetched at all (R-5), so queueing them
    # would burn slots on requests that can never succeed
    disc = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"
    routed = set()
    if os.path.exists(disc):
        d = pd.read_csv(disc)
        routed = set(d[d["status"] == "ROUTED"]["key"])

    cand = [k for k, t in target.items()
            if t == t and t > 0 and have.get(k, 0) < 0.25 * t and k in routed]
    wave1 = sorted((k for k in cand if k not in step4), key=lambda k: target[k])
    wave2 = sorted((k for k in cand if k in step4), key=lambda k: target[k])

    print("R-1 launch plan")
    print("  truncated + routable        : {:,}".format(len(cand)))
    print("  WAVE 1, free to run now     : {:,}".format(len(wave1)))
    print("  WAVE 2, held behind STEP 4  : {:,}  (same stations STEP 4 is repackaging)".format(len(wave2)))
    print("  concurrency                 : {} connections (EarthScope's published cap)".format(args.concurrency))
    print("  order                       : smallest target first ({:,} .. {:,} days)".format(
        int(target[wave1[0]]) if wave1 else 0, int(target[wave1[-1]]) if wave1 else 0))
    est = sum(target[k] for k in wave1) / (args.concurrency * 26.0) / 60.0
    print("  est. wall-clock for wave 1  : ~{:.0f} h at the measured 26 days/min/station".format(est))

    if args.dry_run:
        print("\ndry run -- nothing submitted")
        return 0

    slurm = os.path.join(R, "r1.slurm")
    open(slurm, "w").write("""#!/bin/bash
#SBATCH -J r1_redownload
#SBATCH -A tolugboj_lab
#SBATCH -n 1
#SBATCH --mem-per-cpu=7G
#SBATCH -o {root}/logs/r1_%A_%a.out
#SBATCH -e {root}/logs/r1_%A_%a.err
# One connection per task; the array's %N throttle supplies the rest of the cap.
export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
export CONDA_PKGS_DIRS={root}/.cache/conda
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
export WAVENET_PROD_ROOT={root}
export WAVENET_DISCOVERY_MANIFEST=/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv
export WAVENET_QUOTA_STOP_PCT=97
export WAVENET_DOWNLOAD_THREADS=1
IDX=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" {root}/$WAVENET_R1_LIST)
echo "[r1] manifest idx $IDX  (1 connection, array capped at {conc})"
python3 {code}/orchestrator.py "$IDX"
""".format(root=R, conc=args.concurrency,
           code="/scratch/tolugboj_lab/wavenet_ncf/code/CURRENT/production"))
    os.makedirs(os.path.join(R, "logs"), exist_ok=True)

    jobs = []
    for name, wave, dep in (("r1_wave1.txt", wave1, None),
                            ("r1_wave2.txt", wave2, "wave2")):
        if not wave:
            continue
        with open(os.path.join(R, name), "w") as f:
            f.write("\n".join(str(idx_of[k]) for k in wave) + "\n")
        cmd = ["sbatch", "--export=NONE,WAVENET_R1_LIST=" + name,
               "-p", args.partition, "--qos", args.partition, "-t", "5-00:00:00",
               "--array", "0-{}%{}".format(len(wave) - 1, args.concurrency)]
        if dep:
            # Wave 2 waits for BOTH step 4 (same shards) and wave 1 (so the 5-connection cap
            # is never exceeded by two arrays running at once).
            deps = [j for j in ([args.step4_job] if args.step4_job else []) + jobs if j]
            if deps:
                cmd += ["--dependency=afterany:" + ":".join(deps)]
        cmd.append(slurm)
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=R)
        jid = out.stdout.strip().split()[-1] if out.returncode == 0 else None
        print("  {:<12} {:>5} stations -> {}".format(
            name, len(wave), jid or out.stderr.strip()[:160]))
        if jid:
            jobs.append(jid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
