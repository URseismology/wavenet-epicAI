#!/usr/bin/env python
"""
Rotates per-day NoisePy cross-correlation output from NEZ component coordinates
to RTZ (Radial, Transverse, Vertical) using the geodetic azimuth between stations,
then phase-weighted stacks all days into a single per-pair *_ncf.h5 file.

Called by stream_xcorr_pipeline.py via rotate_stack_pair() after cross-correlation
completes for each pair.
"""


import os
import glob
import h5py
import numpy as np
from scipy.signal import hilbert
from obspy.geodetics import gps2dist_azimuth

from config import CONFIG


def _component(channel_code):
    """ Helper function to extract component from channel code"""
    if not channel_code:
        return None
    last = channel_code[-1].upper()
    if last == '1':
        return 'N'
    if last == '2':
        return 'E'
    if last in ('N', 'E', 'Z'):
        return last
    return None


def extract_all_components(h5_filepath):
    """ Takes in the h5 filepath for a station pair and extracts all components"""

    nested = {}
    dt = 1.0 / CONFIG["sampling_rate"]
    maxlag = CONFIG["maxlag"]

    def visitor(name, node):
        """ Visitor function === when you find something do as such"""

        nonlocal dt, maxlag
        # Checks if the node is a dataset
        if not isinstance(node, h5py.Dataset):
            return
        # Gets the dataset name
        dataset_name = name.split('/')[-1]
        # Splits the dataset name into parts
        parts = dataset_name.split('_')
        if len(parts) != 2:
            return
        # Gets the component
        comp1 = _component(parts[0])
        comp2 = _component(parts[1])
        if comp1 is None or comp2 is None:
            return
        # Gets the sensor
        sensor1 = parts[0][:2] if len(parts[0]) >= 2 else parts[0]
        sensor2 = parts[1][:2] if len(parts[1]) >= 2 else parts[1]
        sensor_key = f"{sensor1}_{sensor2}"
        comp_key = f"{comp1}{comp2}"

        if sensor_key not in nested:
            nested[sensor_key] = {}
        if comp_key not in nested[sensor_key]:
            nested[sensor_key][comp_key] = []
        
        
        data2d = node[:]
        # Appends the data to the nested dictionary
        if len(data2d.shape) == 2:
            for row in data2d:
                nested[sensor_key][comp_key].append(row)
        elif len(data2d.shape) == 1:
            nested[sensor_key][comp_key].append(data2d)

        if 'dt' in node.attrs:
            dt = node.attrs['dt']
        if 'maxlag' in node.attrs:
            maxlag = node.attrs['maxlag']

    # Visits all items in the h5 file (Aka gets every combination of all three of the componets per day)
    with h5py.File(h5_filepath, 'r') as f:
        f.visititems(visitor)

    return nested, dt, maxlag


def rotate_tensor(components, azimuth_deg):
    """ Rotates the components from NEZ to RTZ"""
    phi = np.radians(azimuth_deg)
    cos_phi = np.cos(phi)
    sin_phi = np.sin(phi)

    def _stack(key):
        """ Helper function to stack the components together"""
        entry = components.get(key)
        if entry is None or entry[1] == 0:
            return None
        # Calculate linear and coherence 
        linear = entry[0] / entry[1]
        # entry[2] is the sum of the phasors
        coherence = np.abs(entry[2]) / entry[1]
        # Resultant phase weighted stack
        return linear * coherence ** 1
    
    # Stacks the components together
    NN = _stack("NN")
    NE = _stack("NE")
    NZ = _stack("NZ")
    EN = _stack("EN")
    EE = _stack("EE")
    EZ = _stack("EZ")
    ZN = _stack("ZN")
    ZE = _stack("ZE")
    ZZ = _stack("ZZ")

    ref_len = None

    for arr in [ZZ, NN, EE, NE, EN, NZ, EZ, ZN, ZE]:
        if arr is not None:
            ref_len = len(arr)
            break

    if ref_len is None:
        print("  Warning: No components found!")
        return {"RR": None, "TT": None, "ZZ": None}
    # Account for missing components
    zeros = np.zeros(ref_len)
    if NN is None: NN = zeros
    if NE is None: NE = zeros
    if EN is None: EN = zeros
    if EE is None: EE = zeros
    if NZ is None: NZ = zeros
    if EZ is None: EZ = zeros
    if ZN is None: ZN = zeros
    if ZE is None: ZE = zeros
    if ZZ is None: ZZ = zeros
    
    # Rotate the components from NEZ to RTZ
    RR = (cos_phi**2 * NN +
          cos_phi * sin_phi * (NE + EN) +
          sin_phi**2 * EE)

    TT = (sin_phi**2 * NN -
          cos_phi * sin_phi * (NE + EN) +
          cos_phi**2 * EE)
    
    n_windows = {key: (components[key][1] if key in components else 0)
                 for key in ["NN", "NE", "EN", "EE", "NZ", "EZ", "ZN", "ZE", "ZZ"]}

    return {
        "RR": RR, "TT": TT, "ZZ": ZZ,
        "_n_windows": n_windows,
    }


def save_pair_ncf(outpath, pair_label, sensor_key, rotated, dt, maxlag, n_days, n_win):
    """Writes rotated ZZ/RR/TT NCFs for one sensor pair into the Wavenet HDF5 schema at outpath."""
    # Check for valid component data
    npts = next(
        (len(rotated[c]) for c in ["ZZ", "RR", "TT"] if rotated.get(c) is not None),
        None,
    )
    if npts is None:
        print(f"  Warning: no component data for {pair_label}/{sensor_key} — skipping save")
        return
    # Get the frequency and time axis for the cross-correlation data
    freq_axis = np.fft.rfftfreq(npts, d=dt)
    time_axis = np.linspace(-maxlag, maxlag, npts)
    
    # Generate HDF5 file
    with h5py.File(outpath, 'a') as f:
        pair_grp = f.require_group(pair_label)
        pair_grp.attrs["dt"] = dt
        pair_grp.attrs["maxlag"] = maxlag
        pair_grp.attrs["stack_days"] = n_days

        sensor_grp = pair_grp.create_group(sensor_key)
        sensor_grp.attrs["n_windows_ZZ"] = n_win.get("ZZ", 0)
        sensor_grp.attrs["n_windows_RR"] = n_win.get("NN", 0)
        sensor_grp.attrs["n_windows_TT"] = n_win.get("EE", 0)
        sensor_grp.create_dataset("freq_axis", data=freq_axis)
        sensor_grp.create_dataset("time_axis", data=time_axis)

        for comp in ["ZZ", "RR", "TT"]:
            data = rotated.get(comp)
            if data is None:
                continue
            comp_grp = sensor_grp.create_group(comp)
            comp_grp.create_dataset("time_domain", data=data.astype(np.float64))
            comp_grp.create_dataset("cross_spectrum", data=np.fft.rfft(data).astype(np.complex128))

    print(f"  Saved: {outpath}")


def rotate_stack_pair(pair_row, ncfdir, ncf_outdir):
    """Loads all daily NCF HDF5 files for a pair, accumulates a PWS stack, rotates NEZ→RTZ, and saves the result."""
    net1, sta1 = pair_row['net1'], pair_row['sta1']
    net2, sta2 = pair_row['net2'], pair_row['sta2']
    lat1, lon1 = pair_row['lat1'], pair_row['lon1']
    lat2, lon2 = pair_row['lat2'], pair_row['lon2']
    pair_label = f"{net1}.{sta1}_{net2}.{sta2}"
    
    # Get metrics for the pair based on latitude and longitude
    dist_m, az, baz = gps2dist_azimuth(lat1, lon1, lat2, lon2)
    dist_km = dist_m / 1000.0
    
    pair_dir = os.path.join(ncfdir, pair_label)
    if not os.path.exists(pair_dir):
        print(f"  No NCF directory found: {pair_dir}")
        return False

    h5_files = sorted(glob.glob(os.path.join(pair_dir, "*.h5")))
    if not h5_files:
        print(f"  No HDF5 files in {pair_dir}")
        return False

    print(f"  Found {len(h5_files)} daily cross-correlation files.")

    all_components = {}

    for h5_path in h5_files:
        # Extracts the components from the h5 file
        file_nested, dt, maxlag = extract_all_components(h5_path)
        # Iterates through the components
        for sensor_key, comps in file_nested.items():
            # If the sensor key is not in the all_components dictionary, add it
            if sensor_key not in all_components:
                all_components[sensor_key] = {}
            # Loop over components
            for comp_key, windows in comps.items():
                for w in windows:
                    if comp_key not in all_components[sensor_key]:
                        all_components[sensor_key][comp_key] = [
                            np.zeros(len(w), dtype=np.float64),
                            0,
                            np.zeros(len(w), dtype=np.complex128),
                        ]
                    if len(w) != len(all_components[sensor_key][comp_key][0]):
                        print(f"  Warning: {comp_key} length mismatch in {h5_path} — skipping")
                        continue
                    all_components[sensor_key][comp_key][0] += w # sum of waveforms
                    all_components[sensor_key][comp_key][1] += 1 # day count
                    h = hilbert(w)
                    env = np.abs(h)
                    all_components[sensor_key][comp_key][2] += np.where(env > 0, h / env, 0.0)   # sum of unit phasors

    if not all_components:
        print(f"  No cross-correlation data found for {pair_label}.")
        return False

    overlap_days = len(h5_files)
    print(f"  Sensor groups: {sorted(all_components.keys())}")

    pair_outpath = os.path.join(ncf_outdir, f"{pair_label}_ncf.h5")

    for sensor_key, components in sorted(all_components.items()):
        if sum(v[1] for v in components.values()) == 0:
            continue
        print(f"  Rotating {sensor_key} with azimuth φ = {az:.2f}°...")
        # Rotates the components from NEZ to RTZ and stacks
        rotated = rotate_tensor(components, az)
        n_win = rotated.get("_n_windows", {})
        # Saves the rotated components to a new h5 file
        save_pair_ncf(pair_outpath, pair_label, sensor_key,
                      rotated, dt, maxlag, overlap_days, n_win)
    return True


