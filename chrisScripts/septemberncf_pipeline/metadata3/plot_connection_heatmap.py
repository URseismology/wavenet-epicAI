import argparse
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from pyproj import Geod

META_DIR = os.path.dirname(__file__)
GEOD = Geod(ellps="WGS84")

BG = "#111111"
OCEAN = "#1a1a1a"
LAND = "#2e2e2e"
BORDER = "#484848"
TEXT_COL = "#dddddd"


def path_waypoints(lon1, lat1, lon2, lat2, n_waypoints):
    pts = GEOD.npts(lon1, lat1, lon2, lat2, n_waypoints)
    lons = [lon1] + [p[0] for p in pts] + [lon2]
    lats = [lat1] + [p[1] for p in pts] + [lat2]
    return lons, lats


def main():
    ap = argparse.ArgumentParser(
        description="Heatmap of interstation great-circle path density, area-corrected "
        "for meridian convergence, to visualize the spatial coverage of the "
        "cross-correlation network."
    )
    ap.add_argument("--pairs", default=os.path.join(META_DIR, "fps_pairs.csv"))
    ap.add_argument("--out", default=os.path.join(META_DIR, "connection_heatmap.png"))
    ap.add_argument("--grid-deg", type=float, default=2.0, help="lon/lat bin size in degrees")
    ap.add_argument("--waypoints-per-1000km", type=float, default=4.0)
    args = ap.parse_args()

    pairs = pd.read_csv(args.pairs)
    print(f"Rasterizing {len(pairs)} pairs into a {args.grid_deg}-deg grid")

    lon_bins = np.arange(-180, 180 + args.grid_deg, args.grid_deg)
    lat_bins = np.arange(-90, 90 + args.grid_deg, args.grid_deg)
    density = np.zeros((len(lat_bins) - 1, len(lon_bins) - 1))

    for _, row in pairs.iterrows():
        n_way = max(2, int(row.distance_km / 1000.0 * args.waypoints_per_1000km))
        lons, lats = path_waypoints(row.lon1, row.lat1, row.lon2, row.lat2, n_way)
        h, _, _ = np.histogram2d(lats, lons, bins=[lat_bins, lon_bins])
        density += h

    lat_centers = 0.5 * (lat_bins[:-1] + lat_bins[1:])
    area_correction = np.cos(np.radians(lat_centers))[:, None]
    density_corrected = density / np.clip(area_correction, 0.05, None)

    log_density = np.log1p(density_corrected)

    fig = plt.figure(figsize=(18, 10), facecolor=BG)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    ax.set_facecolor(OCEAN)
    ax.set_global()

    ax.add_feature(cfeature.OCEAN, facecolor=OCEAN, zorder=0)
    ax.add_feature(cfeature.LAND, facecolor=LAND, zorder=1)
    ax.add_feature(cfeature.COASTLINE, edgecolor=BORDER, linewidth=0.6, zorder=3)
    ax.add_feature(cfeature.BORDERS, edgecolor=BORDER, linewidth=0.25, zorder=3, linestyle=":")
    ax.gridlines(color=BORDER, linewidth=0.3, alpha=0.35, linestyle="--")

    mesh = ax.pcolormesh(
        lon_bins, lat_bins, log_density,
        cmap="inferno", transform=ccrs.PlateCarree(), zorder=2, shading="auto",
    )
    cbar = plt.colorbar(mesh, ax=ax, orientation="horizontal", pad=0.05, shrink=0.5)
    cbar.set_label("log(1 + area-corrected ray density)", color=TEXT_COL)
    cbar.ax.xaxis.set_tick_params(color=TEXT_COL)
    plt.setp(plt.getp(cbar.ax, "xticklabels"), color=TEXT_COL)

    ax.set_title(
        f"Interstation Ray-Path Density — Spatial Coverage ({len(pairs)} pairs)",
        color=TEXT_COL, fontsize=14, fontweight="bold", pad=12,
    )

    plt.savefig(args.out, dpi=300, bbox_inches="tight", facecolor=BG)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
