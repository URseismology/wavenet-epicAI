#!/usr/bin/env python
"""Re-queue array tasks that stopped without finishing. Nothing else re-queues them.

WHY THIS EXISTS. R-21 left a residual we accepted knowingly: a task that exceeds its
walltime TIMEOUTs and stops, and no mechanism re-submits it. Over one month that produced
**64 orphaned tasks** across six jobs -- 33 of them from preempt's 2-day cap, and 7 from
pk_Y_exp which timed out even at 15 days. The campaign looked finished; it had quietly
stalled. A fix nobody re-runs is not deployed (PIPELINE_PRINCIPLES §4), and neither is a
re-run nobody triggers.

Tasks are RESUMABLE -- the orchestrator skips days already in the daystate -- so repeated
passes converge. The only thing missing was something to force a pass.

REFUSES TO SUBMIT WHEN SCRATCH IS OVER THE GUARD. The orchestrator stops itself at
WAVENET_QUOTA_STOP_PCT (97%) and exits COMPLETED in ~30 s having done nothing. On
2026-11-17 that silently no-opped 73 re-queued tasks and looked exactly like a successful
run. Submitting into a full filesystem manufactures false evidence of progress, so this
checks the same quota file the orchestrator reads and declines.

SKIPS WHAT IS ALREADY DONE OR ALREADY QUEUED, so running it hourly is harmless.

    requeue_sweep.py --root ROOT --dry-run
    requeue_sweep.py --root ROOT
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

import pandas as pd

# job-name prefix -> (slurm script, index file, partition, walltime)
# urseismo for everything: preempt's 2-day cap caused 33 of the 64 observed timeouts.
JOBS = {
    "pk_":        ("r1dec_pk.slurm",     "r1dec_idx.txt",        "urseismo", "15-00:00:00"),
    "r1dec_pk":   ("r1dec_pk.slurm",     "r1dec_idx.txt",        "urseismo", "15-00:00:00"),
    "s4_retry":   ("step4_retry.slurm",  "step4_retry_idx.txt",  "urseismo", "15-00:00:00"),
    "step4_repack": ("step4_retry.slurm", "step4_retry_idx.txt", "urseismo", "15-00:00:00"),
    "s4_refetch": ("step4_refetch.slurm", "step4_refetch_idx.txt", "urseismo", "15-00:00:00"),
}
DEAD = ("TIMEOUT", "FAILED", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "CANCELLED")


def quota_pct(root):
    """The same number the orchestrator's guard reads, so we agree with it by construction."""
    try:
        gid = os.stat(root).st_gid
        for line in open("/software/circ/share/quota-rep/{}".format(gid)):
            f = line.split()
            if len(f) >= 4 and f[0].endswith("scratch"):
                return 100.0 * float(f[1]) / float(f[3])
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--since", default="2026-10-01", help="sacct start date")
    ap.add_argument("--max-pct", type=float, default=96.0,
                    help="refuse to submit if scratch is above this percent of hard limit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    R = args.root

    pct = quota_pct(R)
    print("[sweep] {}  scratch at {}".format(
        time.strftime("%Y-%m-%dT%H:%M:%S"),
        "{:.2f}% of hard".format(pct) if pct is not None else "UNKNOWN"))
    if pct is not None and pct > args.max_pct:
        print("[sweep] REFUSING to submit: {:.2f}% > {:.1f}%. The orchestrator's own guard "
              "would stop every task in ~30 s and report COMPLETED, which looks like "
              "success. Waiting for the inspector to free space.".format(pct, args.max_pct))
        return 0

    # which stations are already finished -- never re-queue these
    done = set()
    for p in glob.glob(os.path.join(R, "results", "*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if d.get("package_ok"):
            done.add("{}.{}".format(d.get("network"), d.get("station")))

    man = pd.read_csv(os.path.join(R, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    key_of = list(man["key"])

    # anything currently queued or running, so an hourly run does not pile up
    live = set()
    out = subprocess.run(["squeue", "-u", os.environ.get("USER", "tolugboj"), "-h", "-r",
                          "-o", "%j|%i"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.split("|")
        if len(f) == 2 and "_" in f[1]:
            t = f[1].split("_")[1]
            if t.isdigit():
                live.add((f[0], int(t)))

    sacct = subprocess.run(
        ["sacct", "-X", "-n", "-P", "-S", args.since, "-o", "JobID,JobName,State"],
        capture_output=True, text=True).stdout

    # last state wins per (jobname, task): a task re-run successfully is not an orphan
    latest = {}
    for line in sacct.splitlines():
        f = line.split("|")
        if len(f) != 3 or "_" not in f[0]:
            continue
        t = f[0].split("_")[1]
        if not t.isdigit():
            continue
        latest[(f[1], int(t))] = f[2].split()[0]

    plan = {}
    for (name, task), state in sorted(latest.items()):
        if state not in DEAD:
            continue
        cfg = next((v for k, v in JOBS.items() if name.startswith(k)), None)
        if cfg is None:
            continue
        if (name, task) in live:
            continue
        script, idxfile, part, wall = cfg
        ip = os.path.join(R, idxfile)
        if not os.path.exists(ip):
            continue
        idxs = [int(x) for x in open(ip).read().split()]
        if task >= len(idxs):
            continue
        key = key_of[idxs[task]]
        if key in done:
            continue
        plan.setdefault((script, idxfile, part, wall), []).append((task, key))

    if not plan:
        print("[sweep] nothing to re-queue")
        return 0

    for (script, idxfile, part, wall), items in plan.items():
        tasks = sorted({t for t, _ in items})
        print("[sweep] {:<22} {:>3} orphaned task(s) -> {} {}".format(
            script, len(tasks), part, wall))
        for t, k in sorted(items)[:8]:
            print("          task {:<5} {}".format(t, k))
        if len(items) > 8:
            print("          ... and {} more".format(len(items) - 8))
        if args.dry_run:
            continue
        cmd = ["sbatch", "--export=NONE", "-p", part, "--qos", part, "-t", wall,
               "-J", "sweep_" + script.split(".")[0],
               "--array", ",".join(str(t) for t in tasks) + "%40",
               os.path.join(R, script)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=R)
        print("          -> {}".format(
            r.stdout.strip() if r.returncode == 0 else "FAILED: " + r.stderr.strip()[:160]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
