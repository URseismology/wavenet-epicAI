#!/usr/bin/env python3
"""
Visualize perturbed velocity models

Creates plots showing base model + all perturbations
Similar to the uploaded reference image
"""

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path


def read_mod_file(filepath):
    """Read .mod file and return header, data"""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    data_start = None
    for i, line in enumerate(lines):
        if 'H(KM)' in line or 'H (KM)' in line:
            data_start = i + 1
            break
    
    return lines[:data_start], lines[data_start:]


def parse_layers(data_lines):
    """Parse layer data into depths and velocities"""
    depths_top = []
    depths_bot = []
    vp = []
    vs = []
    
    current_depth = 0
    
    for line in data_lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        
        try:
            h = float(parts[0])
            vp_val = float(parts[1])
            vs_val = float(parts[2])
            
            # Top of layer
            depths_top.append(current_depth)
            depths_bot.append(current_depth)
            vp.append(vp_val)
            vs.append(vs_val)
            
            # Bottom of layer
            if h > 0:
                current_depth += h
            else:
                current_depth += 30  # Extend half-space for visualization
            
            depths_top.append(current_depth)
            depths_bot.append(current_depth)
            vp.append(vp_val)
            vs.append(vs_val)
            
        except:
            continue
    
    return np.array(depths_top), np.array(vp), np.array(vs)


def plot_model_family(base_model_path, output_dir, max_perturbations=None):
    """
    Plot base model + all perturbations
    
    Args:
        base_model_path: Path to base model file
        output_dir: Directory with perturbed models
        max_perturbations: Max number of perturbations to plot (None = all)
    """
    output_dir = Path(output_dir)
    
    # Extract model name
    base_name = Path(base_model_path).stem
    if '_' in base_name:
        parts = base_name.split('_')
        for part in reversed(parts):
            if part and not part.isdigit():
                model_name = part
                break
    else:
        model_name = base_name
    
    # Read base model (original = _0001.mod)
    base_file = output_dir / f'{model_name}_0001.mod'
    if not base_file.exists():
        print(f"Base model not found: {base_file}")
        return
    
    _, base_data = read_mod_file(base_file)
    base_depth, base_vp, base_vs = parse_layers(base_data)
    
    # Find all perturbed versions
    perturbed_files = sorted(output_dir.glob(f'{model_name}_*.mod'))
    if max_perturbations:
        perturbed_files = perturbed_files[:max_perturbations]
    
    print(f"Plotting {model_name}: 1 base + {len(perturbed_files)-1} perturbed")
    
    # Create figure
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    
    # Plot Vp (left)
    for pfile in perturbed_files[1:]:  # Skip _0001 (base)
        _, pdata = read_mod_file(pfile)
        pdepth, pvp, pvs = parse_layers(pdata)
        axes[0].plot(pvp, pdepth, color='gray', alpha=0.15, linewidth=0.5)
    
    # Plot base Vp on top
    axes[0].plot(base_vp, base_depth, color='red', linewidth=2.5, label='Base model (Vp)')
    axes[0].set_xlabel('P-wave Velocity (km/s)', fontsize=12)
    axes[0].set_ylabel('Depth (km)', fontsize=12)
    axes[0].set_title(f'{model_name} - Vp', fontsize=13, fontweight='bold')
    axes[0].invert_yaxis()
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=10)
    
    # Plot Vs (right)
    for pfile in perturbed_files[1:]:  # Skip _0001
        _, pdata = read_mod_file(pfile)
        pdepth, pvp, pvs = parse_layers(pdata)
        axes[1].plot(pvs, pdepth, color='gray', alpha=0.15, linewidth=0.5)
    
    # Plot base Vs on top
    axes[1].plot(base_vs, base_depth, color='blue', linewidth=2.5, label='Base model (Vs)')
    axes[1].set_xlabel('S-wave Velocity (km/s)', fontsize=12)
    axes[1].set_ylabel('Depth (km)', fontsize=12)
    axes[1].set_title(f'{model_name} - Vs', fontsize=13, fontweight='bold')
    axes[1].invert_yaxis()
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=10)
    
    plt.tight_layout()
    
    return fig, model_name


def plot_all_models(base_dir, output_dir, save_dir, max_perturbations=1000):
    """
    Create plots for all base models
    
    Args:
        base_dir: Directory with original .mod files
        output_dir: Directory with perturbed models
        save_dir: Directory to save plots
        max_perturbations: Max perturbations to plot per model
    """
    base_dir = Path(base_dir)
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    
    base_files = sorted(base_dir.glob('*.mod'))
    
    print(f"Found {len(base_files)} base models")
    print(f"Creating visualizations...")
    print()
    
    for base_file in base_files:
        try:
            fig, model_name = plot_model_family(base_file, output_dir, max_perturbations)
            
            # Save plot
            plot_path = save_dir / f'{model_name}_perturbations.png'
            plt.savefig(plot_path, dpi=200, bbox_inches='tight')
            plt.close()
            
            print(f"  Saved: {plot_path}")
            
        except Exception as e:
            print(f"  Error plotting {base_file.name}: {e}")
    
    print()
    print(f"All plots saved to: {save_dir}/")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Visualize perturbed velocity models')
    parser.add_argument('--base_dir', type=str, required=True,
                       help='Directory with original .mod files')
    parser.add_argument('--perturbed_dir', type=str, required=True,
                       help='Directory with perturbed models')
    parser.add_argument('--output_dir', type=str, default='perturbation_plots',
                       help='Directory to save plots')
    parser.add_argument('--max_plot', type=int, default=1000,
                       help='Max perturbations to plot per model')
    
    args = parser.parse_args()
    
    plot_all_models(args.base_dir, args.perturbed_dir, args.output_dir, args.max_plot)