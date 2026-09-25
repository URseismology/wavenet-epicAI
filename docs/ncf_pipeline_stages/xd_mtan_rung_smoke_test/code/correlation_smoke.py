#!/usr/bin/env python
"""FastMSPEC correlation smoke test for XD.MTAN / XD.RUNG BHZ, read back through the pipeline's own
load_master_h5.py and benchmarked against ADAMA's Rayleigh (ZZ) phase-velocity product for the same pair
(ADAMAraw_co_ral.h5, key XD.RUNG-XD.MTAN).

Usage: correlation_smoke.py <master.h5> <out_dir> [--adama-dir DIR] [--wband 0.001] [--timing-json placement_errors.json]

Conventions follow FastMSPEC's batch pipeline: 3-hour windows (10801 samples @ 1 Hz), 50 % overlap, 15 per UTC day,
start offset 50 s, last window clamped to end of day, all-zero days skipped; FastMspec at Wband=0.001 (NW ~ 10.8)
with no extra detrend/taper; single-taper = detrend + 5 % cosine taper + plain FFT.

Four FastMspec variants are scored against ADAMA (same windows/bandwidth):
  as_packaged                      -- the packaged data exactly as read back (the smoke test proper)
  excl_outlier_days                -- days with RMS > 20x the station median at either station removed
  timing_corrected                 -- each day's slice fractionally re-timed to true UTC using the replayed placement error
  timing_corrected_excl_outliers   -- both
"""
import argparse, glob, json, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "rover_download"))
sys.path.insert(0, os.path.join(HERE, "fastmspec_python"))

from load_master_h5 import list_stations, list_channels, load_channel  # noqa: E402
from ccf_pipeline import preprocessing as pp  # noqa: E402
from ccf_pipeline.crosscorr_mtc import compute_crosscorr_mtc_fastmspec  # noqa: E402
from obspy import UTCDateTime  # noqa: E402
from scipy.special import j0, jn_zeros  # noqa: E402
from scipy.signal import hilbert  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("master"); ap.add_argument("out_dir")
ap.add_argument("--adama-dir", default="/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/results/adama_reference")
ap.add_argument("--wband", type=float, default=0.001)
ap.add_argument("--timing-json", default=None)
args = ap.parse_args()
os.makedirs(args.out_dir, exist_ok=True)
prov = {"master": args.master, "wband": args.wband, "started": str(UTCDateTime.now())}
OUT = lambda name: os.path.join(args.out_dir, name)  # noqa: E731

# ------------------------------------------------------------------ load back through the tool's own reader
print("stations in master:", list_stations(args.master), flush=True)
for sta in ("MTAN", "RUNG"):
    print(f"  XD.{sta} channels:", list_channels(args.master, "XD", sta), flush=True)
z = {}
for sta in ("RUNG", "MTAN"):          # ADAMA pair order: station1 = RUNG, station2 = MTAN
    data, sr, t0, units = load_channel(args.master, "XD", sta, "BHZ")
    z[sta] = dict(data=data.astype(np.float64), sr=sr, t0=t0, units=units)
    t1 = t0 + (len(data) - 1) / sr
    print(f"  XD.{sta}/BHZ: {len(data):,} samples @ {sr} Hz, {t0} -> {t1}, units={units}", flush=True)
    prov[f"XD.{sta}.BHZ"] = dict(n=len(data), sr=sr, start=str(t0), end=str(t1), units=units)
assert z["RUNG"]["sr"] == z["MTAN"]["sr"] == 1.0

end_of = lambda s: s["t0"] + (len(s["data"]) - 1) / s["sr"]  # noqa: E731
c_start, c_end = max(z["RUNG"]["t0"], z["MTAN"]["t0"]), min(end_of(z["RUNG"]), end_of(z["MTAN"]))
print(f"common range: {c_start} -> {c_end}  ({(c_end - c_start) / 86400:.2f} days)", flush=True)
prov["common_range"] = dict(start=str(c_start), end=str(c_end), days=(c_end - c_start) / 86400)

# ------------------------------------------------------------------ day slices, windows, daily diagnostics
NWIN, WLEN, WCORE, NSTART = 15, 10801, 10800, 50
def idx0(sta, d0): return int(round((d0 - z[sta]["t0"]) * z[sta]["sr"]))
def day_slice(sta, d0):
    i0 = idx0(sta, d0)
    return None if (i0 < 0 or i0 + 86400 > len(z[sta]["data"])) else z[sta]["data"][i0:i0 + 86400]
def windows_from_day(x):
    w = np.zeros((NWIN, WLEN))
    for iw in range(NWIN):
        p0 = int(WCORE * 0.5 * iw) + NSTART; p1 = p0 + WLEN
        if p1 > 86400: p0, p1 = 86400 - WLEN, 86400
        w[iw] = x[p0:p1]
    return w

first_day = UTCDateTime(c_start.year, c_start.month, c_start.day)
if first_day < c_start: first_day += 86400
n_cal = int((c_end - first_day) // 86400)
days_used, day_arr, day_table = [], {}, []
for k in range(n_cal):
    d0 = first_day + k * 86400
    a, b = day_slice("RUNG", d0), day_slice("MTAN", d0)
    if a is None or b is None: continue
    day_table.append((str(d0.date), float((a != 0).mean()), float((b != 0).mean()),
                      float(np.sqrt(np.mean(a ** 2))), float(np.sqrt(np.mean(b ** 2)))))
    if not a.any() or not b.any(): continue
    days_used.append(str(d0.date)); day_arr[str(d0.date)] = (a, b)
nz = np.array([(x[1], x[2]) for x in day_table])
print(f"full UTC days in common range: {len(day_table)}; usable: {len(days_used)}; "
      f">99% nonzero both: {int(((nz > 0.99).all(axis=1)).sum())}; ({days_used[0]} .. {days_used[-1]})", flush=True)
prov["days"] = dict(full_days_in_range=len(day_table), usable=len(days_used), first=days_used[0], last=days_used[-1],
                    n_windows=len(days_used) * NWIN,
                    dates_with_all_zero_data=[x[0] for x in day_table if x[0] not in days_used])
np.savetxt(OUT("daily_coverage.csv"), np.array([tuple(x) for x in day_table], dtype=object), fmt="%s", delimiter=",",
           header="date,nonzero_fraction_RUNG_BHZ,nonzero_fraction_MTAN_BHZ,rms_RUNG_BHZ,rms_MTAN_BHZ", comments="")
rms = np.array([(x[3], x[4]) for x in day_table]); med = np.median(rms, axis=0)
prov["daily_rms"] = {"median_RUNG": float(med[0]), "median_MTAN": float(med[1]),
                     "max_over_median_RUNG": float(rms[:, 0].max() / med[0]), "max_over_median_MTAN": float(rms[:, 1].max() / med[1]),
                     "days_gt_20x_median_RUNG": int((rms[:, 0] > 20 * med[0]).sum()),
                     "days_gt_20x_median_MTAN": int((rms[:, 1] > 20 * med[1]).sum()),
                     "units_attr": {k: z[k]["units"] for k in z}}
print("daily RMS:", prov["daily_rms"], flush=True)
rms_by_date = {x[0]: (x[3], x[4]) for x in day_table}
keep = np.array([(rms_by_date[d][0] <= 20 * med[0]) and (rms_by_date[d][1] <= 20 * med[1]) for d in days_used])
prov["outlier_days_excluded"] = [d for d, k in zip(days_used, keep) if not k]
print(f"outlier-day mask: keeping {int(keep.sum())} of {len(keep)} days; excluded {prov['outlier_days_excluded']}", flush=True)

hour_rms = {}
for sta in ("RUNG", "MTAN"):
    rows = [np.sqrt((day_arr[d][0 if sta == "RUNG" else 1].reshape(24, 3600) ** 2).mean(axis=1)) for d in days_used]
    hour_rms[sta] = (np.median(np.array(rows), axis=0) / np.median(np.array(rows))).round(3).tolist()
prov["hour_of_day_rms_profile"] = hour_rms

eps = {k: float((z[k]["t0"] + idx0(k, first_day)) - first_day) for k in z}
prov["sample_grid_offset_s_vs_midnight"] = eps
print(f"sample-grid offsets vs UTC midnight: {eps}; relative MTAN-RUNG = {eps['MTAN'] - eps['RUNG']:+.3f} s", flush=True)

# ------------------------------------------------------------------ per-day timing correction (from timing_replay.py)
def shift_day(x, s):
    """y[j] = x_continuous(j - s): delay by s samples via a frequency-domain linear phase (band-limited, tapered ends)."""
    F = np.fft.rfft(x); fr = np.fft.rfftfreq(len(x), 1.0)
    return np.fft.irfft(F * np.exp(-2j * np.pi * fr * s), n=len(x))

s_off = None
if args.timing_json:
    tj = json.load(open(args.timing_json))
    s_off = {}
    for d in days_used:
        key = d.replace("-", ""); d0 = UTCDateTime(d)
        rec = {sta: tj[sta]["days"].get(key) for sta in ("RUNG", "MTAN")}
        if rec["RUNG"] is None or rec["MTAN"] is None: continue
        # true time of the slice's first stored sample relative to UTC midnight:  s = start_k + i0 - idx_k - D
        s_off[d] = {sta: rec[sta]["start_ts"] + idx0(sta, d0) - rec[sta]["idx"] - d0.timestamp for sta in rec}
    sv = np.array([[v["RUNG"], v["MTAN"]] for v in s_off.values()]); rel = sv[:, 0] - sv[:, 1]
    prov["timing_offsets_s"] = {"n_days": len(s_off),
        "RUNG_slice_true_start_minus_midnight_min_med_max": [float(sv[:, 0].min()), float(np.median(sv[:, 0])), float(sv[:, 0].max())],
        "MTAN_slice_true_start_minus_midnight_min_med_max": [float(sv[:, 1].min()), float(np.median(sv[:, 1])), float(sv[:, 1].max())],
        "relative_RUNG_minus_MTAN_min_med_max": [float(rel.min()), float(np.median(rel)), float(rel.max())],
        "refused_days": {sta: tj[sta]["refused"] for sta in ("RUNG", "MTAN")}}
    print("timing offsets:", json.dumps(prov["timing_offsets_s"]), flush=True)

# ------------------------------------------------------------------ ADAMA Rayleigh benchmark setup
ref_file = glob.glob(os.path.join(args.adama_dir, "ADAMAraw_co_ral.h5__XD.RUNG-XD.MTAN__*.npy"))
assert len(ref_file) == 1, f"ADAMA co_ral reference not found in {args.adama_dir}"
adama_raw = np.load(ref_file[0])
summ = json.load(open(os.path.join(args.adama_dir, "adama_pair_summary.json")))
attrs = next(iter(summ["ADAMAraw_co_ral.h5"]["leaves"].values()))["attrs"]
fo, fend, Na, R_KM = attrs["fo"], attrs["fend"], attrs["N"], attrs["distance"]
fa = np.linspace(fo, fend, Na); good = adama_raw != 0
fa_g, c_g = fa[good], adama_raw[good] / 1000.0
f_lo, f_hi = float(fa_g.min()), float(fa_g.max())
print(f"ADAMA co_ral (ZZ, Rayleigh): {good.sum()} nonzero samples, band {f_lo:.4f}-{f_hi:.4f} Hz "
      f"(T {1 / f_hi:.1f}-{1 / f_lo:.1f} s), c {c_g.min():.2f}-{c_g.max():.2f} km/s, R={R_KM:.2f} km", flush=True)
prov["adama"] = dict(file=os.path.basename(ref_file[0]), band_hz=[f_lo, f_hi],
                     contiguous=bool(np.all(np.diff(np.where(good)[0]) == 1)),
                     c_range_km_s=[float(c_g.min()), float(c_g.max())], distance_km=R_KM)

N = WLEN
faxis = np.fft.fftfreq(N, d=1.0); pos = faxis > 0; f = faxis[pos]
band = (f >= f_lo) & (f <= f_hi); fb = f[band]
c_of_f = np.interp(fb, fa_g, c_g)
pred = j0(2 * np.pi * fb * R_KM / c_of_f)                     # Aki (1957) vertical-component coherency

def corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
def crossings(freqs, y):
    i = np.where(np.diff(np.sign(y)) != 0)[0]
    return freqs[i] - y[i] * (freqs[i + 1] - freqs[i]) / (y[i + 1] - y[i])
f_pred_x = crossings(fb, pred); ZEROS = jn_zeros(0, 80)

def score(coh_band):
    obs_x = crossings(fb, coh_band); rows = []
    for fp in f_pred_x:
        c_loc = np.interp(fp, fb, c_of_f); spacing = c_loc / (2 * R_KM)
        if len(obs_x) == 0: rows.append((fp, np.nan, np.nan, np.nan)); continue
        fo_ = obs_x[np.argmin(np.abs(obs_x - fp))]
        n = int(np.argmin(np.abs(ZEROS - 2 * np.pi * fp * R_KM / c_loc)))
        c_imp = 2 * np.pi * fo_ * R_KM / ZEROS[n]; c_ad = np.interp(fo_, fb, c_of_f)
        rows.append((fp, fo_, (fo_ - fp) / spacing, 100 * (c_imp - c_ad) / c_ad))
    arr = np.array(rows); ok = np.abs(arr[:, 2]) < 0.25
    near = (np.array([np.min(np.abs(f_pred_x - o)) for o in obs_x]) / (np.interp(obs_x, fb, c_of_f) / (2 * R_KM)) < 0.25
            if len(obs_x) else np.array([]))
    return arr, {"corr_vs_adama_bessel": corr(coh_band, pred), "n_predicted_crossings": int(len(f_pred_x)),
                 "n_observed_crossings": int(len(obs_x)), "recall_within_quarter_spacing": int(ok.sum()),
                 "precision_observed_near_prediction": float(near.mean()) if len(near) else float("nan"),
                 "median_signed_velocity_diff_pct": float(np.nanmedian(arr[:, 3])),
                 "median_abs_velocity_diff_pct": float(np.nanmedian(np.abs(arr[:, 3]))),
                 "median_abs_offset_in_spacings": float(np.nanmedian(np.abs(arr[:, 2]))),
                 "p95_abs_coherence_in_band": float(np.percentile(np.abs(coh_band), 95))}

LAGS = np.arange(N) - N // 2
def ncf_stats(coh_c):
    spec = np.where((np.abs(faxis) >= 0.03) & (np.abs(faxis) <= 0.2), coh_c, 0)
    ncf = np.fft.fftshift(np.fft.ifft(spec).real); env = np.abs(hilbert(ncf))
    win = (np.abs(LAGS) >= 10) & (np.abs(LAGS) <= 200)
    pm, nm = win & (LAGS > 0), win & (LAGS < 0)
    def refine(mask):                                           # parabolic sub-sample refinement of the envelope peak
        i = int(np.where(mask)[0][np.argmax(env[mask])]); y0, y1, y2 = env[i - 1], env[i], env[i + 1]
        return float(LAGS[i] + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2))
    pk_p, pk_n = refine(pm), refine(nm)
    return ncf, env, {"peak_lag_causal_s": pk_p, "peak_lag_acausal_s": pk_n,
                      "implied_group_velocity_km_s": [R_KM / pk_p, R_KM / abs(pk_n)],
                      "peak_asymmetry_s": pk_p + pk_n}          # 0 for a perfectly symmetric NCF

# ------------------------------------------------------------------ correlation variants
def build(mask=None, corrected=False):
    s1, s2 = [], []
    for i, d in enumerate(days_used):
        if mask is not None and not mask[i]: continue
        a, b = day_arr[d]
        if corrected:
            if d not in s_off: continue
            a, b = shift_day(a, s_off[d]["RUNG"]), shift_day(b, s_off[d]["MTAN"])
        s1.append(windows_from_day(a)); s2.append(windows_from_day(b))
    return np.stack(s1), np.stack(s2)

variants = {"as_packaged": dict(mask=None, corrected=False), "excl_outlier_days": dict(mask=keep, corrected=False)}
if s_off is not None:
    variants["timing_corrected"] = dict(mask=None, corrected=True)
    variants["timing_corrected_excl_outliers"] = dict(mask=keep, corrected=True)

results, coh_real, coh_cplx, metrics = {}, {}, {}, {"band_hz": [f_lo, f_hi], "n_freq_bins": int(band.sum()), "variants": {}}
for name, kw in variants.items():
    if kw["mask"] is not None and not (kw["mask"].sum() >= 5 and (~kw["mask"]).any()):
        print(f"variant {name}: skipped (no outlier days or too few kept)", flush=True); continue
    S1, S2 = build(**kw)
    t0c = time.time()
    r = compute_crosscorr_mtc_fastmspec(S1, S2, wband=args.wband, cutoff=1 - 1e-5, epsilon=1e-5)
    coh_c = r.coh_sum / r.coh_num
    coh_real[name], coh_cplx[name] = coh_c[pos].real, coh_c[pos]
    arr, m = score(coh_real[name][band]); ncf, env, nm = ncf_stats(coh_c)
    m.update(n_days=int(S1.shape[0]), coh_num=int(r.coh_num), K=int(r.taper_size), ncf=nm)
    metrics["variants"][name] = m; results[name] = dict(arr=arr, ncf=ncf, env=env)
    np.savetxt(OUT(f"zero_crossing_comparison_{name}.csv"), arr, delimiter=",",
               header="f_pred_adama_hz,f_obs_hz,offset_in_local_zero_spacings,implied_velocity_diff_pct", comments="")
    print(f"[{name}] {time.time() - t0c:.0f}s  days={S1.shape[0]} coh_num={r.coh_num}  corr={m['corr_vs_adama_bessel']:+.3f}  "
          f"recall={m['recall_within_quarter_spacing']}/{m['n_predicted_crossings']} precision={m['precision_observed_near_prediction']:.2f} "
          f"dv={m['median_signed_velocity_diff_pct']:+.1f}%  p95|coh|={m['p95_abs_coherence_in_band']:.3f}  "
          f"NCF peaks {nm['peak_lag_causal_s']:+.2f}/{nm['peak_lag_acausal_s']:+.2f} s", flush=True)
    del S1, S2

# single-taper (context; as packaged, all days)
S1, S2 = build()
t0c = time.time()
s1p = pp.ccf_cos_taper_3dim(pp.ccf_detrend_3dim(S1)); s2p = pp.ccf_cos_taper_3dim(pp.ccf_detrend_3dim(S2))
F1, F2 = np.fft.fft(s1p, axis=2), np.fft.fft(s2p, axis=2)
ct = F2 * np.conj(F1) / np.abs(F1) / np.abs(F2); ct = np.where(np.isnan(ct), 0, ct)
coh_st = (ct.sum(axis=(0, 1)) / (ct.shape[0] * ct.shape[1]))[pos].real
del F1, F2, ct, S1, S2, s1p, s2p
arr_st, m_st = score(coh_st[band]); metrics["single_taper_as_packaged"] = m_st
print(f"[single-taper] {time.time() - t0c:.0f}s  corr={m_st['corr_vs_adama_bessel']:+.3f} n_obs_crossings={m_st['n_observed_crossings']} "
      f"precision={m_st['precision_observed_near_prediction']:.2f}", flush=True)

# ------------------------------------------------------------------ package
sel = np.abs(LAGS) <= 300
cols = [LAGS[sel]] + [results[n]["ncf"][sel] for n in results] + [results[n]["env"][sel] for n in results]
np.savetxt(OUT("ncf_0.03-0.2Hz.csv"), np.column_stack(cols), delimiter=",", comments="",
           header="lag_s," + ",".join(f"ncf_{n}" for n in results) + "," + ",".join(f"envelope_{n}" for n in results))
c_full = np.full_like(f, np.nan); c_full[band] = c_of_f
p_full = np.full_like(f, np.nan); p_full[band] = pred
names = list(coh_real)
np.savez(OUT("xd_mtan_rung_bhz_coherence.npz"), freq_hz=f, coh_singletaper=coh_st, adama_c_km_s=c_full, adama_bessel_prediction=p_full,
         adama_band_hz=np.array([f_lo, f_hi]), dist_km=R_KM, days_used=np.array(days_used),
         **{f"coh_fastmspec_{n}": coh_real[n] for n in names}, **{f"cohcomplex_fastmspec_{n}": coh_cplx[n] for n in names},
         **{f"ncf_{n}": results[n]["ncf"] for n in names}, ncf_lags_s=LAGS)
np.savetxt(OUT("xd_mtan_rung_bhz_coherence.csv"),
           np.column_stack([f, 1 / f, coh_st] + [coh_real[n] for n in names] + [c_full, p_full]), delimiter=",", comments="",
           header="freq_hz,period_s,re_coh_singletaper," + ",".join(f"re_coh_fastmspec_{n}" for n in names) +
                  ",adama_rayleigh_c_km_s,adama_bessel_prediction")
json.dump(metrics, open(OUT("benchmark_metrics.json"), "w"), indent=1)
prov["finished"] = str(UTCDateTime.now())
json.dump(prov, open(OUT("provenance.json"), "w"), indent=1)

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
main = "timing_corrected_excl_outliers" if "timing_corrected_excl_outliers" in coh_real else "as_packaged"
fig, ax = plt.subplots(3, 1, figsize=(11, 11))
m = f <= 0.25
ax[0].plot(f[m], coh_st[m], color="0.6", lw=0.6, label="single-taper (as packaged)")
ax[0].plot(f[m], coh_real["as_packaged"][m], color="tab:red", lw=1.0, label="FastMspec, as packaged")
if main != "as_packaged": ax[0].plot(f[m], coh_real[main][m], color="tab:blue", lw=1.0, label=f"FastMspec, {main}")
ax[0].plot(fb, pred, "k--", lw=1.0, label="J0 prediction from ADAMA Rayleigh c(f)")
ax[0].axvspan(f_lo, f_hi, color="tab:blue", alpha=0.06); ax[0].axhline(0, color="k", lw=0.3)
ax[0].set_xlabel("Frequency (Hz)"); ax[0].set_ylabel("Re[coherence]"); ax[0].legend(fontsize=8)
ax[0].set_title(f"XD.RUNG-XD.MTAN BHZ-BHZ, {len(days_used)} days ({days_used[0]}..{days_used[-1]}), R={R_KM:.1f} km")
for n, c in (("as_packaged", "tab:red"), (main, "tab:blue")):
    ax[1].plot(fb, coh_real[n][band], color=c, lw=1.0, label=n)
ax[1].plot(fb, pred, "k--", lw=1.0, label="ADAMA J0 prediction")
for fp in f_pred_x: ax[1].axvline(fp, color="k", lw=0.4, alpha=0.5)
ax[1].set_title("Inside ADAMA band (vertical lines = ADAMA-predicted zero crossings)", fontsize=10)
ax[1].set_xlabel("Frequency (Hz)"); ax[1].set_ylabel("Re[coherence]"); ax[1].legend(fontsize=8)
for n, c in (("as_packaged", "tab:red"), (main, "tab:blue")):
    ok = ~np.isnan(results[n]["arr"][:, 1])
    ax[2].plot(results[n]["arr"][ok, 1], np.interp(results[n]["arr"][ok, 1], fb, c_of_f) * (1 + results[n]["arr"][ok, 3] / 100),
               "o", color=c, label=f"implied by {n}")
ax[2].plot(fb, c_of_f, "k-", lw=1.5, label="ADAMA co_ral (Rayleigh)")
ax[2].set_xlabel("Frequency (Hz)"); ax[2].set_ylabel("Phase velocity (km/s)"); ax[2].legend(fontsize=8)
plt.tight_layout(); plt.savefig(OUT("xd_mtan_rung_bhz_summary.png"), dpi=130)
print("DONE ->", args.out_dir, flush=True)
