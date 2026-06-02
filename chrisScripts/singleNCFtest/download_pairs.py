#!/usr/bin/env python
"""
download_pairs.py — GeoLab Download Script (Parquet Index + Multithreading)
===========================================================================
Reads GridMeta's station pairs CSV, calculates overlapping days instantly
using a partitioned Parquet key index (keys_partitioned_year/), and downloads
ONLY the raw MiniSEED files that overlap using 50 parallel threads.

Saves them locally in a structured directory tree ready for transfer to BlueHive.

Run this on GeoLab (EarthScope cloud) where S3 access is fast and free.

Prerequisites:
    1. Build the key index first:  python build_key_index.py --outdir keys_partitioned_year
    2. Have your pairs CSV:        global_station_pairs10k.csv

Usage:
    python download_pairs.py \
        --pairs global_station_pairs10k.csv \
        --keyindex keys_partitioned_year \
        --outdir raw_data \
        --max-pairs 100 \
        --networks CI \
        --workers 50
"""

import os
import sys
import time
import argparse
import pandas as pd
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from earthscope_sdk import EarthScopeClient


# ==========================================
# Thread-safe globals
# ==========================================
AUTH_LOCK = threading.Lock()
global_s3_client = None
global_es_client = None
progress_downloaded = 0
progress_skipped = 0
progress_errors = 0
progress_total = 0
total_bytes_downloaded = 0


def refresh_s3_client(es_client, max_pool=50):
    """Get fresh AWS credentials and return a new S3 client."""
    creds = es_client.user.get_aws_credentials()
    def _v(x):
        return x.get_secret_value() if hasattr(x, 'get_secret_value') else x

    session = boto3.Session(
        aws_access_key_id=creds.aws_access_key_id,
        aws_secret_access_key=_v(creds.aws_secret_access_key),
        aws_session_token=_v(creds.aws_session_token),
    )
    s3_config = Config(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        max_pool_connections=max_pool,
    )
    return session.client("s3", config=s3_config)


def load_and_filter_pairs(pairs_file, networks=None, max_pairs=None):
    """Load GridMeta pairs and apply optional filters."""
    df = pd.read_csv(pairs_file)
    print(f"Loaded {len(df):,} pairs from {pairs_file}")

    if networks:
        mask = df['net1'].isin(networks) & df['net2'].isin(networks)
        df = df[mask].reset_index(drop=True)
        print(f"  After network filter ({networks}): {len(df):,} pairs")

    if max_pairs is not None:
        if 'days1' in df.columns and 'days2' in df.columns:
            df = df.sort_values('days1', ascending=False).head(max_pairs).reset_index(drop=True)
        else:
            df = df.head(max_pairs).reset_index(drop=True)
        print(f"  Limited to top {max_pairs} pairs")

    return df


def build_availability_from_parquet(keyindex_path, unique_stations):
    """Load the partitioned Parquet index and build availability dicts instantly."""
    print(f"Loading key index from {keyindex_path}...")
    t0 = time.time()

    # Build filter for only the stations we need
    nets = list(set(n for n, s in unique_stations))
    stas = list(set(s for n, s in unique_stations))

    # Read only relevant rows from Parquet (partition pruning + row filtering)
    df = pd.read_parquet(
        keyindex_path,
        filters=[
            ('network', 'in', nets),
        ]
    )
    # Further filter by station
    df = df[df['station'].isin(stas)].copy()

    # Build the availability dict using vectorized ops
    df['sta_key'] = df['network'].astype(str) + '.' + df['station'].astype(str)
    df['date_str'] = pd.to_datetime(
        df['year'].astype(str) + df['yearday'].astype(str).str.zfill(3),
        format='%Y%j'
    ).dt.strftime('%Y-%m-%d')

    station_availability = {}
    for sta_key, group in df.groupby('sta_key'):
        station_availability[sta_key] = dict(zip(group['date_str'], group['dataacess_key']))

    elapsed = time.time() - t0
    print(f"  Loaded {len(df):,} records for {len(station_availability)} stations in {elapsed:.1f}s")

    return station_availability


def download_worker(item):
    """Worker function to download a single file from S3."""
    global progress_downloaded, progress_skipped, progress_errors
    global progress_total, total_bytes_downloaded, global_s3_client

    bucket, outdir, sta_label, s3_key = item

    sta_dir = os.path.join(outdir, sta_label)
    local_path = os.path.join(sta_dir, s3_key)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    # Skip if already exists
    if os.path.exists(local_path):
        with AUTH_LOCK:
            progress_skipped += 1
            done = progress_downloaded + progress_skipped + progress_errors
            if done % 50 == 0 or done == progress_total:
                sys.stdout.write(f"\r  Progress: [{done}/{progress_total}] ({progress_downloaded} new, {progress_skipped} cached, {progress_errors} err)")
                sys.stdout.flush()
        return {'station': sta_label, 'key': s3_key, 'status': 'skipped', 'bytes': 0}

    s3_client = global_s3_client

    try:
        resp = s3_client.get_object(Bucket=bucket, Key=s3_key)
        data = resp['Body'].read()
        with open(local_path, "wb") as f:
            f.write(data)
        nbytes = len(data)

        with AUTH_LOCK:
            progress_downloaded += 1
            total_bytes_downloaded += nbytes
            done = progress_downloaded + progress_skipped + progress_errors
            if done % 10 == 0 or done == progress_total:
                dl_gb = total_bytes_downloaded / (1024**3)
                sys.stdout.write(f"\r  Progress: [{done}/{progress_total}] ({progress_downloaded} new, {progress_skipped} cached, {progress_errors} err) — {dl_gb:.2f} GB downloaded")
                sys.stdout.flush()

        return {'station': sta_label, 'key': s3_key, 'status': 'ok', 'bytes': nbytes}

    except Exception as e:
        error_str = str(e)
        if "ExpiredToken" in error_str or "Token expired" in error_str or "Forbidden" in error_str:
            # Refresh credentials safely
            with AUTH_LOCK:
                print(f"\n  -> Token expired. Refreshing credentials...")
                global_s3_client = refresh_s3_client(global_es_client, max_pool=50)
                s3_client = global_s3_client

            # Retry once
            try:
                resp = s3_client.get_object(Bucket=bucket, Key=s3_key)
                data = resp['Body'].read()
                with open(local_path, "wb") as f:
                    f.write(data)
                nbytes = len(data)
                with AUTH_LOCK:
                    progress_downloaded += 1
                    total_bytes_downloaded += nbytes
                return {'station': sta_label, 'key': s3_key, 'status': 'ok', 'bytes': nbytes}
            except Exception as e2:
                with AUTH_LOCK:
                    progress_errors += 1
                return {'station': sta_label, 'key': s3_key, 'status': f'error: {e2}', 'bytes': 0}
        else:
            with AUTH_LOCK:
                progress_errors += 1
            return {'station': sta_label, 'key': s3_key, 'status': f'error: {e}', 'bytes': 0}


def main():
    global global_s3_client, global_es_client
    global progress_downloaded, progress_skipped, progress_errors, progress_total, total_bytes_downloaded

    parser = argparse.ArgumentParser(
        description="Download raw MiniSEED data for overlapping GridMeta station pairs."
    )
    parser.add_argument("--pairs", required=True, help="Path to GridMeta pairs CSV")
    parser.add_argument("--keyindex", required=True, help="Path to keys_partitioned_year/ Parquet directory")
    parser.add_argument("--outdir", default="raw_data", help="Output directory")
    parser.add_argument("--max-pairs", type=int, default=None, help="Max pairs to process")
    parser.add_argument("--networks", nargs="+", default=None, help="Filter by network codes (e.g., CI IU)")
    parser.add_argument("--workers", type=int, default=50, help="Number of parallel download threads (default: 50)")
    args = parser.parse_args()

    # Validate keyindex exists
    if not os.path.exists(args.keyindex):
        print(f"ERROR: Key index '{args.keyindex}' not found!")
        print(f"Run build_key_index.py first:  python build_key_index.py --outdir {args.keyindex}")
        sys.exit(1)

    # ==========================================
    # 1. Authenticate with EarthScope
    # ==========================================
    print("Authenticating with EarthScope...")
    global_es_client = EarthScopeClient()
    global_s3_client = refresh_s3_client(global_es_client, max_pool=args.workers)
    BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"
    print("Authenticated successfully.\n")

    t_start = time.time()

    # Track peak RAM
    try:
        import resource
        ram_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:
        ram_before = None

    # ==========================================
    # 2. Load and filter pairs
    # ==========================================
    df_pairs = load_and_filter_pairs(
        args.pairs, networks=args.networks, max_pairs=args.max_pairs
    )
    os.makedirs(args.outdir, exist_ok=True)
    df_pairs.to_csv(os.path.join(args.outdir, "pairs_to_process.csv"), index=False)

    # Get unique stations
    stations = set()
    for _, row in df_pairs.iterrows():
        stations.add((row['net1'], row['sta1']))
        stations.add((row['net2'], row['sta2']))
    unique_stations = sorted(stations)
    print(f"\nUnique stations: {len(unique_stations)}")

    # ==========================================
    # 3. Instant Overlap Calculation (Parquet)
    # ==========================================
    print(f"\n{'='*60}")
    print(f"Calculating overlaps from Parquet index...")
    print(f"{'='*60}")

    station_availability = build_availability_from_parquet(args.keyindex, unique_stations)

    to_download = set()
    total_overlapping_days = 0
    pairs_with_overlap = 0

    for _, row in df_pairs.iterrows():
        sta1 = f"{row['net1']}.{row['sta1']}"
        sta2 = f"{row['net2']}.{row['sta2']}"

        avail1 = station_availability.get(sta1, {})
        avail2 = station_availability.get(sta2, {})

        overlap_dates = set(avail1.keys()).intersection(set(avail2.keys()))

        if overlap_dates:
            total_overlapping_days += len(overlap_dates)
            pairs_with_overlap += 1
            for date in overlap_dates:
                to_download.add((sta1, avail1[date]))
                to_download.add((sta2, avail2[date]))

    print(f"\n  Pairs with overlap:    {pairs_with_overlap} / {len(df_pairs)}")
    print(f"  Total overlapping days: {total_overlapping_days:,}")
    print(f"  Unique files to fetch:  {len(to_download):,}")

    # ==========================================
    # 4. Download (Multithreaded)
    # ==========================================
    if not to_download:
        print("\nNo overlaps found. Nothing to download.")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Downloading with {args.workers} parallel threads...")
    print(f"{'='*60}\n")

    progress_total = len(to_download)
    progress_downloaded = 0
    progress_skipped = 0
    progress_errors = 0
    total_bytes_downloaded = 0

    to_download_sorted = sorted(list(to_download))
    download_tasks = [(BUCKET, args.outdir, sta, key) for sta, key in to_download_sorted]

    all_downloads = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(download_worker, item) for item in download_tasks]
        for future in as_completed(futures):
            all_downloads.append(future.result())

    print("\n")

    # ==========================================
    # 5. Summary with Statistics
    # ==========================================
    manifest = pd.DataFrame(all_downloads)
    if not manifest.empty:
        manifest_path = os.path.join(args.outdir, "download_manifest.csv")
        manifest.to_csv(manifest_path, index=False)

    total_ok = sum(1 for d in all_downloads if d['status'] == 'ok')
    total_skip = sum(1 for d in all_downloads if d['status'] == 'skipped')
    total_err = sum(1 for d in all_downloads if d['status'].startswith('error'))
    total_bytes = sum(d.get('bytes', 0) for d in all_downloads)

    # Calculate total size on disk (including previously cached files)
    disk_size = 0
    for root, dirs, files in os.walk(args.outdir):
        for f in files:
            fp = os.path.join(root, f)
            try:
                disk_size += os.path.getsize(fp)
            except OSError:
                pass

    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)
    hrs, mins = divmod(mins, 60)

    # RAM usage
    try:
        import resource
        ram_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux, ru_maxrss is in KB
        ram_peak_mb = ram_after / 1024
        ram_str = f"{ram_peak_mb:.0f} MB"
    except (ImportError, Exception):
        try:
            import psutil
            process = psutil.Process(os.getpid())
            ram_peak_mb = process.memory_info().rss / (1024 * 1024)
            ram_str = f"{ram_peak_mb:.0f} MB"
        except (ImportError, Exception):
            ram_str = "N/A (install psutil for RAM tracking)"

    # Download speed
    if t_elapsed > 0 and total_ok > 0:
        speed_files = total_ok / t_elapsed
        speed_mb = (total_bytes / (1024 * 1024)) / t_elapsed
    else:
        speed_files = 0
        speed_mb = 0

    print(f"{'='*60}")
    print(f"DOWNLOAD COMPLETE")
    print(f"{'='*60}")
    print(f"  Pairs Processed:     {len(df_pairs):,}")
    print(f"  Pairs with Overlap:  {pairs_with_overlap:,}")
    print(f"  Overlapping Days:    {total_overlapping_days:,}")
    print(f"  ─────────────────────────────────────")
    print(f"  Files Downloaded:    {total_ok:,}")
    print(f"  Files Cached:        {total_skip:,} (already on disk)")
    print(f"  Files Errored:       {total_err:,}")
    print(f"  ─────────────────────────────────────")
    print(f"  Data Downloaded:     {total_bytes / (1024**3):.2f} GB (this run)")
    print(f"  Total on Disk:       {disk_size / (1024**3):.2f} GB (raw_data/)")
    print(f"  Download Speed:      {speed_files:.1f} files/s | {speed_mb:.1f} MB/s")
    print(f"  ─────────────────────────────────────")
    print(f"  Peak RAM Usage:      {ram_str}")
    print(f"  Total Time:          {hrs}h {mins}m {secs}s")


if __name__ == "__main__":
    main()
