#!/usr/bin/env python3
"""
Generate Causal vs Acausal vs Symmetric FTAN Comparison
For all cases in Experiment 6

Outputs separate images for each case showing:
[Causal FTAN] [Acausal FTAN] [Symmetric FTAN]
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import scipy.interpolate
import re

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed. Install with: pip install pycwt")
    sys.exit(1)


class FTAN_Comparison:
    def __init__(self, ccf_file, distance_km, model_name, az_range, rad_range):
        self.ccf_file = ccf_file
        self.distance_km = distance_km
        self.model_name = model_name
        self.az_range = az_range
        self.rad_range = rad_range
        self.dt = 0.5

        data = np.loadtxt(ccf_file)
        self.lags = data[:, 0]
        self.ccf = data[:, 1]

    def compute_ftan(self, lag_type='sym', fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5):
        """
        Compute FTAN with specified lag type
        
        lag_type: 'pos' (causal), 'neg' (acausal), 'sym' (symmetric)
        """
        npts = len(self.ccf)
        indx = npts // 2

        # Select lag based on type
        if lag_type == 'neg':  # ACAUSAL
            data = self.ccf[:indx + 1]
            data = np.flip(data, axis=0)  # Flip to make it positive time
        elif lag_type == 'pos':  # CAUSAL
            data = self.ccf[indx:]
        elif lag_type == 'sym':  # SYMMETRIC
            data = 0.5 * self.ccf[indx:] + 0.5 * np.flip(self.ccf[:indx + 1], axis=0)
        else:
            raise ValueError("lag_type must be 'pos', 'neg', or 'sym'")
        
        # Trim to velocity window
        pt1 = int(self.distance_km / vmax / self.dt)
        pt2 = int(self.distance_km / vmin / self.dt)
        
        if pt1 == 0:
            pt1 = 10
        if pt2 > (npts // 2):
            pt2 = npts // 2
            
        indx = np.arange(pt1, pt2)
        tvec = indx * self.dt
        data = data[indx]
        
        # Wavelet transform
        dj = 1/24
        s0 = -1
        J = -1
        wvn = 'morlet'
        
        cwt, sj, freq, coi, _, _ = pycwt.cwt(data, self.dt, dj, s0, J, wvn)
        
        if (fmax > np.max(freq)) or (fmax <= fmin):
            raise ValueError(f"Frequency out of limits! freq range: {freq.min():.3f}-{freq.max():.3f} Hz")
        
        freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
        cwt = cwt[freq_ind]
        freq = freq[freq_ind]
        
        period = 1 / freq
        rcwt = np.abs(cwt) ** 2
        
        # Grid for interpolation
        per = np.arange(int(1/fmax), int(1/fmin), 0.25)
        vel = np.arange(vmin, vmax, 0.01)
        
        velocity_data = self.distance_km / tvec
        fc = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
        rcwt_new = fc(vel, per)
        
        # Normalize
        for ii in range(len(per)):
            max_val = np.max(rcwt_new[ii])
            if max_val > 0:
                rcwt_new[ii] /= max_val
        
        # Smooth
        from scipy.ndimage import gaussian_filter1d
        sigma = 0.15
        for j in range(len(vel)):
            rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=sigma)
        
        self.periods = per
        self.velocities = vel
        self.ftan_amp = rcwt_new
        
        return per, vel, rcwt_new


def plot_ftan_comparison(causal_data, acausal_data, symmetric_data, 
                         periods, velocities, 
                         model_name, az_range, case_id, output_dir):
    """
    Plot side-by-side comparison of causal, acausal, and symmetric FTANs
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    
    lag_types = ['Causal (Positive Lag)', 'Acausal (Negative Lag)', 'Symmetric']
    data_types = [causal_data, acausal_data, symmetric_data]
    
    for i, (ax, data, title) in enumerate(zip(axes, data_types, lag_types)):
        im = ax.imshow(np.transpose(data), 
                      cmap='inferno',
                      extent=[periods[0], periods[-1], 
                             velocities[0], velocities[-1]],
                      aspect='auto',
                      origin='lower',
                      vmin=0, vmax=1,
                      interpolation='bilinear')
        
        ax.set_xlabel('Period (s)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Group Velocity (km/s)', fontsize=12, fontweight='bold')
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3, color='white', linewidth=0.5)
        
        # Colorbar
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Normalized Amplitude', fontsize=10)
    
    # Main title
    fig.suptitle(f'{model_name} | Case {case_id} | az{az_range}°', 
                fontsize=16, fontweight='bold', y=0.98)
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    
    # Save
    filename = f'ftan_comparison_{model_name}_case{case_id}_az{az_range}.png'
    filepath = output_dir / filename
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"  Saved: {filename}")
    
    return filename


def parse_model_name(exp_path):
    sim_dirs = list(exp_path.glob('sim_*'))
    if not sim_dirs:
        return None
    dir_name = sim_dirs[0].name
    match = re.search(r'_rad_\d+_\d+_(.+)$', dir_name)
    if match:
        return match.group(1)
    parts = dir_name.split('_')
    for part in reversed(parts):
        if not part.isdigit() and part:
            return part
    return None


def parse_distance_and_radius(exp_path):
    sim_dirs = list(exp_path.glob('sim_*'))
    if not sim_dirs:
        return None, None
    dir_name = sim_dirs[0].name
    dist_match = re.search(r'_dist_(\d+)_', dir_name)
    distance = int(dist_match.group(1)) if dist_match else None
    rad_match = re.search(r'_rad_(\d+)_(\d+)', dir_name)
    if rad_match:
        rad_min, rad_max = int(rad_match.group(1)), int(rad_match.group(2))
        rad_range = f"{rad_min}-{rad_max}"
    else:
        rad_range = None
    return distance, rad_range


def process_experiment_comparison(exp_num, output_dir):
    print(f"\n{'='*70}")
    print(f"FTAN COMPARISON - EXPERIMENT {exp_num}")
    print('='*70)
    
    exp_base = Path(f"experiments/experiment_{exp_num}/outputs")
    if not exp_base.exists():
        print(f"  Error: {exp_base} not found")
        return 0

    analysis_dir = exp_base / "azimuthal_coverage_analysis"
    if not analysis_dir.exists():
        print(f"  Error: {analysis_dir} not found")
        return 0

    model_name = parse_model_name(exp_base)
    distance_km, rad_range = parse_distance_and_radius(exp_base)

    if not model_name:
        print("  Error: Could not parse model name")
        return 0

    print(f"  Model: {model_name}")
    print(f"  Distance: {distance_km} km")
    print(f"  Radius: {rad_range} km")
    print()

    case_dirs = sorted(analysis_dir.glob('case_*'))
    if not case_dirs:
        print("  Error: No cases found")
        return 0

    print(f"  Processing {len(case_dirs)} cases...")
    print()

    successful = 0
    failed = 0

    for case_dir in case_dirs:
        case_name = case_dir.name
        case_id = case_name.split('_')[1]

        az_match = re.search(r'_az(\d+)-(\d+)', case_name)
        az_range = f"{az_match.group(1)}-{az_match.group(2)}" if az_match else "unknown"

        ccf_file = case_dir / 'stacked_time_ccf.txt'
        if not ccf_file.exists():
            print(f"  Case {case_id}: CCF file not found")
            failed += 1
            continue

        try:
            # Create FTAN object
            ftan_obj = FTAN_Comparison(ccf_file, distance_km, model_name, az_range, rad_range)
            
            # Compute all three types
            print(f"  Case {case_id} (az{az_range}°): Computing FTANs...", end=' ')
            
            per_causal, vel_causal, ftan_causal = ftan_obj.compute_ftan(lag_type='pos')
            per_acausal, vel_acausal, ftan_acausal = ftan_obj.compute_ftan(lag_type='neg')
            per_symmetric, vel_symmetric, ftan_symmetric = ftan_obj.compute_ftan(lag_type='sym')
            
            # Plot comparison
            plot_ftan_comparison(ftan_causal, ftan_acausal, ftan_symmetric,
                               per_causal, vel_causal,
                               model_name, az_range, case_id, output_dir)
            
            successful += 1
            
        except Exception as e:
            print(f"  Case {case_id}: Error - {e}")
            failed += 1

    print()
    print(f"  Complete: {successful} comparisons generated, {failed} failed")
    
    return successful


def main():
    exp_num = 6  # Experiment 6 (tak135sph)
    
    print("="*70)
    print("FTAN CAUSAL vs ACAUSAL vs SYMMETRIC COMPARISON")
    print("="*70)
    print(f"Experiment: {exp_num}")
    print(f"Output: FTAN_COMPARISON/")
    print(f"Format: Side-by-side heatmaps (causal | acausal | symmetric)")
    print()
    
    output_dir = Path("FTAN_COMPARISON")
    output_dir.mkdir(exist_ok=True)
    
    total_generated = process_experiment_comparison(exp_num, output_dir)
    
    if total_generated > 0:
        print(f"\n{'='*70}")
        print("SUMMARY")
        print('='*70)
        print(f"Total comparisons generated: {total_generated}")
        print(f"Images saved to: {output_dir}/")
        print()
    else:
        print("\nNo comparisons generated!")


if __name__ == "__main__":
    main()