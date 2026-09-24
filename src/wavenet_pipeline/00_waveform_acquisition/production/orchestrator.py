#!/usr/bin/env python
"""
Orchestrator: one SLURM array task = one station's full download -> preprocess ->
package pipeline. This is the "actor" the master (master.py) fans out across the fixed
2,000-station network (metadata3/fps_stations.csv, locked -- never regenerate).

Pipeline logic (download restrictions, response removal, Nyquist guard, HDF5 schema) is
the exact verified logic from ../rover_download/mdl_bluehive_quicktest.py (confirmed
correct against ADAMA's independent reference, correlation 0.9523 -- see
docs/ncf_pipeline_stages/hdf5_schema.md). This file generalizes it: configurable
root/manifest instead of hardcoded paths, zero-padded idx for a 4-digit station count,
and idempotent skip-if-already-packaged so a re-submitted array (crash recovery, adding
stragglers) doesn't redo finished work.

Env vars (set by orchestrator.slurm, not hardcoded, so the same script serves both a
small sanity-check root and the eventual full-2000 production root):
    WAVENET_PROD_ROOT     scratch root, e.g. /scratch/tolugboj_lab/wavenet_ncf_production
    WAVENET_STATIONS_CSV  path to the station manifest (defaults to ROOT/manifest/fps_stations.csv)

Download window (PI, 2026-09-24): full available history for every station, not a
connectivity-gated subset -- "the goal is to get all the data." DOWNLOAD_START/END below
span the full plausible modern seismic-archive era; MassDownloader only fetches what
actually exists per station within that window, so this needs no per-station lookup
against the key index. Channel selection (BH?/LH?) is unchanged from verification.
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

ROOT = os.environ["WAVENET_PROD_ROOT"]
STATIONS_CSV = os.environ.get("WAVENET_STATIONS_CSV", os.path.join(ROOT, "manifest", "fps_stations.csv"))

# Full available history (PI, 2026-09-24) -- 1970 predates any station in the real key
# index (earliest confirmed start 1982, station_summary.csv), so this is a safe lower
# bound, not a guess. Upper bound is capped to the END of yesterday, not the literal
# current instant (PI, 2026-09-24) -- avoids requesting a partial "today" that most
# providers haven't finished ingesting/replicating yet, which would just come back empty
# or truncated rather than actually getting today's data.
DOWNLOAD_START = UTCDateTime(1970, 1, 1)
DOWNLOAD_END = UTCDateTime(UTCDateTime.now().date) - 1  # midnight today, minus 1s -> end of yesterday

# Discard raw SEED after packaging is NOT done here -- that's the inspector's job, gated
# on verifying the merged master-h5 copy is good (docs/ncf_pipeline_stages/PROGRESS.md's
# "discard only once proven solid" principle, applied per-station instead of globally).

RESULT_DIR = os.path.join(ROOT, "results")
H5_DIR = os.path.join(ROOT, "packaged_h5")
os.makedirs(RESULT_DIR, exist_ok=True)
os.makedirs(H5_DIR, exist_ok=True)

# SLURM's MaxArraySize (1001, confirmed 2026-09-24) caps the max array INDEX, not the
# task count -- a chunk whose global manifest rows start above that (e.g. idx 1131-1999)
# can't be submitted directly as $SLURM_ARRAY_TASK_ID. master.py works around this by
# submitting such chunks REBASED to 0-(chunk_size-1) and passing the true starting row
# via WAVENET_IDX_OFFSET; chunks that already fit under the cap just get offset=0.
idx = int(sys.argv[1]) + int(os.environ.get("WAVENET_IDX_OFFSET", 0))
manifest = pd.read_csv(STATIONS_CSV)
sta = manifest.iloc[idx]
network, station, lat, lon = sta["network"], sta["station"], sta["lat"], sta["lon"]

result_path = os.path.join(RESULT_DIR, f"{idx:04d}_{network}_{station}.json")

# Idempotent resume: a prior run that already packaged this station successfully is not
# redone -- lets master.py re-submit a partial/failed array range without re-downloading
# everything that already succeeded.
if os.path.exists(result_path):
    with open(result_path) as f:
        prior = json.load(f)
    if prior.get("package_ok"):
        print(json.dumps({**prior, "skipped_already_done": True}))
        sys.exit(0)

work_dir = os.path.join(ROOT, "scratch_work", f"{network}_{station}")
mseed_dir = os.path.join(work_dir, "mseed")
xml_dir = os.path.join(work_dir, "stationxml")
os.makedirs(mseed_dir, exist_ok=True)
os.makedirs(xml_dir, exist_ok=True)

result = dict(idx=idx, network=network, station=station,
              download_ok=False, preprocess_ok=False, package_ok=False)

# ---- download ----
# Chunked by YEAR, not one single call spanning the whole DOWNLOAD_START-DOWNLOAD_END
# range. Found by direct testing (2026-09-24, real production run on all 2,000
# stations): a single MassDownloader call spanning many decades reproducibly triggers
# an internal ObsPy bug when it queries IRIS's availability endpoint over that wide a
# span (`TypeError: sequence item 0: expected str instance, tuple found`) -- sometimes
# raised (crashing the whole station), sometimes silently swallowed inside ObsPy and
# reported as "IRIS has no data" (a SILENT data-loss failure mode, not just a crash).
# A real full-scale launch hit 0% success across ~800 completed stations before this
# was caught. Confirmed fix by direct re-test: three separate single-year windows
# (1988, 2000, 2010) against the same station that crashed on the full span all
# succeeded cleanly, zero TypeErrors -- every prior verification test (1-week windows)
# already proved a narrow span is safe; year-chunking just applies that same proven
# scope to the new full-history requirement instead of one all-or-nothing request.
t0 = time.time()
year_errors = {}
try:
    domain = RectangularDomain(minlatitude=lat - 0.5, maxlatitude=lat + 0.5,
                                minlongitude=lon - 0.5, maxlongitude=lon + 0.5)
    mdl = MassDownloader()  # one client-discovery pass reused across all years below
    for year in range(DOWNLOAD_START.year, DOWNLOAD_END.year + 1):
        year_start = max(DOWNLOAD_START, UTCDateTime(year, 1, 1))
        year_end = min(DOWNLOAD_END, UTCDateTime(year, 12, 31, 23, 59, 59))
        if year_start > year_end:
            continue
        restrictions = Restrictions(
            starttime=year_start, endtime=year_end,
            chunklength_in_sec=86400, network=network, station=station,
            channel="BH?,LH?",
            reject_channels_with_gaps=False, minimum_length=0.0,
            channel_priorities=["LH?", "BH?"],
            location_priorities=["", "00", "10"],
        )
        try:
            mdl.download(domain, restrictions, mseed_storage=mseed_dir, stationxml_storage=xml_dir)
        except Exception as ye:
            # One bad year doesn't sink the whole station -- keep whatever other years
            # succeeded. Recorded, not silently dropped (see result["year_errors"]).
            year_errors[year] = f"{type(ye).__name__}: {ye}"
    mseed_files = [os.path.join(mseed_dir, f) for f in os.listdir(mseed_dir)]
    xml_files = [os.path.join(xml_dir, f) for f in os.listdir(xml_dir)]
    download_bytes = sum(os.path.getsize(f) for f in mseed_files)
    result.update(download_ok=len(mseed_files) > 0, download_bytes=download_bytes,
                   download_elapsed_s=time.time() - t0, n_channels=len(mseed_files))
    if year_errors:
        result["year_errors"] = year_errors
except Exception as e:
    result.update(download_elapsed_s=time.time() - t0, download_error=f"{type(e).__name__}: {e}")

# ---- preprocess ----
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
                    pass
                try:
                    meta = inv.get_channel_metadata(tr.id, tr.stats.starttime)
                    azimuth, dip = meta["azimuth"], meta["dip"]
                except Exception:
                    pass
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

# ---- package ----
t0 = time.time()
if result["preprocess_ok"]:
    try:
        h5_path = os.path.join(H5_DIR, f"{network}.{station}.h5")
        channel_meta = {}
        with h5py.File(h5_path, "w") as f:
            grp = f.require_group(f"{network}.{station}")
            grp.attrs["network"] = network
            grp.attrs["station"] = station
            grp.attrs["latitude"] = float(lat)
            grp.attrs["longitude"] = float(lon)
            if xml_files:
                with open(xml_files[0], "rb") as xf:
                    grp.create_dataset("_stationxml_raw", data=np.void(xf.read()))
            for p in processed:
                ds = grp.create_dataset(p["channel"], data=p["data"], compression="gzip", compression_opts=4)
                ds.attrs["sampling_rate"] = p["sampling_rate"]
                ds.attrs["start_time"] = p["starttime"]
                ds.attrs["units"] = "m" if p.get("response_removed") else "counts"
                if p.get("azimuth") is not None:
                    ds.attrs["azimuth"] = p["azimuth"]
                if p.get("dip") is not None:
                    ds.attrs["dip"] = p["dip"]
                # Recorded so the inspector can cross-check the master-h5 copy's length
                # against what THIS shard actually wrote, without needing to keep the
                # per-station shard file around after the logger merges it.
                channel_meta[p["channel"]] = dict(n_samples=int(len(p["data"])),
                                                    sampling_rate=p["sampling_rate"])
        result.update(package_ok=True, package_elapsed_s=time.time() - t0,
                       h5_size_bytes=os.path.getsize(h5_path), channel_meta=channel_meta,
                       packaged_h5_path=h5_path)
    except Exception as e:
        result.update(package_elapsed_s=time.time() - t0, package_error=f"{type(e).__name__}: {e}")

# Raw SEED is deliberately NOT purged here -- the inspector purges it once it has
# independently confirmed the master-h5 copy is intact (docs/ncf_pipeline_stages/
# PROGRESS.md's discard-after-proof principle, applied per-station).
result["raw_seed_kept_at"] = work_dir

with open(result_path, "w") as f:
    json.dump(result, f)
print(json.dumps(result))
