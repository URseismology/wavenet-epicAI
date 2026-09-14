import argparse
import os

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature

META_DIR = os.path.dirname(__file__)

BG = "#111111"
OCEAN = "#1a1a1a"
LAND = "#2e2e2e"
BORDER = "#484848"
DOT = "#e8c87a"
TEXT_COL = "#dddddd"


def main():
    ap = argparse.ArgumentParser(description="Plot the FPS-downsampled station network on a world map.")
    ap.add_argument("--stations", default=os.path.join(META_DIR, "fps_stations.csv"))
    ap.add_argument("--out", default=os.path.join(META_DIR, "metadata_panel.png"))
    args = ap.parse_args()

    sta = pd.read_csv(args.stations)

    fig = plt.figure(figsize=(18, 10), facecolor=BG)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    ax.set_facecolor(OCEAN)
    ax.set_global()

    ax.add_feature(cfeature.OCEAN, facecolor=OCEAN, zorder=0)
    ax.add_feature(cfeature.LAND, facecolor=LAND, zorder=1)
    ax.add_feature(cfeature.COASTLINE, edgecolor=BORDER, linewidth=0.6, zorder=2)
    ax.add_feature(cfeature.BORDERS, edgecolor=BORDER, linewidth=0.25, zorder=2, linestyle=":")
    ax.add_feature(cfeature.LAKES, facecolor=OCEAN, zorder=2, alpha=0.7)
    ax.gridlines(color=BORDER, linewidth=0.3, alpha=0.35, linestyle="--")

    ax.scatter(
        sta.lon, sta.lat,
        s=8, color=DOT, alpha=0.75, linewidths=0,
        transform=ccrs.PlateCarree(), zorder=3,
    )

    ax.set_title(
        f"FPS-Downsampled Station Network — {len(sta)} Stations",
        color=TEXT_COL, fontsize=14, fontweight="bold", pad=12,
    )

    plt.savefig(args.out, dpi=300, bbox_inches="tight", facecolor=BG)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
