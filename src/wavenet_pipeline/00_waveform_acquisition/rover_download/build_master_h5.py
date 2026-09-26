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


# How many whole samples of day-boundary overlap may be trimmed off the front of new data
# instead of refusing the day outright. Raised twice, each time by production evidence:
#   2    -- the smoke test's value, from the XD pair's single-extra-sample case only.
#           Refused 18% of all attempted days; NL.HGN alone lost 490 of 572.
#   60   -- covered the dominant cluster, every refusal being 10-19 samples (median 15):
#           day files starting seconds before midnight while the previous day ran seconds
#           past it. Still refused AC.VLO's 29 days, whose overlaps are 136-342 samples.
#   3600 -- current. A record straddling midnight can run minutes into the next day (the
#           smoke test's own RUNG case ran 42 s), so minutes-scale overlap is a normal
#           boundary artefact, not corruption.
# Trimming is correct at any of these sizes: the leading samples cover a time range already
# stored, so dropping them and appending the rest is exactly right. The guard exists to
# catch a gross logic error -- a whole day re-appended -- and 3600 is still only 4% of a
# day, so that case is still REFUSED. Trims are COUNTED (see orchestrator's n_days_trimmed)
# rather than silent, because a threshold this permissive must not hide a station that is
# routinely overlapping by minutes. Override with WAVENET_MAX_TRIM_SAMPLES.
MAX_TRIM_SAMPLES = int(os.environ.get("WAVENET_MAX_TRIM_SAMPLES", "3600"))


def align_to_integer_second(tr):
    """[PATCH 1] Shift `tr`'s samples (band-limited fractional delay) so that sample 0 falls exactly on an
    integer UTC second, and set starttime accordingly; returns `tr`.

    Why: each processed day is decimated from its own first raw sample, so its 1 Hz samples sit at an
    arbitrary sub-second phase that differs from day to day. append_channel_data places days on a grid
    anchored at the first day's start; without this alignment the placement error reaches ~1 s and
    changes by whole seconds between days (measured on XD.MTAN/XD.RUNG: -0.35..+0.70 s), which
    corrupts inter-station timing and makes later days look like overlaps. Data are already low-passed
    at 0.4 Hz (< Nyquist), so the FFT fractional delay is accurate; the residual edge effect sits in the
    5 % taper zone. Intended for the pipeline's 1 Hz output (call after decimation)."""
    dt = tr.stats.delta
    t = tr.stats.starttime.timestamp
    t_int = round(t)
    s = t - t_int                                  # seconds; |s| <= 0.5
    if abs(s) > 1e-6 * dt:
        n = len(tr.data)
        F = np.fft.rfft(tr.data)
        f = np.fft.rfftfreq(n, d=dt)
        tr.data = np.fft.irfft(F * np.exp(-2j * np.pi * f * s), n=n)    # y[j] = x(j - s/dt): delay by s
    tr.stats.starttime = UTCDateTime(t_int)
    return tr


def append_channel_data(grp, channel, data, sampling_rate, start_time, units,
                         azimuth=None, dip=None, max_trim_samples=MAX_TRIM_SAMPLES):
    """Core gap-fill/append logic, shared by merge_channel (below, used by logger.py to
    merge a completed per-station shard into the master file) and orchestrator.py's
    day-by-day checkpointing (added 2026-09-24 -- appends one calendar day's processed
    trace directly into the per-station shard as it's produced, instead of building a
    station's whole history in memory first; see orchestrator.py's module docstring for
    why). Both call sites need the exact same create-or-append-with-gap-fill behavior,
    so it lives in one place rather than two independently-maintained copies.

    `start_time` may be a UTCDateTime or anything UTCDateTime() accepts. Creates the
    dataset if `channel` doesn't exist yet, refuses (returns a status string, doesn't
    raise or overwrite) on a time-range overlap. Returns a short status string."""
    start_time = UTCDateTime(start_time)

    if channel not in grp:
        # Chunk size is a FIXED constant (one day at the 1Hz target rate this pipeline
        # always decimates to), never derived from the first batch's length -- a bug
        # caught during schema review (2026-09-23): deriving it from len(data) would
        # lock in a misaligned chunk size forever if a station's first-ever write
        # happened to bring in less than a full day. HDF5 allows a chunk shape larger
        # than the initial data (it pads internally), so this is safe even for a
        # smaller-than-one-day first batch.
        ds = grp.create_dataset(
            channel, data=data, maxshape=(None,), chunks=(86400,),
            compression="gzip", compression_opts=4, dtype="float32",
        )
        ds.attrs["sampling_rate"] = sampling_rate
        ds.attrs["start_time"] = str(start_time)
        ds.attrs["units"] = units
        if azimuth is not None:
            ds.attrs["azimuth"] = azimuth
        if dip is not None:
            ds.attrs["dip"] = dip
        return "created"

    ds = grp[channel]
    existing_start = UTCDateTime(ds.attrs["start_time"])
    existing_sr = ds.attrs["sampling_rate"]
    if abs(existing_sr - sampling_rate) > 1e-6:
        return f"REFUSED (sampling_rate mismatch: existing={existing_sr}, new={sampling_rate})"

    existing_end = existing_start + (len(ds) - 1) / existing_sr

    n_trimmed = 0
    if start_time <= existing_end + 0.5 / sampling_rate:
        # [PATCH 1b] whole-sample overlap: trim the coinciding leading samples of the NEW data instead of
        # refusing the entire day. Larger overlaps (real duplicated data) are still refused.
        overlap = int(round((existing_end - start_time) * sampling_rate)) + 1
        if overlap >= len(data):
            # entirely inside data already stored: an idempotent re-append (e.g. a resubmitted array task) -- skip it
            return f"SKIPPED (new data fully covered by existing data, overlap={overlap} samples)"
        if overlap > max_trim_samples:
            return (f"REFUSED (overlap: new data starts {start_time}, existing data runs through "
                    f"{existing_end}; {overlap} samples exceeds max_trim_samples={max_trim_samples})")
        data = data[overlap:]
        start_time = start_time + overlap / sampling_rate
        n_trimmed = overlap

    gap_samples = int(round((start_time - existing_end) * sampling_rate)) - 1
    gap_samples = max(gap_samples, 0)
    old_len = len(ds)
    new_len = old_len + gap_samples + len(data)
    ds.resize((new_len,))
    if gap_samples:
        # Filled in bounded chunks, not one `ds[a:b] = 0.0` assignment covering the
        # whole gap -- found by direct testing (2026-09-24): a real, not hypothetical,
        # multi-decade gap between two data-bearing spans (a station re-deployed years
        # later is a real scenario, not just a test artifact) meant a single gap-fill
        # of ~633 million samples, which is exactly the kind of unbounded-by-history-
        # length memory spike this whole day-by-day rewrite exists to eliminate. Chunk
        # size matches the dataset's own chunk size (86400 = one day at 1Hz) so this
        # doesn't introduce a new tuning parameter.
        GAP_FILL_CHUNK = 86400
        pos = old_len
        gap_end = old_len + gap_samples
        while pos < gap_end:
            n = min(GAP_FILL_CHUNK, gap_end - pos)
            ds[pos:pos + n] = 0.0
            pos += n
    ds[old_len + gap_samples:new_len] = data
    # azimuth/dip are set once at creation -- a channel's orientation doesn't change
    # between appends of the same channel, so there's nothing to update here.
    return (f"appended ({len(data):,} samples, gap={gap_samples} samples"
            + (f", trimmed {n_trimmed} overlapping sample(s)" if n_trimmed else "") + ")")


def merge_channel(master_grp, channel, src_ds):
    """Append src_ds's data into master_grp[channel] -- thin wrapper around
    append_channel_data() that pulls the needed values out of a source h5py Dataset
    (the shape logger.py has them in: a completed per-station shard's own dataset)."""
    data = src_ds[()]
    sampling_rate = src_ds.attrs["sampling_rate"]
    # accept both spellings -- earlier test runs wrote "starttime" (no underscore)
    # before the schema doc standardized on "start_time"
    raw_start = src_ds.attrs.get("start_time", src_ds.attrs.get("starttime"))
    if raw_start is None:
        return "REFUSED (no start_time/starttime attr found)"
    units = src_ds.attrs.get("units", "unknown")
    # Previously dropped on merge into the master file (a real gap, fixed 2026-09-24
    # alongside this refactor) -- orchestrator.py writes these per-channel, but the
    # master file never carried them past the per-station shard until now.
    azimuth = src_ds.attrs.get("azimuth")
    dip = src_ds.attrs.get("dip")
    return append_channel_data(master_grp, channel, data, sampling_rate, raw_start,
                                units, azimuth=azimuth, dip=dip)


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
