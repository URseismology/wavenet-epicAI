#!/usr/bin/env python
"""
Merges per-station HDF5 files (as produced by mdl_bluehive_quicktest.py, one file per
station: /{network}.{station}/{channel}) into a single, growable master HDF5 file.

Model: parallel production, serial merge (confirmed decision, 2026-09-23) -- many
workers each produce one small per-station file independently; this script is the one
process that appends them into the shared master file, one at a time. Not simultaneous
multi-writer HDF5, not SWMR -- see docs/ncf_pipeline_stages/hdf5_schema.md for the full
schema and rationale.

Usage:
    python build_master_h5.py --inputs packaged_h5/*.h5 --master master.h5
    python build_master_h5.py --inputs-dir packaged_h5/ --master master.h5
"""
import argparse
import glob
import os

import h5py
import numpy as np
from obspy import UTCDateTime


def merge_channel(master_grp, channel, src_ds):
    """Append src_ds's data into master_grp[channel], creating it if new, refusing
    (logging, not overwriting) on time-range overlap. Returns a short status string."""
    data = src_ds[()]
    sampling_rate = src_ds.attrs["sampling_rate"]
    # accept both spellings -- earlier test runs wrote "starttime" (no underscore)
    # before the schema doc standardized on "start_time"
    raw_start = src_ds.attrs.get("start_time", src_ds.attrs.get("starttime"))
    if raw_start is None:
        return "REFUSED (no start_time/starttime attr found)"
    start_time = UTCDateTime(raw_start)
    units = src_ds.attrs.get("units", "unknown")

    if channel not in master_grp:
        # Chunk size is a FIXED constant (one day at the 1Hz target rate this pipeline
        # always decimates to), never derived from the first batch's length -- a bug
        # caught during schema review (2026-09-23): deriving it from len(data) would
        # lock in a misaligned chunk size forever if a station's first-ever merge
        # happened to bring in less than a full day. HDF5 allows a chunk shape larger
        # than the initial data (it pads internally), so this is safe even for a
        # smaller-than-one-day first batch.
        ds = master_grp.create_dataset(
            channel, data=data, maxshape=(None,), chunks=(86400,),
            compression="gzip", compression_opts=4, dtype="float32",
        )
        ds.attrs["sampling_rate"] = sampling_rate
        ds.attrs["start_time"] = str(start_time)
        ds.attrs["units"] = units
        return "created"

    ds = master_grp[channel]
    existing_start = UTCDateTime(ds.attrs["start_time"])
    existing_sr = ds.attrs["sampling_rate"]
    if abs(existing_sr - sampling_rate) > 1e-6:
        return f"REFUSED (sampling_rate mismatch: existing={existing_sr}, new={sampling_rate})"

    existing_end = existing_start + (len(ds) - 1) / existing_sr
    new_end = start_time + (len(data) - 1) / sampling_rate

    if start_time <= existing_end:
        return (f"REFUSED (overlap: new data starts {start_time}, existing data runs "
                f"through {existing_end})")

    gap_samples = int(round((start_time - existing_end) * sampling_rate)) - 1
    gap_samples = max(gap_samples, 0)
    old_len = len(ds)
    new_len = old_len + gap_samples + len(data)
    ds.resize((new_len,))
    if gap_samples:
        ds[old_len:old_len + gap_samples] = 0.0
    ds[old_len + gap_samples:new_len] = data
    return f"appended ({len(data):,} samples, gap={gap_samples} samples)"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", nargs="*", default=None, help="explicit list of per-station .h5 files")
    ap.add_argument("--inputs-dir", default=None, help="directory of per-station .h5 files (all *.h5 inside)")
    ap.add_argument("--master", required=True, help="path to the shared master .h5 file (created if missing)")
    args = ap.parse_args()

    inputs = list(args.inputs or [])
    if args.inputs_dir:
        inputs += sorted(glob.glob(os.path.join(args.inputs_dir, "*.h5")))
    if not inputs:
        raise SystemExit("No input files given (use --inputs or --inputs-dir)")

    with h5py.File(args.master, "a") as master:
        for path in inputs:
            with h5py.File(path, "r") as src:
                for station_key in src.keys():
                    src_grp = src[station_key]
                    master_grp = master.require_group(station_key)
                    for attr in ("network", "station", "latitude", "longitude"):
                        if attr in src_grp.attrs and attr not in master_grp.attrs:
                            master_grp.attrs[attr] = src_grp.attrs[attr]
                    # Full raw StationXML: copy once per station, never re-copy/duplicate
                    # on subsequent merges of the same station (e.g. a later day-range
                    # append) -- it's the same station-level metadata document each time.
                    if "_stationxml_raw" in src_grp and "_stationxml_raw" not in master_grp:
                        master_grp.create_dataset("_stationxml_raw", data=src_grp["_stationxml_raw"][()])
                        print(f"  {path} :: {station_key}/_stationxml_raw -> copied (once)")
                    for channel in src_grp.keys():
                        if channel == "_stationxml_raw":
                            continue
                        status = merge_channel(master_grp, channel, src_grp[channel])
                        print(f"  {path} :: {station_key}/{channel} -> {status}")

    print(f"\nDone. Master file: {args.master} "
          f"({os.path.getsize(args.master):,} bytes)")


if __name__ == "__main__":
    main()
