#!/usr/bin/env python3
"""
Analyze experiment 6 using 100 randomly selected wedges (1M sources total).
Each wedge has 10,000 sources -> 100 wedges = 1,000,000 sources.

Produces:
  1. Waveform plot (R1 top, R2 bottom)
  2. CCF time domain
  3. CCF frequency domain (coherence)
  4. FTAN with extracted + theoretical dispersion

Usage:
    python analyze_exp6_1M.py
"""

import os
import re
import sys
import random
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
EXP_DIR    = Path('experiments/experiment_6/outputs')
OUTPUT_DIR = Path('experiments/experiment_6/analysis_1M')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

N_WEDGES       = 100      # 100 wedges x 10,000 sources = 1,000,000
SOURCES_TOTAL  = N_WEDGES * 10000
LABEL          = '1mil'

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


# ===========================================================================
# Plotting
# ===========================================================================

def plot_waveforms(r1, r2, time_axis):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(f'Waveforms — {LABEL} sources ({N_WEDGES} wedges × 10,000)',
                 fontsize=14, fontweight='bold')
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
    plt.savefig(OUTPUT_DIR / f'waveforms_{LABEL}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: waveforms_{LABEL}.png")


def plot_ccf_time(lags, ccf):
    fig, ax = plt.subplots(figsize=(12, 5))
    mask = np.abs(lags) <= LAG_TIME_MAX
    ax.plot(lags[mask], ccf[mask], 'k-', lw=0.8)
    ax.axvline(0, color='red', ls='--', lw=1, alpha=0.5)
    ax.axhline(0, color='gray', lw=0.5, alpha=0.3)
    ax.set_xlabel('Lag Time (s)', fontsize=12)
    ax.set_ylabel('CCF Amplitude', fontsize=12)
    ax.set_title(f'Time-Domain CCF — {LABEL} sources', fontsize=13, fontweight='bold')
    ax.set_xlim(-LAG_TIME_MAX, LAG_TIME_MAX)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / f'ccf_time_{LABEL}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: ccf_time_{LABEL}.png")


def plot_ccf_freq(freqs, coherence):
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(freqs, coherence, 'b-', lw=1.2)
    ax.set_xlabel('Frequency (Hz)', fontsize=12)
    ax.set_ylabel('Coherence', fontsize=12)
    ax.set_title(f'Frequency-Domain Coherence — {LABEL} sources (smoothed, w={SMOOTH_WINDOW})',
                 fontsize=13, fontweight='bold')
    ax.set_xlim(0, 0.5)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / f'ccf_freq_{LABEL}.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: ccf_freq_{LABEL}.png")


def plot_ftan(per, vel, ftan_amp, per_picked, gv_picked, theory_per, theory_gvel, dist_km):
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_facecolor('#0d0d0d')
    fig.patch.set_facecolor('#0d0d0d')

    ax.imshow(np.transpose(ftan_amp), cmap='inferno', aspect='auto',
              origin='lower', extent=[per[0], per[-1], vel[0], vel[-1]],
              vmin=0, vmax=1)

    if len(theory_per) > 0:
        mask = (theory_per >= per[0]) & (theory_per <= min(50, per[-1]))
        ax.plot(theory_per[mask], theory_gvel[mask], 'c-', lw=2, label='Theory (SREGN)')

    if len(per_picked) > 0:
        mask_ext = per_picked <= 50
        ax.plot(per_picked[mask_ext], gv_picked[mask_ext], '--',
                color='#ff9900', lw=2, label='Extracted')
        if len(theory_per) > 0:
            rms = np.sqrt(np.mean((gv_picked[mask_ext] -
                                   np.interp(per_picked[mask_ext], theory_per, theory_gvel))**2))
            rel = rms / np.mean(np.interp(per_picked[mask_ext], theory_per, theory_gvel)) * 100
            title = f'FTAN — {LABEL} sources  |  dist={dist_km} km  |  RMS={rel:.1f}%'
        else:
            title = f'FTAN — {LABEL} sources  |  dist={dist_km} km'
    else:
        title = f'FTAN — {LABEL} sources  |  dist={dist_km} km'

    ax.set_xlim(per[0], min(50, per[-1]))
    ax.set_xlabel('Period (s)', color='white', fontsize=12)
    ax.set_ylabel('Group Velocity (km/s)', color='white', fontsize=12)
    ax.set_title(title, color='white', fontsize=13, fontweight='bold')
    ax.tick_params(colors='white')
    for sp in ax.spines.values(): sp.set_edgecolor('#444444')
    ax.grid(True, alpha=0.15, color='white')
    ax.legend(fontsize=10, facecolor='#222222', labelcolor='white', framealpha=0.5)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / f'ftan_{LABEL}.png', dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Saved: ftan_{LABEL}.png")


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 60)
    print(f"EXPERIMENT 6 — {LABEL} SOURCES ANALYSIS")
    print(f"Randomly selecting {N_WEDGES} wedges x 10,000 = {SOURCES_TOTAL:,} sources")
    print("=" * 60)

    # Find all sim dirs
    all_sim_dirs = sorted(EXP_DIR.glob('sim_*'))
    if not all_sim_dirs:
        print(f"ERROR: No sim_* dirs found in {EXP_DIR}")
        sys.exit(1)

    print(f"\nTotal wedges available: {len(all_sim_dirs)}")

    # Filter to only those with waveform files
    valid_dirs = [d for d in all_sim_dirs if list(d.glob('WAVE_SIM_*_R1_Z.txt'))]
    print(f"Wedges with waveform files: {len(valid_dirs)}")

    def is_good_wedge(sim_dir):
        """Good wedges: Fresnel zone — 0-30, 150-210, 330-360 deg."""
        m = re.search(r'_ang_(\d+)_(\d+)_', sim_dir.name)
        if not m:
            return False
        az_start = int(m.group(1))
        az_end   = int(m.group(2))
        az_mid   = (az_start + az_end) / 2.0
        return (az_mid <= 30) or (150 <= az_mid <= 210) or (az_mid >= 330)

    good_dirs = [d for d in valid_dirs if is_good_wedge(d)]
    bad_dirs  = [d for d in valid_dirs if not is_good_wedge(d)]
    print(f"Good wedges (Fresnel zone): {len(good_dirs)}")
    print(f"Bad wedges (off-axis):      {len(bad_dirs)}")

    n_good = int(N_WEDGES * 0.20)   # 20 good
    n_bad  = N_WEDGES - n_good       # 80 bad

    random.seed(42)
    sel_good = random.sample(good_dirs, min(n_good, len(good_dirs)))
    sel_bad  = random.sample(bad_dirs,  min(n_bad,  len(bad_dirs)))
    selected = sorted(sel_bad + sel_good)

    print(f"Selected {len(sel_bad)} bad + {len(sel_good)} good = {len(selected)} wedges")

    # Parse distance from first sim dir
    m = re.search(r'_dist_(\d+)_', selected[0].name)
    dist_km = int(m.group(1)) if m else 50
    print(f"Distance: {dist_km} km\n")

    # Stack waveforms and compute CCF
    print("Stacking waveforms...")
    r1_list, r2_list, cp_list, coh_list, freq_ref = [], [], [], [], None

    for sd in selected:
        r1_files = sorted(sd.glob('WAVE_SIM_*_R1_Z.txt'))
        r2_files = sorted(sd.glob('WAVE_SIM_*_R2_Z.txt'))
        if not r1_files or not r2_files:
            continue
        r1 = process_signals(np.loadtxt(r1_files[0]))
        r2 = process_signals(np.loadtxt(r2_files[0]))
        r1_list.append(r1)
        r2_list.append(r2)
        freqs, cp_full, coh_pos = compute_cross_power_spectrum(r1, r2, DELTA)
        cp_list.append(cp_full)
        coh_list.append(coh_pos)
        if freq_ref is None:
            freq_ref = freqs

    r1_stack = np.mean(r1_list, axis=0)
    r2_stack = np.mean(r2_list, axis=0)
    cp_avg   = np.sum(cp_list, axis=0) / len(cp_list)
    coh_avg  = moving_average_numpy(np.real(np.mean(coh_list, axis=0)), SMOOTH_WINDOW)
    ccf, lags = compute_time_ccf(cp_avg, DELTA)
    time_axis = np.arange(len(r1_stack)) * DELTA

    print(f"  Stacked {len(r1_list)} wedges")

    # Find SREGN.ASC
    theory_per, theory_gvel = np.array([]), np.array([])
    final_dirs = list(EXP_DIR.glob('final_dist_*'))
    if final_dirs:
        sregn = final_dirs[0] / 'SREGN.ASC'
        if sregn.exists():
            theory_per, theory_gvel = read_sregn(sregn)
            print(f"  Loaded SREGN.ASC from {final_dirs[0].name}")

    # Plots
    print("\nGenerating plots...")
    plot_waveforms(r1_stack, r2_stack, time_axis)
    plot_ccf_time(lags, ccf)
    plot_ccf_freq(freq_ref, coh_avg)

    print("  Computing FTAN...")
    per, vel, ftan_amp = compute_ftan(lags, ccf, dist_km)
    per_picked, gv_picked = extract_curve(per, vel, ftan_amp)
    print(f"  Extracted {len(per_picked)} dispersion points")
    plot_ftan(per, vel, ftan_amp, per_picked, gv_picked, theory_per, theory_gvel, dist_km)

    print(f"\nDone. Results saved to: {OUTPUT_DIR}")


if __name__ == '__main__':
    main()
    