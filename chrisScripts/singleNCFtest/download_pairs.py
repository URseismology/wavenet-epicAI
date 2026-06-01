#!/usr/bin/env python
"""
download_pairs.py — GeoLab Download Script (Fast Inventory Overlaps)
====================================================================
Reads GridMeta's station pairs CSV, finds overlapping days of data for each pair
using a local inventory file (s3_inventory.csv), and downloads ONLY the raw
MiniSEED files that overlap.

Saves them locally in a structured directory tree ready for transfer to BlueHive.

Run this on GeoLab (EarthScope cloud) where S3 access is fast and free.

Usage:
    # To download using the fast pre-computed inventory (RECOMMENDED):
    python download_pairs.py --pairs global_station_pairs10k.csv \
                             --inventory s3_inventory.csv \
                             --outdir raw_data \
                             --max-pairs 5 \
                             --networks CI

    # Without inventory (Slow - scans S3 directly):
    python download_pairs.py --pairs global_station_pairs10k.csv --outdir raw_data --max-pairs 1
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


def load_and_filter_pairs(pairs_file, networks=None, max_pairs=None,
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


def get_available_keys(s3_client, bucket, station_net, station_sta, start_dt=None, end_dt=None):
    """Scan S3 to find all available keys for a station. Slow fallback if no inventory."""
    available = {}
    
    if start_dt and end_dt:
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
                            available[current.strftime("%Y-%m-%d")] = s3_key
            except Exception:
                pass
            current += pd.Timedelta(days=1)
    else:
        try:
            resp_years = s3_client.list_objects_v2(Bucket=bucket, Prefix=f"miniseed/{station_net}/", Delimiter='/')
            years = [p['Prefix'].split('/')[-2] for p in resp_years.get('CommonPrefixes', [])]
            
            for year in sorted(years):
                resp_days = s3_client.list_objects_v2(Bucket=bucket, Prefix=f"miniseed/{station_net}/{year}/", Delimiter='/')
                days = [p['Prefix'].split('/')[-2] for p in resp_days.get('CommonPrefixes', [])]
                
                for jday in sorted(days):
                    day_prefix = f"miniseed/{station_net}/{year}/{jday}/"
                    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=day_prefix)
                    if 'Contents' in resp:
                        for obj in resp['Contents']:
                            s3_key = obj['Key']
                            filename = s3_key.split('/')[-1]
                            if filename.startswith(f"{station_sta}."):
                                dt_str = datetime.strptime(f"{year}{jday}", "%Y%j").strftime("%Y-%m-%d")
                                available[dt_str] = s3_key
        except Exception as e:
            print(f"  -> Warning: Failed to scan full history for {station_net}: {e}")

    return available


def build_availability_from_inventory(inventory_file, unique_stations, start_dt=None, end_dt=None):
    """Instantly build the availability dictionary from the local CSV inventory."""
    print(f"Loading inventory from {inventory_file}...")
    inv_df = pd.read_csv(inventory_file)
    
    # We only care about the unique stations we need
    # Convert unique_stations (list of tuples) to a format for filtering
    nets = [n for n, s in unique_stations]
    stas = [s for n, s in unique_stations]
    mask = inv_df['network'].isin(nets) & inv_df['station'].isin(stas)
    inv_df = inv_df[mask].copy()

    # Apply date filter if provided
    if start_dt and end_dt:
        inv_df['date_ts'] = pd.to_datetime(
            inv_df['year'].astype(str) + inv_df['yearday'].astype(str).str.zfill(3), 
            format='%Y%j'
        )
        inv_df = inv_df[
            (inv_df['date_ts'] >= pd.Timestamp(start_dt)) & 
            (inv_df['date_ts'] <= pd.Timestamp(end_dt))
        ]

    # Build dictionary: dict['NET.STA']['YYYY-MM-DD'] = 'dataacess_key'
    station_availability = {}
    for _, row in inv_df.iterrows():
        sta = f"{row['network']}.{row['station']}"
        date_str = pd.to_datetime(f"{row['year']}{str(row['yearday']).zfill(3)}", format='%Y%j').strftime("%Y-%m-%d")
        
        if sta not in station_availability:
            station_availability[sta] = {}
        station_availability[sta][date_str] = row['dataacess_key']
        
    return station_availability


def main():
    parser = argparse.ArgumentParser(
        description="Download raw MiniSEED data for overlapping GridMeta station pairs."
    )
    parser.add_argument("--pairs", required=True, help="Path to GridMeta pairs CSV")
    parser.add_argument("--inventory", default=None, help="Path to s3_inventory.csv (Fast mode)")
    parser.add_argument("--start", default=None, help="(Optional) Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="(Optional) End date (YYYY-MM-DD)")
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
    
    start_dt = None
    end_dt = None
    if args.start and args.end:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d")
        end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    # ==========================================
    # 2. Load and filter pairs
    # ==========================================
    df_pairs = load_and_filter_pairs(
        args.pairs, networks=args.networks, max_pairs=args.max_pairs,
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
    if args.inventory and os.path.exists(args.inventory):
        print(f"Using local inventory for INSTANT overlap calculation: {args.inventory}")
        print(f"{'='*60}")
        station_availability = build_availability_from_inventory(
            args.inventory, unique_stations, start_dt, end_dt
        )
    else:
        if args.inventory:
            print(f"Warning: Inventory '{args.inventory}' not found. Falling back to S3 scan.")
        if start_dt and end_dt:
            print(f"Scanning S3 for overlaps: {args.start} → {args.end}")
        else:
            print(f"Scanning S3 for ALL-TIME overlaps (this may take a few minutes...)")
        print(f"{'='*60}")
        
        station_availability = {}
        for i, (net, sta) in enumerate(unique_stations):
            print(f"  Scanning history for {net}.{sta}...")
            station_availability[f"{net}.{sta}"] = get_available_keys(
                s3_client, BUCKET, net, sta, start_dt, end_dt
            )
    
    # Calculate overlaps for pairs
    print(f"\n{'='*60}")
    print("Calculating exact pair overlaps...")
    print(f"{'='*60}")
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
    if not to_download:
        print("\nNo overlaps found. Nothing to download.")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Downloading {len(to_download)} files (only overlapping days)")
    print(f"{'='*60}\n")

    all_downloads = []
    
    # Sort for consistent progress output
    to_download_sorted = sorted(list(to_download))
    
    for i, (sta_label, s3_key) in enumerate(to_download_sorted):
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
            print(f"  [{i+1}/{len(to_download_sorted)}] Downloaded {sta_label} ({s3_key.split('/')[-2]}/{s3_key.split('/')[-1]})")
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
