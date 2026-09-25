#!/usr/bin/env python
"""
Real "test with teeth" (PI, 2026-09-23): run our preprocess->package pipeline against
an already-downloaded, real, multi-decade single-station archive (zero download cost),
to learn how the pipeline scales on genuinely large single-station histories -- not a
7-day toy test. Reads raw SEED directly from an existing ROVER datarepo
(PrjXX_SAmericaNoise/2_Data/2_RoverDB/{net}/{net}-{sta}/datarepo/data/{net}/{year}/{doy}/),
read-only -- never modifies that archive.

Design difference from mdl_bluehive_quicktest.py, deliberate for this scale: processes
PER-DAY (detrend/response-removal/filter/decimate each day independently), THEN
concatenates the already-decimated per-day arrays -- not "read everything, merge, THEN
process once". This keeps memory bounded regardless of how many years of history exist
(a multi-decade un-decimated trace at native rate would be enormous to hold at once).
Real calendar gaps between present day-directories are zero-filled at concatenation
time, matching the master-file schema's "continuous, gaps zero-filled" invariant.

No StationXML exists alongside this particular archive (checked, only a small CSV of
metadata) -- fetched fresh via a single small FDSN get_stations(level="response") call,
not from the bulk archive.
"""
import argparse
import glob
import io
import os
import time

import h5py
import numpy as np
from obspy import read, UTCDateTime
from obspy.clients.fdsn import Client


def process_one_day(day_dir, station):
    """Reads all files in one day-directory, returns {channel: preprocessed 1-day
    numpy array at target rate} -- decimation happens HERE, per day, so nothing large
    is ever held across many days at once.

    Real archive quirk found by direct testing (2026-09-23, II-EFI, a 27-year archive):
    some days have multiple files sharing the same channel id but DIFFERENT sampling
    rates (an instrument/epoch change mid-day) -- ObsPy's plain `Stream.merge()` refuses
    to merge those and raises. Fixed by grouping per (id, sampling_rate) first and
    keeping only the group with the most total samples for that day -- a real,
    deliberate data-quality choice (dominant epoch wins for that day), not silently
    dropping the conflict."""
    files = glob.glob(os.path.join(day_dir, f"{station}.*"))
    if not files:
        return {}
    all_traces = []
    for fp in files:
        try:
            all_traces.extend(read(fp))
        except Exception:
            continue

    groups = {}  # (id, sampling_rate) -> list of traces
    for tr in all_traces:
        groups.setdefault((tr.id, tr.stats.sampling_rate), []).append(tr)

    # For each channel id, keep only the (id, rate) group with the most total samples.
    best_group_per_id = {}
    for (tr_id, rate), traces in groups.items():
        total_samples = sum(len(tr.data) for tr in traces)
        if tr_id not in best_group_per_id or total_samples > best_group_per_id[tr_id][1]:
            best_group_per_id[tr_id] = (traces, total_samples, rate)

    out = {}
    for tr_id, (traces, _, rate) in best_group_per_id.items():
        try:
            st = obspy_stream_from_traces(traces)
            st.merge(fill_value=0)
            tr = st[0]
            tr.detrend("linear")
            tr.detrend("demean")
            out[tr.stats.channel] = dict(
                trace=tr, sampling_rate=tr.stats.sampling_rate,
                starttime=tr.stats.starttime,
            )
        except Exception as e:
            print(f"    [dropped {tr_id} @ {rate}Hz in {day_dir}: {type(e).__name__}: {e}]")
            continue
    return out


def obspy_stream_from_traces(traces):
    from obspy import Stream
    return Stream(traces=list(traces))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datarepo-data", required=True, help="path to .../datarepo/data/{net}")
    ap.add_argument("--network", required=True)
    ap.add_argument("--station", required=True)
    ap.add_argument("--out-h5", required=True)
    ap.add_argument("--limit-days", type=int, default=None, help="process at most N days (for a quick smoke test before the real run)")
    args = ap.parse_args()

    t_start = time.time()

    print("Fetching StationXML fresh (single small FDSN call, not from the bulk archive)...")
    client = Client("IRIS")
    inv = client.get_stations(network=args.network, station=args.station, level="response")

    day_dirs = sorted(glob.glob(os.path.join(args.datarepo_data, "*", "*")))
    if args.limit_days:
        day_dirs = day_dirs[:args.limit_days]
    print(f"Found {len(day_dirs)} day-directories to process.")

    per_channel_days = {}  # channel -> list of (date, {"data":..., "sampling_rate":..., "starttime":...})
    t_read = time.time()
    n_ok, n_empty = 0, 0
    for i, day_dir in enumerate(day_dirs):
        day_result = process_one_day(day_dir, args.station)
        if not day_result:
            n_empty += 1
            continue
        n_ok += 1
        for channel, info in day_result.items():
            tr = info["trace"]
            response_removed = False
            azimuth, dip = None, None
            try:
                tr.remove_response(inventory=inv, output="DISP", water_level=60,
                                    pre_filt=(0.001, 0.005, 0.4, 0.5))
                response_removed = True
            except Exception:
                pass
            try:
                meta = inv.get_channel_metadata(tr.id, tr.stats.starttime)
                azimuth, dip = meta["azimuth"], meta["dip"]
            except Exception:
                pass
            # Guard the lowpass against channels whose native rate is already at or
            # below the target 0.4 Hz corner (Nyquist = sampling_rate/2) -- found by
            # direct testing (2026-09-23, GT-BOSA archive on atos): a fixed 0.4 Hz
            # lowpass crashes scipy's iirfilter ("critical frequencies must be
            # 0 < Wn < 1") on any channel already at or below 0.8 Hz. Mirrors the
            # existing decimate guard just below.
            if tr.stats.sampling_rate > 0.8:
                tr.filter("lowpass", freq=0.4, corners=4, zerophase=True)
            tr.filter("highpass", freq=1.0 / 3600.0, corners=4, zerophase=True)
            if tr.stats.sampling_rate >= 2:
                tr.decimate(factor=int(round(tr.stats.sampling_rate)), no_filter=True)
            tr.detrend("demean")
            tr.taper(max_percentage=0.05)
            per_channel_days.setdefault(channel, []).append(dict(
                data=tr.data.astype(np.float32), starttime=tr.stats.starttime,
                sampling_rate=tr.stats.sampling_rate, response_removed=response_removed,
                azimuth=azimuth, dip=dip,
            ))
        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(day_dirs)} days processed ({time.time()-t_read:.1f}s elapsed)")

    t_process_done = time.time()
    print(f"Day-by-day processing done: {n_ok} ok, {n_empty} empty, "
          f"{t_process_done - t_read:.1f}s total ({(t_process_done - t_read) / max(n_ok,1):.2f}s/day avg)")

    # Concatenate each channel's per-day arrays into one continuous series, zero-filling
    # real calendar gaps between present days (schema invariant: continuous, no true gaps).
    with h5py.File(args.out_h5, "w") as f:
        grp = f.require_group(f"{args.network}.{args.station}")
        xml_buf = io.BytesIO()
        inv.write(xml_buf, format="STATIONXML")
        grp.create_dataset("_stationxml_raw", data=np.void(xml_buf.getvalue()))
        for channel, day_list in per_channel_days.items():
            day_list.sort(key=lambda d: d["starttime"])
            sr = day_list[0]["sampling_rate"]
            pieces = [day_list[0]["data"]]
            for prev, cur in zip(day_list, day_list[1:]):
                prev_end = prev["starttime"] + (len(prev["data"]) - 1) / sr
                gap_samples = int(round((cur["starttime"] - prev_end) * sr)) - 1
                if gap_samples > 0:
                    pieces.append(np.zeros(gap_samples, dtype=np.float32))
                pieces.append(cur["data"])
            full = np.concatenate(pieces)
            ds = grp.create_dataset(channel, data=full, maxshape=(None,), chunks=(86400,),
                                     compression="gzip", compression_opts=4)
            ds.attrs["sampling_rate"] = sr
            ds.attrs["start_time"] = str(day_list[0]["starttime"])
            ds.attrs["units"] = "m" if any(d["response_removed"] for d in day_list) else "counts"
            first_azimuth = next((d["azimuth"] for d in day_list if d["azimuth"] is not None), None)
            first_dip = next((d["dip"] for d in day_list if d["dip"] is not None), None)
            if first_azimuth is not None:
                ds.attrs["azimuth"] = first_azimuth
            if first_dip is not None:
                ds.attrs["dip"] = first_dip
            print(f"  {channel}: {len(full):,} samples ({len(full)/sr/86400:.1f} days span)")

    t_done = time.time()
    h5_size = os.path.getsize(args.out_h5)
    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    print(f"  Days found:        {len(day_dirs)}")
    print(f"  Days with data:    {n_ok}")
    print(f"  Total wall-clock:  {t_done - t_start:.1f}s")
    print(f"  Processing rate:   {(t_process_done - t_read) / max(n_ok,1):.2f}s/day-with-data")
    print(f"  Output file:       {args.out_h5} ({h5_size:,} bytes)")


if __name__ == "__main__":
    main()
