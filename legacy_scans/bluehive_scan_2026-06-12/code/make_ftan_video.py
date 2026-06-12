#!/usr/bin/env python3
"""
Generate a video of diagnostic plots sorted by FTAN RMS quality (best first).

Scans:
  - experiments/experiment_17/movies/
  - experiments/movies/

For each plot, recomputes RMS from stacked_time_ccf.txt + SREGN.ASC,
sorts ascending, and writes a video.

Usage:
    python make_ftan_video.py
    python make_ftan_video.py --fps 10
"""

import re
import sys
import argparse
import numpy as np
from pathlib import Path
import scipy.interpolate
from scipy.ndimage import gaussian_filter1d
import subprocess

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MOVIES_EXP17 = Path('experiments/experiment_17/movies')
MOVIES_OTHER = Path('experiments/movies')
OUTPUT_VIDEO = Path('experiments/ftan_sorted_video.mp4')

# ---------------------------------------------------------------------------
# FTAN parameters
# ---------------------------------------------------------------------------
FTAN_FMIN = 0.05
FTAN_FMAX = 1.0
FTAN_VMIN = 0.5
FTAN_VMAX = 4.5
DT        = 0.5


def read_sregn(sregn_file):
    data = np.loadtxt(sregn_file, skiprows=1)
    mask = data[:, 0] == 0
    return data[mask, 2], data[mask, 5]


def compute_rms(ccf_file, sregn_file, dist_km):
    """Compute RMS error between extracted and theoretical dispersion."""
    try:
        ccf_data = np.loadtxt(ccf_file)
        ccf_lags = ccf_data[:, 0]
        ccf_amp  = ccf_data[:, 1]

        theory_per, theory_gvel = read_sregn(sregn_file)

        npts = len(ccf_amp)
        indx = npts // 2
        data = 0.5 * ccf_amp[indx:] + 0.5 * np.flip(ccf_amp[:indx + 1], axis=0)

        pt1 = int(dist_km / FTAN_VMAX / DT)
        pt2 = int(dist_km / FTAN_VMIN / DT)
        if pt1 == 0: pt1 = 10
        if pt2 > (npts // 2): pt2 = npts // 2

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

        vel_data = dist_km / tvec
        fc       = scipy.interpolate.interp2d(vel_data, period, rcwt, kind='linear')
        rcwt_new = fc(vel, per)

        for ii in range(len(per)):
            mx = rcwt_new[ii].max()
            if mx > 0: rcwt_new[ii] /= mx

        for j in range(len(vel)):
            rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)

        # Extract dispersion
        nper, gv = [], []
        for ii in range(len(per)):
            idx2 = np.argmax(rcwt_new[ii])
            if rcwt_new[ii, idx2] > 0.5:
                nper.append(per[ii])
                gv.append(vel[idx2])

        if len(nper) == 0:
            return 999.9

        per_picked = np.array(nper)
        gv_picked  = np.array(gv)

        theory_at_picked = np.interp(per_picked, theory_per, theory_gvel)
        rms = np.sqrt(np.mean((gv_picked - theory_at_picked)**2))
        rel = rms / np.mean(theory_at_picked) * 100
        return rel

    except Exception as e:
        return 999.9


def find_case_dir_for_movie(movie_png):
    """
    Given a movie PNG filename, find the corresponding case dir
    with stacked_time_ccf.txt and SREGN.ASC.
    """
    name = movie_png.stem

    # exp17 format: CIA_0042_CIA_dist400_full_az0-360_wedges180
    # exp7-16 format: exp10_WUS_dist50_full_az0-360
    # Try exp17 first
    m17 = re.match(r'(.+?)_([A-Za-z]+)_dist(\d+)_(\w+)_az(\d+)-(\d+)_wedges', name)
    if m17:
        stem   = m17.group(1)
        family = m17.group(2)
        case_n = m17.group(4)
        az_s   = m17.group(5)
        az_e   = m17.group(6)
        base = Path('experiments/experiment_17/coverage_analysis') / family
        pattern = f"{stem}_{case_n}_az{az_s}-{az_e}"
        matches = list(base.glob(pattern))
        if matches:
            return matches[0], int(m17.group(3))

    # exp7-16 format
    m_other = re.match(r'exp(\d+)_(.+?)_dist(\d+)_(\w+)_az(\d+)-(\d+)', name)
    if m_other:
        exp_num = m_other.group(1)
        case_n  = m_other.group(4)
        az_s    = m_other.group(5)
        az_e    = m_other.group(6)
        dist_km = int(m_other.group(3))
        base    = Path(f'experiments/experiment_{exp_num}/outputs/azimuthal_coverage_analysis')
        pattern = f"case_*_{case_n}_az{int(az_s):03d}-{int(az_e):03d}"
        matches = list(base.glob(pattern))
        if matches:
            return matches[0], dist_km

    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fps', type=int, default=4)
    args = parser.parse_args()

    print("=" * 60)
    print("FTAN VIDEO GENERATOR")
    print("=" * 60)

    # Collect all PNGs from both movies folders
    all_pngs = []
    for folder in [MOVIES_EXP17, MOVIES_OTHER]:
        if folder.exists():
            pngs = sorted(folder.glob('*.png'))
            all_pngs.extend(pngs)
            print(f"  {folder}: {len(pngs)} images")

    print(f"  Total images: {len(all_pngs)}")

    if not all_pngs:
        print("ERROR: No PNG files found")
        sys.exit(1)

    # Compute RMS for each
    print(f"\nComputing RMS for {len(all_pngs)} images...")
    scored = []
    failed = 0

    for i, png in enumerate(all_pngs):
        case_dir, dist_km = find_case_dir_for_movie(png)

        if case_dir is None:
            rms = 999.9
            failed += 1
        else:
            ccf_file   = case_dir / 'stacked_time_ccf.txt'
            sregn_file = case_dir / 'SREGN.ASC'
            if ccf_file.exists() and sregn_file.exists():
                rms = compute_rms(ccf_file, sregn_file, dist_km)
            else:
                rms = 999.9
                failed += 1

        scored.append((rms, png))

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(all_pngs)}...")

    print(f"  Done. Failed to score: {failed}/{len(all_pngs)}")

    # Sort by RMS ascending (best first)
    scored.sort(key=lambda x: x[0])

    print(f"\nTop 5 best RMS:")
    for rms, png in scored[:5]:
        print(f"  {rms:.1f}%  {png.name}")
    print(f"\nBottom 5 worst RMS:")
    for rms, png in scored[-5:]:
        print(f"  {rms:.1f}%  {png.name}")

    # Save sorted file list
    filelist = Path('experiments/ftan_video_filelist.txt')
    with open(filelist, 'w') as f:
        for rms, png in scored:
            f.write(f"file '{png.resolve()}'\n")
            f.write(f"duration {1.0/args.fps:.4f}\n")

    print(f"\nGenerating video at {args.fps} fps...")
    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(filelist),
        '-vf', 'scale=1920:-2:flags=lanczos,format=yuv420p',
        '-c:v', 'libx264',
        '-crf', '23',
        '-preset', 'fast',
        str(OUTPUT_VIDEO)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Video saved: {OUTPUT_VIDEO}")
    else:
        print(f"ffmpeg failed:\n{result.stderr[-500:]}")
        print(f"\nFile list saved to {filelist}")
        print("Run manually: ffmpeg -f concat -safe 0 -i experiments/ftan_video_filelist.txt "
              "-vf scale=1920:-2,format=yuv420p -c:v libx264 -crf 23 experiments/ftan_sorted_video.mp4")


if __name__ == '__main__':
    main()