#!/usr/bin/env python3
"""
FTAN Analysis
- pycwt for wavelet transform (dj=1/12, s0=-1, J=-1, morlet)
- Period range: 1-20s (fmin=0.05 Hz, fmax=1 Hz)
- Velocity range: 0.5-4.5 km/s
- Symmetric CCF (average of positive and negative lags)
- scipy.interpolate.interp2d for velocity mapping
- Per-period normalization
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


class FTAN_NoisePy:
    def __init__(self, ccf_file, distance_km, sregn_file, output_dir, 
                 case_name, model_name, az_range, rad_range):
        self.ccf_file = ccf_file
        self.distance_km = distance_km
        self.sregn_file = sregn_file
        self.output_dir = output_dir
        self.case_name = case_name
        self.model_name = model_name
        self.az_range = az_range
        self.rad_range = rad_range
        self.dt = 0.5

        # Load CCF
        data = np.loadtxt(ccf_file)
        self.lags = data[:, 0]
        self.ccf = data[:, 1]

        # Load theoretical dispersion
        self.load_theoretical_dispersion(sregn_file)

    def load_theoretical_dispersion(self, sregn_file):
        """Load theoretical dispersion from SREGN.ASC file"""
        data = np.loadtxt(sregn_file, skiprows=1)
        mode0_mask = data[:, 0] == 0
        self.theory_periods = data[mode0_mask, 2]
        self.theory_gvel = data[mode0_mask, 5]

    def compute_ftan(self, fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5):
        """
        Compute FTAN
        
        Parameters:
        -----------
        fmin : float
            Minimum frequency (Hz) - default 0.05 Hz (20s period)
        fmax : float
            Maximum frequency (Hz) - default 1.0 Hz (1s period)
        vmin : float
            Minimum velocity (km/s) - default 0.5
        vmax : float
            Maximum velocity (km/s) - default 4.5
        """
        print(f"  Computing FTAN: freq {fmin}-{fmax} Hz, vel {vmin}-{vmax} km/s")
        
        # Create symmetric CCF (average of positive and negative lags)
        npts = len(self.ccf)
        indx = npts // 2
        
        # Symmetric: average of positive and negative lags
        data = 0.5 * self.ccf[indx:] + 0.5 * np.flip(self.ccf[:indx + 1], axis=0)
        
        # Trim data according to velocity window
        pt1 = int(self.distance_km / vmax / self.dt)
        pt2 = int(self.distance_km / vmin / self.dt)
        
        if pt1 == 0:
            pt1 = 10
        if pt2 > (npts // 2):
            pt2 = npts // 2
            
        indx = np.arange(pt1, pt2)
        tvec = indx * self.dt
        data = data[indx]
        
        print(f"  Trimmed data: {len(data)} points, time range {tvec[0]:.1f}-{tvec[-1]:.1f}s")
        
        # Wavelet transformation using pycwt (NoisePy parameters)
        dj = 1/24      # Scale resolution - FINER for better detail
        s0 = -1        # Starting scale (automatic)
        J = -1         # Number of scales (automatic)
        wvn = 'morlet' # Wavelet type
        
        print(f"  Running pycwt.cwt with dj={dj}, s0={s0}, J={J}, wavelet={wvn}")
        cwt, sj, freq, coi, _, _ = pycwt.cwt(data, self.dt, dj, s0, J, wvn)
        
        print(f"  CWT complete: {len(freq)} frequencies from {freq.min():.3f} to {freq.max():.3f} Hz")
        
        # Filter to frequency range
        if (fmax > np.max(freq)) or (fmax <= fmin):
            raise ValueError(f"Frequency out of limits! freq range: {freq.min():.3f}-{freq.max():.3f} Hz")
        
        freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
        cwt = cwt[freq_ind]
        freq = freq[freq_ind]
        
        print(f"  Filtered to {len(freq)} frequencies: {freq.min():.3f}-{freq.max():.3f} Hz")
        
        # Convert to period and amplitude
        period = 1 / freq
        rcwt = np.abs(cwt) ** 2  # Power spectrum
        
        print(f"  Period range: {period.min():.1f}-{period.max():.1f}s")
        
        # Create period and velocity grids for interpolation
        per = np.arange(int(1/fmax), int(1/fmin), 0.25)  # Period grid - FINER 0.25s spacing
        vel = np.arange(vmin, vmax, 0.01)                # Velocity grid - FINER 0.01 km/s spacing
        
        print(f"  Interpolation grids: {len(per)} periods, {len(vel)} velocities")
        
        # Interpolate to velocity-period grid using scipy.interpolate.interp2d
        # Map from (velocity=dist/tvec, period) to regular (vel, per) grid
        velocity_data = self.distance_km / tvec
        
        fc = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
        rcwt_new = fc(vel, per)
        
        # Normalize each period independently (NoisePy approach)
        for ii in range(len(per)):
            max_val = np.max(rcwt_new[ii])
            if max_val > 0:
                rcwt_new[ii] /= max_val
        
        from scipy.ndimage import gaussian_filter1d
        sigma = 0.15  # Very light smoothing
        for j in range(len(vel)):
            rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=sigma)
        
        # Store results
        self.periods = per
        self.velocities = vel
        self.ftan_amp = rcwt_new
        self.tvec = tvec
        self.data = data
        
        print(f"  FTAN complete: {rcwt_new.shape}")
        
        return per, vel, rcwt_new

    def extract_dispersion(self):
        """Extract dispersion curve from FTAN map"""
        
        nper = []
        gv = []
        
        for ii in range(len(self.periods)):
            # Find velocity with maximum amplitude at this period
            max_idx = np.argmax(self.ftan_amp[ii, :])
            max_amp = self.ftan_amp[ii, max_idx]
            
            # Only accept if amplitude is significant (>0.5 after normalization)
            if max_amp > 0.5:
                nper.append(self.periods[ii])
                gv.append(self.velocities[max_idx])
        
        self.periods_picked = np.array(nper)
        self.group_velocities = np.array(gv)
        
        print(f"  Extracted {len(nper)} dispersion points")
        
        # Calculate RMS error
        if len(self.periods_picked) > 0:
            theory_at_picked = np.interp(self.periods_picked, self.theory_periods, self.theory_gvel)
            misfit = np.sqrt(np.mean((self.group_velocities - theory_at_picked)**2))
            rel_misfit = misfit / np.mean(theory_at_picked) * 100
            self.rms_error = rel_misfit
        else:
            self.rms_error = 999.9
        
        print(f"  RMS error: {self.rms_error:.1f}%")
        
        # Save dispersion curve
        dispersion_file = self.output_dir / f'dispersion_{self.model_name}_dist{int(self.distance_km)}_rad{self.rad_range}_az{self.az_range}.txt'
        np.savetxt(dispersion_file,
                  np.column_stack([self.periods_picked, self.group_velocities]),
                  header='period_s group_velocity_km/s', fmt='%.6f')
        
        return self.periods_picked, self.group_velocities

    def plot_ftan(self):
        """Plot FTAN map with theoretical and extracted dispersion"""
        fig = plt.figure(figsize=(14, 8))
        
        # Main FTAN panel
        ax1 = plt.subplot2grid((2, 3), (0, 0), colspan=2, rowspan=2)
        
        # Plot FTAN image
        im = ax1.imshow(np.transpose(self.ftan_amp), 
                       cmap='inferno',  # Colorblind-friendly, publication-quality
                       extent=[self.periods[0], self.periods[-1], 
                              self.velocities[0], self.velocities[-1]],
                       aspect='auto', 
                       origin='lower',
                       vmin=0, vmax=1)
        
        # Overlay theoretical dispersion
        theory_mask = (self.theory_periods >= self.periods[0]) & (self.theory_periods <= self.periods[-1])
        theory_mask &= (self.theory_gvel >= self.velocities[0]) & (self.theory_gvel <= self.velocities[-1])
        ax1.plot(self.theory_periods[theory_mask], self.theory_gvel[theory_mask],
                'c-', linewidth=2.5, label='Theoretical', alpha=0.9)  # Cyan solid line
        
        # Overlay extracted dispersion
        if len(self.periods_picked) > 0:
            ax1.plot(self.periods_picked, self.group_velocities,
                    'c--', linewidth=2.5, label='Extracted', alpha=1.0)  # Cyan dashed line
        
        ax1.set_xlabel('Period [s]', fontsize=14)
        ax1.set_ylabel('Group Velocity [km/s]', fontsize=14)
        ax1.set_title(f'FTAN: {self.model_name} | Az {self.az_range} | {self.distance_km:.0f} km\n'
                     f'Error: {self.rms_error:.1f}%', fontsize=16, fontweight='bold')
        ax1.legend(loc='upper right', fontsize=11)
        
        cbar = plt.colorbar(im, ax=ax1)
        cbar.set_label('Normalized Amplitude', fontsize=12)
        
        # Waveform panel
        ax2 = plt.subplot2grid((2, 3), (0, 2))
        ax2.plot(self.data, self.tvec, 'b-', linewidth=0.8)
        ax2.set_ylabel('Time [s]', fontsize=12)
        ax2.set_xlabel('CCF Amplitude', fontsize=12)
        ax2.set_title('Symmetric CCF', fontsize=12)
        ax2.grid(True, alpha=0.3)
        
        # Dispersion curve panel
        ax3 = plt.subplot2grid((2, 3), (1, 2))
        ax3.plot(self.theory_periods, self.theory_gvel, 'k-', linewidth=2, label='Theory', alpha=0.7)
        if len(self.periods_picked) > 0:
            ax3.plot(self.periods_picked, self.group_velocities, 'ro-', 
                    linewidth=2, markersize=5, label='Extracted')
        ax3.set_xlabel('Period [s]', fontsize=12)
        ax3.set_ylabel('Group Velocity [km/s]', fontsize=12)
        ax3.set_title('Dispersion Curve', fontsize=12)
        ax3.grid(True, alpha=0.3)
        ax3.legend(fontsize=10)
        
        plt.tight_layout()
        
        # Save figure
        outfile = self.output_dir / f'ftan_{self.model_name}_dist{int(self.distance_km)}_rad{self.rad_range}_az{self.az_range}.png'
        plt.savefig(outfile, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"  Saved: {outfile.name}")


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


def main():
    if len(sys.argv) < 2:
        print("Usage: python FTAN_noisepy_approach.py <experiment_number>")
        sys.exit(1)

    exp_num = int(sys.argv[1])
    print(f"FTAN ANALYSIS - EXPERIMENT {exp_num}")
    print()

    exp_base = Path(f"experiments/experiment_{exp_num}/outputs")
    if not exp_base.exists():
        print(f" Error: {exp_base} not found")
        sys.exit(1)

    analysis_dir = exp_base / "azimuthal_coverage_analysis"
    if not analysis_dir.exists():
        print(f" Error: {analysis_dir} not found")
        sys.exit(1)

    ftan_dir = analysis_dir / "ftan_noisepy_enhanced"
    ftan_dir.mkdir(exist_ok=True)
    print(f"Output: {ftan_dir}\n")

    model_name = parse_model_name(exp_base)
    distance_km, rad_range = parse_distance_and_radius(exp_base)

    if not model_name:
        print(" Error: Could not parse model name")
        sys.exit(1)

    print(f"Model: {model_name}")
    print(f"Distance: {distance_km} km")
    print(f"Radius: {rad_range} km\n")

    sregn_file = None
    final_dirs = list(exp_base.glob('final_dist_*'))
    if final_dirs:
        sregn_file = final_dirs[0] / 'SREGN.ASC'
        if not sregn_file.exists():
            sregn_file = None

    if not sregn_file:
        print(" Error: SREGN.ASC not found")
        sys.exit(1)

    print(f"Theory: {sregn_file.name}\n")

    case_dirs = sorted(analysis_dir.glob('case_*'))
    if not case_dirs:
        print(" Error: No cases found")
        sys.exit(1)

    print(f"Processing {len(case_dirs)} cases\n")

    successful = 0
    failed = 0

    for case_dir in case_dirs:
        case_name = case_dir.name
        case_id = case_name.split('_')[1]

        az_match = re.search(r'_az(\d+)-(\d+)', case_name)
        az_range = f"{az_match.group(1)}-{az_match.group(2)}" if az_match else "unknown"

        print(f"Case {case_id} (Az {az_range})...")

        ccf_file = case_dir / 'stacked_time_ccf.txt'
        if not ccf_file.exists():
            print(f"   CCF not found")
            failed += 1
            continue

        try:
            ftan = FTAN_NoisePy(ccf_file, distance_km, sregn_file, ftan_dir, 
                               case_name, model_name, az_range, rad_range)
            
            # Compute FTAN
            ftan.compute_ftan(fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5)
            
            # Extract dispersion
            ftan.extract_dispersion()
            
            # Plot
            ftan.plot_ftan()
            
            print(f"   Complete\n")
            successful += 1
            
        except Exception as e:
            print(f"   Error: {e}\n")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"Total: {len(case_dirs)} | Success: {successful} | Failed: {failed}")
    print(f"\nResults: {ftan_dir}/\n")


if __name__ == "__main__":
    main()