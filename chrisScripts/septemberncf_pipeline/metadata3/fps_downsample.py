import argparse
import os

import numpy as np
import pandas as pd

META_DIR = os.path.dirname(__file__)


def latlon_to_xyz(lat, lon):
    lat_r = np.radians(lat)
    lon_r = np.radians(lon)
    x = np.cos(lat_r) * np.cos(lon_r)
    y = np.cos(lat_r) * np.sin(lon_r)
    z = np.sin(lat_r)
    return np.column_stack([x, y, z])


def farthest_point_sample(xyz, seed_score, n_target):
    n = xyz.shape[0]
    n_target = min(n_target, n)
    selected = np.empty(n_target, dtype=np.int64)

    seed = int(np.argmax(seed_score))
    selected[0] = seed
    min_dist2 = np.sum((xyz - xyz[seed]) ** 2, axis=1)
    min_dist2[seed] = -np.inf

    for i in range(1, n_target):
        nxt = int(np.argmax(min_dist2))
        selected[i] = nxt
        new_dist2 = np.sum((xyz - xyz[nxt]) ** 2, axis=1)
        min_dist2 = np.minimum(min_dist2, new_dist2)
        min_dist2[nxt] = -np.inf

    return selected


def main():
    ap = argparse.ArgumentParser(
        description="Downsample the station inventory via greedy farthest-point "
        "sampling on the sphere (max-min spacing). Deterministic and nested: "
        "the first N rows of any run are identical to the first N rows of a "
        "run with a larger --n-stations, so scaling up never requires a rerun "
        "from scratch. Purely spatial by design -- it does not consider "
        "recording-time overlap between stations, which build_pairs.py handles "
        "downstream. A selected station without a current partner still adds "
        "spatial coverage and can gain valid pairs later as the network grows."
    )
    ap.add_argument("--inventory", default=os.path.join(META_DIR, "s3_inventory.csv"))
    ap.add_argument("--min-days", type=int, default=15, help="drop stations with fewer active days")
    ap.add_argument("--n-stations", type=int, default=2000, help="target station count")
    ap.add_argument("--out", default=os.path.join(META_DIR, "fps_stations.csv"))
    args = ap.parse_args()

    inv = pd.read_csv(args.inventory)
    inv = inv[inv["days"] >= args.min_days].drop_duplicates(subset=["network", "station"]).reset_index(drop=True)
    print(f"Candidate pool after >= {args.min_days} day filter: {len(inv)} stations")

    xyz = latlon_to_xyz(inv["lat"].to_numpy(), inv["lon"].to_numpy())
    order = farthest_point_sample(xyz, inv["days"].to_numpy(), args.n_stations)

    out = inv.iloc[order].copy()
    out.insert(0, "rank", np.arange(len(out)))
    out.to_csv(args.out, index=False)

    print(f"Selected {len(out)} stations (rank-ordered, nested) -> {args.out}")


if __name__ == "__main__":
    main()
