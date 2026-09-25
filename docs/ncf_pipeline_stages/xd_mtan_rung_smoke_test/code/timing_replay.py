#!/usr/bin/env python
"""Replay build_master_h5.append_channel_data's placement arithmetic from the RAW miniSEED headers to find,
for every processed day, where the orchestrator put that day's first sample on the per-station 1 Hz grid and
how far (seconds) that grid position is from the sample's true acquisition time.

  e_k = (t0 + idx_k) - start_k      [grid time assigned to day k's first sample  -  its true time]

Replay rule (verbatim from append_channel_data):
    delta = start_k - (t0 + (cur_len - 1));  gap = max(round(delta) - 1, 0);  idx_k = cur_len + gap
    REFUSED (day dropped) if start_k <= existing_end.
Validation: the replayed idx_k must reproduce the four positions measured directly against the packaged file
(diag_alignment.py): RUNG 19940526 -> +1 sample past nearest, RUNG 19940530 -> +1, MTAN 19940526 -> 0, MTAN 19940530 -> 0.
Usage: timing_replay.py <out.json>
"""
import glob, json, math, os, sys
import numpy as np
from obspy import read, UTCDateTime

R = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"
out = {}
for sta in ("MTAN", "RUNG"):
    files = sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*BHZ__*T*"))
    days = []
    for f in files:
        st = read(f, headonly=True)
        start = min(t.stats.starttime for t in st); end = max(t.stats.endtime for t in st)
        fs = st[0].stats.sampling_rate
        n_raw = int(round((end - start) * fs)) + 1
        days.append((os.path.basename(f).split("__")[1][:8], start, math.ceil(n_raw / int(round(fs))), len(st)))
    days.sort()
    t0 = UTCDateTime(str(days[0][1]))            # dataset start attr is str(UTCDateTime) -> microsecond precision
    t0_ts = t0.timestamp
    cur_len, rec, refused = 0, {}, []
    for date, start, n_dec, ntr in days:
        s_ts = UTCDateTime(str(start)).timestamp
        if cur_len == 0:
            idx = 0
        else:
            existing_end = t0_ts + (cur_len - 1)
            if s_ts <= existing_end:
                refused.append(date); continue
            delta = s_ts - existing_end
            idx = cur_len + max(int(round(delta)) - 1, 0)
        rec[date] = dict(idx=idx, n=n_dec, start_ts=s_ts, e=(t0_ts + idx) - s_ts, n_segments=ntr)
        cur_len = idx + n_dec
    out[sta] = dict(t0=str(t0), t0_ts=t0_ts, final_len=cur_len, days=rec, refused=refused)
    es = np.array([v["e"] for v in rec.values()])
    print(f"{sta}: {len(rec)} days placed, {len(refused)} refused; final grid length {cur_len:,}; "
          f"placement error e = grid_time - true_time: min {es.min():+.3f}, median {np.median(es):+.3f}, "
          f"max {es.max():+.3f} s", flush=True)

# validation against the four measured positions
chk = {("RUNG", "19940526"): 52423 + 1, ("RUNG", "19940530"): 398023 + 1,
       ("MTAN", "19940526"): 23048, ("MTAN", "19940530"): 368648}
ok = True
for (sta, d), want in chk.items():
    got = out[sta]["days"].get(d, {}).get("idx")
    print(f"validate {sta} {d}: replay idx={got} expected={want}  {'OK' if got == want else 'MISMATCH'}", flush=True)
    ok &= (got == want)
print("REPLAY VALIDATION:", "PASS" if ok else "FAIL")
json.dump(out, open(sys.argv[1], "w"))
rel = {d: out["RUNG"]["days"][d]["e"] - out["MTAN"]["days"][d]["e"]
       for d in out["RUNG"]["days"] if d in out["MTAN"]["days"]}
rr = np.array(list(rel.values()))
print(f"relative placement error RUNG-MTAN over {len(rr)} common days: min {rr.min():+.3f}, median {np.median(rr):+.3f}, "
      f"max {rr.max():+.3f} s")
