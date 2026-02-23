#!/usr/bin/env python3
"""
Compute stacked cross-correlations for ambient noise simulations.
Groups simulations by distance and radius parameters.
Extracts eigenfunction files for complete groups (360° coverage).
Automatically generates SREGN.ASC and SLEGN.ASC dispersion files.

Usage:
    python compute_ccf.py <output_directory>

Example:
    python compute_ccf.py experiment_1_point_forces/output_10wedges
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy import signal
from scipy.signal import windows, detrend
import os
import sys
import glob
import re
import shutil
import subprocess
from collections import defaultdict

# ============================================================================
# PARAMETERS
# ============================================================================
DELTA = 0.5  # Sampling interval (seconds)
FILTER_PERIOD_RANGE = [2.0, 100.0]  # Period range (seconds): [min, max]
FILTER_FREQ_RANGE = [1.0/FILTER_PERIOD_RANGE[1], 1.0/FILTER_PERIOD_RANGE[0]]  # [0.01, 0.5] Hz
TAPER_WIDTH = 0.2  # Cosine taper width (costap_wid in MATLAB)
LAG_TIME_MAX = 250.0  # Maximum lag time for plotting (seconds)
SMOOTH_WINDOW = 10  # Smoothing window size (smooth_val in MATLAB)

# ============================================================================
# GROUPING FUNCTIONS
# ============================================================================

def parse_folder_params(folder_name):
    """
    Parse parameters from folder name.
    Example: sim_00001_ang_0_36_dist_200_rad_150_1000_tak135sph
    Returns: (theta_min, theta_max, distance, r_min, r_max, model_name)
    """
    # Parse angle range
    angle_match = re.search(r'_ang_(\d+)_(\d+)_', folder_name)
    theta_min = int(angle_match.group(1)) if angle_match else None
    theta_max = int(angle_match.group(2)) if angle_match else None

    # Parse distance
    dist_match = re.search(r'_dist_(\d+)_', folder_name)
    distance = int(dist_match.group(1)) if dist_match else None

    # Parse radius range
    rad_match = re.search(r'_rad_(\d+)_(\d+)_', folder_name)
    r_min = int(rad_match.group(1)) if rad_match else None
    r_max = int(rad_match.group(2)) if rad_match else None

    # Parse model name
    model_match = re.search(r'_(\w+)$', folder_name)
    model_name = model_match.group(1) if model_match else None

    return theta_min, theta_max, distance, r_min, r_max, model_name


def group_simulations(output_dir):
    """
    Group simulation folders by (distance, r_min, r_max).
    Returns: dict with key=(distance, r_min, r_max), value=list of (folder, theta_min, theta_max)
    """
    sim_folders = sorted(glob.glob(os.path.join(output_dir, 'sim_*')))

    groups = defaultdict(list)

    for folder in sim_folders:
        folder_name = os.path.basename(folder)
        theta_min, theta_max, distance, r_min, r_max, model_name = parse_folder_params(folder_name)

        if None not in [theta_min, theta_max, distance, r_min, r_max]:
            key = (distance, r_min, r_max)
            groups[key].append((folder, theta_min, theta_max, model_name))

    return groups


def check_group_coverage(group_folders):
    """
    Check if a group has 360° coverage.
    Returns: (is_complete, total_coverage, num_folders, first_folder)
    """
    if len(group_folders) == 0:
        return False, 0, 0, None

    total_coverage = 0
    for folder, theta_min, theta_max, model_name in group_folders:
        angle_width = theta_max - theta_min
        total_coverage += angle_width

    is_complete = (total_coverage == 360)
    num_folders = len(group_folders)
    first_folder = group_folders[0][0] if group_folders else None

    return is_complete, total_coverage, num_folders, first_folder


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
                    print(f"       ✓ SREGN.ASC generated")
                else:
                    print(f"       ✗ Failed to generate SREGN.ASC")
                    if result.stderr:
                        print(f"       Error: {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"       ✗ Timeout generating SREGN.ASC")
            except Exception as e:
                print(f"       ✗ Error: {e}")
        else:
            print(f"     ✗ sregn96.egn not found, skipping SREGN.ASC")
        
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
                    print(f"       ✓ SLEGN.ASC generated")
                else:
                    print(f"       ✗ Failed to generate SLEGN.ASC")
                    if result.stderr:
                        print(f"       Error: {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"       ✗ Timeout generating SLEGN.ASC")
            except Exception as e:
                print(f"       ✗ Error: {e}")
        else:
            print(f"     ✗ slegn96.egn not found, skipping SLEGN.ASC")
    
    finally:
        # Always return to original directory
        os.chdir(original_dir)
    
    return rayleigh_success, love_success


def extract_eigenfunction_files(source_folder, final_dir):
    """
    Extract eigenfunction and dispersion files from source folder to final directory.
    Returns: (extracted_count, missing_count)
    """
    files_to_extract = [
        'model.d',
        'sregn96.egn',
        'slegn96.egn',
        'sdisp96.ray',
        'sdisp96.lov',
        'dfile',
        'SDISPR.ASC',
        'SDISPL.ASC',
        # Note: SREGN.ASC and SLEGN.ASC will be generated by sdpegn96
    ]

    extracted = []
    missing = []

    for fname in files_to_extract:
        src = os.path.join(source_folder, fname)
        dst = os.path.join(final_dir, fname)

        if os.path.exists(src):
            shutil.copy2(src, dst)
            extracted.append(fname)
        else:
            missing.append(fname)

    # Count only required files as missing
    required_missing = [m for m in missing if m not in ['SDISPR.ASC', 'SDISPL.ASC']]

    return len(extracted), len(required_missing)


# ============================================================================
# CCF COMPUTATION FUNCTIONS
# ============================================================================

def moving_average_numpy(data, window_size):
    """Apply moving average smoothing (matches MATLAB smooth())."""
    kernel = np.ones(window_size) / window_size
    smoothed_data = np.convolve(data, kernel, mode='same')
    return smoothed_data


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
    """
    Apply Tukey (cosine taper) bandpass filter in frequency domain.
    Matches MATLAB tukey_filt().
    """
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
    """Apply cosine taper to edges of time series (matches MATLAB cos_taper())."""
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
    EXACTLY matches MATLAB workflow.
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

    # 7. Create time axis (EXACTLY matching MATLAB)
    time = (np.arange(N) - np.floor(N/2)) * dt
    lags = time  # Standard fftshift time axis

    return ccf_ifft_final, lags


def plot_timeseries(r1_stack, r2_stack, output_dir):
    """Plot stacked time series (R1 top, R2 bottom)."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))

    time_axis = np.arange(len(r1_stack)) * DELTA

    # Top panel: R1
    ax1.plot(time_axis, r1_stack, 'b-', linewidth=0.8)
    ax1.set_ylabel('Amplitude', fontsize=14)
    ax1.set_title('Stacked R1 Waveform', fontsize=16, fontweight='bold')
    ax1.grid(True, alpha=0.3)

    # Bottom panel: R2
    ax2.plot(time_axis, r2_stack, 'g-', linewidth=0.8)
    ax2.set_xlabel('Time (s)', fontsize=14)
    ax2.set_ylabel('Amplitude', fontsize=14)
    ax2.set_title('Stacked R2 Waveform', fontsize=16, fontweight='bold')
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plot_file = os.path.join(output_dir, 'stacked_timeseries.png')
    plt.savefig(plot_file, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"     Timeseries plot saved")


def plot_coherence(freqs, coherence_smoothed, output_dir):
    """Plot frequency-domain coherence."""
    fig, ax = plt.subplots(figsize=(12, 8))

    ax.plot(freqs, coherence_smoothed, 'b-', linewidth=2)
    ax.set_xlabel('Frequency (Hz)', fontsize=14)
    ax.set_ylabel('Coherence', fontsize=14)
    ax.set_title(f'Stacked Coherence (smoothed, window={SMOOTH_WINDOW})',
                 fontsize=16, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.set_xlim([0, 0.5])

    plt.tight_layout()
    plot_file = os.path.join(output_dir, 'stacked_coherence_freq.png')
    plt.savefig(plot_file, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"     Coherence plot saved")


def plot_time_ccf(lags, time_ccf, output_dir):
    """Plot time-domain CCF (should be symmetric)."""
    fig, ax = plt.subplots(figsize=(12, 8))

    lag_mask = np.abs(lags) <= LAG_TIME_MAX
    ax.plot(lags[lag_mask], time_ccf[lag_mask], 'r-', linewidth=2)
    ax.set_xlabel('Lag Time (s)', fontsize=14)
    ax.set_ylabel('CCF Amplitude', fontsize=14)
    ax.set_title('Stacked Time-Domain CCF', fontsize=16, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.set_xlim([-LAG_TIME_MAX, LAG_TIME_MAX])
    ax.axvline(x=0, color='k', linestyle='--', alpha=0.5, linewidth=1)

    plt.tight_layout()
    plot_file = os.path.join(output_dir, 'stacked_time_ccf.png')
    plt.savefig(plot_file, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"     Time CCF plot saved")


def process_group(group_folders, final_dir_name, output_dir):
    """Process a single parameter group and compute CCF."""

    print(f"\n  Processing simulations...")

    # Get waveform files from this group only
    group_paths = [folder for folder, _, _, _ in group_folders]

    all_files1 = []
    all_files2 = []
    for folder_path in group_paths:
        r1_file = glob.glob(os.path.join(folder_path, 'WAVE_SIM_*_R1_Z.txt'))
        r2_file = glob.glob(os.path.join(folder_path, 'WAVE_SIM_*_R2_Z.txt'))
        if r1_file and r2_file:
            all_files1.extend(r1_file)
            all_files2.extend(r2_file)

    all_files1 = sorted(all_files1)
    all_files2 = sorted(all_files2)

    if len(all_files1) == 0:
        print("  ERROR: No waveform files found!")
        return False

    # Storage for stacking
    wedges_r1_ts = []
    wedges_r2_ts = []
    wedges_cross_power_full = []
    wedges_coherence_pos = []
    wedges_freq_axis = []

    # Process each simulation
    for i, (f1, f2) in enumerate(zip(all_files1, all_files2), 1):
        # Load waveforms
        r1 = np.loadtxt(f1)
        r2 = np.loadtxt(f2)

        # Process signals (detrend + taper)
        r1_processed = process_signals(r1)
        r2_processed = process_signals(r2)

        # Store for stacking
        wedges_r1_ts.append(r1_processed)
        wedges_r2_ts.append(r2_processed)

        # Compute cross-power spectrum and coherence
        freqs, cross_power_full, cross_power_pos, coherence_pos = compute_cross_power_spectrum(
            r1_processed, r2_processed, DELTA)

        wedges_cross_power_full.append(cross_power_full)
        wedges_coherence_pos.append(coherence_pos)
        wedges_freq_axis.append(freqs)

    print(f"   Processed {len(all_files1)} wedges")

    # Stack time series (mean)
    stacked_r1 = np.mean(wedges_r1_ts, axis=0)
    stacked_r2 = np.mean(wedges_r2_ts, axis=0)

    # Stack cross-power (for time CCF - matches MATLAB)
    coh_num = len(wedges_cross_power_full)
    coh_sum_full = np.sum(wedges_cross_power_full, axis=0)
    cross_power_avg = coh_sum_full / coh_num

    # Stack coherence (for frequency plot)
    coherence_avg_pos = np.mean(wedges_coherence_pos, axis=0)
    coherence_smoothed = moving_average_numpy(np.real(coherence_avg_pos), SMOOTH_WINDOW)

    # Frequency axis
    freqs = wedges_freq_axis[0]

    # Compute time-domain CCF
    time_ccf, lags = compute_time_ccf(cross_power_avg, DELTA)

    # Create final directory
    final_dir = os.path.join(output_dir, final_dir_name)
    os.makedirs(final_dir, exist_ok=True)

    # Save results
    print(f"\n  Saving CCF results...")

    # 1. Stacked coherence (frequency domain, smoothed)
    coherence_file = os.path.join(final_dir, 'stacked_coherence_freq.txt')
    np.savetxt(coherence_file, np.column_stack([freqs, coherence_smoothed]),
               fmt='%.6e', header='frequency_Hz coherence_smoothed')

    # 2. Time CCF
    time_ccf_file = os.path.join(final_dir, 'stacked_time_ccf.txt')
    np.savetxt(time_ccf_file, np.column_stack([lags, time_ccf]),
               fmt='%.6e', header='lag_time_s CCF_amplitude')

    # 3. Stacked R1 waveform
    r1_file = os.path.join(final_dir, 'stacked_r1_waveform.txt')
    time_axis = np.arange(len(stacked_r1)) * DELTA
    np.savetxt(r1_file, np.column_stack([time_axis, stacked_r1]),
               fmt='%.6e', header='time_s amplitude')

    # 4. Stacked R2 waveform
    r2_file = os.path.join(final_dir, 'stacked_r2_waveform.txt')
    np.savetxt(r2_file, np.column_stack([time_axis, stacked_r2]),
               fmt='%.6e', header='time_s amplitude')

    print(f"     Saved 4 data files")

    # Generate plots
    print(f"\n  Generating plots...")
    plot_timeseries(stacked_r1, stacked_r2, final_dir)
    plot_coherence(freqs, coherence_smoothed, final_dir)
    plot_time_ccf(lags, time_ccf, final_dir)

    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python compute_ccf_final.py <output_directory>")
        print("Example: python compute_ccf_final.py experiment_1_point_forces/output_10wedges")
        sys.exit(1)

    output_dir = sys.argv[1]

    if not os.path.isdir(output_dir):
        print(f"ERROR: Directory not found: {output_dir}")
        sys.exit(1)

    print("="*70)
    print("CCF ANALYSIS + EIGENFUNCTION EXTRACTION")
    print("="*70)
    print(f"Output directory: {output_dir}")
    print(f"Sampling interval: {DELTA} s")
    print(f"Filter range: {FILTER_FREQ_RANGE[0]:.3f} - {FILTER_FREQ_RANGE[1]:.3f} Hz")
    print(f"Period range: {FILTER_PERIOD_RANGE[0]:.1f} - {FILTER_PERIOD_RANGE[1]:.1f} s")
    print(f"Smoothing window: {SMOOTH_WINDOW} points")
    print("="*70)

    # Group simulations by parameters
    groups = group_simulations(output_dir)

    if len(groups) == 0:
        print("\nERROR: No simulation folders found!")
        sys.exit(1)

    print(f"\nFound {len(groups)} parameter group(s)")

    # Process each group
    complete_groups = 0
    incomplete_groups = 0

    for (distance, r_min, r_max), group_folders in sorted(groups.items()):
        print(f"\nGROUP: dist={distance} km, radius=[{r_min}, {r_max}] km")

        is_complete, total_coverage, num_folders, first_folder = check_group_coverage(group_folders)

        print(f"Folders: {num_folders}")
        print(f"Coverage: {total_coverage}°")
        print(f"Complete: {'YES' if is_complete else 'NO'}")

        if is_complete:
            complete_groups += 1

            # Create final directory name
            final_dir_name = f"final_dist_{distance}_rad_{r_min}_{r_max}"
            final_dir = os.path.join(output_dir, final_dir_name)

            print(f"\n✓ Processing complete group")
            print(f"  → Destination: {final_dir_name}")

            # Process CCF
            success = process_group(group_folders, final_dir_name, output_dir)

            if success:
                # Extract eigenfunction files
                print(f"\n  Extracting eigenfunction files...")
                print(f"  From: {os.path.basename(first_folder)}")

                extracted_count, missing_count = extract_eigenfunction_files(first_folder, final_dir)

                print(f"     Extracted {extracted_count} files")
                if missing_count > 0:
                    print(f"     {missing_count} required files missing")

                # Generate ASCII dispersion files using sdpegn96
                print(f"\n  Generating dispersion ASCII files...")
                rayleigh_ok, love_ok = generate_ascii_dispersion(final_dir)
                
                if rayleigh_ok and love_ok:
                    print(f"     ✓ Both dispersion files generated")
                elif rayleigh_ok:
                    print(f"     ⚠ Only SREGN.ASC generated")
                elif love_ok:
                    print(f"     ⚠ Only SLEGN.ASC generated")
                else:
                    print(f"     ✗ Failed to generate dispersion files")

                print(f"\n  ✓ GROUP COMPLETE: {final_dir_name}")
        else:
            incomplete_groups += 1
            print(f"\n✗ Incomplete ({total_coverage}° / 360°) - skipping")

    # Summary
    print("\n" + "="*70)
    print("SUMMARY:")
    print("="*70)
    print(f"Total groups: {len(groups)}")
    print(f"Complete (360°): {complete_groups}")
    print(f"Incomplete: {incomplete_groups}")

    if complete_groups > 0:
        print(f"\n✓ Processed {complete_groups} complete group(s)")
        print(f"✓ CCF results + eigenfunction files + ASCII dispersion saved")
    
    print("="*70)


if __name__ == '__main__':
    main()
