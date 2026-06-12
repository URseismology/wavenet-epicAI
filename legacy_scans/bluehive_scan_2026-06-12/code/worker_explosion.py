#!/usr/bin/env python3

import numpy as np
import subprocess
import os
import sys
import shutil
import time
import math
from mpi4py import MPI

def get_vmin_from_model(model_file):
    try:
        data = np.loadtxt(model_file, skiprows=12)
        vs_values = data[:, 2]
        vs_nonzero = vs_values[vs_values > 0]

        if len(vs_nonzero) == 0:
            print("WARNING: No non-zero VS values in model! Using default 2.5 km/s")
            return 2.5

        vmin = np.min(vs_nonzero)
        return vmin

    except Exception as e:
        print(f"WARNING: Could not read model file: {e}")
        print("Using default VMIN = 2.5 km/s")
        return 2.5


def precompute_greens_functions(model_file, workdir, r_max, rank):
    """Compute eigenfunctions for the maximum possible source distance."""
    os.chdir(workdir)

    model_file = os.path.abspath(model_file)
    subprocess.run(['cp', model_file, 'model.d'], check=True)

    VMIN_MODEL = get_vmin_from_model(model_file)

    # Calculate maximum distance (diagonal of square domain)
    DIST_MAX = np.sqrt(2) * r_max

    DELTA = 0.5
    NPT = int((DIST_MAX / VMIN_MODEL) / DELTA)
    NPT = 2 ** math.ceil(math.log2(NPT))

    with open('dfile', 'w') as f:
        f.write(f"{DIST_MAX} {DELTA} {NPT} 0.0 0.0\n")

    if rank == 0:
        print("  Computing eigenfunctions")
        print(f"  Max distance: {DIST_MAX:.2f} km")
        print(f"  NPT: {NPT} (power-of-2 rounded)")

    cmds = [
        'sprep96 -M model.d -HS 0 -HR 0 -L -R -NMOD 10 -d dfile',
        'sdisp96',
        'sregn96 -NOQ',
        'slegn96 -NOQ'
    ]

    for cmd in cmds:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            if rank == 0:
                print(f"ERROR in {cmd.split()[0]}")
                print(result.stderr[:500])
            return False

    if rank == 0:
        print("   Eigenfunctions computed")
    return True


def generate_sources(theta_min, theta_max, r_min, r_max, n_sources, rank, size):
    """Generate sources for this MPI rank with random force components."""
    sources_per_rank = n_sources // size
    start_idx = rank * sources_per_rank

    if rank == size - 1:
        end_idx = n_sources
    else:
        end_idx = start_idx + sources_per_rank

    sources = []
    for i in range(start_idx, end_idx):
        np.random.seed(42 + i)

        theta = np.random.uniform(theta_min, theta_max) * np.pi / 180.0
        r = np.random.uniform(r_min, r_max)
        x = r * np.cos(theta)
        y = r * np.sin(theta)

        # Generate random force components
        fn = np.random.uniform(-1, 1)  # North component
        fe = np.random.uniform(-1, 1)  # East component
        fd = np.random.uniform(-1, 1)  # Down component

        sources.append((x, y, fn, fe, fd))

    return sources


def save_sources_file(all_sources, output_dir, sim_id):
    """Save source coordinates to CSV file."""
    sources_file = os.path.join(output_dir, f'SOURCES_{sim_id:06d}.csv')
    
    with open(sources_file, 'w') as f:
        f.write('# Source locations\n')
        f.write('# x_km,y_km\n')
        for x, y, fn, fe, fd in all_sources:
            f.write(f'{x:.6f},{y:.6f}\n')
    
    return sources_file


def parse_spulse96_output(text_output):
    """
    Parse spulse96 output, skipping metadata lines after each component header.
    """
    lines = text_output.strip().split('\n')

    components = {}
    current_comp = None
    current_data = []
    lines_to_skip = 0

    for line in lines:
        line = line.strip()

        # Check for component header
        if line in ['ZEX', 'REX', 'ZVF', 'RVF', 'ZHF', 'RHF', 'THF']:
            # Save previous component
            if current_comp and len(current_data) > 0 and current_comp not in components:
                arr = np.array(current_data, dtype=np.float32)
                components[current_comp] = arr

            current_comp = line
            current_data = []
            lines_to_skip = 2  # Skip next 2 lines (metadata + header)
            continue

        # Skip metadata lines
        if lines_to_skip > 0:
            lines_to_skip -= 1
            continue

        # Accumulate data (only lines with scientific notation)
        if current_comp:
            if 'E-' in line or 'E+' in line or 'e-' in line or 'e+' in line:
                numbers = line.split()
                for num in numbers:
                    try:
                        val = float(num)
                        current_data.append(val)
                    except:
                        pass

    # Save final component
    if current_comp and len(current_data) > 0 and current_comp not in components:
        arr = np.array(current_data, dtype=np.float32)
        components[current_comp] = arr

    return components


def rotate_forces(f1, f2, f3, azimuth):
    az = azimuth * np.pi / 180.0

    fR = f1 * np.cos(az) + f2 * np.sin(az)
    fT = f1 * np.sin(az) - f2 * np.cos(az)
    fZ = f3

    return fR, fT, fZ


def compute_mt_to_zne(greens, fR, fT, fZ, backazimuth):
    #uZ = fZ * greens.get('ZVF', None) + fR * greens.get('ZHF', None)
    uZ = fZ * greens.get('ZEX', None) #we might have to fall back to this code for only explosion rather than point force
    uR = fR * greens.get('RHF', None) + fZ * greens.get('RVF', None)


    if 'THF' in greens:
        uT = fT * greens.get('THF', None)
    else:
        uT = np.zeros_like(greens.get('ZVF', None), dtype=float)

    baz = backazimuth * np.pi / 180.0
    uN = -np.cos(baz) * uR + np.sin(baz) * uT
    uE = -np.sin(baz) * uR - np.cos(baz) * uT

    return uZ, uN, uE


def simulate_source(x, y, receiver_x, receiver_y, fn, fe, fd, workdir, npt_fixed):
    """Simulate a single source with force rotation applied."""
    os.chdir(workdir)

    dx = receiver_x - x
    dy = receiver_y - y
    dist = np.sqrt(dx**2 + dy**2)

    if dist < 50.0 or dist > 1400.0:
        return None, None, None

    # Calculate azimuth & backazimuth
    azimuth = np.arctan2(dx, dy) * 180.0 / np.pi
    if azimuth < 0:
        azimuth += 360.0

    if azimuth < 180:
        backazimuth = azimuth + 180
    elif azimuth > 180:
        backazimuth = azimuth - 180
    else:
        backazimuth = 0

    delta = 0.5
    with open('dfile_src', 'w') as f:
        f.write(f"{dist} {delta} {npt_fixed} 0.0 0.0\n")

    # Generate explosion waveform
    cmd = 'spulse96 -d dfile_src -p -l 2 -V -EXF'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        return None, None, None

    try:
        components = parse_spulse96_output(result.stdout)
        fR, fT, fZ = rotate_forces(fn, fe, fd, azimuth)
        uZ, uN, uE = compute_mt_to_zne(components, fR, fT, fZ, backazimuth)

        return uZ, uN, uE

    except Exception as e:
        return None, None, None


def parse_config_params(config_file):
    """Parse configuration parameters from config file."""
    params = {}
    with open(config_file, 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0]
                    try:
                        params[key] = float(parts[1])
                    except:
                        params[key] = parts[1]
    return params


def build_output_folder_name(sim_id, config_params, model_file):
    """Build descriptive output folder name from config and model."""
    theta_min = int(config_params.get('THETA_MIN_DEG', 0))
    theta_max = int(config_params.get('THETA_MAX_DEG', 360))
    r_min = int(config_params.get('R_MIN_KM', 150))
    r_max = int(config_params.get('R_MAX_KM', 1000))
    xr1 = config_params.get('XR1_KM', -100.0)
    xr2 = config_params.get('XR2_KM', 100.0)
    
    # Calculate distance
    distance = int(abs(xr2 - xr1))
    
    # Extract model name (without path and extension)
    model_basename = os.path.basename(model_file)
    model_name = os.path.splitext(model_basename)[0]
    
    # Build folder name
    folder_name = f"sim_{sim_id:05d}_ang_{theta_min}_{theta_max}_dist_{distance}_rad_{r_min}_{r_max}_{model_name}"
    
    return folder_name


def run_simulation(config_file, model_file, output_dir, sim_id):
    """Run simulation with MPI and force randomization."""
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    start_time = time.time()

    if rank == 0:
        print(f"Simulation ID: {sim_id}")
        print(f"MPI Ranks: {size}")
        print(f"Force Randomization: ENABLED")

    # Parse config
    config = parse_config_params(config_file)

    theta_min = config.get('THETA_MIN_DEG', 0)
    theta_max = config.get('THETA_MAX_DEG', 360)
    r_min = config.get('R_MIN_KM', 150)
    r_max = config.get('R_MAX_KM', 1000)
    n_sources = int(config.get('N_SOURCES', 10000))

    rx1 = config.get('XR1_KM', 100.0)
    ry1 = config.get('YR1_KM', 0.0)
    rx2 = config.get('XR2_KM', -100.0)
    ry2 = config.get('YR2_KM', 0.0)

    # Build descriptive output folder name
    output_folder_name = build_output_folder_name(sim_id, config, model_file)
    final_output_dir = os.path.join(output_dir, output_folder_name)

    # Create work directory per rank 
    workdir = f'/scratch/tolugboj_lab/Prj_Wavenet/epic_production/tmp_wavenet/task_{sim_id}/rank_{rank}'
    os.makedirs(workdir, exist_ok=True)

    if rank == 0:
        print(f"Output folder: {output_folder_name}")
        print("Pre-computing eigenfunctions")

    # Only rank 0 precomputes eigenfunctions
    if rank == 0:
        success = precompute_greens_functions(model_file, workdir, r_max, rank)
        if not success:
            print("ERROR: Eigenfunction computation failed")
            comm.Abort(1)
        print(" Pre-computation complete\n")
        
        # Copy eigenfunction files to output directory AFTER successful computation
        os.makedirs(final_output_dir, exist_ok=True)
        eigen_files = ['model.d', 'sregn96.egn', 'slegn96.egn',
                       'sdisp96.ray', 'sdisp96.lov', 'dfile']
        
        for fname in eigen_files:
            src = os.path.join(workdir, fname)
            dst = os.path.join(final_output_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        
        print("✓ Eigenfunction files saved to output directory\n")

    # All ranks wait for rank 0 to finish
    comm.Barrier()

    # Other ranks copy eigenfunction files from rank 0
    if rank != 0:
        rank0_dir = f'/scratch/tolugboj_lab/Prj_Wavenet/epic_production/tmp_wavenet/task_{sim_id}/rank_0'
        eigen_files = ['model.d', 'sregn96.egn', 'slegn96.egn',
                       'sdisp96.ray', 'sdisp96.lov', 'dfile']

        for fname in eigen_files:
            src = os.path.join(rank0_dir, fname)
            dst = os.path.join(workdir, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)

    comm.Barrier()

    # Generate sources WITH RANDOM FORCES (divided across ranks)
    sources = generate_sources(theta_min, theta_max, r_min, r_max, n_sources, rank, size)

    # Gather all sources from all ranks to rank 0
    all_sources_gathered = comm.gather(sources, root=0)

    if rank == 0:
        # Flatten list of lists
        all_sources_flat = []
        for rank_sources in all_sources_gathered:
            all_sources_flat.extend(rank_sources)
        
        # Save sources to file
        save_sources_file(all_sources_flat, final_output_dir, sim_id)
        print(f"✓ Saved {len(all_sources_flat)} source locations\n")

    if rank == 0:
        print(f"Total sources: {n_sources}")
        print(f"Sources per rank: ~{len(sources)}")
        print(f"Force randomization: ENABLED\n")

    # Constants
    TMAX = 3599.0
    DELTA = 0.5
    TMAX_SAMPLES = int(TMAX / DELTA) + 1

    model_vmin = get_vmin_from_model(model_file)
    max_dist = np.sqrt(2) * r_max

    NPT_FIXED = int((max_dist / model_vmin) / DELTA)

    STACK_LENGTH = TMAX_SAMPLES + NPT_FIXED

    if rank == 0:
        print(f"Model file: {model_file}")
        print(f"  Model Vs_min: {model_vmin:.2f} km/s")
        print()
        print(f"Array length calculation:")
        print(f"  Max distance: {max_dist:.2f} km")
        print(f"  NPT_FIXED (wave length): {NPT_FIXED} samples")
        print(f"  TMAX_SAMPLES (time window): {TMAX_SAMPLES} samples")
        print(f"  STACK_LENGTH (total): {STACK_LENGTH} samples")
        print()

    # Create local stacking arrays
    local_stack_r1 = {'E': np.zeros(STACK_LENGTH, dtype=np.float64),
                      'N': np.zeros(STACK_LENGTH, dtype=np.float64),
                      'Z': np.zeros(STACK_LENGTH, dtype=np.float64)}
    local_stack_r2 = {'E': np.zeros(STACK_LENGTH, dtype=np.float64),
                      'N': np.zeros(STACK_LENGTH, dtype=np.float64),
                      'Z': np.zeros(STACK_LENGTH, dtype=np.float64)}

    success_count = 0
    sources_per_rank = n_sources // size
    start_idx = rank * sources_per_rank

    # Process sources assigned to this rank
    for i, (x, y, fn, fe, fd) in enumerate(sources):
        # Calculate global source number for TSHIFT
        global_src_num = start_idx + i

        # Calculate TSHIFT
        tshift = global_src_num * (TMAX / n_sources)
        shift_samples = int(tshift / DELTA)

        # Receiver 1 - WITH FORCE ROTATION
        z1, n1, e1 = simulate_source(x, y, rx1, ry1, fn, fe, fd, workdir, NPT_FIXED)
        if z1 is not None:
            for comp, wave in zip(['Z', 'N', 'E'], [z1, n1, e1]):
                wave_len = len(wave)
                end_idx = min(shift_samples + wave_len, STACK_LENGTH)
                copy_len = end_idx - shift_samples
                if copy_len > 0:
                    local_stack_r1[comp][shift_samples:end_idx] += wave[:copy_len]
            success_count += 1

        # Receiver 2 - WITH FORCE ROTATION
        z2, n2, e2 = simulate_source(x, y, rx2, ry2, fn, fe, fd, workdir, NPT_FIXED)
        if z2 is not None:
            for comp, wave in zip(['Z', 'N', 'E'], [z2, n2, e2]):
                wave_len = len(wave)
                end_idx = min(shift_samples + wave_len, STACK_LENGTH)
                copy_len = end_idx - shift_samples
                if copy_len > 0:
                    local_stack_r2[comp][shift_samples:end_idx] += wave[:copy_len]

        if rank == 0 and (i + 1) % 100 == 0:
            print(f"  Rank 0: Processed {i+1}/{len(sources)} sources")

    # Gather success counts
    all_success_counts = comm.gather(success_count, root=0)

    if rank == 0:
        total_successes = sum(all_success_counts)

    comm.Barrier()

    # Reduce (sum) all local stacks to rank 0
    global_stack_r1 = {}
    global_stack_r2 = {}

    for comp in ['E', 'N', 'Z']:
        if rank == 0:
            global_stack_r1[comp] = np.zeros(STACK_LENGTH, dtype=np.float64)
            global_stack_r2[comp] = np.zeros(STACK_LENGTH, dtype=np.float64)
        else:
            global_stack_r1[comp] = None
            global_stack_r2[comp] = None

        comm.Reduce(local_stack_r1[comp], global_stack_r1[comp], op=MPI.SUM, root=0)
        comm.Reduce(local_stack_r2[comp], global_stack_r2[comp], op=MPI.SUM, root=0)

    # Only rank 0 writes output
    if rank == 0:
        os.chdir(final_output_dir)

        for comp in ['E', 'N', 'Z']:
            r1_file = f'WAVE_SIM_{sim_id:06d}_R1_{comp}.txt'
            r2_file = f'WAVE_SIM_{sim_id:06d}_R2_{comp}.txt'

            r1_trimmed = global_stack_r1[comp][:TMAX_SAMPLES]
            r2_trimmed = global_stack_r2[comp][:TMAX_SAMPLES]

            np.savetxt(r1_file, r1_trimmed, fmt='%.15e')
            np.savetxt(r2_file, r2_trimmed, fmt='%.15e')

        meta_file = f'WAVE_SIM_{sim_id:06d}_meta.txt'
        with open(meta_file, 'w') as f:
            f.write(f"Simulation_ID: {sim_id}\n")
            f.write(f"Output_Folder: {output_folder_name}\n")
            f.write(f"Config: {config_file}\n")
            f.write(f"Model: {model_file}\n")
            f.write(f"Angles: [{theta_min}°, {theta_max}°]\n")
            f.write(f"Radius: [{r_min}, {r_max}] km\n")
            f.write(f"Receiver_1: ({rx1}, {ry1}) km\n")
            f.write(f"Receiver_2: ({rx2}, {ry2}) km\n")
            f.write(f"Distance: {np.sqrt((rx2-rx1)**2 + (ry2-ry1)**2):.2f} km\n")
            f.write(f"Sources: {n_sources}\n")
            f.write(f"Successful: {total_successes}\n")
            f.write(f"Force_Randomization: ENABLED\n")
            f.write(f"MPI_Ranks: {size}\n")
            f.write(f"Sampling_Rate: 2.0 Hz\n")
            f.write(f"Delta: 0.5 s\n")
            f.write(f"Samples: {TMAX_SAMPLES}\n")
            f.write(f"NPT_FIXED: {NPT_FIXED}\n")
            f.write(f"STACK_LENGTH: {STACK_LENGTH}\n")

        total_time = time.time() - start_time

        print(f"\n✓ Simulation {sim_id} Complete")
        print(f"  Output: {final_output_dir}")
        print(f"  Total successes: {total_successes}/{n_sources}")
        print(f"  Force randomization: ENABLED")
        print(f"  Output samples: {TMAX_SAMPLES}")
        print(f"  Total time: {total_time:.2f}s")

    # Cleanup temporary workdir
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print("Usage: simulation_worker_mpi.py <job_map.csv> <output_dir> <task_id>")
        sys.exit(1)

    job_map_csv = os.path.abspath(sys.argv[1])
    output_dir = os.path.abspath(sys.argv[2])
    task_id = int(sys.argv[3])

    if not os.path.exists(job_map_csv):
        print(f"ERROR: Job map not found: {job_map_csv}")
        sys.exit(1)

    try:
        with open(job_map_csv, 'r') as f:
            for line_num, line in enumerate(f, 1):
                if line_num == task_id:
                    parts = line.strip().split(',')
                    sim_id = int(parts[0])
                    config_file = os.path.abspath(parts[1])
                    model_file = os.path.abspath(parts[2])
                    break
            else:
                print(f"ERROR: Task ID {task_id} not found in job map")
                sys.exit(1)
    except Exception as e:
        print(f"ERROR reading job map: {e}")
        sys.exit(1)

    if not os.path.exists(config_file):
        print(f"ERROR: Config not found: {config_file}")
        sys.exit(1)

    if not os.path.exists(model_file):
        print(f"ERROR: Model not found: {model_file}")
        sys.exit(1)

    run_simulation(config_file, model_file, output_dir, sim_id)
