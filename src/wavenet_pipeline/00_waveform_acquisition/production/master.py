#!/usr/bin/env python
"""
Master: the single entry point that runs the other three actors (orchestrator array,
logger, inspector) against a given root directory + station manifest slice. Run this on
Bluehive itself (needs `sbatch`/`squeue` on $PATH), not from axon-1 directly.

Roles, for reference (full detail in each actor's own module docstring):
  - orchestrator.py  (SLURM array, one task per station) download -> preprocess -> package
  - logger.py        (one long-lived job) serial-merges finished shards into the master h5
  - inspector.py      (one long-lived job) verifies each merge, then purges raw SEED

Typical flow for a NEW root (small sanity check before any full-scale run -- required,
see docs/ncf_pipeline_stages/PROGRESS.md's "verify before scaling" discipline):
    python master.py init   --root ROOT --manifest fps_stations.csv --n-stations 20
    python master.py submit --root ROOT --array-limit 20
    python master.py status --root ROOT           # poll until done
    python master.py stop   --root ROOT            # writes the STOP file logger/inspector watch for

Scaling to the full 2,000 once the small run is clean:
    python master.py init   --root ROOT --manifest fps_stations.csv   # no --n-stations = all rows
    python master.py submit --root ROOT --array-limit 96              # 96-way: confirmed safe, no
                                                                       # provider throttling observed
                                                                       # at that concurrency (2026-09-23)
"""
import argparse
import json
import os
import subprocess
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FPS_STATIONS_DEFAULT = os.path.join(HERE, "..", "metadata3", "fps_stations.csv")

# All partitions confirmed available to the tolugboj_lab account (sacctmgr, 2026-09-23),
# restricted to ones actually useful for this CPU/IO-bound, low-memory workload (skips
# gpu/gpu-debug/gpu-interactive/phi/highmem/visual/fastx/spec-nodes/reserved -- no benefit
# to this task, and no point competing for GPU-holding nodes another job might need).
# `debug` has only a 1h walltime cap, which is fine for orchestrator's ~30min tasks but
# too short for logger/inspector's long-lived polling loops -- so it's orchestrator-only.
#
# IMPORTANT (confirmed by direct testing, 2026-09-24): this cluster requires `--qos` to
# match `-p` 1:1 by name (`sbatch -p standard` alone fails with "Invalid qos
# specification"; `-p standard --qos=standard` succeeds) -- and a single sbatch call
# can't mix partitions with different implied QOS. So "use multiple partitions" here
# means SPLITTING the array across several separate sbatch submissions (one per
# partition, each with its own matching --qos), not one `-p a,b,c` call -- see
# cmd_submit's per-partition chunking.
ORCHESTRATOR_PARTITIONS_DEFAULT = "urseismo,standard,preempt,debug,interactive"
SERVICE_PARTITION_DEFAULT = "urseismo"


def cmd_init(args):
    os.makedirs(args.root, exist_ok=True)
    for sub in ("manifest", "results", "packaged_h5", "scratch_work", "state", "master", "logs"):
        os.makedirs(os.path.join(args.root, sub), exist_ok=True)

    manifest = pd.read_csv(args.manifest or FPS_STATIONS_DEFAULT)
    if args.n_stations:
        manifest = manifest.iloc[: args.n_stations]
    manifest_out = os.path.join(args.root, "manifest", "fps_stations.csv")
    manifest.to_csv(manifest_out, index=False)
    print(f"[master] wrote {len(manifest)}-row manifest to {manifest_out}")

    stop_path = os.path.join(args.root, "state", "STOP")
    if os.path.exists(stop_path):
        os.remove(stop_path)  # a re-init of an existing root shouldn't inherit a stale STOP

    for name, template in (("orchestrator.slurm", ORCHESTRATOR_SLURM),
                            ("logger.slurm", LOGGER_SLURM),
                            ("inspector.slurm", INSPECTOR_SLURM)):
        path = os.path.join(args.root, name)
        with open(path, "w") as f:
            f.write(template.format(root=args.root, here=HERE))
        print(f"[master] wrote {path}")

    # Partition/QOS choice is a SUBMIT-time decision (see cmd_submit), not baked into the
    # script -- stash the requested defaults so `submit` doesn't need them re-passed.
    with open(os.path.join(args.root, "state", "partition_defaults.json"), "w") as f:
        json.dump(dict(
            orchestrator_partitions=args.orchestrator_partitions or ORCHESTRATOR_PARTITIONS_DEFAULT,
            service_partition=args.service_partition or SERVICE_PARTITION_DEFAULT,
        ), f)


def cmd_submit(args):
    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))

    defaults_path = os.path.join(args.root, "state", "partition_defaults.json")
    defaults = json.load(open(defaults_path)) if os.path.exists(defaults_path) else {}
    orch_partitions = (args.orchestrator_partitions or defaults.get("orchestrator_partitions")
                       or ORCHESTRATOR_PARTITIONS_DEFAULT).split(",")
    service_partition = args.service_partition or defaults.get("service_partition") or SERVICE_PARTITION_DEFAULT

    # Split the station range into one contiguous chunk per partition (remainder rows go
    # to the first chunks) -- each chunk is its own sbatch call with -p/--qos matched by
    # name (see ORCHESTRATOR_PARTITIONS_DEFAULT's comment for why: this cluster rejects a
    # single `-p a,b,c` list with "Invalid qos specification").
    n_parts = len(orch_partitions)
    base, extra = divmod(n_rows, n_parts)
    lo = 0
    orch_ids = []
    for i, partition in enumerate(orch_partitions):
        chunk_size = base + (1 if i < extra else 0)
        if chunk_size == 0:
            continue
        hi = lo + chunk_size - 1
        array_spec = f"{lo}-{hi}"
        if args.array_limit:
            array_spec += f"%{min(args.array_limit, chunk_size)}"
        job_id = _sbatch(["--array", array_spec, "-p", partition, "--qos", partition,
                           os.path.join(args.root, "orchestrator.slurm")])
        orch_ids.append(job_id)
        print(f"[master] orchestrator chunk on '{partition}': idx {lo}-{hi} "
              f"({chunk_size} tasks, limit={args.array_limit}) -> job {job_id}")
        lo = hi + 1
    print(f"[master] orchestrator fully submitted across {len(orch_ids)} partitions: {orch_ids}")

    if not args.no_logger:
        logger_id = _sbatch(["-p", service_partition, "--qos", service_partition,
                              os.path.join(args.root, "logger.slurm")])
        print(f"[master] logger submitted on '{service_partition}': {logger_id}")
    if not args.no_inspector:
        inspector_id = _sbatch(["-p", service_partition, "--qos", service_partition,
                                 os.path.join(args.root, "inspector.slurm")])
        print(f"[master] inspector submitted on '{service_partition}': {inspector_id}")


def _sbatch(argv):
    # --export=NONE: found by direct testing (2026-09-23) to be load-bearing, not
    # cosmetic. master.py itself runs inside an activated `instaseis` conda env (it needs
    # pandas); sbatch's default is to propagate the SUBMITTING shell's environment into
    # the batch job, so every job submitted via `master.py submit` was inheriting an
    # already-active conda env, then re-running `conda activate instaseis` INSIDE the
    # batch script on top of it. That double-activation reproducibly broke
    # `pkg_resources`'s vendored `packaging` submodule resolution (ImportError: cannot
    # import name '_manylinux' at the `from obspy import ...` line) -- 5 separate
    # `master.py submit` trials failed this way regardless of jitter, retries, a
    # concurrency throttle, or precompiled bytecode, while every job submitted from a
    # plain (non-activated) shell -- including this exact same orchestrator.slurm run
    # standalone -- succeeded every time. --export=NONE gives the batch job a clean
    # environment so its own `source conda.sh && conda activate` is the only activation.
    out = subprocess.run(["sbatch", "--export=NONE"] + argv, capture_output=True, text=True, check=True)
    # sbatch prints "Submitted batch job <id>"
    return out.stdout.strip().split()[-1]


def cmd_status(args):
    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))
    results_dir = os.path.join(args.root, "results")
    n_reported = len(os.listdir(results_dir)) if os.path.isdir(results_dir) else 0

    master_log = os.path.join(args.root, "master_log.csv")
    n_merged = len(pd.read_csv(master_log)) if os.path.exists(master_log) else 0

    inspector_log = os.path.join(args.root, "inspector_log.csv")
    n_purged = len(pd.read_csv(inspector_log)) if os.path.exists(inspector_log) else 0

    flagged_path = os.path.join(args.root, "state", "inspector_flagged.json")
    n_flagged = 0
    if os.path.exists(flagged_path):
        import json
        n_flagged = len(json.load(open(flagged_path)))

    print(f"manifest total     : {n_rows}")
    print(f"orchestrator done  : {n_reported} / {n_rows}")
    print(f"merged to master   : {n_merged}")
    print(f"verified + purged  : {n_purged}")
    print(f"flagged (review)   : {n_flagged}")
    print()
    subprocess.run(["squeue", "-A", "tolugboj_lab", "-o", "%.12i %.20j %.8T %.10M"])


def cmd_progress(args):
    """Progress bar + throughput/ETA for a full deployment. Rate is computed from a
    snapshot log (state/progress_snapshots.jsonl) this command appends to on every call
    -- so the first call of a session has no rate/ETA yet (nothing to compare against),
    and accuracy improves the more often it's checked. Not a running daemon: this is a
    point-in-time report, call it again later to refresh."""
    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))
    results_dir = os.path.join(args.root, "results")
    result_files = [os.path.join(results_dir, f) for f in os.listdir(results_dir)] if os.path.isdir(results_dir) else []

    n_reported = len(result_files)
    download_bytes = 0
    packaged_bytes = 0
    n_with_data = 0
    for rf in result_files:
        with open(rf) as f:
            r = json.load(f)
        download_bytes += r.get("download_bytes") or 0
        packaged_bytes += r.get("h5_size_bytes") or 0
        if r.get("package_ok"):
            n_with_data += 1

    master_log = os.path.join(args.root, "master_log.csv")
    n_merged = len(pd.read_csv(master_log)) if os.path.exists(master_log) else 0
    inspector_log = os.path.join(args.root, "inspector_log.csv")
    n_purged = len(pd.read_csv(inspector_log)) if os.path.exists(inspector_log) else 0

    state_dir = os.path.join(args.root, "state")
    os.makedirs(state_dir, exist_ok=True)
    snapshot_path = os.path.join(state_dir, "progress_snapshots.jsonl")
    now = time.time()
    prev = None
    if os.path.exists(snapshot_path):
        with open(snapshot_path) as f:
            lines = [l for l in f if l.strip()]
        if lines:
            prev = json.loads(lines[-1])
    with open(snapshot_path, "a") as f:
        f.write(json.dumps(dict(t=now, n_reported=n_reported, download_bytes=download_bytes,
                                 packaged_bytes=packaged_bytes)) + "\n")

    pct = 100.0 * n_reported / n_rows if n_rows else 0.0
    bar_width = 40
    filled = int(bar_width * n_reported / n_rows) if n_rows else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    print(f"[{bar}] {n_reported}/{n_rows} ({pct:.1f}%) orchestrator tasks reported")
    print(f"  with real data     : {n_with_data}")
    print(f"  merged to master   : {n_merged}")
    print(f"  verified + purged  : {n_purged}")
    print(f"  downloaded so far  : {download_bytes / 1e9:.2f} GB")
    print(f"  packaged so far    : {packaged_bytes / 1e9:.2f} GB")

    if prev and now > prev["t"]:
        dt = now - prev["t"]
        d_reported = n_reported - prev["n_reported"]
        d_bytes = download_bytes - prev["download_bytes"]
        if d_reported > 0 and dt > 0:
            rate_stations_per_hr = d_reported / dt * 3600
            rate_gb_per_hr = (d_bytes / 1e9) / dt * 3600
            remaining = n_rows - n_reported
            eta_hr = remaining / rate_stations_per_hr if rate_stations_per_hr > 0 else float("inf")
            print(f"  rate (since last check, {dt/60:.1f} min ago): "
                  f"{rate_stations_per_hr:.1f} stations/hr, {rate_gb_per_hr:.2f} GB/hr")
            print(f"  ETA to finish remaining {remaining}: ~{eta_hr:.1f} hr")
        else:
            print("  rate: no new tasks reported since last check -- can't estimate yet")
    else:
        print("  rate: no prior snapshot yet -- run `progress` again later for a rate/ETA")
    print()
    subprocess.run(["squeue", "-A", "tolugboj_lab", "-o", "%.12i %.20j %.10P %.8T %.10M"])


def cmd_stop(args):
    stop_path = os.path.join(args.root, "state", "STOP")
    os.makedirs(os.path.dirname(stop_path), exist_ok=True)
    open(stop_path, "w").close()
    print(f"[master] wrote {stop_path} -- logger/inspector will exit at their next poll")


ORCHESTRATOR_SLURM = """#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -t 00:30:00
#SBATCH --mem-per-cpu=2G
#SBATCH -n 1
#SBATCH -o {root}/logs/orchestrator_%a.out
#SBATCH -e {root}/logs/orchestrator_%a.err

# Every path here is under /scratch/tolugboj_lab -- never $HOME (real storage-quota
# constraint: tolugboj's $HOME already carries ~8GB of pre-existing personal package
# installs, docs/ncf_pipeline_stages/PROGRESS.md). Redirect incidental cache writes
# (matplotlib font cache, any XDG-respecting library) off $HOME too, just in case.
export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache

# Stagger conda activation / first-import across the array (up to 38s, 2s steps over a
# 20-wide cycle) -- found by direct testing (2026-09-23) that a whole array hitting the
# shared conda env's `pkg_resources`/obspy import chain in the same instant can trip a
# transient `ImportError: cannot import name '_manylinux'` (100% reproducible for a
# 15-task array launched together, 0/15 failures for the same import sequence run with
# any stagger at all) -- not a code bug, a shared-filesystem race on first concurrent
# import. Isolated single-task and small non-array-launched-together runs never hit it.
# Jitter alone did NOT eliminate it (still saw whole-array failures with it in place,
# 2026-09-23) -- the real mitigation is the retry loop below: orchestrator.py always
# writes its own result JSON and exits 0 once past this import, wrapping try/except
# around each pipeline stage internally, so a nonzero exit here means the crash happened
# before any of that -- safe to blindly retry a few times with a short backoff.
sleep $(( (SLURM_ARRAY_TASK_ID % 20) * 2 ))

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
export WAVENET_PROD_ROOT={root}
for attempt in 1 2 3 4; do
    python3 {here}/orchestrator.py $SLURM_ARRAY_TASK_ID && break
    echo "[orchestrator.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
"""

LOGGER_SLURM = """#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -t 08:00:00
#SBATCH --mem-per-cpu=4G
#SBATCH -n 1
#SBATCH -o {root}/logs/logger.out
#SBATCH -e {root}/logs/logger.err

export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
# Same transient shared-env import race as orchestrator.slurm can crash this job before
# its loop even starts -- retry a few times rather than leaving the whole run un-merged.
for attempt in 1 2 3 4; do
    python3 {here}/logger.py --root {root} --poll-interval 30 && break
    echo "[logger.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
"""

INSPECTOR_SLURM = """#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -t 08:00:00
#SBATCH --mem-per-cpu=2G
#SBATCH -n 1
#SBATCH -o {root}/logs/inspector.out
#SBATCH -e {root}/logs/inspector.err

export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
for attempt in 1 2 3 4; do
    python3 {here}/inspector.py --root {root} --poll-interval 30 && break
    echo "[inspector.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="lay out ROOT, write a manifest slice, render SLURM scripts")
    p.add_argument("--root", required=True)
    p.add_argument("--manifest", default=None, help="defaults to metadata3/fps_stations.csv")
    p.add_argument("--n-stations", type=int, default=None, help="first N rows only (sanity-check runs)")
    p.add_argument("--orchestrator-partitions", default=None,
                    help=f"comma list, array is SPLIT across these (default: {ORCHESTRATOR_PARTITIONS_DEFAULT})")
    p.add_argument("--service-partition", default=None,
                    help=f"single partition for logger/inspector (default: {SERVICE_PARTITION_DEFAULT})")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("submit", help="sbatch the orchestrator array (split across partitions) + logger + inspector")
    p.add_argument("--root", required=True)
    p.add_argument("--array-limit", type=int, default=96,
                    help="max concurrent orchestrator tasks PER PARTITION CHUNK (96-way confirmed safe 2026-09-23)")
    p.add_argument("--orchestrator-partitions", default=None,
                    help="override the partitions set at init (comma list)")
    p.add_argument("--service-partition", default=None, help="override the partition set at init")
    p.add_argument("--no-logger", action="store_true")
    p.add_argument("--no-inspector", action="store_true")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="print progress counts + squeue")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("progress", help="progress bar + GB ingested/packaged + rate/ETA")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_progress)

    p = sub.add_parser("stop", help="signal logger/inspector to exit at their next poll")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_stop)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
