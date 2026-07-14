#!/usr/bin/env python
"""
Cross-correlation worker module used by stream_xcorr_pipeline.py.

process_single_pair() preprocesses MiniSEED files in CONFIG["chunk_days"]-day chunks
to cap RAM, loads them into an InMemoryDataStore, runs NoisePy cross_correlate(),
and deletes raw files on success. Returns a result dict with status, n_timespans,
and runtime_sec.
"""



import os

#Cap to 1 thread per process to prevent multiple cores being fought over
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import re
import gc
import glob
import shutil
import time as time_module
import traceback
from typing import List
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor

from noisepy.seis import cross_correlate
from noisepy.seis.io.asdfstore import ASDFCCStore
from noisepy.seis.io.channelcatalog import XMLStationChannelCatalog
import obspy

from config import build_config, CONFIG
from preprocess import preprocess_pair_data, fetch_inventories
from datastore import InMemoryDataStore


def _collect_day_dates(sta_dir: str) -> List[str]:
    """Scans a station directory and returns all date tokens found in filenames in YYYY.DDD or YYYY-MM-DD format."""
    date_tokens = set()
    if not os.path.exists(sta_dir):
        return []
    pattern_julian = re.compile(r'\d{4}\.\d{3}')
    pattern_iso = re.compile(r'\d{4}-\d{2}-\d{2}')
    for root, dirs, files in os.walk(sta_dir):
        for f in files:
            for m in pattern_julian.finditer(f):
                date_tokens.add(m.group())
            for m in pattern_iso.finditer(f):
                date_tokens.add(m.group())
    return sorted(date_tokens)


def process_single_pair(args):
    """Preprocesses, cross-correlates, and cleans up raw data for one station pair. Returns a result dict."""
    chunk_days = CONFIG["chunk_days"]
    if len(args) == 8:
        pair_row, datadir, ncfdir, start_str, end_str, n_workers, force_reprocess, chunk_days = args
    elif len(args) == 7:
        pair_row, datadir, ncfdir, start_str, end_str, n_workers, force_reprocess = args
    else:
        pair_row, datadir, ncfdir, start_str, end_str, n_workers = args
        force_reprocess = False

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

    cc_path = os.path.join(ncfdir, pair_label)
    # Check if the cross-correlation data already exists
    if not force_reprocess and os.path.exists(cc_path):
        existing_h5 = glob.glob(os.path.join(cc_path, "*.h5"))
        if existing_h5:
            result['status'] = 'skipped (already exists)'
            result['n_timespans'] = len(existing_h5)
            result['runtime_sec'] = round(time_module.time() - t_start, 1)
            print(f"  {pair_label} — {len(existing_h5)} existing HDF5 files. Use --force to reprocess.")
            return result
    
    try:
        # Build config for Noisepy
        start_date = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        config = build_config(start_date, end_date)
        
        # Padding to ensure all stations are included
        PAD = 1.0
        config.lamin = min(pair_row['lat1'], pair_row['lat2']) - PAD
        config.lamax = max(pair_row['lat1'], pair_row['lat2']) + PAD
        config.lomin = min(pair_row['lon1'], pair_row['lon2']) - PAD
        config.lomax = max(pair_row['lon1'], pair_row['lon2']) + PAD
        config.networks = list(set([net1, net2]))
        config.stations = list(set([sta1, sta2]))
        # Autocorrelate if same station, otherwise cross-correlate
        if net1 == net2 and sta1 == sta2:
            config.acorr_only = True
            config.xcorr_only = False
        else:
            config.acorr_only = False
            config.xcorr_only = True

        sta1_dir = os.path.join(datadir, f"{net1}.{sta1}")
        sta2_dir = os.path.join(datadir, f"{net2}.{sta2}")
        # Fetch the inventory for each station
        station_pairs_list = [(net1, sta1), (net2, sta2)]
        inventories = fetch_inventories(
            station_pairs_list,
            cache_dir=os.path.join(ncfdir, "station_xml_cache"),
        )

        catalog_dir = os.path.join(ncfdir, "station_xml_cache")
        catalog = XMLStationChannelCatalog(catalog_dir)

        cc_path = os.path.join(ncfdir, pair_label)
        #Noisepy store that takes the cross correlation results and writes them to .h5 files on disk 
        cc_store = ASDFCCStore(cc_path)
        src_id = f"{net1}.{sta1}"
        rec_id = f"{net2}.{sta2}"
        # Curates all date tokens from both stations
        all_date_tokens = _collect_day_dates(sta1_dir)
        all_date_tokens = sorted(set(all_date_tokens) | set(_collect_day_dates(sta2_dir)))

        if not all_date_tokens:
            print(f"  Warning: could not extract date tokens from filenames — processing all files in one chunk.")
            all_date_tokens = [None]
            n_chunks = 1
        else:
            n_chunks = (len(all_date_tokens) + chunk_days - 1) // chunk_days
            print(f"  Date-chunked processing: {len(all_date_tokens)} days → {n_chunks} chunks of ≤{chunk_days} days")

        merged_preprocessed: dict = {}
        # Preprocess data in chunks to prevent memory errors
        pair_executor = ProcessPoolExecutor(max_workers=n_workers)
        try:
            for chunk_idx in range(n_chunks):
                if all_date_tokens == [None]:
                    date_subset = None
                else:
                    chunk_start = chunk_idx * chunk_days
                    chunk_end = chunk_start + chunk_days
                    date_subset = all_date_tokens[chunk_start:chunk_end]
                    print(f"  Chunk {chunk_idx+1}/{n_chunks}: {date_subset[0]} … {date_subset[-1]}")
                #Processes the data for the given date subset
                preprocessed = preprocess_pair_data(
                    sta1_dir, sta2_dir, inventories,
                    remove_response=True,
                    n_workers=n_workers,
                    date_subset=date_subset,
                    executor=pair_executor,
                )

                if not preprocessed or all(len(v) == 0 for v in preprocessed.values()):
                    print(f"    No usable data in this date range.")
                    del preprocessed
                    gc.collect()
                    continue
                # Merges the preprocessed data from the current chunk with the data from previous chunks
                for sta_id, windows in preprocessed.items():
                    if sta_id not in merged_preprocessed:
                        merged_preprocessed[sta_id] = {}
                    for day_iso, day_stream in windows.items():
                        if day_iso not in merged_preprocessed[sta_id]:
                            merged_preprocessed[sta_id][day_iso] = obspy.Stream()
                        for tr in day_stream:
                            merged_preprocessed[sta_id][day_iso].append(tr)
                del preprocessed
                gc.collect()
        finally:
            pair_executor.shutdown(wait=True)

        if not merged_preprocessed or all(len(v) == 0 for v in merged_preprocessed.values()):
            print(f"  No usable data across all chunks for {pair_label}.")
        else:
            # Stores the preprocessed data in memory as our custom DataStore
            raw_store = InMemoryDataStore(
                merged_preprocessed,
                catalog,
                timespan_seconds=86400,
                min_stations=1 if (net1 == net2 and sta1 == sta2) else 2,
                station_coords={
                    f"{net1}.{sta1}": (pair_row['lat1'], pair_row['lon1']),
                    f"{net2}.{sta2}": (pair_row['lat2'], pair_row['lon2']),
                },
            )
            n_blocks = sum(len(v) for v in merged_preprocessed.values())
            print(f"  DataStore: {n_blocks} station-window blocks total")
            if not raw_store.get_timespans():
                print(f"  No valid timespans after filtering — skipping xcorr.")
                for _sta_dir in [sta1_dir, sta2_dir]:
                    if os.path.exists(_sta_dir):
                        shutil.rmtree(_sta_dir)
                        print(f"    [CLEANUP] Deleted raw data for {_sta_dir}")
                result['status'] = 'no_data'
                result['runtime_sec'] = round(time_module.time() - t_start, 1)
                return result
            #Cross-correlates the data in the DataStore
            _t_xcorr = time_module.time()
            cross_correlate(raw_store, config, cc_store)
            _xcorr_sec = time_module.time() - _t_xcorr
            # Gets the timespans from the DataStore
            _ts_list = raw_store.get_timespans()
            _n_ts = len(_ts_list)
            _chan_counts = [len(raw_store.get_channels(ts)) for ts in _ts_list]
            _n_chan_max = max(_chan_counts) if _chan_counts else 0
            _n_ts_both = sum(1 for c in _chan_counts if c >= 6)
            _mem_str = ""
            # Prints the results of the cross-correlation
            try:
                import psutil as _psutil
                _mem_mb = _psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
                _mem_str = f" | RAM {_mem_mb:.0f} MB"
            except ImportError:
                pass
            print(
                f"  [NCF] {pair_label}"
                f" | timespans={_n_ts} (both-sta={_n_ts_both})"
                f" | ch_max={_n_chan_max}"
                f" | xcorr={_xcorr_sec:.1f}s"
                f"{_mem_str}"
            )

            del merged_preprocessed, raw_store
            gc.collect()

        timespans = cc_store.get_timespans(src_id, rec_id)
        result['n_timespans'] = len(timespans)
        # Delete raw data regardless of outcome — keeping failed pair data wastes GeoLab disk
        for sta_dir in [sta1_dir, sta2_dir]:
            if os.path.exists(sta_dir):
                shutil.rmtree(sta_dir)
                print(f"    [CLEANUP] Deleted raw data for {sta_dir}")
        if len(timespans) > 0:
            result['status'] = 'ok'
        else:
            result['status'] = 'no_data'

    except Exception as e:
        result['status'] = f'error: {str(e)[:200]}'
        traceback.print_exc()

    result['runtime_sec'] = round(time_module.time() - t_start, 1)
    print(f"  [{result['status']}] {pair_label} — {result['n_timespans']} timespans, {result['runtime_sec']}s")

    gc.collect()
    # Returns the result as a dictionary 
    return result
