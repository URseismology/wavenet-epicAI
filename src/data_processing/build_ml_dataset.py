#!/usr/bin/env python3
import os
import glob
import re
import numpy as np
import datetime
from concurrent.futures import ProcessPoolExecutor
from h5_wavenet_tools import HDF5Writer
import scipy.interpolate
from scipy.signal import windows, detrend
from scipy.ndimage import gaussian_filter1d
import json

try:
    import pycwt
except ImportError:
    print("WARNING: pycwt is not installed. FTAN computation will fail if attempted.")

def process_signals(data_array, taper_percent=0.05):
    detrended_data = detrend(data_array)
    taper_window = windows.tukey(len(detrended_data), alpha=taper_percent)
    return detrended_data * taper_window

def compute_cross_power_spectrum(r1, r2, delta):
    fft_r1 = np.fft.fft(r1)
    fft_r2 = np.fft.fft(r2)
    return fft_r1 * np.conj(fft_r2)

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

def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper

def get_time_ccf(r1, r2, dt):
    r1_p = process_signals(r1)
    r2_p = process_signals(r2)
    cross_power = compute_cross_power_spectrum(r1_p, r2_p, dt)
    N = len(cross_power)
    ccf_ifft = np.fft.ifft(cross_power, N).real
    ccf_ifft = np.fft.fftshift(ccf_ifft)
    ccf_ifft = detrend(ccf_ifft)
    ccf_ifft = cosine_taper(ccf_ifft)
    ccf_fft = np.fft.fft(np.fft.fftshift(ccf_ifft))
    ccf_filtered = bandpass_filter_freq(ccf_fft, [0.01, 0.5], dt)
    ccf_ifft_final = np.fft.ifft(ccf_filtered)
    ccf_ifft_final = np.fft.fftshift(ccf_ifft_final.real)
    return ccf_ifft_final

def compute_ftan(ccf, dt, distance_km, vmin=0.5, vmax=4.5, fmin=0.05, fmax=1.0):
    npts = len(ccf)
    indx = npts // 2
    data = 0.5 * ccf[indx:] + 0.5 * np.flip(ccf[:indx + 1], axis=0)
    
    pt1 = int(distance_km / vmax / dt)
    pt2 = int(distance_km / vmin / dt)
    if pt1 == 0: pt1 = 10
    if pt2 > (npts // 2): pt2 = npts // 2
    
    indx = np.arange(pt1, pt2)
    tvec = indx * dt
    data = data[indx]
    
    cwt, sj, freq, coi, _, _ = pycwt.cwt(data, dt, 1/24, -1, -1, 'morlet')
    
    freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
    cwt = cwt[freq_ind]
    freq = freq[freq_ind]
    period = 1 / freq
    rcwt = np.abs(cwt) ** 2
    
    per = np.arange(int(1 / fmax), int(1 / fmin), 0.25)
    vel = np.arange(vmin, vmax, 0.01)
    
    velocity_data = distance_km / tvec
    
    # SciPy interp2d is deprecated. Use RegularGridInterpolator instead.
    # RegularGridInterpolator requires strictly ascending coordinate arrays.
    vel_sort_idx = np.argsort(velocity_data)
    vel_asc = velocity_data[vel_sort_idx]
    
    per_sort_idx = np.argsort(period)
    per_asc = period[per_sort_idx]
    
    rcwt_sorted = rcwt[per_sort_idx, :][:, vel_sort_idx]
    
    interp = scipy.interpolate.RegularGridInterpolator((per_asc, vel_asc), rcwt_sorted, method='linear', bounds_error=False, fill_value=0.0)
    
    P, V = np.meshgrid(per, vel, indexing='ij')
    rcwt_new = interp((P, V))
    
    for ii in range(len(per)):
        row_max = np.max(rcwt_new[ii])
        if row_max > 0:
            rcwt_new[ii] /= row_max
            
    for j in range(len(vel)):
        rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)
        
    return per, vel, rcwt_new

def parse_model_d(model_file):
    """Parse model.d velocity structure. [depth, Vp, Vs, rho]"""
    layers = []
    with open(model_file, 'r') as f:
        lines = f.readlines()
        for line in lines[12:]: # skip headers
            parts = line.split()
            if len(parts) >= 4:
                layers.append([float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])])
    return np.array(layers, dtype=np.float32)

def parse_sdisp(sdisp_file, max_periods=76, use_group_velocity=False):
    """Parse SDISP[R|L].ASC or SREGN.ASC files."""
    periods, vels = [], []
    if os.path.exists(sdisp_file):
        data = np.loadtxt(sdisp_file, skiprows=1)
        if len(data) == 0:
            return np.zeros(max_periods, dtype=np.float32)
        mode0 = data[:, 0] == 0
        periods = data[mode0, 2]
        
        # Determine the column index for Velocity
        if use_group_velocity and data.shape[1] > 5:
            vels = data[mode0, 5] # U is at index 5 in SREGN
        else:
            vels = data[mode0, 4] # C is at index 4
    
    # Pad or truncate to max_periods
    padded_vels = np.zeros(max_periods, dtype=np.float32)
    n = min(len(vels), max_periods)
    padded_vels[:n] = vels[:n]
    return padded_vels

def create_mask_and_guidance(rcwt_new, theo_vels, velocities, line_width=5):
    """Creates target mask and the Gaussian curve guidance row."""
    n_periods, n_vel = rcwt_new.shape
    mask = np.zeros((n_periods, n_vel), dtype=np.float32)
    curve_row = np.zeros((1, n_vel), dtype=np.float32)
    
    vel_range = velocities[-1] - velocities[0]
    sigma = 5.0
    x = np.arange(n_vel, dtype=np.float32)
    
    for i in range(n_periods):
        v = theo_vels[i]
        if v <= 0.0: continue
        
        # Mask
        vel_idx = int(np.argmin(np.abs(velocities - v)))
        vel_start = max(0, vel_idx - line_width // 2)
        vel_end = min(n_vel, vel_idx + line_width // 2 + 1)
        mask[i, vel_start:vel_end] = 1.0
        
        # Guidance bump
        bin_idx = (v - velocities[0]) / vel_range * (n_vel - 1)
        curve_row[0] += np.exp(-0.5 * ((x - bin_idx) / sigma) ** 2)
        
    row_max = curve_row.max()
    if row_max > 1e-10:
        curve_row /= row_max
        
    return mask, curve_row

def process_simulation(sim_dir):
    try:
        # Find metadata
        meta_file = glob.glob(os.path.join(sim_dir, 'WAVE_SIM_*_meta.txt'))
        if not meta_file: return None
        
        with open(meta_file[0], 'r') as f:
            meta_text = f.read()
            
        sim_id_match = re.search(r'Simulation_ID:\s*(\w+)', meta_text)
        sim_id = sim_id_match.group(1) if sim_id_match else "unknown"
        
        dist_match = re.search(r'Distance:\s*([\d.]+)', meta_text)
        dist = float(dist_match.group(1)) if dist_match else 200.0
        
        delta_match = re.search(r'Delta:\s*([\d.]+)', meta_text)
        delta = float(delta_match.group(1)) if delta_match else 0.5
        
        stack_match = re.search(r'STACK_LENGTH:\s*(\d+)', meta_text)
        stack_length = int(stack_match.group(1)) if stack_match else 7743
        
        # We need domain from path (CIA, Craton, Rift, etc.)
        domain = "unknown"
        for dom in ["CIA", "Craton", "Rift", "Shield", "CUS", "KOREA", "Interior", "Continental"]:
            if dom in sim_dir:
                domain = dom
                break
                
        # Find wave files
        waves = {}
        for ch in ['R1_E', 'R1_N', 'R1_Z', 'R2_E', 'R2_N', 'R2_Z']:
            wfile = glob.glob(os.path.join(sim_dir, f'WAVE_SIM_*_{ch}.txt'))
            if wfile:
                waves[ch] = np.loadtxt(wfile[0])
            else:
                return None # missing channel
                
        # Stack to [time_steps, 6]
        raw_tensor = np.stack([waves['R1_E'], waves['R1_N'], waves['R1_Z'],
                               waves['R2_E'], waves['R2_N'], waves['R2_Z']], axis=1).astype(np.float32)
                               
        # Compute CCF and FTAN on Z component
        ccf = get_time_ccf(waves['R1_Z'], waves['R2_Z'], delta)
        per, vel, rcwt = compute_ftan(ccf, delta, dist)
        
        # Parse theoretical files
        model_d_path = os.path.join(sim_dir, 'model.d')
        sregn_path = os.path.join(sim_dir, 'SREGN.ASC')
        sdispl_path = os.path.join(sim_dir, 'SDISPL.ASC')
        sdispr_path = os.path.join(sim_dir, 'SDISPR.ASC')
        
        vel_model = parse_model_d(model_d_path) if os.path.exists(model_d_path) else np.zeros((1, 4))
        sdispl = parse_sdisp(sdispl_path, use_group_velocity=False)
        sdispr = parse_sdisp(sdispr_path, use_group_velocity=False)
        
        # Generate SREGN.ASC dynamically if it is missing but binary exists
        if not os.path.exists(sregn_path) and os.path.exists(os.path.join(sim_dir, 'sregn96.egn')):
            import subprocess
            subprocess.run('module load CPS && sdpegn96 -R -U -ASC', shell=True, cwd=sim_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # The ML Target Mask MUST use Group Velocity (U) from SREGN.ASC
        if os.path.exists(sregn_path):
            theo_curve = parse_sdisp(sregn_path, use_group_velocity=True)
        else:
            theo_curve = sdispr # Absolute fallback

        
        mask, guidance = create_mask_and_guidance(rcwt, theo_curve, vel)
        
        # Assemble (80, 400) inputs
        ftan_input = np.vstack([rcwt, guidance]).astype(np.float32) # (77, 400)
        ftan_input = np.pad(ftan_input, ((0, 3), (0, 0)), mode='constant') # (80, 400)
        
        mask_input = np.pad(mask, ((0, 4), (0, 0)), mode='constant').astype(np.uint8) # (80, 400)
        
        return {
            'raw_waveforms': raw_tensor,
            'ftan_inputs': ftan_input,
            'target_masks': mask_input,
            'theoretical_curves': theo_curve,
            'sdispl_curves': sdispl,
            'sdispr_curves': sdispr,
            'velocity_models': vel_model,
            'simulation_id': sim_id,
            'domain': domain,
            'distance_km': dist,
            'radius_range': "unknown",
            'azimuth_range': "unknown",
            'stack_length': stack_length,
            'delta': delta,
            'processed_log': datetime.datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"Error processing {sim_dir}: {e}")
        return None

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_dir', type=str, required=True, help='Directory containing simulation folders')
    parser.add_argument('--output_h5', type=str, default='wavenet_training_data.h5', help='Output HDF5 path')
    parser.add_argument('--limit', type=int, default=None, help='Max number of simulations to process')
    args = parser.parse_args()
    
    writer = HDF5Writer(args.output_h5, mode='a')
    processed = writer.get_processed_simulations()
    
    # Find all simulation dirs containing WAVE_SIM_meta
    all_sim_dirs = []
    for root, dirs, files in os.walk(args.input_dir):
        if any(f.startswith('WAVE_SIM_') and f.endswith('_meta.txt') for f in files):
            all_sim_dirs.append(root)
            
    print(f"Found {len(all_sim_dirs)} total simulation directories.")
    
    # Filter unprocessed
    to_process = []
    for d in all_sim_dirs:
        # Extract ID
        meta_file = glob.glob(os.path.join(d, 'WAVE_SIM_*_meta.txt'))[0]
        with open(meta_file, 'r') as f:
            sim_id_match = re.search(r'Simulation_ID:\s*(\w+)', f.read())
            if sim_id_match and sim_id_match.group(1) not in processed:
                to_process.append(d)
                
    print(f"{len(to_process)} simulations are new and pending processing.")
    
    if args.limit:
        to_process = to_process[:args.limit]
        print(f"Limiting to {args.limit} simulations for this run.")
        
    if not to_process:
        print("Nothing to process.")
        return

    # Process in parallel
    batch_data = {k: [] for k in ['raw_waveforms', 'ftan_inputs', 'target_masks', 'theoretical_curves',
                                  'sdispl_curves', 'sdispr_curves', 'velocity_models', 'simulation_id',
                                  'domain', 'distance_km', 'radius_range', 'azimuth_range', 'stack_length',
                                  'delta', 'processed_log']}
    
    success_count = 0
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        for res in executor.map(process_simulation, to_process):
            if res:
                for k in batch_data:
                    batch_data[k].append(res[k])
                success_count += 1
                
                # Append in batches of 50 to save memory
                if len(batch_data['simulation_id']) >= 50:
                    writer.append_batch(batch_data)
                    print(f"Appended 50 simulations to {args.output_h5}")
                    for k in batch_data: batch_data[k].clear()
                    
    # Append remaining
    if len(batch_data['simulation_id']) > 0:
        writer.append_batch(batch_data)
        print(f"Appended final {len(batch_data['simulation_id'])} simulations.")
        
    print(f"Finished. Successfully processed {success_count} simulations.")

if __name__ == "__main__":
    main()
