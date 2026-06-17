#!/usr/bin/env python3
"""
wvsim_terra_allmodsv2.py — Terravibranium 100K Model-Level Parallel Simulator

Generalization of wavenet_simulator_v4.py:
  - Model-Level parallelization (1 core = 1 full model) to amortize spulse96 cost
  - v4's proven physics engine (CPS pipeline, 0.5km LUT, wedge-based noise, etc.)
  - FTAN dispersion via pycwt
  - Modular HDF5 writer

Fixes over wvsim_terra_allmods.py:
  Bug1: CPS model header (SPHERICAL EARTH, DATA, halfspace H=0)
  Bug2: CPS pipeline order (sregn96 before sdpegn96)
  Bug3: LUT 0.5km grid (not 5km)
  Bug4: Halfspace last-layer H=0
  Bug5: Wedge-based noise with per-source seeding and TMAX time shifts
  Bug6: Dynamic NPT from Vmin
  Bug7: SREGN.ASC column indices (period=2, phase=4, group=5)
"""

import os
import sys
import time
import math
import shutil
import subprocess
import tempfile
import argparse
import multiprocessing
import platform
import traceback

import numpy as np
import pandas as pd
import h5py

try:
    import pycwt
    HAS_PYCWT = True
except ImportError:
    HAS_PYCWT = False
    print("WARNING: pycwt not installed. FTAN will be skipped.")

# -----------------------------------------------------------------------
# CPS Binary Path
# -----------------------------------------------------------------------
if platform.system() == 'Linux':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'
else:
    CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'

# -----------------------------------------------------------------------
# Physics Constants
# -----------------------------------------------------------------------
DELTA = 0.5        # Sampling interval (seconds)
TMAX = 3599.0      # Observation window (seconds)
N_SOURCES = 1000000
N_WEDGES = 360
MAX_LAG_S = 500.0  # Max lag for CCF trimming

# -----------------------------------------------------------------------
# CPS Environment
# -----------------------------------------------------------------------
def setup_cps_env():
    env = os.environ.copy()
    env['PATH'] = f"{CPS_BIN}:{env['PATH']}"
    return env


def run_cps_command(cmd, cwd):
    """Run a CPS command, return True on success."""
    env = setup_cps_env()
    res = subprocess.run(cmd, shell=True, env=env, cwd=cwd,
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  CPS FAILED: {cmd}\n  stderr: {res.stderr[:500]}")
        return False
    return True


# -----------------------------------------------------------------------
# Model File & Eigenfunction Pipeline  (Bug 1, 2, 4, 6 fixed)
# -----------------------------------------------------------------------
def get_vmin_from_model_df(model_df):
    """Get minimum non-zero Vs for NPT computation."""
    vs = model_df['VS_kms'].values
    vs_nonzero = vs[vs > 0]
    return np.min(vs_nonzero) if len(vs_nonzero) > 0 else 2.5


def write_cps_model_file(model_df, filepath):
    """Write CPS MODEL.01 file — matching v4 exactly.
    
    Bug1 fix: SPHERICAL EARTH + DATA header (not FLAT EARTH / CONSTANT VELOCITY)
    Bug4 fix: Last layer H = 0.0 (halfspace indicator)
    """
    rows = model_df.reset_index(drop=True)
    with open(filepath, 'w') as f:
        f.write("MODEL.01\n")
        f.write("Wavenet\n")
        f.write("ISOTROPIC\n")
        f.write("KGS\n")
        f.write("SPHERICAL EARTH\n")
        f.write("1-D\n")
        f.write("DATA\n")
        f.write("H(KM)   VP(KM/S)   VS(KM/S) RHO(GM/CC)     QP         QS"
                "       ETAP       ETAS      FREFP      FREFS\n")
        for i, row in rows.iterrows():
            h = 0.0 if i == len(rows) - 1 else row['H_km']
            f.write(f"{h:8.4f} {row['VP_kms']:8.4f} {row['VS_kms']:8.4f} "
                    f"{row['RHO_gcc']:8.4f} 200.0 70.0 0.0 0.0 1.0 1.0\n")


def precompute_greens_functions(model_df, workdir, r_max):
    """Run the full CPS eigenfunction pipeline.
    
    Bug2 fix: Correct ordering — sprep96 → sdisp96 → sregn96 → slegn96 → sdpegn96 → sdpsrf96
    Bug6 fix: NPT computed dynamically from Vmin
    
    Returns: (success_bool, NPT)
    """
    # Write model file
    write_cps_model_file(model_df, os.path.join(workdir, 'model.d'))

    # Compute NPT from model's Vmin (Bug 6 fix)
    VMIN_MODEL = get_vmin_from_model_df(model_df)
    DIST_MAX = np.sqrt(2) * r_max
    NPT = int((DIST_MAX / VMIN_MODEL) / DELTA)
    NPT = 2 ** math.ceil(math.log2(NPT))

    # Write dfile
    with open(os.path.join(workdir, 'dfile'), 'w') as f:
        f.write(f"{DIST_MAX} {DELTA} {NPT} 0.0 0.0\n")

    # Run CPS pipeline (Bug 2 fix — correct order, matching v4)
    cmds = [
        f'{CPS_BIN}/sprep96 -M model.d -HS 0 -HR 0 -L -R -NMOD 10 -d dfile',
        f'{CPS_BIN}/sdisp96',
        f'{CPS_BIN}/sregn96 -NOQ',
        f'{CPS_BIN}/slegn96 -NOQ',
        f'{CPS_BIN}/sdpegn96 -R -U -ASC',
        f'{CPS_BIN}/sdpegn96 -L -U -ASC',
        f'{CPS_BIN}/sdpsrf96 -R -ASC',
        f'{CPS_BIN}/sdpsrf96 -L -ASC',
    ]
    for cmd in cmds:
        if not run_cps_command(cmd, workdir):
            return False, NPT

    return True, NPT


# -----------------------------------------------------------------------
# Dispersion Extraction  (Bug 7 fixed — column indices)
# -----------------------------------------------------------------------
def extract_dispersion(workdir):
    """Parse SREGN.ASC for theoretical dispersion.
    
    Bug7 fix: Correct column indices — period=[2], phase=[4], group=[5]
    (terra had phase=[3], group=[4] which is wrong)
    """
    per, grp, pha = [], [], []
    sregn_path = os.path.join(workdir, 'SREGN.ASC')
    try:
        with open(sregn_path, 'r') as f:
            for line in f.readlines()[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 6:
                    per.append(float(parts[2]))
                    pha.append(float(parts[4]))
                    grp.append(float(parts[5]))
    except Exception:
        pass
    return {
        'period': np.array(per),
        'phase': np.array(pha),
        'group': np.array(grp),
    }


# -----------------------------------------------------------------------
# LUT Construction  (Bug 3 fixed — 0.5 km grid)
# -----------------------------------------------------------------------
def parse_spulse96(output):
    """Parse spulse96 text output into component arrays."""
    lines = output.strip().split('\n')
    comps = {}
    curr_comp, curr_data, skip = None, [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line in ['ZEX', 'REX', 'ZVF', 'RVF', 'ZHF', 'RHF', 'THF']:
            if curr_comp:
                comps[curr_comp] = np.array(curr_data, dtype=np.float32)
            curr_comp, curr_data, skip = line, [], 2
            continue
        if skip > 0:
            skip -= 1
            continue
        if curr_comp:
            for x in line.split():
                try:
                    curr_data.append(float(x))
                except ValueError:
                    pass
    if curr_comp:
        comps[curr_comp] = np.array(curr_data, dtype=np.float32)
    return comps


def build_lut(workdir, r_max, npt_fixed):
    """Build Green's function LUT at 0.5 km spacing (Bug 3 fix).
    
    v4 uses 0.5 km steps from 50 km to sqrt(2)*r_max.
    terra used 5.0 km steps — 10× too coarse.
    """
    max_dist = np.sqrt(2) * r_max
    distances = np.arange(50.0, max_dist + 1.0, 0.5)
    env = setup_cps_env()

    lut = {}
    for d in distances:
        with open(os.path.join(workdir, 'dfile_src'), 'w') as f:
            f.write(f"{d} {DELTA} {npt_fixed} 0.0 0.0\n")
        cmd = f'{CPS_BIN}/spulse96 -d dfile_src -p -l 2 -V -EXF'
        out = subprocess.run(cmd, shell=True, env=env, cwd=workdir,
                             capture_output=True, text=True).stdout
        comps = parse_spulse96(out)
        if comps:
            lut[round(d, 1)] = comps

    return lut


# -----------------------------------------------------------------------
# Seismogram Computation (from v4, unchanged)
# -----------------------------------------------------------------------
def rotate_forces(f1, f2, f3, azimuth):
    az = azimuth * np.pi / 180.0
    return (f1 * np.cos(az) + f2 * np.sin(az),
            -f1 * np.sin(az) + f2 * np.cos(az),
            f3)


def compute_zne(greens, fR, fT, fZ, backazimuth):
    uZ = fZ * greens.get('ZVF', 0) + fR * greens.get('ZHF', 0)
    uR = fR * greens.get('RHF', 0) + fZ * greens.get('RVF', 0)
    uT = fT * greens.get('THF', 0) if 'THF' in greens else np.zeros_like(uZ)

    baz = backazimuth * np.pi / 180.0
    return (uZ,
            -np.cos(baz) * uR + np.sin(baz) * uT,
            -np.sin(baz) * uR - np.cos(baz) * uT)


# -----------------------------------------------------------------------
# Noise Source Simulation  (Bug 5 fixed — v4 wedge architecture)
# -----------------------------------------------------------------------
def simulate_wedge(wedge_id, wedge_min, wedge_max, sources_per_wedge,
                   r_min, r_max, rx1, ry1, rx2, ry2,
                   stack_length, npt_fixed, lut):
    """Simulate one azimuthal wedge of noise sources.
    
    Bug5 fix: Restores v4's architecture:
    - Per-source deterministic seeding
    - Sources distributed across azimuthal wedges
    - Time shifts spanning full TMAX window (not ±10s)
    """
    TMAX_SAMPLES = int(TMAX / DELTA) + 1

    wedge_r1_Z = np.zeros(stack_length)
    wedge_r2_Z = np.zeros(stack_length)

    for i in range(sources_per_wedge):
        global_source_id = wedge_id * sources_per_wedge + i
        np.random.seed(42 + global_source_id)

        theta = np.random.uniform(wedge_min, wedge_max) * np.pi / 180.0
        r = np.random.uniform(r_min, r_max)
        x, y = r * np.cos(theta), r * np.sin(theta)
        fn = np.random.uniform(-1, 1)
        fe = np.random.uniform(-1, 1)
        fd = np.random.uniform(-1, 1)

        tshift = i * (TMAX / sources_per_wedge)
        shift_samples = int(tshift / DELTA)

        for rx, ry, stack_Z in [(rx1, ry1, wedge_r1_Z), (rx2, ry2, wedge_r2_Z)]:
            dx, dy = rx - x, ry - y
            dist = np.sqrt(dx**2 + dy**2)
            if dist < 50.0 or dist > 1400.0:
                continue

            nearest_d = round(round(dist * 2) / 2.0, 1)
            if nearest_d not in lut:
                continue
            greens = lut[nearest_d]
            if not greens:
                continue

            azimuth = np.arctan2(dx, dy) * 180.0 / np.pi
            if azimuth < 0:
                azimuth += 360.0
            baz = (azimuth + 180 if azimuth < 180
                   else azimuth - 180 if azimuth > 180
                   else 0)

            fR, fT, fZ = rotate_forces(fn, fe, fd, azimuth)
            uZ, _, _ = compute_zne(greens, fR, fT, fZ, baz)

            end = min(shift_samples + len(uZ), stack_length)
            stack_Z[shift_samples:end] += uZ[:end - shift_samples]

    # Trim to TMAX and compute spectral products
    r1 = wedge_r1_Z[:TMAX_SAMPLES]
    r2 = wedge_r2_Z[:TMAX_SAMPLES]

    fft_r1 = np.fft.fft(r1)
    fft_r2 = np.fft.fft(r2)
    cross_power = fft_r1 * np.conj(fft_r2)
    p11 = np.abs(fft_r1)**2
    p22 = np.abs(fft_r2)**2

    # Also return the raw stacked traces for output
    return cross_power, p11, p22, r1, r2


# -----------------------------------------------------------------------
# FTAN Computation (kept from terra, velocity axis fixed)
# -----------------------------------------------------------------------
def compute_ftan(ccf_trimmed, sep_km, dt=DELTA):
    """Compute FTAN 2D matrix using pycwt.
    
    Returns dict with FTAN_ZZ, period_s, velocity_kms or None if pycwt unavailable.
    """
    if not HAS_PYCWT:
        return None

    MAX_LAG_SAMPLES = int(MAX_LAG_S / dt)

    # Empirical Green's Function = negative derivative of positive-lag CCF
    egf = -np.gradient(ccf_trimmed)
    egf = egf[MAX_LAG_SAMPLES:]  # Positive lags only

    if len(egf) < 10:
        return None

    dj = 1 / 12   # Twelve sub-octaves per octave
    s0 = 2 * dt   # Starting scale
    J = 7 / dj    # Seven powers of two

    try:
        cwt, sj, freq_ftan, coi, _, _ = pycwt.cwt(egf, dt, dj, s0, J, 'morlet')
    except Exception:
        return None

    rcwt = np.abs(cwt)**2

    # Normalize each frequency row
    for i in range(len(freq_ftan)):
        row_max = np.max(rcwt[i, :])
        if row_max > 0:
            rcwt[i, :] /= row_max

    period_s = 1.0 / freq_ftan
    # Velocity = distance / travel_time; travel_time = sample_index * dt
    travel_times = np.arange(len(egf)) * dt
    velocity_kms = np.where(travel_times > 0, sep_km / travel_times, 0.0)

    return {
        'FTAN_ZZ': rcwt,
        'period_s': period_s,
        'velocity_kms': velocity_kms,
    }


# -----------------------------------------------------------------------
# Process One Model (the worker function)
# -----------------------------------------------------------------------
def process_model(args):
    """
    Complete processing for a single model:
    1. Write CPS model → run eigenfunction pipeline
    2. Extract theoretical dispersion
    3. Build 0.5km LUT via spulse96
    4. Simulate noise wedges sequentially → build CCF
    5. Compute FTAN
    Returns a result dict.
    """
    model_id, model_df, sep_km = args

    start_time = time.time()
    temp_dir = tempfile.mkdtemp(prefix=f"wvsim_{model_id}_")

    try:
        # --- Geometry ---
        r_min = max(200.0, sep_km)
        r_max = r_min + 100.0
        rx1, ry1 = -sep_km / 2, 0.0
        rx2, ry2 = sep_km / 2, 0.0

        # --- Step 1: CPS Eigenfunctions ---
        success, NPT_FIXED = precompute_greens_functions(model_df, temp_dir, r_max)
        if not success:
            raise RuntimeError("CPS eigenfunction pipeline failed")

        # --- Step 2: Theoretical Dispersion ---
        dispersion = extract_dispersion(temp_dir)

        # --- Step 3: Build LUT ---
        lut = build_lut(temp_dir, r_max, NPT_FIXED)
        if not lut:
            raise RuntimeError("LUT is empty — no Green's functions were generated")

        # --- Step 4: Simulate Noise ---
        TMAX_SAMPLES = int(TMAX / DELTA) + 1
        STACK_LENGTH = TMAX_SAMPLES + NPT_FIXED

        sources_per_wedge = N_SOURCES // N_WEDGES
        wedge_width = 360.0 / N_WEDGES

        global_cross_power = np.zeros(TMAX_SAMPLES, dtype=np.complex128)
        global_p11 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
        global_p22 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
        # Accumulate raw stacks across wedges
        global_stack1 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
        global_stack2 = np.zeros(TMAX_SAMPLES, dtype=np.float64)

        for w in range(N_WEDGES):
            w_min = w * wedge_width
            w_max = (w + 1) * wedge_width

            cp, p11, p22, r1, r2 = simulate_wedge(
                w, w_min, w_max, sources_per_wedge,
                r_min, r_max, rx1, ry1, rx2, ry2,
                STACK_LENGTH, NPT_FIXED, lut
            )
            global_cross_power += cp
            global_p11 += p11
            global_p22 += p22
            global_stack1 += r1
            global_stack2 += r2

            if (w + 1) % 60 == 0:
                print(f"  [{model_id}] {w + 1}/{N_WEDGES} wedges done", flush=True)

        # --- Average over wedges ---
        avg_cross_power = global_cross_power / N_WEDGES
        avg_p11 = global_p11 / N_WEDGES
        avg_p22 = global_p22 / N_WEDGES

        # --- CCF ---
        ccf_ifft = np.fft.ifft(avg_cross_power).real
        ccf_final = np.fft.fftshift(ccf_ifft)

        # --- Coherence ---
        den = np.sqrt(avg_p11 * avg_p22)
        coherence = np.zeros_like(avg_cross_power, dtype=np.complex128)
        mask = den > 0
        coherence[mask] = np.real(avg_cross_power[mask]) / den[mask]
        coherence = coherence.real

        freqs = np.fft.fftfreq(TMAX_SAMPLES, d=DELTA)

        # --- Trim CCF to ±MAX_LAG ---
        MAX_LAG_SAMPLES = int(MAX_LAG_S / DELTA)
        mid = len(ccf_final) // 2
        s_idx = mid - MAX_LAG_SAMPLES
        e_idx = mid + MAX_LAG_SAMPLES + 1
        lags_trimmed = (np.arange(-MAX_LAG_SAMPLES, MAX_LAG_SAMPLES + 1)) * DELTA
        ccf_trimmed = ccf_final[s_idx:e_idx]

        # --- Step 5: FTAN ---
        ftan_result = compute_ftan(ccf_trimmed, sep_km)

        # --- Build result ---
        result = {
            'model_id': model_id,
            'model_family': model_id.split('_')[0],
            'status': 'success',
            'time_s': time.time() - start_time,
            'velocity_profile': {
                'H_km': model_df['H_km'].values,
                'VP_kms': model_df['VP_kms'].values,
                'VS_kms': model_df['VS_kms'].values,
                'RHO_gcc': model_df['RHO_gcc'].values,
            },
            'theoretical': {
                'period': dispersion['period'],
                'phase_velocity_dispersion': dispersion['phase'],
                'group_velocity_dispersion': dispersion['group'],
            },
            'ccf_isotropic': {
                'lags_s': lags_trimmed,
                'freqs_hz': freqs,
                'CCF_ZZ': ccf_trimmed,
                'COH_REAL_ZZ': coherence,
                'stack_sta1_Z': global_stack1,
                'stack_sta2_Z': global_stack2,
            },
        }
        if ftan_result is not None:
            result['empirical_ftan_dispersion'] = ftan_result

    except Exception as e:
        result = {
            'model_id': model_id,
            'status': 'error',
            'error': str(e),
            'traceback': traceback.format_exc(),
            'time_s': time.time() - start_time,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return result


# -----------------------------------------------------------------------
# HDF5 Writer (modular, from terra — kept clean)
# -----------------------------------------------------------------------
def save_model_to_hdf5(h5f, result, sep_km):
    """Save one model's results to the master HDF5 file."""
    if result['status'] != 'success':
        print(f"  Skipping {result['model_id']} — error: {result.get('error', 'unknown')}")
        return

    model_key = f"simulations/{result['model_id']}"
    if model_key in h5f:
        del h5f[model_key]

    grp = h5f.create_group(model_key)
    grp.attrs['model_family'] = result['model_family']

    # Velocity Profile
    prof_grp = grp.create_group("velocity_profile")
    for k, v in result['velocity_profile'].items():
        prof_grp.create_dataset(k, data=v)

    # Theoretical Dispersion
    theo_grp = grp.create_group("theoretical")
    for k, v in result['theoretical'].items():
        theo_grp.create_dataset(k, data=v)

    # Geometry → CCF
    geom_grp = grp.create_group(f"geometries/separation_{sep_km:.1f}km")

    ccf_grp = geom_grp.create_group("ccf_isotropic")
    ccf_grp.attrs['n_wedges_used'] = N_WEDGES
    ccf_grp.attrs['n_sources_used'] = N_SOURCES
    for k, v in result['ccf_isotropic'].items():
        ccf_grp.create_dataset(k, data=v)

    # FTAN
    if 'empirical_ftan_dispersion' in result:
        ftan_grp = geom_grp.create_group("empirical_ftan_dispersion")
        for k, v in result['empirical_ftan_dispersion'].items():
            ftan_grp.create_dataset(k, data=v)
    else:
        ftan_grp = geom_grp.create_group("empirical_ftan_dispersion")
        ftan_grp.attrs['status'] = 'pending_ftan_computation'


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Terravibranium 100K — Model-Level Parallel Ambient Noise Simulator"
    )
    parser.add_argument("--models", type=str,
                        default="../01_parametrization/model_manifest.parquet")
    parser.add_argument("--output", type=str,
                        default="wavenet_dataset_100k.h5")
    parser.add_argument("--cores", type=int, default=None,
                        help="Number of parallel workers (default: cpu_count)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only N models (for testing)")
    parser.add_argument("--sep_km", type=float, default=127.0,
                        help="Station separation in km")
    args = parser.parse_args()

    # Setup multiprocessing
    try:
        multiprocessing.set_start_method('fork')
    except RuntimeError:
        pass

    cores = args.cores if args.cores else min(multiprocessing.cpu_count(), 48)

    # Load models
    models_df = pd.read_parquet(args.models)
    unique_models = models_df['Model_ID'].unique()
    if args.limit:
        unique_models = unique_models[:args.limit]

    # Build task list: (model_id, model_dataframe, sep_km)
    tasks = []
    for m_id in unique_models:
        m_df = models_df[models_df['Model_ID'] == m_id]
        tasks.append((m_id, m_df, args.sep_km))

    print(f"═══════════════════════════════════════════════════════════════")
    print(f"  Terravibranium v2 — {len(tasks)} models × {N_SOURCES} sources each")
    print(f"  Cores: {cores} | Sep: {args.sep_km} km | Output: {args.output}")
    print(f"═══════════════════════════════════════════════════════════════")

    h5f = h5py.File(args.output, 'a')

    start_global = time.time()
    success_count = 0
    error_count = 0

    with multiprocessing.Pool(cores) as pool:
        for i, result in enumerate(pool.imap_unordered(process_model, tasks)):
            if result['status'] == 'success':
                success_count += 1
            else:
                error_count += 1
                print(f"  ERROR [{result['model_id']}]: {result.get('error', '?')}")
                if 'traceback' in result:
                    print(result['traceback'][:500])

            save_model_to_hdf5(h5f, result, args.sep_km)

            elapsed = time.time() - start_global
            avg_s = elapsed / (i + 1)
            remaining_h = (len(tasks) - (i + 1)) * avg_s / 3600.0
            print(f"  [{i+1}/{len(tasks)}] {result['model_id']} "
                  f"in {result.get('time_s', 0):.1f}s | "
                  f"Avg: {avg_s:.1f}s/mod | "
                  f"Remaining: {remaining_h:.2f}h | "
                  f"OK: {success_count} Err: {error_count}")

    h5f.close()

    total_time = time.time() - start_global
    print(f"\n{'═'*63}")
    print(f"  DONE — {success_count}/{len(tasks)} models in {total_time:.1f}s "
          f"({total_time/3600:.2f}h)")
    print(f"{'═'*63}")


if __name__ == '__main__':
    main()
