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
STATION_SUMMARY_DEFAULT = os.path.join(HERE, "..", "metadata3", "key_index_summary", "station_summary.csv")

# All partitions confirmed available to the tolugboj_lab account (sacctmgr, 2026-09-23),
# restricted to ones actually useful for this CPU/IO-bound, low-memory workload (skips
# gpu/gpu-debug/gpu-interactive/phi/highmem/visual/fastx/spec-nodes/reserved -- no benefit
# to this task, and no point competing for GPU-holding nodes another job might need).
#
# IMPORTANT (confirmed by direct testing, 2026-09-24): this cluster requires `--qos` to
# match `-p` 1:1 by name (`sbatch -p standard` alone fails with "Invalid qos
# specification"; `-p standard --qos=standard` succeeds) -- and a single sbatch call
# can't mix partitions with different implied QOS. So "use multiple partitions" here
# means SPLITTING the array across several separate sbatch submissions (one per
# partition, each with its own matching --qos), not one `-p a,b,c` call -- see
# cmd_submit's per-partition chunking.
#
# `debug` (1h cap) is EXCLUDED by default now that the download window is full station
# history (PI, 2026-09-24), not a 1-week placeholder -- a real per-station estimate from
# the key index (station_summary.csv: avg 1,385 station-days, worst case 15,050) puts
# even a "typical" station's combined download+preprocess time well past 1h, so debug
# would just fail almost everything on timeout. Still selectable via --orchestrator-
# partitions for short/test runs against a narrow date range.
ORCHESTRATOR_PARTITIONS_DEFAULT = "urseismo,standard,preempt,interactive"
SERVICE_PARTITION_DEFAULT = "urseismo"

# Safe walltime per partition, kept under each one's own TIMELIMIT (sinfo, 2026-09-23:
# urseismo 15-00:00:00, standard 5-00:00:00, preempt 2-00:00:00, interactive 12:00:00).
# Manifest rows are sorted by history length (largest first, see cmd_init) and chunked in
# that order across ORCHESTRATOR_PARTITIONS_DEFAULT, so the partition listed first should
# be the one with the most walltime headroom -- pairs the biggest, slowest stations with
# the longest-running partition on purpose, not by accident of dict ordering.
PARTITION_WALLTIME = {
    "urseismo": "9-00:00:00",
    "standard": "4-00:00:00",
    "preempt": "1-12:00:00",
    "interactive": "0-11:00:00",
    "debug": "0-00:55:00",
}

# Relative concurrency assumption per partition, used only to WORK-BALANCE chunk
# boundaries (see cmd_init) -- urseismo's 120 cores are dedicated (sinfo, 2026-09-23:
# 5 nodes, confirmed no contention at 96-way concurrency this session); standard/preempt/
# interactive are shared with other lab users (directly observed: 6+ concurrent jobs from
# another user on standard/preempt during this session) so their realistic SUSTAINED
# concurrency for us is well under their raw node counts -- these are working estimates,
# not measured, and can be corrected once a real run's actual throughput is observed.
PARTITION_WEIGHT = {
    "urseismo": 120,
    "standard": 70,
    "preempt": 35,
    "interactive": 15,
    "debug": 10,
}

# Confirmed via `scontrol show config` (2026-09-24): sbatch rejects a job array whose
# highest index is >= this, independent of how many tasks are actually in the array.
MAX_ARRAY_INDEX = 1001


def cmd_init(args):
    os.makedirs(args.root, exist_ok=True)
    for sub in ("manifest", "results", "packaged_h5", "scratch_work", "state", "master", "logs"):
        os.makedirs(os.path.join(args.root, sub), exist_ok=True)

    manifest = pd.read_csv(args.manifest or FPS_STATIONS_DEFAULT)
    if args.n_stations:
        # Truncate BEFORE size-sort/chunk-boundary computation, not after -- boundaries
        # computed against the full 2,000 rows would be nonsense for a small test slice.
        manifest = manifest.iloc[: args.n_stations].reset_index(drop=True)

    # Sort largest-history-first (station_summary.csv's total_days, from the Stage 1 key
    # index scan) so cmd_submit's partition chunking naturally pairs the biggest, slowest
    # stations with the longest-walltime partitions instead of a random mix landing on
    # whichever partition happens to run out of walltime first. Stations missing from the
    # summary (shouldn't happen for the locked 2,000-network, but not assumed) sort last,
    # not first -- unknown size is treated as small, not as "biggest, needs urseismo."
    summary_path = args.station_summary or STATION_SUMMARY_DEFAULT
    chunk_bounds = None  # list of (partition, lo, hi) index ranges, computed below if possible
    if os.path.exists(summary_path) and not args.no_size_sort:
        summary = pd.read_csv(summary_path)[["network", "station", "total_days"]]
        manifest = manifest.merge(summary, on=["network", "station"], how="left")
        manifest["total_days"] = manifest["total_days"].fillna(0)
        manifest = manifest.sort_values("total_days", ascending=False).reset_index(drop=True)
        print(f"[master] sorted manifest by history length (largest first) using {summary_path}")

        # Work-balance chunk boundaries across ORCHESTRATOR_PARTITIONS_DEFAULT instead of
        # an equal station-COUNT split. Found by direct modeling (2026-09-24): an
        # equal-count split puts ~78% of total estimated work in the first (largest-
        # history) quarter alone -- the other 3 partitions would finish in a few days and
        # then sit idle while that one chunk still has over a week left. Cost proxy here
        # mirrors the same rough model used for the campaign-level time estimate
        # (download ~1MB/s/connection + ~8s/channel-day preprocessing, 3 channels avg) --
        # not exact, but directionally correct, which is all a boundary cut needs.
        DL_RATE_MBps, CHANNELS_AVG, PREP_S_PER_CHDAY = 1.0, 3.0, 8.0
        summary_bytes = pd.read_csv(summary_path)[["network", "station", "total_bytes"]]
        m2 = manifest.merge(summary_bytes, on=["network", "station"], how="left")
        m2["total_bytes"] = m2["total_bytes"].fillna(0)
        work_days = (m2["total_bytes"] / 1e6 / DL_RATE_MBps
                     + manifest["total_days"] * CHANNELS_AVG * PREP_S_PER_CHDAY) / 86400

        partitions = (args.orchestrator_partitions or ORCHESTRATOR_PARTITIONS_DEFAULT).split(",")
        weights = [PARTITION_WEIGHT.get(p, 10) for p in partitions]
        total_weight = sum(weights)
        total_work = work_days.sum()
        targets = [total_work * w / total_weight for w in weights]

        chunk_bounds = []
        lo = 0
        cum = 0.0
        n_rows_now = len(manifest)
        for i, (partition, target) in enumerate(zip(partitions, targets)):
            if lo >= n_rows_now:
                break
            if i == len(partitions) - 1:
                hi = n_rows_now - 1  # last partition takes whatever remains -- no rounding gap
            else:
                hi = lo
                acc = 0.0
                while hi < n_rows_now and acc < target:
                    acc += work_days.iloc[hi]
                    hi += 1
                hi -= 1
                hi = max(hi, lo)  # always give every listed partition at least 1 station if any remain
            chunk_work = work_days.iloc[lo:hi + 1].sum()
            chunk_bounds.append((partition, lo, hi, round(chunk_work, 1)))
            lo = hi + 1
        print("[master] work-balanced chunk boundaries: " +
              ", ".join(f"{p}=idx[{lo}-{hi}]({w}CPU-days)" for p, lo, hi, w in chunk_bounds))

        manifest = manifest.drop(columns=["total_days"])
    else:
        print(f"[master] no size-sort applied (summary not found or --no-size-sort)")

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
            # [partition, lo, hi, estimated_cpu_days] per chunk, work-balanced (see
            # above) -- None if size-sort was skipped, in which case cmd_submit falls
            # back to a plain equal-count split.
            chunk_bounds=chunk_bounds,
        ), f)


def cmd_submit(args):
    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))

    defaults_path = os.path.join(args.root, "state", "partition_defaults.json")
    defaults = json.load(open(defaults_path)) if os.path.exists(defaults_path) else {}
    service_partition = args.service_partition or defaults.get("service_partition") or SERVICE_PARTITION_DEFAULT

    # Prefer the work-balanced boundaries computed at `init` time (see cmd_init) -- only
    # valid if the partition list wasn't overridden here, since the boundaries were
    # computed against a specific partition/weight set. An override falls back to a
    # plain equal-count split rather than silently using stale boundaries.
    chunk_bounds = defaults.get("chunk_bounds") if not args.orchestrator_partitions else None
    if chunk_bounds:
        print("[master] using work-balanced chunk boundaries from init")
    else:
        orch_partitions = (args.orchestrator_partitions or defaults.get("orchestrator_partitions")
                           or ORCHESTRATOR_PARTITIONS_DEFAULT).split(",")
        n_parts = len(orch_partitions)
        base, extra = divmod(n_rows, n_parts)
        lo = 0
        chunk_bounds = []
        for i, partition in enumerate(orch_partitions):
            chunk_size = base + (1 if i < extra else 0)
            if chunk_size == 0:
                continue
            hi = lo + chunk_size - 1
            chunk_bounds.append((partition, lo, hi, None))
            lo = hi + 1
        print("[master] no work-balanced boundaries available -- using a plain equal-count split")

    # Each chunk is its own sbatch call with -p/--qos matched by name (see
    # ORCHESTRATOR_PARTITIONS_DEFAULT's comment for why: this cluster rejects a single
    # `-p a,b,c` list with "Invalid qos specification").
    orch_ids = []
    for partition, lo, hi, est_work in chunk_bounds:
        chunk_size = hi - lo + 1
        if chunk_size <= 0:
            continue
        # SLURM's MaxArraySize (1001, confirmed 2026-09-24) caps the max array INDEX, not
        # the task count -- a chunk starting at or spanning past that can't be submitted
        # with its raw global indices ("Invalid job array specification"). Rebase to
        # 0-(chunk_size-1) and pass the true starting row via WAVENET_IDX_OFFSET
        # (orchestrator.py adds it back); chunks already under the cap get offset=0 and
        # are otherwise unaffected.
        offset = lo if hi >= MAX_ARRAY_INDEX else 0
        array_lo, array_hi = (0, hi - lo) if offset else (lo, hi)
        array_spec = f"{array_lo}-{array_hi}"
        if args.array_limit:
            array_spec += f"%{min(args.array_limit, chunk_size)}"
        walltime = PARTITION_WALLTIME.get(partition, "0-00:30:00")
        job_id = _sbatch(["--array", array_spec, "-p", partition, "--qos", partition,
                           "-t", walltime, os.path.join(args.root, "orchestrator.slurm")],
                          extra_env={"WAVENET_IDX_OFFSET": str(offset)} if offset else None)
        orch_ids.append(job_id)
        work_note = f", ~{est_work} CPU-days est." if est_work is not None else ""
        offset_note = f", rebased with offset={offset}" if offset else ""
        print(f"[master] orchestrator chunk on '{partition}' (walltime={walltime}): idx {lo}-{hi} "
              f"({chunk_size} tasks, limit={args.array_limit}{work_note}{offset_note}) -> job {job_id}")
    print(f"[master] orchestrator fully submitted across {len(orch_ids)} partitions: {orch_ids}")

    if not args.no_logger:
        logger_id = _sbatch(["-p", service_partition, "--qos", service_partition,
                              os.path.join(args.root, "logger.slurm")])
        print(f"[master] logger submitted on '{service_partition}': {logger_id}")
    if not args.no_inspector:
        inspector_id = _sbatch(["-p", service_partition, "--qos", service_partition,
                                 os.path.join(args.root, "inspector.slurm")])
        print(f"[master] inspector submitted on '{service_partition}': {inspector_id}")


def _sbatch(argv, extra_env=None):
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
    # extra_env rides along on the same flag (`--export=NONE,VAR=value`, valid SLURM
    # syntax) rather than a separate flag, so it doesn't reopen the environment-inheritance
    # hole this was fixed for.
    export_arg = "--export=NONE"
    if extra_env:
        export_arg += "," + ",".join(f"{k}={v}" for k, v in extra_env.items())
    out = subprocess.run(["sbatch", export_arg] + argv, capture_output=True, text=True, check=True)
    # sbatch prints "Submitted batch job <id>"
    return out.stdout.strip().split()[-1]


def cmd_status(args):
    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))
    results_dir = os.path.join(args.root, "results")
    n_reported = len([f for f in os.listdir(results_dir) if f.endswith(".json")]) \
        if os.path.isdir(results_dir) else 0

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
    # Only *.json -- orchestrator.py now writes via a temp file + atomic rename
    # (result_path + ".tmp", then os.replace), so a genuinely complete *.json file is
    # never visible mid-write; excluding the .tmp extension here closes the read race
    # a naive full-directory listdir would otherwise reopen on the temp name instead.
    result_files = sorted(f for f in os.listdir(results_dir) if f.endswith(".json")) \
        if os.path.isdir(results_dir) else []
    result_files = [os.path.join(results_dir, f) for f in result_files]

    n_reported = 0
    download_bytes = 0
    packaged_bytes = 0
    n_with_data = 0
    n_unreadable = 0
    for rf in result_files:
        try:
            with open(rf) as f:
                r = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Belt-and-suspenders: even with the atomic-write fix above, don't let one
            # bad file crash the whole report -- count it and keep going.
            n_unreadable += 1
            continue
        n_reported += 1
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
    if n_unreadable:
        print(f"  (skipped {n_unreadable} unreadable result file(s) -- transient, ignore "
              f"unless this count keeps growing on repeat checks)")
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
#SBATCH -o {root}/logs/orchestrator_%A_%a.out
#SBATCH -e {root}/logs/orchestrator_%A_%a.err

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
    p.add_argument("--station-summary", default=None,
                    help="defaults to metadata3/key_index_summary/station_summary.csv (for size-sort)")
    p.add_argument("--no-size-sort", action="store_true",
                    help="skip sorting by history length (default sorts largest-first)")
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
