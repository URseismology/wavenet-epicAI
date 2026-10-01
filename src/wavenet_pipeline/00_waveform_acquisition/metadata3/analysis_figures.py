#!/usr/bin/env python
"""
Stages 2-3 of measure_delivered_network.py: pairs, equal-area cell metrics, and every figure.

Runs on axon-1 off the ~2 MB cache produced on BH3, so nothing here touches a cluster
filesystem and figures can be re-rendered in seconds. Imported by measure_delivered_network;
not meant to be run directly.
"""
import json
import math
import os
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from sklearn.neighbors import BallTree

from measure_delivered_network import (
    BG, OCEAN, LAND, BORDER, DOT, TEXT_COL, EARTH_RADIUS_KM, OVERLAP_LADDER, N_AZ_BINS,
    CellAccumulator, all_pairs_with_shared_days, cell_metrics, fibonacci_sphere,
    haversine_km, load_cache, log, banner, rasterize,
)

PLATE = ccrs.PlateCarree()


# ------------------------------------------------------------------ plotting helpers

def new_map(title, figsize=(16, 9)):
    fig = plt.figure(figsize=figsize, facecolor=BG)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.Robinson())
    base_map(ax, title)
    return fig, ax


def base_map(ax, title=None):
    ax.set_facecolor(OCEAN)
    ax.set_global()
    ax.add_feature(cfeature.OCEAN, facecolor=OCEAN, zorder=0)
    ax.add_feature(cfeature.LAND, facecolor=LAND, zorder=1)
    ax.add_feature(cfeature.COASTLINE, edgecolor=BORDER, linewidth=0.6, zorder=2)
    ax.gridlines(color=BORDER, linewidth=0.3, alpha=0.35, linestyle="--")
    if title:
        ax.set_title(title, color=TEXT_COL, fontsize=14, fontweight="bold", pad=12)
    return ax


def cell_field(ax, clat, clon, vals, cmap, label, vmin=None, vmax=None, size=14):
    m = np.isfinite(vals)
    sc = ax.scatter(clon[m], clat[m], c=vals[m], s=size, cmap=cmap, vmin=vmin, vmax=vmax,
                    linewidths=0, transform=PLATE, zorder=3)
    cb = plt.colorbar(sc, ax=ax, orientation="horizontal", pad=0.04, shrink=0.55)
    cb.set_label(label, color=TEXT_COL)
    cb.ax.xaxis.set_tick_params(color=TEXT_COL)
    plt.setp(plt.getp(cb.ax, "xticklabels"), color=TEXT_COL)
    return sc


def style_axes(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(OCEAN)
    for s in ax.spines.values():
        s.set_color(BORDER)
    ax.tick_params(colors=TEXT_COL)
    if title:
        ax.set_title(title, color=TEXT_COL, fontsize=11, fontweight="bold")
    if xlabel:
        ax.set_xlabel(xlabel, color=TEXT_COL)
    if ylabel:
        ax.set_ylabel(ylabel, color=TEXT_COL)
    ax.grid(color=BORDER, alpha=0.3, linewidth=0.4)
    return ax


def save(fig, out_dir, name):
    path = os.path.join(out_dir, name + ".png")
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    log("    saved {}".format(os.path.basename(path)))
    return path


def gini(x):
    x = np.sort(np.asarray(x, dtype=np.float64))
    n = len(x)
    if n == 0 or x.sum() == 0:
        return float("nan")
    return float((2.0 * np.sum((np.arange(1, n + 1)) * x) / (n * x.sum())) - (n + 1.0) / n)


# ------------------------------------------------------------------ stage 2

def stage2_footprint(args, sta, out_dir):
    import pandas as pd

    banner("STAGE 2 -- footprint and packaged volume")
    man = pd.read_csv(args.manifest)
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    delivered = set(sta["key"])

    rec_path = os.path.join(args.cache_dir, "reconciliation.csv")
    klass = {}
    if os.path.exists(rec_path):
        rec = pd.read_csv(rec_path)
        klass = dict(zip(rec["key"], rec["klass"]))

    def cls(k):
        if k in delivered:
            return "delivered"
        c = klass.get(k, "not_yet_reported")
        return "reported_no_data" if c in ("reported_no_data", "h5_but_package_not_ok") else (
            "in_flight" if c == "in_flight_no_json" else "not_yet_reported")

    man["klass"] = [cls(k) for k in man["key"]]
    counts = man["klass"].value_counts().to_dict()
    log("  manifest station disposition: {}".format(counts))

    colors = {"delivered": DOT, "in_flight": "#6ea8d8",
              "reported_no_data": "#a0522d", "not_yet_reported": "#555555"}
    fig, ax = new_map("Delivered vs planned station network  "
                      "({} of {} manifest stations packaged with data)".format(
                          len(delivered), len(man)))
    for k in ["not_yet_reported", "reported_no_data", "in_flight", "delivered"]:
        s = man[man["klass"] == k]
        if len(s):
            ax.scatter(s.lon, s.lat, s=11 if k == "delivered" else 7, color=colors[k],
                       alpha=0.9 if k == "delivered" else 0.65, linewidths=0,
                       transform=PLATE, zorder=3, label="{} ({})".format(k, len(s)))
    leg = ax.legend(loc="lower left", facecolor=BG, edgecolor=BORDER, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_COL)
    save(fig, out_dir, "fig01_footprint_delivered_vs_planned")

    fig, axes = plt.subplots(1, 3, figsize=(17, 4.6), facecolor=BG)
    style_axes(axes[0], "Days of real data per station", "days covered", "stations")
    axes[0].hist(sta["days_covered"], bins=60, color=DOT)
    style_axes(axes[1], "Channels per station", "channels", "stations")
    axes[1].hist(sta["n_channels"], bins=np.arange(0.5, sta["n_channels"].max() + 1.5), color=DOT)
    style_axes(axes[2], "Packaged size per station", "GB", "stations")
    axes[2].hist(sta["h5_bytes"] / 1e9, bins=60, color=DOT)
    axes[2].set_yscale("log")
    fig.suptitle("Packaged volume, delivered stations", color=TEXT_COL, fontweight="bold")
    save(fig, out_dir, "fig02_volume_hists")

    bands = sta["band_primary"].value_counts()
    fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=BG)
    style_axes(ax, "Primary channel band delivered", "band", "stations")
    ax.bar(bands.index.astype(str), bands.values, color=DOT)
    save(fig, out_dir, "fig03_band_mix")
    log("  band mix: {}".format(bands.to_dict()))
    return man


# ------------------------------------------------------------------ stage 3

def stage3_connectivity(args, sta, M, man, out_dir):
    import pandas as pd

    banner("STAGE 3 -- ray-path connectivity of the delivered network")

    iu, ju, dist, sdays = all_pairs_with_shared_days(sta, M, args)
    keys = sta["key"].to_numpy()
    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()

    pairs = pd.DataFrame(dict(
        sta1=keys[iu], lat1=lat[iu], lon1=lon[iu],
        sta2=keys[ju], lat2=lat[ju], lon2=lon[ju],
        distance_km=dist, shared_days=sdays,
    ))
    pairs["q"] = (pairs["shared_days"] / args.snr_ref_days) ** args.snr_exponent
    pairs.to_csv(os.path.join(out_dir, "delivered_pairs.csv"), index=False)
    log("  wrote delivered_pairs.csv ({:,} rows)".format(len(pairs)))

    # ---- per-path quality figures
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=BG)
    style_axes(ax, "Concurrent recording days per station pair (the quality of one ray path)",
               "shared days (log)", "pairs")
    ax.hist(np.clip(sdays, 1, None), bins=np.logspace(0, np.log10(max(sdays.max(), 10)), 70), color=DOT)
    ax.set_xscale("log")
    for t, c in zip(OVERLAP_LADDER, ["#d9534f", "#e8a33d", "#5bc0de", "#8fd14f"]):
        ax.axvline(t, color=c, linestyle="--", linewidth=1.2, label="{} d".format(t))
    leg = ax.legend(facecolor=BG, edgecolor=BORDER, fontsize=9)
    for tx in leg.get_texts():
        tx.set_color(TEXT_COL)
    save(fig, out_dir, "fig04_shared_days_hist")

    fig, ax = plt.subplots(figsize=(9, 5), facecolor=BG)
    style_axes(ax, "Are the long paths also the temporally thin ones?",
               "inter-station distance (km)", "shared days")
    idx = np.random.default_rng(0).choice(len(dist), size=min(60000, len(dist)), replace=False)
    ax.scatter(dist[idx], sdays[idx], s=1.5, alpha=0.25, color=DOT, linewidths=0)
    ax.set_yscale("log")
    save(fig, out_dir, "fig05_shared_days_vs_distance")

    # ---- equal-area cells
    clat, clon = fibonacci_sphere(args.n_cells)
    cell_area = 4.0 * math.pi * EARTH_RADIUS_KM ** 2 / args.n_cells
    cell_side = math.sqrt(cell_area)
    log("  {} equal-area cells; {:,.0f} km^2 each (equivalent side {:.0f} km)".format(
        args.n_cells, cell_area, cell_side))
    tree = BallTree(np.radians(np.stack([clat, clon], axis=1)), metric="haversine")

    # ---- one ascending-distance pass yields every restriction level
    levels = []
    for tok in args.restrict_max_km.split(","):
        tok = tok.strip()
        levels.append(float("inf") if tok.lower() == "none" else float(tok))
    levels = sorted(set(levels))

    acc = CellAccumulator(args.n_cells)
    sweep_rows = []
    lo = 0.0
    order = np.argsort(dist)
    for lev in levels:
        sel = order[(dist[order] > lo) & (dist[order] <= lev)]
        if len(sel):
            rasterize(iu[sel], ju[sel], dist[sel], sdays[sel], sta, tree,
                      args.n_cells, args, acc, label=" <={}".format(lev))
        cm = cell_metrics(acc, args, cell_area)
        n_pairs_cum = int((dist <= lev).sum())
        illum = float((cm["n_paths"] > 0).mean())
        sweep_rows.append(dict(
            max_km=lev, n_pairs=n_pairs_cum,
            pct_cells_illuminated=round(100 * illum, 2),
            pct_cells_resolved=round(100 * float(cm["resolved"].mean()), 2),
            median_fresnel_km=round(float(np.nanmedian(cm["fresnel_km"])), 1),
            median_A=round(float(np.nanmedian(cm["A"])), 3),
            median_paths_per_cell=float(np.median(cm["n_paths"])),
        ))
        log("  level <= {:>8}: {:>8,} pairs | {:5.1f}% cells lit | {:5.1f}% resolved | "
            "median Fresnel {:6.0f} km".format(
                "none" if np.isinf(lev) else int(lev), n_pairs_cum,
                sweep_rows[-1]["pct_cells_illuminated"], sweep_rows[-1]["pct_cells_resolved"],
                sweep_rows[-1]["median_fresnel_km"]))
        lo = lev

    sweep = pd.DataFrame(sweep_rows)
    sweep.to_csv(os.path.join(out_dir, "restriction_sweep.csv"), index=False)

    cm = cell_metrics(acc, args, cell_area)
    cm.insert(0, "lat", clat)
    cm.insert(1, "lon", clon)
    cm.to_csv(os.path.join(out_dir, "cell_metrics.csv"), index=False)

    # ---- per-cell diagnostic maps
    lit = cm["n_paths"].to_numpy() > 0
    fig, ax = new_map("Illumination: ray paths crossing each cell (all {:,} delivered paths)".format(len(pairs)))
    cell_field(ax, clat, clon, np.where(lit, np.log10(np.maximum(cm["n_paths"], 1)), np.nan),
               "inferno", "log10(paths per cell)")
    save(fig, out_dir, "fig06_map_path_count_nc")

    fig, axes = plt.subplots(2, 2, figsize=(17, 9), facecolor=BG,
                             subplot_kw=dict(projection=ccrs.Robinson()))
    for axx, t in zip(axes.ravel(), OVERLAP_LADDER):
        base_map(axx, "paths with >= {} days of overlap".format(t))
        v = cm["n_ge_{}d".format(t)].to_numpy().astype(float)
        cell_field(axx, clat, clon, np.where(v > 0, np.log10(v), np.nan), "inferno",
                   "log10(count)", size=7)
    fig.suptitle("Illumination by temporal quality of the contributing paths",
                 color=TEXT_COL, fontweight="bold", fontsize=14)
    save(fig, out_dir, "fig07_map_count_by_overlap_ladder")

    fig, ax = new_map("Median concurrent recording days of the paths crossing each cell")
    cell_field(ax, clat, clon, cm["median_shared_days"].to_numpy(), "viridis", "median shared days")
    save(fig, out_dir, "fig08_map_median_shared_days")

    fig, ax = new_map("Azimuthal spread $A_c$  (1 = paths from all directions, 0 = all parallel)")
    cell_field(ax, clat, clon, cm["A"].to_numpy(), "magma", "$A_c = 1 - R_c$", vmin=0, vmax=1)
    save(fig, out_dir, "fig09_map_azimuthal_Ac")

    fig, ax = new_map("Largest azimuthal gap (deg, mod 180) -- lower is better")
    cell_field(ax, clat, clon, cm["az_gap_deg"].to_numpy(), "magma_r", "max gap (deg)", vmin=15, vmax=180)
    save(fig, out_dir, "fig10_map_azgap")

    fig, ax = new_map("Effective resolution: median Fresnel width  "
                      "(cell side {:.0f} km)".format(cell_side))
    cell_field(ax, clat, clon, cm["fresnel_km"].to_numpy(), "cividis", "Fresnel width (km)")
    save(fig, out_dir, "fig11_map_resolution_wc")

    fig, ax = new_map("Azimuthal redundancy $\\rho_c$ (high = many paths sharing few orientations)")
    cell_field(ax, clat, clon, np.where(lit, cm["rho"], np.nan), "plasma", "$\\rho_c$", vmin=0, vmax=1)
    save(fig, out_dir, "fig12_map_redundancy_rho")

    klass = np.full(args.n_cells, 0)
    klass[lit & (cm["n_paths"] < args.min_hits)] = 1
    klass[lit & (cm["n_paths"] >= args.min_hits) & (cm["A"] < args.min_azq)] = 2
    klass[lit & (cm["n_paths"] >= args.min_hits) & (cm["A"] >= args.min_azq)
          & (cm["az_gap_deg"] > args.max_gap_deg)] = 3
    klass[cm["resolved"].to_numpy()] = 4
    names = {0: "unlit", 1: "too few paths", 2: "anisotropic", 3: "azimuth gap", 4: "resolved"}
    cols = {0: "#444444", 1: "#a0522d", 2: "#d9534f", 3: "#e8a33d", 4: "#8fd14f"}
    fig, ax = new_map("Where can we actually invert? ({:.1f}% of cells resolved)".format(
        100 * float(cm["resolved"].mean())))
    for k in range(5):
        m = klass == k
        if m.any():
            ax.scatter(clon[m], clat[m], s=14, color=cols[k], linewidths=0, transform=PLATE,
                       zorder=3, label="{} ({})".format(names[k], int(m.sum())))
    leg = ax.legend(loc="lower left", facecolor=BG, edgecolor=BORDER, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_COL)
    save(fig, out_dir, "fig13_map_quality_classes")

    # ---- delivered vs planned, apples to apples in the production band
    planned_path = args.planned_pairs
    psi = None
    if os.path.exists(planned_path):
        pp = pd.read_csv(planned_path)
        band = (dist >= 110) & (dist <= 1110)
        f_path = band.sum() / float(len(pp))
        f_sta = len(sta) / float(len(man))
        psi = f_path / (f_sta ** 2)
        log("  production-band comparison (110-1110 km, same day gates):")
        log("    delivered pairs {:,}   planned pairs {:,}   f_path={:.3f}".format(
            int(band.sum()), len(pp), f_path))
        log("    f_sta={:.3f}  f_sta^2={:.3f}  psi = f_path/f_sta^2 = {:.2f}".format(
            f_sta, f_sta ** 2, psi))

        acc_p = CellAccumulator(args.n_cells)
        pstat = pd.DataFrame(dict(lat=np.concatenate([pp.lat1, pp.lat2]),
                                  lon=np.concatenate([pp.lon1, pp.lon2])))
        n = len(pp)
        rasterize(np.arange(n), np.arange(n) + n, pp["distance_km"].to_numpy(),
                  pp["shared_days"].to_numpy(), pstat, tree, args.n_cells, args, acc_p,
                  label=" planned")
        cm_p = cell_metrics(acc_p, args, cell_area)

        acc_d = CellAccumulator(args.n_cells)
        selb = np.flatnonzero(band)
        rasterize(iu[selb], ju[selb], dist[selb], sdays[selb], sta, tree, args.n_cells,
                  args, acc_d, label=" delivered-band")
        cm_d = cell_metrics(acc_d, args, cell_area)

        dstate = np.zeros(args.n_cells, int)
        dstate[cm_p["resolved"].to_numpy() & cm_d["resolved"].to_numpy()] = 1   # retained
        dstate[cm_p["resolved"].to_numpy() & ~cm_d["resolved"].to_numpy()] = 2  # degraded
        dstate[~cm_p["resolved"].to_numpy() & cm_d["resolved"].to_numpy()] = 3  # gained
        nm = {0: "neither", 1: "retained", 2: "lost in delivery", 3: "gained"}
        cl = {0: "#333333", 1: "#8fd14f", 2: "#d9534f", 3: "#5bc0de"}
        fig, ax = new_map("Production band 110-1110 km: resolved cells, delivered vs planned")
        for k in range(4):
            m = dstate == k
            if m.any():
                ax.scatter(clon[m], clat[m], s=14, color=cl[k], linewidths=0, transform=PLATE,
                           zorder=3, label="{} ({})".format(nm[k], int(m.sum())))
        leg = ax.legend(loc="lower left", facecolor=BG, edgecolor=BORDER, fontsize=9)
        for t in leg.get_texts():
            t.set_color(TEXT_COL)
        save(fig, out_dir, "fig14_delivered_vs_planned_delta")

    # ---- the tradeoff figure
    # The unrestricted level has max_km = inf, which a log axis cannot draw. Place it at the
    # true antipodal maximum (20,015 km) so the full range is visible rather than dropped.
    fin = sweep.copy()
    fin["max_km"] = fin["max_km"].replace(np.inf, 20015.0)
    fig, ax = plt.subplots(figsize=(9.5, 5.5), facecolor=BG)
    style_axes(ax, "Allowing longer paths: illumination rises, resolution degrades",
               "maximum inter-station separation allowed (km)", "% of cells illuminated")
    ax.plot(fin["max_km"], fin["pct_cells_illuminated"], "-o", color=DOT, label="% cells illuminated")
    ax.plot(fin["max_km"], fin["pct_cells_resolved"], "-s", color="#8fd14f", label="% cells resolved")
    ax.set_xscale("log")
    ax2 = ax.twinx()
    ax2.plot(fin["max_km"], fin["median_fresnel_km"], "-^", color="#5bc0de",
             label="median Fresnel width (km)")
    ax2.set_ylabel("median Fresnel width (km)", color="#5bc0de")
    ax2.tick_params(colors="#5bc0de")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    leg = ax.legend(h1 + h2, l1 + l2, loc="center right", facecolor=BG, edgecolor=BORDER, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_COL)
    save(fig, out_dir, "fig15_restriction_tradeoff")

    # ---- diagnostics panel
    fig = plt.figure(figsize=(17, 9.5), facecolor=BG)
    ax = fig.add_subplot(2, 3, 1)
    style_axes(ax, "Paths per cell", "paths (log)", "cells")
    ax.hist(np.maximum(cm["n_paths"][lit], 1), bins=np.logspace(0, np.log10(max(cm["n_paths"].max(), 10)), 50), color=DOT)
    ax.set_xscale("log")
    ax.axvline(args.min_hits, color="#d9534f", linestyle="--")

    ax = fig.add_subplot(2, 3, 2)
    style_axes(ax, "Azimuthal spread $A_c$", "$A_c$", "cells")
    ax.hist(cm["A"][lit].dropna(), bins=50, color=DOT)
    ax.axvline(args.min_azq, color="#d9534f", linestyle="--")

    ax = fig.add_subplot(2, 3, 3)
    style_axes(ax, "Max azimuthal gap", "degrees", "cells")
    ax.hist(cm["az_gap_deg"][lit].dropna(), bins=50, color=DOT)
    ax.axvline(args.max_gap_deg, color="#d9534f", linestyle="--")

    ax = fig.add_subplot(2, 3, 4)
    style_axes(ax, "Effective resolution (Fresnel width)", "km", "cells")
    ax.hist(cm["fresnel_km"][lit].dropna(), bins=50, color=DOT)
    ax.axvline(cell_side, color="#5bc0de", linestyle="--", label="cell side")

    ax = fig.add_subplot(2, 3, 5)
    style_axes(ax, "Lorenz curve of illumination (Gini {:.2f})".format(gini(cm["n_paths"][lit])),
               "cumulative share of cells", "cumulative share of paths")
    v = np.sort(cm["n_paths"][lit].to_numpy().astype(float))
    ax.plot(np.linspace(0, 1, len(v)), np.cumsum(v) / v.sum(), color=DOT)
    ax.plot([0, 1], [0, 1], color=BORDER, linestyle="--")

    ax = fig.add_subplot(2, 3, 6, projection="polar")
    ax.set_facecolor(OCEAN)
    cand = np.flatnonzero(cm["n_paths"].to_numpy() >= args.min_hits)
    if len(cand):
        best = cand[np.nanargmax(cm["A"].to_numpy()[cand])]
        worst = cand[np.nanargmin(cm["A"].to_numpy()[cand])]
        th = np.radians(np.arange(N_AZ_BINS) * (180.0 / N_AZ_BINS))
        for ci, col, lab in [(best, "#8fd14f", "best A={:.2f}".format(cm["A"][best])),
                             (worst, "#d9534f", "worst A={:.2f}".format(cm["A"][worst]))]:
            h = acc.az_hist[ci]
            h = h / h.max() if h.max() > 0 else h
            ax.bar(th, h, width=np.radians(180.0 / N_AZ_BINS), color=col, alpha=0.6, label=lab)
        leg = ax.legend(facecolor=BG, edgecolor=BORDER, fontsize=8, loc="upper right")
        for t in leg.get_texts():
            t.set_color(TEXT_COL)
    ax.set_title("Azimuth roses (exemplar cells)", color=TEXT_COL, fontsize=11, fontweight="bold")
    ax.tick_params(colors=TEXT_COL)
    save(fig, out_dir, "fig16_quality_diagnostics")

    # ---- candidate pairs for the senior's NCF convergence work
    cand = pairs.copy()
    cand["band"] = pd.cut(cand["distance_km"], [0, 300, 600, 1110, 2000, 5000, 1e9],
                          labels=["<300", "300-600", "600-1110", "1110-2000", "2000-5000", ">5000"])
    top = (cand.sort_values("shared_days", ascending=False)
               .groupby("band", observed=True).head(25)
               .sort_values(["band", "shared_days"], ascending=[True, False]))
    top.to_csv(os.path.join(out_dir, "ncf_candidate_pairs.csv"), index=False)
    log("  wrote ncf_candidate_pairs.csv ({} pairs across {} distance bands)".format(
        len(top), top["band"].nunique()))

    return cm, pairs, sweep, psi, cell_side


def run_analysis(args, stages):
    os.makedirs(args.out_dir, exist_ok=True)
    sta, M, scan_utc = load_cache(args.cache_dir)
    log("  cache from {} -- {} stations x {} days".format(scan_utc, M.shape[0], M.shape[1]))
    sta.to_csv(os.path.join(args.out_dir, "delivered_stations.csv"), index=False)

    man = None
    if 2 in stages:
        man = stage2_footprint(args, sta, args.out_dir)
    if 3 in stages:
        if man is None:
            import pandas as pd
            man = pd.read_csv(args.manifest)
        cm, pairs, sweep, psi, cell_side = stage3_connectivity(args, sta, M, man, args.out_dir)
        write_summary(args, sta, man, cm, pairs, sweep, psi, cell_side, scan_utc)


def write_summary(args, sta, man, cm, pairs, sweep, psi, cell_side, scan_utc):
    lit = cm["n_paths"] > 0
    lines = []
    A = lines.append
    A("DELIVERED NCF NETWORK -- footprint, packaging and ray-path illumination")
    A("generated {}   cache scanned {}".format(
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), scan_utc))
    A("")
    A("FOOTPRINT")
    A("  manifest stations                 : {:,}".format(len(man)))
    A("  delivered (packaged, with data)   : {:,}  ({:.1f}%)".format(
        len(sta), 100.0 * len(sta) / len(man)))
    A("  days of real data                 : {:,}".format(int(sta['days_covered'].sum())))
    A("  packaged on disk                  : {:.1f} GB".format(sta["h5_bytes"].sum() / 1e9))
    A("  median days per station           : {:,.0f}".format(sta["days_covered"].median()))
    A("")
    A("RAY PATHS (all separations, gated only on concurrent recording days)")
    A("  usable station pairs              : {:,}".format(len(pairs)))
    A("  median shared days per pair       : {:,.0f}".format(pairs["shared_days"].median()))
    for t in OVERLAP_LADDER:
        n = int((pairs["shared_days"] >= t).sum())
        A("    pairs with >= {:>4} d overlap    : {:,} ({:.1f}%)".format(
            t, n, 100.0 * n / len(pairs)))
    A("  median path length                : {:,.0f} km".format(pairs["distance_km"].median()))
    A("")
    A("ILLUMINATION ({} equal-area cells, {:.0f} km side)".format(args.n_cells, cell_side))
    A("  cells with >=1 path               : {:,} ({:.1f}%)".format(
        int(lit.sum()), 100.0 * float(lit.mean())))
    A("  cells resolved (n>={}, A>={}, gap<={})".format(args.min_hits, args.min_azq, args.max_gap_deg))
    A("                                    : {:,} ({:.1f}%)".format(
        int(cm["resolved"].sum()), 100.0 * float(cm["resolved"].mean())))
    A("  median paths per lit cell         : {:,.0f}".format(cm["n_paths"][lit].median()))
    A("  median azimuthal spread A_c       : {:.3f}  (kappa = {:.2f})".format(
        float(cm["A"][lit].median()), 2.0 / max(float(cm["A"][lit].median()), 1e-9) - 1.0))
    A("  median Fresnel width              : {:,.0f} km".format(float(cm["fresnel_km"][lit].median())))
    A("  resolution-matched cells          : {:,} ({:.1f}%)".format(
        int(cm["resolution_matched"].sum()), 100.0 * float(cm["resolution_matched"].mean())))
    A("  Gini of paths per cell            : {:.3f}  (0 = perfectly even)".format(
        gini(cm["n_paths"][lit])))
    if psi is not None:
        A("")
        A("DELIVERED vs PLANNED (production band 110-1110 km, same day gates)")
        A("  psi = f_path / f_sta^2            : {:.2f}".format(psi))
        A("    psi ~ 1  losses spread randomly")
        A("    psi < 1  losses concentrated in dense clusters (cheap)")
        A("    psi > 1  losses concentrated in sparse regions (expensive)")
    A("")
    A("RESTRICTION SWEEP")
    A("  {:>10} {:>12} {:>10} {:>10} {:>14}".format("max km", "pairs", "% lit", "% resolved", "Fresnel km"))
    for _, r in sweep.iterrows():
        A("  {:>10} {:>12,} {:>10.1f} {:>10.1f} {:>14.0f}".format(
            "none" if np.isinf(r["max_km"]) else int(r["max_km"]), int(r["n_pairs"]),
            r["pct_cells_illuminated"], r["pct_cells_resolved"], r["median_fresnel_km"]))
    txt = "\n".join(lines)
    with open(os.path.join(args.out_dir, "SUMMARY.txt"), "w") as fh:
        fh.write(txt + "\n")
    log("")
    log(txt)
