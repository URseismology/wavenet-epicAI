#!/usr/bin/env python3
"""
verify_terra_v2.py — Verification of wvsim_terra_allmodsv2.py output.

Modes:
  1. Generate frames:  python verify_terra_v2.py --h5 file.h5 --outdir frames/
  2. Make movie:        python verify_terra_v2.py --make_movie --outdir frames/ --movie out.mp4

Adapted from verify_10_models.py. Produces a 6-panel visualization per model:
  Panel 1: Source distribution geometry
  Panel 2: FTAN dispersion map with theoretical group velocity overlay
  Panel 3: FTAN thresholded mask
  Panel 4: Velocity profile (Vp)
  Panel 5: Time-domain CCF (bandpassed)
  Panel 6: Frequency coherence with SPAC Bessel J0 theory
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import h5py
import scipy.interpolate
import scipy.special
from scipy.signal import detrend
import sys
import os
import argparse
import subprocess

try:
    import pycwt
except ImportError:
    print("pycwt not installed — FTAN panels will be blank")
    pycwt = None


# ===========================================================================
# HELPER FUNCTIONS
# ===========================================================================
def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper


def bandpass_filter_freq(fft_data, freq_range, dt):
    TAPER_WIDTH = 0.2
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


def plot_source_distribution(ax, distance_km, r_min, r_max):
    num_points = 5000
    np.random.seed(0)
    r = np.sqrt(np.random.uniform(r_min**2, r_max**2, num_points))
    theta = np.random.uniform(0, 2 * np.pi, num_points)
    x = r * np.cos(theta)
    y = r * np.sin(theta)

    ax.scatter(x, y, s=1, c='white', alpha=0.3, edgecolors='none', rasterized=True)
    half = distance_km / 2
    ax.plot(-half, 0, 'rv', ms=8, mec='white', mew=0.5, label='Station 1')
    ax.plot( half, 0, 'rv', ms=8, mec='white', mew=0.5, label='Station 2')
    ax.plot([-half, half], [0, 0], 'w--', lw=1, alpha=0.6)
    ax.set_aspect('equal')
    ax.set_xlabel('X (km)', fontsize=9)
    ax.set_ylabel('Y (km)', fontsize=9)
    ax.set_title(f'Source Distribution (r: {r_min:.0f}-{r_max:.0f}km)', fontsize=10)
    ax.legend(fontsize=8, facecolor='#222222', labelcolor='white', framealpha=0.5)


# ===========================================================================
# SINGLE MODEL 6-PANEL PLOTTER
# ===========================================================================
def compute_ftan_and_plot(hdf5_path, output_png, target_model_key, frame_idx, total_frames):
    dt = 0.5
    fmin, fmax = 0.05, 1.0
    vmin, vmax = 2.0, 5.0

    with h5py.File(hdf5_path, 'r') as f:
        model_group = f['simulations'][target_model_key]

        theory_periods = model_group['theoretical']['period'][:]
        theory_gvel = model_group['theoretical']['group_velocity_dispersion'][:]
        theory_pvel = model_group['theoretical']['phase_velocity_dispersion'][:]

        vp_prof = model_group['velocity_profile']['VP_kms'][:]
        vs_prof = model_group['velocity_profile']['VS_kms'][:]
        h_prof = model_group['velocity_profile']['H_km'][:]
        depths = np.cumsum(h_prof)
        depths = np.insert(depths, 0, 0)[:-1]

        geom_keys = list(model_group['geometries'].keys())
        geom_key = geom_keys[0]
        geom_group = model_group['geometries'][geom_key]

        ccf_final = geom_group['ccf_isotropic']['CCF_ZZ'][:]
        coh_real = geom_group['ccf_isotropic']['COH_REAL_ZZ'][:]
        freqs_hz = geom_group['ccf_isotropic']['freqs_hz'][:]

        try:
            distance_km = float(geom_key)
        except ValueError:
            distance_km = float(geom_key.split('_')[1].replace('km', ''))

    # ---------------------------------------------------------------
    # Coherence-based CCF (bandpassed)
    # ---------------------------------------------------------------
    coh_ifft_raw = np.fft.ifft(coh_real).real
    coh_time_raw = np.fft.fftshift(coh_ifft_raw)
    coh_time_raw = detrend(coh_time_raw)
    coh_time_raw = cosine_taper(coh_time_raw)

    coh_fft = np.fft.fft(np.fft.fftshift(coh_time_raw))
    coh_filt = bandpass_filter_freq(coh_fft, [fmin, fmax], dt)
    time_ccf = np.fft.fftshift(np.fft.ifft(coh_filt).real)

    npts = len(time_ccf)
    lags = (np.arange(npts) - np.floor(npts / 2)) * dt

    # ---------------------------------------------------------------
    # FTAN from coherence-based EGF
    # ---------------------------------------------------------------
    indx = npts // 2
    egf = 0.5 * time_ccf[indx:] + 0.5 * np.flip(time_ccf[:indx + 1], axis=0)

    pt1 = int(distance_km / vmax / dt)
    pt2 = int(distance_km / vmin / dt)
    if pt1 == 0:
        pt1 = 10
    if pt2 > (npts // 2):
        pt2 = npts // 2

    indx_arr = np.arange(pt1, pt2)
    tvec = indx_arr * dt
    egf = egf[indx_arr]

    # FTAN via pycwt
    rcwt_new = None
    per = np.arange(int(1 / fmax), int(1 / fmin), 0.25)
    vel = np.arange(vmin, vmax, 0.01)

    if pycwt is not None and len(egf) > 10:
        try:
            dj = 1 / 24
            s0 = -1
            J = -1
            cwt, sj, freq, coi, _, _ = pycwt.cwt(egf, dt, dj, s0, J, 'morlet')

            freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
            cwt = cwt[freq_ind]
            freq = freq[freq_ind]
            period = 1 / freq
            rcwt = np.abs(cwt) ** 2

            velocity_data = distance_km / tvec

            import warnings
            warnings.filterwarnings("ignore")
            fc = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
            rcwt_new = fc(vel, per)

            for ii in range(len(per)):
                row_max = np.max(rcwt_new[ii])
                if row_max > 0:
                    rcwt_new[ii] /= row_max

            from scipy.ndimage import gaussian_filter1d
            for j in range(len(vel)):
                rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)
        except Exception:
            rcwt_new = None

    # ---------------------------------------------------------------
    # 6-Panel Figure
    # ---------------------------------------------------------------
    fig = plt.figure(figsize=(20, 12), facecolor='#0d0d0d')
    gs = gridspec.GridSpec(2, 3, figure=fig, left=0.06, right=0.97,
                           top=0.90, bottom=0.07, wspace=0.35, hspace=0.45)

    ax_src  = fig.add_subplot(gs[0, 0])
    ax_ftan = fig.add_subplot(gs[0, 1])
    ax_mask = fig.add_subplot(gs[0, 2])
    ax_vp   = fig.add_subplot(gs[1, 0])

    gs_mid      = gridspec.GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[1, 1:3], hspace=0.55)
    ax_ccf_time = fig.add_subplot(gs_mid[0])
    ax_coh      = fig.add_subplot(gs_mid[1])

    for ax in [ax_src, ax_ftan, ax_mask, ax_vp, ax_ccf_time, ax_coh]:
        ax.set_facecolor('#111111')
        ax.tick_params(colors='white', labelsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor('#444444')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')
        ax.grid(True, alpha=0.15, color='white')

    # Panel 1: Source Distribution
    r_min = max(200.0, distance_km)
    r_max = r_min + 100.0
    plot_source_distribution(ax_src, distance_km, r_min, r_max)

    # Fundamental mode extraction
    wrap_indices = np.where(np.diff(theory_periods) < 0)[0]
    if len(wrap_indices) > 0:
        idx = wrap_indices[0] + 1
        fund_per = theory_periods[:idx]
        fund_gvel = theory_gvel[:idx]
        fund_pvel = theory_pvel[:idx]
    else:
        fund_per = theory_periods
        fund_gvel = theory_gvel
        fund_pvel = theory_pvel

    # Panel 2: FTAN
    if rcwt_new is not None:
        ax_ftan.imshow(np.transpose(rcwt_new), cmap='inferno',
                       extent=[per[0], per[-1], vel[0], vel[-1]],
                       aspect='auto', origin='lower')
    ax_ftan.plot(fund_per, fund_gvel, color='lime', ls='--', lw=2,
                 label='Theory GVel (Fund)')
    ax_ftan.set_xlim(per[0], per[-1])
    ax_ftan.set_ylim(2.0, 5.0)
    ax_ftan.set_title('FTAN Output', fontsize=10)
    ax_ftan.set_xlabel('Period (s)', fontsize=9)
    ax_ftan.set_ylabel('Group Velocity (km/s)', fontsize=9)
    ax_ftan.legend(fontsize=8, facecolor='#222222', labelcolor='white')

    # Panel 3: Thresholded Mask
    if rcwt_new is not None:
        mask = rcwt_new > 0.8
        ax_mask.imshow(np.transpose(mask), cmap='viridis',
                       extent=[per[0], per[-1], vel[0], vel[-1]],
                       aspect='auto', origin='lower')
    ax_mask.plot(fund_per, fund_gvel, color='lime', ls='--', lw=2,
                 label='Theory GVel (Fund)')
    ax_mask.set_xlim(per[0], per[-1])
    ax_mask.set_ylim(2.0, 5.0)
    ax_mask.set_title('Target Mask (FTAN > 0.8)', fontsize=10)

    # Panel 4: Vp Profile (show Vp and Vs)
    ax_vp.step(vp_prof, depths, 'w-', lw=2, where='post', label='Vp')
    ax_vp.step(vs_prof, depths, color='#ff6666', lw=1.5, where='post', label='Vs', ls='--')
    ax_vp.invert_yaxis()
    ax_vp.set_xlabel('Velocity (km/s)', fontsize=9)
    ax_vp.set_ylabel('Depth (km)', fontsize=9)
    ax_vp.set_title('Velocity Profile', fontsize=10)
    ax_vp.legend(fontsize=8, facecolor='#222222', labelcolor='white')

    # Panel 5: Time-Domain CCF
    LAG_TIME_MAX = 250.0
    lag_mask = np.abs(lags) <= LAG_TIME_MAX
    ax_ccf_time.plot(lags[lag_mask], time_ccf[lag_mask], 'w-', lw=1)
    ax_ccf_time.set_xlabel('Lag Time (s)', fontsize=9)
    ax_ccf_time.set_title(f'Time-Domain CCF (Bandpass: {fmin}-{fmax} Hz)', fontsize=10)
    ax_ccf_time.axvline(0, color='r', ls='--', alpha=0.5)

    # Panel 6: Coherence + SPAC Theory
    ax_coh.plot(freqs_hz, coh_real, color='#5599ff', lw=1, label='Empirical Coherence')

    if len(fund_per) > 0:
        f_theory = 1.0 / fund_per
        sort_idx = np.argsort(f_theory)
        f_theory_sorted = f_theory[sort_idx]
        c_theory_sorted = fund_pvel[sort_idx]

        f_valid_idx = freqs_hz > 0.001
        f_valid = freqs_hz[f_valid_idx]
        c_interp = scipy.interpolate.interp1d(
            f_theory_sorted, c_theory_sorted,
            bounds_error=False, fill_value="extrapolate"
        )(f_valid)
        theoretical_bessel = scipy.special.jv(
            0, 2 * np.pi * f_valid * distance_km / c_interp
        )
        ax_coh.plot(f_valid, theoretical_bessel, color='orange', ls='--',
                    lw=1.5, label='SPAC Theory $J_0$')

    ax_coh.set_xlim(0, 0.5)
    ax_coh.set_ylim(-1, 1)
    ax_coh.set_xlabel('Frequency (Hz)', fontsize=9)
    ax_coh.set_title('Frequency Coherence', fontsize=10)
    ax_coh.legend(fontsize=7, facecolor='#222222', labelcolor='white')

    fig.suptitle(f'Terra v2 Verification — {target_model_key} '
                 f'(Dist: {distance_km}km) [{frame_idx+1}/{total_frames}]',
                 color='white', fontsize=14, fontweight='bold')
    plt.savefig(output_png, dpi=100, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close()


# ===========================================================================
# MOVIE STITCHING
# ===========================================================================
def make_movie(frames_dir, output_movie, fps=4):
    """Stitch frame_NNNN.png files into an MP4 using ffmpeg."""
    pattern = os.path.join(frames_dir, 'frame_%04d.png')

    cmd = [
        'ffmpeg', '-y',
        '-framerate', str(fps),
        '-i', pattern,
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-crf', '23',
        '-preset', 'medium',
        output_movie
    ]

    print(f"Stitching {output_movie} at {fps} fps...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr[:500]}")
        return False
    print(f"Movie saved: {output_movie}")
    return True


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Verify wvsim_terra_allmodsv2.py output — frames + movie"
    )
    parser.add_argument("--h5", type=str, default=None,
                        help="Path to HDF5 output file")
    parser.add_argument("--outdir", type=str, default="verify_terra_v2_frames",
                        help="Output directory for frame PNGs")
    parser.add_argument("--max_models", type=int, default=None,
                        help="Max models to plot (default: all)")
    parser.add_argument("--make_movie", action="store_true",
                        help="Stitch frames into MP4 (run locally after rsync)")
    parser.add_argument("--movie", type=str, default="terra_v2_verification.mp4",
                        help="Output movie filename")
    parser.add_argument("--fps", type=int, default=4,
                        help="Frames per second for movie")
    args = parser.parse_args()

    # Movie-only mode
    if args.make_movie:
        make_movie(args.outdir, args.movie, args.fps)
        return

    # Frame generation mode
    if args.h5 is None:
        print("ERROR: --h5 is required for frame generation")
        sys.exit(1)

    if not os.path.exists(args.h5):
        print(f"ERROR: HDF5 file not found: {args.h5}")
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)

    with h5py.File(args.h5, 'r') as f:
        if 'simulations' not in f:
            print("ERROR: No 'simulations' group in HDF5 file")
            sys.exit(1)
        model_keys = sorted(f['simulations'].keys())

    n = len(model_keys)
    if args.max_models:
        n = min(n, args.max_models)

    print(f"Generating {n} verification frames from {args.h5}...")

    for i, mk in enumerate(model_keys[:n]):
        out_png = os.path.join(args.outdir, f"frame_{i:04d}.png")
        try:
            compute_ftan_and_plot(args.h5, out_png, mk, i, n)
            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1}/{n}] {mk} → {out_png}")
        except Exception as e:
            print(f"  [{i+1}/{n}] {mk} FAILED: {e}")

    print(f"\nDone. {n} frames saved to {args.outdir}/")
    print(f"To make movie: python verify_terra_v2.py --make_movie --outdir {args.outdir}")


if __name__ == '__main__':
    main()
