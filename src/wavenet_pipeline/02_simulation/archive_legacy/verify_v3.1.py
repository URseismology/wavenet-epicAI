import numpy as np
import matplotlib.pyplot as plt
import h5py
import scipy.interpolate
import sys
import os

try:
    import pycwt
except ImportError:
    print("pycwt not installed")
    sys.exit(1)

def compute_ftan_and_plot(hdf5_path, output_png):
    with h5py.File(hdf5_path, 'r') as f:
        model_group = f['simulations']['M01_0000']
        theory_periods = model_group['theoretical']['period'][:]
        theory_gvel = model_group['theoretical']['group_velocity_dispersion'][:]
        geom_keys = list(model_group['geometries'].keys())
        geom_key = geom_keys[0]
        geom_group = model_group['geometries'][geom_key]
        
        ccf_final = geom_group['ccf_isotropic']['CCF_ZZ'][:]
        
        # Extract distance from string (e.g. 'separation_275.4km' -> 275.4)
        distance_km = float(geom_key.split('_')[1].replace('km', ''))

    # theoretical data already processed directly from HDF5
    
    # Process CCF
    # ccf_final is straight out of irfft, so it is [t=0, t>0 ..., t<0]
    # We must fftshift it to put t=0 in the center
    ccf = np.fft.fftshift(ccf_final)
    
    dt = 0.5
    fmin, fmax = 0.05, 1.0
    vmin, vmax = 0.5, 4.5
    
    npts = len(ccf)
    indx = npts // 2
    # Fold the cross-correlation function into empirical Green's function
    egf = 0.5 * ccf[indx:] + 0.5 * np.flip(ccf[:indx + 1], axis=0)
    
    pt1 = int(distance_km / vmax / dt)
    pt2 = int(distance_km / vmin / dt)
    if pt1 == 0: pt1 = 10
    if pt2 > (npts // 2): pt2 = npts // 2
    
    indx_arr = np.arange(pt1, pt2)
    tvec = indx_arr * dt
    egf = egf[indx_arr]
    
    dj = 1/24
    s0 = -1
    J = -1
    wvn = 'morlet'
    
    cwt, sj, freq, coi, _, _ = pycwt.cwt(egf, dt, dj, s0, J, wvn)
    
    freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
    cwt = cwt[freq_ind]
    freq = freq[freq_ind]
    period = 1 / freq
    rcwt = np.abs(cwt) ** 2
    
    per = np.arange(int(1/fmax), int(1/fmin), 0.25)
    vel = np.arange(vmin, vmax, 0.01)
    
    velocity_data = distance_km / tvec
    fc = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
    rcwt_new = fc(vel, per)
    
    # Normalize per row
    for ii in range(len(per)):
        row_max = np.max(rcwt_new[ii])
        if row_max > 0:
            rcwt_new[ii] /= row_max
            
    # Gaussian smooth
    from scipy.ndimage import gaussian_filter1d
    for j in range(len(vel)):
        rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=0.15)
        
    plt.figure(figsize=(10, 8))
    plt.imshow(
        np.transpose(rcwt_new),
        cmap='inferno',
        extent=[per[0], per[-1], vel[0], vel[-1]],
        aspect='auto',
        origin='lower',
        vmin=0, vmax=1
    )
    plt.plot(theory_periods, theory_gvel, 'w--', lw=2, label='Theoretical Group Vel')
    plt.xlim(per[0], per[-1])
    plt.ylim(vel[0], vel[-1])
    plt.title(f'Frequency-Time Analysis (FTAN)\nCCF Envelope (Distance: {distance_km}km)')
    plt.xlabel('Period (s)')
    plt.ylabel('Group Velocity (km/s)')
    plt.colorbar(label='Normalized Amplitude')
    plt.legend(loc='lower right')
    plt.savefig(output_png)
    print(f"Saved FTAN plot to {output_png}")

if __name__ == '__main__':
    hdf5_path = 'output_dataset/dataset_shard_000.h5'
    output_png = 'v3.1_ftan_verification.png'
    compute_ftan_and_plot(hdf5_path, output_png)
