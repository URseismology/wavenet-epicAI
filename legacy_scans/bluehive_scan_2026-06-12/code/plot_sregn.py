#!/usr/bin/env python3
"""
Plot group velocity dispersion curves from SREGN.ASC files.

Columns: RMODE NFREQ PERIOD(S) FREQUENCY(Hz) C(KM/S) U(KM/S) AR GAMMA(1/KM) ELLIPTICITY

Produces:
  1. vel_curves/plots/all_families.png  - all curves, colored by family
  2. vel_curves/plots/<Family>.png      - one plot per family, all models
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from pathlib import Path
import sys


def read_sregn(filepath):
    """Read SREGN.ASC and return mode-0 period and group velocity arrays."""
    periods = []
    u_vals  = []

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('RMODE'):
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            try:
                mode   = int(parts[0])
                period = float(parts[2])
                u      = float(parts[5])
                if mode == 0:
                    periods.append(period)
                    u_vals.append(u)
            except ValueError:
                continue

    if not periods:
        return None, None

    periods = np.array(periods)
    u_vals  = np.array(u_vals)
    order   = np.argsort(periods)
    return periods[order], u_vals[order]


def main():
    root         = Path('experiments/experiment_17/vel_curves')
    plots_dir    = root / 'plots'
    plots_dir.mkdir(exist_ok=True)

    # Discover families (subdirs with .ASC files, excluding plots/)
    families = sorted([
        d for d in root.iterdir()
        if d.is_dir() and d.name != 'plots'
    ])

    if not families:
        print("No family directories found in", root)
        sys.exit(1)

    print(f"Found {len(families)} families: {[f.name for f in families]}")

    # Color map - one color per family
    family_colors = {
        fam.name: color
        for fam, color in zip(families, cm.tab10.colors)
    }

    # -----------------------------------------------------------------------
    # Plot 1: All families in one figure
    # -----------------------------------------------------------------------
    print("\nGenerating all_families.png ...")

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_facecolor('#0d0d0d')
    fig.patch.set_facecolor('#0d0d0d')

    plotted = {fam.name: False for fam in families}

    for fam_dir in families:
        color    = family_colors[fam_dir.name]
        asc_files = sorted(fam_dir.glob('*.ASC'))
        print(f"  {fam_dir.name}: {len(asc_files)} files")

        for asc_file in asc_files:
            periods, u_vals = read_sregn(asc_file)
            if periods is None:
                continue
            mask = periods <= 50
            periods, u_vals = periods[mask], u_vals[mask]
            if len(periods) == 0:
                continue

            label = fam_dir.name if not plotted[fam_dir.name] else None
            ax.plot(periods, u_vals,
                    color=color, alpha=0.15, linewidth=0.4, label=label)
            plotted[fam_dir.name] = True

    ax.set_xlabel('Period (s)',           color='white', fontsize=13)
    ax.set_ylabel('Group Velocity (km/s)', color='white', fontsize=13)
    ax.set_title('Rayleigh Wave Group Velocity — All Families',
                 color='white', fontsize=14, fontweight='bold')
    ax.tick_params(colors='white', labelsize=10)
    for sp in ax.spines.values():
        sp.set_edgecolor('#444444')
    ax.grid(True, alpha=0.15, color='white')

    legend = ax.legend(fontsize=10, framealpha=0.3,
                       facecolor='#222222', edgecolor='#555555', labelcolor='white',
                       loc='upper right')

    plt.tight_layout()
    out = plots_dir / 'all_families.png'
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Saved: {out}")

    # -----------------------------------------------------------------------
    # Plot 2: One figure per family
    # -----------------------------------------------------------------------
    for fam_dir in families:
        asc_files = sorted(fam_dir.glob('*.ASC'))
        if not asc_files:
            continue

        print(f"\nGenerating {fam_dir.name}.png ({len(asc_files)} models)...")

        fig, ax = plt.subplots(figsize=(12, 7))
        ax.set_facecolor('#0d0d0d')
        fig.patch.set_facecolor('#0d0d0d')

        # Use a gradient colormap within the family
        cmap   = cm.get_cmap('plasma', len(asc_files))
        color  = family_colors[fam_dir.name]

        for i, asc_file in enumerate(asc_files):
            periods, u_vals = read_sregn(asc_file)
            if periods is None:
                continue
            mask = periods <= 50
            periods, u_vals = periods[mask], u_vals[mask]
            if len(periods) == 0:
                continue
            ax.plot(periods, u_vals,
                    color=cmap(i), alpha=0.2, linewidth=0.4)

        ax.set_xlabel('Period (s)',            color='white', fontsize=13)
        ax.set_ylabel('Group Velocity (km/s)', color='white', fontsize=13)
        ax.set_title(f'Rayleigh Wave Group Velocity — {fam_dir.name} ({len(asc_files)} models)',
                     color='white', fontsize=14, fontweight='bold')
        ax.tick_params(colors='white', labelsize=10)
        for sp in ax.spines.values():
            sp.set_edgecolor('#444444')
        ax.grid(True, alpha=0.15, color='white')

        plt.tight_layout()
        out = plots_dir / f'{fam_dir.name}.png'
        plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
        plt.close()
        print(f"  Saved: {out}")

    print("\nDone. All plots in:", plots_dir)


if __name__ == '__main__':
    main()