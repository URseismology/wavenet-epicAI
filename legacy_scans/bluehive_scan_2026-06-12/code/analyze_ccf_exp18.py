#!/usr/bin/env python3
"""
Analysis for Experiment 18 - Source count comparison.

Processes all 3 sim output folders:
  sim_00001 -> 100k sources
  sim_00002 -> 500k sources
  sim_00003 -> 1M sources

For each:
  1. Waveform plot (R1 top, R2 bottom)
  2. CCF time domain
  3. CCF frequency domain (coherence)
  4. FTAN with extracted + theoretical dispersion

Generates SREGN.ASC if missing.

Usage:
    python analyze_exp18.py
"""

import os
import re
import sys
import shutil
import subprocess
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.signal import windows, detrend
from scipy.ndimage import gaussian_filter1d
import scipy.interpolate

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT       = Path('experiments/experiment_18')
OUTPUT_DIR = ROOT / 'outputs'

SOURCE_LABELS = {
    'sim_00001': '100k',
    'sim_00002': '500k',
    'sim_00003': '1mil',
}

# ---------------------------------------------------------------------------
# CCF parameters
# ---------------------------------------------------------------------------
DELTA               = 0.5
FILTER_PERIOD_RANGE = [2.0, 100.0]
FILTER_FREQ_RANGE   = [1.0 / FILTER_PERIOD_RANGE[1], 1.0 / FILTER_PERIOD_RANGE[0]]
TAPER_WIDTH         = 0.2
LAG_TIME_MAX        = 100.0
SMOOTH_WINDOW       = 10

FTAN_FMIN = 0.05
FTAN_FMAX = 1.0
FTAN_VMIN = 0.5
FTAN_VMAX = 4.5


# ===========================================================================
# CCF functions - exact from compute_ccf.py
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
    return freqs[pos_mask], cross_power_full, coherence_full[pos_mask]


def bandpass_filter_freq(fft_data, freq_range, dt):
    n = len(fft_data)
    freqs = np.fft.fftfreq(n, dt)
    filt = np.zeros(n)
    f_min, f_max = freq_range
    tw = TAPER_WIDTH * (f_max - f_min)
    for i, f in enumerate(freqs):
        af = abs(f)
        if af < f_min - tw or af > f_max + tw:
            filt[i] = 0.0
        elif f_min + tw <= af <= f_max - tw:
            filt[i] = 1.0
        elif af < f_min + tw:
            filt[i] = 0.5 * (1 - np.cos(np.pi * (af - f_min + tw) / (2 * tw)))
        else:
            filt[i] = 0.5 * (1 + np.cos(np.pi * (af - f_max + tw) / (2 * tw)))
    return fft_data * filt


def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    tl = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:tl]  = 0.5 * (1 - np.cos(np.pi * np.arange(tl) / tl))
    taper[-tl:] = 0.5 * (1 - np.cos(np.pi * np.arange(tl, 0, -1) / tl))
    return data * taper


def compute_time_ccf(cross_power_avg, dt):
    N = len(cross_power_avg)
    ccf = np.fft.fftshift(np.fft.ifft(cross_power_avg, N).real)
    ccf = cosine_taper(detrend(ccf))
    ccf_filt = bandpass_filter_freq(np.fft.fft(np.fft.fftshift(ccf)), FILTER_FREQ_RANGE, dt)
    ccf_out = np.fft.fftshift(np.fft.ifft(ccf_filt).real)
    lags = (np.arange(N) - np.floor(N / 2)) * dt
    return ccf_out, lags


# ===========================================================================
# FTAN
# ===========================================================================

def compute_ftan(ccf_lags, ccf_amp, distance_km):
    npts = len(ccf_amp)
    indx = npts // 2
    data = 0.5 * ccf_amp[indx:] + 0.5 * np.flip(ccf_amp[:indx + 1], axis=0)

    pt1 = int(distance_km / FTAN_VMAX / DELTA)
    pt2 = int(distance_km / FTAN_VMIN / DELTA)
    if pt1 == 0: pt1 = 10
    if pt2 > (npts // 2): pt2 = npts // 2

    idx  = np.arange(pt1, pt2)
    tvec = idx * DELTA
    data = data[idx]

    cwt, _, freq, _, _, _ = pycwt.cwt(data, DELTA, 1/24, -1, -1, 'morlet')
    freq_ind = np.where((freq >= FTAN_FMIN) & (freq <= FTAN_FMAX))[0]
    cwt  = cwt[freq_ind]
    freq = freq[freq_ind]

    period = 1 / freq
    rcwt   = np.abs(cwt) ** 2

    per = np.arange(int(1 / FTAN_FMAX), int(1 / FTAN_FMIN), 0.25)
    vel = np.arange(FTAN_VMIN, FTAN_VMAX, 0.01)

    vel_data = distance_km / tvec
    fc       = scipy.interpolate.interp2d(vel_data, period, rcwt, kind='linear')
    rcwt_new = fc(vel, per)

    for ii in range(len(per)):
        mx = rcwt_new[ii].max()
        if mx > 0: rcwt_new[ii] /= mx

    for j in range(len(vel)):
        rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)

    return per, vel, rcwt_new


def extract_curve(per, vel, ftan_amp):
    nper, gv = [], []
    for ii in range(len(per)):
        idx = np.argmax(ftan_amp[ii])
        if ftan_amp[ii, idx] > 0.5:
            nper.append(per[ii])
            gv.append(vel[idx])
    return np.array(nper), np.array(gv)


def read_sregn(sregn_file):
    data = np.loadtxt(sregn_file, skiprows=1)
    mask = data[:, 0] == 0
    return data[mask, 2], data[mask, 5]


def generate_sregn(sim_dir):
    """Generate SREGN.ASC in sim_dir if missing."""
    sregn = sim_dir / 'SREGN.ASC'
    if sregn.exists():
        return sregn

    model_d = sim_dir / 'model.d'
    if not model_d.exists():
        print(f"    WARNING: model.d not found in {sim_dir.name}")
        return None

    print(f"    Generating SREGN.ASC...")
    for cmd in ['module load CPS/3.30 && sdpegn96 -R -U -ASC',
                'sdpegn96 -R -U -ASC']:
        result = subprocess.run(cmd, shell=True, executable='/bin/bash',
                                capture_output=True, text=True, cwd=str(sim_dir), timeout=60)
        if result.returncode == 0 and sregn.exists():
            print(f"    SREGN.ASC generated")
            return sregn

    # If sdpegn96 fails, run full CPS pipeline
    dfile = sim_dir / 'dfile'
    if not dfile.exists():
        print(f"    WARNING: dfile not found, cannot generate SREGN.ASC")
        return None

    cmds = [
        'module load CPS/3.30',
        f'sprep96 -M model.d -HS 0 -HR 0 -L -R -NMOD 10 -d dfile',
        'sdisp96',
        'sregn96 -NOQ',
        'sdpegn96 -R -U -ASC',
    ]
    full_cmd = ' && '.join(cmds)
    result = subprocess.run(full_cmd, shell=True, executable='/bin/bash',
                            capture_output=True, text=True, cwd=str(sim_dir), timeout=300)
    if sregn.exists():
        print(f"    SREGN.ASC generated via full pipeline")
        return sregn

    print(f"    WARNING: Could not generate SREGN.ASC")
    return None


# ===========================================================================
# Plotting
# ===========================================================================

def plot_waveforms(r1, r2, time_axis, sim_dir, label):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(f'Waveforms — {label} sources', fontsize=14, fontweight='bold')

    ax1.plot(time_axis, r1, 'b-', lw=0.6)
    ax1.set_ylabel('Amplitude', fontsize=11)
    ax1.set_title('Receiver 1 (R1) — Z component', fontsize=11)
    ax1.grid(True, alpha=0.3)

    ax2.plot(time_axis, r2, 'g-', lw=0.6)
    ax2.set_xlabel('Time (s)', fontsize=11)
    ax2.set_ylabel('Amplitude', fontsize=11)
    ax2.set_title('Receiver 2 (R2) — Z component', fontsize=11)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(sim_dir / f'waveforms_{label}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"    Saved: waveforms_{label}.png")


def plot_ccf_time(lags, ccf, sim_dir, label):
    fig, ax = plt.subplots(figsize=(12, 5))
    mask = np.abs(lags) <= LAG_TIME_MAX
    ax.plot(lags[mask], ccf[mask], 'k-', lw=0.8)
    ax.axvline(0, color='red', ls='--', lw=1, alpha=0.5)
    ax.axhline(0, color='gray', lw=0.5, alpha=0.3)
    ax.set_xlabel('Lag Time (s)', fontsize=12)
    ax.set_ylabel('CCF Amplitude', fontsize=12)
    ax.set_title(f'Time-Domain CCF — {label} sources', fontsize=13, fontweight='bold')
    ax.set_xlim(-LAG_TIME_MAX, LAG_TIME_MAX)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(sim_dir / f'ccf_time_{label}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"    Saved: ccf_time_{label}.png")


def plot_ccf_freq(freqs, coherence, sim_dir, label):
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(freqs, coherence, 'b-', lw=1.2)
    ax.set_xlabel('Frequency (Hz)', fontsize=12)
    ax.set_ylabel('Coherence', fontsize=12)
    ax.set_title(f'Frequency-Domain Coherence — {label} sources (smoothed, w={SMOOTH_WINDOW})',
                 fontsize=13, fontweight='bold')
    ax.set_xlim(0, 0.5)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(sim_dir / f'ccf_freq_{label}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"    Saved: ccf_freq_{label}.png")


def plot_ftan(per, vel, ftan_amp, per_picked, gv_picked, theory_per, theory_gvel,
              sim_dir, label, dist_km):
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_facecolor('#0d0d0d')
    fig.patch.set_facecolor('#0d0d0d')

    ax.imshow(np.transpose(ftan_amp), cmap='inferno', aspect='auto',
              origin='lower', extent=[per[0], per[-1], vel[0], vel[-1]],
              vmin=0, vmax=1)

    mask = (theory_per >= per[0]) & (theory_per <= min(50, per[-1]))
    ax.plot(theory_per[mask], theory_gvel[mask], 'c-', lw=2, label='Theory (SREGN)')

    if len(per_picked) > 0:
        mask_ext = per_picked <= 50
        ax.plot(per_picked[mask_ext], gv_picked[mask_ext], '--',
                color='#ff9900', lw=2, label='Extracted')
        rms = np.sqrt(np.mean((gv_picked[mask_ext] -
                               np.interp(per_picked[mask_ext], theory_per, theory_gvel))**2))
        rel = rms / np.mean(np.interp(per_picked[mask_ext], theory_per, theory_gvel)) * 100
        title = f'FTAN — {label} sources  |  dist={dist_km} km  |  RMS={rel:.1f}%'
    else:
        title = f'FTAN — {label} sources  |  dist={dist_km} km'

    ax.set_xlim(per[0], min(50, per[-1]))
    ax.set_xlabel('Period (s)', color='white', fontsize=12)
    ax.set_ylabel('Group Velocity (km/s)', color='white', fontsize=12)
    ax.set_title(title, color='white', fontsize=13, fontweight='bold')
    ax.tick_params(colors='white')
    for sp in ax.spines.values(): sp.set_edgecolor('#444444')
    ax.grid(True, alpha=0.15, color='white')
    legend = ax.legend(fontsize=10, facecolor='#222222', labelcolor='white', framealpha=0.5)

    plt.tight_layout()
    plt.savefig(sim_dir / f'ftan_{label}.png', dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"    Saved: ftan_{label}.png")


# ===========================================================================
# Process one sim folder
# ===========================================================================

def process_sim(sim_dir, label):
    print(f"\n{'='*60}")
    print(f"Processing: {sim_dir.name}  ({label} sources)")
    print('='*60)

    # Find waveform files
    r1_files = sorted(sim_dir.glob('WAVE_SIM_*_R1_Z.txt'))
    r2_files = sorted(sim_dir.glob('WAVE_SIM_*_R2_Z.txt'))
    if not r1_files or not r2_files:
        print(f"  ERROR: Waveform files not found in {sim_dir.name}")
        return

    r1_raw = np.loadtxt(r1_files[0])
    r2_raw = np.loadtxt(r2_files[0])
    time_axis = np.arange(len(r1_raw)) * DELTA

    print(f"  Waveform length: {len(r1_raw)} samples ({len(r1_raw)*DELTA:.0f}s)")

    # Parse distance from sim folder name
    m = re.search(r'_dist_(\d+)_', sim_dir.name)
    dist_km = int(m.group(1)) if m else 200
    print(f"  Distance: {dist_km} km")

    # Plot waveforms
    print("  Plotting waveforms...")
    plot_waveforms(r1_raw, r2_raw, time_axis, sim_dir, label)

    # Process signals for CCF
    r1 = process_signals(r1_raw)
    r2 = process_signals(r2_raw)

    freqs, cross_power_full, coherence_pos = compute_cross_power_spectrum(r1, r2, DELTA)
    coherence_smoothed = moving_average_numpy(np.real(coherence_pos), SMOOTH_WINDOW)
    ccf, lags = compute_time_ccf(cross_power_full, DELTA)

    # Plot CCF time domain
    print("  Plotting CCF (time domain)...")
    plot_ccf_time(lags, ccf, sim_dir, label)

    # Plot CCF frequency domain
    print("  Plotting CCF (frequency domain)...")
    plot_ccf_freq(freqs, coherence_smoothed, sim_dir, label)

    # FTAN
    print("  Computing FTAN...")
    # Use symmetric CCF for FTAN
    npts = len(ccf)
    indx = npts // 2
    ccf_sym = 0.5 * ccf[indx:] + 0.5 * np.flip(ccf[:indx + 1], axis=0)
    lags_pos = np.arange(len(ccf_sym)) * DELTA

    per, vel, ftan_amp = compute_ftan(lags, ccf, dist_km)
    per_picked, gv_picked = extract_curve(per, vel, ftan_amp)
    print(f"  Extracted {len(per_picked)} dispersion points")

    # Get SREGN
    sregn_file = generate_sregn(sim_dir)
    if sregn_file:
        theory_per, theory_gvel = read_sregn(sregn_file)
    else:
        theory_per, theory_gvel = np.array([]), np.array([])

    # Plot FTAN
    print("  Plotting FTAN...")
    plot_ftan(per, vel, ftan_amp, per_picked, gv_picked,
              theory_per, theory_gvel, sim_dir, label, dist_km)

    print(f"  Done: {sim_dir.name}")


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 60)
    print("EXPERIMENT 18 - SOURCE COUNT ANALYSIS")
    print("=" * 60)

    if not OUTPUT_DIR.exists():
        print(f"ERROR: Output directory not found: {OUTPUT_DIR}")
        sys.exit(1)

    # Find all sim directories
    all_sim_dirs = sorted(OUTPUT_DIR.glob('sim_*'))
    if not all_sim_dirs:
        print(f"ERROR: No sim_* directories found in {OUTPUT_DIR}")
        sys.exit(1)

    print(f"\nFound {len(all_sim_dirs)} sim directories:")
    for sd in all_sim_dirs:
        # Match sim ID
        m = re.match(r'sim_(\d+)', sd.name)
        if m:
            sim_id = f"sim_{m.group(1).zfill(5)}"
            label = SOURCE_LABELS.get(sim_id, f'sim_{m.group(1)}')
            print(f"  {sd.name} -> {label} sources")

    for sd in all_sim_dirs:
        m = re.match(r'sim_(\d+)', sd.name)
        if not m:
            continue
        sim_id = f"sim_{m.group(1).zfill(5)}"
        label = SOURCE_LABELS.get(sim_id, f'sim_{m.group(1)}')
        process_sim(sd, label)

    print("\n" + "=" * 60)
    print("ALL DONE")
    print("=" * 60)


if __name__ == '__main__':
    main()