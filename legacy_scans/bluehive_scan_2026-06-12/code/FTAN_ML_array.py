#!/usr/bin/env python3
"""
Generate FTAN data for ML training.

Arrays saved as per-row normalized [0,1] FTAN + Gaussian curve guidance row.
PNG images match arrays exactly (same normalization).
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import scipy.interpolate
from scipy.interpolate import interp1d
import re
import csv

try:
    import pycwt
except ImportError:
    print("Error: pycwt not installed. Install with: pip install pycwt")
    sys.exit(1)


class FTAN_ML:
    def __init__(self, ccf_file, distance_km, sregn_file, model_name, az_range, rad_range, exp_num):
        self.ccf_file    = ccf_file
        self.distance_km = distance_km
        self.sregn_file  = sregn_file
        self.model_name  = model_name
        self.az_range    = az_range
        self.rad_range   = rad_range
        self.exp_num     = exp_num
        self.dt          = 0.5

        data      = np.loadtxt(ccf_file)
        self.lags = data[:, 0]
        self.ccf  = data[:, 1]

        self.load_theoretical_dispersion(sregn_file)

    def load_theoretical_dispersion(self, sregn_file):
        data           = np.loadtxt(sregn_file, skiprows=1)
        mode0_mask     = data[:, 0] == 0
        self.theory_periods = data[mode0_mask, 2]
        self.theory_gvel    = data[mode0_mask, 5]

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

        dj  = 1 / 24
        s0  = -1
        J   = -1
        wvn = 'morlet'

        cwt, sj, freq, coi, _, _ = pycwt.cwt(data, self.dt, dj, s0, J, wvn)

        if (fmax > np.max(freq)) or (fmax <= fmin):
            raise ValueError(
                f"Frequency out of limits! freq range: {freq.min():.3f}-{freq.max():.3f} Hz"
            )

        freq_ind = np.where((freq >= fmin) & (freq <= fmax))[0]
        cwt  = cwt[freq_ind]
        freq = freq[freq_ind]

        period = 1 / freq
        rcwt   = np.abs(cwt) ** 2

        per = np.arange(int(1 / fmax), int(1 / fmin), 0.25)
        vel = np.arange(vmin, vmax, 0.01)

        velocity_data = self.distance_km / tvec
        fc       = scipy.interpolate.interp2d(velocity_data, period, rcwt, kind='linear')
        rcwt_new = fc(vel, per)

        # Per-row normalization: each period row scaled to [0, 1] by its own max.
        # Matches the visual output and is the correct representation for
        # segmentation (we care about peak location per period, not absolute amplitude).
        for ii in range(len(per)):
            row_max = np.max(rcwt_new[ii])
            if row_max > 0:
                rcwt_new[ii] /= row_max

        from scipy.ndimage import gaussian_filter1d
        sigma = 0.15
        for j in range(len(vel)):
            rcwt_new[:, j] = gaussian_filter1d(rcwt_new[:, j], sigma=sigma)

        self.periods    = per
        self.velocities = vel
        self.ftan_amp   = rcwt_new  # per-row normalized [0, 1]

        return per, vel, rcwt_new

    def extract_dispersion(self):
        nper = []
        gv   = []

        for ii in range(len(self.periods)):
            max_idx = np.argmax(self.ftan_amp[ii, :])
            max_amp = self.ftan_amp[ii, max_idx]

            if max_amp > 0.5:
                nper.append(self.periods[ii])
                gv.append(self.velocities[max_idx])

        self.periods_picked   = np.array(nper)
        self.group_velocities = np.array(gv)

        if len(self.periods_picked) > 0:
            theory_at_picked = np.interp(
                self.periods_picked, self.theory_periods, self.theory_gvel
            )
            misfit     = np.sqrt(np.mean((self.group_velocities - theory_at_picked) ** 2))
            rel_misfit = misfit / np.mean(theory_at_picked) * 100
            self.rms_error = rel_misfit
        else:
            self.rms_error = 999.9

        return self.rms_error

    def save_ml_image(self, output_dir, data_type='observed', img_size=256):
        """Save 256x256 RGB PNG. ftan_amp is already per-row normalized so vmin/vmax=0/1."""
        fig = plt.figure(figsize=(img_size / 100, img_size / 100), dpi=100)
        ax  = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')

        ax.imshow(
            np.transpose(self.ftan_amp),
            cmap='inferno',
            extent=[self.periods[0], self.periods[-1],
                    self.velocities[0], self.velocities[-1]],
            aspect='auto',
            origin='lower',
            vmin=0, vmax=1,
            interpolation='bilinear'
        )

        if data_type == 'theoretical':
            filename = (f'ftan_theoretical_{self.model_name}_az{self.az_range}'
                        f'_dist{int(self.distance_km)}_rad{self.rad_range}.png')
        else:
            filename = (f'ftan_{self.model_name}_az{self.az_range}'
                        f'_dist{int(self.distance_km)}_rad{self.rad_range}.png')

        plt.savefig(output_dir / filename, dpi=100, bbox_inches='tight', pad_inches=0)
        plt.close()

        return filename

    def build_observed_array(self):
        """
        Build (77, 400) array:
          rows 0-75 : per-row normalized FTAN amplitude [0, 1]
          row  76   : Gaussian bump centered at extracted curve velocity per period bin

        The Gaussian row gives the network a soft spatial hint — a smooth peak at
        the expected velocity position for each period — rather than tiling scalar
        values which produces spurious vertical stripes.
        """
        n_periods, n_velocities = self.ftan_amp.shape  # (76, 400)

        # Extracted curve: argmax velocity per period row
        extracted_vel = np.array(
            [self.velocities[np.argmax(self.ftan_amp[i, :])] for i in range(n_periods)],
            dtype=np.float32
        )  # (76,) in km/s

        # Build Gaussian bump row: for each period, accumulate a Gaussian centered
        # at the extracted velocity bin. sigma=5 bins ≈ 0.05 km/s half-width.
        x         = np.arange(n_velocities, dtype=np.float32)
        curve_row = np.zeros((1, n_velocities), dtype=np.float32)
        sigma     = 5.0
        vel_range = self.velocities[-1] - self.velocities[0]

        for i in range(n_periods):
            v       = extracted_vel[i]
            bin_idx = (v - self.velocities[0]) / vel_range * (n_velocities - 1)
            curve_row[0] += np.exp(-0.5 * ((x - bin_idx) / sigma) ** 2)

        # Normalize to [0, 1]
        row_max = curve_row.max()
        if row_max > 1e-10:
            curve_row /= row_max

        return np.vstack([self.ftan_amp, curve_row]).astype(np.float32)  # (77, 400)

    def save_numerical_arrays(self, numerical_dir, theoretical_array=None):
        base          = (f'ftan_{self.model_name}_az{self.az_range}'
                         f'_dist{int(self.distance_km)}_rad{self.rad_range}')
        obs_filename  = f'{base}_observed.npy'
        theo_filename = f'{base}_theoretical.npy' if theoretical_array is not None else None

        np.save(numerical_dir / obs_filename, self.build_observed_array())

        if theoretical_array is not None:
            np.save(numerical_dir / theo_filename, theoretical_array)

        return obs_filename, theo_filename

    def get_metadata(self, filename, data_type='observed', obs_array=None, theo_array=None):
        az_parts  = self.az_range.split('-')
        rad_parts = self.rad_range.split('-')

        meta = {
            'filename':      filename,
            'data_type':     data_type,
            'experiment':    self.exp_num,
            'model':         self.model_name,
            'azimuth_start': int(az_parts[0]),
            'azimuth_end':   int(az_parts[1]),
            'distance_km':   self.distance_km,
            'radius_min':    int(rad_parts[0]),
            'radius_max':    int(rad_parts[1]),
            'error_percent': round(self.rms_error, 2) if data_type == 'observed' else 0.0,
        }
        if obs_array:
            meta['observed_array']    = obs_array
        if theo_array:
            meta['theoretical_array'] = theo_array

        return meta


# ---------------------------------------------------------------------------
# Theoretical FTAN helpers
# ---------------------------------------------------------------------------

def create_theoretical_sharp_map(theory_periods, theory_gvel,
                                  period_range=(1, 20), velocity_range=(0.5, 4.5),
                                  period_spacing=0.25, velocity_spacing=0.01,
                                  line_width=5):
    """
    Build (n_periods, n_vel) binary map with a ±line_width/2 band around the
    theoretical dispersion curve.  Values are 0.0 or 1.0.
    """
    period_grid   = np.arange(period_range[0],   period_range[1],   period_spacing)
    velocity_grid = np.arange(velocity_range[0], velocity_range[1], velocity_spacing)

    amplitude_map = np.zeros((len(period_grid), len(velocity_grid)), dtype=np.float32)

    for per, vel in zip(theory_periods, theory_gvel):
        if (per < period_range[0] or per >= period_range[1] or
                vel < velocity_range[0] or vel >= velocity_range[1]):
            continue

        per_idx = int(np.argmin(np.abs(period_grid - per)))
        vel_idx = int(np.argmin(np.abs(velocity_grid - vel)))

        per_start = max(0, per_idx - line_width // 2)
        per_end   = min(len(period_grid),   per_idx + line_width // 2 + 1)
        vel_start = max(0, vel_idx - line_width // 2)
        vel_end   = min(len(velocity_grid), vel_idx + line_width // 2 + 1)

        amplitude_map[per_start:per_end, vel_start:vel_end] = 1.0

    return period_grid, velocity_grid, amplitude_map


def build_theoretical_array(amplitude_map, theory_periods, theory_gvel,
                             period_grid, velocity_grid):
    """
    Build (77, 400) array:
      rows 0-75 : binary theoretical FTAN map (0 or 1)
      row  76   : SREGN group velocity interpolated onto period_grid as Gaussian bumps
    """
    n_periods, n_velocities = amplitude_map.shape

    interp_func = interp1d(theory_periods, theory_gvel,
                           kind='cubic', bounds_error=False, fill_value='extrapolate')
    theo_vel    = interp_func(period_grid).astype(np.float32)  # (76,) km/s

    # Gaussian bump row matching the observed array format
    x         = np.arange(n_velocities, dtype=np.float32)
    curve_row = np.zeros((1, n_velocities), dtype=np.float32)
    sigma     = 5.0
    vel_range = velocity_grid[-1] - velocity_grid[0]

    for i in range(n_periods):
        v       = theo_vel[i]
        bin_idx = (v - velocity_grid[0]) / vel_range * (n_velocities - 1)
        curve_row[0] += np.exp(-0.5 * ((x - bin_idx) / sigma) ** 2)

    row_max = curve_row.max()
    if row_max > 1e-10:
        curve_row /= row_max

    return np.vstack([amplitude_map, curve_row]).astype(np.float32)  # (77, 400)


def generate_theoretical_ftan(sregn_file, model_name, az_range, rad_range,
                               distance_km, exp_num, output_dir, numerical_dir,
                               img_size=256):
    data       = np.loadtxt(sregn_file, skiprows=1)
    mode0_mask = data[:, 0] == 0
    theory_periods = data[mode0_mask, 2]
    theory_gvel    = data[mode0_mask, 5]

    period_grid, velocity_grid, amplitude_map = create_theoretical_sharp_map(
        theory_periods, theory_gvel
    )

    theo_array = build_theoretical_array(
        amplitude_map, theory_periods, theory_gvel, period_grid, velocity_grid
    )

    # PNG
    fig = plt.figure(figsize=(img_size / 100, img_size / 100), dpi=100)
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.axis('off')
    ax.imshow(
        np.transpose(amplitude_map),
        cmap='inferno',
        extent=[period_grid[0], period_grid[-1],
                velocity_grid[0], velocity_grid[-1]],
        aspect='auto', origin='lower',
        vmin=0, vmax=1, interpolation='bilinear'
    )
    filename = (f'ftan_theoretical_{model_name}_az{az_range}'
                f'_dist{int(distance_km)}_rad{rad_range}.png')
    plt.savefig(output_dir / filename, dpi=100, bbox_inches='tight', pad_inches=0)
    plt.close()

    # .npy
    base              = f'ftan_{model_name}_az{az_range}_dist{int(distance_km)}_rad{rad_range}'
    theo_npy_filename = f'{base}_theoretical.npy'
    np.save(numerical_dir / theo_npy_filename, theo_array)

    az_parts  = az_range.split('-')
    rad_parts = rad_range.split('-')
    metadata  = {
        'filename':          filename,
        'data_type':         'theoretical',
        'experiment':        exp_num,
        'model':             model_name,
        'azimuth_start':     int(az_parts[0]),
        'azimuth_end':       int(az_parts[1]),
        'distance_km':       distance_km,
        'radius_min':        int(rad_parts[0]),
        'radius_max':        int(rad_parts[1]),
        'error_percent':     0.0,
        'theoretical_array': theo_npy_filename,
    }

    return filename, metadata, theo_array


# ---------------------------------------------------------------------------
# Path parsing helpers
# ---------------------------------------------------------------------------

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
    dir_name   = sim_dirs[0].name
    dist_match = re.search(r'_dist_(\d+)_', dir_name)
    distance   = int(dist_match.group(1)) if dist_match else None
    rad_match  = re.search(r'_rad_(\d+)_(\d+)', dir_name)
    if rad_match:
        rad_range = f"{rad_match.group(1)}-{rad_match.group(2)}"
    else:
        rad_range = None
    return distance, rad_range


# ---------------------------------------------------------------------------
# Per-experiment processing
# ---------------------------------------------------------------------------

def process_experiment(exp_num, output_dir, theoretical_dir, numerical_dir,
                       metadata_list, img_size=256):
    print(f"\n{'='*70}")
    print(f"EXPERIMENT {exp_num}")
    print('=' * 70)

    exp_base     = Path(f"experiments/experiment_{exp_num}/outputs")
    analysis_dir = exp_base / "azimuthal_coverage_analysis"

    for path, label in [(exp_base, str(exp_base)),
                        (analysis_dir, str(analysis_dir))]:
        if not path.exists():
            print(f"  Error: {label} not found")
            return 0, 0, 0

    model_name             = parse_model_name(exp_base)
    distance_km, rad_range = parse_distance_and_radius(exp_base)

    if not model_name:
        print("  Error: Could not parse model name")
        return 0, 0, 0

    print(f"  Model: {model_name}  |  Distance: {distance_km} km  |  Radius: {rad_range} km")

    sregn_file = None
    for d in exp_base.glob('final_dist_*'):
        candidate = d / 'SREGN.ASC'
        if candidate.exists():
            sregn_file = candidate
            break

    if not sregn_file:
        print("  Error: SREGN.ASC not found")
        return 0, 0, 0

    case_dirs = sorted(analysis_dir.glob('case_*'))
    if not case_dirs:
        print("  Error: No cases found")
        return 0, 0, 0

    print(f"  Processing {len(case_dirs)} cases...")

    successful = failed = theoretical_count = 0

    for case_dir in case_dirs:
        case_name = case_dir.name
        case_id   = case_name.split('_')[1]

        az_match = re.search(r'_az(\d+)-(\d+)', case_name)
        az_range = f"{az_match.group(1)}-{az_match.group(2)}" if az_match else "unknown"

        ccf_file = case_dir / 'stacked_time_ccf.txt'
        if not ccf_file.exists():
            failed += 1
            continue

        try:
            theo_filename, theo_metadata, theo_array = generate_theoretical_ftan(
                sregn_file, model_name, az_range, rad_range,
                distance_km, exp_num, theoretical_dir, numerical_dir, img_size=img_size
            )

            ftan = FTAN_ML(ccf_file, distance_km, sregn_file,
                           model_name, az_range, rad_range, exp_num)
            ftan.compute_ftan(fmin=0.05, fmax=1.0, vmin=0.5, vmax=4.5)
            ftan.extract_dispersion()

            obs_png          = ftan.save_ml_image(output_dir, data_type='observed', img_size=img_size)
            obs_npy, theo_npy = ftan.save_numerical_arrays(numerical_dir, theoretical_array=theo_array)

            metadata_list.append(ftan.get_metadata(
                obs_png, data_type='observed',
                obs_array=obs_npy, theo_array=theo_npy
            ))
            metadata_list.append(theo_metadata)

            successful        += 1
            theoretical_count += 1

        except Exception as e:
            print(f"    Case {case_id}: Error - {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print(f"  Complete: {successful} observed, {theoretical_count} theoretical, {failed} failed")
    return successful, theoretical_count, failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_ml_ftan.py <exp1> [exp2] ...")
        sys.exit(1)

    experiment_numbers = [int(x) for x in sys.argv[1:]]

    output_dir      = Path("FTAN_ML_INPUT");      output_dir.mkdir(exist_ok=True)
    theoretical_dir = output_dir / "theoretical"; theoretical_dir.mkdir(exist_ok=True)
    numerical_dir   = Path("FTAN_NUMERICAL");     numerical_dir.mkdir(exist_ok=True)

    metadata_list  = []
    total_obs = total_theo = total_failed = 0

    for exp_num in experiment_numbers:
        obs, theo, failed = process_experiment(
            exp_num, output_dir, theoretical_dir, numerical_dir, metadata_list
        )
        total_obs    += obs
        total_theo   += theo
        total_failed += failed

    if metadata_list:
        metadata_file = output_dir / 'metadata.csv'
        fieldnames    = [
            'filename', 'data_type', 'experiment', 'model',
            'azimuth_start', 'azimuth_end', 'distance_km',
            'radius_min', 'radius_max', 'error_percent',
            'observed_array', 'theoretical_array'
        ]
        with open(metadata_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(metadata_list)

        print(f"\nSummary: {total_obs} observed | {total_theo} theoretical | {total_failed} failed")
        print(f"Arrays (77x400): {numerical_dir}/")
        print(f"Images (256x256): {output_dir}/")
        print(f"Metadata: {metadata_file}")
    else:
        print("\nNo data processed!")


if __name__ == "__main__":
    main()