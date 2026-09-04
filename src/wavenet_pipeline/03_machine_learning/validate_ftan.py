#!/usr/bin/env python3
"""
QA gate for Stage A: cross-checks the precomputed empirical_ftan_dispersion/FTAN_ZZ
(computed during simulation, pycwt dj=1/12 on gradient-of-CCF) against an independent
recomputation (dj=1/24, coherence-derived, bandpassed 0.05-1.0 Hz) — the same method
src/wavenet_pipeline/02_simulation/verify_main.py already uses for its own plots, here
extracted into a standalone, reusable function instead of buried inside a monolithic
plotting routine.

Finding worth recording: verify_main.py's recompute already lands natively on almost
exactly the canonical (76, 300) grid (`per = arange(1,20,0.25)`, `vel = arange(2,5,0.01)`)
— this is very likely *why* the canonical grid was chosen the way it was. So the
recomputed FTAN needs no further regridding; only the precomputed FTAN_ZZ (native,
0-254 km/s / 1-132s raw axes) needs ftan_grid.regrid_ftan applied before comparison.

This is a blocking gate per the plan: Stage A isn't "done" until this passes with
acceptable agreement. The 0.05 km/s threshold below is a recommendation (outside the
mask's own +-2-bin ~= +-0.02 km/s resolution), not a pre-existing project standard —
confirm during Stage A review, don't treat it as settled.

Usage:
    python validate_ftan.py --h5 wavenetv2_dataset_10k_full.h5 --n-samples 200 --seed 42 \
        --out qa_report/
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import h5py
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import detrend

from . import ftan_grid
from .dataset import enumerate_samples

CENTROID_DISCREPANCY_THRESHOLD_KMS = 0.05  # recommendation, not a settled standard


def _cosine_taper(data: np.ndarray, taper_fraction: float = 0.05) -> np.ndarray:
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper


def _bandpass_filter_freq(fft_data: np.ndarray, freq_range: tuple[float, float], dt: float) -> np.ndarray:
    taper_width_frac = 0.2
    n = len(fft_data)
    freqs = np.fft.fftfreq(n, dt)
    filt = np.zeros(n)
    f_min, f_max = freq_range
    taper_width = taper_width_frac * (f_max - f_min)
    for i, f in enumerate(freqs):
        abs_f = abs(f)
        if abs_f < f_min - taper_width or abs_f > f_max + taper_width:
            filt[i] = 0.0
        elif f_min + taper_width <= abs_f <= f_max - taper_width:
            filt[i] = 1.0
        elif abs_f < f_min + taper_width:
            filt[i] = 0.5 * (1 - np.cos(np.pi * (abs_f - f_min + taper_width) / (2 * taper_width)))
        else:
            filt[i] = 0.5 * (1 + np.cos(np.pi * (abs_f - f_max + taper_width) / (2 * taper_width)))
    return fft_data * filt


def recompute_ftan_from_coherence(coh_real: np.ndarray, freqs_hz: np.ndarray, sep_km: float,
                                   dt: float = 0.5, dj: float = 1 / 24,
                                   fmin: float = 0.05, fmax: float = 1.0) -> dict:
    """
    Independent FTAN recompute from ccf_isotropic/{COH_REAL_ZZ,freqs_hz}, matching
    verify_main.py's compute_ftan_and_plot() exactly (dj=1/24, coherence-derived,
    bandpassed). Returns FTAN_ZZ already on ftan_grid.PERIOD_GRID x ftan_grid.VEL_GRID —
    no further regridding needed (see module docstring).
    """
    import pycwt  # deferred import: only needed for this QA path, not the main pipeline

    coh_ifft_raw = np.fft.ifft(coh_real).real
    coh_time_raw = np.fft.fftshift(coh_ifft_raw)
    coh_time_raw = detrend(coh_time_raw)
    coh_time_raw = _cosine_taper(coh_time_raw)

    coh_fft = np.fft.fft(np.fft.fftshift(coh_time_raw))
    coh_filt = _bandpass_filter_freq(coh_fft, (fmin, fmax), dt)
    time_ccf = np.fft.fftshift(np.fft.ifft(coh_filt).real)

    npts = len(time_ccf)
    indx = npts // 2
    egf = 0.5 * time_ccf[indx:] + 0.5 * np.flip(time_ccf[:indx + 1], axis=0)

    vmin, vmax = ftan_grid.VEL_MIN, ftan_grid.VEL_MAX
    pt1 = int(sep_km / vmax / dt)
    pt2 = int(sep_km / vmin / dt)
    if pt1 == 0:
        pt1 = 10
    if pt2 > (npts // 2):
        pt2 = npts // 2

    indx_arr = np.arange(pt1, pt2)
    tvec = indx_arr * dt
    egf = egf[indx_arr]

    if len(egf) <= 10:
        return {"FTAN_ZZ": None, "period_s": ftan_grid.PERIOD_GRID, "velocity_kms": ftan_grid.VEL_GRID}

    cwt, sj, freq, coi, _, _ = pycwt.cwt(egf, dt, dj, -1, -1, "morlet")
    freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
    cwt = cwt[freq_ind]
    freq = freq[freq_ind]
    period = 1 / freq
    rcwt = np.abs(cwt) ** 2

    velocity_data = sep_km / tvec

    from scipy.interpolate import RegularGridInterpolator
    fc = RegularGridInterpolator((period, velocity_data), rcwt, bounds_error=False, fill_value=0.0)
    P, V = np.meshgrid(ftan_grid.PERIOD_GRID, ftan_grid.VEL_GRID, indexing="ij")
    rcwt_new = fc(np.stack((P, V), axis=-1))

    for ii in range(rcwt_new.shape[0]):
        row_max = rcwt_new[ii].max()
        if row_max > 0:
            rcwt_new[ii] /= row_max
    for j in range(rcwt_new.shape[1]):
        rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)

    return {
        "FTAN_ZZ": rcwt_new.astype(np.float32),
        "period_s": ftan_grid.PERIOD_GRID,
        "velocity_kms": ftan_grid.VEL_GRID,
    }


def _pixel_correlation(a: np.ndarray, b: np.ndarray) -> float:
    a_flat, b_flat = a.ravel(), b.ravel()
    if a_flat.std() == 0 or b_flat.std() == 0:
        return float("nan")
    return float(np.corrcoef(a_flat, b_flat)[0, 1])


def _thresholded_iou(a: np.ndarray, b: np.ndarray, frac_of_row_max: float = 0.5) -> float:
    def _thresh(img):
        row_max = img.max(axis=-1, keepdims=True)
        return img >= (frac_of_row_max * np.where(row_max > 0, row_max, 1.0))

    a_bin, b_bin = _thresh(a), _thresh(b)
    inter = (a_bin & b_bin).sum()
    union = (a_bin | b_bin).sum()
    return float(inter / union) if union > 0 else 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--h5", required=True, type=Path)
    parser.add_argument("--n-samples", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if not args.h5.exists():
        print(f"ERROR: {args.h5} not found", file=sys.stderr)
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    samples = enumerate_samples([args.h5])
    families = sorted({s.family for s in samples})
    per_family = max(1, args.n_samples // len(families))
    rng = np.random.default_rng(args.seed)
    picked = []
    for fam in families:
        fam_samples = [s for s in samples if s.family == fam]
        rng.shuffle(fam_samples)
        picked.extend(fam_samples[:per_family])
    print(f"Validating {len(picked)} samples across {len(families)} families...")

    rows = []
    with h5py.File(args.h5, "r") as f:
        for ref in picked:
            sim = f["simulations"][ref.sim_key]
            geom = sim["geometries"][ref.geom_key]
            ftan_grp = geom["empirical_ftan_dispersion"]
            precomputed = ftan_grid.regrid_ftan(
                ftan_grp["FTAN_ZZ"][:], ftan_grp["period_s"][:], ftan_grp["velocity_kms"][:]
            )
            coh = geom["ccf_isotropic"]
            recomputed = recompute_ftan_from_coherence(coh["COH_REAL_ZZ"][:], coh["freqs_hz"][:], ref.sep_km)
            if recomputed["FTAN_ZZ"] is None:
                rows.append({"sim_key": ref.sim_key, "geom_key": ref.geom_key, "family": ref.family,
                             "status": "recompute_skipped_short_egf"})
                continue

            c_pre = ftan_grid.weighted_centroid_curve(precomputed)
            c_re = ftan_grid.weighted_centroid_curve(recomputed["FTAN_ZZ"])
            both_detected = ~np.isnan(c_pre) & ~np.isnan(c_re)
            centroid_rmse = (float(np.sqrt(np.mean((c_pre[both_detected] - c_re[both_detected]) ** 2)))
                             if both_detected.any() else float("nan"))

            rows.append({
                "sim_key": ref.sim_key, "geom_key": ref.geom_key, "family": ref.family,
                "status": "ok",
                "centroid_rmse_kms": centroid_rmse,
                "pixel_correlation": _pixel_correlation(precomputed, recomputed["FTAN_ZZ"]),
                "thresholded_iou": _thresholded_iou(precomputed, recomputed["FTAN_ZZ"]),
                "n_rows_both_detected": int(both_detected.sum()),
            })

    ok_rows = [r for r in rows if r["status"] == "ok"]
    flagged = [r for r in ok_rows if not np.isnan(r["centroid_rmse_kms"])
               and r["centroid_rmse_kms"] > CENTROID_DISCREPANCY_THRESHOLD_KMS]

    with open(args.out / "summary.csv", "w", newline="") as fcsv:
        fieldnames = ["sim_key", "geom_key", "family", "status", "centroid_rmse_kms",
                      "pixel_correlation", "thresholded_iou", "n_rows_both_detected"]
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})

    rmse_values = [r["centroid_rmse_kms"] for r in ok_rows if not np.isnan(r["centroid_rmse_kms"])]
    summary = {
        "n_checked": len(rows),
        "n_ok": len(ok_rows),
        "n_skipped": len(rows) - len(ok_rows),
        "n_flagged_over_threshold": len(flagged),
        "threshold_kms": CENTROID_DISCREPANCY_THRESHOLD_KMS,
        "centroid_rmse_mean_kms": float(np.mean(rmse_values)) if rmse_values else None,
        "centroid_rmse_median_kms": float(np.median(rmse_values)) if rmse_values else None,
        "centroid_rmse_p95_kms": float(np.percentile(rmse_values, 95)) if rmse_values else None,
        "flagged_samples": [{"sim_key": r["sim_key"], "geom_key": r["geom_key"],
                              "centroid_rmse_kms": r["centroid_rmse_kms"]} for r in flagged],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\nChecked {summary['n_checked']} samples ({summary['n_ok']} ok, {summary['n_skipped']} skipped)")
    print(f"Centroid RMSE: mean={summary['centroid_rmse_mean_kms']:.4f}  "
          f"median={summary['centroid_rmse_median_kms']:.4f}  "
          f"p95={summary['centroid_rmse_p95_kms']:.4f} km/s"
          if rmse_values else "No valid centroid comparisons.")
    print(f"Flagged (> {CENTROID_DISCREPANCY_THRESHOLD_KMS} km/s discrepancy): {len(flagged)}")
    print(f"Report written to {args.out}/")


if __name__ == "__main__":
    main()
