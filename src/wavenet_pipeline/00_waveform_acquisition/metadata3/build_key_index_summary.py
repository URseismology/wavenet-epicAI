#!/usr/bin/env python
"""
Queries the full EarthScope key index (keys_partitioned_year_full/, 60,125 stations,
35M records, 1.09 PB total raw data across the whole archive) down to just our fixed
2,000-station network (fps_stations.csv), and builds the summary datasets used in
DATA_AVAILABILITY_AND_COST_REPORT.md. We don't need the other 58,125 stations' records
for this -- this script's whole job is the query/filter step.

Outputs (key_index_summary/):
  station_summary.csv       -- per station: days, bytes, date range, in-network flag
  daily_coverage.csv/.png   -- per calendar day: how many of our 2000 stations had data
  pair_size_estimate.csv    -- fps_pairs.csv + relaxed-band candidates: shared-days-gated
                                pair counts and overlap-day download volume
  north_star_min_coverage.csv -- greedy minimum-edge-cover sizing (see PROGRESS.md)
"""
import os

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

META_DIR = os.path.dirname(__file__)
OUT_DIR = os.path.join(META_DIR, "key_index_summary")
os.makedirs(OUT_DIR, exist_ok=True)

PACKAGED_KB_PER_CHANNEL_DAY = 350  # confirmed real range 300-410 KB/channel-day (Bluehive tests)
TIER_SPLIT_KM, TIER1_MIN_DAYS, TIER2_MIN_DAYS = 1500, 90, 365


def load_index_for_our_network(stations_csv):
    sta = pd.read_csv(stations_csv)
    sta["key"] = sta["network"].astype(str) + "." + sta["station"].astype(str)
    networks = set(sta["network"].astype(str))

    print(f"Our network: {len(sta)} stations across {len(networks)} distinct networks")
    print("Filtering the 60,125-station/35M-record full index down to just these "
          f"{len(networks)} networks first (pushdown filter), then to our exact "
          f"{len(sta)} stations...")

    dataset = ds.dataset(os.path.join(META_DIR, "keys_partitioned_year_full"),
                         format="parquet", partitioning="hive")
    tbl = dataset.to_table(
        columns=["network", "station", "yearday", "year", "size_bytes"],
        filter=ds.field("network").isin(list(networks)),
    )
    df = tbl.to_pandas()
    df["key"] = df["network"].astype(str) + "." + df["station"].astype(str)
    df = df[df["key"].isin(set(sta["key"]))]
    print(f"After filtering to our exact stations: {len(df):,} records "
          f"(from the full archive's 35,026,531)")
    return sta, df


def build_station_summary(sta, df):
    g = df.groupby("key").agg(
        total_days=("yearday", "count"),
        total_bytes=("size_bytes", "sum"),
        year_min=("year", "min"),
        year_max=("year", "max"),
    ).reset_index()
    out = sta.merge(g, on="key", how="left")
    out["in_fixed_2000_network"] = True
    out["total_days"] = out["total_days"].fillna(0).astype(int)
    out["total_bytes"] = out["total_bytes"].fillna(0).astype(np.int64)
    path = os.path.join(OUT_DIR, "station_summary.csv")
    out.to_csv(path, index=False)
    print(f"Wrote {path} ({len(out)} rows)")
    return out


def build_daily_coverage(df):
    df = df.copy()
    df["day_id"] = df["year"].astype(np.int32) * 1000 + df["yearday"].astype(np.int32)
    daily = df.groupby("day_id")["key"].nunique().reset_index(name="stations_with_data")
    daily["year"] = daily["day_id"] // 1000
    daily["yearday"] = daily["day_id"] % 1000
    daily["date"] = pd.to_datetime(daily["year"].astype(str) + daily["yearday"].astype(str).str.zfill(3),
                                    format="%Y%j", errors="coerce")
    daily = daily.dropna(subset=["date"]).sort_values("date")
    path = os.path.join(OUT_DIR, "daily_coverage.csv")
    daily[["date", "stations_with_data"]].to_csv(path, index=False)
    print(f"Wrote {path} ({len(daily)} days)")

    BG = "#111111"
    fig, ax = plt.subplots(figsize=(16, 6), facecolor=BG)
    ax.set_facecolor(BG)
    ax.plot(daily["date"], daily["stations_with_data"], color="#e8c87a", linewidth=0.8)
    ax.fill_between(daily["date"], daily["stations_with_data"], color="#e8c87a", alpha=0.25)
    ax.set_title("Map of Connected Days -- stations (of our fixed 2,000) with data per day",
                  color="#dddddd", fontsize=13)
    ax.set_xlabel("Date", color="#dddddd")
    ax.set_ylabel("Stations with data that day", color="#dddddd")
    ax.tick_params(colors="#dddddd")
    for spine in ax.spines.values():
        spine.set_color("#484848")
    ax.grid(color="#484848", linewidth=0.3, alpha=0.4)
    png_path = os.path.join(OUT_DIR, "daily_coverage_timeline.png")
    fig.savefig(png_path, dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {png_path}")
    return daily


def load_day_sizes(df):
    """key -> {day_id: size_bytes}, for pair overlap-volume computation."""
    df = df.copy()
    df["day_id"] = df["year"].astype(np.int32) * 1000 + df["yearday"].astype(np.int32)
    out = {}
    for key, grp in df.groupby("key", observed=True):
        out[key] = dict(zip(grp["day_id"], grp["size_bytes"]))
    return out


def build_pair_size_estimate(sta, day_sizes):
    import sys
    sys.path.insert(0, META_DIR)
    from build_pairs import legal_pairs_within_band

    bands = [
        ("110-1110 (current)", 110, 1110),
        ("110-1500", 110, 1500),
        ("110-2000", 110, 2000),
        ("110-3000", 110, 3000),
        ("50-3000", 50, 3000),
        ("50-5000", 50, 5000),
    ]
    TIER_SPLIT_KM, TIER1_MIN_DAYS, TIER2_MIN_DAYS = 1500, 90, 365

    rows = []
    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()
    keys = sta["key"].to_numpy()
    for label, lo, hi in bands:
        i_idx, j_idx, km = legal_pairs_within_band(lat, lon, lo, hi)
        n_geometric = len(i_idx)
        n_passed = 0
        # CORRECT metric (fixed 2026-09-23, see DATA_AVAILABILITY_AND_COST_REPORT.md sec 8):
        # the real download requirement is the UNION, per station, of days it needs
        # across ALL its pair partners -- NOT the sum of each pair's overlap-bytes
        # separately, which double-counts any station that appears in multiple pairs
        # (average degree here is ~10 pairs/station) and can exceed the true
        # full-history ceiling (78.9 TB), which is nonsensical.
        required_days_per_station = {}
        for i, j, d in zip(i_idx, j_idx, km):
            ki, kj = keys[i], keys[j]
            days_i = day_sizes.get(ki, {})
            days_j = day_sizes.get(kj, {})
            shared = set(days_i) & set(days_j)
            min_days = TIER1_MIN_DAYS if d < TIER_SPLIT_KM else TIER2_MIN_DAYS
            if len(shared) < min_days:
                continue
            n_passed += 1
            required_days_per_station.setdefault(ki, set()).update(shared)
            required_days_per_station.setdefault(kj, set()).update(shared)
        required_bytes = sum(
            day_sizes.get(k, {}).get(d_, 0)
            for k, days in required_days_per_station.items() for d_ in days
        )
        rows.append(dict(
            band=label, sep_min_km=lo, sep_max_km=hi,
            geometric_pairs=n_geometric, pairs_passing_shared_days_gate=n_passed,
            pass_rate_pct=round(100 * n_passed / n_geometric, 1) if n_geometric else 0,
            stations_with_any_valid_pair=len(required_days_per_station),
            required_download_gb=round(required_bytes / 1e9, 2),
        ))
    out = pd.DataFrame(rows)
    path = os.path.join(OUT_DIR, "pair_size_estimate.csv")
    out.to_csv(path, index=False)
    print(f"Wrote {path}")
    print(out.to_string(index=False))
    return out


def build_north_star_min_coverage(sta, day_sizes):
    """Greedy minimum-edge-cover: select a minimum set of pairs so every one of the
    2000 stations touches >=1 selected pair.

    Uses the FRESHLY recomputed, complete pair set at the current production band
    (110-1110km), not the committed fps_pairs.csv -- fixed 2026-09-23 after finding
    fps_pairs.csv (9,743 pairs, built against an earlier/incomplete key-index scan) is
    stale relative to the full index's real 10,626 passing pairs, which caused a real
    discrepancy (407 vs. 367 "uncovered" stations) between this function and
    build_connectivity_analysis.py's independently-recomputed coverage. Does NOT write
    over fps_pairs.csv -- station/pair selection file stays locked."""
    import sys
    sys.path.insert(0, META_DIR)
    from build_pairs import legal_pairs_within_band

    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()
    keys = sta["key"].to_numpy()
    i_idx, j_idx, km = legal_pairs_within_band(lat, lon, 110, 1110)
    rows = []
    for i, j, d in zip(i_idx, j_idx, km):
        ki, kj = keys[i], keys[j]
        days_i, days_j = day_sizes.get(ki, {}), day_sizes.get(kj, {})
        shared = set(days_i) & set(days_j)
        min_days = TIER1_MIN_DAYS if d < TIER_SPLIT_KM else TIER2_MIN_DAYS
        if len(shared) < min_days:
            continue
        rows.append(dict(key1=ki, key2=kj, distance_km=d, shared_days=len(shared)))
    pairs = pd.DataFrame(rows)

    all_stations = set(sta["key"])
    covered = set()
    selected = []
    # Greedy: sort by shared_days descending so we tend to pick well-connected, cheap-to-justify pairs first
    pairs_sorted = pairs.sort_values("shared_days", ascending=False)
    for _, row in pairs_sorted.iterrows():
        if row["key1"] not in covered or row["key2"] not in covered:
            selected.append(row)
            covered.add(row["key1"])
            covered.add(row["key2"])
        if covered >= all_stations:
            break

    uncovered = all_stations - covered
    # CORRECT metric (fixed 2026-09-23): per-station UNION of required days across all
    # its selected pairs, not a per-pair sum -- see build_pair_size_estimate's docstring
    # for why summing per-pair double-counts and can exceed the true full-history
    # ceiling. A station covered by more than one selected pair (possible even in a
    # greedy minimum-edge-cover) must not have its days counted twice.
    required_days_per_station = {}
    for row in selected:
        days_i = day_sizes.get(row["key1"], {})
        days_j = day_sizes.get(row["key2"], {})
        shared = set(days_i) & set(days_j)
        required_days_per_station.setdefault(row["key1"], set()).update(shared)
        required_days_per_station.setdefault(row["key2"], set()).update(shared)

    total_raw_bytes = sum(
        day_sizes.get(k, {}).get(d, 0)
        for k, days in required_days_per_station.items() for d in days
    )
    total_unique_station_days = sum(len(days) for days in required_days_per_station.values())

    n_channels_assumed = 5  # confirmed real range 3-6 channels/station, use middle
    packaged_bytes_est = total_unique_station_days * n_channels_assumed * PACKAGED_KB_PER_CHANNEL_DAY * 1024

    out = pd.DataFrame(selected)
    path = os.path.join(OUT_DIR, "north_star_min_coverage.csv")
    out.to_csv(path, index=False)

    summary = dict(
        stations_covered=len(covered), stations_total=len(all_stations),
        stations_uncovered=len(uncovered), pairs_selected=len(selected),
        total_unique_station_days=total_unique_station_days,
        required_download_gb=round(total_raw_bytes / 1e9, 2),
        packaged_size_estimate_gb=round(packaged_bytes_est / 1e9, 2),
        packaged_size_assumption=f"{n_channels_assumed} channels/station x {PACKAGED_KB_PER_CHANNEL_DAY} KB/channel-day (confirmed real range 300-410 KB)",
    )
    print("\nNorth Star minimum-coverage summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if uncovered:
        print(f"  WARNING: {len(uncovered)} stations have NO valid pair at the current "
              f"110-1110km/90-365day band and are not covered by any selected pair.")
    pd.Series(summary).to_csv(os.path.join(OUT_DIR, "north_star_summary.csv"))
    return out, summary


if __name__ == "__main__":
    sta, df = load_index_for_our_network(os.path.join(META_DIR, "fps_stations.csv"))
    build_station_summary(sta, df)
    build_daily_coverage(df)
    day_sizes = load_day_sizes(df)
    build_pair_size_estimate(sta, day_sizes)
    build_north_star_min_coverage(sta, day_sizes)
