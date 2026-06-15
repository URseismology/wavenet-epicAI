#!/usr/bin/env python3
"""
HDF5 Dataset Verification Suite
Samples 100 entries from the unified wavenet_training_data.h5 dataset
and reconstructs the legacy 6-panel verification plots:
1. Source Distribution
2. FTAN Input
3. Target Mask
4. P-wave Velocity Profile
5. Time-Domain CCF
6. Frequency Coherence
"""

import os
import sys
import subprocess
import h5py
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.signal import detrend, windows

import warnings
warnings.filterwarnings("ignore")

# ===========================================================================
# CONSTANTS & CONFIGURATION
# ===========================================================================
SYNOLOGY_LINK = "https://repovibranium.quickconnect.to/sharing/xL5f3x1FE"
HDF5_FILE = "wavenet_training_data.h5"
NUM_SAMPLES = 100
DELTA = 0.5
FILTER_FREQ_RANGE = [0.01, 0.5]
TAPER_WIDTH = 0.2
LAG_TIME_MAX = 250.0
SMOOTH_WINDOW = 10

# ===========================================================================
# DATA FETCHING
# ===========================================================================
def check_and_download_data():
    """
    Downloads the HDF5 dataset via curl if it does not exist locally.
    """
    if not os.path.exists(HDF5_FILE):
        print(f"[*] HDF5 dataset not found locally. Downloading from NAS...")
        print(f"[*] Command: curl -L -o {HDF5_FILE} <Synology_Link>")
        print(f"[*] NOTE: If QuickConnect redirects to an HTML portal instead of the direct file stream, "
              f"the downloaded file will be invalid. You must provide a direct download URL.")
        subprocess.run(['curl', '-L', '-o', HDF5_FILE, SYNOLOGY_LINK])
        
        # Verify it's a valid HDF5 file
        try:
            with h5py.File(HDF5_FILE, 'r') as f:
                pass
        except OSError:
            print(f"\n[ERROR] The downloaded file '{HDF5_FILE}' is not a valid HDF5 file.")
            print(f"[ERROR] The sharing link likely returned an HTML web portal instead of the raw file.")
            print(f"[ERROR] Please manually download the dataset or provide a direct File Station link.")
            sys.exit(1)

# ===========================================================================
# TIME-DOMAIN CCF AND COHERENCE (Adapted from compute_ccf.py)
# ===========================================================================
def moving_average(data, window_size):
    kernel = np.ones(window_size) / window_size
    return np.convolve(data, kernel, mode='same')

def process_signals(data_array, taper_percent=0.05):
    detrended = detrend(data_array)
    taper_window = windows.tukey(len(detrended), alpha=taper_percent)
    return detrended * taper_window

def bandpass_filter_freq(fft_data, freq_range, dt):
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

def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper

def compute_ccf_and_coherence(r1, r2, dt):
    # Process
    r1_proc = process_signals(r1)
    r2_proc = process_signals(r2)
    # FFT
    fft_r1 = np.fft.fft(r1_proc)
    fft_r2 = np.fft.fft(r2_proc)
    cross_power = fft_r1 * np.conj(fft_r2)
    
    # Coherence
    den = np.abs(fft_r1) * np.abs(fft_r2)
    coherence = np.zeros_like(cross_power, dtype=np.complex128)
    mask = den > 0
    coherence[mask] = cross_power[mask] / den[mask]
    
    # Freq axis
    n = len(r1)
    freqs = np.fft.fftfreq(n, d=dt)
    pos_mask = freqs > 0
    freqs_pos = freqs[pos_mask]
    coherence_pos = coherence[pos_mask]
    
    coherence_smoothed = moving_average(np.real(coherence_pos), SMOOTH_WINDOW)
    
    # Time CCF
    ccf_ifft = np.fft.ifft(cross_power).real
    ccf_ifft = np.fft.fftshift(ccf_ifft)
    ccf_ifft = detrend(ccf_ifft)
    ccf_ifft = cosine_taper(ccf_ifft)
    
    ccf_fft = np.fft.fft(np.fft.fftshift(ccf_ifft))
    ccf_filt = bandpass_filter_freq(ccf_fft, FILTER_FREQ_RANGE, dt)
    time_ccf = np.fft.fftshift(np.fft.ifft(ccf_filt).real)
    lags = (np.arange(n) - np.floor(n/2)) * dt
    
    return lags, time_ccf, freqs_pos, coherence_smoothed

# ===========================================================================
# PLOTTING FUNCTIONS
# ===========================================================================
def plot_source_distribution(ax, distance_km, radius_range, azimuth_range):
    """
    Procedurally reconstructs a representative uniform distribution of sources
    within the annulus defined by the simulation parameters.
    """
    try:
        r_min, r_max = map(float, radius_range.split('_'))
    except:
        r_min, r_max = 150.0, 1000.0
        
    try:
        az_min, az_max = map(float, azimuth_range.split('_'))
    except:
        az_min, az_max = 0.0, 360.0
        
    num_points = 10000
    r = np.sqrt(np.random.uniform(r_min**2, r_max**2, num_points))
    theta = np.random.uniform(np.radians(az_min), np.radians(az_max), num_points)
    
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
    ax.set_title(f'Source Distribution (r: {r_min}-{r_max}km)', fontsize=10)
    ax.legend(fontsize=8, facecolor='#222222', labelcolor='white', framealpha=0.5)

# ===========================================================================
# MAIN LOGIC
# ===========================================================================
def main():
    print(f"[*] Starting HDF5 Verification Suite...")
    check_and_download_data()
    
    out_dir = "verification_plots"
    os.makedirs(out_dir, exist_ok=True)
    
    with h5py.File(HDF5_FILE, 'r') as f:
        N_total = f['ftan_inputs'].shape[0]
        print(f"[*] Total simulations in database: {N_total}")
        
        # Sample random indices
        num_to_sample = min(NUM_SAMPLES, N_total)
        indices = np.random.choice(N_total, num_to_sample, replace=False)
        print(f"[*] Processing {num_to_sample} random entries...")
        
        for i, idx in enumerate(indices):
            # 1. Load Metadata
            sim_id = f['metadata/simulation_id'][idx].decode('utf-8')
            dist = f['metadata/distance_km'][idx]
            rad_range = f['metadata/radius_range'][idx].decode('utf-8')
            az_range = f['metadata/azimuth_range'][idx].decode('utf-8')
            
            # 2. Load Data Arrays
            raw = f['raw_waveforms'][idx]
            r1_z = raw[:, 2] # R1_Z (Channel 2)
            r2_z = raw[:, 5] # R2_Z (Channel 5)
            
            ftan = f['ftan_inputs'][idx]
            mask = f['target_masks'][idx]
            
            # Velocity model shape is typically [layers, 3 or 4]
            # Assuming depth, vp, vs
            vp_prof = f['velocity_models'][idx]
            depths = vp_prof[:, 0]
            vps = vp_prof[:, 1]
            
            # 3. Compute CCF & Coherence
            lags, time_ccf, freqs, coherence = compute_ccf_and_coherence(r1_z, r2_z, DELTA)
            
            # 4. Generate 6-Panel Visualization
            fig = plt.figure(figsize=(20, 12), facecolor='#0d0d0d')
            gs  = gridspec.GridSpec(2, 3, figure=fig,
                                    left=0.06, right=0.97,
                                    top=0.90, bottom=0.07,
                                    wspace=0.35, hspace=0.45)
            
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
                for sp in ax.spines.values(): sp.set_edgecolor('#444444')
                ax.xaxis.label.set_color('white')
                ax.yaxis.label.set_color('white')
                ax.title.set_color('white')
                ax.grid(True, alpha=0.15, color='white')
                
            plot_source_distribution(ax_src, dist, rad_range, az_range)
            
            # FTAN
            ax_ftan.imshow(ftan, cmap='inferno', aspect='auto', origin='lower')
            ax_ftan.set_title('FTAN Input', fontsize=10)
            
            # Mask
            ax_mask.imshow(mask, cmap='viridis', aspect='auto', origin='lower')
            ax_mask.set_title('Target Mask', fontsize=10)
            
            # Vp Profile
            ax_vp.plot(vps, depths, 'w-', lw=2)
            ax_vp.invert_yaxis()
            ax_vp.set_xlabel('Vp (km/s)', fontsize=9)
            ax_vp.set_ylabel('Depth (km)', fontsize=9)
            ax_vp.set_title('P-wave Velocity', fontsize=10)
            
            # CCF Time
            lag_mask = np.abs(lags) <= LAG_TIME_MAX
            ax_ccf_time.plot(lags[lag_mask], time_ccf[lag_mask], 'w-', lw=1)
            ax_ccf_time.set_xlabel('Lag Time (s)', fontsize=9)
            ax_ccf_time.set_title('Time-Domain CCF', fontsize=10)
            ax_ccf_time.axvline(0, color='r', ls='--', alpha=0.5)
            
            # Coherence
            ax_coh.plot(freqs, coherence, color='#5599ff', lw=1)
            ax_coh.set_xlim(0, 0.5)
            ax_coh.set_xlabel('Frequency (Hz)', fontsize=9)
            ax_coh.set_title('Frequency Coherence', fontsize=10)
            
            fig.suptitle(f'Verification: {sim_id}', color='white', fontsize=14, fontweight='bold')
            out_file = os.path.join(out_dir, f'verify_{sim_id}.png')
            plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor(), bbox_inches='tight')
            plt.close()
            
            if (i+1) % 10 == 0:
                print(f"  Processed {i+1}/{num_to_sample}...")
                
    print(f"\n[*] Verification complete! 100 plots saved to ./{out_dir}")

if __name__ == '__main__':
    main()
