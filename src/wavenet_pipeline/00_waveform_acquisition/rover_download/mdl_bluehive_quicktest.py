#!/usr/bin/env python
"""
Real Bluehive-scale end-to-end Stage 2->4 test, one station per SLURM array task:
download (ObsPy MassDownloader) -> preprocess (detrend/response-removal/filter/decimate)
-> package (numpy-direct HDF5) -> discard raw SEED.

This exact script (paths included) is what was run to produce the confirmed results in
docs/ncf_pipeline_stages/stage_2/3/4 (single-station and 20-task SLURM array runs,
2026-09-23) -- kept as-run for reproducibility. The `ROOT`/`STATIONS_CSV` paths below
are specific to that verification run on Bluehive's `/scratch/tolugboj_lab/` -- adapt
them (and the SLURM script, `mdl_bluehive_quicktest.slurm`, alongside this file) to a
different scratch location for a new run.

Adapted from PrjXX_SAmericaNoise's proven download_missing_data_slurm_script.py pattern
(per-station domain instead of one shared regional RectangularDomain, since our fixed
2000-station set is global, not regional). Does not touch PrjXX_SAmericaNoise's own
paths/data -- writes to a separate scratch dir.

Design principles applied (docs/ncf_pipeline_stages/PROGRESS.md, 2026-09-23):
  - Raw miniSEED is transient in the eventual PRODUCTION pipeline, but kept during this
    verification phase (KEEP_RAW_SEED_FOR_DEBUGGING=True below) -- re-fetching is
    costly, and keeping raw input allows debugging without a re-download. Flip to False
    only once the pipeline is proven solid.
  - Packaging is numpy-direct HDF5 (h5py), no SAC round-trip.
  - One HDF5 file per array task (per station) -- merged into a single growable master
    file afterward by build_master_h5.py (serial append, not concurrent multi-writer;
    see docs/ncf_pipeline_stages/hdf5_schema.md for why).
  - Channel selection: BH?/LH? only (excludes HN accelerometer channels -- not useful
    for ambient-noise cross-correlation), LH? preferred over BH? when both exist at the
    same location code (LH is already at the 1Hz target rate; BH would need decimating
    from a much higher native rate, confirmed ~2.7-3x more expensive in both download
    volume and preprocessing time -- see stage_2_download_execution.md).

Real confirmed results from this exact script (2026-09-23, see stage docs for full
detail): ~5-6s/channel-day preprocessing when LH is available, ~16s/channel-day when
only BH exists, ~323 KB/channel-day final packaged storage regardless of channel type,
linear scaling confirmed from 1-way to 20-way concurrency on Bluehive's `urseismo`
partition (no provider rate-limiting or filesystem contention observed at that scale).
"""
import os
import sys
import time
import json
import shutil

import h5py
import numpy as np
import pandas as pd
from obspy import UTCDateTime, read, read_inventory
from obspy.clients.fdsn.mass_downloader import Restrictions, MassDownloader, RectangularDomain

STATIONS_CSV = "/scratch/tolugboj_lab/wavenet_ncf_quicktest/bluehive_test_20stations.csv"
ROOT = "/scratch/tolugboj_lab/wavenet_ncf_quicktest"
RESULT_DIR = os.path.join(ROOT, "results")
H5_DIR = os.path.join(ROOT, "packaged_h5")
os.makedirs(RESULT_DIR, exist_ok=True)
os.makedirs(H5_DIR, exist_ok=True)

idx = int(sys.argv[1])
sta = pd.read_csv(STATIONS_CSV).iloc[idx]
network, station, lat, lon = sta["network"], sta["station"], sta["lat"], sta["lon"]

work_dir = os.path.join(ROOT, "scratch_work", f"{network}_{station}")
mseed_dir = os.path.join(work_dir, "mseed")
xml_dir = os.path.join(work_dir, "stationxml")
os.makedirs(mseed_dir, exist_ok=True)
os.makedirs(xml_dir, exist_ok=True)

result = dict(idx=idx, network=network, station=station,
              download_ok=False, preprocess_ok=False, package_ok=False)

# ---- Stage 2: download ----
t0 = time.time()
try:
    domain = RectangularDomain(minlatitude=lat - 0.5, maxlatitude=lat + 0.5,
                                minlongitude=lon - 0.5, maxlongitude=lon + 0.5)
    restrictions = Restrictions(
        starttime=UTCDateTime(2018, 1, 1), endtime=UTCDateTime(2018, 1, 8),  # 1 week (PI, 2026-09-23): enough to exercise day-boundary/gap handling without years-scale waste
        chunklength_in_sec=86400, network=network, station=station,
        channel="BH?,LH?",  # PI (2026-09-23): restrict to BH?/LH? only -- exclude HN (accelerometer, strong-motion) channels, not useful for ambient-noise cross-correlation and wasted download/preprocess cost
        reject_channels_with_gaps=False, minimum_length=0.0,
        channel_priorities=["LH?", "BH?"],  # PI (2026-09-23): prefer LH when both exist (already 1Hz, no wasted bandwidth decimating BH down to the same rate) -- wildcard "?" not "[ZNE]", since some stations use numbered orientation codes (BH1/BH2/BH3) instead of Z/N/E, and "[ZNE]" would silently skip those
        location_priorities=["", "00", "10"],
    )
    mdl = MassDownloader()
    mdl.download(domain, restrictions, mseed_storage=mseed_dir, stationxml_storage=xml_dir)
    mseed_files = [os.path.join(mseed_dir, f) for f in os.listdir(mseed_dir)]
    xml_files = [os.path.join(xml_dir, f) for f in os.listdir(xml_dir)]
    download_bytes = sum(os.path.getsize(f) for f in mseed_files)
    result.update(download_ok=len(mseed_files) > 0, download_bytes=download_bytes,
                   download_elapsed_s=time.time() - t0, n_channels=len(mseed_files))
except Exception as e:
    result.update(download_elapsed_s=time.time() - t0, download_error=f"{type(e).__name__}: {e}")

# ---- Stage 3: preprocess (real instrument-response removal now possible via StationXML) ----
# Read ALL downloaded files (one per channel per day) into one combined Stream first,
# then merge same-channel traces across days into one continuous week-long trace --
# this is what actually exercises day-boundary/gap handling, not per-day processing.
t0 = time.time()
processed = []
if result["download_ok"]:
    try:
        inv = read_inventory(os.path.join(xml_dir, "*.xml")) if xml_files else None
        combined = None
        for mf in mseed_files:
            st_part = read(mf)
            combined = st_part if combined is None else combined + st_part
        combined.merge(fill_value=0)
        for tr in combined:
            tr.detrend("linear")
            tr.detrend("demean")
            response_removed = False
            azimuth, dip = None, None
            if inv is not None:
                try:
                    tr.remove_response(inventory=inv, output="DISP", water_level=60,
                                        pre_filt=(0.001, 0.005, 0.4, 0.5))
                    response_removed = True
                except Exception:
                    pass  # fall back to raw counts if response lookup fails for this channel -- tracked via response_removed, not silently assumed
                try:
                    # orientation metadata (PI, 2026-09-23): needed for future rotation
                    # of numbered-orientation channels (BH1/BH2 etc, not aligned to true
                    # N/E) -- must capture now, since the source StationXML is discarded
                    # once the pipeline is proven solid (KEEP_RAW_SEED_FOR_DEBUGGING).
                    # Uses the metadata epoch active at this trace's start time -- if a
                    # station's sensor was reoriented mid-history, a dataset spanning
                    # multiple epochs would need per-epoch tracking, not handled here (v1).
                    meta = inv.get_channel_metadata(tr.id, tr.stats.starttime)
                    azimuth, dip = meta["azimuth"], meta["dip"]
                except Exception:
                    pass  # leave as None -- tracked explicitly, not silently defaulted to 0
            # Guard against channels whose native rate is already at or below the
            # target 0.4 Hz corner (Nyquist = sampling_rate/2) -- found by direct
            # testing (2026-09-23, a real archive on atos): a fixed 0.4 Hz lowpass
            # crashes scipy's iirfilter on any channel already at or below 0.8 Hz.
            # Hasn't been triggered by our own BH?/LH? restriction yet, but fixed here
            # too for consistency with preprocess_existing_archive.py.
            if tr.stats.sampling_rate > 0.8:
                tr.filter("lowpass", freq=0.4, corners=4, zerophase=True)
            tr.filter("highpass", freq=1.0 / 3600.0, corners=4, zerophase=True)
            if tr.stats.sampling_rate >= 2:
                tr.decimate(factor=int(round(tr.stats.sampling_rate)), no_filter=True)
            tr.detrend("demean")
            tr.taper(max_percentage=0.05)
            processed.append(dict(channel=tr.stats.channel, data=tr.data.astype(np.float32),
                                   sampling_rate=tr.stats.sampling_rate, starttime=str(tr.stats.starttime),
                                   response_removed=response_removed, azimuth=azimuth, dip=dip))
        result.update(preprocess_ok=len(processed) > 0, preprocess_elapsed_s=time.time() - t0,
                       n_processed=len(processed))
    except Exception as e:
        result.update(preprocess_elapsed_s=time.time() - t0, preprocess_error=f"{type(e).__name__}: {e}")

# ---- Stage 4: package (numpy-direct HDF5, no SAC round-trip) ----
t0 = time.time()
if result["preprocess_ok"]:
    try:
        h5_path = os.path.join(H5_DIR, f"{network}.{station}.h5")
        with h5py.File(h5_path, "w") as f:
            grp = f.require_group(f"{network}.{station}")
            grp.attrs["network"] = network
            grp.attrs["station"] = station
            grp.attrs["latitude"] = float(lat)
            grp.attrs["longitude"] = float(lon)
            # Full raw StationXML, stored ONCE per station (PI, 2026-09-23) -- not just
            # the few fields (azimuth/dip) pulled out below. Cherry-picking fields is
            # fragile against future, not-yet-known needs; the whole file is tiny
            # (~0.1 MB) next to the waveform data, so there's no real cost to keeping
            # everything. This is what makes discarding the raw scratch_work/ directory
            # safe later -- nothing StationXML-derived is lost.
            if xml_files:
                with open(xml_files[0], "rb") as xf:
                    grp.create_dataset("_stationxml_raw", data=np.void(xf.read()))
            for p in processed:
                ds = grp.create_dataset(p["channel"], data=p["data"], compression="gzip", compression_opts=4)
                ds.attrs["sampling_rate"] = p["sampling_rate"]
                ds.attrs["start_time"] = p["starttime"]  # matches docs/ncf_pipeline_stages/hdf5_schema.md
                ds.attrs["units"] = "m" if p.get("response_removed") else "counts"
                if p.get("azimuth") is not None:
                    ds.attrs["azimuth"] = p["azimuth"]
                if p.get("dip") is not None:
                    ds.attrs["dip"] = p["dip"]
        result.update(package_ok=True, package_elapsed_s=time.time() - t0,
                       h5_size_bytes=os.path.getsize(h5_path))
    except Exception as e:
        result.update(package_elapsed_s=time.time() - t0, package_error=f"{type(e).__name__}: {e}")

# ---- Discard raw SEED -- DISABLED during verification phase (PI, 2026-09-23): keep
# raw SEED + StationXML around until the pipeline is proven solid, since re-fetching is
# costly (time/provider load) and having the raw input makes debugging preprocess/
# package issues possible without a re-download. Re-enable (uncomment) once verified.
KEEP_RAW_SEED_FOR_DEBUGGING = True
if not KEEP_RAW_SEED_FOR_DEBUGGING:
    shutil.rmtree(work_dir, ignore_errors=True)
else:
    result["raw_seed_kept_at"] = work_dir

with open(os.path.join(RESULT_DIR, f"result_{idx:02d}_{network}_{station}.json"), "w") as f:
    json.dump(result, f)
print(json.dumps(result))
