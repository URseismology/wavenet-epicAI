#!/usr/bin/env python
"""Every day of the window: measured timing shift between the ORIGINAL master.h5 and the FIXED master_fixed.h5 (BHZ),
versus the shift predicted by timing_replay.py. Both masters come from the same raw data, so the cross-spectrum of a day
is near-perfect apart from the pure time shift: its phase slope over 0.02-0.2 Hz gives the shift.
Output: results/timing_measured_vs_replay.csv
"""
import json, os, sys
import numpy as np
R = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"
sys.path.insert(0, f"{R}/code/rover_download")
from load_master_h5 import load_channel
from obspy import UTCDateTime
plc = json.load(open(f"{R}/results/placement_errors.json"))
rows = []
for sta in ("MTAN", "RUNG"):
    xo, sr, t0o, uo = load_channel(f"{R}/master.h5", "XD", sta, "BHZ")
    xf, sr2, t0f, uf = load_channel(f"{R}/master_fixed.h5", "XD", sta, "BHZ")
    xo, xf = xo.astype(np.float64), xf.astype(np.float64)
    print(f"{sta}: original n={len(xo):,} start={t0o} units={uo!r};  fixed n={len(xf):,} start={t0f} units={uf!r}", flush=True)
    d0 = UTCDateTime(max(t0o, t0f).year, max(t0o, t0f).month, max(t0o, t0f).day) + 86400
    fr = np.fft.rfftfreq(86400, 1.0); m = (fr >= 0.02) & (fr <= 0.2)
    D = d0
    while D + 86400 <= min(t0o + len(xo), t0f + len(xf)):
        io, iff = int(round(D - t0o)), int(round(D - t0f))
        a, b = xo[io:io + 86400], xf[iff:iff + 86400]
        key = D.strftime("%Y%m%d"); rec = plc[sta]["days"].get(key)
        row = dict(station=sta, date=str(D.date), placed_in_original=rec is not None)
        if len(a) == 86400 and len(b) == 86400 and a.any() and b.any():
            Fa, Fb = np.fft.rfft(a), np.fft.rfft(b)
            C = Fa[m] * np.conj(Fb[m]); w = np.abs(C); ph = np.unwrap(np.angle(C))
            s_meas = float(np.sum(w * fr[m] * ph) / np.sum(w * fr[m] ** 2) / (2 * np.pi))
            coh = float(np.abs(np.sum(C)) / np.sqrt(np.sum(np.abs(Fa[m]) ** 2) * np.sum(np.abs(Fb[m]) ** 2)))
            row.update(measured_shift_s=s_meas, band_coherence=coh, rms_ratio=float(np.sqrt(np.mean(a ** 2)) / np.sqrt(np.mean(b ** 2))))
        if rec is not None:
            row["replay_predicted_shift_s"] = rec["start_ts"] + io - rec["idx"] - D.timestamp
        rows.append(row); D += 86400
import pandas as pd
df = pd.DataFrame(rows); df.to_csv(f"{R}/results/timing_measured_vs_replay.csv", index=False)
ok = df.dropna(subset=["measured_shift_s", "replay_predicted_shift_s"]); ok = ok[ok.band_coherence > 0.9]
print(f"days compared: {len(ok)} (band coherence > 0.9); |measured - predicted|: median {np.median(np.abs(ok.measured_shift_s - ok.replay_predicted_shift_s)):.4f} s, "
      f"95th pct {np.percentile(np.abs(ok.measured_shift_s - ok.replay_predicted_shift_s), 95):.4f} s, max {np.abs(ok.measured_shift_s - ok.replay_predicted_shift_s).max():.4f} s")
print(df.groupby("station")[["measured_shift_s"]].describe().round(3).to_string())
print("days present in the fixed master but absent (refused/zero) in the original:", df[(~df.placed_in_original) | df.measured_shift_s.isna()].groupby("station").date.apply(list).to_dict())
