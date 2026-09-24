#!/usr/bin/env python
"""
Inspector: reads the logger's ledger (master_log.csv), independently verifies each
merged station's data actually landed correctly in the master HDF5, and only THEN
purges that station's raw SEED scratch directory.

This is the mechanism for docs/ncf_pipeline_stages/PROGRESS.md's "do NOT discard raw
SEED yet... discard only once the pipeline is proven solid" principle -- applied
per-station and gated on a real check, not a blanket flip of KEEP_RAW_SEED_FOR_DEBUGGING.
A station that fails verification is flagged for manual review and its raw SEED is left
alone, not purged.

Verification here is internal consistency (master-h5 vs. what the orchestrator's own
result JSON recorded it wrote), NOT a re-check against an independent third-party
reference -- that harder check (ADAMA SAC comparison) was already done once, on GT.BOSA,
in ../rover_download/compare_against_reference.py, and isn't repeated per-station here
(no independent reference exists for all 2,000 stations). What IS checked per station:
  - every channel the orchestrator packaged is present in the master group
  - each channel's sample count in the master is >= what the shard originally wrote
    (>= not == because gap-filling in merge_channel can insert zero-padding on later
    appends of the SAME station -- not expected on a first-and-only merge, but the
    inequality is the correct invariant, not a coincidental equality)
  - no all-NaN / all-zero channel (a real, previously-seen symptom of a silent
    response-removal or decimation failure)

Resumable: state/inspector_state.json records verified+purged idx's.

Usage:
    python inspector.py --root /scratch/tolugboj_lab/wavenet_ncf_production --poll-interval 30
"""
import argparse
import json
import os
import shutil
import time

import h5py
import numpy as np
import pandas as pd

from lockutil import acquire_singleton_lock


def load_state(state_path):
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return {"verified_idx": []}


def save_state(state_path, state):
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)


def append_csv(path, header, row):
    is_new = not os.path.exists(path)
    with open(path, "a") as f:
        if is_new:
            f.write(",".join(header) + "\n")
        f.write(",".join(str(row[k]) for k in header) + "\n")


def verify_station(master_path, station_key, expected_channel_meta):
    """Returns (ok: bool, reason: str)."""
    with h5py.File(master_path, "r") as master:
        if station_key not in master:
            return False, "station group missing from master"
        grp = master[station_key]
        for channel, meta in expected_channel_meta.items():
            if channel not in grp:
                return False, f"channel {channel} missing from master"
            ds = grp[channel]
            if len(ds) < meta["n_samples"]:
                return False, f"channel {channel} shorter in master ({len(ds)}) than shard wrote ({meta['n_samples']})"
            sample = ds[: min(len(ds), 10000)]
            if not np.any(sample):
                return False, f"channel {channel} is all-zero in master"
            if np.any(np.isnan(sample)):
                return False, f"channel {channel} contains NaNs in master"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--poll-interval", type=float, default=30.0)
    ap.add_argument("--dry-run", action="store_true", help="verify and report but never rmtree raw SEED")
    args = ap.parse_args()

    manifest_path = args.manifest or os.path.join(args.root, "manifest", "fps_stations.csv")
    n_total = len(pd.read_csv(manifest_path))

    results_dir = os.path.join(args.root, "results")
    state_dir = os.path.join(args.root, "state")
    os.makedirs(state_dir, exist_ok=True)
    acquire_singleton_lock(os.path.join(state_dir, "inspector.lock"))
    state_path = os.path.join(state_dir, "inspector_state.json")
    flagged_path = os.path.join(state_dir, "inspector_flagged.json")
    inspector_log_path = os.path.join(args.root, "inspector_log.csv")
    master_log_path = os.path.join(args.root, "master_log.csv")
    master_path = os.path.join(args.root, "master", "wavenet_ncf_master.h5")
    stop_path = os.path.join(args.root, "state", "STOP")

    state = load_state(state_path)
    verified = set(state["verified_idx"])
    flagged = json.load(open(flagged_path)) if os.path.exists(flagged_path) else {}

    print(f"[inspector] starting, root={args.root}, n_total={n_total}, "
          f"already_verified={len(verified)}", flush=True)

    while True:
        if not os.path.exists(master_log_path):
            time.sleep(args.poll_interval)
            if os.path.exists(stop_path):
                break
            continue

        ledger = pd.read_csv(master_log_path)
        did_work = False
        for _, row in ledger.iterrows():
            idx = int(row["idx"])
            if idx in verified or str(idx) in flagged:
                continue

            result_path_glob = os.path.join(results_dir, f"{idx:04d}_{row['network']}_{row['station']}.json")
            if not os.path.exists(result_path_glob):
                continue
            with open(result_path_glob) as f:
                r = json.load(f)
            channel_meta = r.get("channel_meta", {})
            station_key = f"{row['network']}.{row['station']}"

            ok, reason = verify_station(master_path, station_key, channel_meta)
            did_work = True
            if ok:
                raw_dir = r.get("raw_seed_kept_at")
                if raw_dir and os.path.isdir(raw_dir) and not args.dry_run:
                    shutil.rmtree(raw_dir, ignore_errors=True)
                verified.add(idx)
                state["verified_idx"] = sorted(verified)
                save_state(state_path, state)
                append_csv(inspector_log_path,
                           ["idx", "network", "station", "verified_at", "purged"],
                           dict(idx=idx, network=row["network"], station=row["station"],
                                verified_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                purged=not args.dry_run))
                print(f"[inspector] idx={idx} {station_key} -> verified"
                      f"{' + purged' if not args.dry_run else ' (dry-run, not purged)'}", flush=True)
            else:
                flagged[str(idx)] = dict(network=row["network"], station=row["station"], reason=reason)
                with open(flagged_path, "w") as f:
                    json.dump(flagged, f, indent=2)
                print(f"[inspector] idx={idx} {station_key} -> FLAGGED: {reason}", flush=True)

        if os.path.exists(stop_path):
            print(f"[inspector] stop file found, exiting. verified={len(verified)}, flagged={len(flagged)}", flush=True)
            break
        # Done once (a) the orchestrator array has fully reported (n_total result files),
        # (b) the LOGGER has fully caught up -- the ledger has a row for every result that
        # actually succeeded, not just "every row currently in it has been handled" (that
        # was a real bug, confirmed 2026-09-24: a 15-station run exited with only 4/5
        # successes verified because the logger hadn't merged the 5th yet when this
        # inspector loop happened to check -- `len(ledger)` alone can't distinguish "the
        # logger is done" from "the logger just hasn't gotten here yet"), and (c) every
        # row currently in that now-complete ledger has been verified or flagged. Stations
        # with no data never reach the ledger at all, so (b) compares against successCOUNT,
        # not n_total.
        orchestrator_done = os.path.isdir(results_dir) and len(os.listdir(results_dir)) >= n_total
        n_success = 0
        if orchestrator_done:
            for rf in os.listdir(results_dir):
                with open(os.path.join(results_dir, rf)) as f:
                    if json.load(f).get("package_ok"):
                        n_success += 1
        logger_caught_up = orchestrator_done and len(ledger) >= n_success
        ledger_fully_processed = len(verified) + len(flagged) >= len(ledger)
        if orchestrator_done and logger_caught_up and ledger_fully_processed and not did_work:
            print(f"[inspector] orchestrator finished, logger caught up ({len(ledger)}/{n_success}), "
                  f"ledger fully processed (verified={len(verified)}, flagged={len(flagged)}), exiting.", flush=True)
            break
        if not did_work:
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
