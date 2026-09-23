#!/usr/bin/env python
"""
Preparatory Stage 2->4 pipeline test: download (ROVER) -> preprocess (ObsPy) -> package
(numpy-direct HDF5), run in parallel across a small station subset, to get real
throughput numbers for the AWS-vs-ROVER cost/time comparison (docs/ncf_pipeline_stages/
stage_1_data_availability_cost_analysis.md).

Does NOT touch fps_stations.csv / fps_downsample.py -- reads the fixed station set only.

Known simplifications vs the full pipeline (see docs/ncf_pipeline_stages/stage_3_preprocessing.md
and stage_4_packaging.md for what full Stage 3/4 still need to add):
  - No instrument-response removal (would need a StationXML fetch per station; skipped
    here to keep this a throughput/architecture test, not a science-correctness test).
  - Single channel (LHZ), single day per station.
  - One HDF5 output file for the whole batch (fine at this scale; chunking/compression
    strategy for full scale is an open question, see stage_4_packaging.md).

Usage:
    python pipeline_test.py --n-stations 8 --workers 8 \
        --start 2018-01-01 --end 2018-01-02 --channel LHZ
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import h5py
import numpy as np
import pandas as pd

META_DIR = os.path.join(os.path.dirname(__file__), "..", "metadata3")
DATASELECT_URL = "https://service.earthscope.org/fdsnws/dataselect/1/query"


def download_one(rover_cmd, net, sta, loc, chan, start, end, workdir):
    """Runs `rover download` for one station into an isolated repo dir (avoids sqlite
    contention between parallel workers -- see stage_1 doc's ROVER findings)."""
    repo_dir = os.path.join(workdir, f"{net}_{sta}")
    os.makedirs(repo_dir, exist_ok=True)
    config_path = os.path.join(repo_dir, "rover.config")
    subprocess.run([rover_cmd, "init-repository", repo_dir, "-f", config_path],
                   capture_output=True, text=True, timeout=30)
    req_path = os.path.join(repo_dir, "request.txt")
    with open(req_path, "w") as f:
        f.write(f"{net} {sta} {loc or '*'} {chan} {start}T00:00:00 {end}T00:00:00\n")

    t0 = time.time()
    proc = subprocess.run(
        [rover_cmd, "download", req_path, "--dataselect-url", DATASELECT_URL, "-v", "3"],
        cwd=repo_dir, capture_output=True, text=True, timeout=120,
    )
    elapsed = time.time() - t0

    byte_count = 0
    for line in proc.stdout.splitlines() + proc.stderr.splitlines():
        line = line.strip()
        if line.startswith("{") and "download_byte_count" in line:
            try:
                byte_count = json.loads(line)["download_byte_count"]
            except Exception:
                pass

    data_root = os.path.join(repo_dir, "data", net)
    mseed_path = None
    if os.path.isdir(data_root):
        for root, _, files in os.walk(data_root):
            for fn in files:
                mseed_path = os.path.join(root, fn)

    return dict(
        network=net, station=sta, ok=(proc.returncode == 0 and mseed_path is not None),
        elapsed_s=elapsed, bytes=byte_count, mseed_path=mseed_path,
        stderr_tail="\n".join(proc.stderr.splitlines()[-3:]) if proc.returncode != 0 else "",
    )


def preprocess_one(mseed_path):
    """Simplified version of ADAMA/GVibToNCFs/pre_pross.py's pipeline: detrend, anti-alias
    lowpass, long-period highpass, decimate -- no instrument-response removal (see module
    docstring). Returns (numpy_array, sampling_rate, starttime_iso) or None on failure."""
    import obspy

    t0 = time.time()
    try:
        st = obspy.read(mseed_path)
        st.merge(fill_value=0)
        tr = st[0]
        tr.detrend("linear")
        tr.detrend("demean")
        tr.filter("lowpass", freq=0.4, corners=4, zerophase=True)
        tr.filter("highpass", freq=1.0 / 3600.0, corners=4, zerophase=True)
        tr.decimate(factor=int(round(tr.stats.sampling_rate)), no_filter=True) \
            if tr.stats.sampling_rate >= 2 else None
        tr.detrend("demean")
        tr.taper(max_percentage=0.05)
        elapsed = time.time() - t0
        return dict(
            data=tr.data.astype(np.float32), sampling_rate=tr.stats.sampling_rate,
            starttime=str(tr.stats.starttime), elapsed_s=elapsed, ok=True,
        )
    except Exception as e:
        return dict(ok=False, error=str(e), elapsed_s=time.time() - t0)


def package_to_hdf5(h5_path, station_key, channel, pp_result):
    """Numpy-direct HDF5 write -- no SAC round-trip (per PI's explicit scoping note,
    2026-09-23): this intermediate file holds ONLY preprocessed pre-correlation
    waveforms, not the full FTAN/dispersion schema used downstream by
    src/wavenet_pipeline/03_machine_learning/ (a separate, unrelated pipeline)."""
    t0 = time.time()
    with h5py.File(h5_path, "a") as f:
        grp = f.require_group(station_key)
        ds_name = channel
        if ds_name in grp:
            del grp[ds_name]
        ds = grp.create_dataset(ds_name, data=pp_result["data"], compression="gzip", compression_opts=4)
        ds.attrs["sampling_rate"] = pp_result["sampling_rate"]
        ds.attrs["starttime"] = pp_result["starttime"]
    return time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", default=os.path.join(META_DIR, "fps_stations.csv"))
    ap.add_argument("--n-stations", type=int, default=8)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2018-01-02")
    ap.add_argument("--channel", default="LHZ")
    ap.add_argument("--location", default="*")
    ap.add_argument("--rover-cmd", default=shutil.which("rover") or "rover")
    ap.add_argument("--out-h5", default=os.path.join(os.path.dirname(__file__), "pipeline_test_output.h5"))
    args = ap.parse_args()

    sta = pd.read_csv(args.stations).head(args.n_stations)
    print(f"Testing {len(sta)} stations, {args.workers} parallel workers, "
          f"channel={args.channel}, {args.start} -> {args.end}")

    workdir = tempfile.mkdtemp(prefix="rover_pipeline_test_")
    if os.path.exists(args.out_h5):
        os.remove(args.out_h5)

    download_results = []
    t_download_start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(download_one, args.rover_cmd, row.network, row.station,
                      args.location, args.channel, args.start, args.end, workdir): row
            for row in sta.itertuples()
        }
        for fut in as_completed(futs):
            r = fut.result()
            download_results.append(r)
            status = "OK" if r["ok"] else "FAIL"
            print(f"  [download {status}] {r['network']}.{r['station']} "
                  f"{r['bytes']:,}B in {r['elapsed_s']:.1f}s" +
                  (f"  ({r['stderr_tail']})" if not r["ok"] else ""))
    t_download_total = time.time() - t_download_start

    n_ok = sum(1 for r in download_results if r["ok"])
    total_bytes = sum(r["bytes"] for r in download_results)
    print(f"\nDownload stage: {n_ok}/{len(sta)} succeeded, {total_bytes:,} bytes total, "
          f"{t_download_total:.1f}s wall-clock ({args.workers}-way parallel)")

    pp_results = []
    t_pp_start = time.time()
    for r in download_results:
        if not r["ok"]:
            continue
        pp = preprocess_one(r["mseed_path"])
        pp["network"], pp["station"] = r["network"], r["station"]
        pp_results.append(pp)
        status = "OK" if pp["ok"] else "FAIL"
        print(f"  [preprocess {status}] {r['network']}.{r['station']} in {pp['elapsed_s']:.2f}s" +
              (f"  ({pp.get('error')})" if not pp["ok"] else ""))
    t_pp_total = time.time() - t_pp_start

    n_pp_ok = sum(1 for r in pp_results if r["ok"])
    print(f"\nPreprocess stage: {n_pp_ok}/{n_ok} succeeded, {t_pp_total:.1f}s wall-clock (serial)")

    t_pack_start = time.time()
    for r, pp in zip([r for r in download_results if r["ok"]], pp_results):
        if pp["ok"]:
            package_to_hdf5(args.out_h5, f"{r['network']}.{r['station']}", args.channel, pp)
    t_pack_total = time.time() - t_pack_start

    h5_size = os.path.getsize(args.out_h5) if os.path.exists(args.out_h5) else 0
    print(f"\nPackage stage: {t_pack_total:.2f}s wall-clock, output {args.out_h5} "
          f"({h5_size / 1024:.1f} KB, {n_pp_ok} station-day datasets)")

    print(f"\n{'='*60}\nSUMMARY\n{'='*60}")
    print(f"  Stations attempted:   {len(sta)}")
    print(f"  Download success:     {n_ok}/{len(sta)}")
    print(f"  Preprocess success:   {n_pp_ok}/{n_ok}")
    print(f"  Total bytes downloaded: {total_bytes:,} ({total_bytes/1024/1024:.2f} MB)")
    print(f"  Download wall-clock:  {t_download_total:.1f}s -> {total_bytes/1024/1024/max(t_download_total,0.01):.2f} MB/s effective, "
          f"{len(sta)/max(t_download_total,0.01)*60:.1f} stations/min")
    print(f"  Preprocess wall-clock: {t_pp_total:.1f}s ({t_pp_total/max(n_ok,1):.2f}s/station, serial)")
    print(f"  Package wall-clock:   {t_pack_total:.2f}s")
    print(f"  End-to-end wall-clock: {t_download_total + t_pp_total + t_pack_total:.1f}s")

    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
