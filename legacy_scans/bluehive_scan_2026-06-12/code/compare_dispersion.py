#!/usr/bin/env python3
"""
Compare CWT-extracted dispersion with theoretical dispersion from CPS
"""
import numpy as np
import matplotlib.pyplot as plt
import sys
import os

def load_sregn_asc(filepath):
    """
    Load SREGN.ASC (Rayleigh wave theoretical dispersion)
    
    Format:
    RMODE NFREQ PERIOD(S) FREQUENCY(Hz) C(KM/S) U(KM/S) AR GAMMA(1/KM) ELLIPTICITY
    """
    print(f"Loading theoretical dispersion: {filepath}")
    
    # Load data, skipping header
    data = np.loadtxt(filepath, skiprows=1)
    
    # Extract columns
    mode = data[:, 0]          # Mode number (0 = fundamental)
    periods = data[:, 2]       # Period (s)
    phase_vel = data[:, 4]     # C(KM/S) - phase velocity
    group_vel = data[:, 5]     # U(KM/S) - group velocity
    
    # Filter for fundamental mode only (mode=0)
    mode0_mask = mode == 0
    periods = periods[mode0_mask]
    phase_vel = phase_vel[mode0_mask]
    group_vel = group_vel[mode0_mask]
    
    print(f"  Loaded {len(periods)} points (mode=0)")
    print(f"  Period range: {periods[0]:.1f} - {periods[-1]:.1f} s")
    print(f"  Group velocity range: {np.min(group_vel):.2f} - {np.max(group_vel):.2f} km/s")
    
    return periods, phase_vel, group_vel

def load_cwt_dispersion(filepath):
    """Load CWT-extracted dispersion"""
    print(f"\nLoading CWT-extracted dispersion: {filepath}")
    
    data = np.loadtxt(filepath)
    periods = data[:, 0]
    group_vel = data[:, 1]
    amplitudes = data[:, 2]
    
    print(f"  Loaded {len(periods)} points")
    print(f"  Group velocity range: {np.min(group_vel):.2f} - {np.max(group_vel):.2f} km/s")
    
    return periods, group_vel, amplitudes

def plot_comparison(periods_theory, group_vel_theory, 
                   periods_cwt, group_vel_cwt, 
                   distance_km, output_dir):
    """Plot theoretical vs CWT-extracted dispersion"""
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    
    # Period vs velocity
    ax1.plot(periods_theory, group_vel_theory, 'k-', linewidth=3, 
            label='Theoretical (tak135sph)', alpha=0.7)
    ax1.plot(periods_cwt, group_vel_cwt, 'ro-', linewidth=2, markersize=6, 
            label='CWT-Extracted', alpha=0.8)
    ax1.set_xlabel('Period (s)', fontsize=14)
    ax1.set_ylabel('Group Velocity (km/s)', fontsize=14)
    ax1.set_title(f'Dispersion Comparison - {distance_km} km', 
                 fontsize=16, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=12)
    ax1.set_xlim([5, 40])
    ax1.set_ylim([1.5, 5.0])
    
    # Frequency vs velocity
    freqs_theory = 1.0 / periods_theory
    freqs_cwt = 1.0 / periods_cwt
    
    ax2.plot(freqs_theory, group_vel_theory, 'k-', linewidth=3, 
            label='Theoretical (tak135sph)', alpha=0.7)
    ax2.plot(freqs_cwt, group_vel_cwt, 'ro-', linewidth=2, markersize=6, 
            label='CWT-Extracted', alpha=0.8)
    ax2.set_xlabel('Frequency (Hz)', fontsize=14)
    ax2.set_ylabel('Group Velocity (km/s)', fontsize=14)
    ax2.set_title(f'Dispersion Comparison - {distance_km} km', 
                 fontsize=16, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=12)
    ax2.set_xlim([0.025, 0.2])
    ax2.set_ylim([1.5, 5.0])
    
    plt.tight_layout()
    
    outfile = os.path.join(output_dir, f'dispersion_comparison_{distance_km}km.png')
    plt.savefig(outfile, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"\n✓ Saved comparison: {outfile}")
    
    # Compute misfit
    # Interpolate to common period grid (5-40s range where we have CWT data)
    period_min = max(5, np.min(periods_cwt))
    period_max = min(40, np.max(periods_cwt))
    periods_common = np.linspace(period_min, period_max, 50)
    
    theory_interp = np.interp(periods_common, periods_theory, group_vel_theory)
    cwt_interp = np.interp(periods_common, periods_cwt, group_vel_cwt)
    
    misfit = np.sqrt(np.mean((theory_interp - cwt_interp)**2))
    rel_misfit = misfit / np.mean(theory_interp) * 100
    
    print(f"\nMisfit Analysis:")
    print(f"  RMS misfit: {misfit:.3f} km/s")
    print(f"  Relative misfit: {rel_misfit:.1f}%")
    
    if rel_misfit < 5:
        print("  ✓ EXCELLENT agreement!")
    elif rel_misfit < 10:
        print("  ✓ GOOD agreement")
    elif rel_misfit < 20:
        print("  ⚠ FAIR agreement")
    else:
        print("  ✗ POOR agreement - likely noise-dominated")

def main():
    if len(sys.argv) != 3:
        print("Usage: python compare_dispersion.py <final_dist_dir> <distance_km>")
        print("Example: python compare_dispersion.py experiments/experiment_5/outputs/final_dist_150_rad_50_500 150")
        sys.exit(1)
    
    final_dir = sys.argv[1]
    distance_km = float(sys.argv[2])
    
    print("="*70)
    print("Dispersion Curve Comparison: Theoretical vs CWT-Extracted")
    print("="*70)
    
    # Load theoretical dispersion
    sregn_file = os.path.join(final_dir, 'SREGN.ASC')
    if not os.path.exists(sregn_file):
        print(f"ERROR: Theoretical dispersion not found: {sregn_file}")
        print("Make sure compute_ccf_final.py copied eigenfunction files!")
        sys.exit(1)
    
    periods_theory, phase_vel, group_vel_theory = load_sregn_asc(sregn_file)
    
    # Load CWT-extracted dispersion
    cwt_file = os.path.join(final_dir, f'dispersion_cwt_{distance_km}km.txt')
    if not os.path.exists(cwt_file):
        print(f"ERROR: CWT dispersion not found: {cwt_file}")
        print("Run ftan_cwt.py first!")
        sys.exit(1)
    
    periods_cwt, group_vel_cwt, amplitudes = load_cwt_dispersion(cwt_file)
    
    # Plot comparison
    plot_comparison(periods_theory, group_vel_theory, 
                   periods_cwt, group_vel_cwt, 
                   distance_km, final_dir)
    
    print("="*70)
    print("✓ Comparison Complete")
    print("="*70)

if __name__ == "__main__":
    main()
