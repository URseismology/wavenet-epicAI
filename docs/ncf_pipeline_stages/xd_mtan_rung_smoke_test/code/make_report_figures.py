#!/usr/bin/env python
"""Report figures for the XD.MTAN / XD.RUNG BHZ pipeline smoke test + FastMSPEC benchmark against ADAMA.

Inputs (pulled from Bluehive): <results>/{xd_mtan_rung_bhz_coherence.npz, benchmark_metrics.json, provenance.json,
daily_coverage.csv, ncf_0.03-0.2Hz.csv, zero_crossing_comparison_<variant>.csv} and <adama>/ (unused here: ADAMA c(f)
is carried inside the npz).  Everything is computed on 1-D arrays, so this runs locally in a couple of minutes.

Picking: the seislib-derived picker (FastMSPEC's vendored, instrumented copy) is run on each coherence with TWO
reference curves --
  * "flat 3.5 km/s" : an ADAMA-independent prior (constant velocity, +/-0.8 km/s corridor) -- the fair benchmark;
  * "ADAMA c(f)"    : ADAMA's own Rayleigh curve as the reference -- circular for accuracy, shown only because it is the
                      configuration the batch pipeline uses (reference-guided) and for the KDE view.
Picked velocities are then compared with ADAMA c(f).
"""
import argparse, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

# The picker and the Notebook-4 helpers come from a FastMSPEC checkout (github.com/URseismology/FastMSPEC, branch notebook5-phase-velocity-revamp).
FMS = Path(os.environ.get("FASTMSPEC_DIR", "FastMSPEC"))   # set FASTMSPEC_DIR to your checkout
sys.path.insert(0, str(FMS / "python")); sys.path.insert(0, str(FMS / "notebooks"))
from dispcurve_pick import extract_dispcurve, build_template_family  # noqa: E402
from _lib.nb4_helpers import scan_templates_with_picker, best_of  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--results", required=True); ap.add_argument("--figs", required=True)
ap.add_argument("--fixed", default=None, help="results dir of the FIXED-pipeline correlation run (optional)")
ap.add_argument("--verify-csv", default=None, help="timing_measured_vs_replay.csv (optional)")
args = ap.parse_args()
RES, FIG = Path(args.results), Path(args.figs); FIG.mkdir(parents=True, exist_ok=True)

d = np.load(RES / "xd_mtan_rung_bhz_coherence.npz", allow_pickle=True)
metrics = json.load(open(RES / "benchmark_metrics.json")); prov = json.load(open(RES / "provenance.json"))
cov = pd.read_csv(RES / "daily_coverage.csv"); ncf_df = pd.read_csv(RES / "ncf_0.03-0.2Hz.csv")
f = d["freq_hz"]; R_KM = float(d["dist_km"]); f_lo, f_hi = [float(x) for x in d["adama_band_hz"]]
band = (f >= f_lo) & (f <= f_hi); fb = f[band]; c_of_f = d["adama_c_km_s"][band]; pred = d["adama_bessel_prediction"][band]
VARS = [v for v in ("as_packaged", "excl_outlier_days", "timing_corrected", "timing_corrected_excl_outliers")
        if f"coh_fastmspec_{v}" in d.files]
LABEL = {"as_packaged": "As packaged", "excl_outlier_days": "Outlier days removed",
         "timing_corrected": "Timing corrected", "timing_corrected_excl_outliers": "Timing corrected + outliers removed"}
COL = {"as_packaged": "tab:red", "excl_outlier_days": "tab:orange", "timing_corrected": "tab:green",
       "timing_corrected_excl_outliers": "tab:blue"}
MAIN = "timing_corrected_excl_outliers" if "timing_corrected_excl_outliers" in VARS else VARS[-1]
coh = {v: d[f"coh_fastmspec_{v}"] for v in VARS}; coh_st = d["coh_singletaper"]
zx = {v: pd.read_csv(RES / f"zero_crossing_comparison_{v}.csv") for v in VARS}
if args.fixed:
    FX = Path(args.fixed); dfx = np.load(FX / "xd_mtan_rung_bhz_coherence.npz", allow_pickle=True)
    mfx = json.load(open(FX / "benchmark_metrics.json")); ncf_fx = pd.read_csv(FX / "ncf_0.03-0.2Hz.csv")
    for src, name in (("as_packaged", "fixed_pipeline"), ("excl_outlier_days", "fixed_pipeline_excl_outliers")):
        if f"coh_fastmspec_{src}" in dfx.files:
            VARS.append(name); coh[name] = dfx[f"coh_fastmspec_{src}"]; metrics["variants"][name] = mfx["variants"][src]
            zx[name] = pd.read_csv(FX / f"zero_crossing_comparison_{src}.csv")
            ncf_df[f"ncf_{name}"] = ncf_fx[f"ncf_{src}"].values; ncf_df[f"envelope_{name}"] = ncf_fx[f"envelope_{src}"].values
    LABEL.update({"fixed_pipeline": "FIXED pipeline (no downstream correction)", "fixed_pipeline_excl_outliers": "Fixed pipeline + outlier days removed"})
    COL.update({"fixed_pipeline": "tab:cyan", "fixed_pipeline_excl_outliers": "navy"})
    metrics["single_taper_fixed_pipeline"] = mfx["single_taper_as_packaged"]
f_pred_x = np.array(sorted(set(zx[VARS[0]]["f_pred_adama_hz"])))
FIXED = "fixed_pipeline" if "fixed_pipeline" in VARS else None
ndays = len(d["days_used"])
title_pair = f"XD.RUNG-XD.MTAN BHZ-BHZ, {R_KM:.1f} km"

def save(fig, name):
    fig.tight_layout(); fig.savefig(FIG / name, dpi=150); plt.close(fig); print("wrote", name, flush=True)

# ---------------------------------------------------------------- Fig 0: coverage timeline + Fig 9: timing placement
PLC = json.load(open(RES.parent / "placement_errors.json")) if (RES.parent / "placement_errors.json").exists() else json.load(open(RES / "placement_errors.json"))
def yyyymmdd(x): return pd.to_datetime(x, format="%Y%m%d")
fig, ax = plt.subplots(2, 1, figsize=(11, 5.2), sharex=True)
cov_dates = pd.to_datetime(pd.read_csv(RES / "daily_coverage.csv")["date"])
cov_nz = pd.read_csv(RES / "daily_coverage.csv")
for a, sta in zip(ax, ("RUNG", "MTAN")):
    placed = [yyyymmdd(k) for k in PLC[sta]["days"]]; refused = [yyyymmdd(k) for k in PLC[sta]["refused"]]
    a.plot(placed, np.ones(len(placed)), "|", color="tab:green", ms=14, label="processed day placed in master")
    a.plot(refused, np.ones(len(refused)), "|", color="tab:red", ms=22, mew=3, label="day REFUSED by append logic (dropped)")
    zero = cov_dates[cov_nz[f"nonzero_fraction_{sta}_BHZ"] < 0.5]
    a.plot(zero, np.ones(len(zero)), "x", color="k", label="day present in range but ~all zeros")
    a.set_yticks([]); a.set_ylabel(f"XD.{sta}"); a.legend(fontsize=7, loc="upper right", ncol=3)
ax[0].set_title("Coverage of the 1994-05-25 .. 1994-10-30 window (BHZ), one tick per UTC day")
save(fig, "fig0_coverage.png")

fig, ax = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
for sta, c in (("RUNG", "tab:purple"), ("MTAN", "tab:green")):
    ks = sorted(PLC[sta]["days"]); ax[0].plot([yyyymmdd(k) for k in ks], [PLC[sta]["days"][k]["e"] for k in ks], ".", color=c, ms=4, label=f"XD.{sta}")
ax[0].axhline(0, color="k", lw=0.5); ax[0].set_ylabel("grid time - true time of day's first sample (s)"); ax[0].legend(fontsize=8)
ax[0].set_title("Where the pipeline placed each processed day on its 1 Hz grid vs where the samples were truly acquired")
common = sorted(set(PLC["RUNG"]["days"]) & set(PLC["MTAN"]["days"]))
rel = [PLC["RUNG"]["days"][k]["e"] - PLC["MTAN"]["days"][k]["e"] for k in common]
ax[1].plot([yyyymmdd(k) for k in common], rel, ".", color="tab:blue", ms=4)
ax[1].axhline(0, color="k", lw=0.5); ax[1].set_ylabel("RUNG - MTAN placement error (s)")
ax[1].set_title(f"Relative placement error between the two stations (median {np.median(rel):+.2f} s); "
                "a further -0.30 s / +0.22 s grid-phase offset applies when slicing UTC days (total ~1 s)", fontsize=9)
save(fig, "fig9_timing_placement.png")

# ---------------------------------------------------------------- Fig 1: data quality
cov["date"] = pd.to_datetime(cov["date"])
fig, ax = plt.subplots(2, 1, figsize=(11, 8.5))
for col, c, lab in (("rms_RUNG_BHZ", "tab:purple", "XD.RUNG BHZ"), ("rms_MTAN_BHZ", "tab:green", "XD.MTAN BHZ")):
    ax[0].semilogy(cov["date"], cov[col].clip(lower=1e-1), ".-", ms=4, lw=0.6, color=c, label=lab)
    med = cov[col].median(); ax[0].axhline(20 * med, color=c, ls=":", lw=0.9)
    ax[0].plot(cov["date"][cov[col] > 20 * med], cov[col][cov[col] > 20 * med], "o", mfc="none", mec="k", ms=9)
ax[0].plot([], [], "o", mfc="none", mec="k", label="> 20 x station median (excluded in 'outliers removed')")
for dt in prov.get("timing_offsets_s", {}).get("refused_days", {}).get("RUNG", []) + prov.get("timing_offsets_s", {}).get("refused_days", {}).get("MTAN", []):
    ax[0].axvline(pd.to_datetime(dt), color="tab:red", alpha=0.35, lw=3)
ax[0].plot([], [], color="tab:red", alpha=0.35, lw=3, label="day refused by append logic (data dropped)")
ax[0].set_ylabel("Daily RMS of packaged BHZ  (labelled 'm'; actually nm)"); ax[0].legend(fontsize=8, loc="upper right")
ax[0].set_title("Packaged BHZ: daily RMS (dotted = 20 x median). Outliers are single-station and 10^3-10^6 x the quiet-day level")
hp = prov["hour_of_day_rms_profile"]; hrs = np.arange(24)
ax[1].bar(hrs - 0.2, hp["RUNG"], 0.4, color="tab:purple", label="XD.RUNG"); ax[1].bar(hrs + 0.2, hp["MTAN"], 0.4, color="tab:green", label="XD.MTAN")
ax[1].axvspan(-0.5, 0.7, color="0.85", zorder=0); ax[1].axvspan(22.8, 23.5, color="0.85", zorder=0)
ax[1].set_xlabel("UTC hour of day"); ax[1].set_ylabel("Median RMS / overall median"); ax[1].legend(fontsize=8)
ax[1].set_title("Hour-of-day amplitude profile: 5 % cosine taper applied to every processed day (grey = 72-min taper zones)")
save(fig, "fig1_data_quality.png")

# ---------------------------------------------------------------- Fig 2: processing waterfall
steps = ["raw counts", "detrend", "response\nremoval", "lowpass\n0.4 Hz", "highpass\n1/3600 Hz", "decimate\n+taper"]
cases = {"MTAN 05-26 (quiet)": [563.7, 325.3, 755.0, 754.9, 754.9, 742.6],
         "RUNG 05-26 (quiet)": [2704, 292.0, 650.7, 650.6, 650.6, 640.3],
         "MTAN 05-28 (spikes, restart)": [44250, 44250, 772400, 772400, 772400, 772400],
         "RUNG 05-29 (spikes, restart)": [134500, 134500, 1414000, 1413000, 1413000, 1413000],
         "RUNG 05-30 (spikes)": [35230, 35200, 149000, 148900, 148900, 148900],
         "MTAN 05-30 (raw pegged at int32 max)": [4.829e8, 4.813e8, 6.284e8, 6.275e8, 6.275e8, 6.118e8]}
fig, ax = plt.subplots(figsize=(10, 5.5))
for (k, v), c in zip(cases.items(), ["tab:green", "tab:purple", "tab:orange", "tab:red", "tab:brown", "k"]):
    ax.semilogy(range(len(steps)), v, "o-", color=c, label=k, ls="--" if "quiet" in k else "-")
ax.set_xticks(range(len(steps))); ax.set_xticklabels(steps, fontsize=8); ax.set_ylabel("RMS after step")
ax.set_title("Outlier days are already extreme in the RAW counts; no processing step creates them\n"
             "(response removal rescales ~2x on quiet days, up to ~17x on spike days)", fontsize=10)
ax.legend(fontsize=7, loc="center right"); save(fig, "fig2_processing_steps.png")

# ---------------------------------------------------------------- Fig 3: coherence overlay (correlation overlay)
fig, ax = plt.subplots(3, 1, figsize=(11.5, 12))
m = f <= 0.25
ax[0].plot(f[m], coh_st[m], color="0.65", lw=0.6, label="single-taper (as packaged)")
ax[0].plot(f[m], coh["as_packaged"][m], color=COL["as_packaged"], lw=1.0, label="FastMspec, as packaged")
ax[0].plot(f[m], coh[MAIN][m], color=COL[MAIN], lw=1.1, label=f"FastMspec, {LABEL[MAIN].lower()}")
if FIXED: ax[0].plot(f[m], coh[FIXED][m], color=COL[FIXED], lw=1.1, ls="--", label=f"FastMspec, {LABEL[FIXED].lower()}")
ax[0].plot(fb, pred, "k--", lw=1.0, label="J0(2 pi f R / c) with ADAMA Rayleigh c(f)")
ax[0].axvspan(f_lo, f_hi, color="tab:blue", alpha=0.06); ax[0].axhline(0, color="k", lw=0.3)
ax[0].set_ylabel("Re[coherence]"); ax[0].set_xlabel("Frequency (Hz)"); ax[0].legend(fontsize=8, ncol=2, loc="upper right")
ax[0].set_title(f"{title_pair}: {ndays} days ({d['days_used'][0]} .. {d['days_used'][-1]})")
for a, v in ((ax[1], "as_packaged"), (ax[2], FIXED or MAIN)):
    a.plot(fb, coh[v][band], color=COL[v], lw=1.1, label=f"FastMspec, {LABEL[v].lower()}")
    a.plot(fb, pred, "k--", lw=1.0, label="ADAMA-predicted J0 coherence")
    for fp in f_pred_x: a.axvline(fp, color="k", lw=0.4, alpha=0.5)
    mv = metrics["variants"][v]
    a.set_title(f"{LABEL[v]}: corr with prediction = {mv['corr_vs_adama_bessel']:+.2f};  "
                f"{mv['recall_within_quarter_spacing']}/{mv['n_predicted_crossings']} ADAMA zero crossings matched (within 1/4 spacing);  "
                f"median velocity offset {mv['median_signed_velocity_diff_pct']:+.1f} %", fontsize=9)
    a.set_ylabel("Re[coherence]"); a.set_xlabel("Frequency (Hz)"); a.legend(fontsize=8, loc="upper right")
save(fig, "fig3_coherence_overlay.png")

# ---------------------------------------------------------------- Fig 4: zero-crossing velocities vs ADAMA
fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
ax[0].plot(fb, c_of_f, "k-", lw=1.6, label="ADAMA co_ral (Rayleigh)")
for v in VARS:
    t = zx[v].dropna(); c_imp = np.interp(t["f_obs_hz"], fb, c_of_f) * (1 + t["implied_velocity_diff_pct"] / 100)
    ax[0].plot(t["f_obs_hz"], c_imp, "o", color=COL[v], ms=5, alpha=0.85, label=f"{LABEL[v]}")
    ax[1].plot(t["f_obs_hz"], t["implied_velocity_diff_pct"], "o-", color=COL[v], ms=4, lw=0.7, alpha=0.85)
ax[1].axhline(0, color="k", lw=0.5); ax[1].set_ylabel("Implied - ADAMA phase velocity (%)"); ax[1].set_xlabel("Frequency (Hz)")
ax[0].set_ylabel("Phase velocity (km/s)"); ax[0].legend(fontsize=8)
ax[0].set_title("Phase velocity implied by each observed zero crossing (Bessel-order assigned from ADAMA's own prediction)")
save(fig, "fig4_zero_crossing_velocity.png")

# ---------------------------------------------------------------- Fig 5: NCF
fig, ax = plt.subplots(2, 1, figsize=(10.5, 7.5), sharex=True)
lag = ncf_df["lag_s"].values; sel = np.abs(lag) <= 120
for a, v in zip(ax, ("as_packaged", FIXED or MAIN)):
    a.plot(lag[sel], ncf_df[f"ncf_{v}"].values[sel], color=COL[v], lw=1.0, label="NCF (FastMspec coherency, 0.03-0.2 Hz)")
    a.plot(lag[sel], ncf_df[f"envelope_{v}"].values[sel], "k", lw=0.7, alpha=0.7, label="envelope")
    nm = metrics["variants"][v]["ncf"]
    for pk in (nm["peak_lag_causal_s"], nm["peak_lag_acausal_s"]): a.axvline(pk, color="k", ls=":", lw=0.9)
    a.set_title(f"{LABEL[v]}: envelope peaks {nm['peak_lag_causal_s']:+.2f} s / {nm['peak_lag_acausal_s']:+.2f} s "
                f"(asymmetry {nm['peak_asymmetry_s']:+.2f} s; implied group velocity "
                f"{nm['implied_group_velocity_km_s'][0]:.2f} / {nm['implied_group_velocity_km_s'][1]:.2f} km/s)", fontsize=9)
    a.set_ylabel("NCF"); a.legend(fontsize=8, loc="upper right")
ax[1].set_xlabel("Lag (s)   [positive = RUNG -> MTAN]"); save(fig, "fig5_ncf.png")

# ---------------------------------------------------------------- Fig 6: scorecard
rows = [(LABEL[v], metrics["variants"][v]) for v in VARS] + [("Single-taper (as packaged)", metrics["single_taper_as_packaged"])]
fig, ax = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
names = [r[0] for r in rows]
cols = [COL.get(v, "0.5") for v in VARS] + ["0.5"]
_st = metrics["single_taper_as_packaged"]; _n_obs = _st.get("n_obs_crossings", _st.get("n_observed_crossings"))
panels = [([r[1]["corr_vs_adama_bessel"] for r in rows], "Correlation with\nADAMA-predicted coherence"),
          ([r[1]["recall_within_quarter_spacing"] / r[1]["n_predicted_crossings"] for r in rows], "Fraction of ADAMA zero crossings found\n(within 1/4 local spacing)"),
          ([r[1]["precision_observed_near_prediction"] for r in rows], "Fraction of observed crossings that are real" + (f"\n(single-taper: {_n_obs} observed for {_st['n_predicted_crossings']} predicted)" if _n_obs else ""))]
y = np.arange(len(rows))[::-1]
for a, (vals, title) in zip(ax, panels):
    a.barh(y, vals, color=cols); a.set_title(title, fontsize=10); a.set_xlim(0, 1.12); a.grid(axis="x", lw=0.3)
    for yy, v in zip(y, vals): a.text(v + 0.01, yy, f"{v:.2f}", va="center", fontsize=8)
ax[0].set_yticks(y); ax[0].set_yticklabels(names, fontsize=8)
fig.tight_layout()
save(fig, "fig6_scorecard.png")

# ---------------------------------------------------------------- Figs 7+: seislib picker (native KDE diagnostic) + picks vs ADAMA
def cref(kind):
    if kind == "ADAMA c(f)": return interp1d(fb, c_of_f, bounds_error=False, fill_value=(c_of_f[0], c_of_f[-1]))
    return interp1d([f_lo, f_hi], [3.5, 3.5])
PK = dict(filt_width=10, filt_height=1.0, x_step=0.05, pick_threshold=0, horizontal_polarization=False)   # J0 (vertical/Rayleigh)
summary, picked = [], {}
cases = [(v, coh[v]) for v in VARS] + [("single_taper", coh_st)]
for kind in ("flat 3.5 km/s", "ADAMA c(f)"):
    for v, c in cases:
        templates = build_template_family(cref(kind), f_lo, f_hi, corridor_km_s=0.8, step_km_s=0.2)
        scanned = scan_templates_with_picker(f, c, R_KM, templates, f_lo, f_hi, 1.2, 4.8, horizontal_polarization=False, verbose=False)
        delta, zc, picks, diag = best_of(scanned)
        row = dict(reference=kind, variant=v, converged=diag is not None, delta_km_s=delta)
        if diag is not None:
            vv = np.interp(picks[:, 0], fb, c_of_f); dv = 100 * (picks[:, 1] - vv) / vv
            row.update(coverage=diag.freq_coverage_fraction, bad_quality=diag.bad_quality_fraction, n_picks=len(picks),
                       pick_band_hz=f"{picks[:, 0].min():.3f}-{picks[:, 0].max():.3f}",
                       mean_signed_diff_pct=float(dv.mean()), rms_diff_pct=float(np.sqrt((dv ** 2).mean())))
            picked[(kind, v)] = picks
            # native seislib diagnostic figure for the winning template (KDE density, tracked branch, picks, curve)
            if v in (MAIN, "as_packaged", "single_taper") or v == "timing_corrected" or v.startswith("fixed_pipeline"):
                fg = np.linspace(f_lo, f_hi, 200); ref = np.column_stack([fg, templates[delta](fg)])
                tag = f"{'adama' if kind.startswith('ADAMA') else 'flat'}_{v}"
                try:
                    extract_dispcurve(f, c, R_KM, ref, freqmin=f_lo, freqmax=f_hi, cmin=1.2, cmax=4.8, plotting=True,
                                      savefig=str(FIG / f"fig7_seislib_kde_{tag}.png"), sta1="XD.RUNG", sta2="XD.MTAN", **PK)
                    plt.close("all"); print("wrote", f"fig7_seislib_kde_{tag}.png", flush=True)
                except Exception as e:
                    plt.close("all"); print(f"native picker plot failed for {tag}: {type(e).__name__}", flush=True)
        else:
            # show the picker's own view of a NON-converging case too (best-effort)
            if v in ("single_taper", "as_packaged"):
                fg = np.linspace(f_lo, f_hi, 200); ref = np.column_stack([fg, templates[0.0](fg) if 0.0 in templates else templates[list(templates)[len(templates) // 2]](fg)])
                tag = f"{'adama' if kind.startswith('ADAMA') else 'flat'}_{v}_NOCONV"
                try:
                    extract_dispcurve(f, c, R_KM, ref, freqmin=f_lo, freqmax=f_hi, cmin=1.2, cmax=4.8, plotting=True,
                                      savefig=str(FIG / f"fig7_seislib_kde_{tag}.png"), sta1="XD.RUNG", sta2="XD.MTAN", **PK)
                except Exception:
                    pass
                plt.close("all")
        summary.append(row)
        print(row, flush=True)
pd.DataFrame(summary).to_csv(FIG / "picker_summary.csv", index=False)

fig, ax = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
for a, kind in zip(ax, ("flat 3.5 km/s", "ADAMA c(f)")):
    a.plot(fb, c_of_f, "k-", lw=2, label="ADAMA co_ral (Rayleigh)")
    for v, _ in cases:
        p = picked.get((kind, v))
        if p is not None: a.plot(p[:, 0], p[:, 1], "-", lw=1.6, color=COL.get(v, "0.5"), label=LABEL.get(v, "single-taper"))
    a.set_title(f"Picked with reference = {kind}" + ("  (independent of ADAMA)" if kind.startswith("flat") else "  (circular; KDE view only)"), fontsize=10)
    a.set_xlabel("Frequency (Hz)"); a.legend(fontsize=7)
ax[0].set_ylabel("Phase velocity (km/s)"); save(fig, "fig8_picked_curves_vs_adama.png")
if args.verify_csv:
    tv = pd.read_csv(args.verify_csv); tv["date"] = pd.to_datetime(tv["date"])
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    for j, (sta, c) in enumerate((("RUNG", "tab:purple"), ("MTAN", "tab:green"))):
        t = tv[(tv.station == sta)].dropna(subset=["measured_shift_s"]); t = t[t.band_coherence > 0.9]
        ax[0, j].plot(t["date"], t["measured_shift_s"], ".", color=c, ms=5, label="measured (original vs fixed data)")
        tp = t.dropna(subset=["replay_predicted_shift_s"]); ax[0, j].plot(tp["date"], tp["replay_predicted_shift_s"], "k.", ms=2, label="predicted by replay of the append logic")
        ax[0, j].set_title(f"XD.{sta}: timing offset of each UTC day\nin the original data vs true time", fontsize=10); ax[0, j].set_ylabel("seconds"); ax[0, j].legend(fontsize=7)
        ax[1, j].plot(tp["replay_predicted_shift_s"], tp["measured_shift_s"], ".", color=c, ms=5)
        lim = [min(tp["replay_predicted_shift_s"].min(), tp["measured_shift_s"].min()) - 0.05, max(tp["replay_predicted_shift_s"].max(), tp["measured_shift_s"].max()) + 0.05]
        ax[1, j].plot(lim, lim, "k-", lw=0.6); ax[1, j].set_xlabel("replay-predicted shift (s)"); ax[1, j].set_ylabel("measured shift (s)")
        dd = np.abs(tp["measured_shift_s"] - tp["replay_predicted_shift_s"]); ax[1, j].set_title(f"{len(tp)} days; |measured - predicted|: median {dd.median()*1000:.1f} ms, max {dd.max()*1000:.0f} ms", fontsize=9)
    save(fig, "fig10_timing_verification.png")
print("ALL DONE")
