#!/usr/bin/env python3
import os
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"
import h5py
import numpy as np
import matplotlib.pyplot as plt
import pycwt
import scipy.interpolate
from scipy.signal import detrend
import sys

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

def cosine_taper(data, taper_fraction=0.05):
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))
    return data * taper

def plot_verification():
    h5_file = "output_dataset_v4/dataset_shard_000.h5"
    if not os.path.exists(h5_file):
        print(f"File {h5_file} not found!")
        return

    with h5py.File(h5_file, "r") as f:
        model_id = list(f["simulations"].keys())[0]
        model_grp = f[f"simulations/{model_id}"]
        
        # Geometry setup
        annulus_rmin, annulus_rmax = 10000.0, 10500.0
        n_sources = 10000
        np.random.seed(42 + int(model_id.split('_')[-1]))
        global_r = np.random.uniform(annulus_rmin, annulus_rmax, n_sources)
        global_theta = np.random.uniform(0, 360, n_sources)
        global_x = global_r * np.cos(np.deg2rad(global_theta))
        global_y = global_r * np.sin(np.deg2rad(global_theta))
        
        for k in model_grp["geometries"].keys():
            if "ccf_isotropic" in model_grp[f"geometries/{k}"]:
                geom_key = k
                break
        geom_grp = model_grp[f"geometries/{geom_key}"]
        sep_km = float(geom_key.split("_")[1].replace("km", ""))
        
        # In V3.1 we just simulated the full isotropic scenario
        # We can just plot all sources as the geometry
        mask = np.ones_like(global_theta, dtype=bool) 
        
        fig = plt.figure(figsize=(20, 12))
        
        # Panel 1: Geometry
        ax1 = plt.subplot(231)
        ax1.scatter(global_x[::100], global_y[::100], s=1, c='lightgray', alpha=0.5, label='All Sources (subsampled)')
        ax1.scatter(global_x[mask][::10], global_y[mask][::10], s=2, c='red', alpha=0.5, label='Isotropic (subsampled)')
        ax1.plot([-sep_km/2, sep_km/2], [0, 0], 'b^', markersize=10, label='Stations')
        ax1.set_aspect('equal')
        ax1.set_title(f"Source Geometry (1M sources, sep={sep_km}km)")
        ax1.legend(loc='lower right', fontsize=8)
        
        # Panel 2: Velocity Model
        ax2 = plt.subplot(232)
        prof = model_grp["velocity_profile"]
        depth = np.cumsum(prof["H_km"][:])
        vs = prof["VS_kms"][:]
        vp = prof["VP_kms"][:]
        depth_plt, vs_plt, vp_plt = [0], [vs[0]], [vp[0]]
        for i in range(len(depth)):
            depth_plt.extend([depth[i], depth[i]])
            if i < len(depth)-1:
                vs_plt.extend([vs[i], vs[i+1]])
                vp_plt.extend([vp[i], vp[i+1]])
            else:
                vs_plt.extend([vs[i], vs[i]])
                vp_plt.extend([vp[i], vp[i]])
                
        ax2.plot(vs_plt, depth_plt, 'r-', label='Vs (km/s)')
        ax2.plot(vp_plt, depth_plt, 'b-', label='Vp (km/s)')
        ax2.invert_yaxis()
        ax2.set_xlabel("Velocity (km/s)")
        ax2.set_ylabel("Depth (km)")
        ax2.set_title(f"1D Velocity Model: {model_id}")
        ax2.legend()
        
        # Data
        scen_grp = geom_grp["ccf_isotropic"]
        lags = scen_grp["lags_s"][:] if "lags_s" in scen_grp else None
        freqs = scen_grp["freqs_hz"][:]
        ccf_zz_raw = scen_grp["CCF_ZZ"][:]
        coh_zz = scen_grp["COH_REAL_ZZ"][:]
        
        dt = 1.0
        npts = len(ccf_zz_raw)
        
        # V3.1 outputs irfft circularly shifted. We must fftshift it.
        ccf_zz_shifted = np.fft.fftshift(ccf_zz_raw)
        
        # Apply bandpass filter appropriate for the FTAN period range (5s to 50s => 0.02 to 0.2 Hz)
        ccf_ifft = detrend(ccf_zz_shifted)
        ccf_ifft = cosine_taper(ccf_ifft)
        ccf_fft = np.fft.fft(np.fft.fftshift(ccf_ifft))
        ccf_filt = bandpass_filter_freq(ccf_fft, [0.01, 0.5], dt)  # Wide bandpass to capture dispersion
        ccf_zz = np.fft.fftshift(np.fft.ifft(ccf_filt).real)
        
        if lags is None:
            lags = (np.arange(npts) - np.floor(npts/2)) * dt
            
        # Panel 3: CCF ZZ
        ax3 = plt.subplot(233)
        ax3.plot(lags, ccf_zz, 'k-', linewidth=0.8)
        ax3.set_xlim(-150, 150)
        ax3.set_xlabel("Lag Time (s)")
        ax3.set_ylabel("Amplitude")
        ax3.set_title("Cross-Correlation (ZZ) Isotropic")
        
        # Panel 4: Coherence
        ax4 = plt.subplot(234)
        ax4.plot(freqs, coh_zz, 'g-', linewidth=1)
        ax4.set_xlim(0.01, 0.2)
        ax4.set_xlabel("Frequency (Hz)")
        ax4.set_ylabel("Real Coherence")
        ax4.set_title("Frequency Domain Coherence (ZZ)")
        
        # Panel 5: FTAN
        ax5 = plt.subplot(235)
        indx = npts // 2
        sym_ccf = 0.5 * ccf_zz[indx:] + 0.5 * np.flip(ccf_zz[:indx + 1], axis=0)
        
        # Truncate
        vmin, vmax = 1.0, 6.0
        pt1 = int(sep_km / vmax / dt)
        pt2 = int(sep_km / vmin / dt)
        if pt1 == 0: pt1 = 10
        if pt2 > (npts // 2): pt2 = npts // 2
        tvec = np.arange(pt1, pt2) * dt
        data = sym_ccf[pt1:pt2]
        
        cwt, _, freq_cwt, _, _, _ = pycwt.cwt(data, dt, 1/24, -1, -1, 'morlet')
        freq_ind = np.where((freq_cwt >= 1.0/50.0) & (freq_cwt <= 1.0/5.0))[0]
        cwt = cwt[freq_ind]
        freq_cwt = freq_cwt[freq_ind]
        period = 1 / freq_cwt
        rcwt = np.abs(cwt) ** 2
        
        per_grid = np.arange(5.0, 50.0, 0.5)
        vel_grid = np.arange(2.0, 5.0, 0.01)
        
        import warnings
        warnings.filterwarnings("ignore")
        fc = scipy.interpolate.interp2d(sep_km / tvec, period, rcwt, kind='linear')
        rcwt_new = fc(vel_grid, per_grid)
        
        for ii in range(len(per_grid)):
            row_max = np.max(rcwt_new[ii])
            if row_max > 0:
                rcwt_new[ii] /= row_max
                
        # Get Ground Truth
        theo = model_grp["theoretical"]
        th_per = theo["period"][:]
        th_grp = theo["group_velocity_dispersion"][:]
        
        ax5.imshow(rcwt_new.T, cmap='inferno', extent=[per_grid[0], per_grid[-1], vel_grid[0], vel_grid[-1]], aspect='auto', origin='lower', vmin=0, vmax=1)
        ax5.plot(th_per, th_grp, 'w--', linewidth=2, label='Theory Group Vel')
        ax5.set_xlim(5, 50)
        ax5.set_ylim(2.0, 5.0)
        ax5.set_xlabel("Period (s)")
        ax5.set_ylabel("Group Velocity (km/s)")
        ax5.set_title("Empirical FTAN with Ground Truth")
        ax5.legend()
        
        plt.tight_layout()
        plt.savefig("v3.1_fixed_verify_phase3.png", dpi=150)
        print("Saved verification figure to v3.1_fixed_verify_phase3.png")

if __name__ == "__main__":
    plot_verification()
