import argparse
import os

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from day_index import load_day_sets

META_DIR = os.path.dirname(__file__)
EARTH_RADIUS_KM = 6371.0088


def legal_pairs_within_band(lat, lon, sep_min_km, sep_max_km):
    coords_rad = np.radians(np.column_stack([lat, lon]))
    tree = BallTree(coords_rad, metric="haversine")
    max_rad = sep_max_km / EARTH_RADIUS_KM

    idx_lists, dist_lists = tree.query_radius(coords_rad, r=max_rad, return_distance=True)

    rows_i, rows_j, rows_km = [], [], []
    for i, (idxs, dists) in enumerate(zip(idx_lists, dist_lists)):
        for j, d in zip(idxs, dists):
            if j <= i:
                continue
            km = d * EARTH_RADIUS_KM
            if km >= sep_min_km:
                rows_i.append(i)
                rows_j.append(j)
                rows_km.append(km)
    return np.array(rows_i), np.array(rows_j), np.array(rows_km)


def main():
    ap = argparse.ArgumentParser(
        description="Generate cross-correlation-valid station pairs from an "
        "FPS-downsampled station set: exhaustive distance-band filtering "
        "(no set-cover / azimuth optimization) plus a distance-tiered shared "
        "recording-days gate."
    )
    ap.add_argument("--stations", default=os.path.join(META_DIR, "fps_stations.csv"))
    ap.add_argument("--key-index", default=os.path.join(META_DIR, "keys_partitioned_year"))
    ap.add_argument("--sep-min-km", type=float, default=110.0)
    ap.add_argument("--sep-max-km", type=float, default=1110.0)
    ap.add_argument("--tier-split-km", type=float, default=1500.0)
    ap.add_argument("--tier1-min-days", type=int, default=90)
    ap.add_argument("--tier2-min-days", type=int, default=365)
    ap.add_argument("--out", default=os.path.join(META_DIR, "fps_pairs.csv"))
    args = ap.parse_args()

    sta = pd.read_csv(args.stations)
    sta["key"] = sta["network"].astype(str) + "." + sta["station"].astype(str)
    print(f"Loaded {len(sta)} downsampled stations")

    i_idx, j_idx, km = legal_pairs_within_band(
        sta["lat"].to_numpy(), sta["lon"].to_numpy(), args.sep_min_km, args.sep_max_km
    )
    print(f"Pairs within [{args.sep_min_km}, {args.sep_max_km}] km band: {len(i_idx)}")

    day_sets = load_day_sets(args.key_index, set(sta["key"]))
    print(f"Loaded recording-day sets for {len(day_sets)} of {len(sta)} stations")

    rows = []
    for i, j, d in zip(i_idx, j_idx, km):
        si, sj = sta.iloc[i], sta.iloc[j]
        days_i = day_sets.get(si["key"], set())
        days_j = day_sets.get(sj["key"], set())
        shared_days = len(days_i & days_j)

        min_days = args.tier1_min_days if d < args.tier_split_km else args.tier2_min_days
        if shared_days < min_days:
            continue

        rows.append(
            dict(
                net1=si["network"], sta1=si["station"], lat1=si["lat"], lon1=si["lon"],
                net2=sj["network"], sta2=sj["station"], lat2=sj["lat"], lon2=sj["lon"],
                distance_km=d, shared_days=shared_days,
            )
        )

    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"Valid pairs after shared-days gate: {len(out)} -> {args.out}")


if __name__ == "__main__":
    main()
