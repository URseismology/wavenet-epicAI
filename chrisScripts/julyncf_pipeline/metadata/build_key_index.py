#!/usr/bin/env python
"""
Scans all networks in the EarthScope S3 bucket and builds a partitioned Parquet index
mapping each (network, station, year, yearday) to its S3 key.

The index is written to keys_partitioned_year/ (partitioned by year) and used by
download_pairs.py to look up which days of data exist for a station pair without
hitting S3 at download time.

Usage:
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
from botocore.config import Config
from earthscope_sdk import EarthScopeClient


AUTH_LOCK = threading.Lock()
PROGRESS_LOCK = threading.Lock()
global_es_client = None
global_s3_client = None
networks_done = 0
networks_total = 0


def refresh_s3_client(es_client, max_pool=20):
    """Creates a fresh boto3 S3 client using current EarthScope credentials."""
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
    """Paginates the S3 bucket for one network and returns a list of (network, station, year, yearday, s3_key) tuples."""
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
                    if len(parts) >= 5:
                        year = parts[2]
                        yearday = parts[3]
                        station = parts[-1].split('.')[0]
                        records.append((network, station, year, yearday, key))
    except Exception as e:
        error_str = str(e)
        if "ExpiredToken" in error_str or "Token expired" in error_str or "Forbidden" in error_str:
            with AUTH_LOCK:
                print(f"\n  -> Token expired during {network}. Refreshing...")
                global_s3_client = refresh_s3_client(global_es_client)
            
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
    """Authenticates with EarthScope, scans all S3 networks in parallel, and writes the partitioned Parquet index."""
    global global_es_client, global_s3_client, networks_total

    parser = argparse.ArgumentParser(description="Build partitioned Parquet key index from EarthScope S3.")
    parser.add_argument("--outdir", default="keys_partitioned_year", help="Output directory for partitioned Parquet")
    parser.add_argument("--workers", type=int, default=20, help="Number of parallel scanning threads")
    args, unknown = parser.parse_known_args()

    print("Authenticating with EarthScope...")
    global_es_client = EarthScopeClient()
    global_s3_client = refresh_s3_client(global_es_client, max_pool=args.workers)
    BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"
    print("Authenticated successfully.\n")

    t_start = time.time()

    print("Discovering networks in S3...")
    resp = global_s3_client.list_objects_v2(Bucket=BUCKET, Prefix="miniseed/", Delimiter="/")
    network_list = [p['Prefix'].split('/')[1] for p in resp.get('CommonPrefixes', [])]
    
    if 'SY' in network_list:
        network_list.remove('SY')
    
    networks_total = len(network_list)
    print(f"Found {networks_total} networks: {', '.join(network_list[:10])}{'...' if len(network_list) > 10 else ''}\n")

    print(f"Scanning all networks with {args.workers} threads...")
    all_records = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(scan_network, net, BUCKET): net for net in network_list}
        for future in as_completed(futures):
            records = future.result()
            all_records.extend(records)

    print(f"\n\nScan complete! Total records: {len(all_records):,}")

    print("\nBuilding DataFrame and saving to Parquet...")
    df = pd.DataFrame(all_records, columns=['network', 'station', 'year', 'yearday', 'dataacess_key'])
    
    df['network'] = df['network'].astype('category')
    df['station'] = df['station'].astype('category')
    df['year'] = df['year'].astype('int16')
    df['yearday'] = df['yearday'].astype('int16')

    df.to_parquet(
        args.outdir,
        engine='pyarrow',
        partition_cols=['year'],
        index=False
    )

    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)

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
