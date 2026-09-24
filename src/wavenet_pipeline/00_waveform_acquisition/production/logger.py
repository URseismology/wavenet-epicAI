#!/usr/bin/env python
"""
Logger: the single serial-merge process. Polls results/*.json for orchestrator tasks
that finished packaging, appends each one's per-station HDF5 shard into the shared
master HDF5, and records completion in an append-only ledger (master_log.csv).

Why a single process, not each orchestrator writing directly to the master file: HDF5
has no safe concurrent-multi-writer mode in this setup (not SWMR) -- confirmed decision,
docs/ncf_pipeline_stages/hdf5_schema.md. "Parallel production, serial merge": many
orchestrator tasks run at once, each producing its OWN small file; this one process is
the only thing that ever opens the master file for writing, one station at a time.

Resumable: state/logger_state.json records which idx's have already been merged, so a
restarted logger doesn't double-append. Run as a long-lived SLURM job (logger.slurm)
alongside the orchestrator array; exits when a stop file appears (written by
`master.py --stop`) or when every manifest row has a terminal result (merged or
permanently failed).

Usage:
    python logger.py --root /scratch/tolugboj_lab/wavenet_ncf_production --poll-interval 30
"""
import argparse
import glob
import json
import os
import sys
import time

import h5py
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rover_download"))
from build_master_h5 import merge_channel  # reuse the exact verified append/gap logic
from lockutil import acquire_singleton_lock, open_h5_retry


def load_state(state_path):
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return {"merged_idx": []}


def save_state(state_path, state):
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)  # atomic -- safe against a crash mid-write


def append_ledger(ledger_path, row):
    is_new = not os.path.exists(ledger_path)
    with open(ledger_path, "a") as f:
        if is_new:
            f.write("idx,network,station,n_channels,h5_size_bytes,merged_at,status\n")
        f.write(",".join(str(row[k]) for k in
                ("idx", "network", "station", "n_channels", "h5_size_bytes", "merged_at", "status")) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--manifest", default=None, help="defaults to ROOT/manifest/fps_stations.csv")
    ap.add_argument("--poll-interval", type=float, default=30.0)
    args = ap.parse_args()

    manifest_path = args.manifest or os.path.join(args.root, "manifest", "fps_stations.csv")
    n_total = len(pd.read_csv(manifest_path))

    results_dir = os.path.join(args.root, "results")
    state_dir = os.path.join(args.root, "state")
    os.makedirs(state_dir, exist_ok=True)
    acquire_singleton_lock(os.path.join(state_dir, "logger.lock"))
    state_path = os.path.join(state_dir, "logger_state.json")
    ledger_path = os.path.join(args.root, "master_log.csv")
    master_path = os.path.join(args.root, "master", "wavenet_ncf_master.h5")
    os.makedirs(os.path.dirname(master_path), exist_ok=True)
    stop_path = os.path.join(args.root, "state", "STOP")

    state = load_state(state_path)
    merged = set(state["merged_idx"])

    print(f"[logger] starting, root={args.root}, n_total={n_total}, "
          f"already_merged={len(merged)}", flush=True)

    while True:
        result_files = sorted(glob.glob(os.path.join(results_dir, "*.json")))
        did_work = False
        for rf in result_files:
            with open(rf) as f:
                r = json.load(f)
            idx = r["idx"]
            if idx in merged or not r.get("package_ok"):
                continue

            h5_path = r.get("packaged_h5_path")
            if not h5_path or not os.path.exists(h5_path):
                continue  # orchestrator result says ok but shard vanished -- skip, don't crash the loop

            station_key = f"{r['network']}.{r['station']}"
            # inspector.py opens this same master file for READ concurrently -- retry
            # on the resulting HDF5 file-lock conflict rather than crash the whole loop
            # (mirrors inspector.py's own protection, see lockutil.open_h5_retry).
            with open_h5_retry(master_path, "a") as master, h5py.File(h5_path, "r") as src:
                src_grp = src[station_key]
                master_grp = master.require_group(station_key)
                for attr in ("network", "station", "latitude", "longitude"):
                    if attr in src_grp.attrs and attr not in master_grp.attrs:
                        master_grp.attrs[attr] = src_grp.attrs[attr]
                if "_stationxml_raw" in src_grp and "_stationxml_raw" not in master_grp:
                    master_grp.create_dataset("_stationxml_raw", data=src_grp["_stationxml_raw"][()])
                for channel in src_grp.keys():
                    if channel == "_stationxml_raw":
                        continue
                    status = merge_channel(master_grp, channel, src_grp[channel])
                    print(f"[logger] idx={idx} {station_key}/{channel} -> {status}", flush=True)

            append_ledger(ledger_path, dict(
                idx=idx, network=r["network"], station=r["station"],
                n_channels=r.get("n_processed", 0), h5_size_bytes=r.get("h5_size_bytes", 0),
                merged_at=time.strftime("%Y-%m-%dT%H:%M:%S"), status="merged",
            ))
            merged.add(idx)
            state["merged_idx"] = sorted(merged)
            save_state(state_path, state)
            did_work = True

        if os.path.exists(stop_path):
            print(f"[logger] stop file found, exiting. merged={len(merged)}/{n_total}", flush=True)
            break
        # Completion signal is "every array task has produced a result file", not "every
        # station merged" -- a real fraction of stations genuinely have no data (the
        # known 367-station gap) and will never reach package_ok=True, so merged never
        # reaches n_total on its own. One result file per idx (created/overwritten by
        # orchestrator.py) makes len(result_files) a reliable count.
        if len(result_files) >= n_total:
            print(f"[logger] all {n_total} orchestrator tasks reported, merged={len(merged)}, exiting.", flush=True)
            break
        if not did_work:
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
