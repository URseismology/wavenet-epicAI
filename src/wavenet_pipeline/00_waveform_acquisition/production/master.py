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
import glob
import json
import os
import subprocess
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FPS_STATIONS_DEFAULT = os.path.join(HERE, "..", "metadata3", "fps_stations.csv")
STATION_SUMMARY_DEFAULT = os.path.join(HERE, "..", "metadata3", "key_index_summary", "station_summary.csv")


def _scratch_quota():
    """Scratch usage against the real quota, via CIRC's own tool. This is a hard ceiling,
    not just filesystem capacity: /scratch is 10 TB soft / 11 TB hard per circ-quota, while
    `df` shows hundreds of TB free on the shared filesystem -- so df is reassuring and
    wrong. It matters because raw SEED runs several times the size of the packaged output,
    so a full campaign's raw would exceed the quota outright if the inspector is not
    purging. Reported here so the ceiling is visible long before it is hit."""
    try:
        out = subprocess.run(["circ-quota"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "/scratch":
            try:
                used, soft, hard = float(parts[1]), float(parts[2]), float(parts[3])
                return dict(used_gb=used, soft_gb=soft, hard_gb=hard,
                             pct_of_hard=100.0 * used / hard if hard else 0.0)
            except ValueError:
                return None
    return None


def _count_log_rows(path):
    """Row count of an APPEND-ONLY log, by lines rather than pd.read_csv. These logs span
    pipeline versions (inspector_log gained patch_level on 2026-09-25), so one file can
    hold rows of two widths and pandas refuses to parse it. A monitoring command must
    never be the thing that breaks, and it only needs a count."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path) as f:
            return max(sum(1 for _ in f) - 1, 0)
    except OSError:
        return 0


def format_eta(hr):
    """Render an ETA in whichever unit keeps the number in a readable 1-24/1-7/1-4/1-12
    range (hours/days/weeks/months), falling back to years beyond a year -- PI request:
    a raw hours figure for a multi-week estimate (e.g. "412.3 hr") is technically
    correct but not something anyone can size up at a glance."""
    if hr < 24:
        return f"{hr:.1f} hr"
    days = hr / 24
    if days < 7:
        return f"{days:.1f} days"
    weeks = days / 7
    if weeks < 4:
        return f"{weeks:.1f} weeks"
    months = days / 30.44  # average month length -- fine for an estimate, not a calendar
    if months < 12:
        return f"{months:.1f} months"
    return f"{months / 12:.1f} years"

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

# Two-tier strategy (PI, 2026-09-24), replacing a single size-sorted split. Rationale:
# this network is farthest-point-sampled, so a station's GEOGRAPHIC footprint value is
# already fully captured the moment it has any reasonable data -- decades more history
# at an already-covered location deepens time coverage, not spatial diversity. A pure
# size-sorted split (biggest stations first, on the longest-walltime partition) was
# backwards for "maximize global footprint fast": it tied up urseismo on exactly the
# ~40 stations that matter least for footprint speed. Confirmed by direct investigation
# (2026-09-24, debug partition) that these same ~40 stations are also genuinely
# expensive -- modern instrumentation records at far higher native rates than a
# decades-old station's early years, so recent-year data dominates their real cost.
#
# So: split into a FAST tier (~1,950+ stations) that gets ALL footprint-building
# capacity (standard/preempt/interactive, sorted smallest-first for fastest coverage
# growth), and a SLOW tier (the real multi-decade outliers) that runs exclusively and
# independently on urseismo -- not sequenced after the fast tier (they take so long
# regardless that delaying their start has no benefit), just kept off the fast tier's
# capacity and off the shared/contended partitions where a multi-day job risks more
# preemption cycles.
OUTLIER_TOTAL_DAYS_THRESHOLD = 8000  # matches the real cutoff found in station_summary.csv (2026-09-23): only 43 of 1984 stations with a known range exceed this
OUTLIER_PARTITION = "urseismo"
FAST_TIER_PARTITIONS_DEFAULT = "standard,preempt,interactive"

# Confirmed via `scontrol show config` (2026-09-24): sbatch rejects a job array whose
# highest index is >= this, independent of how many tasks are actually in the array.
MAX_ARRAY_INDEX = 1001

# Banked pre-patch timing offsets (timing_replay_production.py). The inspector will not
# purge a pre-patch station's raw SEED until that station's offsets are present here --
# purging destroys the only source of the exact correction.
TIMING_OFFSETS_DEFAULT = "/scratch/tolugboj_lab/wavenet_ncf_migration/timing_offsets"


def cmd_init(args):
    os.makedirs(args.root, exist_ok=True)
    for sub in ("manifest", "results", "packaged_h5", "scratch_work", "state", "master", "logs"):
        os.makedirs(os.path.join(args.root, sub), exist_ok=True)

    manifest = pd.read_csv(args.manifest or FPS_STATIONS_DEFAULT)
    if args.n_stations:
        # Truncate BEFORE size-sort/chunk-boundary computation, not after -- boundaries
        # computed against the full 2,000 rows would be nonsense for a small test slice.
        manifest = manifest.iloc[: args.n_stations].reset_index(drop=True)

    # Two-tier split (see the constants block above for the full rationale): fast tier
    # (footprint-building majority, sorted SMALLEST-first) gets standard/preempt/
    # interactive; the real multi-decade outliers get their own chunk, exclusively on
    # urseismo. Stations missing from the summary sort into the fast tier (unknown size
    # treated as small, not as "outlier, needs urseismo").
    summary_path = args.station_summary or STATION_SUMMARY_DEFAULT
    chunk_bounds = None  # list of (partition, lo, hi, est_work) index ranges, computed below if possible
    if os.path.exists(summary_path) and not args.no_size_sort:
        summary = pd.read_csv(summary_path)[["network", "station", "total_days"]]
        manifest = manifest.merge(summary, on=["network", "station"], how="left")
        manifest["total_days"] = manifest["total_days"].fillna(0)

        is_outlier = manifest["total_days"] > OUTLIER_TOTAL_DAYS_THRESHOLD
        fast = manifest[~is_outlier].sort_values("total_days", ascending=True)
        outliers = manifest[is_outlier].sort_values("total_days", ascending=True)
        manifest = pd.concat([fast, outliers], ignore_index=True)
        n_fast = len(fast)
        print(f"[master] two-tier split: {n_fast} fast-tier stations (smallest-first, "
              f"footprint priority) + {len(outliers)} outlier stations "
              f"(>{OUTLIER_TOTAL_DAYS_THRESHOLD} real days, urseismo-only) "
              f"using {summary_path}")

        # Cost proxy mirrors the campaign-level time estimate (download ~1MB/s/
        # connection + ~8s/channel-day preprocessing, 3 channels avg) -- not exact, but
        # directionally correct, which is all a boundary cut needs.
        DL_RATE_MBps, CHANNELS_AVG, PREP_S_PER_CHDAY = 1.0, 3.0, 8.0
        summary_bytes = pd.read_csv(summary_path)[["network", "station", "total_bytes"]]
        m2 = manifest.merge(summary_bytes, on=["network", "station"], how="left")
        m2["total_bytes"] = m2["total_bytes"].fillna(0)
        work_days = (m2["total_bytes"] / 1e6 / DL_RATE_MBps
                     + manifest["total_days"] * CHANNELS_AVG * PREP_S_PER_CHDAY) / 86400

        # Fast tier: work-balance idx[0 .. n_fast-1] across the fast-tier partitions.
        fast_partitions = (args.orchestrator_partitions or FAST_TIER_PARTITIONS_DEFAULT).split(",")
        weights = [PARTITION_WEIGHT.get(p, 10) for p in fast_partitions]
        total_weight = sum(weights)
        fast_work = work_days.iloc[:n_fast]
        total_work = fast_work.sum()
        targets = [total_work * w / total_weight for w in weights]

        chunk_bounds = []
        lo = 0
        for i, (partition, target) in enumerate(zip(fast_partitions, targets)):
            if lo >= n_fast:
                break
            if i == len(fast_partitions) - 1:
                hi = n_fast - 1  # last fast-tier partition takes whatever remains -- no rounding gap
            else:
                hi = lo
                acc = 0.0
                while hi < n_fast and acc < target:
                    acc += work_days.iloc[hi]
                    hi += 1
                hi -= 1
                hi = max(hi, lo)
            chunk_work = work_days.iloc[lo:hi + 1].sum()
            chunk_bounds.append((partition, lo, hi, round(chunk_work, 1)))
            lo = hi + 1

        # Outlier tier: one chunk, all on urseismo, regardless of --orchestrator-partitions.
        if len(outliers):
            outlier_work = round(work_days.iloc[n_fast:].sum(), 1)
            chunk_bounds.append((OUTLIER_PARTITION, n_fast, len(manifest) - 1, outlier_work))

        print("[master] chunk boundaries: " +
              ", ".join(f"{p}=idx[{lo}-{hi}]({w}CPU-days)" for p, lo, hi, w in chunk_bounds))

        manifest = manifest.drop(columns=["total_days"])
    else:
        print(f"[master] no size-sort applied (summary not found or --no-size-sort)")

    manifest_out = os.path.join(args.root, "manifest", "fps_stations.csv")
    manifest.to_csv(manifest_out, index=False)
    print(f"[master] wrote {len(manifest)}-row manifest to {manifest_out}")

    # Carry forward already-completed stations from a prior run (e.g. the canary) so
    # they're not redownloaded/reprocessed -- PI (2026-09-24). Writes a results/*.json
    # for each manifest row whose (network, station) has a package_ok=True result in
    # the source root, under this run's NEW idx (idx changes under the two-tier
    # resort), so orchestrator.py's existing idempotency check just naturally skips it.
    # packaged_h5/, master/, master_log.csv etc. are untouched here -- they're keyed by
    # network.station, not idx, so nothing needs to move for those to already be correct.
    if args.carry_forward_from:
        src_results_dir = os.path.join(args.carry_forward_from, "results")
        completed = {}
        for fn in os.listdir(src_results_dir) if os.path.isdir(src_results_dir) else []:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(src_results_dir, fn)) as f:
                    r = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if r.get("package_ok"):
                completed[(r.get("network"), r.get("station"))] = r

        results_dir = os.path.join(args.root, "results")
        n_carried = 0
        for new_idx, row in manifest.iterrows():
            key = (row["network"], row["station"])
            if key in completed:
                r = dict(completed[key])
                r["idx"] = int(new_idx)
                r["carried_forward_from"] = args.carry_forward_from
                out_path = os.path.join(results_dir, f"{new_idx:04d}_{row['network']}_{row['station']}.json")
                tmp = out_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(r, f)
                os.replace(tmp, out_path)
                n_carried += 1
        print(f"[master] carried forward {n_carried} already-completed station(s) from "
              f"{args.carry_forward_from} -- these will be skipped, not redownloaded")

    stop_path = os.path.join(args.root, "state", "STOP")
    if os.path.exists(stop_path):
        os.remove(stop_path)  # a re-init of an existing root shouldn't inherit a stale STOP

    for name, template in (("orchestrator.slurm", ORCHESTRATOR_SLURM),
                            ("logger.slurm", LOGGER_SLURM),
                            ("inspector.slurm", INSPECTOR_SLURM)):
        path = os.path.join(args.root, name)
        with open(path, "w") as f:
            f.write(template.format(root=args.root, here=HERE,
                                     offsets_dir=TIMING_OFFSETS_DEFAULT))
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
        walltime = PARTITION_WALLTIME.get(partition, "0-00:30:00")
        work_note = f", ~{est_work} CPU-days est." if est_work is not None else ""
        # SLURM's MaxArraySize (1001, confirmed 2026-09-24) caps the max array INDEX,
        # not the task count -- a chunk that's simply too BIG (the two-tier fast-tier
        # chunks can be 1000+ stations, found by direct testing 2026-09-24) or that
        # STARTS at a high index (the older single-tier chunks' problem) both produce
        # "Invalid job array specification" the same way. Fix covers both: split into
        # sub-chunks of at most MAX_ARRAY_INDEX rows, each rebased to 0-(size-1) with
        # its true starting row passed via WAVENET_IDX_OFFSET (orchestrator.py adds it
        # back).
        sub_lo = lo
        while sub_lo <= hi:
            sub_hi = min(sub_lo + MAX_ARRAY_INDEX - 1, hi)
            sub_size = sub_hi - sub_lo + 1
            array_spec = f"0-{sub_hi - sub_lo}"
            if args.array_limit:
                array_spec += f"%{min(args.array_limit, sub_size)}"
            job_id = _sbatch(["--array", array_spec, "-p", partition, "--qos", partition,
                               "-t", walltime, os.path.join(args.root, "orchestrator.slurm")],
                              extra_env={"WAVENET_IDX_OFFSET": str(sub_lo)})
            orch_ids.append(job_id)
            print(f"[master] orchestrator chunk on '{partition}' (walltime={walltime}): idx {sub_lo}-{sub_hi} "
                  f"({sub_size} tasks, limit={args.array_limit}{work_note}, offset={sub_lo}) -> job {job_id}")
            sub_lo = sub_hi + 1
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
    n_merged = _count_log_rows(master_log)

    inspector_log = os.path.join(args.root, "inspector_log.csv")
    n_purged = _count_log_rows(inspector_log)

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
    snapshot log (state/progress_snapshots.jsonl) this command appends to on every call,
    so the first call of a session has no rate/ETA yet. Not a running daemon: this is a
    point-in-time report, call it again later to refresh.

    Packaged bytes and days-checkpointed are true live totals (scanning packaged_h5/ is
    safe at any scale -- bounded by station count, not data volume; see PROGRESS.md).
    Downloaded bytes is an ESTIMATE (avg bytes/day from finished stations x live days
    checkpointed) rather than a live disk scan, since raw scratch_work/ can hold
    10,000+ files per station -- not safe to walk on every check at full scale."""
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
    n_with_data = 0
    n_unreadable = 0
    n_patched = 0        # patch_level >= 2
    n_prepatch = 0       # patch_level 1 (or absent, which means pre-patch)
    n_days_refused = 0
    completed_stations = set()
    completed_packaged_bytes = 0
    finished_download_bytes = 0   # sum over finished stations, used only to derive an avg $/day
    finished_days_processed = 0   # ditto
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
        completed_packaged_bytes += r.get("h5_size_bytes") or 0
        if r.get("package_ok"):
            n_with_data += 1
            completed_stations.add((r.get("network"), r.get("station")))
            finished_download_bytes += r.get("download_bytes") or 0
            finished_days_processed += r.get("n_days_processed") or 0
            # Provenance split. A result written before 2026-09-25 has no patch_level at
            # all, which IS the pre-patch marker -- absence is meaningful here, not missing
            # data. Kept visible because a pair mixing levels needs the banked timing
            # offsets applied before correlation.
            if int(r.get("patch_level") or 1) >= 2:
                n_patched += 1
            else:
                n_prepatch += 1
            # Days the overlap bug refused and silently dropped (surfaced by patch 2).
            # Pre-patch stations report nothing here even though they lost days -- that
            # loss is only visible via the banked replay, hence the separate counter.
            n_days_refused += r.get("n_days_refused") or 0

    # Packaged bytes + days-checkpointed are TRUE live totals (not just finished
    # stations) -- safe because packaged_h5/ is bounded by station count (<=2,000 files,
    # one shard + one small .daystate.json each), never by data volume. See
    # PROGRESS.md for why this is NOT done the same way for raw scratch_work/ (could be
    # millions of files at full scale -- not safe to live-scan on every check).
    packaged_bytes = completed_packaged_bytes
    n_days_checkpointed = 0
    packaged_h5_dir = os.path.join(args.root, "packaged_h5")
    if os.path.isdir(packaged_h5_dir):
        for fname in os.listdir(packaged_h5_dir):
            if fname.endswith(".h5"):
                net_sta = tuple(fname[:-3].split(".", 1)) if "." in fname[:-3] else None
                if net_sta not in completed_stations:  # avoid double-counting a finished station
                    try:
                        packaged_bytes += os.path.getsize(os.path.join(packaged_h5_dir, fname))
                    except OSError:
                        pass
            elif fname.endswith(".daystate.json"):
                try:
                    with open(os.path.join(packaged_h5_dir, fname)) as f:
                        n_days_checkpointed += len(json.load(f))
                except (json.JSONDecodeError, OSError):
                    pass

    # Downloaded bytes: ESTIMATED from live days-checkpointed x an avg bytes/day ratio
    # measured from finished stations (your suggestion) -- avoids scanning
    # scratch_work/ at all, so it's both live-feeling AND safe at full scale.
    avg_bytes_per_day = (finished_download_bytes / finished_days_processed
                         ) if finished_days_processed else None
    est_download_bytes = avg_bytes_per_day * n_days_checkpointed if avg_bytes_per_day else None

    # Total expected days for THIS run's stations (one cheap read of station_summary.csv,
    # joined against the manifest) -- gives a day-based ETA that doesn't go silent
    # between whole-station completions, unlike the old station-completion-only ETA.
    total_expected_days = None
    if os.path.exists(STATION_SUMMARY_DEFAULT):
        manifest_df = pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv"))
        summary_df = pd.read_csv(STATION_SUMMARY_DEFAULT)[["network", "station", "total_days"]]
        joined = manifest_df.merge(summary_df, on=["network", "station"], how="left")
        total_expected_days = int(joined["total_days"].fillna(0).sum())

    # NOTE: the shared master HDF5 and its logger daemon were retired 2026-09-25 -- the
    # master is now built on demand from shards by build_master_h5.py. master_log.csv is
    # therefore frozen history, NOT live progress, and is deliberately no longer reported:
    # a stale number that looks like progress is worse than no number (that is exactly how
    # logger's death went unnoticed for 21 hours).
    inspector_log = os.path.join(args.root, "inspector_log.csv")
    n_purged = _count_log_rows(inspector_log)
    n_indexed = len(glob.glob(os.path.join(args.root, "station_index", "*.json")))

    # Instrument-response coverage: a channel left in raw counts is not amplitude-comparable
    # with any other station, so this is a first-class health number, not a detail.
    meta_dir = os.path.join(args.root, "station_metadata")
    n_meta_ok = n_meta_none = 0
    for p in glob.glob(os.path.join(meta_dir, "*.json")):
        if p.endswith("_coverage.json"):
            continue
        try:
            if json.load(open(p)).get("ok"):
                n_meta_ok += 1
            else:
                n_meta_none += 1
        except (json.JSONDecodeError, OSError):
            pass

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
        f.write(json.dumps(dict(t=now, n_reported=n_reported, packaged_bytes=packaged_bytes,
                                 n_days_checkpointed=n_days_checkpointed)) + "\n")

    pct = 100.0 * n_reported / n_rows if n_rows else 0.0
    bar_width = 40
    filled = int(bar_width * n_reported / n_rows) if n_rows else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    days_frac = f" / {total_expected_days:,} ({100*n_days_checkpointed/total_expected_days:.1f}%)" \
        if total_expected_days else ""
    dl_str = f"~{est_download_bytes / 1e9:.2f} GB (est.)" if est_download_bytes is not None else "n/a yet"

    print(f"[{bar}] {n_reported}/{n_rows} stations reported ({pct:.1f}%)"
          + (f"  [{n_unreadable} unreadable, transient]" if n_unreadable else ""))
    print(f"  with data          : {n_with_data}   (patched {n_patched} / pre-patch {n_prepatch})")
    print(f"  response metadata  : {n_meta_ok} ok / {n_meta_none} none  "
          f"[raw counts are not amplitude-comparable -- run `fetch_station_metadata.py --report`]")
    print(f"  indexed / purged   : {n_indexed} / {n_purged}"
          + (f"   days refused (data lost): {n_days_refused:,}" if n_days_refused else ""))
    print(f"  days checkpointed  : {n_days_checkpointed:,}{days_frac}")
    print(f"  downloaded         : {dl_str}   packaged: {packaged_bytes / 1e9:.2f} GB")

    if prev and now > prev["t"] and (dt := now - prev["t"]) > 0:
        d_days = n_days_checkpointed - prev.get("n_days_checkpointed", 0)
        rate_days_per_hr = d_days / dt * 3600
        if rate_days_per_hr > 0 and total_expected_days:
            remaining_days = total_expected_days - n_days_checkpointed
            eta_hr = remaining_days / rate_days_per_hr
            print(f"  rate: {rate_days_per_hr:.0f} days/hr -> ETA ~{format_eta(eta_hr)} "
                  f"({dt/60:.0f} min since last check)")
        else:
            print(f"  rate: no new days checkpointed in the last {dt/60:.0f} min -- can't estimate yet")
    else:
        print("  rate: no prior snapshot yet -- run `progress` again later for a rate/ETA")
    q = _scratch_quota()
    if q:
        warn = ""
        if q["pct_of_hard"] >= 90:
            warn = "  <<< CRITICAL: purging must keep up or the campaign stalls"
        elif q["pct_of_hard"] >= 75:
            warn = "  <<< approaching the hard limit"
        print(f"  scratch quota       : {q['used_gb']:,.0f} GB used of {q['hard_gb']:,.0f} GB hard "
              f"({q['pct_of_hard']:.1f}%), soft {q['soft_gb']:,.0f} GB{warn}")

    # Service health. For a SLURM-chained service the invariant is "exactly one link queued
    # or running" -- zero looks exactly like normal quiet otherwise, which is how logger sat
    # dead for 21 hours while every other number kept climbing. Report it explicitly.
    try:
        q = subprocess.run(["squeue", "-A", "tolugboj_lab", "-h", "-o", "%j|%T"],
                            capture_output=True, text=True, timeout=30).stdout
        live = {}
        for line in q.splitlines():
            if "|" in line:
                name, _state = line.rsplit("|", 1)
                live[name.strip().split(".")[0]] = live.get(name.strip().split(".")[0], 0) + 1
        orch = live.get("orchestrator", 0)
        insp = live.get("inspector", 0)
        insp_str = ("OK (1 link)" if insp == 1 else
                    "NOT RUNNING -- chain broken or intentionally off" if insp == 0 else
                    f"{insp} links -- duplicate chain, investigate")
        print(f"  services           : orchestrator {orch} task(s) | inspector chain: {insp_str}")
    except Exception:
        pass
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
    python3 {here}/orchestrator.py $SLURM_ARRAY_TASK_ID && exit 0
    echo "[orchestrator.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
echo "[orchestrator.slurm] gave up after 4 attempts -- this is a real failure, not transient." >&2
exit 1
"""

LOGGER_SLURM = """#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -t 15-00:00:00
#SBATCH --mem-per-cpu=24G
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
    python3 {here}/logger.py --root {root} --poll-interval 30 && exit 0
    echo "[logger.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
echo "[logger.slurm] gave up after 4 attempts -- this is a real failure, not transient." >&2
exit 1
"""

INSPECTOR_SLURM = """#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -t 02:00:00
#SBATCH --mem-per-cpu=8G
#SBATCH -n 1
#SBATCH -o {root}/logs/inspector_%j.out
#SBATCH -e {root}/logs/inspector_%j.err

# ---- SELF-CHAINING: hand the baton forward BEFORE doing any work. --------------------
# This is the FIRST statement on purpose. An OOM cgroup kill, a walltime kill or a node
# failure destroys this entire job -- shell, traps and all -- so a successor submitted at
# exit time would simply never be created. Confirmed the hard way on 2026-09-24: logger
# was OOM-killed and its in-job `for attempt in 1 2 3 4` retry wrapper never ran a single
# retry, because the cgroup kill took the wrapper with it. Submitting the successor here,
# while healthy, puts the recovery record in the SLURM controller's queue -- outside this
# job's failure domain, which is the only place it survives a SIGKILL.
#   --dependency=afterany : fires on EVERY terminal state (success, failure, timeout, OOM,
#                           node death), so recovery is uniform rather than case-by-case.
#   --begin=now+20minutes : sets the tick cadence AND caps a runaway chain at ~3/hour even
#                           if every link were to die instantly.
# Progress (as opposed to continuity) comes from inspector_state.json, which is resumable:
# the chain keeps a link alive, the state file means a new link re-does only what was
# unfinished. Two separate mechanisms, deliberately.
# Guards: honour the STOP file, and refuse to chain if the previous link handed off less
# than 5 minutes ago (belt-and-braces against a fast failure loop).
CHAIN_STAMP={root}/state/inspector_last_chain
NOW=$(date +%s)
LAST=$(cat $CHAIN_STAMP 2>/dev/null || echo 0)
if [ -f {root}/state/STOP ]; then
    echo "[inspector.slurm] STOP present -- not chaining a successor." >&2
elif [ $((NOW - LAST)) -lt 300 ]; then
    echo "[inspector.slurm] last chain was $((NOW - LAST))s ago -- refusing to chain (fast-failure guard)." >&2
else
    echo $NOW > $CHAIN_STAMP
    sbatch --export=NONE -p ${{SLURM_JOB_PARTITION}} --qos ${{SLURM_JOB_PARTITION}} \\
           --dependency=afterany:$SLURM_JOB_ID --begin=now+20minutes \\
           {root}/inspector.slurm >> {root}/logs/inspector_chain.log 2>&1 \\
        && echo "[inspector.slurm] successor queued." >&2
fi

export MPLCONFIGDIR={root}/.cache/mpl
export XDG_CACHE_HOME={root}/.cache
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
# One pass, then exit -- periodic, not a daemon, so memory cannot accumulate across ticks
# and there is no walltime exposure. --timing-offsets-dir gates purging of pre-patch
# stations on their exact correction having been banked from the raw headers first.
for attempt in 1 2 3; do
    python3 {here}/inspector.py --root {root} --once \\
        --timing-offsets-dir {offsets_dir} && exit 0
    echo "[inspector.slurm] attempt $attempt failed (transient import race), retrying..." >&2
    sleep $(( RANDOM % 15 + 5 ))
done
echo "[inspector.slurm] gave up after 3 attempts -- real failure; the chain continues regardless." >&2
exit 1
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="lay out ROOT, write a manifest slice, render SLURM scripts")
    p.add_argument("--root", required=True)
    p.add_argument("--manifest", default=None, help="defaults to metadata3/fps_stations.csv")
    p.add_argument("--n-stations", type=int, default=None, help="first N rows only (sanity-check runs)")
    p.add_argument("--orchestrator-partitions", default=None,
                    help=f"comma list for the FAST TIER (default: {FAST_TIER_PARTITIONS_DEFAULT}; "
                         f"outliers always go to {OUTLIER_PARTITION} regardless of this flag; "
                         f"falls back to {ORCHESTRATOR_PARTITIONS_DEFAULT} if --no-size-sort)")
    p.add_argument("--service-partition", default=None,
                    help=f"single partition for logger/inspector (default: {SERVICE_PARTITION_DEFAULT})")
    p.add_argument("--station-summary", default=None,
                    help="defaults to metadata3/key_index_summary/station_summary.csv (for size-sort)")
    p.add_argument("--no-size-sort", action="store_true",
                    help="skip the two-tier size sort entirely (equal-count split across all partitions)")
    p.add_argument("--carry-forward-from", default=None,
                    help="an earlier run's ROOT (e.g. a canary) -- its already-completed "
                         "stations are marked done here too, under their new idx, so they "
                         "are not redownloaded/reprocessed")
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
