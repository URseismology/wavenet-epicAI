#!/usr/bin/env python
"""
End-to-end pipeline that streams download, cross-correlation, and rotation/stacking
for each station pair in sequence.

For each pair it:
  1. Queries the Parquet key index for overlapping days and downloads MiniSEED from EarthScope S3
  2. Preprocesses and cross-correlates via NoisePy (xcorr_pairs.process_single_pair)
  3. Rotates NEZ→RTZ and stacks into a per-pair *_ncf.h5 (rotate_and_stack.rotate_stack_pair)

Use --skip-download when raw_data/ is already populated (e.g. after a standalone
download_pairs.py run). Use --pair-index to process a single row for job arrays.

Output HDF5 schema (one *_ncf.h5 per pair):
    {pair_label}/                 attrs: dt, maxlag, stack_days
      {sensor_key}/               attrs: n_windows_ZZ, n_windows_RR, n_windows_TT
        freq_axis                 1D float64 — frequency bins (Hz)
        time_axis                 1D float64 — lag time axis (s), -maxlag to +maxlag
        ZZ/
          time_domain             1D float64
          cross_spectrum          1D complex128
        RR/  (same layout)
        TT/  (same layout)

Usage:
    python stream_xcorr_pipeline.py \\
        --pairs africa_pairs_filtered.csv \\
        --keyindex keys_partitioned_year/ \\
        --outdir raw_data \\
        --ncfdir NCF_output
"""
import os
import glob
import time
import argparse
import pandas as pd

from concurrent.futures import ThreadPoolExecutor

import download_pairs
from xcorr_pairs import process_single_pair, _collect_day_dates
from rotate_and_stack import rotate_stack_pair
from config import EARTHSCOPE_BUCKET
from datetime import datetime as _dt, timedelta as _td


def _token_to_iso(token):
    """Converts a Julian date token (YYYY.DDD) or ISO date (YYYY-MM-DD) to ISO format string."""
    if '-' in token:
        return token
    year, doy = token.split('.')
    d = _dt(int(year), 1, 1) + _td(days=int(doy) - 1)
    return d.strftime('%Y-%m-%d')


def main():
    """Parses CLI arguments and runs the streaming download → xcorr → rotate/stack pipeline."""
    parser = argparse.ArgumentParser(description="Streaming Download and Cross-Correlation Pipeline")
    parser.add_argument("--pairs", required=True, help="Path to pairs CSV")
    parser.add_argument("--keyindex", default=None,
                        help="Path to partitioned Parquet index (required unless --skip-download is set)")
    parser.add_argument("--outdir", default="raw_data", help="Temporary directory for raw data")
    parser.add_argument("--ncfdir", default="NCF_output", help="Directory for NCF output")
    parser.add_argument("--ncf-outdir", default=None,
                        help="Directory for per-pair *_ncf.h5 output (default: same as --ncfdir)")
    parser.add_argument("--pair-index", type=int, default=None,
                        help="Row index into --pairs CSV to process (for SLURM job arrays: use $SLURM_ARRAY_TASK_ID). "
                             "If omitted, all pairs are processed sequentially.")
    parser.add_argument("--force", action="store_true", help="Force reprocess pairs that already have NCF output")
    parser.add_argument("--max-days", type=int, default=None, help="Maximum number of days to process per pair (for testing)")
    parser.add_argument("--download-workers", type=int, default=50, help="Number of parallel download threads")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip EarthScope auth and S3 download. Use when raw_data/ is already populated.")
    args = parser.parse_args()

    if not args.skip_download and not args.keyindex:
        parser.error("--keyindex is required unless --skip-download is set")

    ncf_outdir = args.ncf_outdir if args.ncf_outdir else args.ncfdir
    os.makedirs(ncf_outdir, exist_ok=True)

    download_workers = args.download_workers
    cpu_workers = os.cpu_count() or 4

    print(f"Auto-detected workers: {cpu_workers} CPUs (preprocessing), {download_workers} threads (S3 download)")

    if not args.skip_download:
        from earthscope_sdk import EarthScopeClient

        print("Authenticating with EarthScope...")
        #First s3 client created
        es_client = EarthScopeClient()
        s3_client = download_pairs.refresh_s3_client(es_client, max_pool=download_workers)

        #s3 client shared across all threads
        download_pairs.global_s3_client = s3_client
        #callback for token refresh
        download_pairs.CUSTOM_REFRESH_CALLBACK = lambda max_pool=download_workers: download_pairs.refresh_s3_client(es_client, max_pool=max_pool)

        BUCKET = EARTHSCOPE_BUCKET

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.ncfdir, exist_ok=True)
   
    df_pairs = pd.read_csv(args.pairs)

    if args.pair_index is not None:
        df_pairs = df_pairs.iloc[[args.pair_index]].reset_index(drop=True)
        print(f"Pair index {args.pair_index}: processing 1 pair.")

    print(f"Loaded {len(df_pairs)} pairs. Starting pipeline stream...\n")

    n_skipped = 0
    n_success = 0
    n_failed = 0
    n_no_overlap = 0
    pipeline_start = time.time()
    # Iterate through the pairs in the csv to cross correlate
    for idx, row in df_pairs.iterrows():
        pair_label = f"{row['net1']}.{row['sta1']}_{row['net2']}.{row['sta2']}"
        print(f"\n{'='*60}")
        print(f"Processing Pair {idx+1}/{len(df_pairs)}: {pair_label}")
        print(f"{'='*60}")
        
        # Make sure to check for existing NCF output
        cc_path = os.path.join(args.ncfdir, pair_label)
        if not args.force and os.path.exists(cc_path):
            existing_h5 = glob.glob(os.path.join(cc_path, "*.h5"))
            if existing_h5:
                print(f"  {pair_label} — {len(existing_h5)} HDF5 files already exist. Use --force to reprocess.")
                n_skipped += 1
                continue

        sta1_id = f"{row['net1']}.{row['sta1']}"
        sta2_id = f"{row['net2']}.{row['sta2']}"

        if args.skip_download:
            sta1_dir = os.path.join(args.outdir, sta1_id)
            sta2_dir = os.path.join(args.outdir, sta2_id)
            tokens = sorted(
                set(_collect_day_dates(sta1_dir)) | set(_collect_day_dates(sta2_dir))
            )

            if not tokens:
                print(f"  No data files found on disk for {pair_label}. Skipping.")
                n_no_overlap += 1
                continue

            iso_dates = [_token_to_iso(t) for t in tokens]
            pair_start = iso_dates[0]
            pair_end = iso_dates[-1]
            print(f"  Found {len(iso_dates)} days on disk ({pair_start} to {pair_end}). Starting cross-correlation...")

        else:
            unique_stations = [(row['net1'], row['sta1']), (row['net2'], row['sta2'])]
            # Build availability from parquet
            avail = download_pairs.build_availability_from_parquet(args.keyindex, unique_stations)

            avail1 = avail.get(sta1_id, {})
            avail2 = avail.get(sta2_id, {})
            # Overlap dates are the dates where both stations have data
            overlap_dates = set(avail1.keys()).intersection(set(avail2.keys()))

            if not overlap_dates:
                print(f"  No overlapping data found for {pair_label}.")
                n_no_overlap += 1
                continue

            overlap_dates_sorted = sorted(list(overlap_dates))
            # Limit the number of days to process for testing
            if args.max_days and len(overlap_dates_sorted) > args.max_days:
                overlap_dates_sorted = overlap_dates_sorted[:args.max_days]
                overlap_dates = set(overlap_dates_sorted)
                print(f" Limited to first {args.max_days} overlapping days.")

            print(f"  Found {len(overlap_dates)} overlapping days. Starting download...")

            # Build the list of files to download
            to_download = set()
            for date in overlap_dates:
                to_download.add((sta1_id, avail1[date]))
                to_download.add((sta2_id, avail2[date]))

            download_tasks = [(BUCKET, args.outdir, sta, key) for sta, key in to_download]
            # Download the files in parallel
            with ThreadPoolExecutor(max_workers=download_workers) as executor:
                results = list(executor.map(download_pairs.download_worker, download_tasks))

            errors = [r for r in results if r['status'].startswith('error')]
            if errors:
                print(f"  {len(errors)} downloads failed. Example error: {errors[0]['status']}")

            print(f"  Download complete. Starting cross-correlation...")

            overlap_dates_sorted = sorted(list(overlap_dates))
            pair_start = overlap_dates_sorted[0]
            pair_end = overlap_dates_sorted[-1]

        print(f"  Cross-correlating from {pair_start} to {pair_end}...")

        xcorr_args = (row.to_dict(), args.outdir, args.ncfdir, pair_start, pair_end, cpu_workers, args.force)
        # Process the pairs through entire pipeline
        result = process_single_pair(xcorr_args)

        if result['status'] == 'ok':
            print(f"  Cross-correlation finished. Raw data deleted.")
            print(f"  Running rotation and stacking...")
            rotate_stack_pair(row.to_dict(), args.ncfdir, ncf_outdir)
            n_success += 1
        elif result['status'].startswith('skipped'):
            print(f"  Skipped (cached). Running rotation and stacking from existing NCF output...")
            rotate_stack_pair(row.to_dict(), args.ncfdir, ncf_outdir)
            n_skipped += 1
        else:
            print(f"  Cross-correlation failed: {result['status']}")
            n_failed += 1

    elapsed = time.time() - pipeline_start
    mins, secs = divmod(int(elapsed), 60)
    hrs, mins = divmod(mins, 60)

    print(f"\n{'='*60}")
    print(f"PIPELINE COMPLETE")
    print(f"{'='*60}")
    print(f"  Total Pairs:       {len(df_pairs)}")
    print(f"  Successful:        {n_success}")
    print(f"  Skipped (cached):  {n_skipped}")
    print(f"  No Overlap:        {n_no_overlap}")
    print(f"  Failed:            {n_failed}")
    print(f"  Total Time:        {hrs}h {mins}m {secs}s")

if __name__ == "__main__":
    main()
