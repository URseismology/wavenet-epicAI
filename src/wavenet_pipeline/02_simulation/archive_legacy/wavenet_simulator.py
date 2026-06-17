#!/usr/bin/env python3
"""
Wavenet EpicAI Python Simulator
Phase 3 Core Engine

Interpolates a single spulse96 LUT to generate millions of source geometries.
Builds Azimuthal ML Scenarios (Wedges) and extracts empirical FTAN arrays.
Saves data into lock-free sharded HDF5.
"""

import os
import sys
import tempfile
import shutil
import subprocess
import numpy as np
import pandas as pd
import h5py
from multiprocessing import Pool
import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
import math
from scipy import signal
from scipy.signal import windows, detrend

try:
    import obspy
except ImportError:
    logging.warning("Obspy not found. Waveform parsing will fail.")

CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'

# Constants
ANNULUS_RMIN = 1000.0
ANNULUS_RMAX = 1100.0
MAX_STATION_SEP = 500.0
GRID_SPACING = 5.0
MIN_DIST = ANNULUS_RMIN - (MAX_STATION_SEP / 2.0)
MAX_DIST = ANNULUS_RMAX + (MAX_STATION_SEP / 2.0)

N_SOURCES = 10000
TMAX = 3600.0
DELTA = 0.5
TMAX_SAMPLES = int(TMAX / DELTA) + 1
FILTER_PERIOD_RANGE = [5.0, 150.0]
FILTER_FREQ_RANGE = [1.0/FILTER_PERIOD_RANGE[1], 1.0/FILTER_PERIOD_RANGE[0]]
MAX_LAG = 500.0
MAX_LAG_SAMPLES = int(MAX_LAG / DELTA)

def setup_cps_env():
    env = os.environ.copy()
    env['PATH'] = f"{CPS_BIN}:{env['PATH']}"
    return env

def get_vmin(model_df):
    vs = model_df['VS_kms'].values
    vs_nonzero = vs[vs > 0]
    return np.min(vs_nonzero) if len(vs_nonzero) > 0 else 2.5

def fast_interp_2d(d_array, min_d, spacing, grid):
    i = ((d_array - min_d) / spacing).astype(int)
    i = np.clip(i, 0, grid.shape[0] - 2)
    f = ((d_array - (min_d + i*spacing)) / spacing)[:, None]
    return grid[i] * (1-f) + grid[i+1] * f

def bandpass_filter_freq(fft_data, freq_range, dt):
    n = len(fft_data)
    freqs = np.fft.fftfreq(n, dt)
    filt = np.zeros(n)
    f_min, f_max = freq_range
    taper_width = 0.2 * (f_max - f_min)
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

def compute_ccf_engine(r1_arr, r2_arr):
    r1_proc = detrend(r1_arr) * windows.tukey(len(r1_arr), alpha=0.05)
    r2_proc = detrend(r2_arr) * windows.tukey(len(r2_arr), alpha=0.05)
    
    fft_r1 = np.fft.fft(r1_proc)
    fft_r2 = np.fft.fft(r2_proc)
    cross_power = fft_r1 * np.conj(fft_r2)
    
    # Coherence
    den = np.abs(fft_r1) * np.abs(fft_r2)
    coherence = np.zeros_like(cross_power, dtype=np.complex128)
    mask = den > 0
    coherence[mask] = cross_power[mask] / den[mask]
    
    # CCF
    ccf_ifft = np.fft.ifft(cross_power).real
    ccf_ifft = np.fft.fftshift(ccf_ifft)
    ccf_ifft = detrend(ccf_ifft)
    taper_len = int(len(ccf_ifft) * 0.05)
    taper = np.ones(len(ccf_ifft))
    if taper_len > 0:
        taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
        taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    ccf_ifft = ccf_ifft * taper
    
    ccf_fft = np.fft.fft(np.fft.fftshift(ccf_ifft))
    ccf_filtered = bandpass_filter_freq(ccf_fft, FILTER_FREQ_RANGE, DELTA)
    ccf_final = np.fft.fftshift(np.fft.ifft(ccf_filtered).real)
    
    # Pos coherence
    freqs = np.fft.fftfreq(len(r1_arr), d=DELTA)
    pos_mask = freqs > 0
    
    return ccf_final, coherence[pos_mask].real, freqs[pos_mask]

def generate_lut(model_df: pd.DataFrame, work_dir: str):
    env = setup_cps_env()
    
    # 1. Write MODEL.01
    # CRITICAL: CPS sprep96 requires the last (half-space) layer to have H=0.0
    # Without this, sprep96 throws a Fortran EOF error and the LUT is all zeros.
    mod_path = os.path.join(work_dir, 'MODEL.01')
    rows = model_df.reset_index(drop=True)
    with open(mod_path, 'w') as f:
        f.write("MODEL.01\nWavenet\nISOTROPIC\nKGS\nSPHERICAL EARTH\n1-D\nDATA\n")
        f.write("H(KM)   VP(KM/S)   VS(KM/S) RHO(GM/CC)     QP         QS       ETAP       ETAS      FREFP      FREFS\n")
        for i, row in rows.iterrows():
            h = 0.0 if i == len(rows) - 1 else row['H_km']  # Force last layer H=0
            f.write(f"{h:8.4f} {row['VP_kms']:8.4f} {row['VS_kms']:8.4f} {row['RHO_gcc']:8.4f} 200.0 70.0 0.0 0.0 1.0 1.0\n")
            
    # 2. Write dfile
    distances = np.arange(MIN_DIST, MAX_DIST + GRID_SPACING, GRID_SPACING)
    
    vmin = get_vmin(model_df)
    npts_needed = int((MAX_DIST / vmin) / DELTA)
    npts = 2 ** math.ceil(math.log2(npts_needed))
    
    dfile_path = os.path.join(work_dir, 'dfile')
    with open(dfile_path, 'w') as f:
        for d in distances:
            f.write(f"{d:.2f} {DELTA} {npts} 0.0 0.0\n")
            
    # 3. CPS commands
    cmds = [
        f"{CPS_BIN}/sprep96 -M MODEL.01 -d dfile -R -L -PMIN 5 -PMAX 150 -NMOD 1",
        f"{CPS_BIN}/sdisp96",
        f"{CPS_BIN}/sregn96 -NOQ",
        f"{CPS_BIN}/slegn96 -NOQ",
        f"{CPS_BIN}/spulse96 -d dfile -V -p -l 2 -EXF > file96",
        f"{CPS_BIN}/f96tosac -B file96"
    ]
    
    for cmd in cmds:
        res = subprocess.run(cmd, shell=True, env=env, cwd=work_dir, capture_output=True)
        if res.returncode != 0:
            logging.error(f"Command Failed: {cmd}\n{res.stderr.decode()}")
            return None
            
    # 4. Extract Theoretical Dispersion Ground Truth
    subprocess.run(f"{CPS_BIN}/sdpegn96 -R -U -ASC", shell=True, env=env, cwd=work_dir, capture_output=True)
    per, grp, pha = [], [], []
    try:
        with open(os.path.join(work_dir, 'SREGN.ASC'), 'r') as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 6:
                    per.append(float(parts[2]))
                    pha.append(float(parts[4]))
                    grp.append(float(parts[5]))
    except:
        pass
        
    disp_dict = {'period': np.array(per), 'phase': np.array(pha), 'group': np.array(grp)}
            
    # 5. Parse SAC files into LUT
    # f96tosac -B produces: B{dist_idx:03d}{trace_num:02d}{COMP}.sac
    # The trace numbers are fixed per component within each distance block:
    #   ZEX=09, REX=10, ZVF=11, RVF=12, ZHF=13, RHF=14, THF=15
    lut_dict = {'distances': distances, 'npts': npts, 'dispersion': disp_dict}
    components = ['ZVF', 'RVF', 'ZHF', 'RHF', 'THF', 'ZEX', 'REX']
    comp_trace = {'ZEX': 9, 'REX': 10, 'ZVF': 11, 'RVF': 12, 'ZHF': 13, 'RHF': 14, 'THF': 15}
    
    for comp in components:
        lut_dict[comp] = np.zeros((len(distances), npts), dtype=np.float32)
        
    for i, d in enumerate(distances):
        dist_idx = i + 1  # 1-based
        for comp in components:
            trace_num = comp_trace[comp]
            sac_name = f"B{dist_idx:03d}{trace_num:02d}{comp}.sac"
            sac_path = os.path.join(work_dir, sac_name)
            if os.path.exists(sac_path):
                try:
                    st = obspy.read(sac_path)
                    lut_dict[comp][i, :] = np.nan_to_num(st[0].data[:npts])
                    st.clear()
                    del st
                except Exception as e:
                    logging.warning(f"Could not read {sac_name}: {e}")
            else:
                logging.warning(f"SAC file not found: {sac_name}")
                    
    # Quick sanity check — always print so we can debug zero-waveform issues
    zvf_sum = np.sum(np.abs(lut_dict['ZVF']))
    n_nonzero = np.sum(lut_dict['ZVF'] != 0)
    status = "OK" if zvf_sum > 0 else "FAILED"
    print(f"LUT check: ZVF sum={zvf_sum:.4g}, nonzero_samples={n_nonzero}, status={status}", flush=True)
    
    return lut_dict


def process_model(args):
    model_df, geom_subset, shard_path = args
    model_id = model_df['Model_ID'].iloc[0]
    
    temp_dir = tempfile.mkdtemp(prefix=f"wavenet_{model_id}_")
    try:
        logging.info("Generating LUT...")
        lut = generate_lut(model_df, temp_dir)
        if lut is None:
            return False
            
        with h5py.File(shard_path, 'a') as h5f:
            # Delete stale group from any prior run so create_dataset never hits 'already exists'
            model_key = f"simulations/{model_id}"
            if model_key in h5f:
                del h5f[model_key]
            grp = h5f.require_group(model_key)
            logging.info("Saving to HDF5...")
            grp.attrs['model_family'] = model_id.split('_')[0]
            
            # Save 1D Profile
            prof_grp = grp.require_group("velocity_profile")
            prof_grp.create_dataset("H_km", data=model_df['H_km'].values)
            prof_grp.create_dataset("VP_kms", data=model_df['VP_kms'].values)
            prof_grp.create_dataset("VS_kms", data=model_df['VS_kms'].values)
            prof_grp.create_dataset("RHO_gcc", data=model_df['RHO_gcc'].values)
            
            # Save Theoretical Ground Truth
            theo_grp = grp.require_group("theoretical")
            theo_grp.create_dataset("period", data=lut['dispersion']['period'])
            theo_grp.create_dataset("phase_velocity_dispersion", data=lut['dispersion']['phase'])
            theo_grp.create_dataset("group_velocity_dispersion", data=lut['dispersion']['group'])
            
            # Generate the 10,000 global sources and point forces
            np.random.seed(42 + int(model_id.split('_')[-1]))
            global_r = np.random.uniform(ANNULUS_RMIN, ANNULUS_RMAX, N_SOURCES)
            global_theta = np.random.uniform(0, 360, N_SOURCES)
            global_x = global_r * np.cos(np.deg2rad(global_theta))
            global_y = global_r * np.sin(np.deg2rad(global_theta))
            
            global_fn = np.random.uniform(-1, 1, N_SOURCES)
            global_fe = np.random.uniform(-1, 1, N_SOURCES)
            global_fd = np.random.uniform(-1, 1, N_SOURCES)
            
            tshifts = np.linspace(0, TMAX, N_SOURCES)
            stack_length = TMAX_SAMPLES + lut['npts']
            
            import scipy.fft
            nfft = scipy.fft.next_fast_len(stack_length)
            freqs_rfft = np.fft.rfftfreq(nfft, d=DELTA)
            phase_shifts = np.exp(-2j * np.pi * freqs_rfft[None, :] * tshifts[:, None])
            
            # Pre-allocate output arrays
            lags_trimmed = (np.arange(-MAX_LAG_SAMPLES, MAX_LAG_SAMPLES + 1)) * DELTA
            
            # Loop over cached geometries
            for _, geom in (geom_subset.head(1) if os.environ.get('TEST_1_GEOM') else geom_subset).iterrows():
                sep_km = geom['Station_Separation_km']
                sep_str = f"separation_{sep_km:.1f}km"
                sim_grp = grp.require_group(f"geometries/{sep_str}")
                
                # Receiver coordinates (along X axis)
                rx1, ry1 = -sep_km/2, 0.0
                rx2, ry2 = sep_km/2, 0.0
                
                # Setup 5 Scenario masks
                rand_ang = geom.get('Random_Wedge_Angle_deg', np.random.uniform(0, 360))
                sim_grp.attrs['random_50deg_azimuth_value'] = rand_ang
                
                scenarios = {
                    'ccf_isotropic': np.ones(N_SOURCES, dtype=bool),
                    'ccf_inline': (np.abs(global_theta) <= 15) | (np.abs(global_theta - 180) <= 15) | (np.abs(global_theta - 360) <= 15),
                    'ccf_broadside': (np.abs(global_theta - 90) <= 15) | (np.abs(global_theta - 270) <= 15),
                    'ccf_onesided': (global_theta >= 0) & (global_theta <= 180),
                    'ccf_random_50deg': (global_theta >= rand_ang - 25) & (global_theta <= rand_ang + 25)
                }
                
                # Handle angle wrap-around for 50deg wedge
                if rand_ang - 25 < 0:
                    scenarios['ccf_random_50deg'] |= (global_theta >= rand_ang - 25 + 360)
                elif rand_ang + 25 > 360:
                    scenarios['ccf_random_50deg'] |= (global_theta <= rand_ang + 25 - 360)
                
                # We calculate waves for all sources
                dx1 = rx1 - global_x
                dy1 = ry1 - global_y
                dist1 = np.sqrt(dx1**2 + dy1**2)
                az1 = np.rad2deg(np.arctan2(dx1, dy1)) % 360
                baz1 = (az1 + 180) % 360
                
                dx2 = rx2 - global_x
                dy2 = ry2 - global_y
                dist2 = np.sqrt(dx2**2 + dy2**2)
                az2 = np.rad2deg(np.arctan2(dx2, dy2)) % 360
                baz2 = (az2 + 180) % 360
                
                # 1. Receiver 1 interpolation
                zvf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['ZVF'])
                zhf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['ZHF'])
                rvf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['RVF'])
                rhf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['RHF'])
                thf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['THF'])
                
                # 2. Receiver 2 interpolation
                zvf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['ZVF'])
                zhf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['ZHF'])
                rvf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['RVF'])
                rhf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['RHF'])
                thf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['THF'])
                
                def compute_waves(zvf, zhf, rvf, rhf, thf, az, baz):
                    az_rad = np.deg2rad(az)
                    fR = global_fn * np.cos(az_rad) + global_fe * np.sin(az_rad)
                    fT = -global_fn * np.sin(az_rad) + global_fe * np.cos(az_rad)
                    fZ = global_fd
                    
                    uZ = fZ[:, None] * zvf + fR[:, None] * zhf
                    uR = fR[:, None] * rhf + fZ[:, None] * rvf
                    uT = fT[:, None] * thf
                    
                    baz_rad = np.deg2rad(baz)
                    uN = -np.cos(baz_rad)[:, None] * uR + np.sin(baz_rad)[:, None] * uT
                    uE = -np.sin(baz_rad)[:, None] * uR - np.cos(baz_rad)[:, None] * uT
                    return uZ, uN, uE
                
                # Compute waves for all sources at both receivers
                Z1, N1, E1 = compute_waves(zvf1, zhf1, rvf1, rhf1, thf1, az1, baz1)
                Z2, N2, E2 = compute_waves(zvf2, zhf2, rvf2, rhf2, thf2, az2, baz2)
                
                # Accumulate into each scenario
                for sc_name, mask in scenarios.items():
                    sc_grp = sim_grp.require_group(sc_name)
                    valid_idx = np.where(mask)[0]
                    
                    if len(valid_idx) == 0:
                        continue
                        
                    ps_valid = phase_shifts[valid_idx]
                    
                    def compute_stack(waves):
                        waves_fd = np.fft.rfft(waves[valid_idx], n=nfft, axis=1)
                        stack_fd = np.sum(waves_fd * ps_valid, axis=0)
                        return np.fft.irfft(stack_fd, n=nfft)[:stack_length]
                        
                    r1z = compute_stack(Z1)
                    r1n = compute_stack(N1)  # Transverse
                    r1e = compute_stack(E1)
                    r2z = compute_stack(Z2)
                    r2n = compute_stack(N2)
                    r2e = compute_stack(E2)
                    
                    # Save raw stacked waveforms for debugging (before CCF)
                    sc_grp.create_dataset('stack_sta1_Z', data=r1z)
                    sc_grp.create_dataset('stack_sta1_R', data=r1e)
                    sc_grp.create_dataset('stack_sta1_T', data=r1n)
                    sc_grp.create_dataset('stack_sta2_Z', data=r2z)
                    sc_grp.create_dataset('stack_sta2_R', data=r2e)
                    sc_grp.create_dataset('stack_sta2_T', data=r2n)
                    sc_grp.attrs['n_sources_used'] = int(valid_idx.shape[0])
                    
                    # Compute CCFs (ZZ, RR, TT, RZ, ZR)
                    # Recall: E is Radial, N is Transverse
                    ccf_zz, coh_zz, freqs = compute_ccf_engine(r1z, r2z)
                    ccf_rr, coh_rr, _ = compute_ccf_engine(r1e, r2e)
                    ccf_tt, coh_tt, _ = compute_ccf_engine(r1n, r2n)
                    ccf_rz, coh_rz, _ = compute_ccf_engine(r1e, r2z)
                    ccf_zr, coh_zr, _ = compute_ccf_engine(r1z, r2e)
                    
                    # Trim CCFs
                    mid = len(ccf_zz) // 2
                    s_idx = mid - MAX_LAG_SAMPLES
                    e_idx = mid + MAX_LAG_SAMPLES + 1
                    
                    ccf_zz = ccf_zz[s_idx:e_idx]
                    ccf_rr = ccf_rr[s_idx:e_idx]
                    ccf_tt = ccf_tt[s_idx:e_idx]
                    ccf_rz = ccf_rz[s_idx:e_idx]
                    ccf_zr = ccf_zr[s_idx:e_idx]
                    
                    # Save results
                    sc_grp.create_dataset('lags_s', data=lags_trimmed)
                    sc_grp.create_dataset('freqs_hz', data=freqs)
                    
                    sc_grp.create_dataset('CCF_ZZ', data=ccf_zz)
                    sc_grp.create_dataset('CCF_RR', data=ccf_rr)
                    sc_grp.create_dataset('CCF_TT', data=ccf_tt)
                    sc_grp.create_dataset('CCF_RZ', data=ccf_rz)
                    sc_grp.create_dataset('CCF_ZR', data=ccf_zr)
                    
                    sc_grp.create_dataset('COH_REAL_ZZ', data=coh_zz)
                    sc_grp.create_dataset('COH_REAL_RR', data=coh_rr)
                    sc_grp.create_dataset('COH_REAL_TT', data=coh_tt)
                    
                    print(f"    Saved scenario: {sc_name}")
                
                # Empirical FTAN placeholder (to be populated in Phase 4)
                ftan_grp = sim_grp.require_group("empirical_ftan_dispersion")
                ftan_grp.attrs['status'] = 'pending_ftan_computation'

    finally:
        shutil.rmtree(temp_dir)
        
    return True

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Wavenet Simulator Core")
    parser.add_argument("--models", type=str, default="../01_parametrization/model_manifest.parquet", help="Path to model manifest")
    parser.add_argument("--sims", type=str, default="../01_parametrization/simulation_manifest.parquet", help="Path to simulation manifest")
    parser.add_argument("--output", type=str, default="output_dataset", help="Output directory for HDF5 shards")
    parser.add_argument("--cores", type=int, default=os.cpu_count(), help="Number of cores to use (defaults to all)")
    parser.add_argument("--test", action="store_true", help="Run only 1 model for testing")
    args = parser.parse_args()
    
    os.makedirs(args.output, exist_ok=True)
    
    print(f"Loading manifests from {args.models} and {args.sims}")
    try:
        models_df = pd.read_parquet(args.models)
        sims_df = pd.read_parquet(args.sims)
    except FileNotFoundError:
        print("Manifests not found. Please run the parametrization phase first.")
        return
        
    unique_models = models_df['Model_ID'].unique()
    if args.test:
        unique_models = unique_models[:1]
        print(f"TEST MODE: Running simulation on a single model: {unique_models[0]}")
    
    # We create chunks so each worker writes to an independent HDF5 shard file
    chunk_size = max(1, len(unique_models) // args.cores)
    model_chunks = [unique_models[i:i + chunk_size] for i in range(0, len(unique_models), chunk_size)]
    
    print(f"Distributed {len(unique_models)} models across {len(model_chunks)} Shards using {args.cores} CPU Cores.")
    
    # Flatten the arguments so each model is an independent task for the pool,
    # but they write to their designated shard file
    tasks = []
    for shard_idx, chunk in enumerate(model_chunks):
        shard_path = os.path.join(args.output, f"dataset_shard_{shard_idx:03d}.h5")
        for m_id in chunk:
            m_df = models_df[models_df['Model_ID'] == m_id]
            geom_subset = sims_df[sims_df['Model_ID'] == m_id]
            tasks.append((m_df, geom_subset, shard_path))
            
    successes = 0
    if args.test:
        process_model(tasks[0])
        return
    with Pool(processes=args.cores) as pool:
        for i, result in enumerate(pool.imap_unordered(process_model, tasks)):
            if result:
                successes += 1
            if (i + 1) % 10 == 0 or args.test:
                print(f"Progress: {i+1}/{len(tasks)} models completed...")
                
    print(f"Simulation Complete! Successfully processed {successes}/{len(tasks)} models.")

if __name__ == "__main__":
    main()
