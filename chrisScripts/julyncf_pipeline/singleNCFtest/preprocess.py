#!/usr/bin/env python
"""
Preprocessing pipeline for raw MiniSEED seismic data.

Steps applied to each trace:
1. Instrument response removal (output = displacement)
2. Demean and detrend
3. Decimation to 1 Hz (multi-stage, with anti-alias filter)
4. Demean and detrend again post-decimation

Entry points:
- preprocess_stream()      : process a single stream
- preprocess_pair_data()   : parallel processing of all files for a station pair
- fetch_inventories()      : download/cache StationXML from IRIS FDSN
"""
import os
import gc
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import obspy
from obspy import Stream, Trace, Inventory
from obspy.clients.fdsn import Client
from config import CONFIG
import numpy as np
from scipy.signal import butter, sosfiltfilt
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed

#cache so that the filter coefficients are not recomputed each time 
_sos_cache: dict = {}

def _get_sos(order: int, cutoff: float) -> np.ndarray:
    """ Function to get the second order sections of a Butterworth filter """
    key = (order, cutoff)
    if key not in _sos_cache:
        _sos_cache[key] = butter(order, cutoff, btype='low', output='sos')
    return _sos_cache[key]

# Cache for the high pass filter coefficents
_sos_hp_cache: dict = {}

def _get_sos_hp(order: int, cutoff: float) -> np.ndarray:
    """ Function to get the second order sections of a high pass Butterworth filter """
    key = (order, cutoff)
    if key not in _sos_hp_cache:
        _sos_hp_cache[key] = butter(order, cutoff, btype='high', output='sos')
    return _sos_hp_cache[key]




def safe_decimate(tr: Trace, target_sr: float = 1.0) -> Trace:
    """ Function to safely decimate a trace to a target sampling rate """ 
    while round(tr.stats.sampling_rate) > round(target_sr):
        current_sr = round(tr.stats.sampling_rate)
        factor = None
        # Decimate by the largest possible factor
        for f in [10, 8, 5, 4, 2]:
            if current_sr % f == 0 and (current_sr // f) >= round(target_sr):
                factor = f
                break
        if factor is None:
            factor = max(2, int(round(tr.stats.sampling_rate / target_sr)))
        # Low pass filter to avoid aliasing 
        cutoff_lp = (0.4 * target_sr) / (tr.stats.sampling_rate / 2.0)
        cutoff_lp = min(cutoff_lp, 0.999)
        sos_lp = _get_sos(8, cutoff_lp)
        tr.data = sosfiltfilt(sos_lp, tr.data).astype(tr.data.dtype)

        tr.decimate(factor, no_filter=True)
    # High pass filter to remove long period noise
    cutoff_hp = (1.0 / 3600.0) / (tr.stats.sampling_rate / 2.0)
    sos_hp = _get_sos_hp(4, cutoff_hp)
    tr.data = sosfiltfilt(sos_hp, tr.data).astype(tr.data.dtype)

    return tr


def _select_channel_group(st: Stream) -> Stream:
    """ Function to select the channel group """
    groups = defaultdict(list)
    for tr in st:
        groups[tr.stats.channel[:2]].append(tr)
    # Selects the channel group with 3 components and sampling rate >= 1Hz 
    candidates = [
        (prefix, traces)
        for prefix, traces in groups.items()
        if len({tr.stats.channel[2] for tr in traces}) == 3
        and all(round(tr.stats.sampling_rate) >= 1 for tr in traces)
    ]
    if not candidates:
        return st
    # Returns the best channel group 
    best_prefix, best_traces = min(candidates, key=lambda x: x[1][0].stats.sampling_rate)
    return Stream(best_traces)


def preprocess_trace(tr: Trace,inv: Optional[Inventory],remove_response: bool = True,) -> Optional[Trace]:
    """ Function to preprocess a trace """
    if (tr.stats.npts / tr.stats.sampling_rate) < 60:
        return None
    
    # Removes the response of the instrument
    if remove_response and inv is not None:
        try:

            tr_st = obspy.Stream([tr])
            tr_st.attach_response(inv)
            sr = tr.stats.sampling_rate
            nyq = sr / 2.0
            # remove response, zero mean, taper, pre_filt, water_level
            tr_st.remove_response(
                output="DISP",
                zero_mean=True,
                taper=True,
                taper_fraction=CONFIG["taper_fraction"],
                pre_filt=[0.001, 0.005, nyq * 0.80, nyq * 0.90],
                water_level=CONFIG["water_level"],
            )
            tr = tr_st[0]
        except Exception as e:
            print(f"    Warning: response removal failed for {tr.id}: {e}")
    

    tr.detrend('demean')
    tr.detrend('linear')

    # Checks if the trace needs to be decimated
    needs_decimate = round(tr.stats.sampling_rate) != round(CONFIG["sampling_rate"])
    if needs_decimate:
        safe_decimate(tr, target_sr=CONFIG["sampling_rate"])
        tr.detrend('demean')
        tr.detrend('linear')

    return tr


def preprocess_stream(st: Stream,inv: Optional[Inventory],remove_response: bool = True,) -> Stream:
    """ Function to preprocess a stream of traces """
    # Selects the channel group with 3 components and sampling rate >= 1Hz
    st = _select_channel_group(st)
    # Merges the stream
    st.merge(method=1, fill_value=0)
    processed = obspy.Stream()
    # Preprocesses each trace
    for tr in st:
        result = preprocess_trace(tr, inv, remove_response)
        if result is not None:
            processed.append(result)
    return processed


def _preprocess_file_worker(args):
    """ Function to preprocess a file """
    # Takes in arguments
    sta_id, inv, fp, remove_response_flag = args
    try:
        #Preprocess stream
        st = obspy.read(fp)
        processed = preprocess_stream(st, inv, remove_response_flag)
        if len(processed) == 0:
            return sta_id, None, None
        day_iso = processed[0].stats.starttime.date.isoformat() + "T00:00:00"
        #Return results as a tuple
        return sta_id, (day_iso, processed), None
    except Exception as e:
        return sta_id, None, str(e)


def preprocess_pair_data(
    sta1_dir: str,
    sta2_dir: str,
    inventories: Dict[str, Inventory],
    remove_response: bool = True,
    n_workers: int = None,
    date_subset: Optional[List[str]] = None,
    executor=None,
) -> Dict[str, Dict[str, Stream]]:
    """ Appends all files to be processed """
    # Get the cpu count as the worker count
    if n_workers is None:
        n_workers = os.cpu_count() or 4

    result_dict = {}

    #Get a set of dates to process
    date_tokens: Optional[set] = set(date_subset) if date_subset is not None else None

    all_files = []
    for s_dir in [sta1_dir, sta2_dir]:
        if not os.path.exists(s_dir):
            continue
        #Extract station ID from the directory name 
        sta_id = os.path.basename(s_dir) 
        # Gets the inventory for the station from inventory Dict 
        inv = inventories.get(sta_id)
        if inv is not None and '.' in sta_id:
            # Splits the station ID into network and station 
            _net, _sta = sta_id.split('.', 1)
            try:
                pruned = inv.select(network=_net, station=_sta)
                if len(pruned.networks) > 0:
                    inv = pruned
            except Exception:
                pass
        # Walks through the directory and gets all the files 
        for root, dirs, files in os.walk(s_dir):
            for f in files:
                # Checks to see if the file needs to be processed based on the date tokens
                if date_tokens is not None:
                    if not any(tok in f for tok in date_tokens):
                        continue
                # Appends the file to the list of files to be processed
                all_files.append((sta_id, inv, os.path.join(root, f), remove_response))

    print(f"  Preprocessing {len(all_files)} raw MiniSEED files using {n_workers} processes...")
    
    # Actual submission of jobs and recieving the results
    def _run_futures(ex):
        """ Submits, collects, and processes results from the preprocess_file_worker function to the executor"""
        futures = {ex.submit(_preprocess_file_worker, args): args for args in all_files}
        for future in tqdm(as_completed(futures), total=len(all_files), desc="  Preprocessing", leave=False):
            sta_id, result, err = future.result()
            if err:
                print(f"    Skipping file for {sta_id}: {err}")
                continue
            if result is None:
                continue
            day_iso, day_stream = result
            if sta_id not in result_dict:
                result_dict[sta_id] = {}
            if day_iso not in result_dict[sta_id]:
                result_dict[sta_id][day_iso] = obspy.Stream()
            for tr in day_stream:
                result_dict[sta_id][day_iso].append(tr)

    if executor is not None:
        _run_futures(executor)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as _ex:
            _run_futures(_ex)

    gc.collect()
    return result_dict


def fetch_inventories(
    station_pairs: List[Tuple[str, str]],
    cache_dir: str,
) -> Dict[str, Inventory]:
    """ Fetches the inventory for each station in the station pairs """
    os.makedirs(cache_dir, exist_ok=True)
    fdsn_client = Client("IRIS")
    inventories = {}
    for net, sta in set(station_pairs):
        inv_path = os.path.join(cache_dir, f"{net}_{sta}.xml")
        try:
            if os.path.exists(inv_path):
                inv = obspy.read_inventory(inv_path)
            else:
                print(f"  Downloading StationXML for {net}.{sta}...")
                inv = fdsn_client.get_stations(
                    network=net, station=sta, level="response"
                )
                inv.write(inv_path, format="STATIONXML")
            inventories[f"{net}.{sta}"] = inv
        except Exception as e:
            print(f"  Warning: Could not fetch StationXML for {net}.{sta}: {e}")
    return inventories
