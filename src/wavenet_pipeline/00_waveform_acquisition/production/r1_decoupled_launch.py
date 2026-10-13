#!/usr/bin/env python
"""R-1, decoupled: one array downloads, a second packages, joined per task.

WHY TWO ARRAYS. Measured 2026-10-13 across 92 R-1 stations: downloading is 1.0% of a
task's wall time and preprocessing is 99.0% (0.53 s/day against 53.55 s/day). So five
concurrent monolithic tasks hold five provider connections while averaging about 0.05
connections actually in use -- we were pacing the whole campaign against a limit we were
nowhere near, and the real constraint was CPU all along.

Splitting lets each half be sized for what actually binds it:

    phase 1  download   --array=...%5   WAVENET_SKIP_PACKAGE=1    provider-capped
    phase 2  package    --array=...%P   WAVENET_SKIP_DOWNLOAD=1   CPU-bound, P >> 5

EarthScope's published cap of 5 concurrent connections is still respected exactly, by
phase 1 alone: phase 2 never opens a socket. See PROVIDER_LIMITS.md.

PER-TASK JOINING, not per-job. `--dependency=aftercorr:<job1>` starts task i of phase 2
when task i of phase 1 succeeds, so packaging begins on the first station while the last
is still downloading. A plain `afterok` would idle every CPU until the slowest download
finished. aftercorr requires exit 0, so a station whose download genuinely failed is not
packaged -- correct, and SLURM clears the orphaned task as DependencyNeverSatisfied
rather than leaving it pending forever.

CODE PATH IS PINNED, not CURRENT. CURRENT still points at the tree the running STEP 4
retry uses; a job that outlives a symlink repoint is how we previously ran a campaign
against code nobody could identify afterwards (R-8). The tree is named here and its
checksum is recorded in VERSIONS.txt.

    r1_decoupled_launch.py --root ROOT --dry-run
    r1_decoupled_launch.py --root ROOT --limit 3        # TEST the submission path first
    r1_decoupled_launch.py --root ROOT
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import pandas as pd

CODE_TREE = "/scratch/tolugboj_lab/wavenet_ncf/code/tree_20261013_r20/production"
DISCOVERY = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"

SLURM = """#!/bin/bash
#SBATCH -J {name}
#SBATCH -A tolugboj_lab
#SBATCH -n 1
#SBATCH --mem-per-cpu={mem}
#SBATCH -o {root}/logs/{name}_%A_%a.out
#SBATCH -e {root}/logs/{name}_%A_%a.err
set -u
export PATH="${{PATH:-/usr/bin:/bin}}"
# conda's profile and the env's activate.d hooks dereference variables with no default,
# which --export=NONE plus `set -u` turns fatal before any of our code runs. Scope the
# relaxation to activation so our own code still runs under set -u.
set +u
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
set -u
export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
export CONDA_PKGS_DIRS={root}/.cache/conda
export WAVENET_PROD_ROOT={root}
export WAVENET_DISCOVERY_MANIFEST={disc}
export WAVENET_QUOTA_STOP_PCT=97
export WAVENET_DOWNLOAD_THREADS=1
{extra}
IDX=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" {root}/{idxfile})
echo "[{name}] manifest idx $IDX"
python3 {code}/orchestrator.py "$IDX"
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--idxfile", default="r1_wave1.txt",
                    help="index file already written by r1_launch.py")
    ap.add_argument("--limit", type=int, default=0,
                    help="submit only the first N stations -- use this to TEST the path")
    ap.add_argument("--pack-concurrency", type=int, default=50,
                    help="phase 2 tasks at once; bounded by nodes, NOT by the provider")
    ap.add_argument("--download-concurrency", type=int, default=5,
                    help="phase 1 tasks at once; this IS the provider cap, do not raise")
    ap.add_argument("--partition", default="standard")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    R = args.root

    src = os.path.join(R, args.idxfile)
    if not os.path.exists(src):
        print("no index file:", src)
        return 1
    idxs = [x for x in open(src).read().split() if x.strip()]

    # Drop stations already finished, so a re-launch does not re-queue them. The
    # orchestrator would skip them anyway, but queueing them wastes array slots and
    # makes the task count a lie.
    man = pd.read_csv(os.path.join(R, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    key_of = list(man["key"])
    done = set()
    for p in glob.glob(os.path.join(R, "results", "*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if d.get("package_ok"):
            done.add("{}.{}".format(d.get("network"), d.get("station")))
    todo = [i for i in idxs if key_of[int(i)] not in done]

    if args.limit:
        todo = todo[:args.limit]

    print("R-1 DECOUPLED LAUNCH")
    print("  index file            : {} ({:,} entries)".format(args.idxfile, len(idxs)))
    print("  already package_ok    : {:,}".format(len(idxs) - len(todo) if not args.limit else 0))
    print("  to submit             : {:,}".format(len(todo)))
    print("  code tree             : {}".format(CODE_TREE))
    print("  phase 1 concurrency   : {}  (EarthScope published cap)".format(args.download_concurrency))
    print("  phase 2 concurrency   : {}  (CPU-bound; provider never contacted)".format(args.pack_concurrency))
    if not todo:
        print("  nothing to do")
        return 0
    if args.dry_run:
        print("\ndry run -- nothing submitted")
        return 0

    os.makedirs(os.path.join(R, "logs"), exist_ok=True)
    tag = "r1dec" + ("_test" if args.limit else "")
    idxname = tag + "_idx.txt"
    with open(os.path.join(R, idxname), "w") as f:
        f.write("\n".join(todo) + "\n")

    jobs = {}
    for phase, name, mem, extra, conc, walltime in (
            (1, tag + "_dl", "4G", "export WAVENET_SKIP_PACKAGE=1",
             args.download_concurrency, "2-00:00:00"),
            (2, tag + "_pk", "7G", "export WAVENET_SKIP_DOWNLOAD=1",
             args.pack_concurrency, "5-00:00:00")):
        path = os.path.join(R, name + ".slurm")
        open(path, "w").write(SLURM.format(name=name, mem=mem, root=R, disc=DISCOVERY,
                                           extra=extra, idxfile=idxname, code=CODE_TREE))
        cmd = ["sbatch", "--export=NONE",
               "-p", args.partition, "--qos", args.partition, "-t", walltime,
               "--array", "0-{}%{}".format(len(todo) - 1, conc)]
        if phase == 2:
            # per-TASK, so packaging starts on station 0 while station N still downloads
            cmd += ["--dependency=aftercorr:" + jobs[1]]
        cmd.append(path)
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=R)
        if out.returncode != 0:
            print("  phase {} SUBMIT FAILED: {}".format(phase, out.stderr.strip()[:300]))
            return 1
        jobs[phase] = out.stdout.strip().split()[-1]
        print("  phase {} ({:<12}) -> job {}".format(phase, name, jobs[phase]))

    print("\n  phase 2 is chained per-task: aftercorr:{}".format(jobs[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
