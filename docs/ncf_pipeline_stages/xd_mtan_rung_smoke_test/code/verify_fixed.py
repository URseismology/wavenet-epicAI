"""Verify the FIXED orchestrator's output against the ORIGINAL packaged data and against the replay's predictions.
Usage: verify_fixed.py <fixed_root> <orig_packaged_dir_snapshot> <placement_errors.json>
"""
import json, os, sys
import h5py, numpy as np
from obspy import UTCDateTime
fixed_root, orig_dir, plc_json = sys.argv[1:4]
plc = json.load(open(plc_json))
DAYS = ["1994-05-26", "1994-05-27"]                    # quiet days present in both
for sta in ("MTAN", "RUNG"):
    print(f"\n===== XD.{sta} =====")
    fp = f"{fixed_root}/packaged_h5/XD.{sta}.h5"
    with h5py.File(fp, "r") as f:
        g = f[f"XD.{sta}"]; print("channels:", [k for k in g.keys() if not k.startswith("_")])
        ds = g["BHZ"]; t0f = UTCDateTime(ds.attrs["start_time"]); xf_all = ds[()].astype(np.float64)
        print(f"BHZ fixed: n={len(ds):,}  start_time={t0f}  integer second: {t0f.timestamp % 1 == 0}  units={ds.attrs['units']!r}  sr={ds.attrs['sampling_rate']}")
    qc = json.load(open(fp + ".qc.json"))
    for day, chans in sorted(qc.items()):
        print(f"  QC {day}: " + "; ".join(f"{c}: flag={v['flag']} rms/robust={v['rms_over_robust_sigma']:.1f} sat={v['saturated']} segs={v['n_segments']}" for c, v in chans.items() if c == "BHZ"))
    with h5py.File(f"{orig_dir}/XD.{sta}.h5", "r") as f:
        ds = f[f"XD.{sta}"]["BHZ"]; t0o = UTCDateTime(ds.attrs["start_time"]); xo_all = ds[()].astype(np.float64)
    for d in DAYS:
        D = UTCDateTime(d)
        io = int(round(D - t0o)); iff = int(round(D - t0f))
        xo, xf = xo_all[io:io + 86400], xf_all[iff:iff + 86400]
        if len(xo) < 86400 or len(xf) < 86400: print(f"  {d}: slice too short"); continue
        xf_nm = xf * 1e9 if abs(xf).max() < 1e-2 else xf          # fixed data are metres; original nanometres
        print(f"  {d}: RMS orig = {np.sqrt(np.mean(xo**2)):.3g} nm-labelled-m ; RMS fixed = {np.sqrt(np.mean(xf**2)):.3g} m  (ratio {np.sqrt(np.mean(xo**2))/np.sqrt(np.mean(xf**2)):.3g})")
        Fo, Ff = np.fft.rfft(xo), np.fft.rfft(xf_nm); fr = np.fft.rfftfreq(86400, 1.0)
        m = (fr >= 0.02) & (fr <= 0.2)
        C = Fo[m] * np.conj(Ff[m]); w = np.abs(C); ph = np.unwrap(np.angle(C))
        s_est = np.sum(w * fr[m] * ph) / np.sum(w * fr[m] ** 2) / (2 * np.pi)          # seconds
        rec = plc[sta]["days"][d.replace("-", "")]
        s_rep = rec["start_ts"] + io - rec["idx"] - D.timestamp
        coh = np.abs(np.sum(C)) / np.sqrt(np.sum(np.abs(Fo[m]) ** 2) * np.sum(np.abs(Ff[m]) ** 2))
        print(f"      measured shift between orig-slice and fixed-slice = {s_est:+.3f} s   (replay predicted {s_rep:+.3f} s; band coherence {coh:.3f})")
