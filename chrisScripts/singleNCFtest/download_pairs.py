#!/usr/bin/env python
"""
download_pairs.py — GeoLab Download Script
===========================================
Reads GridMeta's station pairs CSV, resolves which raw MiniSEED files 
need to be downloaded from EarthScope's S3 bucket, and saves them 
locally in a structured directory tree ready for transfer to BlueHive.

Run this on GeoLab (EarthScope cloud) where S3 access is fast and free.

Usage:
    python download_pairs.py --pairs global_station_pairs10k.csv \
                             --inventory s3_inventory.csv \
                             --start 2019-01-01 --end 2019-01-31 \
                             --outdir raw_data \
                             --max-pairs 10

Output structure:
    raw_data/
    ├── CI.LJR/
    │   ├── miniseed/CI/2019/001/LJR.CI.2019.001
    │   ├── miniseed/CI/2019/002/LJR.CI.2019.002
    │   └── ...
    ├── CI.DLA/
    │   └── ...
    └── download_manifest.csv   ← tracks what was downloaded

Then scp/rsync the raw_data/ folder to BlueHive.
"""

import os
import sys
import gc
import time
import argparse
import pandas as pd
import numpy as np
from datetime import datetime

import boto3
from botocore.config import Config
from earthscope_sdk import EarthScopeClient


def refresh_s3_client(es_client):
    """Get fresh AWS credentials and return a new S3 client."""
    creds = es_client.user.get_aws_credentials()
    def _get_val(x):
        return x.get_secret_value() if hasattr(x, 'get_secret_value') else x

    session = boto3.Session(
        aws_access_key_id=creds.aws_access_key_id,
        aws_secret_access_key=_get_val(creds.aws_secret_access_key),
        aws_session_token=_get_val(creds.aws_session_token),
    )
    # EarthScope's S3 Access Point does not support newer boto3 checksum
    # features (CRC32/CRC64). Explicitly disable both request and response
    # checksum handling to avoid "NotImplemented" errors on GetObject.
    s3_config = Config(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return session.client("s3", config=s3_config)


def load_and_filter_pairs(pairs_file, start_date, end_date, 
                          networks=None, max_pairs=None,
                          dist_min=None, dist_max=None):
    """Load GridMeta pairs and apply optional filters."""
    df = pd.read_csv(pairs_file)
    print(f"Loaded {len(df):,} pairs from {pairs_file}")

    if networks:
        mask = df['net1'].isin(networks) & df['net2'].isin(networks)
        df = df[mask].reset_index(drop=True)
        print(f"  After network filter ({networks}): {len(df):,} pairs")

    if dist_min is not None:
        df = df[df['distance_km'] >= dist_min].reset_index(drop=True)
    if dist_max is not None:
        df = df[df['distance_km'] <= dist_max].reset_index(drop=True)
        print(f"  After distance filter [{dist_min}-{dist_max} km]: {len(df):,} pairs")

    if max_pairs is not None:
        # Sort by combined uptime if available, take best pairs
        if 'days1' in df.columns and 'days2' in df.columns:
            df = df.sort_values('days1', ascending=False).head(max_pairs).reset_index(drop=True)
        else:
            df = df.head(max_pairs).reset_index(drop=True)
        print(f"  Limited to top {max_pairs} pairs")

    return df


def get_unique_stations(df_pairs):
    """Extract unique NET.STA identifiers from the pairs DataFrame."""
    stations = set()
    for _, row in df_pairs.iterrows():
        stations.add((row['net1'], row['sta1']))
        stations.add((row['net2'], row['sta2']))
    return sorted(stations)


def download_station_data(s3_client, es_client, bucket, station_net, station_sta, 
                          start_date, end_date, outdir, inventory_df=None):
    """
    Download all MiniSEED files for a single station within the date range.
    
    If inventory_df is provided, use it to resolve exact S3 keys.
    Otherwise, construct keys from the standard S3 path convention.
    """
    sta_dir = os.path.join(outdir, f"{station_net}.{station_sta}")
    os.makedirs(sta_dir, exist_ok=True)

    downloaded = []
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    if inventory_df is not None:
        # Use the pre-computed inventory to find exact S3 keys
        mask = (
            (inventory_df['network'] == station_net) & 
            (inventory_df['station'] == station_sta)
        )
        sta_inv = inventory_df[mask].copy()

        if sta_inv.empty:
            print(f"  WARNING: No inventory entries for {station_net}.{station_sta}")
            return downloaded

        # Filter to date range using year/yearday
        sta_inv['date'] = pd.to_datetime(
            sta_inv['year'].astype(str) + sta_inv['yearday'].astype(str).str.zfill(3), 
            format='%Y%j'
        )
        sta_inv = sta_inv[
            (sta_inv['date'] >= pd.Timestamp(start_dt)) & 
            (sta_inv['date'] <= pd.Timestamp(end_dt))
        ]

        for _, row in sta_inv.iterrows():
            s3_key = row['dataacess_key']
            # Preserve the S3 directory structure locally
            local_path = os.path.join(sta_dir, s3_key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            if os.path.exists(local_path):
                downloaded.append({'station': f"{station_net}.{station_sta}", 
                                   'key': s3_key, 'local_path': local_path, 'status': 'skipped'})
                continue

            try:
                s3_client.download_file(bucket, s3_key, local_path)
                downloaded.append({'station': f"{station_net}.{station_sta}", 
                                   'key': s3_key, 'local_path': local_path, 'status': 'ok'})
            except Exception as e:
                error_str = str(e)
                if "ExpiredToken" in error_str or "Token expired" in error_str:
                    print("  -> Token expired. Refreshing credentials...")
                    s3_client = refresh_s3_client(es_client)
                    try:
                        s3_client.download_file(bucket, s3_key, local_path)
                        downloaded.append({'station': f"{station_net}.{station_sta}",
                                           'key': s3_key, 'local_path': local_path, 'status': 'ok'})
                    except Exception as e2:
                        downloaded.append({'station': f"{station_net}.{station_sta}",
                                           'key': s3_key, 'local_path': '', 'status': f'error: {e2}'})
                else:
                    downloaded.append({'station': f"{station_net}.{station_sta}",
                                       'key': s3_key, 'local_path': '', 'status': f'error: {e}'})
    else:
        # No inventory — scan S3 directly for this station's data
        prefix = f"miniseed/{station_net}/"

        # Iterate over each day in the date range
        current = start_dt
        while current <= end_dt:
            year = current.strftime("%Y")
            jday = current.strftime("%j")
            day_prefix = f"miniseed/{station_net}/{year}/{jday}/"

            try:
                resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=day_prefix)
                if 'Contents' in resp:
                    for obj in resp['Contents']:
                        s3_key = obj['Key']
                        filename = s3_key.split('/')[-1]
                        # Only download files that match this station
                        if filename.startswith(f"{station_sta}."):
                            local_path = os.path.join(sta_dir, s3_key)
                            os.makedirs(os.path.dirname(local_path), exist_ok=True)

                            if os.path.exists(local_path):
                                downloaded.append({'station': f"{station_net}.{station_sta}",
                                                   'key': s3_key, 'local_path': local_path, 'status': 'skipped'})
                                continue

                            s3_client.download_file(bucket, s3_key, local_path)
                            downloaded.append({'station': f"{station_net}.{station_sta}",
                                               'key': s3_key, 'local_path': local_path, 'status': 'ok'})
            except Exception as e:
                error_str = str(e)
                if "ExpiredToken" in error_str or "Token expired" in error_str:
                    print("  -> Token expired. Refreshing credentials...")
                    s3_client = refresh_s3_client(es_client)
                else:
                    print(f"  -> Error on {station_net}/{year}/{jday}: {e}")

            current += pd.Timedelta(days=1)

    return downloaded, s3_client


def main():
    parser = argparse.ArgumentParser(
        description="Download raw MiniSEED data for GridMeta station pairs from EarthScope S3."
    )
    parser.add_argument("--pairs", required=True, help="Path to GridMeta pairs CSV (global_station_pairs10k.csv)")
    parser.add_argument("--inventory", default=None, help="Path to s3_inventory.csv (optional, speeds up key resolution)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--outdir", default="raw_data", help="Output directory for downloaded data")
    parser.add_argument("--max-pairs", type=int, default=None, help="Maximum number of pairs to process")
    parser.add_argument("--networks", nargs="+", default=None, help="Filter to specific networks (e.g., CI IU)")
    parser.add_argument("--dist-min", type=float, default=None, help="Minimum pair distance (km)")
    parser.add_argument("--dist-max", type=float, default=None, help="Maximum pair distance (km)")
    args = parser.parse_args()

    # ==========================================
    # 1. Authenticate with EarthScope
    # ==========================================
    print("Authenticating with EarthScope...")
    es_client = EarthScopeClient()
    s3_client = refresh_s3_client(es_client)
    BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"
    print("Authenticated successfully.\n")

    t_start = time.time()

    # ==========================================
    # 2. Load and filter GridMeta pairs
    # ==========================================
    df_pairs = load_and_filter_pairs(
        args.pairs, args.start, args.end,
        networks=args.networks, max_pairs=args.max_pairs,
        dist_min=args.dist_min, dist_max=args.dist_max
    )

    # Save the filtered pairs alongside the data
    os.makedirs(args.outdir, exist_ok=True)
    pairs_out = os.path.join(args.outdir, "pairs_to_process.csv")
    df_pairs.to_csv(pairs_out, index=False)
    print(f"\nSaved filtered pairs to: {pairs_out}")

    # ==========================================
    # 3. Get unique stations to download
    # ==========================================
    stations = get_unique_stations(df_pairs)
    print(f"\nUnique stations to download: {len(stations)}")
    for net, sta in stations:
        print(f"  {net}.{sta}")

    # ==========================================
    # 4. Load inventory (if provided)
    # ==========================================
    inventory_df = None
    if args.inventory and os.path.exists(args.inventory):
        print(f"\nLoading inventory from {args.inventory}...")
        inventory_df = pd.read_csv(args.inventory)
        print(f"  Loaded {len(inventory_df):,} inventory records")

    # ==========================================
    # 5. Download data for each station
    # ==========================================
    print(f"\n{'='*60}")
    print(f"Downloading MiniSEED data: {args.start} → {args.end}")
    print(f"Output directory: {args.outdir}")
    print(f"{'='*60}\n")

    all_downloads = []
    for i, (net, sta) in enumerate(stations):
        print(f"[{i+1}/{len(stations)}] Downloading {net}.{sta}...")

        result, s3_client = download_station_data(
            s3_client, es_client, BUCKET, net, sta,
            args.start, args.end, args.outdir, inventory_df
        )
        all_downloads.extend(result)

        ok_count = sum(1 for d in result if d['status'] == 'ok')
        skip_count = sum(1 for d in result if d['status'] == 'skipped')
        err_count = sum(1 for d in result if d['status'].startswith('error'))
        print(f"  → {ok_count} downloaded, {skip_count} skipped, {err_count} errors")

    # ==========================================
    # 6. Save download manifest
    # ==========================================
    manifest = pd.DataFrame(all_downloads)
    manifest_path = os.path.join(args.outdir, "download_manifest.csv")
    manifest.to_csv(manifest_path, index=False)

    total_ok = sum(1 for d in all_downloads if d['status'] == 'ok')
    total_skip = sum(1 for d in all_downloads if d['status'] == 'skipped')
    total_err = sum(1 for d in all_downloads if d['status'].startswith('error'))

    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)
    hrs, mins = divmod(mins, 60)

    # Calculate total size of downloaded files
    total_bytes = 0
    for d in all_downloads:
        if d['status'] == 'ok' and d.get('local_path') and os.path.exists(d['local_path']):
            total_bytes += os.path.getsize(d['local_path'])
    size_mb = total_bytes / (1024 * 1024)

    print(f"\n{'='*60}")
    print(f"DOWNLOAD COMPLETE")
    print(f"{'='*60}")
    print(f"  Stations:   {len(stations)}")
    print(f"  Pairs:      {len(df_pairs)}")
    print(f"  Date range: {args.start} → {args.end}")
    print(f"  Downloaded: {total_ok:,} files ({size_mb:.1f} MB)")
    print(f"  Skipped:    {total_skip:,} files (already exist)")
    print(f"  Errors:     {total_err:,} files")
    print(f"  Time:       {hrs}h {mins}m {secs}s")
    if t_elapsed > 0 and total_ok > 0:
        print(f"  Speed:      {total_ok / t_elapsed:.1f} files/sec, {size_mb / (t_elapsed / 60):.1f} MB/min")
    print(f"  Manifest:   {manifest_path}")
    print(f"\nNext step:")
    print(f"  python sync_and_submit.py <bluehive_user> /scratch/<user>/singleNCFtest")


if __name__ == "__main__":
    main()
