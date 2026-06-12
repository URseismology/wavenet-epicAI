#!/usr/bin/env python3
"""FTAN with proper preprocessing for noisy data"""
import numpy as np
from scipy import signal
from obspy import Trace
from obspy.core import UTCDateTime
from FTANos import FTANos
import matplotlib.pyplot as plt

# Load CCF
data = np.loadtxt('stacked_time_ccf.txt')
lags = data[:, 0]
ccf = data[:, 1]

# Extract positive lags
positive_mask = lags >= 0
lags_pos = lags[positive_mask]
ccf_pos = ccf[positive_mask]

print(f"Original CCF length: {len(ccf_pos)}")

# Step 1: Apply aggressive bandpass filter (5-40s period)
nyquist = 1.0 / (2 * 0.5)  # dt = 0.5s
low_freq = 1.0 / 40.0  # 40s period
high_freq = 1.0 / 5.0  # 5s period

sos = signal.butter(4, [low_freq/nyquist, high_freq/nyquist], 
                    btype='bandpass', output='sos')
ccf_filtered = signal.sosfilt(sos, ccf_pos)

# Step 2: Time-domain normalization (clip extremes)
ccf_norm = ccf_filtered / np.max(np.abs(ccf_filtered))

# Step 3: Window to expected arrival times (for 50 km: 8-50s)
time_window_start = int(8 / 0.5)   # 8 seconds
time_window_end = int(100 / 0.5)   # 100 seconds
ccf_windowed = np.zeros_like(ccf_norm)
ccf_windowed[time_window_start:time_window_end] = ccf_norm[time_window_start:time_window_end]

# Apply cosine taper to window edges
taper_len = 20  # samples
taper = np.ones(len(ccf_windowed))
taper[time_window_start:time_window_start+taper_len] = \
    0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
taper[time_window_end-taper_len:time_window_end] = \
    0.5 * (1 + np.cos(np.pi * np.arange(taper_len) / taper_len))
ccf_final = ccf_windowed * taper

# Plot comparison
fig, axes = plt.subplots(4, 1, figsize=(14, 10))
time = lags_pos

axes[0].plot(time, ccf_pos, 'b-', linewidth=0.5)
axes[0].set_ylabel('Original')
axes[0].set_xlim([0, 200])
axes[0].grid(True, alpha=0.3)

axes[1].plot(time, ccf_filtered, 'g-', linewidth=0.5)
axes[1].set_ylabel('Bandpassed')
axes[1].set_xlim([0, 200])
axes[1].grid(True, alpha=0.3)

axes[2].plot(time, ccf_norm, 'r-', linewidth=0.5)
axes[2].set_ylabel('Normalized')
axes[2].set_xlim([0, 200])
axes[2].grid(True, alpha=0.3)

axes[3].plot(time, ccf_final, 'purple', linewidth=0.8)
axes[3].set_ylabel('Final (windowed)')
axes[3].set_xlabel('Time (s)')
axes[3].set_xlim([0, 200])
axes[3].axvspan(8, 100, alpha=0.2, color='yellow', label='Signal window')
axes[3].grid(True, alpha=0.3)
axes[3].legend()

plt.tight_layout()
plt.savefig('ccf_preprocessing_steps.png', dpi=150)
print("Saved preprocessing comparison: ccf_preprocessing_steps.png")

# Create SAC file with preprocessed data
tr = Trace(data=ccf_final)
tr.stats.delta = 0.5
tr.stats.station = 'R2'
tr.stats.channel = 'BHZ'
tr.stats.network = 'WN'
tr.stats.starttime = UTCDateTime(0)
tr.stats.sac = {'dist': 50000, 'b': 0.0}
tr.write('stacked_ccf_50km_processed.sac', format='SAC')

# Generate FTAN
ftan = FTANos(
    filename='stacked_ccf_50km_processed.sac',
    filetype='SAC',
    dist=50000,
    dt=0.5,
    fre1=0.025,  # 40s
    fre2=0.2,    # 5s  
    vel1=2.0,
    vel2=4.5,
    alpha=15,     # Wider filter for noisy data
    ftan_sc=40    # Less aggressive amplitude scaling
)

ftan.plot_FTAN()
print("\n✓ FTAN with preprocessing: stacked_ccf_50km_processed.sac.png")
