#!/usr/bin/env python3
import numpy as np
import pandas as pd
import h5py
import subprocess
import os
import time
import math
import tempfile
import shutil
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import cpu_count
import argparse
import getpass

if getpass.getuser() == 'tolugboj':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'
else:
    CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'

def setup_cps_env():
    env = os.environ.copy()
    env['PATH'] = f"{CPS_BIN}:{env['PATH']}"
    return env

def get_vmin_from_model_df(model_df):
    vs = model_df['VS_kms'].values
    vs_nonzero = vs[vs > 0]
    return np.min(vs_nonzero) if len(vs_nonzero) > 0 else 2.5

def precompute_greens_functions(model_df, workdir, r_max):
    # Write MODEL.01
    mod_path = os.path.join(workdir, 'model.d')
    rows = model_df.reset_index(drop=True)
    with open(mod_path, 'w') as f:
        f.write("MODEL.01\nWavenet\nISOTROPIC\nKGS\nSPHERICAL EARTH\n1-D\nDATA\n")
        f.write("H(KM)   VP(KM/S)   VS(KM/S) RHO(GM/CC)     QP         QS       ETAP       ETAS      FREFP      FREFS\n")
        for i, row in rows.iterrows():
            h = 0.0 if i == len(rows) - 1 else row['H_km']
            f.write(f"{h:8.4f} {row['VP_kms']:8.4f} {row['VS_kms']:8.4f} {row['RHO_gcc']:8.4f} 200.0 70.0 0.0 0.0 1.0 1.0\n")

    VMIN_MODEL = get_vmin_from_model_df(model_df)
    DIST_MAX = np.sqrt(2) * r_max
    DELTA = 0.5
    NPT = int((DIST_MAX / VMIN_MODEL) / DELTA)
    NPT = 2 ** math.ceil(math.log2(NPT))

    dfile_path = os.path.join(workdir, 'dfile')
    with open(dfile_path, 'w') as f:
        f.write(f"{DIST_MAX} {DELTA} {NPT} 0.0 0.0\n")

    env = setup_cps_env()
    cmds = [
        f'{CPS_BIN}/sprep96 -M model.d -HS 0 -HR 0 -L -R -NMOD 10 -d dfile',
        f'{CPS_BIN}/sdisp96',
        f'{CPS_BIN}/sregn96 -NOQ', f'{CPS_BIN}/slegn96 -NOQ',
        f'{CPS_BIN}/sdpegn96 -R -U -ASC', f'{CPS_BIN}/sdpegn96 -L -U -ASC',
        f'{CPS_BIN}/sdpsrf96 -R -ASC', f'{CPS_BIN}/sdpsrf96 -L -ASC'
    ]

    for cmd in cmds:
        res = subprocess.run(cmd, shell=True, env=env, cwd=workdir, capture_output=True)
        if res.returncode != 0:
            print(f"Failed CMD: {cmd}")
            print(res.stderr.decode())
            return False, NPT
            
    return True, NPT

def parse_spulse96(output):
    lines = output.strip().split('\n')
    comps = {}
    curr_comp, curr_data, skip = None, [], 0
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
    if curr_comp: comps[curr_comp] = np.array(curr_data, dtype=np.float32)
    return comps

def build_lut(workdir, r_max, npt_fixed):
    max_dist = np.sqrt(2) * r_max
    distances = np.arange(50.0, max_dist + 1.0, 0.5)
    env = setup_cps_env()
    
    lut = {}
    for i, d in enumerate(distances):
        with open(os.path.join(workdir, 'dfile_src'), 'w') as f:
            f.write(f"{d} 0.5 {npt_fixed} 0.0 0.0\n")
        cmd = f'{CPS_BIN}/spulse96 -d dfile_src -p -l 2 -V -EXF'
        out = subprocess.run(cmd, shell=True, env=env, cwd=workdir, capture_output=True, text=True).stdout
        lut[round(d, 1)] = parse_spulse96(out)
        
    return lut

def rotate_forces(f1, f2, f3, azimuth):
    az = azimuth * np.pi / 180.0
    return f1*np.cos(az) + f2*np.sin(az), -f1*np.sin(az) + f2*np.cos(az), f3

def compute_zne(greens, fR, fT, fZ, backazimuth):
    uZ = fZ * greens.get('ZVF',0) + fR * greens.get('ZHF',0)
    uR = fR * greens.get('RHF',0) + fZ * greens.get('RVF',0)
    uT = fT * greens.get('THF',0) if 'THF' in greens else np.zeros_like(uZ)
    
    baz = backazimuth * np.pi / 180.0
    return uZ, -np.cos(baz)*uR + np.sin(baz)*uT, -np.sin(baz)*uR - np.cos(baz)*uT

def process_wedge(args):
    wedge_id, wedge_min, wedge_max, sources_per_wedge, r_min, r_max, rx1, ry1, rx2, ry2, stack_length, npt_fixed, lut = args
    TMAX, DELTA = 3599.0, 0.5
    
    wedge_r1 = {'Z': np.zeros(stack_length)}
    wedge_r2 = {'Z': np.zeros(stack_length)}
    
    for i in range(sources_per_wedge):
        global_source_id = wedge_id * sources_per_wedge + i
        np.random.seed(42 + global_source_id)
        
        theta = np.random.uniform(wedge_min, wedge_max) * np.pi / 180.0
        r = np.random.uniform(r_min, r_max)
        x, y = r * np.cos(theta), r * np.sin(theta)
        fn, fe, fd = np.random.uniform(-1, 1), np.random.uniform(-1, 1), np.random.uniform(-1, 1)
        
        tshift = i * (TMAX / sources_per_wedge)
        shift_samples = int(tshift / DELTA)
        
        for rx, ry, stack in [(rx1, ry1, wedge_r1), (rx2, ry2, wedge_r2)]:
            dx, dy = rx - x, ry - y
            dist = np.sqrt(dx**2 + dy**2)
            if dist < 50.0 or dist > 1400.0: continue
            
            nearest_d = round(round(dist * 2) / 2.0, 1)
            if nearest_d not in lut: continue
            greens = lut[nearest_d]
            if not greens: continue
            
            azimuth = np.arctan2(dx, dy) * 180.0 / np.pi
            if azimuth < 0: azimuth += 360.0
            baz = azimuth + 180 if azimuth < 180 else azimuth - 180 if azimuth > 180 else 0
            
            fR, fT, fZ = rotate_forces(fn, fe, fd, azimuth)
            uZ, _, _ = compute_zne(greens, fR, fT, fZ, baz)
            
            end = min(shift_samples + len(uZ), stack_length)
            stack['Z'][shift_samples:end] += uZ[:end-shift_samples]
            
    TMAX_SAMPLES = int(TMAX / DELTA) + 1
    r1 = wedge_r1['Z'][:TMAX_SAMPLES]
    r2 = wedge_r2['Z'][:TMAX_SAMPLES]
    
    fft_r1 = np.fft.fft(r1)
    fft_r2 = np.fft.fft(r2)
    cross_power = fft_r1 * np.conj(fft_r2)
    p11 = np.abs(fft_r1)**2
    p22 = np.abs(fft_r2)**2
    
    return cross_power, p11, p22

def extract_dispersion(workdir):
    per, grp, pha = [], [], []
    try:
        with open(os.path.join(workdir, 'SREGN.ASC'), 'r') as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 6:
                    per.append(float(parts[2]))
                    pha.append(float(parts[4]))
                    grp.append(float(parts[5]))
    except: pass
    return {'period': np.array(per), 'phase': np.array(pha), 'group': np.array(grp)}

def run_simulation(model_df, geom_subset, shard_path):
    model_id = model_df['Model_ID'].iloc[0]
    
    temp_dir = tempfile.mkdtemp(prefix=f"wavenet_v4_{model_id}_")
    try:
        # Determine worst-case r_max based on all geometries
        max_sep = geom_subset['Station_Separation_km'].max()
        r_min = max(200.0, max_sep)
        r_max = r_min + 100.0
        
        print("Pre-computing eigenfunctions...", flush=True)
        success, NPT_FIXED = precompute_greens_functions(model_df, temp_dir, r_max)
        if not success:
            return False
            
        dispersion = extract_dispersion(temp_dir)
            
        print("Building LUT...", flush=True)
        lut = build_lut(temp_dir, r_max, NPT_FIXED)
        
        with h5py.File(shard_path, 'a') as h5f:
            model_key = f"simulations/{model_id}"
            if model_key in h5f: del h5f[model_key]
            grp = h5f.require_group(model_key)
            grp.attrs['model_family'] = model_id.split('_')[0]
            
            # Save 1D Profile
            prof_grp = grp.require_group("velocity_profile")
            prof_grp.create_dataset("H_km", data=model_df['H_km'].values)
            prof_grp.create_dataset("VP_kms", data=model_df['VP_kms'].values)
            prof_grp.create_dataset("VS_kms", data=model_df['VS_kms'].values)
            prof_grp.create_dataset("RHO_gcc", data=model_df['RHO_gcc'].values)
            
            # Save Theoretical Ground Truth
            theo_grp = grp.require_group("theoretical")
            theo_grp.create_dataset("period", data=dispersion['period'])
            theo_grp.create_dataset("phase_velocity_dispersion", data=dispersion['phase'])
            theo_grp.create_dataset("group_velocity_dispersion", data=dispersion['group'])
            
            for _, geom in geom_subset.iterrows():
                sep_km = geom['Station_Separation_km']
                rx1, ry1 = -sep_km/2, 0.0
                rx2, ry2 = sep_km/2, 0.0
                
                sim_grp = grp.require_group(f"geometries/separation_{sep_km:.1f}km")
                
                n_sources_total = 1000000
                n_wedges = 360
                sources_per_wedge = n_sources_total // n_wedges
                TMAX_SAMPLES = int(3599.0 / 0.5) + 1
                STACK_LENGTH = TMAX_SAMPLES + NPT_FIXED
                
                wedges = []
                wedge_width = 360.0 / n_wedges
                for i in range(n_wedges):
                    w_min = i * wedge_width
                    w_max = (i + 1) * wedge_width
                    wedges.append((i, w_min, w_max, sources_per_wedge, r_min, r_max, rx1, ry1, rx2, ry2, STACK_LENGTH, NPT_FIXED, lut))

                cores = min(cpu_count(), 16) # Don't overwhelm local machine completely
                global_cross_power = np.zeros(TMAX_SAMPLES, dtype=np.complex128)
                global_p11 = np.zeros(TMAX_SAMPLES, dtype=np.float64)
                global_p22 = np.zeros(TMAX_SAMPLES, dtype=np.float64)

                print(f"Simulating geometry {sep_km}km using {n_wedges} wedges...")
                with ProcessPoolExecutor(max_workers=cores) as executor:
                    for cp, p11, p22 in executor.map(process_wedge, wedges):
                        global_cross_power += cp
                        global_p11 += p11
                        global_p22 += p22
                        
                avg_cross_power = global_cross_power / n_wedges
                avg_p11 = global_p11 / n_wedges
                avg_p22 = global_p22 / n_wedges
                
                ccf_ifft = np.fft.ifft(avg_cross_power).real
                ccf_final = np.fft.fftshift(ccf_ifft)
                
                # Coherence
                den = np.sqrt(avg_p11 * avg_p22)
                coherence = np.zeros_like(avg_cross_power, dtype=np.complex128)
                mask = den > 0
                coherence[mask] = np.real(avg_cross_power[mask]) / den[mask]
                coherence = coherence.real
                
                freqs = np.fft.fftfreq(TMAX_SAMPLES, d=0.5)
                
                MAX_LAG_SAMPLES = int(500.0 / 0.5)
                mid = len(ccf_final) // 2
                s_idx = mid - MAX_LAG_SAMPLES
                e_idx = mid + MAX_LAG_SAMPLES + 1
                lags_trimmed = (np.arange(-MAX_LAG_SAMPLES, MAX_LAG_SAMPLES + 1)) * 0.5
                
                sc_name = 'ccf_isotropic'
                sc_grp = sim_grp.require_group(sc_name)
                sc_grp.attrs['n_wedges_used'] = n_wedges
                sc_grp.attrs['n_sources_used'] = n_sources_total
                
                sc_grp.create_dataset('lags_s', data=lags_trimmed)
                sc_grp.create_dataset('freqs_hz', data=freqs)
                sc_grp.create_dataset('CCF_ZZ', data=ccf_final[s_idx:e_idx])
                sc_grp.create_dataset('COH_REAL_ZZ', data=coherence)
                
                ftan_grp = sim_grp.require_group("empirical_ftan_dispersion")
                ftan_grp.attrs['status'] = 'pending_ftan_computation'
                
                print(f"Saved {model_id} - {sep_km}km")
                
    finally:
        shutil.rmtree(temp_dir)
        
    return True

def main():
    import multiprocessing
    try:
        multiprocessing.set_start_method('fork')
    except RuntimeError: pass
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=str, default="../01_parametrization/model_manifest.parquet")
    parser.add_argument("--sims", type=str, default="../01_parametrization/simulation_manifest.parquet")
    parser.add_argument("--output", type=str, default="output_dataset_v4")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    
    os.makedirs(args.output, exist_ok=True)
    models_df = pd.read_parquet(args.models)
    sims_df = pd.read_parquet(args.sims)
    
    unique_models = models_df['Model_ID'].unique()
    if args.test:
        unique_models = unique_models[:1]
        
    for m_id in unique_models:
        m_df = models_df[models_df['Model_ID'] == m_id]
        geom_subset = sims_df[sims_df['Model_ID'] == m_id]
        if args.test: geom_subset = geom_subset.head(1)
        
        shard_path = os.path.join(args.output, f"dataset_shard_000.h5")
        run_simulation(m_df, geom_subset, shard_path)

if __name__ == '__main__':
    main()
