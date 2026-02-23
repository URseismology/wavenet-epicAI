#!/usr/bin/env python3
"""
Plot Model Suite Comparisons
- Velocity profiles (Vs, Vp vs depth)
- Dispersion curves (group velocity vs period)
"""

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import subprocess
import os

def read_mod_file(filepath):
    """Read CPS .mod file and extract velocity structure"""
    data = []
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    # Skip header (first 11 lines)
    for line in lines[12:]:
        parts = line.split()
        if len(parts) >= 3:
            try:
                h = float(parts[0])      # thickness (km)
                vp = float(parts[1])     # P-wave velocity (km/s)
                vs = float(parts[2])     # S-wave velocity (km/s)
                data.append([h, vp, vs])
            except:
                continue
    
    return np.array(data)


def compute_depth_profile(layer_data):
    """Convert layer thicknesses to depth-velocity profile"""
    depths = []
    vp_profile = []
    vs_profile = []
    
    cumulative_depth = 0
    for h, vp, vs in layer_data:
        # Top of layer
        depths.append(cumulative_depth)
        vp_profile.append(vp)
        vs_profile.append(vs)
        
        # Bottom of layer
        cumulative_depth += h
        depths.append(cumulative_depth)
        vp_profile.append(vp)
        vs_profile.append(vs)
    
    return np.array(depths), np.array(vp_profile), np.array(vs_profile)

def plot_velocity_profiles(models_data, output_file):
    """Plot Vp and Vs profiles for all models"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 10))
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(models_data)))
    
    for i, (name, data) in enumerate(models_data.items()):
        depths, vp, vs = data
        
        # Plot Vs (left panel)
        ax1.plot(vs, depths, label=name, linewidth=2.5, color=colors[i])
        
        # Plot Vp (right panel)
        ax2.plot(vp, depths, label=name, linewidth=2.5, color=colors[i])
    
    # Format Vs plot
    ax1.invert_yaxis()
    ax1.set_xlabel('S-wave Velocity (km/s)', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Depth (km)', fontsize=14, fontweight='bold')
    ax1.set_title('Shear Velocity Profiles', fontsize=16, fontweight='bold')
    ax1.legend(fontsize=11, loc='lower right')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, 8)
    ax1.set_ylim(bottom=max([d.max() for d in [data[0] for data in models_data.values()]]))
    
    # Format Vp plot
    ax2.invert_yaxis()
    ax2.set_xlabel('P-wave Velocity (km/s)', fontsize=14, fontweight='bold')
    ax2.set_ylabel('Depth (km)', fontsize=14, fontweight='bold')
    ax2.set_title('P-wave Velocity Profiles', fontsize=16, fontweight='bold')
    ax2.legend(fontsize=11, loc='lower right')
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(4, 14)
    ax2.set_ylim(bottom=max([d.max() for d in [data[0] for data in models_data.values()]]))
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved velocity profiles: {output_file}")


def plot_vs_crustal_detail(models_data, output_file, max_depth=100):
    """Plot Vs profiles with crustal detail (0-100 km)"""
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(models_data)))
    
    for i, (name, data) in enumerate(models_data.items()):
        depths, vp, vs = data
        
        # Filter for crustal depths
        mask = depths <= max_depth
        ax.plot(vs[mask], depths[mask], label=name, linewidth=2.5, color=colors[i])
    
    ax.invert_yaxis()
    ax.set_xlabel('S-wave Velocity (km/s)', fontsize=14, fontweight='bold')
    ax.set_ylabel('Depth (km)', fontsize=14, fontweight='bold')
    ax.set_title(f'Crustal Shear Velocity (0-{max_depth} km)', fontsize=16, fontweight='bold')
    ax.legend(fontsize=11, loc='lower right')
    ax.grid(True, alpha=0.3)
    ax.set_xlim(3, 5)
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved crustal detail: {output_file}")


def plot_velocity_differences(models_data, reference_model, output_file):
    """Plot velocity differences relative to reference model"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 10))
    
    ref_depths, ref_vp, ref_vs = models_data[reference_model]
    colors = plt.cm.tab10(np.linspace(0, 1, len(models_data)-1))
    
    color_idx = 0
    for name, data in models_data.items():
        if name == reference_model:
            continue
        
        depths, vp, vs = data
        
        # Interpolate to common depth grid
        common_depths = np.linspace(0, min(depths.max(), ref_depths.max()), 1000)
        vs_interp = np.interp(common_depths, depths, vs)
        vp_interp = np.interp(common_depths, depths, vp)
        ref_vs_interp = np.interp(common_depths, ref_depths, ref_vs)
        ref_vp_interp = np.interp(common_depths, ref_depths, ref_vp)
        
        # Compute differences
        dvs = ((vs_interp - ref_vs_interp) / ref_vs_interp) * 100  # percent
        dvp = ((vp_interp - ref_vp_interp) / ref_vp_interp) * 100
        
        ax1.plot(dvs, common_depths, label=name, linewidth=2.5, color=colors[color_idx])
        ax2.plot(dvp, common_depths, label=name, linewidth=2.5, color=colors[color_idx])
        color_idx += 1
    
    # Format dVs plot
    ax1.invert_yaxis()
    ax1.axvline(0, color='k', linestyle='--', linewidth=1)
    ax1.set_xlabel('ΔVs (%)', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Depth (km)', fontsize=14, fontweight='bold')
    ax1.set_title(f'Vs Difference from {reference_model}', fontsize=16, fontweight='bold')
    ax1.legend(fontsize=11, loc='best')
    ax1.grid(True, alpha=0.3)
    
    # Format dVp plot
    ax2.invert_yaxis()
    ax2.axvline(0, color='k', linestyle='--', linewidth=1)
    ax2.set_xlabel('ΔVp (%)', fontsize=14, fontweight='bold')
    ax2.set_ylabel('Depth (km)', fontsize=14, fontweight='bold')
    ax2.set_title(f'Vp Difference from {reference_model}', fontsize=16, fontweight='bold')
    ax2.legend(fontsize=11, loc='best')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved velocity differences: {output_file}")



def main():
    # Model files
    model_dir = Path('experiments/model_suite')
    
    model_files = {
        'tak135sph': model_dir / 'tak135sph.mod',
        'Central US': model_dir / 'Central_US_Continental.mod',
        'East African Rift': model_dir / 'East_African_Rift.mod',
        'Australian Interior': model_dir / 'Australian_Interior.mod',
        'Siberian Craton': model_dir / 'Siberian_Craton.mod',
        'Arabian Shield': model_dir / 'Arabian_Shield.mod',
        'Korea': model_dir / 'KOREA.mod',
        'West US': model_dir / 'WUS.mod',
        'CIA': model_dir / 'CIA.mod',
        'CUS': model_dir / 'CUS.mod'
    }
    
    output_dir = Path('model_suite_plots')
    output_dir.mkdir(exist_ok=True)
    
    models_data = {}
    for name, filepath in model_files.items():
        if filepath.exists():
            layer_data = read_mod_file(filepath)
            depths, vp, vs = compute_depth_profile(layer_data)
            models_data[name] = (depths, vp, vs)
            print(f"  {name}: {len(layer_data)} layers, max depth {depths.max():.1f} km")
        else:
            print(f"  Warning: {filepath} not found")
    
    plot_velocity_profiles(models_data, output_dir / 'velocity_profiles_all.png')
    plot_vs_crustal_detail(models_data, output_dir / 'velocity_profiles_crustal.png', max_depth=100)
    
    plot_velocity_differences(models_data, 'tak135sph', output_dir / 'velocity_differences.png')
    
    print(f"Plots saved to: {output_dir}/")
    print(f"  - velocity_profiles_all.png")
    print(f"  - velocity_profiles_crustal.png")
    print(f"  - velocity_differences.png")


if __name__ == "__main__":
    main()