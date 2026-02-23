#!/usr/bin/env python3
"""
Generate pure FTAN heatmaps for ML training - 256x256 RGB format

Outputs:
- Observed FTAN images (256x256 RGB PNG)
- Theoretical FTAN images (256x256 RGB PNG) 
- metadata.csv with data_type column ('observed' or 'theoretical')
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import scipy.interpolate
import re
import csv

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed. Install with: pip install pycwt")
    sys.exit(1)


class FTAN_ML:
    def __init__(self, ccf_file, distance_km, sregn_file, model_name, az_range, rad_range, exp_num):
        self.ccf_file = ccf_file
        self.distance_km = distance_km
        self.sregn_file = sregn_file
        self.model_name = model_name
        self.az_range = az_range
        self.rad_range = rad_range
        self.exp_num = exp_num
        self.dt = 0.5

        data = np.loadtxt(ccf_file)
        self.lags = data[:, 0]
        self.ccf = data[:, 1]

        self.load_theoretical_dispersion(sregn_file)

    def load_theoretical_dispersion(self, sregn_file):
        data = np.loadtxt(sregn_file, skiprows=1)
        mode0_mask = data[:, 0] == 0
        self.theory_periods = data[mode0_mask, 2]
        self.theory_gvel = data[mode0_mask, 5]

    def compute_ftan(self, fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5):
        npts = len(self.ccf)
        indx = npts // 2
        data = 0.5 * self.ccf[indx:] + 0.5 * np.flip(self.ccf[:indx + 1], axis=0)
        
        pt1 = int(self.distance_km / vmax / self.dt)
        pt2 = int(self.distance_km / vmin / self.dt)
        
        if pt1 == 0:
            pt1 = 10
        if pt2 > (npts // 2):
            pt2 = npts // 2
            
        indx = np.arange(pt1, pt2)
        tvec = indx * self.dt
        data = data[indx]
        
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
        
        per = np.arange(int(1/fmax), int(1/fmin), 0.25)
        vel = np.arange(vmin, vmax, 0.01)
        
        velocity_data = self.distance_km / tvec
        fc = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
        rcwt_new = fc(vel, per)
        
        for ii in range(len(per)):
            max_val = np.max(rcwt_new[ii])
            if max_val > 0:
                rcwt_new[ii] /= max_val
        
        from scipy.ndimage import gaussian_filter1d
        sigma = 0.15
        for j in range(len(vel)):
            rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=sigma)
        
        self.periods = per
        self.velocities = vel
        self.ftan_amp = rcwt_new
        
        return per, vel, rcwt_new

    def extract_dispersion(self):
        nper = []
        gv = []
        
        for ii in range(len(self.periods)):
            max_idx = np.argmax(self.ftan_amp[ii, :])
            max_amp = self.ftan_amp[ii, max_idx]
            
            if max_amp > 0.5:
                nper.append(self.periods[ii])
                gv.append(self.velocities[max_idx])
        
        self.periods_picked = np.array(nper)
        self.group_velocities = np.array(gv)
        
        if len(self.periods_picked) > 0:
            theory_at_picked = np.interp(self.periods_picked, self.theory_periods, self.theory_gvel)
            misfit = np.sqrt(np.mean((self.group_velocities - theory_at_picked)**2))
            rel_misfit = misfit / np.mean(theory_at_picked) * 100
            self.rms_error = rel_misfit
        else:
            self.rms_error = 999.9
        
        return self.rms_error

    def save_ml_image(self, output_dir, data_type='observed', img_size=256):
        """Save as 256x256 RGB PNG"""
        fig = plt.figure(figsize=(img_size/100, img_size/100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')
        
        ax.imshow(np.transpose(self.ftan_amp), 
                 cmap='inferno',
                 extent=[self.periods[0], self.periods[-1], 
                        self.velocities[0], self.velocities[-1]],
                 aspect='auto',
                 origin='lower',
                 vmin=0, vmax=1,
                 interpolation='bilinear')
        
        if data_type == 'theoretical':
            filename = f'ftan_theoretical_{self.model_name}_az{self.az_range}_dist{int(self.distance_km)}_rad{self.rad_range}.png'
        else:
            filename = f'ftan_{self.model_name}_az{self.az_range}_dist{int(self.distance_km)}_rad{self.rad_range}.png'
        
        filepath = output_dir / filename
        
        plt.savefig(filepath, dpi=100, bbox_inches='tight', pad_inches=0)
        plt.close()
        
        return filename

    def get_metadata(self, filename, data_type='observed'):
        az_parts = self.az_range.split('-')
        rad_parts = self.rad_range.split('-')
        
        return {
            'filename': filename,
            'data_type': data_type,
            'experiment': self.exp_num,
            'model': self.model_name,
            'azimuth_start': int(az_parts[0]),
            'azimuth_end': int(az_parts[1]),
            'distance_km': self.distance_km,
            'radius_min': int(rad_parts[0]),
            'radius_max': int(rad_parts[1]),
            'error_percent': round(self.rms_error, 2) if data_type == 'observed' else 0.0
        }


def create_theoretical_sharp_map(periods, group_velocities,
                                period_range=(1, 20), velocity_range=(0.5, 4.5),
                                period_spacing=0.25, velocity_spacing=0.01,
                                line_width=5):
    """Create sharp theoretical dispersion map"""
    period_grid = np.arange(period_range[0], period_range[1], period_spacing)
    velocity_grid = np.arange(velocity_range[0], velocity_range[1], velocity_spacing)
    
    amplitude_map = np.zeros((len(period_grid), len(velocity_grid)))
    
    for per, vel in zip(periods, group_velocities):
        if (per < period_range[0] or per >= period_range[1] or 
            vel < velocity_range[0] or vel >= velocity_range[1]):
            continue
        
        per_idx = np.argmin(np.abs(period_grid - per))
        vel_idx = np.argmin(np.abs(velocity_grid - vel))
        
        per_start = max(0, per_idx - line_width // 2)
        per_end = min(len(period_grid), per_idx + line_width // 2 + 1)
        vel_start = max(0, vel_idx - line_width // 2)
        vel_end = min(len(velocity_grid), vel_idx + line_width // 2 + 1)
        
        amplitude_map[per_start:per_end, vel_start:vel_end] = 1.0
    
    return period_grid, velocity_grid, amplitude_map


def generate_theoretical_ftan(sregn_file, model_name, az_range, rad_range, distance_km, exp_num, output_dir, img_size=256):
    """Generate theoretical FTAN image matching observed format - 256x256 RGB"""
    data = np.loadtxt(sregn_file, skiprows=1)
    mode0_mask = data[:, 0] == 0
    theory_periods = data[mode0_mask, 2]
    theory_gvel = data[mode0_mask, 5]
    
    period_grid, velocity_grid, amplitude_map = create_theoretical_sharp_map(
        theory_periods, theory_gvel,
        period_range=(1, 20),
        velocity_range=(0.5, 4.5),
        period_spacing=0.25,
        velocity_spacing=0.01,
        line_width=5
    )
    
    fig = plt.figure(figsize=(img_size/100, img_size/100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    
    ax.imshow(np.transpose(amplitude_map), 
             cmap='inferno',
             extent=[period_grid[0], period_grid[-1], 
                    velocity_grid[0], velocity_grid[-1]],
             aspect='auto',
             origin='lower',
             vmin=0, vmax=1,
             interpolation='bilinear')
    
    filename = f'ftan_theoretical_{model_name}_az{az_range}_dist{int(distance_km)}_rad{rad_range}.png'
    filepath = output_dir / filename
    
    plt.savefig(filepath, dpi=100, bbox_inches='tight', pad_inches=0)
    plt.close()
    
    az_parts = az_range.split('-')
    rad_parts = rad_range.split('-')
    
    metadata = {
        'filename': filename,
        'data_type': 'theoretical',
        'experiment': exp_num,
        'model': model_name,
        'azimuth_start': int(az_parts[0]),
        'azimuth_end': int(az_parts[1]),
        'distance_km': distance_km,
        'radius_min': int(rad_parts[0]),
        'radius_max': int(rad_parts[1]),
        'error_percent': 0.0
    }
    
    return filename, metadata


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


def process_experiment(exp_num, output_dir, theoretical_dir, metadata_list, img_size=256):
    print(f"\n{'='*70}")
    print(f"EXPERIMENT {exp_num}")
    print('='*70)
    
    exp_base = Path(f"experiments/experiment_{exp_num}/outputs")
    if not exp_base.exists():
        print(f"  Error: {exp_base} not found")
        return 0, 0, 0

    analysis_dir = exp_base / "azimuthal_coverage_analysis"
    if not analysis_dir.exists():
        print(f"  Error: {analysis_dir} not found")
        return 0, 0, 0

    model_name = parse_model_name(exp_base)
    distance_km, rad_range = parse_distance_and_radius(exp_base)

    if not model_name:
        print("  Error: Could not parse model name")
        return 0, 0, 0

    print(f"  Model: {model_name}")
    print(f"  Distance: {distance_km} km")
    print(f"  Radius: {rad_range} km")

    sregn_file = None
    final_dirs = list(exp_base.glob('final_dist_*'))
    if final_dirs:
        sregn_file = final_dirs[0] / 'SREGN.ASC'
        if not sregn_file.exists():
            sregn_file = None

    if not sregn_file:
        print("  Error: SREGN.ASC not found")
        return 0, 0, 0

    case_dirs = sorted(analysis_dir.glob('case_*'))
    if not case_dirs:
        print("  Error: No cases found")
        return 0, 0, 0

    print(f"  Processing {len(case_dirs)} cases...")

    successful = 0
    failed = 0
    theoretical_count = 0

    for case_dir in case_dirs:
        case_name = case_dir.name
        case_id = case_name.split('_')[1]

        az_match = re.search(r'_az(\d+)-(\d+)', case_name)
        az_range = f"{az_match.group(1)}-{az_match.group(2)}" if az_match else "unknown"

        ccf_file = case_dir / 'stacked_time_ccf.txt'
        if not ccf_file.exists():
            failed += 1
            continue

        try:
            ftan = FTAN_ML(ccf_file, distance_km, sregn_file, model_name, az_range, rad_range, exp_num)
            ftan.compute_ftan(fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5)
            ftan.extract_dispersion()
            filename = ftan.save_ml_image(output_dir, data_type='observed', img_size=img_size)
            metadata_list.append(ftan.get_metadata(filename, data_type='observed'))
            successful += 1
            
            theo_filename, theo_metadata = generate_theoretical_ftan(
                sregn_file, model_name, az_range, rad_range, distance_km, exp_num, theoretical_dir, img_size=img_size
            )
            metadata_list.append(theo_metadata)
            theoretical_count += 1
            
        except Exception as e:
            print(f"    Case {case_id}: Error - {e}")
            failed += 1

    print(f"  Complete: {successful} observed, {theoretical_count} theoretical, {failed} failed")
    
    return successful, theoretical_count, failed


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_ml_ftan.py <exp1> [exp2] [exp3] ...")
        print("Example: python generate_ml_ftan.py 6 7 8")
        sys.exit(1)

    experiment_numbers = [int(x) for x in sys.argv[1:]]
    
    img_size = 256  # 256x256 images
    
    print("="*70)
    print("FTAN ML INPUT GENERATION")
    print("="*70)
    print(f"Experiments: {experiment_numbers}")
    print(f"Output: FTAN_ML_INPUT/ (observed)")
    print(f"        FTAN_ML_INPUT/theoretical/ (theoretical)")
    print(f"Format: {img_size}x{img_size} RGB PNG, inferno colormap, sharp curves")
    print(f"Ranges: Period 1-20s, Velocity 0.5-4.5 km/s")
    
    output_dir = Path("FTAN_ML_INPUT")
    output_dir.mkdir(exist_ok=True)
    
    theoretical_dir = output_dir / "theoretical"
    theoretical_dir.mkdir(exist_ok=True)
    
    metadata_list = []
    total_observed = 0
    total_theoretical = 0
    total_failed = 0
    
    for exp_num in experiment_numbers:
        obs, theo, failed = process_experiment(exp_num, output_dir, theoretical_dir, metadata_list, img_size=img_size)
        total_observed += obs
        total_theoretical += theo
        total_failed += failed
    
    if metadata_list:
        metadata_file = output_dir / 'metadata.csv'
        fieldnames = ['filename', 'data_type', 'experiment', 'model', 'azimuth_start', 'azimuth_end', 
                     'distance_km', 'radius_min', 'radius_max', 'error_percent']
        
        with open(metadata_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(metadata_list)
        
        print(f"\n{'='*70}")
        print("SUMMARY")
        print('='*70)
        print(f"Observed FTANs: {total_observed}")
        print(f"Theoretical FTANs: {total_theoretical}")
        print(f"Failed: {total_failed}")
        print(f"Metadata: {metadata_file}")
        print(f"Observed images: {output_dir}/")
        print(f"Theoretical images: {theoretical_dir}/")
        print()
    else:
        print("\nNo data processed!")


if __name__ == "__main__":
    main()