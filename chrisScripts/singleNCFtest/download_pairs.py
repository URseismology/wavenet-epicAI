#!/usr/bin/env python
"""
download_pairs.py — GeoLab Download Script
===========================================
Reads GridMeta's station pairs CSV, finds overlapping days of data in S3
for each pair, and downloads ONLY the raw MiniSEED files that overlap.
Saves them locally in a structured directory tree ready for transfer to BlueHive.

Run this on GeoLab (EarthScope cloud) where S3 access is fast and free.

Usage:
    python download_pairs.py --pairs global_station_pairs10k.csv \
                             --start 2019-01-01 --end 2019-01-31 \
                             --outdir raw_data \
                             --max-pairs 10
"""

import os
import sys
import time
import argparse
import pandas as pd
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


def get_available_keys(s3_client, bucket, station_net, station_sta, start_dt, end_dt):
    """Scan S3 to find all available keys for a station within the date range."""
    available = {}
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
                    if filename.startswith(f"{station_sta}."):
                        # Record the date and the S3 key
                        available[current.strftime("%Y-%m-%d")] = s3_key
        except Exception as e:
            # Handle token expiration implicitly or just ignore missing days
            pass
            
        current += pd.Timedelta(days=1)
        
    return available


def main():
    parser = argparse.ArgumentParser(
        description="Download raw MiniSEED data for overlapping GridMeta station pairs."
    )
    parser.add_argument("--pairs", required=True, help="Path to GridMeta pairs CSV")
    parser.add_argument("--inventory", default=None, help="(Ignored) S3 Inventory")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--outdir", default="raw_data", help="Output directory")
    parser.add_argument("--max-pairs", type=int, default=None, help="Max pairs")
    parser.add_argument("--networks", nargs="+", default=None, help="Filter networks")
    parser.add_argument("--dist-min", type=float, default=None, help="Min distance (km)")
    parser.add_argument("--dist-max", type=float, default=None, help="Max distance (km)")
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
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    # ==========================================
    # 2. Load and filter pairs
    # ==========================================
    df_pairs = load_and_filter_pairs(
        args.pairs, args.start, args.end,
        networks=args.networks, max_pairs=args.max_pairs,
        dist_min=args.dist_min, dist_max=args.dist_max
    )
    os.makedirs(args.outdir, exist_ok=True)
    df_pairs.to_csv(os.path.join(args.outdir, "pairs_to_process.csv"), index=False)

    unique_stations = get_unique_stations(df_pairs)
    print(f"\nUnique stations to check: {len(unique_stations)}")

    # ==========================================
    # 3. Check Overlaps
    # ==========================================
    print(f"\n{'='*60}")
    print(f"Scanning S3 for overlaps: {args.start} → {args.end}")
    print(f"{'='*60}")
    
    # Pre-fetch available dates for all unique stations
    station_availability = {}
    for i, (net, sta) in enumerate(unique_stations):
        station_availability[f"{net}.{sta}"] = get_available_keys(
            s3_client, BUCKET, net, sta, start_dt, end_dt
        )
    
    # Calculate overlaps for pairs
    to_download = set()  # Store exact S3 keys to download
    total_overlapping_days = 0
    
    for _, row in df_pairs.iterrows():
        sta1 = f"{row['net1']}.{row['sta1']}"
        sta2 = f"{row['net2']}.{row['sta2']}"
        
        avail1 = station_availability.get(sta1, {})
        avail2 = station_availability.get(sta2, {})
        
        # Find intersecting dates
        overlap_dates = set(avail1.keys()).intersection(set(avail2.keys()))
        
        if overlap_dates:
            total_overlapping_days += len(overlap_dates)
            for date in overlap_dates:
                to_download.add((sta1, avail1[date]))
                to_download.add((sta2, avail2[date]))
        
        print(f"  {sta1} & {sta2}: {len(overlap_dates)} overlapping days")

    # ==========================================
    # 4. Download exact matching files
    # ==========================================
    print(f"\n{'='*60}")
    print(f"Downloading {len(to_download)} files (only overlapping days)")
    print(f"{'='*60}\n")

    all_downloads = []
    
    for i, (sta_label, s3_key) in enumerate(to_download):
        net, sta = sta_label.split('.')
        sta_dir = os.path.join(args.outdir, sta_label)
        os.makedirs(sta_dir, exist_ok=True)
        local_path = os.path.join(sta_dir, s3_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        if os.path.exists(local_path):
            all_downloads.append({'station': sta_label, 'key': s3_key, 'status': 'skipped'})
            continue
            
        try:
            resp = s3_client.get_object(Bucket=BUCKET, Key=s3_key)
            with open(local_path, "wb") as f:
                f.write(resp['Body'].read())
            all_downloads.append({'station': sta_label, 'key': s3_key, 'status': 'ok'})
            print(f"  [{i+1}/{len(to_download)}] Downloaded {sta_label} ({s3_key.split('/')[-2]}/{s3_key.split('/')[-1]})")
        except Exception as e:
            error_str = str(e)
            if "ExpiredToken" in error_str or "Token expired" in error_str:
                print("  -> Token expired. Refreshing credentials...")
                s3_client = refresh_s3_client(es_client)
                try:
                    resp = s3_client.get_object(Bucket=BUCKET, Key=s3_key)
                    with open(local_path, "wb") as f:
                        f.write(resp['Body'].read())
                    all_downloads.append({'station': sta_label, 'key': s3_key, 'status': 'ok'})
                except Exception as e2:
                    all_downloads.append({'station': sta_label, 'key': s3_key, 'status': f'error: {e2}'})
            else:
                all_downloads.append({'station': sta_label, 'key': s3_key, 'status': f'error: {e}'})

    # ==========================================
    # 5. Summary
    # ==========================================
    manifest = pd.DataFrame(all_downloads)
    if not manifest.empty:
        manifest_path = os.path.join(args.outdir, "download_manifest.csv")
        manifest.to_csv(manifest_path, index=False)

    total_ok = sum(1 for d in all_downloads if d['status'] == 'ok')
    total_skip = sum(1 for d in all_downloads if d['status'] == 'skipped')
    total_err = sum(1 for d in all_downloads if d['status'].startswith('error'))

    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)
    hrs, mins = divmod(mins, 60)

    print(f"\n{'='*60}")
    print(f"DOWNLOAD COMPLETE")
    print(f"{'='*60}")
    print(f"  Pairs Processed:   {len(df_pairs)}")
    print(f"  Overlapping Days:  {total_overlapping_days}")
    print(f"  Files Downloaded:  {total_ok:,}")
    print(f"  Files Skipped:     {total_skip:,} (already exist)")
    print(f"  Errors:            {total_err:,}")
    print(f"  Total Time:        {hrs}h {mins}m {secs}s")


if __name__ == "__main__":
    main()
