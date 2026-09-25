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
are the outer bound (the full plausible modern seismic-archive era); the actual
per-station year LOOP is bounded to that station's own real deployment span from the
key index (station_summary.csv), not the full 1970-2026 range for every station --
see "download" section below for why this was a real, not theoretical, fix. Channel
selection (BH?/LH?) is unchanged from verification.
"""
import os
import sys
import time
import json
from collections import defaultdict

import h5py
import numpy as np
import pandas as pd
from obspy import UTCDateTime, read, read_inventory
from obspy.clients.fdsn.mass_downloader import Restrictions, MassDownloader, RectangularDomain

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rover_download"))
from build_master_h5 import append_channel_data, align_to_integer_second  # shared gap-fill/append logic

ROOT = os.environ["WAVENET_PROD_ROOT"]
STATIONS_CSV = os.environ.get("WAVENET_STATIONS_CSV", os.path.join(ROOT, "manifest", "fps_stations.csv"))

# Full available history (PI, 2026-09-24) -- 1970 predates any station in the real key
# index (earliest confirmed start 1982, station_summary.csv), so this is a safe lower
# bound, not a guess. Upper bound is capped to the END of yesterday, not the literal
# current instant (PI, 2026-09-24) -- avoids requesting a partial "today" that most
# providers haven't finished ingesting/replicating yet, which would just come back empty
# or truncated rather than actually getting today's data.
DOWNLOAD_START = UTCDateTime(os.environ.get("WAVENET_START", "1970-01-01"))
DOWNLOAD_END = (UTCDateTime(os.environ["WAVENET_END"]) if "WAVENET_END" in os.environ
                else UTCDateTime(UTCDateTime.now().date) - 1)  # default: midnight today, minus 1s -> end of yesterday

# [PATCH 6] Per-run overrides (defaults reproduce the previous hard-coded behaviour exactly):
#   WAVENET_START / WAVENET_END      download + processing window (ISO date/time)
#   WAVENET_CHANNELS                 obspy channel selector, default "BH?,LH?"
#   WAVENET_CHANNEL_PRIORITIES       comma list, default "LH?,BH?"
#   WAVENET_SKIP_DOWNLOAD=1          reprocess raw files already in scratch_work/ (no network)
#   WAVENET_DAY_START / _DAY_END     'YYYYMMDD' inclusive; process only these days (parallel date-range chunks)
#   WAVENET_TAPER_PCT                per-end taper fraction applied to every processed day, default 0.05
CHANNELS = os.environ.get("WAVENET_CHANNELS", "BH?,LH?")
CHANNEL_PRIORITIES = os.environ.get("WAVENET_CHANNEL_PRIORITIES", "LH?,BH?").split(",")
SKIP_DOWNLOAD = os.environ.get("WAVENET_SKIP_DOWNLOAD", "0") == "1"
DAY_START = os.environ.get("WAVENET_DAY_START")
DAY_END = os.environ.get("WAVENET_DAY_END")
TAPER_PCT = float(os.environ.get("WAVENET_TAPER_PCT", "0.05"))


def _raw_qc(x, n_segments):
    """[PATCH 3] Non-destructive per-channel-day quality flags from the RAW counts (nothing is altered or removed).
    Flag = int32-saturated, or heavy-tailed (RMS / robust sigma > 30; quiet days were <= ~9, corrupt days > 100
    on XD.MTAN / XD.RUNG), or the day contains more than one record segment (gap / recorder restart)."""
    x = np.asarray(x, dtype=np.float64)
    med = np.median(x)
    robust = float(np.median(np.abs(x - med)) * 1.4826 + 1e-12)
    rms, mx = float(np.sqrt(np.mean(x ** 2))), float(np.max(np.abs(x)))
    ratio, saturated = rms / robust, bool(mx >= 0.999 * 2 ** 31)
    return dict(rms=rms, robust_sigma=robust, max_abs=mx, rms_over_robust_sigma=float(ratio),
                saturated=saturated, n_segments=int(n_segments),
                flag=bool(saturated or ratio > 30 or n_segments > 1))


_UNIT_SCALE = {"M/S": ("m", 1.0), "M": ("m", 1.0), "NM/S": ("m", 1e-9), "NM": ("m", 1e-9),
               "MM/S": ("m", 1e-3), "UM/S": ("m", 1e-6)}


def _disp_units(inv, tr):
    """[PATCH 4] remove_response(output='DISP') returns displacement in the response's OWN length unit (input
    NM/S -> nanometres) and never converts to metres. Return (label, scale) so the packaged data really are
    metres; an unrecognised/unreadable unit is labelled as such and left unscaled instead of being called 'm'."""
    try:
        u = inv.get_response(tr.id, tr.stats.starttime).instrument_sensitivity.input_units.upper()
    except Exception:
        return "unknown (response input units unreadable)", 1.0
    return _UNIT_SCALE.get(u, (f"displacement[{u}-derived, not rescaled]", 1.0))

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

# Per-station year bound, not the full 1970-2026 range for every station. Found by
# direct evidence, not guessing (2026-09-24, real canary run): a full 56-year loop for
# EVERY station regardless of actual deployment span means a station that really only
# ever recorded 1-2 years still pays 56 rounds of multi-provider availability
# negotiation -- the real observed download rate on the canary run was ~40x slower
# than earlier assumed, and the average real station year-span in the key index is only
# 5.55 years (station_summary.csv, 1984 stations with a valid range) vs the 56-year
# loop every station was actually running -- a ~10x average overhead multiplier, worse
# for short-deployment stations (all but 2 of 1984 span under 50 years). A missing
# summary entry (shouldn't happen for the locked 2,000-network, but not assumed) falls
# back to the full DOWNLOAD_START/END range rather than skipping the station.
STATION_SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "..", "metadata3",
                                     "key_index_summary", "station_summary.csv")
station_year_start, station_year_end = None, None
if os.path.exists(STATION_SUMMARY_PATH):
    summary = pd.read_csv(STATION_SUMMARY_PATH)
    match = summary[(summary["network"] == network) & (summary["station"] == station)]
    if len(match) and pd.notna(match.iloc[0].get("year_min")) and pd.notna(match.iloc[0].get("year_max")):
        # +/-1 year margin -- the key index's year boundaries come from an S3 prefix
        # scan, not a guaranteed-exact deployment date, so a one-year buffer avoids
        # clipping a station whose first/last real byte lands right at a year edge.
        station_year_start = int(match.iloc[0]["year_min"]) - 1
        station_year_end = int(match.iloc[0]["year_max"]) + 1

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
    mdl = None if SKIP_DOWNLOAD else MassDownloader()  # one client-discovery pass reused across all years below
    loop_year_start = station_year_start if station_year_start is not None else DOWNLOAD_START.year
    loop_year_end = station_year_end if station_year_end is not None else DOWNLOAD_END.year
    for year in ([] if SKIP_DOWNLOAD else range(loop_year_start, loop_year_end + 1)):
        year_start = max(DOWNLOAD_START, UTCDateTime(year, 1, 1))
        year_end = min(DOWNLOAD_END, UTCDateTime(year, 12, 31, 23, 59, 59))
        if year_start > year_end:
            continue
        restrictions = Restrictions(
            starttime=year_start, endtime=year_end,
            chunklength_in_sec=86400, network=network, station=station,
            channel=CHANNELS,
            reject_channels_with_gaps=False, minimum_length=0.0,
            channel_priorities=CHANNEL_PRIORITIES,
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
                   download_elapsed_s=time.time() - t0, n_channels=len(mseed_files),
                   download_year_range=[loop_year_start, loop_year_end])
    if year_errors:
        result["year_errors"] = year_errors
except Exception as e:
    result.update(download_elapsed_s=time.time() - t0, download_error=f"{type(e).__name__}: {e}")

# ---- preprocess + package, ONE CALENDAR DAY AT A TIME, CHECKPOINTED ----
# Rewritten 2026-09-24 after a real OOM finding on the exact station this bug hit
# hardest: reading + merging under half a year of this station's data into one
# in-memory Stream (the previous design, matching mdl_bluehive_quicktest.py) used 7.3GB
# RSS and got OOM-killed at an 8GB ceiling before processing even started -- and that
# was well short of a full year, let alone full history. Full-history-per-station (this
# pipeline's actual requirement as of 2026-09-24) makes the old whole-station-at-once
# design untenable regardless of how much memory it's given.
#
# PI decision (2026-09-24): day-by-day, matching the already-proven-safe design from
# preprocess_existing_archive.py (551MB RSS regardless of history length, confirmed on
# a real 27-year archive, because it never holds more than one day's data across all
# channels at once) -- "compute time is expensive, this should be robust, even though
# it means more read-write time... it is what it is." Each day is detrended/response-
# removed/filtered/decimated/tapered on its own, then appended DIRECTLY into the
# per-station HDF5 shard via append_channel_data() (the same gap-fill/append logic
# logger.py already uses to merge shards into the master file -- reused, not
# reimplemented). A day-completion checkpoint file tracks which days are already in the
# shard, so a crash/timeout/preemption loses at most the single day in flight, not the
# whole station -- and a resumed run skips straight to where it left off instead of
# reprocessing from scratch.
t0 = time.time()
n_days_processed = 0
n_days_refused = 0      # [PATCH 2]
day_errors = {}
day_notes = {}          # [PATCH 1b/2] benign statuses (e.g. SKIPPED = already stored), kept apart from real errors
if result["download_ok"]:
    h5_path = os.path.join(H5_DIR, f"{network}.{station}.h5")
    day_state_path = h5_path + ".daystate.json"
    done_days = set()
    if os.path.exists(day_state_path):
        with open(day_state_path) as f:
            done_days = set(json.load(f))

    def save_day_state():
        tmp = day_state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(done_days), f)
        os.replace(tmp, day_state_path)  # atomic -- a resumed run never sees a partial list

    # Filenames are NET.STA.LOC.CHAN__STARTISO__ENDISO.mseed (MassDownloader's own
    # naming, chunklength_in_sec=86400 in the Restrictions above means one file per
    # channel per calendar day already) -- group by the calendar day encoded in the
    # start timestamp so each group is exactly one day's files across all channels.
    files_by_day = defaultdict(list)
    for mf in mseed_files:
        base = os.path.basename(mf)
        parts = base.split("__")
        if len(parts) >= 2 and len(parts[1]) >= 8:
            files_by_day[parts[1][:8]].append(mf)

    if DAY_START or DAY_END:                      # [PATCH 6] parallel date-range chunks
        files_by_day = defaultdict(list, {k: v for k, v in files_by_day.items()
                                          if (not DAY_START or k >= DAY_START) and (not DAY_END or k <= DAY_END)})

    # [PATCH 3] per-day raw-quality sidecar (JSON next to the shard), same atomic pattern as daystate
    qc_path = h5_path + ".qc.json"
    qc_all = json.load(open(qc_path)) if os.path.exists(qc_path) else {}

    def save_qc():
        tmp = qc_path + ".tmp"
        with open(tmp, "w") as fq:
            json.dump(qc_all, fq)
        os.replace(tmp, qc_path)

    # channel_meta is just a small per-channel tally (n_samples, sampling_rate) kept in
    # memory across the day loop -- bounded by channel count (<=6 here), not by how
    # many days/years are processed, so it doesn't reintroduce the history-length-
    # scaling memory problem this rewrite exists to fix.
    channel_meta = {}
    try:
        inv = read_inventory(os.path.join(xml_dir, "*.xml")) if xml_files else None

        for day_str in sorted(files_by_day):
            if day_str in done_days:
                continue
            try:
                # ---- process ONE day, entirely OUTSIDE any open HDF5 file handle ----
                day_stream = None
                for mf in files_by_day[day_str]:
                    st_part = read(mf)
                    day_stream = st_part if day_stream is None else day_stream + st_part
                seg_count = {}
                for tr_ in day_stream:                 # [PATCH 3] record segments per channel BEFORE merge (gaps / restarts)
                    seg_count[tr_.id] = seg_count.get(tr_.id, 0) + 1
                day_stream.merge(fill_value=0)  # scoped to ONE day now -- cheap, not the 7.3GB version

                day_traces = []
                day_qc = {}
                for tr in day_stream:
                    day_qc[tr.stats.channel] = _raw_qc(tr.data, seg_count.get(tr.id, 1))
                    tr.detrend("linear")
                    tr.detrend("demean")
                    response_removed = False
                    units_out = "counts"
                    azimuth, dip = None, None
                    if inv is not None:
                        try:
                            tr.remove_response(inventory=inv, output="DISP", water_level=60,
                                                pre_filt=(0.001, 0.005, 0.4, 0.5))
                            response_removed = True
                            units_out, scale_ = _disp_units(inv, tr)      # [PATCH 4]
                            if scale_ != 1.0:
                                tr.data = tr.data * scale_
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
                    align_to_integer_second(tr)                            # [PATCH 1] sample 0 exactly on an integer UTC second
                    tr.detrend("demean")
                    tr.taper(max_percentage=TAPER_PCT)                     # [PATCH 6] was hard-coded 0.05
                    day_traces.append((tr, response_removed, azimuth, dip, units_out))
                qc_all[day_str] = day_qc
                save_qc()

                # ---- open the shard ONLY to append this one day, then close it again.
                # HDF5 has no journal/crash-safety against a hard kill (OOM SIGKILL,
                # scancel, node failure) mid-write -- keeping the file open across the
                # WHOLE station's history (the original design) meant a kill at any
                # point could corrupt the shard, defeating the point of checkpointing.
                # Minimizing the open window to one day's worth of appends means a kill
                # either lands cleanly between days (file valid, day_state accurate) or,
                # worst case, corrupts only the current shard -- caught by the inspector
                # later regardless, but far less likely than with the file open for a
                # station's whole multi-year run. ----
                day_refused = False
                with h5py.File(h5_path, "a") as f:
                    grp = f.require_group(f"{network}.{station}")
                    grp.attrs["network"] = network
                    grp.attrs["station"] = station
                    grp.attrs["latitude"] = float(lat)
                    grp.attrs["longitude"] = float(lon)
                    if xml_files and "_stationxml_raw" not in grp:
                        with open(xml_files[0], "rb") as xf:
                            grp.create_dataset("_stationxml_raw", data=np.void(xf.read()))

                    for tr, response_removed, azimuth, dip, units_out in day_traces:
                        channel = tr.stats.channel
                        data = tr.data.astype(np.float32)
                        units = units_out                                  # [PATCH 4] was: "m" if response_removed else "counts"
                        status = append_channel_data(grp, channel, data, tr.stats.sampling_rate,
                                                      tr.stats.starttime, units, azimuth=azimuth, dip=dip)
                        if status.startswith("SKIPPED"):
                            day_notes[f"{day_str}/{channel}"] = status
                            continue
                        if status.startswith("REFUSED"):
                            # A real anomaly (e.g. overlapping timestamps from a
                            # provider) -- skip this trace, don't crash the whole day,
                            # but don't silently pretend it succeeded either.
                            day_errors[f"{day_str}/{channel}"] = status
                            day_refused = True                             # [PATCH 2]
                            continue
                        meta_entry = channel_meta.setdefault(
                            channel, dict(n_samples=0, sampling_rate=tr.stats.sampling_rate))
                        meta_entry["n_samples"] += len(data)
                if day_refused:
                    # [PATCH 2] a refused day used to be marked done anyway (silently lost). Now it is NOT marked done
                    # (a resubmit retries it) and is counted in the result JSON (n_days_refused / day_errors).
                    n_days_refused += 1
                    continue
                # File is now safely closed -- only mark the day done AFTER that, so
                # day_state can never claim a day is in the shard when it isn't.
                done_days.add(day_str)
                save_day_state()  # checkpoint after EVERY day, not just at the end
                n_days_processed += 1
            except Exception as de:
                # One bad day doesn't sink the whole station -- move on, keep the
                # checkpoint where it is (this day is NOT marked done, so a
                # resubmit will retry it), record what happened.
                day_errors[day_str] = f"{type(de).__name__}: {de}"

        result.update(preprocess_ok=n_days_processed > 0, package_ok=n_days_processed > 0,
                       preprocess_elapsed_s=time.time() - t0, n_days_processed=n_days_processed,
                       n_processed=len(channel_meta), channel_meta=channel_meta,
                       h5_size_bytes=os.path.getsize(h5_path) if os.path.exists(h5_path) else 0,
                       packaged_h5_path=h5_path, n_days_refused=n_days_refused,
                       n_days_qc_flagged=sum(1 for v in qc_all.values() if any(c.get("flag") for c in v.values())),
                       qc_sidecar=qc_path, day_notes=day_notes)
        if day_errors:
            result["day_errors"] = day_errors
    except Exception as e:
        result.update(preprocess_elapsed_s=time.time() - t0, preprocess_error=f"{type(e).__name__}: {e}",
                       n_days_processed=n_days_processed)

# Raw SEED is deliberately NOT purged here -- the inspector purges it once it has
# independently confirmed the master-h5 copy is intact (docs/ncf_pipeline_stages/
# PROGRESS.md's discard-after-proof principle, applied per-station).
result["raw_seed_kept_at"] = work_dir

# Atomic write (temp file + rename), matching the pattern already used for state files
# elsewhere in this framework (logger.py/inspector.py's save_state) -- a plain
# `open(...).write()` here leaves a window where `master.py progress`, run frequently by
# more than one person now, could read a half-written file and crash on invalid JSON.
# rename() is atomic on the same filesystem, so a reader always sees either the old
# content or the complete new content, never a partial write.
tmp_path = result_path + ".tmp"
with open(tmp_path, "w") as f:
    json.dump(result, f)
os.replace(tmp_path, result_path)
print(json.dumps(result))
