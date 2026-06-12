#!/usr/bin/env python3
"""
Azimuthal Coverage Analysis - Enhanced Version

Includes:
- Source distribution plots
- Time-domain CCF
- Frequency-domain coherence (smoothed)
- Eigenfunction file extraction
- Coherence data saved to file

Usage:
    python analyze_azimuthal_coverage_enhanced.py <experiment_number>

Example:
    python analyze_azimuthal_coverage_enhanced.py 5
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import glob
import pandas as pd
import shutil
import subprocess
import os
from scipy.signal import windows, detrend

# Define all 10 azimuthal coverage cases
CASES = [
    {"id": "01", "name": "full", "az_start": 0, "az_end": 360, "description": "Full ring coverage"},
    {"id": "02", "name": "hemisphere", "az_start": 270, "az_end": 90, "description": "W→N→E hemisphere"},
    {"id": "03", "name": "narrow_N", "az_start": 340, "az_end": 20, "description": "Narrow North sector"},
    {"id": "04", "name": "NNE", "az_start": 20, "az_end": 50, "description": "North-Northeast"},
    {"id": "05", "name": "NE_E", "az_start": 50, "az_end": 90, "description": "Northeast to East"},
    {"id": "06", "name": "NE_ESE", "az_start": 60, "az_end": 120, "description": "NE to ESE sector"},
    {"id": "07", "name": "E_S", "az_start": 90, "az_end": 180, "description": "East to South"},
    {"id": "08", "name": "narrow_N2", "az_start": 0, "az_end": 20, "description": "Very narrow North"},
    {"id": "09", "name": "NW", "az_start": 300, "az_end": 340, "description": "Northwest sector"},
    {"id": "10", "name": "W_NW", "az_start": 270, "az_end": 330, "description": "West to Northwest"},
]

DELTA = 0.5  # Sampling interval (seconds)
FILTER_PERIOD_RANGE = [2.0, 100.0]  # Period range (seconds): [min, max]
FILTER_FREQ_RANGE = [1.0/FILTER_PERIOD_RANGE[1], 1.0/FILTER_PERIOD_RANGE[0]]  # [0.01, 0.5] Hz
TAPER_WIDTH = 0.2  # Cosine taper width
SMOOTH_WINDOW = 10  # Smoothing window size


def moving_average_numpy(data, window_size):
    """Apply moving average smoothing (matches MATLAB smooth())."""
    kernel = np.ones(window_size) / window_size
    smoothed_data = np.convolve(data, kernel, mode='same')
    return smoothed_data


def get_wedge_numbers(az_start, az_end, wedge_width=2):
    """
    Convert azimuth range to wedge numbers.
    
    Wedges are numbered 1-180, each covering 2 degrees.
    Handles wraparound cases (e.g., 340-20 spans across 0°)
    """
    wedges = []
    
    if az_end > az_start:
        for az in range(az_start, az_end, wedge_width):
            wedge_num = (az // wedge_width) + 1
            if wedge_num <= 180:
                wedges.append(wedge_num)
    else:
        # Wraparound case
        for az in range(az_start, 360, wedge_width):
            wedge_num = (az // wedge_width) + 1
            if wedge_num <= 180:
                wedges.append(wedge_num)
        for az in range(0, az_end, wedge_width):
            wedge_num = (az // wedge_width) + 1
            if wedge_num <= 180:
                wedges.append(wedge_num)
    
    return sorted(list(set(wedges)))


def find_sim_directories(exp_path, wedge_numbers):
    """Find simulation directories corresponding to wedge numbers."""
    sim_dirs = []
    
    for wedge_num in wedge_numbers:
        pattern = f"sim_{wedge_num:05d}_*"
        matches = list(exp_path.glob(pattern))
        
        if matches:
            sim_dirs.append(matches[0])
        else:
            print(f"  ⚠ Warning: Could not find directory for wedge {wedge_num}")
    
    return sim_dirs


def process_signals(data_array, taper_percent=0.05):
    """Detrend and taper signals."""
    detrended_data = detrend(data_array)
    taper_window = windows.tukey(len(detrended_data), alpha=taper_percent)
    tapered_data = detrended_data * taper_window
    return tapered_data


def compute_cross_power_spectrum(r1, r2, delta):
    """
    Compute cross-power spectrum (matches MATLAB).

    Returns:
        freqs_pos: Positive frequencies only
        cross_power_full: Full cross-power spectrum (all frequencies)
        cross_power_pos: Cross-power spectrum (positive frequencies only)
        coherence_pos: Coherence (positive frequencies only)
    """
    # Compute FFTs
    fft_r1 = np.fft.fft(r1)
    fft_r2 = np.fft.fft(r2)

    # Cross-power spectrum = FFT(R1) * conj(FFT(R2))
    cross_power_full = fft_r1 * np.conj(fft_r2)

    # Coherence (normalized cross-power)
    den = np.abs(fft_r1) * np.abs(fft_r2)
    coherence_full = np.zeros_like(cross_power_full, dtype=np.complex128)
    mask = den > 0
    coherence_full[mask] = cross_power_full[mask] / den[mask]

    # Frequency axis (positive frequencies only for plotting)
    freqs = np.fft.fftfreq(len(r1), d=delta)
    pos_mask = freqs > 0
    freqs_pos = freqs[pos_mask]
    cross_power_pos = cross_power_full[pos_mask]
    coherence_pos = coherence_full[pos_mask]

    return freqs_pos, cross_power_full, cross_power_pos, coherence_pos


def bandpass_filter_freq(fft_data, freq_range, dt):
    """Apply Tukey (cosine taper) bandpass filter in frequency domain."""
    n = len(fft_data)
    freqs = np.fft.fftfreq(n, dt)

    # Create filter
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
    """Apply cosine taper to edges of time series."""
    n = len(data)
    taper_len = int(n * taper_fraction)
    taper = np.ones(n)

    # Taper at beginning
    taper[:taper_len] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len) / taper_len))

    # Taper at end
    taper[-taper_len:] = 0.5 * (1 - np.cos(np.pi * np.arange(taper_len, 0, -1) / taper_len))

    return data * taper


def compute_time_ccf(cross_power_avg, dt):
    """
    Compute time-domain CCF from frequency-domain cross-power.
    EXACTLY matches compute_ccf_final.py workflow.
    """
    N = len(cross_power_avg)

    # 1. IFFT to time domain
    ccf_ifft = np.fft.ifft(cross_power_avg, N)
    ccf_ifft = ccf_ifft.real

    # 2. fftshift (center zero lag)
    ccf_ifft = np.fft.fftshift(ccf_ifft)

    # 3. Detrend
    ccf_ifft = detrend(ccf_ifft)

    # 4. Cosine taper
    ccf_ifft = cosine_taper(ccf_ifft)

    # 5. Shift back, FFT, apply filter
    ccf_fft = np.fft.fft(np.fft.fftshift(ccf_ifft))
    ccf_filtered = bandpass_filter_freq(ccf_fft, FILTER_FREQ_RANGE, dt)

    # 6. IFFT back to time domain, shift
    ccf_ifft_final = np.fft.ifft(ccf_filtered)
    ccf_ifft_final = np.fft.fftshift(ccf_ifft_final.real)

    # 7. Create time axis
    time = (np.arange(N) - np.floor(N/2)) * dt
    lags = time

    return ccf_ifft_final, lags


def stack_and_compute_ccf(sim_dirs):
    """
    Stack waveforms and compute CCF + coherence using EXACT method from compute_ccf_final.py
    
    Args:
        sim_dirs: List of simulation directory paths
    
    Returns:
        ccf: Cross-correlation function (lag_time, correlation)
        coherence_data: Coherence (frequency, smoothed_coherence)
        r1_stack: Stacked R1 waveform (time, amplitude)
        r2_stack: Stacked R2 waveform (time, amplitude)
        count: Number of wedges processed
    """
    # Storage for stacking
    wedges_r1_ts = []
    wedges_r2_ts = []
    wedges_cross_power_full = []
    wedges_coherence_pos = []
    wedges_freq_axis = []
    
    count = 0
    
    for sim_dir in sim_dirs:
        # Find Z component files
        r1_files = list(sim_dir.glob('WAVE_SIM_*_R1_Z.txt'))
        r2_files = list(sim_dir.glob('WAVE_SIM_*_R2_Z.txt'))
        
        if not r1_files or not r2_files:
            continue
        
        # Load waveforms
        r1 = np.loadtxt(r1_files[0])
        r2 = np.loadtxt(r2_files[0])
        
        # Process signals (detrend + taper)
        r1_processed = process_signals(r1)
        r2_processed = process_signals(r2)
        
        # Store for time-domain stacking
        wedges_r1_ts.append(r1_processed)
        wedges_r2_ts.append(r2_processed)
        
        # Compute cross-power spectrum and coherence
        freqs, cross_power_full, cross_power_pos, coherence_pos = compute_cross_power_spectrum(
            r1_processed, r2_processed, DELTA)
        
        wedges_cross_power_full.append(cross_power_full)
        wedges_coherence_pos.append(coherence_pos)
        wedges_freq_axis.append(freqs)
        count += 1
    
    if count == 0:
        return None, None, None, None, 0
    
    # Stack time series (mean)
    stacked_r1 = np.mean(wedges_r1_ts, axis=0)
    stacked_r2 = np.mean(wedges_r2_ts, axis=0)
    
    # Stack cross-power (for time CCF)
    cross_power_avg = np.mean(wedges_cross_power_full, axis=0)
    
    # Stack coherence (for frequency plot)
    coherence_avg_pos = np.mean(wedges_coherence_pos, axis=0)
    coherence_smoothed = moving_average_numpy(np.real(coherence_avg_pos), SMOOTH_WINDOW)
    
    # Frequency axis
    freqs = wedges_freq_axis[0]
    
    # Compute time-domain CCF
    time_ccf, lags = compute_time_ccf(cross_power_avg, DELTA)
    
    # Create time axis for stacked waveforms
    time_axis = np.arange(len(stacked_r1)) * DELTA
    
    # Format outputs
    r1_stack = np.column_stack([time_axis, stacked_r1])
    r2_stack = np.column_stack([time_axis, stacked_r2])
    ccf = np.column_stack([lags, time_ccf])
    coherence_data = np.column_stack([freqs, coherence_smoothed])
    
    return ccf, coherence_data, r1_stack, r2_stack, count


def extract_eigenfunction_files(source_folder, target_dir):
    """
    Extract eigenfunction and dispersion files from source folder to target directory.
    Returns: (extracted_count, missing_count)
    """
    files_to_extract = [
        'model.d',
        'sregn96.egn',
        'slegn96.egn',
        'sdisp96.ray',
        'sdisp96.lov',
        'dfile',
        'SREGN.ASC',
        'SLEGN.ASC',
        'SDISPR.ASC',
        'SDISPL.ASC',
    ]

    extracted = []
    missing = []

    for fname in files_to_extract:
        src = source_folder / fname
        dst = target_dir / fname

        if src.exists():
            shutil.copy2(src, dst)
            extracted.append(fname)
        else:
            missing.append(fname)

    return len(extracted), len(missing)


def generate_ascii_dispersion(final_dir):
    """
    Generate SREGN.ASC and SLEGN.ASC files using sdpegn96.
    Requires: sregn96.egn and slegn96.egn files in final_dir
    Returns: (rayleigh_success, love_success)
    """
    rayleigh_success = False
    love_success = False
    
    # Change to final directory for sdpegn96 execution
    original_dir = os.getcwd()
    
    try:
        os.chdir(final_dir)
        
        # Generate Rayleigh wave dispersion (SREGN.ASC)
        if os.path.exists('sregn96.egn'):
            print(f"     Generating SREGN.ASC (Rayleigh)...")
            try:
                # Load CPS module and run sdpegn96 in a shell
                cmd = 'module load CPS && sdpegn96 -R -U -ASC'
                result = subprocess.run(
                    cmd,
                    shell=True,
                    executable='/bin/bash',
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0 and os.path.exists('SREGN.ASC'):
                    rayleigh_success = True
                    print(f"      SREGN.ASC generated")
                else:
                    print(f"        Failed to generate SREGN.ASC")
                    if result.stderr:
                        print(f"       Error: {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"        Timeout generating SREGN.ASC")
            except Exception as e:
                print(f"        Error: {e}")
        else:
            print(f"      sregn96.egn not found, skipping SREGN.ASC generation")
        
        # Generate Love wave dispersion (SLEGN.ASC)
        if os.path.exists('slegn96.egn'):
            print(f"     Generating SLEGN.ASC (Love)...")
            try:
                # Load CPS module and run sdpegn96 in a shell
                cmd = 'module load CPS && sdpegn96 -L -U -ASC'
                result = subprocess.run(
                    cmd,
                    shell=True,
                    executable='/bin/bash',
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0 and os.path.exists('SLEGN.ASC'):
                    love_success = True
                    print(f"      SLEGN.ASC generated")
                else:
                    print(f"        Failed to generate SLEGN.ASC")
                    if result.stderr:
                        print(f"       Error: {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"        Timeout generating SLEGN.ASC")
            except Exception as e:
                print(f"        Error: {e}")
        else:
            print(f"      slegn96.egn not found, skipping SLEGN.ASC generation")
    
    finally:
        # Always return to original directory
        os.chdir(original_dir)
    
    return rayleigh_success, love_success


def concatenate_sources(sim_dirs, output_file):
    """
    Concatenate all SOURCES_*.csv files from selected simulations.
    """
    all_sources = []
    
    for sim_dir in sim_dirs:
        source_files = list(sim_dir.glob("SOURCES_*.csv"))
        
        for src_file in source_files:
            try:
                with open(src_file, 'r') as f:
                    lines = f.readlines()
                
                # Find the header line
                header_line = None
                data_start = 0
                for i, line in enumerate(lines):
                    if line.startswith('#') and ('x_km' in line or 'y_km' in line):
                        header_line = line.lstrip('#').strip()
                        data_start = i + 1
                        break
                
                if header_line is None:
                    header_line = 'x_km,y_km'
                    for i, line in enumerate(lines):
                        if not line.startswith('#') and line.strip():
                            data_start = i
                            break
                
                from io import StringIO
                data_lines = ''.join(lines[data_start:])
                df = pd.read_csv(StringIO(data_lines), names=header_line.split(','))
                
                all_sources.append(df)
            except Exception as e:
                print(f"  ⚠ Warning: Could not load {src_file}: {e}")
    
    if len(all_sources) == 0:
        return None
    
    combined = pd.concat(all_sources, ignore_index=True)
    combined.to_csv(output_file, index=False)
    
    return combined


def save_outputs(case_dir, r1_stack, r2_stack, ccf, coherence_data, wedge_numbers, sim_dirs):
    """Save all outputs for this case including coherence."""
    case_dir.mkdir(parents=True, exist_ok=True)
    
    # Save stacked waveforms
    if r1_stack is not None:
        np.savetxt(case_dir / "stacked_r1_waveform.txt", r1_stack, 
                   header="Time(s) Amplitude", fmt="%.6e")
    if r2_stack is not None:
        np.savetxt(case_dir / "stacked_r2_waveform.txt", r2_stack,
                   header="Time(s) Amplitude", fmt="%.6e")
    
    # Save CCF
    if ccf is not None:
        np.savetxt(case_dir / "stacked_time_ccf.txt", ccf,
                   header="Lag_Time(s) Correlation", fmt="%.6e")
    
    # Save coherence
    if coherence_data is not None:
        np.savetxt(case_dir / "stacked_coherence_freq.txt", coherence_data,
                   header="Frequency(Hz) Coherence_Smoothed", fmt="%.6e")
    
    # Save wedge list
    with open(case_dir / "wedge_list.txt", 'w') as f:
        f.write(f"# Wedges used: {len(wedge_numbers)}\n")
        f.write(f"# Wedge numbers:\n")
        for wedge in wedge_numbers:
            f.write(f"{wedge}\n")
    
    # Concatenate and save sources
    sources_file = case_dir / "all_sources.csv"
    sources_df = concatenate_sources(sim_dirs, sources_file)
    
    # Extract eigenfunction files from first simulation
    if sim_dirs:
        print(f"  Extracting eigenfunction files...")
        extracted, missing = extract_eigenfunction_files(sim_dirs[0], case_dir)
        print(f"    Extracted {extracted} files")
        if missing > 0:
            print(f"    {missing} files not found in source")
        
        # Generate ASCII dispersion files if needed
        print(f"  Generating ASCII dispersion files...")
        rayleigh_ok, love_ok = generate_ascii_dispersion(case_dir)
        
        if rayleigh_ok and love_ok:
            print(f"   Both SREGN.ASC and SLEGN.ASC generated")
        elif rayleigh_ok:
            print(f"   SREGN.ASC generated (SLEGN.ASC failed or already exists)")
        elif love_ok:
            print(f"   SLEGN.ASC generated (SREGN.ASC failed or already exists)")
        else:
            # Check if they already existed
            if (case_dir / 'SREGN.ASC').exists():
                print(f"   SREGN.ASC already present")
            if (case_dir / 'SLEGN.ASC').exists():
                print(f"   SLEGN.ASC already present")
    
    return sources_df


def plot_source_ccf_coherence(sources_df, ccf, coherence_data, case_info, output_file, receiver_distance_km=50):
    """
    Create 3-panel plot: source distribution (top) and CCF + Coherence (bottom side-by-side).
    
    Args:
        sources_df: DataFrame with source locations
        ccf: CCF array (lag_time, correlation)
        coherence_data: Coherence array (frequency, coherence)
        case_info: Dictionary with case metadata
        output_file: Where to save plot
        receiver_distance_km: Distance between receivers in km
    """
    fig = plt.figure(figsize=(16, 10))
    
    # Create grid: top row spans full width, bottom row has 2 columns
    gs = fig.add_gridspec(2, 2, height_ratios=[2, 1.2], hspace=0.35, wspace=0.3)
    
    # ============= TOP PANEL: SOURCE DISTRIBUTION (spans both columns) =============
    ax_sources = fig.add_subplot(gs[0, :])
    
    if sources_df is not None and len(sources_df) > 0:
        if 'x_km' in sources_df.columns and 'y_km' in sources_df.columns:
            x_km = sources_df['x_km']
            y_km = sources_df['y_km']
            
            # Subsample for plotting
            n_sources = len(x_km)
            max_plot_points = 10000
            if n_sources > max_plot_points:
                import random
                indices = random.sample(range(n_sources), max_plot_points)
                x_km = x_km.iloc[indices]
                y_km = y_km.iloc[indices]
                n_plot = max_plot_points
            else:
                n_plot = n_sources
            
            # Plot sources
            ax_sources.scatter(x_km, y_km, s=1.5, c='black', alpha=0.5, 
                             edgecolors='none', rasterized=True)
            
            # Plot receivers
            ax_sources.plot(0, 0, 'rv', markersize=10, 
                           markeredgecolor='black', markeredgewidth=1)
            ax_sources.plot(receiver_distance_km, 0, 'rv', markersize=10,
                           markeredgecolor='black', markeredgewidth=1)
            ax_sources.plot([0, receiver_distance_km], [0, 0], 'k--', 
                           linewidth=1.5, alpha=0.7)
            
            # Set limits
            max_extent = max(abs(x_km).max(), abs(y_km).max()) * 1.1
            ax_sources.set_xlim(-max_extent, max_extent)
            ax_sources.set_ylim(-max_extent, max_extent)
            ax_sources.set_aspect('equal')
            
            ax_sources.set_xlabel('X (km)', fontsize=12)
            ax_sources.set_ylabel('Y (km)', fontsize=12)
            title_text = (f"Case {case_info['id']}: {case_info['description']}\n"
                         f"Azimuth: {case_info['az_start']}-{case_info['az_end']}° | Sources: {n_sources:,}")
            if n_plot < n_sources:
                title_text += f" (showing {n_plot:,})"
            ax_sources.set_title(title_text, fontsize=13, fontweight='bold')
            ax_sources.grid(True, alpha=0.3)
        else:
            ax_sources.text(0.5, 0.5, 'Source data format not recognized',
                           ha='center', va='center', transform=ax_sources.transAxes)
    else:
        ax_sources.text(0.5, 0.5, 'No source data available',
                       ha='center', va='center', transform=ax_sources.transAxes)
    
    # ============= BOTTOM LEFT: TIME CCF =============
    ax_ccf = fig.add_subplot(gs[1, 0])
    
    if ccf is not None:
        ax_ccf.plot(ccf[:, 0], ccf[:, 1], 'k-', linewidth=0.8)
        ax_ccf.axvline(0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax_ccf.axhline(0, color='gray', linestyle='-', linewidth=0.5, alpha=0.3)
        
        ax_ccf.set_xlabel('Lag Time (s)', fontsize=11)
        ax_ccf.set_ylabel('Normalized CCF', fontsize=11)
        ax_ccf.set_title('Time-Domain Cross-Correlation', fontsize=12, fontweight='bold')
        ax_ccf.grid(True, alpha=0.3)
        ax_ccf.set_xlim([-100, 100])
    else:
        ax_ccf.text(0.5, 0.5, 'CCF computation failed',
                   ha='center', va='center', transform=ax_ccf.transAxes)
    
    # ============= BOTTOM RIGHT: FREQUENCY COHERENCE =============
    ax_coh = fig.add_subplot(gs[1, 1])
    
    if coherence_data is not None:
        ax_coh.plot(coherence_data[:, 0], coherence_data[:, 1], 'b-', linewidth=1.2)
        
        ax_coh.set_xlabel('Frequency (Hz)', fontsize=11)
        ax_coh.set_ylabel('Coherence', fontsize=11)
        ax_coh.set_title(f'Frequency-Domain Coherence (smoothed, window={SMOOTH_WINDOW})',
                        fontsize=12, fontweight='bold')
        ax_coh.grid(True, alpha=0.3)
        ax_coh.set_xlim([0, 0.5])
        # No ylim - auto-scale to show full range including negatives (matches compute_ccf_final.py)
    else:
        ax_coh.text(0.5, 0.5, 'Coherence computation failed',
                   ha='center', va='center', transform=ax_coh.transAxes)
    
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_azimuthal_coverage_enhanced.py <experiment_number>")
        print("Example: python analyze_azimuthal_coverage_enhanced.py 5")
        sys.exit(1)
    
    exp_num = int(sys.argv[1])
    
    print("=" * 70)
    print(f"AZIMUTHAL COVERAGE ANALYSIS (ENHANCED) - EXPERIMENT {exp_num}")
    print("=" * 70)
    print()
    
    # Find experiment directory
    exp_base = Path(f"experiments/experiment_{exp_num}/outputs")
    if not exp_base.exists():
        print(f" Error: Experiment directory not found: {exp_base}")
        sys.exit(1)
    
    # Create analysis output directory
    analysis_dir = exp_base / "azimuthal_coverage_analysis"
    analysis_dir.mkdir(exist_ok=True)
    print(f"Output directory: {analysis_dir}")
    print(f"Coherence smoothing: window={SMOOTH_WINDOW}")
    print(f"Frequency range: 0-0.5 Hz")
    print()
    
    # Process each case
    for case in CASES:
        print(f"Processing Case {case['id']}: {case['name']}")
        print(f"  Description: {case['description']}")
        print(f"  Azimuth range: {case['az_start']}-{case['az_end']}°")
        
        # Get wedge numbers
        wedge_numbers = get_wedge_numbers(case['az_start'], case['az_end'])
        print(f"  Wedges to include: {len(wedge_numbers)}")
        
        # Find simulation directories
        sim_dirs = find_sim_directories(exp_base, wedge_numbers)
        print(f"  Found simulation directories: {len(sim_dirs)}")
        
        if len(sim_dirs) == 0:
            print(f"   No simulation directories found, skipping case")
            print()
            continue
        
        # Stack waveforms and compute CCF + coherence
        print(f"  Stacking waveforms and computing CCF + coherence...")
        ccf, coherence_data, r1_stack, r2_stack, count = stack_and_compute_ccf(sim_dirs)
        
        if count > 0:
            print(f"    Processed {count} wedges")
        else:
            print(f"     No waveform files found")
        
        if ccf is None:
            print(f"     CCF computation failed")
            print()
            continue
        
        # Create output directory for this case
        case_dirname = f"case_{case['id']}_{case['name']}_az{case['az_start']:03d}-{case['az_end']:03d}"
        case_dir = analysis_dir / case_dirname
        
        # Save outputs (including coherence and eigenfunction files)
        sources_df = save_outputs(case_dir, r1_stack, r2_stack, ccf, coherence_data, 
                                 wedge_numbers, sim_dirs)
        if sources_df is not None:
            print(f" Concatenated {len(sources_df):,} sources")
        print(f" Saved to: {case_dirname}/")
        
        # Create 3-panel plot (sources + CCF + coherence)
        plot_file = case_dir / "sources_ccf_coherence.png"
        plot_source_ccf_coherence(sources_df, ccf, coherence_data, case, plot_file)
        print(f" Plot saved: sources_ccf_coherence.png")
        
        print()
    

if __name__ == "__main__":
    main()