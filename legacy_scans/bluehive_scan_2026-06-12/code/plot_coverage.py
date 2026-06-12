#!/usr/bin/env python3
"""
5-panel diagnostic plot for a coverage analysis output folder.

Layout:
  [Source Distribution] [SREGN vs Extracted] [Full FTAN + curve]
  [Vp depth profile   ] [Vs depth profile  ] [                 ]

Usage:
    python plot_coverage_analysis.py <coverage_analysis_case_dir>

Example:
    python plot_coverage_analysis.py experiments/experiment_17/coverage_analysis/CIA/CIA_0042_full_az0-360
"""

import sys
import re
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path
import scipy.interpolate
from scipy.ndimage import gaussian_filter1d

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_SUITE_DIR  = Path('experiments/model_suite')
PERTURBED_DIR    = Path('experiments/perturbed_models')

FTAN_FMIN  = 0.05
FTAN_FMAX  = 1.0
FTAN_VMIN  = 0.5
FTAN_VMAX  = 4.5
DT         = 0.5

FAMILY_COLORS = {
    'CIA':         '#e41a1c',
    'CUS':         '#377eb8',
    'Continental': '#4daf4a',
    'Craton':      '#984ea3',
    'Interior':    '#ff7f00',
    'KOREA':       '#a65628',
    'Rift':        '#f781bf',
    'Shield':      '#999999',
    'WUS':         '#ffff33',
    'tak135sph':   '#00ced1',
}


# ===========================================================================
# Readers
# ===========================================================================

def read_sregn(sregn_file):
    """Return mode-0 (period, group_velocity) arrays."""
    data = np.loadtxt(sregn_file, skiprows=1)
    mask = data[:, 0] == 0
    return data[mask, 2], data[mask, 5]


def read_mod_file(mod_file):
    """Return (depths, vp, vs) step-function arrays for plotting."""
    with open(mod_file, 'r') as f:
        lines = f.readlines()
    data_start = None
    for i, line in enumerate(lines):
        if 'H(KM)' in line or 'H (KM)' in line:
            data_start = i + 1
            break
    if data_start is None:
        data_start = 0
    depths, vp_vals, vs_vals = [], [], []
    current_depth = 0.0
    for line in lines[data_start:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            h   = float(parts[0])
            vp  = float(parts[1])
            vs  = float(parts[2])
        except ValueError:
            continue
        depths.append(current_depth);  vp_vals.append(vp);  vs_vals.append(vs)
        ext = current_depth + (h if h > 0 else 60.0)
        depths.append(ext);            vp_vals.append(vp);  vs_vals.append(vs)
        if h > 0:
            current_depth += h
    return np.array(depths), np.array(vp_vals), np.array(vs_vals)


def compute_ftan(ccf_lags, ccf_amp, distance_km):
    """Return (periods, velocities, ftan_amp, tvec, data_trimmed)."""
    npts  = len(ccf_amp)
    indx  = npts // 2
    data  = 0.5 * ccf_amp[indx:] + 0.5 * np.flip(ccf_amp[:indx + 1], axis=0)

    pt1 = int(distance_km / FTAN_VMAX / DT)
    pt2 = int(distance_km / FTAN_VMIN / DT)
    if pt1 == 0:
        pt1 = 10
    if pt2 > (npts // 2):
        pt2 = npts // 2

    idx  = np.arange(pt1, pt2)
    tvec = idx * DT
    data = data[idx]

    cwt, _, freq, _, _, _ = pycwt.cwt(data, DT, 1/24, -1, -1, 'morlet')

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
        if mx > 0:
            rcwt_new[ii] /= mx

    for j in range(len(vel)):
        rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)

    return per, vel, rcwt_new, tvec, data


def extract_curve(per, vel, ftan_amp):
    """Return (periods_picked, gvel_picked) from argmax."""
    nper, gv = [], []
    for ii in range(len(per)):
        idx = np.argmax(ftan_amp[ii])
        if ftan_amp[ii, idx] > 0.5:
            nper.append(per[ii])
            gv.append(vel[idx])
    return np.array(nper), np.array(gv)


# ===========================================================================
# Parse helpers
# ===========================================================================

def parse_dist_from_sim(sim_dir_name):
    m = re.search(r'_dist_(\d+)_', sim_dir_name)
    return int(m.group(1)) if m else None


def get_wedge_dirs(model_output_dir, case_az_start, case_az_end, wedge_size=2):
    """Return sim dirs for the wedges in this coverage case."""
    wedges = []
    if case_az_end > case_az_start:
        az = case_az_start
        while az < case_az_end:
            wedges.append((az, az + wedge_size))
            az += wedge_size
    else:
        az = case_az_start
        while az < 360:
            wedges.append((az, az + wedge_size))
            az += wedge_size
        az = 0
        while az < case_az_end:
            wedges.append((az, az + wedge_size))
            az += wedge_size
    dirs = []
    for t_min, t_max in wedges:
        matches = list(model_output_dir.glob(f"sim_*_ang_{t_min}_{t_max}_*"))
        if matches:
            dirs.append(matches[0])
    return dirs


# ===========================================================================
# Main plot
# ===========================================================================

def make_plot(case_dir):
    case_dir = Path(case_dir)
    if not case_dir.exists():
        print(f"ERROR: {case_dir} not found")
        sys.exit(1)

    # ---- Parse case dir name -----------------------------------------------
    # e.g. CIA_0042_full_az0-360
    stem_match = re.match(r'(.+?)_(full|hemisphere|narrow_N2?|NNE|NE_E|NE_ESE|E_S|NW|W_NW)_az(\d+)-(\d+)', case_dir.name)
    if not stem_match:
        print(f"ERROR: Could not parse case dir name: {case_dir.name}")
        sys.exit(1)

    model_stem  = stem_match.group(1)           # e.g. CIA_0042
    case_name   = stem_match.group(2)
    az_start    = int(stem_match.group(3))
    az_end      = int(stem_match.group(4))
    family      = re.sub(r'_[0-9]{4}$', '', model_stem)   # e.g. CIA

    print(f"Model  : {model_stem}  ({family})")
    print(f"Case   : {case_name}  az {az_start}-{az_end}")

    # ---- Locate files -------------------------------------------------------
    ccf_file   = case_dir / 'stacked_time_ccf.txt'
    sregn_file = case_dir / 'SREGN.ASC'
    model_d    = case_dir / 'model.d'
    sources_csv= case_dir / 'all_sources.csv'

    for f, name in [(ccf_file, 'stacked_time_ccf.txt'),
                    (sregn_file, 'SREGN.ASC'),
                    (model_d,    'model.d'),
                    (sources_csv,'all_sources.csv')]:
        if not f.exists():
            print(f"ERROR: {name} not found in {case_dir}")
            sys.exit(1)

    # ---- Load data ----------------------------------------------------------
    ccf_data    = np.loadtxt(ccf_file)
    ccf_lags    = ccf_data[:, 0]
    ccf_amp     = ccf_data[:, 1]

    theory_per, theory_gvel = read_sregn(sregn_file)

    import pandas as pd
    sources_df = pd.read_csv(sources_csv)

    # Distance from sim folder name
    model_output_dir = (case_dir.parents[2] / 'outputs' / family / model_stem)
    dist_km = None
    if model_output_dir.exists():
        sim_dirs = list(model_output_dir.glob('sim_*'))
        if sim_dirs:
            dist_km = parse_dist_from_sim(sim_dirs[0].name)
    if dist_km is None:
        dist_km = 200
        print(f"WARNING: Could not detect distance, using {dist_km} km")

    print(f"Distance: {dist_km} km")

    # ---- Compute FTAN -------------------------------------------------------
    print("Computing FTAN...")
    per, vel, ftan_amp, tvec, data_trim = compute_ftan(ccf_lags, ccf_amp, dist_km)
    per_picked, gv_picked = extract_curve(per, vel, ftan_amp)

    # ---- Load all base models -----------------------------------------------
    base_models = {}
    if MODEL_SUITE_DIR.exists():
        for mf in sorted(MODEL_SUITE_DIR.glob('*.mod')):
            fam = mf.stem
            d, vp, vs = read_mod_file(mf)
            base_models[fam] = (d, vp, vs)
    else:
        print(f"WARNING: {MODEL_SUITE_DIR} not found — skipping Vp/Vs panels")

    # Current model depth profile
    curr_depth, curr_vp, curr_vs = read_mod_file(model_d)

    # ---- Figure layout ------------------------------------------------------
    fig = plt.figure(figsize=(20, 12), facecolor='#0d0d0d')
    gs  = gridspec.GridSpec(2, 3, figure=fig,
                            left=0.06, right=0.97,
                            top=0.90, bottom=0.07,
                            wspace=0.35, hspace=0.45)

    ax_src   = fig.add_subplot(gs[0, 0])   # top-left:    source distribution
    ax_sregn = fig.add_subplot(gs[0, 1])   # top-mid:     SREGN vs extracted
    ax_ftan  = fig.add_subplot(gs[0:, 2])  # right (full height): FTAN
    ax_vp    = fig.add_subplot(gs[1, 0])   # bottom-left: Vp profile

    # Bottom-middle: CCF time (top) and coherence (bottom)
    gs_mid      = gridspec.GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[1, 1], hspace=0.55)
    ax_ccf_time = fig.add_subplot(gs_mid[0])
    ax_coh      = fig.add_subplot(gs_mid[1])

    dark_ax = [ax_src, ax_sregn, ax_ftan, ax_vp, ax_ccf_time, ax_coh]
    for ax in dark_ax:
        ax.set_facecolor('#111111')
        ax.tick_params(colors='white', labelsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor('#444444')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')
        ax.grid(True, alpha=0.15, color='white')

    # ---- Panel 1: Source distribution ---------------------------------------
    x, y = sources_df['x_km'], sources_df['y_km']
    n = len(x)
    if n > 10000:
        import random
        idx = random.sample(range(n), 10000)
        x, y = x.iloc[idx], y.iloc[idx]
    ax_src.scatter(x, y, s=1, c='white', alpha=0.3, edgecolors='none', rasterized=True)
    half = dist_km / 2
    ax_src.plot(-half, 0, 'rv', ms=8, mec='white', mew=0.5)
    ax_src.plot( half, 0, 'rv', ms=8, mec='white', mew=0.5)
    ax_src.plot([-half, half], [0, 0], 'w--', lw=1, alpha=0.6)
    ax_src.set_xlabel('X (km)', fontsize=9)
    ax_src.set_ylabel('Y (km)', fontsize=9)
    ax_src.set_title('Source Distribution', fontsize=10)
    ax_src.set_aspect('equal')

    # ---- Panel 2: SREGN vs extracted curve ----------------------------------
    mask_theory = theory_per <= 50
    ax_sregn.plot(theory_per[mask_theory], theory_gvel[mask_theory], 'c-', lw=2, label='Theory (SREGN)')
    if len(per_picked) > 0:
        mask_ext = per_picked <= 50
        ax_sregn.plot(per_picked[mask_ext], gv_picked[mask_ext], '--', color='#ff9900', lw=2, label='Extracted')
        rms = np.sqrt(np.mean((gv_picked - np.interp(per_picked, theory_per, theory_gvel))**2))
        rel = rms / np.mean(np.interp(per_picked, theory_per, theory_gvel)) * 100
        ax_sregn.set_title(f'Dispersion  RMS={rel:.1f}%', fontsize=10)
    else:
        ax_sregn.set_title('Dispersion', fontsize=10)
    ax_sregn.set_xlabel('Period (s)', fontsize=9)
    ax_sregn.set_ylabel('Group Velocity (km/s)', fontsize=9)
    ax_sregn.legend(fontsize=8, facecolor='#222222', labelcolor='white', framealpha=0.5)

    # ---- Panel 3: FTAN (right, full height) ---------------------------------
    ax_ftan.imshow(np.transpose(ftan_amp),
                   cmap='inferno', aspect='auto', origin='lower',
                   extent=[per[0], per[-1], vel[0], vel[-1]],
                   vmin=0, vmax=1)
    # Theory curve
    mask = (theory_per >= per[0]) & (theory_per <= per[-1])
    ax_ftan.plot(theory_per[mask], theory_gvel[mask], 'c-', lw=2, label='Theory')
    # Extracted curve
    if len(per_picked) > 0:
        ax_ftan.plot(per_picked, gv_picked, '--', color='#ff9900', lw=2, label='Extracted')
    ax_ftan.set_xlim(per[0], min(50, per[-1]))
    ax_ftan.set_xlabel('Period (s)', fontsize=9)
    ax_ftan.set_ylabel('Group Velocity (km/s)', fontsize=9)
    ax_ftan.set_title(f'FTAN  |  {case_name}  az{az_start}-{az_end}', fontsize=10)
    ax_ftan.legend(fontsize=8, facecolor='#222222', labelcolor='white', framealpha=0.5)

    # ---- Panels 4 & 5: Vp and Vs depth profiles ----------------------------
    max_depth = 80.0

    for fam, (d, vp, vs) in base_models.items():
        mask = d <= max_depth
        color = FAMILY_COLORS.get(fam, '#888888')
        is_current = (fam.lower() == family.lower())
        lw    = 2.5 if is_current else 0.8
        alpha = 1.0 if is_current else 0.4
        zorder= 5   if is_current else 2
        c_use = color if is_current else '#888888'
        label = fam   if is_current else None

        ax_vp.plot(vp[mask], d[mask], color=c_use, lw=lw, alpha=alpha,
                   zorder=zorder, label=label)

    ax_vp.set_xlabel('Vp (km/s)', fontsize=9)
    ax_vp.set_ylabel('Depth (km)', fontsize=9)
    ax_vp.invert_yaxis()
    ax_vp.set_ylim(max_depth, 0)
    ax_vp.set_title('P-wave Velocity', fontsize=10)
    ax_vp.legend(fontsize=8, facecolor='#222222', labelcolor='white',
                 framealpha=0.5, loc='lower right')

    # ---- CCF time domain ------------------------------------------------
    LAG_MAX = 100.0
    lags_mask = np.abs(ccf_lags) <= LAG_MAX
    ax_ccf_time.plot(ccf_lags[lags_mask], ccf_amp[lags_mask], 'w-', lw=0.8)
    ax_ccf_time.axvline(0, color='red', ls='--', lw=1, alpha=0.5)
    ax_ccf_time.axhline(0, color='gray', lw=0.5, alpha=0.3)
    ax_ccf_time.set_xlim(-LAG_MAX, LAG_MAX)
    ax_ccf_time.set_xlabel('Lag Time (s)', fontsize=9)
    ax_ccf_time.set_ylabel('Amplitude', fontsize=9)
    ax_ccf_time.set_title('Time-Domain CCF', fontsize=10)

    # ---- Coherence ------------------------------------------------------
    coh_file = case_dir / 'stacked_coherence_freq.txt'
    if coh_file.exists():
        coh_data = np.loadtxt(coh_file)
        ax_coh.plot(coh_data[:, 0], coh_data[:, 1], color='#5599ff', lw=1)
        ax_coh.set_xlim(0, 0.5)
    ax_coh.set_xlabel('Frequency (Hz)', fontsize=9)
    ax_coh.set_ylabel('Coherence', fontsize=9)
    ax_coh.set_title('Frequency Coherence', fontsize=10)

    # ---- Super title --------------------------------------------------------
    fig.suptitle(f'{model_stem}  |  {family}  |  Case: {case_name}  |  dist={dist_km} km  |  az {az_start}-{az_end}°',
                 color='white', fontsize=13, fontweight='bold', y=0.96)

    # ---- Save ---------------------------------------------------------------
    movies_dir = Path('experiments/experiment_17/movies')
    movies_dir.mkdir(parents=True, exist_ok=True)

    # Count wedges used in this case
    wedge_dirs = get_wedge_dirs(model_output_dir, az_start, az_end) if model_output_dir.exists() else []
    n_wedges   = len(wedge_dirs) if wedge_dirs else '?'

    movie_name = (f"{model_stem}_{family}_dist{dist_km}_"
                  f"{case_name}_az{az_start}-{az_end}_"
                  f"wedges{n_wedges}.png")

    out_case  = case_dir  / 'diagnostic_plot.png'
    out_movie = movies_dir / movie_name

    plt.savefig(out_case,  dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.savefig(out_movie, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"Saved: {out_case}")
    print(f"Saved: {out_movie}")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == '__main__':
    if len(sys.argv) == 2:
        arg = sys.argv[1]
        p = Path(arg)
        # If it is a family dir, process all case subdirs
        if p.is_dir() and not re.search(r'_(full|hemisphere|narrow_N2?|NNE|NE_E|NE_ESE|E_S|NW|W_NW)_az', p.name):
            case_dirs = sorted(p.iterdir())
            print(f"Processing {len(case_dirs)} cases in {p}")
            for case_dir in case_dirs:
                if not case_dir.is_dir(): continue
                if (case_dir / "diagnostic_plot.png").exists():
                    print(f"SKIP: {case_dir.name}")
                    continue
                try:
                    make_plot(case_dir)
                except Exception as e:
                    print(f"ERROR {case_dir.name}: {e}")
        else:
            make_plot(arg)
    else:
        print("Usage: python plot_coverage.py <case_dir_or_family_dir>")
        sys.exit(1)