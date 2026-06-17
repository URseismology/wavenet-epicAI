import os
import sys
import glob
import time
import shutil
import subprocess
import tempfile
import argparse
import multiprocessing
import pandas as pd
import numpy as np
import h5py
import pycwt
from scipy.interpolate import RectBivariateSpline

import platform

# -----------------------------------------------------------------------
# Physics Configuration
# -----------------------------------------------------------------------
V_MIN, V_MAX = 2.0, 5.0
F_MIN, F_MAX = 0.01, 1.0
D_DT = 0.5

if platform.system() == 'Linux':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'
else:
    CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'

# -----------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------
def run_command(cmd, cwd=None):
    env = os.environ.copy()
    env['PATH'] = f"{CPS_BIN}:{env['PATH']}"
    try:
        result = subprocess.run(cmd, shell=True, check=True, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running '{cmd}' in {cwd}: Exit Code {e.returncode}\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
        return False
    return True

# -----------------------------------------------------------------------
# Worker Logic (One Model)
# -----------------------------------------------------------------------
def rotate_forces(f1, f2, f3, azimuth):
    az = azimuth * np.pi / 180.0
    return f1*np.cos(az) + f2*np.sin(az), -f1*np.sin(az) + f2*np.cos(az), f3

def compute_zne(greens, fR, fT, fZ, backazimuth):
    uZ = fZ * greens.get('ZVF', 0) + fR * greens.get('ZHF', 0)
    uR = fR * greens.get('RHF', 0) + fZ * greens.get('RVF', 0)
    uT = fT * greens.get('THF', 0) if 'THF' in greens else np.zeros_like(uZ)
    baz = backazimuth * np.pi / 180.0
    return uZ, -np.cos(baz)*uR + np.sin(baz)*uT, -np.sin(baz)*uR - np.cos(baz)*uT

def process_model(args):
    """
    Computes everything for a single model:
    1. Runs CPS (spulse96) to generate Green's Functions.
    2. Builds 2D LUT.
    3. Sums noise wedges sequentially to generate CCF.
    4. Computes FTAN 2D Matrix using pycwt.
    Returns dictionary of results in memory.
    """
    model_id, model_row, sep_km, n_sources_total, n_wedges, r_min, r_max = args
    
    start_time = time.time()
    temp_dir = tempfile.mkdtemp(prefix=f"wvsim_{model_id}_")
    
    try:
        # 1. Create velocity model file for CPS
        h_km = model_row['H_km'].values
        vp_kms = model_row['VP_kms'].values
        vs_kms = model_row['VS_kms'].values
        rho_gcc = model_row['RHO_gcc'].values
        
        n_layers = len(h_km)
        model_file = os.path.join(temp_dir, "model.d")
        with open(model_file, 'w') as f:
            f.write(f"MODEL.01\nModel {model_id}\nISOTROPIC\nKGS\nFLAT EARTH\n1-D\nCONSTANT VELOCITY\n")
            f.write("LINE08\nLINE09\nLINE10\nLINE11\n")
            f.write(f"H(KM) VP(KM/S) VS(KM/S) RHO(GM/CC) QP QS ETAP ETAS FREFP FREFS\n")
            for i in range(n_layers):
                qp = 500.0 if i < n_layers - 1 else 1000.0
                qs = 250.0 if i < n_layers - 1 else 500.0
                f.write(f"{h_km[i]:.4f} {vp_kms[i]:.4f} {vs_kms[i]:.4f} {rho_gcc[i]:.4f} "
                        f"{qp:.1f} {qs:.1f} 0 0 1.0 1.0\n")
                
        # 2. Run sdisp96
        with open(os.path.join(temp_dir, "ddisp96.dat"), 'w') as f:
            f.write("model.d\n33\n")
            periods = np.logspace(np.log10(1.0), np.log10(100.0), 33)
            for p in periods:
                f.write(f"{p:.4f}\n")
                
        run_command("sprep96 -M model.d -NMOD 1 -R -L -d ddisp96.dat", cwd=temp_dir)
        run_command("sdisp96", cwd=temp_dir)
        
        # 3. Read SREGN.ASC for theoretical dispersion
        period_theory, phase_theory, group_theory = [], [], []
        run_command("sdpegn96 -R -U -ASC", cwd=temp_dir)
        sregn_path = os.path.join(temp_dir, "SREGN.ASC")
        if os.path.exists(sregn_path):
            with open(sregn_path, 'r') as f:
                for line in f:
                    if line.strip() and not line.startswith("SURF96"):
                        parts = line.split()
                        if len(parts) >= 6:
                            try:
                                mode = int(parts[1])
                                if mode == 0:
                                    period_theory.append(float(parts[2]))
                                    phase_theory.append(float(parts[3]))
                                    group_theory.append(float(parts[4]))
                            except: pass
                            
        # 4. Generate sprep96 dfile
        TMAX, DELTA, NPT = 3599.0, 0.5, 8192
        max_dist_overall = r_max + sep_km + 50.0
        min_dist_overall = max(0.0, r_min - sep_km - 50.0)
        
        min_dist_overall = np.floor(min_dist_overall / 5.0) * 5.0
        max_dist_overall = np.ceil(max_dist_overall / 5.0) * 5.0
        eval_dists = np.arange(min_dist_overall, max_dist_overall + 1.0, 5.0)
        
        with open(os.path.join(temp_dir, "dfile"), "w") as f:
            for d in eval_dists:
                f.write(f"{d} {DELTA} {NPT} 0.0 0.0\n")
                
        cmds = [
            f"sprep96 -M model.d -HS 0.0 -HR 0.0 -NMOD 1 -R -L -d dfile",
            "sdisp96",
            "sregn96 -NOQ",
            "slegn96 -NOQ",
            "sdpegn96 -R -U -ASC",
            "sdpegn96 -L -U -ASC",
            "sdpsrf96 -R -ASC",
            "sdpsrf96 -L -ASC"
        ]
        
        for cmd in cmds:
            if not run_command(cmd, cwd=temp_dir):
                raise RuntimeError(f"{cmd} failed")
                
        lut = {}
        for d in eval_dists:
            with open(os.path.join(temp_dir, "dfile_src"), "w") as f:
                f.write(f"{d} {DELTA} {NPT} 0.0 0.0\n")
                
            cmd = f"{CPS_BIN}/spulse96 -d dfile_src -p -l 2 -V -EXF"
            env = os.environ.copy()
            env['PATH'] = f"{CPS_BIN}:{env['PATH']}"
            res = subprocess.run(cmd, shell=True, env=env, cwd=temp_dir, capture_output=True, text=True)
            if res.returncode != 0:
                continue
                
            # Parse output
            lines = res.stdout.strip().split('\n')
            curr_comp, curr_data, skip = None, [], 0
            comps = {}
            for line in lines:
                line = line.strip()
                if not line: continue
                if line in ['ZEX', 'REX', 'ZVF', 'RVF', 'ZHF', 'RHF', 'THF']:
                    if curr_comp: comps[curr_comp] = np.array(curr_data, dtype=np.float32)
                    curr_comp, curr_data, skip = line, [], 2
                    continue
                if skip > 0: skip -= 1; continue
                if curr_comp:
                    for x in line.split():
                        try: curr_data.append(float(x))
                        except ValueError: pass
                        
            if curr_comp: 
                comps[curr_comp] = np.array(curr_data, dtype=np.float32)
                
            if 'ZVF' in comps:
                lut[round(d, 1)] = comps
                
        # 5. Simulate wedges
        TMAX_SAMPLES = int(3599.0 / 0.5) + 1
        global_cross_power = np.zeros(TMAX_SAMPLES, dtype=np.complex128)
        global_p11 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
        global_p22 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
        
        rx1, ry1 = -sep_km/2, 0.0
        rx2, ry2 = sep_km/2, 0.0
        sources_per_wedge = n_sources_total // n_wedges
        wedge_width = 360.0 / n_wedges
        
        # Pre-allocate stacked traces
        stack1 = np.zeros(NPT, dtype=np.float32)
        stack2 = np.zeros(NPT, dtype=np.float32)
        
        # Generate all sources and compute nearest discrete distances in one vectorized step
        total_sources = sources_per_wedge * n_wedges
        np.random.seed(int(model_id.split('_')[-1])) # reproducible
        radii = np.random.uniform(r_min, r_max, total_sources)
        angles = np.random.uniform(0, 360, total_sources)
        sx = radii * np.cos(np.radians(angles))
        sy = radii * np.sin(np.radians(angles))
        
        d1 = np.sqrt((sx - rx1)**2 + (sy - ry1)**2)
        d2 = np.sqrt((sx - rx2)**2 + (sy - ry2)**2)
        
        # Snap to nearest 5.0 km
        d1_nearest = np.round(d1 / 5.0) * 5.0
        d2_nearest = np.round(d2 / 5.0) * 5.0
        
        dt_sim = 0.5
        downsample_factor = int(dt_sim / DELTA)
        
        fn = np.random.uniform(-1, 1, total_sources)
        fe = np.random.uniform(-1, 1, total_sources)
        fd = np.random.uniform(-1, 1, total_sources)
        
        # We process in chunks to introduce noise phase shifts efficiently
        chunk_size = 10000
        for i in range(0, total_sources, chunk_size):
            if i % (chunk_size * 10) == 0:
                print(f"[{model_id}] Processed {i}/{total_sources} noise sources...")
            d1_c = d1_nearest[i:i+chunk_size]
            d2_c = d2_nearest[i:i+chunk_size]
            
            shifts = np.random.uniform(-10.0, 10.0, len(d1_c))
            shift_idx = (shifts / DELTA).astype(int)
            
            for j in range(len(d1_c)):
                key1 = round(d1_c[j], 1)
                key2 = round(d2_c[j], 1)
                
                if key1 in lut and key2 in lut:
                    g1 = lut[key1]
                    g2 = lut[key2]
                    
                    # Source location
                    src_idx = i + j
                    x, y = sx[src_idx], sy[src_idx]
                    
                    # R1
                    dx1, dy1 = rx1 - x, ry1 - y
                    az1 = np.arctan2(dx1, dy1) * 180.0 / np.pi
                    if az1 < 0: az1 += 360.0
                    baz1 = az1 + 180 if az1 < 180 else az1 - 180
                    fR1, fT1, fZ1 = rotate_forces(fn[src_idx], fe[src_idx], fd[src_idx], az1)
                    uZ1, _, _ = compute_zne(g1, fR1, fT1, fZ1, baz1)
                    
                    # R2
                    dx2, dy2 = rx2 - x, ry2 - y
                    az2 = np.arctan2(dx2, dy2) * 180.0 / np.pi
                    if az2 < 0: az2 += 360.0
                    baz2 = az2 + 180 if az2 < 180 else az2 - 180
                    fR2, fT2, fZ2 = rotate_forces(fn[src_idx], fe[src_idx], fd[src_idx], az2)
                    uZ2, _, _ = compute_zne(g2, fR2, fT2, fZ2, baz2)
                    
                    idx = shift_idx[j]
                    if idx > 0:
                        stack1[idx:] += uZ1[:-idx]
                        stack2[idx:] += uZ2[:-idx]
                    elif idx < 0:
                        stack1[:idx] += uZ1[-idx:]
                        stack2[:idx] += uZ2[-idx:]
                    else:
                        stack1 += uZ1
                        stack2 += uZ2
                    
        # Downsample stacks
        stack1_ds = stack1[::downsample_factor]
        stack2_ds = stack2[::downsample_factor]
        
        # Cross correlation
        S1 = np.fft.fft(stack1_ds, n=TMAX_SAMPLES)
        S2 = np.fft.fft(stack2_ds, n=TMAX_SAMPLES)
        cross_power = S1 * np.conj(S2)
        p11 = np.abs(S1)**2
        p22 = np.abs(S2)**2
        
        ccf_ifft = np.fft.ifft(cross_power).real
        ccf_final = np.fft.fftshift(ccf_ifft)
        
        # Coherence
        den = np.sqrt(p11 * p22)
        coherence = np.zeros_like(cross_power, dtype=np.complex128)
        mask = den > 0
        coherence[mask] = np.real(cross_power[mask]) / den[mask]
        coherence = coherence.real
        
        # Trim CCF
        MAX_LAG_SAMPLES = int(500.0 / dt_sim)
        mid = len(ccf_final) // 2
        s_idx = mid - MAX_LAG_SAMPLES
        e_idx = mid + MAX_LAG_SAMPLES + 1
        lags_s = (np.arange(-MAX_LAG_SAMPLES, MAX_LAG_SAMPLES + 1)) * dt_sim
        ccf_trimmed = ccf_final[s_idx:e_idx]
        freqs_hz = np.fft.fftfreq(TMAX_SAMPLES, d=dt_sim)
        
        # 6. Compute FTAN using pycwt
        # Extract empirical green's function (positive lag of derivative)
        egf = -np.gradient(ccf_trimmed)
        egf = egf[MAX_LAG_SAMPLES:] # Positive lags only
        
        dj = 1 / 12  # Twelve sub-octaves per octave
        s0 = 2 * dt_sim  # Starting scale
        J = 7 / dj   # Seven powers of two
        wvn = 'morlet'
        
        cwt, sj, freq_ftan, coi, _, _ = pycwt.cwt(egf, dt_sim, dj, s0, J, wvn)
        rcwt = np.abs(cwt)**2
        
        # Normalize envelope
        for i in range(len(freq_ftan)):
            row_max = np.max(rcwt[i,:])
            if row_max > 0:
                rcwt[i,:] /= row_max
                
        period_s = 1.0 / freq_ftan
        velocity_kms = (np.arange(len(egf)) * dt_sim)
        velocity_kms = sep_km / (velocity_kms + 1e-6) # Avoid div by zero
        
        # Result dictionary
        result = {
            'model_id': model_id,
            'model_family': model_id.split('_')[0],
            'status': 'success',
            'time_s': time.time() - start_time,
            'velocity_profile': {
                'H_km': h_km,
                'VP_kms': vp_kms,
                'VS_kms': vs_kms,
                'RHO_gcc': rho_gcc
            },
            'theoretical': {
                'period': np.array(period_theory),
                'phase_velocity_dispersion': np.array(phase_theory),
                'group_velocity_dispersion': np.array(group_theory)
            },
            'ccf_isotropic': {
                'lags_s': lags_s,
                'freqs_hz': freqs_hz,
                'CCF_ZZ': ccf_trimmed,
                'COH_REAL_ZZ': coherence,
                'stack_sta1_Z': stack1_ds,
                'stack_sta2_Z': stack2_ds
            },
            'empirical_ftan_dispersion': {
                'FTAN_ZZ': rcwt,
                'period_s': period_s,
                'velocity_kms': velocity_kms
            }
        }
        
    except Exception as e:
        import traceback
        result = {
            'model_id': model_id,
            'status': 'error',
            'error': str(e),
            'traceback': traceback.format_exc()
        }
    finally:
        shutil.rmtree(temp_dir)
        
    return result

# -----------------------------------------------------------------------
# HDF5 Packaging
# -----------------------------------------------------------------------
def save_model_to_hdf5(h5_file, result, sep_km):
    """
    Saves the completely processed model to the master HDF5 file.
    Maintains legacy structure but omits uncomputed components.
    """
    if result['status'] != 'success':
        print(f"Skipping {result['model_id']} due to error: {result['error']}")
        return
        
    mod_grp = h5_file.require_group(f"simulations/{result['model_id']}")
    mod_grp.attrs['model_family'] = result['model_family']
    
    # 1. Velocity Profile
    vp_grp = mod_grp.require_group("velocity_profile")
    for k, v in result['velocity_profile'].items():
        if k in vp_grp: del vp_grp[k]
        vp_grp.create_dataset(k, data=v)
        
    # 2. Theoretical
    th_grp = mod_grp.require_group("theoretical")
    for k, v in result['theoretical'].items():
        if k in th_grp: del th_grp[k]
        th_grp.create_dataset(k, data=v)
        
    # 3. Geometries
    geom_grp = mod_grp.require_group(f"geometries/separation_{sep_km:.1f}km")
    
    # CCF Isotropic
    ccf_grp = geom_grp.require_group("ccf_isotropic")
    for k, v in result['ccf_isotropic'].items():
        if k in ccf_grp: del ccf_grp[k]
        ccf_grp.create_dataset(k, data=v)
        
    # FTAN
    ftan_grp = geom_grp.require_group("empirical_ftan_dispersion")
    for k, v in result['empirical_ftan_dispersion'].items():
        if k in ftan_grp: del ftan_grp[k]
        ftan_grp.create_dataset(k, data=v)

# -----------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=str, default="../01_parametrization/model_manifest.parquet")
    parser.add_argument("--output", type=str, default="wavenet_dataset_100k.h5")
    parser.add_argument("--cores", type=int, default=os.cpu_count())
    parser.add_argument("--limit", type=int, default=None, help="Process only N models (for testing)")
    parser.add_argument("--sep_km", type=float, default=127.0, help="Fixed station separation")
    args = parser.parse_args()

    # Setup multiprocessing safely
    try:
        multiprocessing.set_start_method('fork')
    except RuntimeError: pass

    models_df = pd.read_parquet(args.models)
    
    unique_models = models_df['Model_ID'].unique()
    if args.limit:
        unique_models = unique_models[:args.limit]
        
    n_sources = 1000000
    n_wedges = 360
    r_min = max(200.0, args.sep_km)
    r_max = r_min + 100.0
    
    # Build argument list
    tasks = []
    for m_id in unique_models:
        m_df = models_df[models_df['Model_ID'] == m_id]
        tasks.append((m_id, m_df, args.sep_km, n_sources, n_wedges, r_min, r_max))
        
    print(f"Spinning up {args.cores} cores for {len(tasks)} models using Model-Level Parallelization...")
    
    h5_file = h5py.File(args.output, 'a')
    
    start_global = time.time()
    success_count = 0
    
    with multiprocessing.Pool(args.cores) as pool:
        for i, result in enumerate(pool.imap_unordered(process_model, tasks)):
            if result['status'] == 'success':
                success_count += 1
            else:
                print(f"[{i+1}/{len(tasks)}] Error on {result['model_id']}: {result['error']}")
                print(result.get('traceback', ''))
                
            save_model_to_hdf5(h5_file, result, args.sep_km)
            
            elapsed = time.time() - start_global
            avg_time = elapsed / (i + 1)
            rem = (len(tasks) - (i + 1)) * avg_time
            print(f"[{i+1}/{len(tasks)}] Completed {result['model_id']} in {result.get('time_s', 0):.1f}s | "
                  f"Avg: {avg_time:.1f}s/mod | Est. Rem: {rem/3600:.1f}h")
                  
    h5_file.close()
    print(f"Finished! Successfully processed {success_count}/{len(tasks)} models in {time.time()-start_global:.1f}s.")

if __name__ == "__main__":
    main()
