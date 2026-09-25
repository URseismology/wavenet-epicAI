#!/usr/bin/env python
"""Where do the outlier days come from: RAW data, PROCESSING (orchestrator steps), or PACKAGING?

For each (station, day) it (a) reads the raw BHZ miniSEED for that UTC day and reports raw-count statistics
(gaps, spikes, robust vs plain spread), (b) replays the orchestrator's exact processing steps one at a time
and prints RMS/max after each step, (c) replays the same steps WITHOUT response removal (counts space) to see
whether the blow-up needs the deconvolution, and (d) compares the freshly-processed day against what is
stored in packaged_h5/ to test packaging fidelity.
"""
import glob, json, os, sys
import h5py
import numpy as np
from obspy import UTCDateTime, read, read_inventory

R = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"
CASES = [("MTAN", d) for d in ("19940526", "19940528", "19940530")] + \
        [("RUNG", d) for d in ("19940526", "19940529", "19940530")]


def stats(x):
    x = np.asarray(x, dtype=np.float64)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 + 1e-30
    return dict(rms=float(np.sqrt(np.mean(x ** 2))), maxabs=float(np.max(np.abs(x))),
                robust_sigma=float(mad), n_gt_10sigma=int((np.abs(x - med) > 10 * mad).sum()))


def fmt(tag, d):
    return f"    {tag:<34} rms={d['rms']:.3e}  max|x|={d['maxabs']:.3e}  robust_sigma={d['robust_sigma']:.3e}  n>10sigma={d['n_gt_10sigma']}"


def process(tr, inv, do_response):
    """Orchestrator's per-day steps, verbatim, with a stats snapshot after each."""
    out = []
    tr.detrend("linear"); tr.detrend("demean"); out.append(("after detrend", stats(tr.data)))
    if do_response:
        tr.remove_response(inventory=inv, output="DISP", water_level=60, pre_filt=(0.001, 0.005, 0.4, 0.5))
        out.append(("after remove_response(DISP)", stats(tr.data)))
    tr.filter("lowpass", freq=0.4, corners=4, zerophase=True); out.append(("after lowpass 0.4 Hz", stats(tr.data)))
    tr.filter("highpass", freq=1.0 / 3600.0, corners=4, zerophase=True); out.append(("after highpass 1/3600 Hz", stats(tr.data)))
    tr.decimate(factor=int(round(tr.stats.sampling_rate)), no_filter=True)
    tr.detrend("demean"); tr.taper(max_percentage=0.05); out.append(("after decimate+demean+taper", stats(tr.data)))
    return tr, out


for sta, day in CASES:
    print(f"\n===== XD.{sta} BHZ  {day} =====", flush=True)
    files = sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*BHZ__{day}T*"))
    if not files:
        print("  no raw BHZ file for this day"); continue
    st = read(files[0])
    print(f"  raw file: {os.path.basename(files[0])}; traces={len(st)}; "
          f"segments: {[(str(t.stats.starttime.time), t.stats.npts) for t in st][:6]}", flush=True)
    inv = read_inventory(f"{R}/scratch_work/XD_{sta}/stationxml/XD.{sta}.xml")
    st.merge(fill_value=0)
    tr0 = st[0]
    print(fmt("RAW counts", stats(tr0.data)), flush=True)
    # (b) full orchestrator replay
    tr, steps = process(tr0.copy(), inv, do_response=True)
    print("  -- orchestrator replay (with response removal):")
    for tag, d in steps:
        print(fmt(tag, d), flush=True)
    # (c) counts-space replay, no response removal
    tr_c, steps_c = process(tr0.copy(), inv, do_response=False)
    print("  -- same steps WITHOUT response removal (counts space):")
    print(fmt(steps_c[-1][0], steps_c[-1][1]), flush=True)
    # (d) packaging fidelity: packaged h5 slice for this day vs the fresh replay
    with h5py.File(f"{R}/packaged_h5/XD.{sta}.h5", "r") as f:
        ds = f[f"XD.{sta}"]["BHZ"]
        t0 = UTCDateTime(ds.attrs["start_time"])
        i0 = int(round((tr.stats.starttime - t0) * ds.attrs["sampling_rate"]))
        pk = ds[i0:i0 + len(tr.data)].astype(np.float64)
    fresh = tr.data.astype(np.float32).astype(np.float64)
    n = min(len(pk), len(fresh))
    same = np.array_equal(pk[:n], fresh[:n])
    rel = float(np.max(np.abs(pk[:n] - fresh[:n])) / (np.max(np.abs(fresh[:n])) + 1e-30))
    print(f"  -- packaging check: packaged slice len={len(pk)} vs fresh {len(fresh)}; identical={same}; "
          f"max|diff|/max|fresh|={rel:.2e}; packaged rms={np.sqrt(np.mean(pk[:n]**2)):.3e}", flush=True)
print("\nDONE")
