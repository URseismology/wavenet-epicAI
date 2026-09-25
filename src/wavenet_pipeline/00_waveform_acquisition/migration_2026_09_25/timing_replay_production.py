#!/usr/bin/env python
"""Bank the exact timing correction for PRE-PATCH stations, per channel, from raw miniSEED
HEADERS ONLY (no waveform decode) -- before inspector purges that raw SEED and the
correction becomes unrecoverable.

Generalised from docs/ncf_pipeline_stages/xd_mtan_rung_smoke_test/code/timing_replay.py,
which was hard-coded to the XD pair and BHZ. Validated there against four directly-measured
positions and, across 310 station-days, against the measured pre-vs-post-patch shift to a
median of 0.0 ms.

Why this is needed at all: pre-patch, each channel's 1 Hz grid is anchored at that
channel's FIRST stored sample, which begins mid-day at an arbitrary sub-second phase.
Decimation inherits the raw record's phase (k x 0.05 s for 20 Hz data), and
append_channel_data places each later day with max(round(gap) - 1, 0), which snaps it onto
the anchor's grid and can only ever delay. So the placement error is piecewise constant at
k x 0.05 s, re-randomising after any real gap >= 1.5 s. That piecewise structure is NOT
recoverable from the packaged array alone -- gaps were zero-filled and are indistinguishable
from quiet data -- so it has to be replayed from raw headers while they still exist.

Replays append_channel_data's arithmetic verbatim:
    existing_end = t0 + (cur_len - 1);  REFUSED if start <= existing_end
    idx = cur_len + max(round(start - existing_end) - 1, 0);  e = (t0 + idx) - start

Emits per station, per channel: t0, per-day {idx, e, n, n_segments}, the days REFUSED by
the pre-patch overlap bug (silently dropped -- also only visible from raw), and summary
stats. A downstream consumer applies -e to realign day k, and knows which days are missing.

Usage: timing_replay_production.py <prod_root> <out_dir> <station_list> <task_idx>
       station_list: one "network,station" per line
"""
import glob
import json
import math
import os
import sys

import numpy as np
from obspy import read, UTCDateTime

prod_root, out_dir, list_path, task_idx = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
task_idx += int(os.environ.get("WAVENET_IDX_OFFSET", 0))

rows = [l.strip().split(",") for l in open(list_path) if l.strip()]
if task_idx >= len(rows):
    print(f"[replay] idx {task_idx} beyond station list ({len(rows)}) -- nothing to do")
    sys.exit(0)
network, station = rows[task_idx]
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, f"{network}.{station}.json")

mseed_dir = os.path.join(prod_root, "scratch_work", f"{network}_{station}", "mseed")
result = dict(network=network, station=station, mseed_dir=mseed_dir)

if not os.path.isdir(mseed_dir) or not os.listdir(mseed_dir):
    # Raw already purged by the inspector (or never kept): the exact correction for this
    # station is gone. Recorded explicitly rather than silently omitted -- this is the
    # count of stations we can no longer correct exactly.
    result.update(raw_missing=True, channels={})
    with open(out_path, "w") as f:
        json.dump(result, f)
    print(f"[replay] {network}.{station}: RAW MISSING (already purged) -- cannot bank correction")
    sys.exit(0)

# group day files by channel: NET.STA.LOC.CHAN__STARTISO__ENDISO.mseed
by_channel = {}
for p in glob.glob(os.path.join(mseed_dir, "*")):
    base = os.path.basename(p)
    parts = base.split("__")
    if len(parts) < 2:
        continue
    ids = parts[0].split(".")
    if len(ids) < 4:
        continue
    by_channel.setdefault(ids[3], []).append((parts[1][:8], p))

channels = {}
for chan, day_files in sorted(by_channel.items()):
    days = []
    for date, p in day_files:
        try:
            st = read(p, headonly=True)
        except Exception as e:
            print(f"[replay] {network}.{station}/{chan} {date}: unreadable ({e}) -- skipped")
            continue
        start = min(t.stats.starttime for t in st)
        end = max(t.stats.endtime for t in st)
        fs = st[0].stats.sampling_rate
        n_raw = int(round((end - start) * fs)) + 1
        # decimate(factor=round(fs)) when fs >= 2, else untouched -- matches orchestrator.py
        factor = int(round(fs)) if fs >= 2 else 1
        days.append((date, start, math.ceil(n_raw / factor), len(st), fs))
    if not days:
        continue
    days.sort()
    t0_ts = UTCDateTime(str(days[0][1])).timestamp
    sr = 1.0 if days[0][4] >= 2 else days[0][4]      # post-decimation rate
    cur_len, rec, refused = 0, {}, []
    for date, start, n_dec, ntr, _fs in days:
        s_ts = UTCDateTime(str(start)).timestamp
        if cur_len == 0:
            idx = 0
        else:
            existing_end = t0_ts + (cur_len - 1) / sr
            if s_ts <= existing_end:
                refused.append(date)
                continue
            idx = cur_len + max(int(round((s_ts - existing_end) * sr)) - 1, 0)
        rec[date] = dict(idx=idx, n=n_dec, e=round((t0_ts + idx / sr) - s_ts, 6),
                          n_segments=ntr)
        cur_len = idx + n_dec
    es = np.array([v["e"] for v in rec.values()]) if rec else np.array([0.0])
    channels[chan] = dict(t0=str(UTCDateTime(t0_ts)), t0_ts=t0_ts, sampling_rate=sr,
                           final_len=cur_len, n_days_placed=len(rec),
                           n_days_refused=len(refused), refused_days=refused,
                           e_min=float(es.min()), e_median=float(np.median(es)),
                           e_max=float(es.max()), days=rec)
    print(f"[replay] {network}.{station}/{chan}: {len(rec)} days, {len(refused)} refused, "
          f"e median {np.median(es):+.3f}s (min {es.min():+.3f}, max {es.max():+.3f})")

result.update(raw_missing=False, channels=channels,
              n_channels=len(channels),
              n_days_refused_total=sum(c["n_days_refused"] for c in channels.values()))
tmp = out_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(result, f)
os.replace(tmp, out_path)
print(f"[replay] {network}.{station}: banked {len(channels)} channel(s) -> {out_path}")
