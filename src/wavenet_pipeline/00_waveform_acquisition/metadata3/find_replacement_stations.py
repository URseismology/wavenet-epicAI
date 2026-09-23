#!/usr/bin/env python
"""
For each of the 2,000-station network's stations with zero valid connections (367 as
of 2026-09-23, see key_index_summary/station_connectivity.csv), searches the live FDSN
station service for nearby real alternative stations, then cross-references our own
already-built key index to confirm the candidate actually has real recorded data (not
just a registered station with no archived miniSEED). Bounded, cheap live queries (a
station-metadata request per uncovered station, not a bulk download) -- per PI
direction (2026-09-23).

Known limitation, explicit per PI direction: only checks whether a candidate has ANY
data in the key index at all -- the index has no channel granularity (confirmed
earlier this session), so BH?/LH? presence specifically is NOT verified here. Deferred
to a later, per-candidate live channel check before actually using any replacement.
"""
import os
import time

import numpy as np
import pandas as pd
from obspy.clients.fdsn import Client
import pyarrow.dataset as ds

META_DIR = os.path.dirname(__file__)
OUT_DIR = os.path.join(META_DIR, "key_index_summary")
SEARCH_RADIUS_DEG = 10.0  # ~1,100 km at the equator -- matches our production band's upper end


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def load_uncovered_stations():
    conn = pd.read_csv(os.path.join(OUT_DIR, "station_connectivity.csv"))
    uncovered_keys = set(conn[conn["n_connections"] == 0]["station"])
    sta = pd.read_csv(os.path.join(META_DIR, "fps_stations.csv"))
    sta["key"] = sta["network"].astype(str) + "." + sta["station"].astype(str)
    return sta[sta["key"].isin(uncovered_keys)].reset_index(drop=True)


def load_key_index_day_counts():
    """key -> total_days, for confirming a candidate actually has archived data."""
    dataset = ds.dataset(os.path.join(META_DIR, "keys_partitioned_year_full"),
                         format="parquet", partitioning="hive")
    tbl = dataset.to_table(columns=["network", "station", "yearday"])
    df = tbl.to_pandas()
    df["key"] = df["network"].astype(str) + "." + df["station"].astype(str)
    return df.groupby("key", observed=True).size().to_dict()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only process the first N uncovered stations (smoke test)")
    args = ap.parse_args()

    uncovered = load_uncovered_stations()
    if args.limit:
        uncovered = uncovered.head(args.limit)
    print(f"Searching replacements for {len(uncovered)} uncovered stations...")
    day_counts = load_key_index_day_counts()
    print(f"Key index loaded: {len(day_counts):,} distinct stations with real archived data")

    our_keys = set(pd.read_csv(os.path.join(META_DIR, "fps_stations.csv"))
                   .assign(key=lambda d: d["network"].astype(str) + "." + d["station"].astype(str))["key"])

    client = Client("EARTHSCOPE")
    rows = []
    for idx, row in uncovered.iterrows():
        lat, lon, orig_key = row["lat"], row["lon"], row["key"]
        try:
            inv = client.get_stations(
                minlatitude=lat - SEARCH_RADIUS_DEG, maxlatitude=lat + SEARCH_RADIUS_DEG,
                minlongitude=lon - SEARCH_RADIUS_DEG, maxlongitude=lon + SEARCH_RADIUS_DEG,
                level="station",
            )
        except Exception as e:
            rows.append(dict(original_station=orig_key, status=f"FDSN query failed: {type(e).__name__}: {e}"))
            continue

        candidates = []
        for net in inv:
            for sta in net:
                cand_key = f"{net.code}.{sta.code}"
                if cand_key == orig_key or cand_key in our_keys:
                    continue
                dist = haversine_km(lat, lon, sta.latitude, sta.longitude)
                days = day_counts.get(cand_key, 0)
                candidates.append((cand_key, sta.latitude, sta.longitude, dist, days))

        candidates_with_data = [c for c in candidates if c[4] > 0]
        candidates_with_data.sort(key=lambda c: c[3])  # nearest first

        if candidates_with_data:
            best = candidates_with_data[0]
            rows.append(dict(
                original_station=orig_key, replacement_station=best[0],
                distance_km=round(best[3], 1), replacement_days_in_key_index=best[4],
                n_candidates_found=len(candidates), n_candidates_with_data=len(candidates_with_data),
                status="OK",
            ))
        else:
            rows.append(dict(
                original_station=orig_key, replacement_station=None,
                n_candidates_found=len(candidates), n_candidates_with_data=0,
                status="no nearby candidate with confirmed archived data",
            ))

        if (idx + 1) % 50 == 0:
            print(f"  ... {idx+1}/{len(uncovered)} searched")
        time.sleep(0.05)  # light politeness delay, not required but cheap to include

    out = pd.DataFrame(rows)
    path = os.path.join(OUT_DIR, "replacement_station_candidates.csv")
    out.to_csv(path, index=False)
    n_found = (out["status"] == "OK").sum()
    print(f"\nWrote {path}")
    print(f"Found a confirmed-has-data replacement for {n_found}/{len(uncovered)} "
          f"uncovered stations ({100*n_found/len(uncovered):.1f}%)")
    if n_found:
        print(f"Replacement distances: median {out[out['status']=='OK']['distance_km'].median():.1f} km, "
              f"max {out[out['status']=='OK']['distance_km'].max():.1f} km")


if __name__ == "__main__":
    main()
