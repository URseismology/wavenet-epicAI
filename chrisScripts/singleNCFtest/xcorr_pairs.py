#!/usr/bin/env python
"""
xcorr_pairs.py — BlueHive Cross-Correlation Script (Local Data)
===================================================
Reads the filtered GridMeta pairs and locally-downloaded MiniSEED data,
runs NoisePy cross-correlation using multiprocessing, and saves the
Noise Correlation Functions (NCFs) in ASDF/HDF5 format.

*CRITICAL*: This script deletes the raw MiniSEED data for a pair once 
its NCF has been successfully generated, to save disk space.

Usage:
    python xcorr_pairs.py --datadir raw_data \
                          --pairs raw_data/pairs_to_process.csv \
                          --start 2019-01-01 --end 2019-01-31 \
                          --ncfdir NCF_output \
                          --workers 8

"""

import os
import sys
import gc
import shutil
import argparse
import time as time_module
import traceback
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count

from noisepy.seis import cross_correlate
from noisepy.seis.io.asdfstore import ASDFCCStore
from noisepy.seis.io.datatypes import (
    ConfigParameters, StackMethod, CCMethod, 
    FreqNorm, RmResp, TimeNorm
)
from noisepy.seis.io.channel_filter_store import channel_filter
from noisepy.seis.io.channelcatalog import XMLStationChannelCatalog
from datetimerange import DateTimeRange
import obspy
from obspy.clients.fdsn import Client
from ftn import apply_ftn, get_filter_TFcoeffs

# We use the SCEDCS3DataStore even for local files because it can read 
# local directories that follow the SCEDC structure.
from noisepy.seis.io.s3store import SCEDCS3DataStore


def build_config(start_date, end_date):
    """Build a standard NoisePy ConfigParameters object."""
    config = ConfigParameters()
    
    config.start_date = start_date
    config.end_date = end_date
    
    config.sampling_rate = 1.0  # Hz (ADAMA default)
    config.cc_len = 14400       # 4 hours (ADAMA winlength=4)
    config.ncomp = 3            # 3-component data
    
    config.acorr_only = False
    config.xcorr_only = True
    
    # Pre-processing natively in NoisePy will be MINIMAL.
    # We remove instrument response, downsample, and FTN normalize OURSELVES with obspy before NoisePy.
    # So NoisePy shouldn't remove response again.
    config.stationxml = False
    config.rm_resp = RmResp.NO
    config.freqmin = 0.0166     # 1/60 Hz
    config.freqmax = 0.33       # 1/3 Hz
    config.max_over_std = 10
    
    # Normalization DISABLED in NoisePy because we do FTN before.
    config.freq_norm = FreqNorm.NO
    config.time_norm = TimeNorm.NO
    
    # Correlation method
    config.cc_method = CCMethod.XCORR
    config.stack_method = StackMethod.ALL
    
    # Substacking
    config.substack = True
    config.substack_windows = 1  # 1 window per cc_len, no substacking inside the 4hr block
    config.maxlag = 200          # Extended maxlag for 1 Hz data
    
    config.channels = ["BH?", "HH?"]
    
    return config


def process_single_pair(args):
    """
    Process a single station pair: cross-correlate and save NCFs.
    
    This function is designed to be called by multiprocessing.Pool.
    All arguments are packed into a single tuple for compatibility.
    """
    pair_row, datadir, ncfdir, start_str, end_str = args
    
    net1, sta1 = pair_row['net1'], pair_row['sta1']
    net2, sta2 = pair_row['net2'], pair_row['sta2']
    dist_km = pair_row['distance_km']
    pair_label = f"{net1}.{sta1}_{net2}.{sta2}"
    
    result = {
        'pair': pair_label,
        'net1': net1, 'sta1': sta1,
        'net2': net2, 'sta2': sta2,
        'distance_km': dist_km,
        'status': 'unknown',
        'n_timespans': 0,
        'runtime_sec': 0,
    }
    
    t_start = time_module.time()
    
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        config = build_config(start_date, end_date)
        
        PAD = 1.0
        config.lamin = min(pair_row['lat1'], pair_row['lat2']) - PAD
        config.lamax = max(pair_row['lat1'], pair_row['lat2']) + PAD
        config.lomin = min(pair_row['lon1'], pair_row['lon2']) - PAD
        config.lomax = max(pair_row['lon1'], pair_row['lon2']) + PAD
        config.networks = list(set([net1, net2]))
        config.stations = list(set([sta1, sta2]))
        
        if net1 == net2 and sta1 == sta2:
            config.acorr_only = True
            config.xcorr_only = False
        else:
            config.acorr_only = False
            config.xcorr_only = True
        
        timerange = DateTimeRange(config.start_date, config.end_date)
        
        # Local paths
        sta1_dir = os.path.join(datadir, f"{net1}.{sta1}")
        sta2_dir = os.path.join(datadir, f"{net2}.{sta2}")
        
        # To make SCEDCS3DataStore work with local files, we point it to the local directory.
        # The downloaded data structure is: datadir/NET.STA/miniseed/NET/YEAR/JDAY/FILE
        # SCEDCS3DataStore expects the base path to contain 'miniseed/'.
        # Since our download script splits it by NET.STA, we need to pass a list of paths or 
        # point it to a common root. The easiest way is to point it to datadir directly if 
        # we had consolidated it, but since they are in subdirs, we might need to adjust.
        # For this POC, we'll assume the data is organized correctly under datadir.
        
        # ---- FTN PREPROCESSING STEP ----
        # 1. We process the raw data and write to a temporary processed directory.
        processed_dir = os.path.join(ncfdir, "tmp_processed", pair_label)
        os.makedirs(processed_dir, exist_ok=True)
        
        b_mat, a_mat = get_filter_TFcoeffs([config.freqmin, config.freqmax], 1.0)
        
        # We process sta1 and sta2 raw directories
        client = Client("IRIS")
        for s_dir, net, sta in [(sta1_dir, net1, sta1), (sta2_dir, net2, sta2)]:
            if not os.path.exists(s_dir):
                continue
            
            try:
                inv = client.get_stations(network=net, station=sta, level="response")
            except:
                inv = None
            
            # Setup output structure: processed_dir / NET.STA / miniseed / ...
            out_s_dir = os.path.join(processed_dir, f"{net}.{sta}")
            os.makedirs(out_s_dir, exist_ok=True)
            
            # We must recreate the NET.STA/miniseed/... structure for SCEDCS3DataStore
            for root, dirs, files in os.walk(s_dir):
                for f in files:
                    file_path = os.path.join(root, f)
                    try:
                        st = obspy.read(file_path)
                        # Process trace by trace
                        for tr in st:
                            # 1. Downsample to 1 Hz
                            if tr.stats.sampling_rate != 1.0:
                                tr.decimate(int(tr.stats.sampling_rate), no_filter=False)
                            
                            # 2. Remove Response to displacement
                            if inv:
                                try:
                                    tr.remove_response(inventory=inv, output="DISP")
                                except:
                                    pass
                                    
                            # 3. Apply FTN
                            if len(tr.data) > 0:
                                tr.data = apply_ftn(tr.data, b_mat, a_mat)
                        
                        # Reconstruct relative path for SCEDC store
                        rel_path = os.path.relpath(root, s_dir)
                        out_root = os.path.join(out_s_dir, rel_path)
                        os.makedirs(out_root, exist_ok=True)
                        st.write(os.path.join(out_root, f), format="MSEED")
                    except Exception as e:
                        print(f"Skipping file {file_path}: {e}")
                        
        # ---- Input data store (points to FTN processed data) ----
        # Local paths for catalog (using S3 as fallback is fine)
        S3_STATION_XML = "s3://scedc-pds/FDSNstationXML/CI/"
        S3_STORAGE_OPTIONS = {"s3": {"anon": True}}
        catalog = XMLStationChannelCatalog(S3_STATION_XML, storage_options=S3_STORAGE_OPTIONS)
        
        # Use PROCESSED datadir as the source for waveforms
        raw_store = SCEDCS3DataStore(
            processed_dir, catalog,
            channel_filter(config.networks, config.stations, config.channels),
            timerange
        )
        
        # ---- Output NCF store ----
        cc_path = os.path.join(ncfdir, pair_label)
        if os.path.exists(cc_path):
            shutil.rmtree(cc_path)
        
        cc_store = ASDFCCStore(cc_path)
        
        # ---- Cross-correlate ----
        cross_correlate(raw_store, config, cc_store)
        
        # ---- Verify output ----
        src_id = f"{net1}.{sta1}"
        rec_id = f"{net2}.{sta2}"
        timespans = cc_store.get_timespans(src_id, rec_id)
        result['n_timespans'] = len(timespans)
        
        if len(timespans) > 0:
            result['status'] = 'ok'
            # CRITICAL: Delete raw MiniSEED data for these stations to save space!
            if os.path.exists(sta1_dir):
                shutil.rmtree(sta1_dir)
                print(f"    [CLEANUP] Deleted raw data for {sta1_dir}")
            if os.path.exists(sta2_dir):
                shutil.rmtree(sta2_dir)
                print(f"    [CLEANUP] Deleted raw data for {sta2_dir}")
                
            # Cleanup processed tmp directory
            if os.path.exists(processed_dir):
                shutil.rmtree(processed_dir)
        else:
            result['status'] = 'no_data'
        
    except Exception as e:
        result['status'] = f'error: {str(e)[:200]}'
        traceback.print_exc()
    
    result['runtime_sec'] = round(time_module.time() - t_start, 1)
    print(f"  [{result['status']}] {pair_label} — {result['n_timespans']} timespans, {result['runtime_sec']}s")
    
    gc.collect()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run NoisePy cross-correlation on local MiniSEED data and delete raw data."
    )
    parser.add_argument("--datadir", default="raw_data", help="Directory with downloaded MiniSEED data")
    parser.add_argument("--pairs", required=True, help="Path to pairs CSV (pairs_to_process.csv)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--ncfdir", default="NCF_output", help="Output directory for NCFs (ASDF format)")
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel workers")
    args = parser.parse_args()

    print("=" * 60)
    print("BlueHive NoisePy Cross-Correlation (Batched / Local Data)")
    print("=" * 60)
    print(f"  Data dir:    {args.datadir}")
    print(f"  Pairs file:  {args.pairs}")
    print(f"  Date range:  {args.start} → {args.end}")
    print(f"  NCF output:  {args.ncfdir}")
    print(f"  Workers:     {args.workers}")
    print()

    # ==========================================
    # 1. Load pairs
    # ==========================================
    df_pairs = pd.read_csv(args.pairs)
    print(f"Loaded {len(df_pairs)} pairs to process.\n")

    os.makedirs(args.ncfdir, exist_ok=True)

    start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    config = build_config(start_date, end_date)
    config_path = os.path.join(args.ncfdir, "xcorr_config.yml")
    config.save_yaml(config_path)

    # ==========================================
    # 2. Build work items
    # ==========================================
    work_items = []
    for _, row in df_pairs.iterrows():
        work_items.append((
            row.to_dict(),
            args.datadir,
            args.ncfdir,
            args.start,
            args.end
        ))

    # ==========================================
    # 3. Process pairs
    # ==========================================
    t_total_start = time_module.time()

    if args.workers > 1:
        print(f"Starting multiprocessing with {args.workers} workers...\n")
        with Pool(processes=args.workers) as pool:
            results = pool.map(process_single_pair, work_items)
    else:
        print("Running sequentially (single worker)...\n")
        results = [process_single_pair(item) for item in work_items]

    total_time = time_module.time() - t_total_start

    # ==========================================
    # 4. Summary
    # ==========================================
    df_results = pd.DataFrame(results)
    summary_path = os.path.join(args.ncfdir, "xcorr_summary.csv")
    df_results.to_csv(summary_path, index=False)

    n_ok = (df_results['status'] == 'ok').sum()
    n_nodata = (df_results['status'] == 'no_data').sum()
    n_err = df_results['status'].str.startswith('error').sum()

    print(f"\n{'='*60}")
    print(f"CROSS-CORRELATION COMPLETE")
    print(f"{'='*60}")
    print(f"  Total pairs:    {len(df_pairs)}")
    print(f"  Succeeded:      {n_ok}")
    print(f"  No data:        {n_nodata}")
    print(f"  Errors:         {n_err}")
    print(f"  Total runtime:  {total_time/60:.1f} minutes")
    print(f"\nRaw MiniSEED data for successful pairs has been DELETED.")
    print(f"NCFs stored in: {args.ncfdir}/")


if __name__ == "__main__":
    main()
