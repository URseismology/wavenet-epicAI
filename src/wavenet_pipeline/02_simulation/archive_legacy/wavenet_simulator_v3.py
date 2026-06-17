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

import getpass
if getpass.getuser() == 'tolugboj':
    CPS_BIN = '/home/tolugboj/PROGRAMS.330/bin'
else:
    CPS_BIN = '/Users/olugboji/SynologyDrive/1.UofR_Seismology/1_Admin/Admin8_LabAI/wavenet-epicAI/scratch/cps/PROGRAMS.330/bin'

# Constants
ANNULUS_RMIN = 1000.0
ANNULUS_RMAX = 1100.0
MAX_STATION_SEP = 500.0
GRID_SPACING = 5.0
MIN_DIST = ANNULUS_RMIN - (MAX_STATION_SEP / 2.0)
MAX_DIST = ANNULUS_RMAX + (MAX_STATION_SEP / 2.0)

N_SOURCES = 1000000
TMAX = 3600.0
DELTA = 1.0
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
    model_df, geom_subset, shard_path, fast_mode = args
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
            
            stack_length = TMAX_SAMPLES + lut['npts']
            
            import scipy.fft
            nfft = scipy.fft.next_fast_len(stack_length)
            freqs_rfft = np.fft.rfftfreq(nfft, d=DELTA)
            
            # Pre-allocate output arrays
            lags_trimmed = (np.arange(-MAX_LAG_SAMPLES, MAX_LAG_SAMPLES + 1)) * DELTA
            
            # === PHYSICS / LOGIC NOTE ===
            # WE NOW USE WEDGE LOGIC:
            # Stacking all 1,000,000 sources into a single time series before cross-correlating 
            # creates massive cross-term interference (O(N^2) noise), preventing the SNR from improving.
            # To fix this, we split the 360-degree source field into 360 independent 1-degree wedges.
            # For each wedge, we linearly stack the raw waveforms and compute its localized Cross-Power Spectrum.
            # Then, we accumulate the Cross-Power Spectra across all wedges. By stacking Cross-Power directly, 
            # the random time-shifts between wedges mathematically cancel out, destroying the cross-terms 
            # and beautifully isolating the Green's function!
            # ============================
            
            n_wedges = 360
            sources_per_wedge = N_SOURCES // n_wedges
            
            # Helper engine to compute final CCF from average Cross-Power
            def compute_ccf_from_crosspower(cross_power, p11, p22, stack_length):
                # Calculate frequency-domain Coherence
                den = np.sqrt(p11 * p22)
                coherence = np.zeros_like(cross_power)
                mask = den > 0
                coherence[mask] = np.abs(cross_power[mask]) / den[mask]
                
                # Transform Cross-Power to Time Domain CCF
                ccf_ifft = np.fft.irfft(cross_power, n=nfft)[:stack_length]
                
                # Apply taper & filter just like V1/V2
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
                
                freqs = freqs_rfft
                return ccf_final, coherence.real, freqs

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
                
                if fast_mode:
                    scenario_names = ['ccf_isotropic']
                    # We only track ZZ
                    acc = {sc: {'cp_zz': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'p11_z': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'p22_z': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'count': 0} for sc in scenario_names}
                else:
                    scenario_names = ['ccf_isotropic', 'ccf_inline', 'ccf_broadside', 'ccf_onesided', 'ccf_random_50deg']
                    # We track 4 tensor components: ZZ, RR, TT, RZ, ZR
                    # But actually we just need Z, N, E cross powers
                    acc = {sc: {'cp_zz': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'p11_z': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'p22_z': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'cp_rr': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'p11_r': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'p22_r': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'cp_tt': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'p11_t': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'p22_t': np.zeros(len(freqs_rfft), dtype=np.float64),
                                'cp_rz': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'cp_zr': np.zeros(len(freqs_rfft), dtype=np.complex128),
                                'count': 0} for sc in scenario_names}
                                
                # Deterministic random state per geometry
                np.random.seed(42 + int(model_id.split('_')[-1]) + int(sep_km))
                
                # We process the source field wedge by wedge (360 wedges total)
                for w in range(n_wedges):
                    w_min = w * (360.0 / n_wedges)
                    w_max = (w + 1) * (360.0 / n_wedges)
                    w_mid = (w_min + w_max) / 2.0
                    
                    # Generate sources constrained strictly to THIS wedge
                    global_r = np.random.uniform(ANNULUS_RMIN, ANNULUS_RMAX, sources_per_wedge)
                    global_theta = np.random.uniform(w_min, w_max, sources_per_wedge)
                    global_x = global_r * np.cos(np.deg2rad(global_theta))
                    global_y = global_r * np.sin(np.deg2rad(global_theta))
                    
                    global_fn = np.random.uniform(-1, 1, sources_per_wedge)
                    global_fe = np.random.uniform(-1, 1, sources_per_wedge)
                    global_fd = np.random.uniform(-1, 1, sources_per_wedge)
                    
                    # Random time shifts inside TMAX
                    tshifts = np.random.uniform(0, TMAX, sources_per_wedge)
                    # === LOGIC NOTE ===
                    # Stack is done natively in frequency domain for speed gains 
                    # phase_shifts applies the tshift as e^(-i w t) without needing an IFFT
                    phase_shifts = np.exp(-2j * np.pi * freqs_rfft[None, :] * tshifts[:, None])
                    # ==================
                    
                    # Determine which scenarios this wedge belongs to
                    scen_masks = {'ccf_isotropic': True}
                    if not fast_mode:
                        scen_masks.update({
                            'ccf_inline': (np.abs(w_mid) <= 15) or (np.abs(w_mid - 180) <= 15) or (np.abs(w_mid - 360) <= 15),
                            'ccf_broadside': (np.abs(w_mid - 90) <= 15) or (np.abs(w_mid - 270) <= 15),
                            'ccf_onesided': (w_mid >= 0) and (w_mid <= 180),
                            'ccf_random_50deg': False
                        })
                        
                        # Handle 50-deg wedge angular wrap-around
                        r_min_ang = rand_ang - 25
                        r_max_ang = rand_ang + 25
                        if r_min_ang < 0:
                            scen_masks['ccf_random_50deg'] = (w_mid >= r_min_ang + 360) or (w_mid <= r_max_ang)
                        elif r_max_ang > 360:
                            scen_masks['ccf_random_50deg'] = (w_mid >= r_min_ang) or (w_mid <= r_max_ang - 360)
                        else:
                            scen_masks['ccf_random_50deg'] = (w_mid >= r_min_ang) and (w_mid <= r_max_ang)
                    
                    # Interpolate LUT for distances
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
                    
                    zvf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['ZVF'])
                    zhf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['ZHF'])
                    rvf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['RVF'])
                    rhf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['RHF'])
                    thf1 = fast_interp_2d(dist1, MIN_DIST, GRID_SPACING, lut['THF'])
                    
                    zvf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['ZVF'])
                    zhf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['ZHF'])
                    rvf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['RVF'])
                    rhf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['RHF'])
                    thf2 = fast_interp_2d(dist2, MIN_DIST, GRID_SPACING, lut['THF'])
                    
                    def compute_waves(zvf, zhf, rvf, rhf, thf, az, baz):
                        az_rad = np.deg2rad(az)
                        fR = global_fn * np.cos(az_rad) + global_fe * np.sin(az_rad)
                        fZ = global_fd
                        uZ = fZ[:, None] * zvf + fR[:, None] * zhf
                        
                        if fast_mode:
                            return uZ, None, None
                            
                        fT = -global_fn * np.sin(az_rad) + global_fe * np.cos(az_rad)
                        uR = fR[:, None] * rhf + fZ[:, None] * rvf
                        uT = fT[:, None] * thf
                        
                        baz_rad = np.deg2rad(baz)
                        uN = -np.cos(baz_rad)[:, None] * uR + np.sin(baz_rad)[:, None] * uT
                        uE = -np.sin(baz_rad)[:, None] * uR - np.cos(baz_rad)[:, None] * uT
                        return uZ, uN, uE
                    
                    # Compute waves for the wedge
                    Z1, N1, E1 = compute_waves(zvf1, zhf1, rvf1, rhf1, thf1, az1, baz1)
                    Z2, N2, E2 = compute_waves(zvf2, zhf2, rvf2, rhf2, thf2, az2, baz2)
                    
                    # 1. Transform all individual source waves to frequency domain
                    Z1_fd = np.fft.rfft(Z1, n=nfft, axis=1)
                    Z2_fd = np.fft.rfft(Z2, n=nfft, axis=1)
                    
                    # 2. Linearly stack the frequency domain waves into a single localized Wedge Wavefield
                    # (Applies the random time-shifts simultaneously)
                    Z1_wedge = np.sum(Z1_fd * phase_shifts, axis=0)
                    Z2_wedge = np.sum(Z2_fd * phase_shifts, axis=0)
                    
                    # 3. Compute the Cross-Power spectrum for THIS WEDGE independently!
                    cp_zz = Z1_wedge * np.conj(Z2_wedge)
                    p11_z = np.abs(Z1_wedge)**2
                    p22_z = np.abs(Z2_wedge)**2
                    
                    if not fast_mode:
                        N1_fd = np.fft.rfft(N1, n=nfft, axis=1)
                        E1_fd = np.fft.rfft(E1, n=nfft, axis=1)
                        N2_fd = np.fft.rfft(N2, n=nfft, axis=1)
                        E2_fd = np.fft.rfft(E2, n=nfft, axis=1)
                        
                        R1_wedge = np.sum(E1_fd * phase_shifts, axis=0) # R is mapped to East for inline
                        T1_wedge = np.sum(N1_fd * phase_shifts, axis=0) # T is mapped to North
                        R2_wedge = np.sum(E2_fd * phase_shifts, axis=0)
                        T2_wedge = np.sum(N2_fd * phase_shifts, axis=0)
                        
                        cp_rr = R1_wedge * np.conj(R2_wedge)
                        p11_r = np.abs(R1_wedge)**2
                        p22_r = np.abs(R2_wedge)**2
                        
                        cp_tt = T1_wedge * np.conj(T2_wedge)
                        p11_t = np.abs(T1_wedge)**2
                        p22_t = np.abs(T2_wedge)**2
                        
                        cp_rz = R1_wedge * np.conj(Z2_wedge)
                        cp_zr = Z1_wedge * np.conj(R2_wedge)
                    
                    # 4. Accumulate the independent Wedge Cross-Power into matching Scenarios
                    for sc_name, is_valid in scen_masks.items():
                        if is_valid:
                            acc[sc_name]['cp_zz'] += cp_zz
                            acc[sc_name]['p11_z'] += p11_z
                            acc[sc_name]['p22_z'] += p22_z
                            if not fast_mode:
                                acc[sc_name]['cp_rr'] += cp_rr
                                acc[sc_name]['p11_r'] += p11_r
                                acc[sc_name]['p22_r'] += p22_r
                                acc[sc_name]['cp_tt'] += cp_tt
                                acc[sc_name]['p11_t'] += p11_t
                                acc[sc_name]['p22_t'] += p22_t
                                acc[sc_name]['cp_rz'] += cp_rz
                                acc[sc_name]['cp_zr'] += cp_zr
                            acc[sc_name]['count'] += 1

                # Finally, compute the actual CCF from the averaged Cross-Power spectra
                for sc_name in acc.keys():
                    n_wedges_used = acc[sc_name]['count']
                    if n_wedges_used == 0:
                        continue
                        
                    sc_grp = sim_grp.require_group(sc_name)
                    sc_grp.attrs['n_wedges_used'] = n_wedges_used
                    sc_grp.attrs['n_sources_used'] = n_wedges_used * sources_per_wedge
                    
                    avg_cp_zz = acc[sc_name]['cp_zz'] / n_wedges_used
                    avg_p11_z = acc[sc_name]['p11_z'] / n_wedges_used
                    avg_p22_z = acc[sc_name]['p22_z'] / n_wedges_used
                    
                    ccf_zz, coh_zz, freqs = compute_ccf_from_crosspower(avg_cp_zz, avg_p11_z, avg_p22_z, stack_length)
                    
                    mid = len(ccf_zz) // 2
                    s_idx = mid - MAX_LAG_SAMPLES
                    e_idx = mid + MAX_LAG_SAMPLES + 1
                    
                    sc_grp.create_dataset('lags_s', data=lags_trimmed)
                    sc_grp.create_dataset('freqs_hz', data=freqs)
                    sc_grp.create_dataset('CCF_ZZ', data=ccf_zz[s_idx:e_idx])
                    sc_grp.create_dataset('COH_REAL_ZZ', data=coh_zz)
                    
                    if not fast_mode:
                        avg_cp_rr = acc[sc_name]['cp_rr'] / n_wedges_used
                        avg_p11_r = acc[sc_name]['p11_r'] / n_wedges_used
                        avg_p22_r = acc[sc_name]['p22_r'] / n_wedges_used
                        
                        avg_cp_tt = acc[sc_name]['cp_tt'] / n_wedges_used
                        avg_p11_t = acc[sc_name]['p11_t'] / n_wedges_used
                        avg_p22_t = acc[sc_name]['p22_t'] / n_wedges_used
                        
                        avg_cp_rz = acc[sc_name]['cp_rz'] / n_wedges_used
                        avg_cp_zr = acc[sc_name]['cp_zr'] / n_wedges_used
                        
                        ccf_rr, coh_rr, _ = compute_ccf_from_crosspower(avg_cp_rr, avg_p11_r, avg_p22_r, stack_length)
                        ccf_tt, coh_tt, _ = compute_ccf_from_crosspower(avg_cp_tt, avg_p11_t, avg_p22_t, stack_length)
                        ccf_rz, _, _ = compute_ccf_from_crosspower(avg_cp_rz, avg_p11_r, avg_p22_z, stack_length)
                        ccf_zr, _, _ = compute_ccf_from_crosspower(avg_cp_zr, avg_p11_z, avg_p22_r, stack_length)
                        
                        sc_grp.create_dataset('CCF_RR', data=ccf_rr[s_idx:e_idx])
                        sc_grp.create_dataset('CCF_TT', data=ccf_tt[s_idx:e_idx])
                        sc_grp.create_dataset('CCF_RZ', data=ccf_rz[s_idx:e_idx])
                        sc_grp.create_dataset('CCF_ZR', data=ccf_zr[s_idx:e_idx])
                        sc_grp.create_dataset('COH_REAL_RR', data=coh_rr)
                        sc_grp.create_dataset('COH_REAL_TT', data=coh_tt)
                    
                    print(f"    Saved scenario: {sc_name} (Wedges: {n_wedges_used})")

                
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
    parser.add_argument("--fast", action="store_true", help="Fast mode: Only compute Isotropic ZZ cross-correlation")
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
            tasks.append((m_df, geom_subset, shard_path, args.fast))
            
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
