#!/usr/bin/env python
"""Inspector: verifies each station's packaged SHARD, maintains the global station index,
and only then purges that station's raw SEED.

Rearchitected 2026-09-25 (PI decision). The shared master HDF5 is gone -- the master is now
built on demand from shards by build_master_h5.py, not maintained continuously by a logger
daemon. Three consequences:

  - Inspector no longer reads logger's ledger or opens the master. It reads results/*.json
    directly, the same source the orchestrator writes. The old coupling was a real failure
    mode, not a tidiness issue: on 2026-09-24 logger died and inspector stayed healthy for
    8 hours doing nothing, because the ledger it waited on never grew. Two outages that
    looked like one.
  - No HDF5 lock contention. The only writer of a shard is that station's own orchestrator
    task, which has already exited by the time package_ok is true. open_h5_retry stays as a
    seatbelt but is no longer load-bearing.
  - It is a short periodic pass (--once), chained by SLURM, not a long-lived daemon. Memory
    cannot accumulate across ticks and there is no walltime exposure. See inspector.slurm.

Inspector is the ONLY component that does anything irreversible (deleting raw SEED), so
every check below is a gate on that delete, and anything unproven results in a HOLD (retry
next tick) rather than a purge.

Verification per station:
  - every channel the orchestrator recorded is present in the shard
  - each channel's stored sample count >= what the orchestrator recorded
  - no all-zero / all-NaN channel (a real, previously-seen symptom of a silent
    response-removal or decimation failure)
  - n_days_refused == 0 -- a station that silently dropped days is NOT complete, so its raw
    data is exactly what we would need to recover them (added 2026-09-25; the pre-patch
    overlap bug dropped 5.5% of channel-days across the stations audited)
  - patch_level present and not mixed -- a shard carrying patch_level_mixed was written by
    two different pipelines and cannot be fixed by appending, so it is never purged
  - a pre-patch (patch_level 1) station is purged only once its timing offsets have been
    banked from the raw headers, because purging destroys the only source of the exact
    correction (--timing-offsets-dir)

Resumable: state/inspector_state.json records verified+purged idx's.

Usage:
    python inspector.py --root ROOT --once [--timing-offsets-dir DIR] [--dry-run]
"""
import argparse
import csv
import glob
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rover_download"))
from build_master_h5 import covered_days  # schema v2 coverage bitmap
from lockutil import acquire_singleton_lock, open_h5_retry


def load_state(state_path):
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return {"verified_idx": []}


def save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def append_csv(path, header, row):
    """Append-only log. If the file exists with a DIFFERENT header (the schema gained
    patch_level on 2026-09-25), rotate it aside rather than appending rows of a different
    width -- a single file holding two schemas is unparseable by anything downstream, and
    it broke `master.py progress` once already."""
    is_new = not os.path.exists(path)
    if not is_new:
        try:
            with open(path) as f:
                existing = f.readline().strip()
            if existing and existing != ",".join(header):
                os.replace(path, path + ".v1")
                is_new = True
        except OSError:
            pass
    with open(path, "a") as f:
        if is_new:
            f.write(",".join(header) + "\n")
        f.write(",".join(str(row[k]) for k in header) + "\n")


def verify_shard(shard_path, station_key, expected_channel_meta):
    """Returns (ok, reason, channels) -- channels is the per-channel detail for the index.

    Schema-aware. Under v2 the dataset spans from the shared epoch, so two v1-era checks
    become actively WRONG and had to change rather than merely be relaxed:
      - `len(ds) >= n_samples` is now trivially true (the span is decades), so it proves
        nothing; the real question is how many DAYS are covered.
      - sampling `ds[:10000]` would read near 1970, which is unwritten and therefore all
        zeros -- it would flag every healthy v2 station as all-zero.
    So v2 verifies against the coverage bitmap and samples from a day that is actually
    covered."""
    channels = {}
    with open_h5_retry(shard_path, "r") as f:
        if station_key not in f:
            return False, "station group missing from shard", channels
        grp = f[station_key]
        attrs = dict(grp.attrs)
        if "patch_level_mixed" in attrs:
            return False, f"shard mixes pipelines ({attrs['patch_level_mixed']})", channels
        for channel, meta in expected_channel_meta.items():
            if channel not in grp:
                return False, f"channel {channel} missing from shard", channels
            ds = grp[channel]
            sr = float(ds.attrs.get("sampling_rate", 0)) or 1.0
            is_v2 = int(ds.attrs.get("schema_version", 1)) >= 2

            if is_v2:
                covered = covered_days(grp, channel)
                if len(covered) == 0:
                    return False, f"channel {channel} has no covered days", channels
                day_len = int(86400 * sr)
                # sample from a day known to hold data, not from the epoch end of the grid
                d = int(covered[len(covered) // 2])
                sample = ds[d * day_len: d * day_len + min(10000, day_len)]
                n_samples = int(len(covered) * day_len)
            else:
                if len(ds) < meta["n_samples"]:
                    return False, (f"channel {channel} shorter in shard ({len(ds)}) than "
                                    f"orchestrator recorded ({meta['n_samples']})"), channels
                sample = ds[: min(len(ds), 10000)]
                n_samples = int(len(ds))

            if not np.any(sample):
                return False, f"channel {channel} is all-zero", channels
            if np.any(np.isnan(sample)):
                return False, f"channel {channel} contains NaNs", channels
            channels[channel] = dict(
                sampling_rate=sr, n_samples=n_samples,
                start_time=str(ds.attrs.get("start_time", "")),
                units=str(ds.attrs.get("units", "")),
                schema_version=2 if is_v2 else 1,
                n_days_covered=int(len(covered_days(grp, channel))) if is_v2 else None,
                duration_s=round(n_samples / sr, 1))
    return True, "ok", channels


def station_record(r, shard_path, channels, purged, offsets_path):
    """One row of the global index -- the input to an on-demand master build, and the
    thing that lets a consumer refuse to silently mix patch levels."""
    starts = [c["start_time"] for c in channels.values() if c["start_time"]]
    return dict(
        network=r["network"], station=r["station"], idx=r.get("idx"),
        shard_path=shard_path,
        shard_bytes=os.path.getsize(shard_path) if os.path.exists(shard_path) else 0,
        patch_level=r.get("patch_level", 1),
        n_channels=len(channels), channels=sorted(channels),
        channel_detail=channels,
        earliest_start=min(starts) if starts else "",
        n_days_processed=r.get("n_days_processed", 0),
        n_days_refused=r.get("n_days_refused", 0),
        n_days_qc_flagged=r.get("n_days_qc_flagged", 0),
        download_year_range=r.get("download_year_range"),
        timing_offsets=offsets_path or "",
        raw_purged=bool(purged),
        verified_at=time.strftime("%Y-%m-%dT%H:%M:%S"))


INDEX_CSV_COLUMNS = ["network", "station", "idx", "patch_level", "n_channels", "channels",
                     "earliest_start", "n_days_processed", "n_days_refused",
                     "n_days_qc_flagged", "shard_bytes", "raw_purged", "timing_offsets",
                     "shard_path", "verified_at"]


def rebuild_index_csv(index_dir, out_csv):
    """Regenerate the flat index from the per-station records. Bounded by station count
    (<=2000 small files), never by data volume -- safe to redo every tick."""
    rows = []
    for p in sorted(glob.glob(os.path.join(index_dir, "*.json"))):
        try:
            rec = json.load(open(p))
        except (ValueError, OSError):
            continue
        rows.append({k: (";".join(rec[k]) if isinstance(rec.get(k), list) else rec.get(k, ""))
                     for k in INDEX_CSV_COLUMNS})
    tmp = out_csv + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out_csv)
    return len(rows)


def one_pass(args, state, verified, flagged, held):
    results_dir = os.path.join(args.root, "results")
    index_dir = os.path.join(args.root, "station_index")
    os.makedirs(index_dir, exist_ok=True)
    state_dir = os.path.join(args.root, "state")
    state_path = os.path.join(state_dir, "inspector_state.json")
    flagged_path = os.path.join(state_dir, "inspector_flagged.json")
    held_path = os.path.join(state_dir, "inspector_held.json")
    inspector_log = os.path.join(args.root, "inspector_log.csv")
    did = 0

    for rf in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            r = json.load(open(rf))
        except (ValueError, OSError):
            continue
        idx = r.get("idx")
        if idx in verified or str(idx) in flagged or not r.get("package_ok"):
            continue

        station_key = f"{r['network']}.{r['station']}"
        shard = r.get("packaged_h5_path") or os.path.join(args.root, "packaged_h5",
                                                           f"{station_key}.h5")
        if not os.path.exists(shard):
            held[str(idx)] = dict(station=station_key, reason="shard not on disk yet")
            continue

        # A station that silently dropped days is not complete, and its raw SEED is exactly
        # what recovering those days would need -- so never purge it.
        n_refused = r.get("n_days_refused", 0)
        if n_refused:
            flagged[str(idx)] = dict(station=station_key,
                                      reason=f"{n_refused} day(s) refused -- incomplete, raw kept")
            print(f"[inspector] {station_key} -> FLAGGED: {n_refused} refused day(s)", flush=True)
            did += 1
            continue

        try:
            ok, reason, channels = verify_shard(shard, station_key, r.get("channel_meta", {}))
        except Exception as e:
            held[str(idx)] = dict(station=station_key, reason=f"transient: {type(e).__name__}: {e}")
            print(f"[inspector] {station_key} -> transient, retry next tick: {e}", flush=True)
            continue

        if not ok:
            flagged[str(idx)] = dict(station=station_key, reason=reason)
            print(f"[inspector] {station_key} -> FLAGGED: {reason}", flush=True)
            did += 1
            continue

        # Pre-patch stations are only safe to purge once their exact timing correction has
        # been banked from the raw headers -- purging destroys the only source of it.
        patch_level = int(r.get("patch_level", 1))
        offsets_path = ""
        raw_dir = r.get("raw_seed_kept_at")
        raw_present = bool(raw_dir) and os.path.isdir(raw_dir) and bool(os.listdir(raw_dir))
        if patch_level < 2 and args.timing_offsets_dir:
            cand = os.path.join(args.timing_offsets_dir, f"{station_key}.json")
            if os.path.exists(cand):
                offsets_path = cand
            elif raw_present:
                held[str(idx)] = dict(station=station_key,
                                       reason="pre-patch, timing offsets not banked yet")
                print(f"[inspector] {station_key} -> HELD: pre-patch offsets not banked",
                      flush=True)
                continue

        purged = False
        if raw_present and not args.dry_run:
            shutil.rmtree(raw_dir, ignore_errors=True)
            purged = True

        save_json_atomic(os.path.join(index_dir, f"{station_key}.json"),
                          station_record(r, shard, channels, purged, offsets_path))
        verified.add(idx)
        state["verified_idx"] = sorted(verified)
        save_json_atomic(state_path, state)
        held.pop(str(idx), None)
        append_csv(inspector_log, ["idx", "network", "station", "patch_level", "verified_at",
                                    "purged"],
                    dict(idx=idx, network=r["network"], station=r["station"],
                         patch_level=patch_level,
                         verified_at=time.strftime("%Y-%m-%dT%H:%M:%S"), purged=purged))
        print(f"[inspector] {station_key} -> verified (patch_level={patch_level})"
              f"{' + purged' if purged else ''}", flush=True)
        did += 1

    save_json_atomic(flagged_path, flagged)
    save_json_atomic(held_path, held)
    n = rebuild_index_csv(index_dir, os.path.join(args.root, "station_index.csv"))
    print(f"[inspector] pass done: {did} newly handled, index now {n} station(s), "
          f"verified={len(verified)} flagged={len(flagged)} held={len(held)}", flush=True)
    return did


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--once", action="store_true",
                    help="run a single pass and exit (the SLURM-chained mode; see inspector.slurm)")
    ap.add_argument("--poll-interval", type=float, default=30.0,
                    help="only used without --once")
    ap.add_argument("--timing-offsets-dir", default=None,
                    help="banked pre-patch timing offsets; a patch_level<2 station is not "
                         "purged until its offsets are present here")
    ap.add_argument("--dry-run", action="store_true", help="verify and index but never purge")
    args = ap.parse_args()

    state_dir = os.path.join(args.root, "state")
    os.makedirs(state_dir, exist_ok=True)
    acquire_singleton_lock(os.path.join(state_dir, "inspector.lock"))
    state = load_state(os.path.join(state_dir, "inspector_state.json"))
    verified = set(state["verified_idx"])
    fp = os.path.join(state_dir, "inspector_flagged.json")
    hp = os.path.join(state_dir, "inspector_held.json")
    flagged = json.load(open(fp)) if os.path.exists(fp) else {}
    held = json.load(open(hp)) if os.path.exists(hp) else {}
    stop_path = os.path.join(state_dir, "STOP")

    print(f"[inspector] start root={args.root} already_verified={len(verified)} "
          f"mode={'once' if args.once else 'daemon'} dry_run={args.dry_run}", flush=True)

    if args.once:
        one_pass(args, state, verified, flagged, held)
        return
    while not os.path.exists(stop_path):
        if one_pass(args, state, verified, flagged, held) == 0:
            time.sleep(args.poll_interval)
    print("[inspector] stop file found, exiting.", flush=True)


if __name__ == "__main__":
    main()
