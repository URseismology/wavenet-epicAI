#!/usr/bin/env python
"""
build_key_index.py — Fast Parallel S3 Key Index Builder
========================================================
Scans the entire EarthScope S3 bucket in parallel using ThreadPoolExecutor
and boto3 Paginators. Produces a partitioned Parquet dataset (keys_partitioned_year/)
that download_pairs.py uses for instant overlap calculations.

Run this ONCE on Google Colab (or any machine with internet access).
Then upload the output directory to GeoLab.

Usage (Colab):
    !pip install earthscope-sdk boto3 pandas pyarrow
    !es login
    !python build_key_index.py --outdir keys_partitioned_year --workers 20

Usage (GeoLab):
    python build_key_index.py --outdir keys_partitioned_year --workers 20
"""

import os
import sys
import time
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from earthscope_sdk import EarthScopeClient


# Thread-safe globals
AUTH_LOCK = threading.Lock()
PROGRESS_LOCK = threading.Lock()
global_es_client = None
global_s3_client = None
networks_done = 0
networks_total = 0


def refresh_s3_client(es_client, max_pool=20):
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


def scan_network(network, bucket):
    """Scan all keys for a single network using a Paginator. Returns list of tuples."""
    global networks_done, global_s3_client
    
    records = []
    s3_client = global_s3_client
    paginator = s3_client.get_paginator('list_objects_v2')
    
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=f"miniseed/{network}/"):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    parts = key.split('/')
                    # Expected format: miniseed/NET/YEAR/DAY/FILE
                    if len(parts) >= 5:
                        year = parts[2]
                        yearday = parts[3]
                        station = parts[-1].split('.')[0]
                        records.append((network, station, year, yearday, key))
    except Exception as e:
        error_str = str(e)
        if "ExpiredToken" in error_str or "Token expired" in error_str or "Forbidden" in error_str:
            # Refresh credentials (thread-safe)
            with AUTH_LOCK:
                print(f"\n  -> Token expired during {network}. Refreshing...")
                global_s3_client = refresh_s3_client(global_es_client)
            
            # Retry this network from scratch with fresh client
            records = []
            s3_client = global_s3_client
            paginator = s3_client.get_paginator('list_objects_v2')
            try:
                for page in paginator.paginate(Bucket=bucket, Prefix=f"miniseed/{network}/"):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            key = obj['Key']
                            parts = key.split('/')
                            if len(parts) >= 5:
                                records.append((network, parts[-1].split('.')[0], parts[2], parts[3], key))
            except Exception as e2:
                print(f"\n  -> ERROR: Failed to scan {network} after retry: {e2}")
        else:
            print(f"\n  -> ERROR: Failed to scan {network}: {e}")

    with PROGRESS_LOCK:
        networks_done += 1
        pct = (networks_done / networks_total) * 100
        sys.stdout.write(f"\r  Progress: [{networks_done}/{networks_total}] networks scanned ({pct:.0f}%) — {network}: {len(records):,} keys")
        sys.stdout.flush()
    
    return records


def main():
    global global_es_client, global_s3_client, networks_total

    parser = argparse.ArgumentParser(description="Build partitioned Parquet key index from EarthScope S3.")
    parser.add_argument("--outdir", default="keys_partitioned_year", help="Output directory for partitioned Parquet")
    parser.add_argument("--workers", type=int, default=20, help="Number of parallel scanning threads")
    args = parser.parse_args()

    # ==========================================
    # 1. Authenticate
    # ==========================================
    print("Authenticating with EarthScope...")
    global_es_client = EarthScopeClient()
    global_s3_client = refresh_s3_client(global_es_client, max_pool=args.workers)
    BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"
    print("Authenticated successfully.\n")

    t_start = time.time()

    # ==========================================
    # 2. Discover all networks
    # ==========================================
    print("Discovering networks in S3...")
    resp = global_s3_client.list_objects_v2(Bucket=BUCKET, Prefix="miniseed/", Delimiter="/")
    network_list = [p['Prefix'].split('/')[1] for p in resp.get('CommonPrefixes', [])]
    
    # Filter out known bad networks
    if 'SY' in network_list:
        network_list.remove('SY')
    
    networks_total = len(network_list)
    print(f"Found {networks_total} networks: {', '.join(network_list[:10])}{'...' if len(network_list) > 10 else ''}\n")

    # ==========================================
    # 3. Parallel scan all networks
    # ==========================================
    print(f"Scanning all networks with {args.workers} threads...")
    all_records = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(scan_network, net, BUCKET): net for net in network_list}
        for future in as_completed(futures):
            records = future.result()
            all_records.extend(records)

    print(f"\n\nScan complete! Total records: {len(all_records):,}")

    # ==========================================
    # 4. Build DataFrame and save as partitioned Parquet
    # ==========================================
    print("\nBuilding DataFrame and saving to Parquet...")
    df = pd.DataFrame(all_records, columns=['network', 'station', 'year', 'yearday', 'dataacess_key'])
    
    # Optimize types
    df['network'] = df['network'].astype('category')
    df['station'] = df['station'].astype('category')
    df['year'] = df['year'].astype('int16')
    df['yearday'] = df['yearday'].astype('int16')

    # Save partitioned by year
    df.to_parquet(
        args.outdir,
        engine='pyarrow',
        partition_cols=['year'],
        index=False
    )

    # ==========================================
    # 5. Summary
    # ==========================================
    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)

    # Calculate output size
    total_size = 0
    for root, dirs, files in os.walk(args.outdir):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
    size_mb = total_size / (1024 * 1024)

    unique_stations = df.groupby(['network', 'station']).ngroups
    year_range = f"{df['year'].min()} - {df['year'].max()}"

    print(f"\n{'='*60}")
    print(f"KEY INDEX BUILD COMPLETE")
    print(f"{'='*60}")
    print(f"  Networks Scanned:    {networks_total}")
    print(f"  Unique Stations:     {unique_stations:,}")
    print(f"  Total Records:       {len(df):,}")
    print(f"  Year Range:          {year_range}")
    print(f"  Output Directory:    {args.outdir}")
    print(f"  Output Size:         {size_mb:.1f} MB")
    print(f"  Build Time:          {mins}m {secs}s")
    print(f"\nNext step: use this with download_pairs.py --keyindex {args.outdir}")


if __name__ == "__main__":
    main()
