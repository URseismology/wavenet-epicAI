#!/usr/bin/env python
"""
Real correctness check (2026-09-23): compares our pipeline's processed output against
an independently-processed trusted reference for the same raw data, rather than just
checking "plausible magnitude, no NaNs". Used to validate GT.BOSA day 1993-058 against
ADAMA's own pre_pross.py SAC output (terravibranium,
/RAID6/bluehiveBackup/Prj5_HarnomicRFTraces/2_Data/preprocessed_data/DataGT/BOSA/) --
result: min/max agreed to 4-5 significant figures, amplitude std ratio 0.9998,
correlation 0.9523. See docs/ncf_pipeline_stages/hdf5_schema.md's "Instrument-response
correctness" section for the full writeup.

This script's file paths/dates are hardcoded to that specific run -- adapt them
(reference SAC path, HDF5 path/station/channel, and the day-offset date) to reproduce
against a different station/day. Not a general-purpose CLI tool, a worked example.
"""
import h5py
import numpy as np
from obspy import read, UTCDateTime

ref = read("reference_LHZ.sac")[0]
print("REFERENCE (ADAMA pre_pross.py output):")
print(" ", ref.stats)
print("  min/max/std:", ref.data.min(), ref.data.max(), ref.data.std())

f = h5py.File("smoketest_BOSA3.h5", "r")
ds = f["GT.BOSA"]["LHZ"]
sr = ds.attrs["sampling_rate"]
start = UTCDateTime(ds.attrs["start_time"])
print("\nMINE (preprocess_existing_archive.py output):")
print("  start:", start, "sampling_rate:", sr, "units:", ds.attrs.get("units"))

# Slice out just day 058 (2nd of the 4 concatenated days -- 057,058,059,060)
day058_start = UTCDateTime(1993, 2, 27)  # day 058 of 1993 = Feb 27
offset_samples = int(round((day058_start - start) * sr))
day_len = int(round(86400 * sr))
mine_day = ds[offset_samples:offset_samples + day_len]
print(f"  sliced day058: offset={offset_samples}, len={len(mine_day)}")
print("  min/max/std:", mine_day.min(), mine_day.max(), mine_day.std())

n = min(len(ref.data), len(mine_day))
r, m = ref.data[:n].astype(np.float64), mine_day[:n].astype(np.float64)
corr = np.corrcoef(r, m)[0, 1]
print(f"\nCorrelation coefficient (first {n} samples): {corr:.4f}")
ratio = np.std(m) / np.std(r) if np.std(r) > 0 else float('nan')
print(f"Amplitude ratio (mine/reference std): {ratio:.4f}")
