#!/usr/bin/env python
"""
Loads data from the shared master HDF5 file (see docs/ncf_pipeline_stages/hdf5_schema.md)
into numpy arrays. Read-only, no dependency on obspy -- just h5py + numpy, so this is
usable by any downstream consumer (e.g. a cross-correlation script) without needing the
full download/preprocess environment.

Usage as a library:
    from load_master_h5 import list_stations, list_channels, load_channel
    stations = list_stations("master.h5")
    channels = list_channels("master.h5", "G", "SSB")
    data, sampling_rate, start_time = load_channel("master.h5", "G", "SSB", "LHZ")

Usage as a CLI (demo/smoke-test):
    python load_master_h5.py master.h5                    # list all stations+channels
    python load_master_h5.py master.h5 G SSB LHZ           # load and print stats for one channel
"""
import sys

import h5py
import numpy as np
from obspy import UTCDateTime


def list_stations(h5_path):
    """Returns a list of (network, station) tuples present in the master file."""
    with h5py.File(h5_path, "r") as f:
        out = []
        for key in f.keys():
            grp = f[key]
            out.append((grp.attrs.get("network", key.split(".")[0]),
                        grp.attrs.get("station", key.split(".")[-1])))
        return out


def list_channels(h5_path, network, station):
    """Returns the list of channel codes available for one station."""
    with h5py.File(h5_path, "r") as f:
        key = f"{network}.{station}"
        if key not in f:
            raise KeyError(f"{key} not in {h5_path}")
        return list(f[key].keys())


def load_channel(h5_path, network, station, channel):
    """Returns (data: np.ndarray[float32], sampling_rate: float, start_time: UTCDateTime,
    units: str) for one station+channel. Loads the whole array into memory -- fine at
    this project's per-channel scale (hundreds of KB to low MB per channel-week)."""
    with h5py.File(h5_path, "r") as f:
        key = f"{network}.{station}"
        if key not in f or channel not in f[key]:
            raise KeyError(f"{key}/{channel} not in {h5_path}")
        ds = f[key][channel]
        data = ds[()].astype(np.float32)
        sampling_rate = float(ds.attrs["sampling_rate"])
        start_time = UTCDateTime(ds.attrs.get("start_time", ds.attrs.get("starttime")))
        units = ds.attrs.get("units", "unknown")
    return data, sampling_rate, start_time, units


def load_channel_range(h5_path, network, station, channel, start, end):
    """Returns (data, sampling_rate, actual_start_time, units) for just the
    [start, end) time window of one channel -- an O(1) index-math slice, not a scan,
    because the schema guarantees each channel's dataset is continuous (gaps were
    zero-filled at preprocessing time, see docs/ncf_pipeline_stages/hdf5_schema.md).
    `start`/`end` accept anything obspy.UTCDateTime does (ISO string, UTCDateTime, ...).
    Clips to the dataset's actual bounds if the requested window extends past either
    end, rather than raising. **A query entirely outside the dataset's range (before
    its start or after its end) returns an empty array (`len(data) == 0`), not
    `None`** -- check `len(data)` (or `data.size`), don't compare against `None`.
    Verified for both directions (2026-09-23): a day before the dataset's start and a
    day after its end both correctly return `len == 0`."""
    start, end = UTCDateTime(start), UTCDateTime(end)
    with h5py.File(h5_path, "r") as f:
        key = f"{network}.{station}"
        if key not in f or channel not in f[key]:
            raise KeyError(f"{key}/{channel} not in {h5_path}")
        ds = f[key][channel]
        sampling_rate = float(ds.attrs["sampling_rate"])
        ds_start = UTCDateTime(ds.attrs.get("start_time", ds.attrs.get("starttime")))
        n_total = ds.shape[0]

        i0 = max(0, int(round((start - ds_start) * sampling_rate)))
        i1 = min(n_total, int(round((end - ds_start) * sampling_rate)))
        if i1 <= i0:
            return np.array([], dtype=np.float32), sampling_rate, start, ds.attrs.get("units", "unknown")

        data = ds[i0:i1].astype(np.float32)
        actual_start = ds_start + i0 / sampling_rate
        units = ds.attrs.get("units", "unknown")
    return data, sampling_rate, actual_start, units


def load_window(h5_path, network, station, channel, start, end):
    """[SCHEMA v2] Load an absolute time window, WITH a coverage mask.

    Returns (data, mask, sampling_rate, actual_start, units). `mask[i]` is True where the
    sample is real recorded data and False where nothing was ever written.

    Why the mask is not optional: on the absolute grid a channel's dataset spans from the
    shared epoch, so the vast majority of it is unwritten and reads back as 0.0. Zero is
    indistinguishable from genuinely quiet ground motion, and correlating never-recorded
    span as if it were quiet data biases an NCF silently. Coverage is tracked per day in
    the "_coverage" group precisely so this question has an answer.

    The slice itself is O(1) index math on a grid shared by every station, so the SAME
    (i0, i1) addresses the same absolute window at any other station -- which is what makes
    pairwise correlation loads alignment-free.
    """
    start, end = UTCDateTime(start), UTCDateTime(end)
    with h5py.File(h5_path, "r") as f:
        key = f"{network}.{station}"
        if key not in f or channel not in f[key]:
            raise KeyError(f"{key}/{channel} not in {h5_path}")
        grp = f[key]
        ds = grp[channel]
        sampling_rate = float(ds.attrs["sampling_rate"])
        epoch = UTCDateTime(ds.attrs.get("epoch", ds.attrs.get("start_time")))
        n_total = ds.shape[0]

        i0 = max(0, int(round((start - epoch) * sampling_rate)))
        i1 = min(n_total, int(round((end - epoch) * sampling_rate)))
        units = ds.attrs.get("units", "unknown")
        if i1 <= i0:
            empty = np.array([], dtype=np.float32)
            return empty, empty.astype(bool), sampling_rate, start, units

        data = ds[i0:i1].astype(np.float32)
        mask = np.zeros(len(data), dtype=bool)
        cov = grp.get("_coverage")
        if cov is not None and channel in cov:
            cd = cov[channel][()]
            day_len = int(86400 * sampling_rate)
            idx = np.arange(i0, i1)
            days = idx // day_len
            valid = days < len(cd)
            mask[valid] = cd[days[valid]].astype(bool)
        else:
            # v1 shard (no coverage group): everything stored was contiguous by
            # construction, so treat the whole slice as real.
            mask[:] = True
        actual_start = epoch + i0 / sampling_rate
    return data, mask, sampling_rate, actual_start, units


def load_station(h5_path, network, station):
    """Returns {channel: (data, sampling_rate, start_time, units)} for every channel of
    one station -- convenience wrapper over load_channel for cross-correlation use,
    where you typically want all components at once."""
    return {ch: load_channel(h5_path, network, station, ch)
            for ch in list_channels(h5_path, network, station)}


def _demo_main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    h5_path = sys.argv[1]
    if len(sys.argv) == 2:
        print(f"Stations in {h5_path}:")
        for net, sta in list_stations(h5_path):
            channels = list_channels(h5_path, net, sta)
            print(f"  {net}.{sta}: {channels}")
        return

    network, station, channel = sys.argv[2], sys.argv[3], sys.argv[4]
    data, sampling_rate, start_time, units = load_channel(h5_path, network, station, channel)
    print(f"{network}.{station}/{channel}: {len(data):,} samples @ {sampling_rate} Hz, "
          f"start={start_time}, units={units}")
    print(f"  min={data.min():.6g} max={data.max():.6g} mean={data.mean():.6g} "
          f"std={data.std():.6g} nan_count={int(np.isnan(data).sum())}")


if __name__ == "__main__":
    _demo_main()
