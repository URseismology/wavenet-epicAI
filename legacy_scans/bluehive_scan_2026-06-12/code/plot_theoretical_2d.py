#!/usr/bin/env python3
"""
Plot theoretical group velocity dispersion as 2D images.

Creates synthetic FTAN-like maps from theoretical dispersion curves,
useful for comparison with observed FTAN results.

Usage:
    python plot_theoretical_dispersion_2d.py <SREGN.ASC file>
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy.ndimage import gaussian_filter


def load_theoretical_dispersion(sregn_file, mode=0):
    data = np.loadtxt(sregn_file, skiprows=1)
    
    # Filter for specific mode
    mode_mask = data[:, 0] == mode
    periods = data[mode_mask, 2]
    group_velocities = data[mode_mask, 5]  # Column 5 is group velocity
    
    return periods, group_velocities


def create_theoretical_2d_map(periods, group_velocities, 
                              period_range=(1, 20), velocity_range=(0.5, 4.5),
                              period_spacing=0.25, velocity_spacing=0.01,
                              sigma_period=0.5, sigma_velocity=0.1):
    # Create regular grids
    period_grid = np.arange(period_range[0], period_range[1], period_spacing)
    velocity_grid = np.arange(velocity_range[0], velocity_range[1], velocity_spacing)
    
    # Initialize amplitude map
    amplitude_map = np.zeros((len(period_grid), len(velocity_grid)))
    
    # For each theoretical point, add Gaussian blob
    for per, vel in zip(periods, group_velocities):
        # Only process if within grid bounds
        if (per < period_range[0] or per >= period_range[1] or 
            vel < velocity_range[0] or vel >= velocity_range[1]):
            continue
        
        # Find nearest grid indices
        per_idx = np.argmin(np.abs(period_grid - per))
        vel_idx = np.argmin(np.abs(velocity_grid - vel))
        
        # Create Gaussian blob around this point
        for i, p in enumerate(period_grid):
            for j, v in enumerate(velocity_grid):
                distance_period = (p - per) / sigma_period
                distance_velocity = (v - vel) / sigma_velocity
                distance = np.sqrt(distance_period**2 + distance_velocity**2)
                
                # Gaussian function
                amplitude_map[i, j] += np.exp(-0.5 * distance**2)
    
    # Normalize
    if np.max(amplitude_map) > 0:
        amplitude_map /= np.max(amplitude_map)
    
    return period_grid, velocity_grid, amplitude_map


def create_theoretical_sharp_map(periods, group_velocities,
                                 period_range=(1, 20), velocity_range=(0.5, 4.5),
                                 period_spacing=0.25, velocity_spacing=0.01,
                                 line_width=3):
    # Create regular grids
    period_grid = np.arange(period_range[0], period_range[1], period_spacing)
    velocity_grid = np.arange(velocity_range[0], velocity_range[1], velocity_spacing)
    
    # Initialize amplitude map
    amplitude_map = np.zeros((len(period_grid), len(velocity_grid)))
    
    # For each theoretical point, set nearby cells to 1
    for per, vel in zip(periods, group_velocities):
        # Only process if within grid bounds
        if (per < period_range[0] or per >= period_range[1] or 
            vel < velocity_range[0] or vel >= velocity_range[1]):
            continue
        
        # Find nearest grid indices
        per_idx = np.argmin(np.abs(period_grid - per))
        vel_idx = np.argmin(np.abs(velocity_grid - vel))
        
        # Set cells within line_width to 1
        per_start = max(0, per_idx - line_width // 2)
        per_end = min(len(period_grid), per_idx + line_width // 2 + 1)
        vel_start = max(0, vel_idx - line_width // 2)
        vel_end = min(len(velocity_grid), vel_idx + line_width // 2 + 1)
        
        amplitude_map[per_start:per_end, vel_start:vel_end] = 1.0
    
    return period_grid, velocity_grid, amplitude_map


def plot_theoretical_dispersion_2d(period_grid, velocity_grid, amplitude_map,
                                   title="Theoretical Group Velocity Dispersion",
                                   output_file=None, cmap='inferno'):
    fig, ax = plt.subplots(figsize=(14, 8))
    
    # Plot 2D map
    extent = [period_grid[0], period_grid[-1], velocity_grid[0], velocity_grid[-1]]
    im = ax.imshow(np.transpose(amplitude_map), 
                   cmap=cmap,
                   extent=extent,
                   aspect='auto',
                   origin='lower',
                   vmin=0, vmax=1)
    
    ax.set_xlabel('Period (s)', fontsize=14, fontweight='bold')
    ax.set_ylabel('Group Velocity (km/s)', fontsize=14, fontweight='bold')
    ax.set_title(title, fontsize=16, fontweight='bold')
    ax.grid(True, alpha=0.2, color='white', linewidth=0.5)
    
    cbar = plt.colorbar(im, ax=ax, pad=0.02)
    cbar.set_label('Normalized Amplitude', fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    
    if output_file:
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        print(f"✓ Saved: {output_file}")
    
    plt.show()


def main():
    if len(sys.argv) < 2:
        print("Usage: python plot_theoretical_dispersion_2d.py <SREGN.ASC file>")
        print("\nExample:")
        print("  python plot_theoretical_dispersion_2d.py experiments/experiment_5/outputs/final_dist_150_rad_50_500/SREGN.ASC")
        sys.exit(1)
    
    sregn_file = Path(sys.argv[1])
    
    if not sregn_file.exists():
        print(f"✗ Error: File not found: {sregn_file}")
        sys.exit(1)
    

    
    # Load fundamental mode (mode 0)
    periods, velocities = load_theoretical_dispersion(sregn_file, mode=0)
    print(f"  Loaded {len(periods)} points")
    print(f"  Period range: {periods.min():.1f} - {periods.max():.1f} s")
    print(f"  Velocity range: {velocities.min():.2f} - {velocities.max():.2f} km/s")
    print()
    
    output_dir = sregn_file.parent / "theoretical_dispersion_2d"
    output_dir.mkdir(exist_ok=True)
    
    period_grid, velocity_grid, amplitude_map = create_theoretical_2d_map(
        periods, velocities,
        period_range=(1, 40),
        velocity_range=(0.5, 4.5),
        sigma_period=0.5,      # Wider in period
        sigma_velocity=0.1     # Narrower in velocity
    )
    
    plot_theoretical_dispersion_2d(
        period_grid, velocity_grid, amplitude_map,
        title="Theoretical Dispersion (Gaussian Spreading)",
        output_file=output_dir / "theoretical_gaussian.png",
        cmap='inferno'
    )
    
    period_grid, velocity_grid, amplitude_map = create_theoretical_sharp_map(
        periods, velocities,
        period_range=(1, 40),
        velocity_range=(0.5, 4.5),
        line_width=5
    )
    
    plot_theoretical_dispersion_2d(
        period_grid, velocity_grid, amplitude_map,
        title="Theoretical Dispersion (Sharp Curve)",
        output_file=output_dir / "theoretical_sharp.png",
        cmap='inferno'
    )
    
    print(f"Output directory: {output_dir}/")


if __name__ == "__main__":
    main()