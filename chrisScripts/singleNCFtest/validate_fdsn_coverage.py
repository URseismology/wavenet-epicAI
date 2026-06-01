#!/usr/bin/env python
"""
validate_fdsn_coverage.py — Check FDSN availability for GridMeta stations
=========================================================================
Reads the global_station_pairs10k.csv, extracts all unique stations,
and verifies each one exists on the FDSN web service with data in
the requested time window.

This does NOT download waveforms — it only queries station metadata,
so it runs in minutes, not hours.

Usage:
    python validate_fdsn_coverage.py \
        --pairs global_station_pairs10k.csv \
        --start 2019-01-01 --end 2019-01-31
"""

import os
import sys
import time
import argparse
import pandas as pd
from obspy.clients.fdsn import Client
from obspy import UTCDateTime


def main():
    parser = argparse.ArgumentParser(
        description="Validate FDSN coverage for GridMeta station pairs."
    )
    parser.add_argument("--pairs", required=True, help="Path to pairs CSV")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--sample", type=int, default=None,
                        help="Only check a random sample of N stations (for quick testing)")
    args = parser.parse_args()

    t_start = time.time()

    # 1. Load pairs and extract unique stations
    print(f"Loading pairs from {args.pairs}...")
    df = pd.read_csv(args.pairs)
    print(f"  {len(df):,} pairs loaded")

    stations = set()
    for _, row in df.iterrows():
        stations.add((row['net1'], row['sta1']))
        stations.add((row['net2'], row['sta2']))
    stations = sorted(stations)
    print(f"  {len(stations):,} unique stations extracted")

    if args.sample:
        import random
        random.seed(42)
        stations = random.sample(stations, min(args.sample, len(stations)))
        print(f"  Sampled down to {len(stations)} stations for testing")

    # 2. Connect to FDSN
    print(f"\nConnecting to FDSN (IRIS)...")
    client = Client("IRIS")

    starttime = UTCDateTime(args.start)
    endtime = UTCDateTime(args.end)

    # 3. Check each station
    available = []
    missing = []
    errors = []

    print(f"Checking {len(stations)} stations for data in {args.start} → {args.end}...\n")

    for i, (net, sta) in enumerate(stations):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [{i+1}/{len(stations)}] checking {net}.{sta}...")

        try:
            inv = client.get_stations(
                network=net, station=sta,
                starttime=starttime, endtime=endtime,
                level="station"
            )
            if len(inv.networks) > 0 and len(inv.networks[0].stations) > 0:
                available.append((net, sta))
            else:
                missing.append((net, sta))
        except Exception as e:
            err_str = str(e)
            if "No data available" in err_str:
                missing.append((net, sta))
            else:
                errors.append((net, sta, err_str))

    # 4. Results
    t_elapsed = time.time() - t_start
    mins, secs = divmod(int(t_elapsed), 60)

    pct_available = len(available) / len(stations) * 100 if stations else 0
    pct_missing = len(missing) / len(stations) * 100 if stations else 0

    print(f"\n{'='*60}")
    print(f"FDSN COVERAGE REPORT")
    print(f"{'='*60}")
    print(f"  Date range:  {args.start} → {args.end}")
    print(f"  Total checked: {len(stations):,}")
    print(f"  Available:     {len(available):,} ({pct_available:.1f}%)")
    print(f"  Missing:       {len(missing):,} ({pct_missing:.1f}%)")
    print(f"  Errors:        {len(errors):,}")
    print(f"  Time:          {mins}m {secs}s")

    if missing:
        print(f"\n  Missing stations (first 20):")
        for net, sta in missing[:20]:
            print(f"    {net}.{sta}")
        if len(missing) > 20:
            print(f"    ... and {len(missing) - 20} more")

    if errors:
        print(f"\n  Errors (first 10):")
        for net, sta, err in errors[:10]:
            print(f"    {net}.{sta}: {err[:80]}")

    # 5. Save detailed results
    results = []
    for net, sta in available:
        results.append({'network': net, 'station': sta, 'status': 'available'})
    for net, sta in missing:
        results.append({'network': net, 'station': sta, 'status': 'missing'})
    for net, sta, err in errors:
        results.append({'network': net, 'station': sta, 'status': f'error: {err[:100]}'})

    out_path = "fdsn_coverage_report.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"\n  Full report saved to: {out_path}")


if __name__ == "__main__":
    main()
