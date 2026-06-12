#!/usr/bin/env python3
import numpy as np
from pathlib import Path
import shutil


def read_mod_file(filepath):
    """Read .mod file and split into header and data"""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    # Find data start
    data_start = None
    for i, line in enumerate(lines):
        if 'H(KM)' in line or 'H (KM)' in line:
            data_start = i + 1
            break
    
    return lines[:data_start], lines[data_start:]


def parse_layer(line):
    """Parse layer data line"""
    parts = line.split()
    if len(parts) < 10:
        return None
    
    return {
        'H': float(parts[0]),
        'VP': float(parts[1]),
        'VS': float(parts[2]),
        'RHO': float(parts[3]),
        'QP': parts[4],
        'QS': parts[5],
        'ETAP': parts[6],
        'ETAS': parts[7],
        'FREFP': parts[8],
        'FREFS': parts[9]
    }


def perturb_layer(layer, variation=0.10, rng=None):
    """Perturb layer by ±variation (default ±10%)"""
    if layer is None or rng is None:
        return layer
    
    perturbed = layer.copy()
    
    # Don't perturb half-space thickness (H=0)
    if layer['H'] > 0:
        perturbed['H'] = layer['H'] * (1 + rng.uniform(-variation, variation))
    
    # Perturb velocities and density
    perturbed['VP'] = layer['VP'] * (1 + rng.uniform(-variation, variation))
    perturbed['VS'] = layer['VS'] * (1 + rng.uniform(-variation, variation))
    perturbed['RHO'] = layer['RHO'] * (1 + rng.uniform(-variation, variation))
    
    return perturbed


def format_layer(layer):
    """Format layer as .mod file line"""
    return (f"  {layer['H']:7.4f} {layer['VP']:7.4f} {layer['VS']:7.4f} "
            f"{layer['RHO']:7.4f} {layer['QP']} {layer['QS']} "
            f"{layer['ETAP']} {layer['ETAS']} {layer['FREFP']} {layer['FREFS']}\n")


def create_perturbed_model(input_file, output_file, variation=0.10, seed=None):
    """Create perturbed .mod file"""
    rng = np.random.default_rng(seed)
    
    header, data_lines = read_mod_file(input_file)
    layers = [parse_layer(line) for line in data_lines]
    perturbed = [perturb_layer(layer, variation, rng) for layer in layers]
    
    with open(output_file, 'w') as f:
        f.writelines(header)
        for layer in perturbed:
            if layer:
                f.write(format_layer(layer))
            else:
                f.write('\n')


def generate_all_models(base_dir, output_dir, n_total=1000, variation=0.10):
    """
    Generate perturbed models
    
    For each base model:
    - basename_0001.mod = original copy
    - basename_0002.mod to basename_1000.mod = 999 perturbed
    """
    base_dir = Path(base_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all .mod files
    mod_files = sorted(base_dir.glob('*.mod'))
    
    print(f"Found {len(mod_files)} base models")
    print(f"Creating {n_total} versions per model (1 original + {n_total-1} perturbed)")
    print(f"Perturbation: ±{variation*100:.0f}%")
    print(f"Total models: {len(mod_files) * n_total}")
    print()
    
    total = 0
    
    for mod_file in mod_files:
        # Extract clean name (remove timestamp prefix if exists)
        name = mod_file.stem
        
        # Try to extract meaningful name
        if '_' in name:
            parts = name.split('_')
            # Take last meaningful part
            for part in reversed(parts):
                if part and not part.isdigit():
                    clean_name = part
                    break
        else:
            clean_name = name
        
        print(f"Processing {clean_name}...")
        
        for i in range(1, n_total + 1):
            output_file = output_dir / f'{clean_name}_{i:04d}.mod'
            
            if i == 1:
                # First file = original copy
                shutil.copy(mod_file, output_file)
            else:
                # Perturbed versions
                seed = hash(f"{clean_name}_{i}") % (2**32)
                create_perturbed_model(mod_file, output_file, variation, seed)
            
            total += 1
            
            if i % 100 == 0:
                print(f"  {i}/{n_total}")
        
        print(f"  Complete: {clean_name}")
        print()
    
    print(f"\nTotal models generated: {total}")
    print(f"Output: {output_dir}/")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_dir', type=str, required=True,
                       help='Directory with base .mod files')
    parser.add_argument('--output_dir', type=str, required=True,
                       help='Output directory')
    parser.add_argument('--n_total', type=int, default=1000,
                       help='Total files per model (1 original + N-1 perturbed)')
    parser.add_argument('--variation', type=float, default=0.10,
                       help='Perturbation (0.10 = ±10%%)')
    
    args = parser.parse_args()
    
    generate_all_models(args.input_dir, args.output_dir, args.n_total, args.variation)