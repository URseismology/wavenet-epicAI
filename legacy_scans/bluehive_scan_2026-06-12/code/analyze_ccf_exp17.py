#!/usr/bin/env python3
"""
CCF Analysis for Experiment 17.

Scans experiments/experiment_17/outputs for every model that has all 180
wedges completed, picks one random coverage case per model, runs the full
CCF analysis pipeline, and saves results.

Output:
  experiments/experiment_17/coverage_analysis/<Family>/<Stem>_<case_name>_az<start>-<end>/

Usage:
    python analyze_ccf_exp17.py
    python analyze_ccf_exp17.py --family CIA    # only process one family
"""

import os
import re
import sys
import shutil
import random
import argparse
import subprocess
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
from scipy.signal import windows, detrend
from io import StringIO

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT         = Path('experiments/experiment_17')
OUTPUTS_DIR  = ROOT / 'outputs'
ANALYSIS_DIR = ROOT / 'coverage_analysis'

# ---------------------------------------------------------------------------
# 10 coverage cases
# ---------------------------------------------------------------------------
CASES = [
    {"id": "01", "name": "full",       "az_start":   0, "az_end": 360, "description": "Full ring coverage"},
    {"id": "02", "name": "hemisphere", "az_start": 270, "az_end":  90, "description": "W->N->E hemisphere"},
    {"id": "03", "name": "narrow_N",   "az_start": 340, "az_end":  20, "description": "Narrow North sector"},
    {"id": "04", "name": "NNE",        "az_start":  20, "az_end":  50, "description": "North-Northeast"},
    {"id": "05", "name": "NE_E",       "az_start":  50, "az_end":  90, "description": "Northeast to East"},
    {"id": "06", "name": "NE_ESE",     "az_start":  60, "az_end": 120, "description": "NE to ESE sector"},
    {"id": "07", "name": "E_S",        "az_start":  90, "az_end": 180, "description": "East to South"},
    {"id": "08", "name": "narrow_N2",  "az_start":   0, "az_end":  20, "description": "Very narrow North"},
    {"id": "09", "name": "NW",         "az_start": 300, "az_end": 340, "description": "Northwest sector"},
    {"id": "10", "name": "W_NW",       "az_start": 270, "az_end": 330, "description": "West to Northwest"},
]

# ---------------------------------------------------------------------------
# CCF parameters - exact from compute_ccf.py
# ---------------------------------------------------------------------------
DELTA               = 0.5
FILTER_PERIOD_RANGE = [2.0, 100.0]
FILTER_FREQ_RANGE   = [1.0 / FILTER_PERIOD_RANGE[1], 1.0 / FILTER_PERIOD_RANGE[0]]
TAPER_WIDTH         = 0.2
LAG_TIME_MAX        = 250.0
SMOOTH_WINDOW       = 10
WEDGE_SIZE          = 2
TOTAL_WEDGES        = 180


# ===========================================================================
# CCF functions - exact copy from compute_ccf.py
# ===========================================================================

def moving_average_numpy(data, window_size):
    kernel = np.ones(window_size) / window_size
    return np.convolve(data, kernel, mode='same')


def process_signals(data_array, taper_percent=0.05):
    detrended_data = detrend(data_array)
    taper_window = windows.tukey(len(detrended_data), alpha=taper_percent)
    return detrended_data * taper_window


def compute_cross_power_spectrum(r1, r2, delta):
    fft_r1 = np.fft.fft(r1)
    fft_r2 = np.fft.fft(r2)
    cross_power_full = fft_r1 * np.conj(fft_r2)
    den = np.abs(fft_r1) * np.abs(fft_r2)
    coherence_full = np.zeros_like(cross_power_full, dtype=np.complex128)
    mask = den > 0
    coherence_full[mask] = cross_power_full[mask] / den[mask]
    freqs = np.fft.fftfreq(len(r1), d=delta)
    pos_mask = freqs > 0
    return freqs[pos_mask], cross_power_full, cross_power_full[pos_mask], coherence_full[pos_mask]


def bandpass_filter_freq(fft_data, freq_range, dt):
    n = len(fft_data)
    freqs = np.fft.fftfreq(n, dt)
    filt = np.zeros(n)
    f_min, f_max = freq_range
    taper_width = TAPER_WIDTH * (f_max - f_min)
    for i, f in enumerate(freqs):
        abs_f = abs(f)
        if abs_f < f_min - taper_width or abs_f > f_max + taper_width:
            filt[i] = 0.0
        elif abs_f >= f_min + taper_width and abs_f <= f_max - taper_width:
            filt[i] = 1.0
        elif abs_f < f_min + taper_width:
            filt[i] = 0.5 * (1 - np.cos(np.pi * (abs_f - f_min + taper_width) / (2 * taper_width)))
        else:
            filt[i] = 0.5 * (1 + np.cos(np.pi * (abs_f - f_max + taper_width) / (2 * taper_width)))
    return fft_data * filt


def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len]  = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper


def compute_time_ccf(cross_power_avg, dt):
    N = len(cross_power_avg)
    ccf_ifft = np.fft.fftshift(np.fft.ifft(cross_power_avg, N).real)
    ccf_ifft = cosine_taper(detrend(ccf_ifft))
    ccf_filtered = bandpass_filter_freq(np.fft.fft(np.fft.fftshift(ccf_ifft)), FILTER_FREQ_RANGE, dt)
    ccf_ifft_final = np.fft.fftshift(np.fft.ifft(ccf_filtered).real)
    lags = (np.arange(N) - np.floor(N / 2)) * dt
    return ccf_ifft_final, lags


def stack_ccf(sim_dirs):
    """Stack waveforms and compute CCF - exact logic from compute_ccf.py process_group()."""
    wedges_r1_ts            = []
    wedges_r2_ts            = []
    wedges_cross_power_full = []
    wedges_coherence_pos    = []
    wedges_freq_axis        = []

    for sim_dir in sim_dirs:
        r1_files = sorted(sim_dir.glob('WAVE_SIM_*_R1_Z.txt'))
        r2_files = sorted(sim_dir.glob('WAVE_SIM_*_R2_Z.txt'))
        if not r1_files or not r2_files:
            continue

        r1_processed = process_signals(np.loadtxt(r1_files[0]))
        r2_processed = process_signals(np.loadtxt(r2_files[0]))

        wedges_r1_ts.append(r1_processed)
        wedges_r2_ts.append(r2_processed)

        freqs, cross_power_full, _, coherence_pos = compute_cross_power_spectrum(
            r1_processed, r2_processed, DELTA)

        wedges_cross_power_full.append(cross_power_full)
        wedges_coherence_pos.append(coherence_pos)
        wedges_freq_axis.append(freqs)

    if not wedges_r1_ts:
        raise RuntimeError("No waveform files found")

    stacked_r1      = np.mean(wedges_r1_ts, axis=0)
    stacked_r2      = np.mean(wedges_r2_ts, axis=0)
    coh_num         = len(wedges_cross_power_full)
    cross_power_avg = np.sum(wedges_cross_power_full, axis=0) / coh_num
    coherence_avg   = moving_average_numpy(np.real(np.mean(wedges_coherence_pos, axis=0)), SMOOTH_WINDOW)
    freqs           = wedges_freq_axis[0]
    time_ccf, lags  = compute_time_ccf(cross_power_avg, DELTA)
    time_axis       = np.arange(len(stacked_r1)) * DELTA

    return {
        'ccf':      np.column_stack([lags, time_ccf]),
        'coherence':np.column_stack([freqs, coherence_avg]),
        'r1':       np.column_stack([time_axis, stacked_r1]),
        'r2':       np.column_stack([time_axis, stacked_r2]),
        'n_wedges': coh_num,
    }


# ===========================================================================
# Wedge / directory helpers
# ===========================================================================

def get_wedge_angles(az_start, az_end):
    wedges = []
    if az_end > az_start:
        az = az_start
        while az < az_end:
            wedges.append((az, az + WEDGE_SIZE))
            az += WEDGE_SIZE
    else:
        az = az_start
        while az < 360:
            wedges.append((az, az + WEDGE_SIZE))
            az += WEDGE_SIZE
        az = 0
        while az < az_end:
            wedges.append((az, az + WEDGE_SIZE))
            az += WEDGE_SIZE
    return wedges


def find_sim_dir(model_dir, theta_min, theta_max):
    matches = list(model_dir.glob(f"sim_*_ang_{theta_min}_{theta_max}_*"))
    return matches[0] if matches else None


def model_has_all_180_wedges(model_dir):
    """Check if model has all 180 wedges with waveform files present."""
    for theta_min in range(0, 360, WEDGE_SIZE):
        theta_max = theta_min + WEDGE_SIZE
        sd = find_sim_dir(model_dir, theta_min, theta_max)
        if sd is None:
            return False
        if not list(sd.glob('WAVE_SIM_*_R1_Z.txt')):
            return False
    return True


def get_sim_dirs_for_case(model_dir, case):
    """Return list of sim dirs for a coverage case (assumes model is complete)."""
    wedges   = get_wedge_angles(case['az_start'], case['az_end'])
    sim_dirs = []
    for theta_min, theta_max in wedges:
        sd = find_sim_dir(model_dir, theta_min, theta_max)
        if sd:
            sim_dirs.append(sd)
    return sim_dirs


def find_all_complete_models(family_filter=None):
    """Return list of (family_name, model_dir) for all models with 180 wedges done."""
    if not OUTPUTS_DIR.exists():
        raise RuntimeError(f"Outputs directory not found: {OUTPUTS_DIR}")

    complete = []
    family_dirs = sorted(OUTPUTS_DIR.iterdir()) if family_filter is None \
        else [OUTPUTS_DIR / family_filter]

    for fam_dir in family_dirs:
        if not fam_dir.is_dir():
            continue
        for model_dir in sorted(fam_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            if model_has_all_180_wedges(model_dir):
                complete.append((fam_dir.name, model_dir))

    return complete


# ===========================================================================
# File extraction + CPS generation
# ===========================================================================

def run_cps(cmd, workdir):
    result = subprocess.run(
        cmd, shell=True, executable='/bin/bash',
        capture_output=True, text=True, cwd=str(workdir), timeout=120
    )
    return result.returncode == 0, result.stderr


def extract_and_generate(sim_dirs, out_dir):
    src = sim_dirs[0]
    for fname in ['dfile', 'model.d', 'sdisp96.lov', 'sdisp96.ray',
                  'sregn96.egn', 'slegn96.egn', 'SDISPL.ASC', 'SDISPR.ASC']:
        f = src / fname
        if f.exists():
            shutil.copy2(f, out_dir / fname)

    if (out_dir / 'sregn96.egn').exists():
        ok, err = run_cps('module load CPS/3.30 && sdpegn96 -R -U -ASC', out_dir)
        print(f"      SREGN.ASC  : {'OK' if ok else 'FAILED'}")
        ok, _ = run_cps('module load CPS/3.30 && sdpegn96 -R -U', out_dir)
        print(f"      SREGNU.PLT : {'OK' if ok else 'FAILED'}")

    if (out_dir / 'slegn96.egn').exists():
        ok, err = run_cps('module load CPS/3.30 && sdpegn96 -L -U -ASC', out_dir)
        print(f"      SLEGN.ASC  : {'OK' if ok else 'FAILED'}")
        ok, _ = run_cps('module load CPS/3.30 && sdpegn96 -L -U', out_dir)
        print(f"      SLEGNU.PLT : {'OK' if ok else 'FAILED'}")


def concatenate_sources(sim_dirs, out_path):
    frames = []
    for sd in sim_dirs:
        for sf in sorted(sd.glob('SOURCES_*.csv')):
            try:
                lines = sf.read_text().splitlines()
                data_start = next(
                    (i for i, l in enumerate(lines) if not l.startswith('#')), 0)
                df = pd.read_csv(StringIO('\n'.join(lines[data_start:])),
                                 names=['x_km', 'y_km'])
                frames.append(df)
            except Exception as e:
                print(f"      Warning: {sf.name}: {e}")
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.to_csv(out_path, index=False)
        return combined
    return None


# ===========================================================================
# Plotting
# ===========================================================================

def plot_analysis(sources_df, results, case, out_dir, stem, dist_km):
    fig = plt.figure(figsize=(16, 10))
    gs  = fig.add_gridspec(2, 2, height_ratios=[2, 1.2], hspace=0.35, wspace=0.3)

    ax_src = fig.add_subplot(gs[0, :])
    if sources_df is not None and len(sources_df) > 0:
        x, y = sources_df['x_km'], sources_df['y_km']
        n = len(x)
        if n > 10000:
            idx = random.sample(range(n), 10000)
            x, y = x.iloc[idx], y.iloc[idx]
        ax_src.scatter(x, y, s=1.5, c='black', alpha=0.4,
                       edgecolors='none', rasterized=True)
        half = dist_km / 2
        ax_src.plot(-half, 0, 'rv', ms=10, mec='black', mew=1)
        ax_src.plot( half, 0, 'rv', ms=10, mec='black', mew=1)
        ax_src.plot([-half, half], [0, 0], 'k--', lw=1.5, alpha=0.7)
        ext = max(abs(sources_df['x_km']).max(), abs(sources_df['y_km']).max()) * 1.1
        ax_src.set_xlim(-ext, ext)
        ax_src.set_ylim(-ext, ext)
        ax_src.set_aspect('equal')
        n_src = len(sources_df)
    else:
        n_src = 0
    ax_src.set_xlabel('X (km)', fontsize=12)
    ax_src.set_ylabel('Y (km)', fontsize=12)
    ax_src.set_title(
        f"{stem}  |  Case {case['id']}: {case['description']}\n"
        f"Az: {case['az_start']}-{case['az_end']}  |  "
        f"{results['n_wedges']} wedges  |  {n_src:,} sources",
        fontsize=13, fontweight='bold')
    ax_src.grid(True, alpha=0.3)

    ax_ccf = fig.add_subplot(gs[1, 0])
    ccf  = results['ccf']
    mask = np.abs(ccf[:, 0]) <= LAG_TIME_MAX
    ax_ccf.plot(ccf[mask, 0], ccf[mask, 1], 'k-', lw=0.8)
    ax_ccf.axvline(0, color='red', ls='--', lw=1, alpha=0.5)
    ax_ccf.axhline(0, color='gray', lw=0.5, alpha=0.3)
    ax_ccf.set_xlabel('Lag Time (s)', fontsize=11)
    ax_ccf.set_ylabel('CCF Amplitude', fontsize=11)
    ax_ccf.set_title('Time-Domain Cross-Correlation', fontsize=12, fontweight='bold')
    ax_ccf.set_xlim(-LAG_TIME_MAX, LAG_TIME_MAX)
    ax_ccf.grid(True, alpha=0.3)

    ax_coh = fig.add_subplot(gs[1, 1])
    coh = results['coherence']
    ax_coh.plot(coh[:, 0], coh[:, 1], 'b-', lw=1.2)
    ax_coh.set_xlabel('Frequency (Hz)', fontsize=11)
    ax_coh.set_ylabel('Coherence', fontsize=11)
    ax_coh.set_title(f'Frequency Coherence (smoothed, window={SMOOTH_WINDOW})',
                     fontsize=12, fontweight='bold')
    ax_coh.set_xlim(0, 0.5)
    ax_coh.grid(True, alpha=0.3)

    plt.savefig(out_dir / 'sources_ccf_coherence.png', dpi=150, bbox_inches='tight')
    plt.close()


def parse_dist_from_sim(sim_dir):
    m = re.search(r'_dist_(\d+)_', sim_dir.name)
    return int(m.group(1)) if m else 200


# ===========================================================================
# Process one model
# ===========================================================================

def process_model(fam_name, model_dir):
    stem = model_dir.name

    # Pick random coverage case
    case = random.choice(CASES)
    sim_dirs = get_sim_dirs_for_case(model_dir, case)

    az_tag   = f"az{case['az_start']}-{case['az_end']}"
    out_name = f"{stem}_{case['name']}_{az_tag}"
    out_dir  = ANALYSIS_DIR / fam_name / out_name

    # Skip if already done
    if (out_dir / 'sources_ccf_coherence.png').exists():
        print(f"  SKIP (already done): {out_name}")
        return True

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Case    : {case['id']} - {case['description']}")
    print(f"  Wedges  : {len(sim_dirs)}")
    print(f"  Output  : {out_dir.name}")

    # Stack CCF
    results = stack_ccf(sim_dirs)

    # Save data
    np.savetxt(out_dir / 'stacked_time_ccf.txt',       results['ccf'],
               fmt='%.6e', header='lag_time_s CCF_amplitude')
    np.savetxt(out_dir / 'stacked_coherence_freq.txt', results['coherence'],
               fmt='%.6e', header='frequency_Hz coherence_smoothed')
    np.savetxt(out_dir / 'stacked_r1_waveform.txt',    results['r1'],
               fmt='%.6e', header='time_s amplitude')
    np.savetxt(out_dir / 'stacked_r2_waveform.txt',    results['r2'],
               fmt='%.6e', header='time_s amplitude')

    with open(out_dir / 'wedge_list.txt', 'w') as f:
        f.write(f"# Case: {case['id']} - {case['description']}\n")
        f.write(f"# Wedges: {results['n_wedges']}\n")
        for sd in sim_dirs:
            f.write(f"{sd.name}\n")

    # Sources
    sources_df = concatenate_sources(sim_dirs, out_dir / 'all_sources.csv')

    # Eigenfunction files + CPS outputs
    extract_and_generate(sim_dirs, out_dir)

    # Plot
    dist_km = parse_dist_from_sim(sim_dirs[0])
    plot_analysis(sources_df, results, case, out_dir, stem, dist_km)

    return True


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--family', type=str, default=None,
                        help='Only process this family (e.g. CIA)')
    args = parser.parse_args()

    print("=" * 70)
    print("CCF ANALYSIS - EXPERIMENT 17 - ALL COMPLETE MODELS")
    print("=" * 70)
    print()

    print("Scanning for models with all 180 wedges complete...")
    complete_models = find_all_complete_models(family_filter=args.family)
    print(f"Found {len(complete_models)} complete models\n")

    if not complete_models:
        print("No complete models found. Check back when more simulations finish.")
        sys.exit(0)

    done    = 0
    skipped = 0
    failed  = 0

    for i, (fam_name, model_dir) in enumerate(complete_models):
        print(f"[{i+1}/{len(complete_models)}] {fam_name} / {model_dir.name}")
        try:
            result = process_model(fam_name, model_dir)
            if result:
                done += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1
        print()

    print("=" * 70)
    print("DONE")
    print(f"  Processed : {done}")
    print(f"  Failed    : {failed}")
    print(f"  Output    : {ANALYSIS_DIR}")
    print("=" * 70)


if __name__ == '__main__':
    main()