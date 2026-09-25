#!/usr/bin/env python
"""
Coverage-density analysis (2026-09-23 request): the actual design goal is not "more
pairs" but improving GLOBAL ray-path coverage density with limited redundancy -- most
ambient-noise tomography studies operate in some band that balances the two, not
necessarily the widest one. This computes a real, comparable metric per band using the
existing key index (metadata only -- no download), descriptive, not a final choice.

Metric, per candidate band, on the same 2-degree grid plot_connection_heatmap.py uses:
  - pct_cells_covered: fraction of global grid cells crossed by >=1 valid pair's path
  - mean_crossings_per_covered_cell: average path density where coverage exists
  - redundancy_cv: coefficient of variation (std/mean) of crossing-counts across
    covered cells -- LOWER means more uniform coverage (less redundant clustering),
    HIGHER means paths pile up on already-covered routes instead of reaching new area
  - marginal_new_cells_per_1000_pairs: how much NEW area a wider band actually buys,
    vs. just adding more pairs that retrace already-covered paths
"""
import os

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

META_DIR = os.path.dirname(__file__)
OUT_DIR = os.path.join(META_DIR, "key_index_summary")

import sys
sys.path.insert(0, META_DIR)
from build_pairs import legal_pairs_within_band

TIER_SPLIT_KM, TIER1_MIN_DAYS, TIER2_MIN_DAYS = 1500, 90, 365
GRID_DEG = 2.0
LON_BINS = np.arange(-180, 180 + GRID_DEG, GRID_DEG)
LAT_BINS = np.arange(-90, 90 + GRID_DEG, GRID_DEG)
N_STEPS = 50  # points sampled along each great-circle-ish path (straight lat/lon interpolation, matches plot_connection_heatmap.py's existing approximation)

BANDS = [
    ("110-1110 (current)", 110, 1110),
    ("110-1500", 110, 1500),
    ("110-2000", 110, 2000),
    ("110-3000", 110, 3000),
    ("50-3000", 50, 3000),
    ("50-5000", 50, 5000),
    ("50-10000", 50, 10000),
    ("50-20015 (180 deg, antipodal max)", 50, 20015),  # PI request 2026-09-23: go all the way to the max possible great-circle separation
]


def load_day_sizes(stations_csv):
    sta = pd.read_csv(stations_csv)
    sta["key"] = sta["network"].astype(str) + "." + sta["station"].astype(str)
    networks = set(sta["network"].astype(str))
    dataset = ds.dataset(os.path.join(META_DIR, "keys_partitioned_year_full"),
                         format="parquet", partitioning="hive")
    tbl = dataset.to_table(columns=["network", "station", "yearday", "year"],
                            filter=ds.field("network").isin(list(networks)))
    df = tbl.to_pandas()
    df["key"] = df["network"].astype(str) + "." + df["station"].astype(str)
    df = df[df["key"].isin(set(sta["key"]))]
    df["day_id"] = df["year"].astype(np.int32) * 1000 + df["yearday"].astype(np.int32)
    day_sets = {k: set(g["day_id"]) for k, g in df.groupby("key", observed=True)}
    return sta, day_sets


def passing_pairs_for_band(sta, day_sets, lo, hi):
    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()
    keys = sta["key"].to_numpy()
    i_idx, j_idx, km = legal_pairs_within_band(lat, lon, lo, hi)
    out = []
    for i, j, d in zip(i_idx, j_idx, km):
        ki, kj = keys[i], keys[j]
        shared = day_sets.get(ki, set()) & day_sets.get(kj, set())
        min_days = TIER1_MIN_DAYS if d < TIER_SPLIT_KM else TIER2_MIN_DAYS
        if len(shared) >= min_days:
            out.append((sta.iloc[i]["lat"], sta.iloc[i]["lon"], sta.iloc[j]["lat"], sta.iloc[j]["lon"]))
    return out


def heatmap_grid(pairs):
    heat = np.zeros((len(LAT_BINS) - 1, len(LON_BINS) - 1))
    for lat1, lon1, lat2, lon2 in pairs:
        lats = np.linspace(lat1, lat2, N_STEPS)
        lons = np.linspace(lon1, lon2, N_STEPS)
        lat_idx = np.clip(np.digitize(lats, LAT_BINS) - 1, 0, len(LAT_BINS) - 2)
        lon_idx = np.clip(np.digitize(lons, LON_BINS) - 1, 0, len(LON_BINS) - 2)
        seen = set(zip(lat_idx, lon_idx))  # count each cell once per PATH, not once per sample point along it
        for la, lo in seen:
            heat[la, lo] += 1
    return heat


def main():
    sta, day_sets = load_day_sizes(os.path.join(META_DIR, "fps_stations.csv"))
    total_cells = (len(LAT_BINS) - 1) * (len(LON_BINS) - 1)

    rows = []
    prev_covered_mask = None
    prev_n_pairs = 0
    for label, lo, hi in BANDS:
        pairs = passing_pairs_for_band(sta, day_sets, lo, hi)
        heat = heatmap_grid(pairs)
        covered_mask = heat > 0
        n_covered = covered_mask.sum()
        covered_vals = heat[covered_mask]
        cv = float(covered_vals.std() / covered_vals.mean()) if len(covered_vals) and covered_vals.mean() > 0 else float("nan")

        if prev_covered_mask is None:
            new_cells = n_covered
        else:
            new_cells = int((covered_mask & ~prev_covered_mask).sum())
        added_pairs = len(pairs) - prev_n_pairs
        marginal = round(1000 * new_cells / added_pairs, 2) if added_pairs > 0 else float("nan")

        rows.append(dict(
            band=label, n_pairs=len(pairs),
            pct_cells_covered=round(100 * n_covered / total_cells, 1),
            mean_crossings_per_covered_cell=round(float(covered_vals.mean()), 2) if len(covered_vals) else 0,
            redundancy_cv=round(cv, 3),
            new_cells_vs_previous_band=new_cells,
            marginal_new_cells_per_1000_added_pairs=marginal,
        ))
        prev_covered_mask = covered_mask
        prev_n_pairs = len(pairs)

    out = pd.DataFrame(rows)
    path = os.path.join(OUT_DIR, "coverage_density_by_band.csv")
    out.to_csv(path, index=False)
    print(f"Wrote {path}")
    print(out.to_string(index=False))
    print("\nReading this table: pct_cells_covered growth that FLATTENS while n_pairs "
          "keeps growing, combined with redundancy_cv climbing, marks the point where "
          "widening the band adds redundant paths rather than new coverage. Descriptive "
          "only -- no band is chosen here.")


if __name__ == "__main__":
    main()
