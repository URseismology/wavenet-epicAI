#!/usr/bin/env python
"""
Measure the DELIVERED NCF station network and how well its ray paths illuminate the globe.

Everything else in this directory measures the PLANNED network: the station manifest joined
against a data-center availability inventory. This measures what we actually downloaded,
packaged and hold on disk -- a different thing, and the distinction that the whole v1->v2
bug recovery was about.

Runs in two places, deliberately:

  stages 0,1   BlueHive3 (sbatch), where the HDF5 lives. Reads ONLY each file's group attrs
               and its `_coverage` bitmaps, and only for stations whose result JSON says
               package_ok=True -- those are finished and quiescent, so the in-flight files
               the live campaign is still writing are excluded by construction rather than
               by a timing heuristic. Emits a ~2 MB cache.
  stages 2,3   axon-1, from that cache. No cluster filesystem involved, so figures can be
               re-rendered in seconds.

terravibranium runs NOTHING here -- it is saturated by the SAmericaNoise packaging campaign.

    # on BH3, inside an allocation
    source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
    conda activate instaseis
    python measure_delivered_network.py --stages 0,1 \
        --root /scratch/tolugboj_lab/wavenet_ncf_production_v2 --cache-dir ./cache

    # on axon-1
    ~/venv-wavenet-analysis/bin/python measure_delivered_network.py --stages 2,3 \
        --cache-dir ./cache --out-dir ./delivered_network_reference

!!! THE ONE FOOTGUN !!!  A channel dataset in these files is ~1.2 BILLION float32 samples,
about 4.9 GB. Slicing one (`grp[cha][:]`) will hang or OOM the node. The only dataset this
script ever reads is `grp['_coverage'][cha]`, which is one byte per day (~14 kB). That is
what makes the whole scan cheap. See read_coverage_only().
"""
import argparse
import glob
import json
import math
import os
import sys
import time

import numpy as np

EARTH_RADIUS_KM = 6371.0088
EPOCH_ISO = "1970-01-01"
OVERLAP_LADDER = (90, 180, 365, 730)   # days of concurrent recording
N_AZ_BINS = 12                         # 15-degree bins over 180 deg (axial data)
N_HIST_BINS = 24                       # log bins for per-cell S and L distributions

# House plot palette, copied verbatim from plot_metadata_panel.py so every figure in this
# directory looks like one set.
BG = "#111111"
OCEAN = "#1a1a1a"
LAND = "#2e2e2e"
BORDER = "#484848"
DOT = "#e8c87a"
TEXT_COL = "#dddddd"

META_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, flush=True)


def banner(title):
    log("")
    log("=" * 78)
    log(title)
    log("=" * 78)


# --------------------------------------------------------------------------------------
# stage 0 -- reconciliation
# --------------------------------------------------------------------------------------

def load_results(results_dir):
    """Read every per-station result JSON. Returns {key: dict}. Never raises on one bad file."""
    out, bad = {}, 0
    for f in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            with open(f) as fh:
                d = json.load(fh)
        except Exception:
            bad += 1
            continue
        key = "{}.{}".format(d.get("network"), d.get("station"))
        d["_path"] = f
        out[key] = d
    if bad:
        log("  WARNING: {} result JSON(s) unreadable and skipped".format(bad))
    return out


def stage0_reconcile(args):
    """Three populations -- manifest, result JSONs, .h5 on disk -- and every disagreement."""
    import pandas as pd

    banner("STAGE 0 -- reconciliation")

    man = pd.read_csv(args.manifest)
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    man_keys = set(man["key"])

    results = load_results(os.path.join(args.root, "results"))
    ok_keys = {k for k, d in results.items() if d.get("package_ok")}

    h5_dir = args.h5_dir or os.path.join(args.root, "packaged_h5")
    h5_paths = {os.path.basename(p)[:-3]: p for p in glob.glob(os.path.join(h5_dir, "*.h5"))}
    h5_keys = set(h5_paths)

    log("  manifest              : {:>6}  ({})".format(len(man_keys), os.path.basename(args.manifest)))
    log("  result JSONs reported : {:>6}".format(len(results)))
    log("  package_ok=True       : {:>6}   <-- the delivered population".format(len(ok_keys)))
    log("  .h5 on disk           : {:>6}".format(len(h5_keys)))
    log("")

    now = time.time()
    rows = []
    for key in sorted(man_keys | set(results) | h5_keys):
        d = results.get(key)
        has_h5 = key in h5_keys
        ok = bool(d and d.get("package_ok"))
        if has_h5 and not ok and d is None:
            cls = "in_flight_no_json"      # growing .h5, result JSON not written yet
        elif has_h5 and not ok:
            cls = "h5_but_package_not_ok"
        elif ok and not has_h5:
            cls = "ok_json_without_h5"     # real data loss if this ever appears
        elif has_h5 and key not in man_keys:
            cls = "h5_not_in_manifest"
        elif ok:
            cls = "delivered"
        elif d is not None:
            cls = "reported_no_data"
        else:
            cls = "not_yet_reported"
        st = os.stat(h5_paths[key]) if has_h5 else None
        rows.append(dict(
            key=key, in_manifest=key in man_keys, has_json=d is not None,
            package_ok=ok, h5_on_disk=has_h5,
            h5_bytes=st.st_size if st else 0,
            h5_age_hours=round((now - st.st_mtime) / 3600.0, 2) if st else None,
            h5_nlink=st.st_nlink if st else None,
            json_days=(d or {}).get("n_days_processed"),
            json_h5_bytes=(d or {}).get("h5_size_bytes"),
            klass=cls,
        ))

    rec = pd.DataFrame(rows)
    log("  disagreement classes:")
    for cls, n in rec["klass"].value_counts().items():
        log("    {:<24} {:>6}".format(cls, n))

    lost = rec[rec["klass"] == "ok_json_without_h5"]
    if len(lost):
        log("")
        log("  *** {} station(s) claim package_ok but have NO .h5 -- REAL DATA LOSS ***".format(len(lost)))
        for k in lost["key"]:
            log("      {}".format(k))
    else:
        log("")
        log("  OK: every package_ok=True station has its .h5 on disk (no data loss).")

    os.makedirs(args.cache_dir, exist_ok=True)
    out = os.path.join(args.cache_dir, "reconciliation.csv")
    rec.to_csv(out, index=False)
    log("  wrote {}".format(out))

    summary = dict(
        manifest_rows=len(man_keys), results_reported=len(results),
        package_ok=len(ok_keys), h5_on_disk=len(h5_keys),
        classes={k: int(v) for k, v in rec["klass"].value_counts().items()},
        sum_days_packaged=int(sum((d.get("n_days_processed") or 0) for d in results.values() if d.get("package_ok"))),
        sum_h5_bytes=int(sum((d.get("h5_size_bytes") or 0) for d in results.values() if d.get("package_ok"))),
        scan_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    with open(os.path.join(args.cache_dir, "reconciliation_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


# --------------------------------------------------------------------------------------
# stage 1 -- coverage scan (the only stage that opens HDF5)
# --------------------------------------------------------------------------------------

def read_coverage_only(path):
    """
    Open one packaged station file and read ONLY metadata + the per-day coverage bitmaps.

    Deliberately never touches a channel's sample array. Those are ~1.2e9 float32 (~4.9 GB)
    and reading one would hang the node; `.shape`/`.attrs` are metadata lookups and free.
    """
    import h5py

    rec = dict(ok=False, error=None, key=None, lat=None, lon=None,
               channels={}, cov=None, cov_len=0)
    try:
        with h5py.File(path, "r", locking=False) as h:
            groups = [k for k in h.keys() if not k.startswith("_")]
            if not groups:
                rec["error"] = "no_station_group"
                return rec
            key = groups[0]
            g = h[key]
            rec["key"] = key
            a = g.attrs
            rec["lat"] = float(a["latitude"]) if "latitude" in a else None
            rec["lon"] = float(a["longitude"]) if "longitude" in a else None

            if "_coverage" not in g:
                rec["error"] = "no_coverage_group"
                return rec

            cov_union = None
            for cha in g["_coverage"].keys():
                dset = g["_coverage"][cha]
                assert dset.name.split("/")[-2] == "_coverage", "refusing to read a non-coverage dataset"
                bits = np.asarray(dset[:], dtype=np.uint8) > 0   # the ONLY array read
                if cov_union is None:
                    cov_union = bits
                elif len(bits) > len(cov_union):
                    cov_union = np.pad(cov_union, (0, len(bits) - len(cov_union))) | bits
                else:
                    cov_union = cov_union | np.pad(bits, (0, len(cov_union) - len(bits)))
                meta = {}
                if cha in g:
                    d = g[cha]
                    meta = dict(sampling_rate=float(d.attrs.get("sampling_rate", 0) or 0),
                                units=str(d.attrs.get("units", "")),
                                n_samples=int(d.shape[0]))   # metadata, not a read
                meta["cov_days"] = int(bits.sum())
                rec["channels"][cha] = meta

            if cov_union is None:
                rec["error"] = "coverage_empty"
                return rec
            if not cov_union.any():
                rec["error"] = "coverage_all_zero"
                return rec

            rec["cov"] = cov_union
            rec["cov_len"] = int(len(cov_union))
            rec["ok"] = True
    except Exception as exc:                                   # never abort the whole scan
        rec["error"] = "{}: {}".format(type(exc).__name__, str(exc)[:200])
    return rec


def stage1_scan(args):
    import pandas as pd

    banner("STAGE 1 -- coverage scan")

    h5_dir = args.h5_dir or os.path.join(args.root, "packaged_h5")
    results = load_results(os.path.join(args.root, "results")) if args.root else {}

    paths = sorted(glob.glob(os.path.join(h5_dir, "*.h5")))
    if args.completed_only and results:
        ok = {k for k, d in results.items() if d.get("package_ok")}
        before = len(paths)
        paths = [p for p in paths if os.path.basename(p)[:-3] in ok]
        log("  restricting to package_ok=True stations: {} -> {} files".format(before, len(paths)))
        log("  (excludes in-flight files the live campaign is still writing)")
    if args.limit:
        paths = paths[:args.limit]
        log("  --limit {} applied".format(args.limit))

    log("  scanning {} files from {}".format(len(paths), h5_dir))
    t0 = time.time()
    rows, anomalies, covs = [], [], []
    for i, p in enumerate(paths, 1):
        r = read_coverage_only(p)
        key = r["key"] or os.path.basename(p)[:-3]
        if not r["ok"]:
            anomalies.append(dict(key=key, path=p, klass=r["error"] or "unknown"))
        else:
            d = results.get(key, {})
            bands = sorted({c[:2] for c in r["channels"]})
            rows.append(dict(
                key=key, network=key.split(".")[0], station=key.split(".", 1)[-1],
                lat=r["lat"], lon=r["lon"],
                n_channels=len(r["channels"]),
                bands=";".join(bands),
                band_primary=bands[0] if bands else "",
                days_covered=int(r["cov"].sum()),
                first_day=int(np.argmax(r["cov"])),
                last_day=int(len(r["cov"]) - 1 - np.argmax(r["cov"][::-1])),
                cov_len=r["cov_len"],
                h5_bytes=os.path.getsize(p),
                json_days=d.get("n_days_processed"),
                units=next((m.get("units") for m in r["channels"].values() if m.get("units")), ""),
            ))
            covs.append((key, r["cov"]))
        if i % 25 == 0 or i == len(paths):
            el = time.time() - t0
            rate = i / el if el > 0 else 0
            eta = (len(paths) - i) / rate if rate > 0 else 0
            log("  [stage1] {}/{} ({:.1f}%)  {:.1f} files/s  elapsed {:.0f}s  eta {:.0f}s  anomalies {}".format(
                i, len(paths), 100.0 * i / max(len(paths), 1), rate, el, eta, len(anomalies)))

    if not rows:
        log("  FATAL: no station scanned successfully")
        return 2

    D = max(r["cov_len"] for r in rows)
    keys = [k for k, _ in covs]
    M = np.zeros((len(covs), D), dtype=bool)
    for i, (_, c) in enumerate(covs):
        M[i, :len(c)] = c                      # bitmaps are ragged per channel; zero-pad right

    os.makedirs(args.cache_dir, exist_ok=True)
    sta = pd.DataFrame(rows)
    tmp = os.path.join(args.cache_dir, "delivered_stations.parquet.tmp")
    sta.to_parquet(tmp, index=False)
    os.replace(tmp, os.path.join(args.cache_dir, "delivered_stations.parquet"))

    # savez appends .npz when the name lacks it, so the temp name must already end in .npz
    tmp = os.path.join(args.cache_dir, "_tmp_coverage_bits.npz")
    np.savez_compressed(tmp, bits=np.packbits(M, axis=1), keys=np.array(keys),
                        n_days=D, epoch=EPOCH_ISO,
                        scan_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    os.replace(tmp, os.path.join(args.cache_dir, "coverage_bits.npz"))

    if anomalies:
        pd.DataFrame(anomalies).to_csv(os.path.join(args.cache_dir, "scan_anomalies.csv"), index=False)

    log("")
    log("  scanned OK      : {}".format(len(rows)))
    log("  anomalies       : {}".format(len(anomalies)))
    for k, n in (pd.DataFrame(anomalies)["klass"].value_counts().items() if anomalies else []):
        log("    {:<24} {:>5}".format(k, n))
    log("  day axis        : {} days since {}".format(D, EPOCH_ISO))
    log("  total days      : {:,}".format(int(M.sum())))
    log("  cache           : {}".format(args.cache_dir))
    log("  elapsed         : {:.1f}s".format(time.time() - t0))
    return 0


def load_cache(cache_dir):
    import pandas as pd
    sta = pd.read_parquet(os.path.join(cache_dir, "delivered_stations.parquet"))
    z = np.load(os.path.join(cache_dir, "coverage_bits.npz"), allow_pickle=False)
    D = int(z["n_days"])
    M = np.unpackbits(z["bits"], axis=1)[:, :D].astype(bool)
    keys = [str(k) for k in z["keys"]]
    sta = sta.set_index("key").loc[keys].reset_index()      # align row order with M
    return sta, M, str(z["scan_utc"])


# --------------------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------------------

def fibonacci_sphere(n):
    """n quasi-uniform, equal-area points on the sphere (golden-angle spiral) -> lat,lon deg."""
    i = np.arange(n, dtype=np.float64) + 0.5
    lat = np.degrees(np.arcsin(1.0 - 2.0 * i / n))
    lon = np.degrees((2.0 * np.pi * i / ((1.0 + 5.0 ** 0.5) / 2.0)) % (2.0 * np.pi))
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    return lat, lon


def unit_vectors(lat_deg, lon_deg):
    la, lo = np.radians(lat_deg), np.radians(lon_deg)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def vec_to_latlon(v):
    lat = np.degrees(np.arcsin(np.clip(v[..., 2], -1, 1)))
    lon = np.degrees(np.arctan2(v[..., 1], v[..., 0]))
    return lat, lon


def slerp_paths(p1, p2, n_way):
    """
    Great-circle waypoints by spherical linear interpolation, vectorized over paths.

    p1,p2: (P,3) unit vectors. Returns (P,n_way,3). Uniform in t gives uniform arc spacing,
    so every segment of a given path has the same length -- which the caller relies on.
    """
    dot = np.clip(np.einsum("ij,ij->i", p1, p2), -1.0, 1.0)
    omega = np.arccos(dot)[:, None]
    t = np.linspace(0.0, 1.0, n_way)[None, :]
    sin_o = np.sin(omega)
    near = sin_o < 1e-12
    a = np.where(near, 1.0 - t, np.sin((1.0 - t) * omega) / np.where(near, 1.0, sin_o))
    b = np.where(near, t, np.sin(t * omega) / np.where(near, 1.0, sin_o))
    return a[:, :, None] * p1[:, None, :] + b[:, :, None] * p2[:, None, :]


def segment_azimuths(lat1, lon1, lat2, lon2):
    """Forward azimuth in degrees from point 1 to point 2, vectorized."""
    la1, la2 = np.radians(lat1), np.radians(lat2)
    dlon = np.radians(lon2 - lon1)
    y = np.sin(dlon) * np.cos(la2)
    x = np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(dlon)
    return np.degrees(np.arctan2(y, x))


def haversine_km(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    dla = la2 - la1
    dlo = np.radians(lon2 - lon1)
    h = np.sin(dla / 2.0) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(dlo / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


# --------------------------------------------------------------------------------------
# pair construction
# --------------------------------------------------------------------------------------

def all_pairs_with_shared_days(sta, M, args):
    """
    Every station pair, no distance band, gated only on concurrent recording days.

    Shared days for all pairs at once by boolean matrix product: M (S x D) -> M @ M.T is the
    count of days both stations recorded. Verified against an explicit AND on random pairs.
    """
    S = len(sta)
    log("  stations: {}   candidate pairs: {:,}".format(S, S * (S - 1) // 2))

    t0 = time.time()
    Mf = M.astype(np.float32)
    shared = np.zeros((S, S), dtype=np.int32)
    block = 256
    for s in range(0, S, block):
        e = min(s + block, S)
        shared[s:e] = np.rint(Mf[s:e] @ Mf.T).astype(np.int32)
    log("  shared-day matrix in {:.1f}s".format(time.time() - t0))

    rng = np.random.default_rng(0)
    bad = 0
    for _ in range(20):
        i, j = rng.integers(0, S, 2)
        if int(np.logical_and(M[i], M[j]).sum()) != int(shared[i, j]):
            bad += 1
    log("  shared-day self-check on 20 random pairs: {}".format("PASS" if bad == 0 else "FAIL ({})".format(bad)))
    if bad:
        raise SystemExit("shared-day matrix failed verification")

    iu, ju = np.triu_indices(S, k=1)
    sd = shared[iu, ju]
    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()
    dist = haversine_km(lat[iu], lon[iu], lat[ju], lon[ju])

    gate = np.where(dist < args.tier_split_km, args.tier1_min_days, args.tier2_min_days)
    keep = sd >= gate
    log("  pairs passing the shared-day gate ({}d under {} km, {}d over): {:,}".format(
        args.tier1_min_days, args.tier_split_km, args.tier2_min_days, int(keep.sum())))
    return iu[keep], ju[keep], dist[keep], sd[keep]


# --------------------------------------------------------------------------------------
# cell accumulation
# --------------------------------------------------------------------------------------

class CellAccumulator:
    """Per-cell sums built up over chunks of ray paths."""

    def __init__(self, n_cells):
        self.n = n_cells
        self.n_paths = np.zeros(n_cells, np.int64)
        self.ladder = {t: np.zeros(n_cells, np.int64) for t in OVERLAP_LADDER}
        self.sumL = np.zeros(n_cells, np.float64)
        self.sum_cos2 = np.zeros(n_cells, np.float64)
        self.sum_sin2 = np.zeros(n_cells, np.float64)
        self.az_hist = np.zeros((n_cells, N_AZ_BINS), np.float64)
        self.S_hist = np.zeros((n_cells, N_HIST_BINS), np.int64)
        self.L_hist = np.zeros((n_cells, N_HIST_BINS), np.int64)
        self.S_edges = np.logspace(np.log10(30), np.log10(12000), N_HIST_BINS + 1)
        self.L_edges = np.logspace(np.log10(50), np.log10(20100), N_HIST_BINS + 1)

    def add_segments(self, cell, seg_len, az_deg):
        """Length-weighted azimuth statistics. One entry per SEGMENT, so a path that
        traverses more of a cell contributes proportionally more -- the L_k weighting."""
        two = np.radians(2.0 * az_deg)
        self.sumL += np.bincount(cell, weights=seg_len, minlength=self.n)
        self.sum_cos2 += np.bincount(cell, weights=seg_len * np.cos(two), minlength=self.n)
        self.sum_sin2 += np.bincount(cell, weights=seg_len * np.sin(two), minlength=self.n)
        ab = np.mod(az_deg, 180.0)
        b = np.clip((ab / (180.0 / N_AZ_BINS)).astype(np.int64), 0, N_AZ_BINS - 1)
        self.az_hist += np.bincount(cell * N_AZ_BINS + b, weights=seg_len,
                                    minlength=self.n * N_AZ_BINS).reshape(self.n, N_AZ_BINS)

    def add_paths(self, cell, shared_days, path_len_km):
        """One entry per unique (path, cell): counts and per-path quality distributions."""
        self.n_paths += np.bincount(cell, minlength=self.n)
        for t in OVERLAP_LADDER:
            m = shared_days >= t
            if m.any():
                self.ladder[t] += np.bincount(cell[m], minlength=self.n)
        sb = np.clip(np.digitize(shared_days, self.S_edges) - 1, 0, N_HIST_BINS - 1)
        self.S_hist += np.bincount(cell * N_HIST_BINS + sb,
                                   minlength=self.n * N_HIST_BINS).reshape(self.n, N_HIST_BINS)
        lb = np.clip(np.digitize(path_len_km, self.L_edges) - 1, 0, N_HIST_BINS - 1)
        self.L_hist += np.bincount(cell * N_HIST_BINS + lb,
                                   minlength=self.n * N_HIST_BINS).reshape(self.n, N_HIST_BINS)


def hist_median(hist, edges):
    """Approximate per-row median from a histogram (bin centres)."""
    centres = np.sqrt(edges[:-1] * edges[1:])
    tot = hist.sum(axis=1)
    out = np.full(hist.shape[0], np.nan)
    nz = tot > 0
    if nz.any():
        cum = np.cumsum(hist[nz], axis=1)
        half = (tot[nz] / 2.0)[:, None]
        idx = (cum >= half).argmax(axis=1)
        out[nz] = centres[idx]
    return out


def rasterize(iu, ju, dist, sdays, sta, cell_tree, n_cells, args, acc, label=""):
    """Walk every ray path, assign its segments to equal-area cells, accumulate."""
    lat, lon = sta["lat"].to_numpy(), sta["lon"].to_numpy()
    P1 = unit_vectors(lat[iu], lon[iu])
    P2 = unit_vectors(lat[ju], lon[ju])

    order = np.argsort(dist)                      # group similar lengths so n_way is uniform
    t0 = time.time()
    done = 0
    chunk = args.chunk
    for s in range(0, len(order), chunk):
        sel = order[s:s + chunk]
        d = dist[sel]
        n_way = int(np.clip(np.ceil(d.max() / args.step_km) + 1, 3, 400))
        pts = slerp_paths(P1[sel], P2[sel], n_way)
        plat, plon = vec_to_latlon(pts)

        # Midpoints are averaged as 3-vectors, not as lat/lon, so paths crossing +-180
        # land in the right place instead of being dragged across the whole globe.
        mid = 0.5 * (pts[:, :-1, :] + pts[:, 1:, :])
        mlat, mlon = vec_to_latlon(mid)
        az = segment_azimuths(plat[:, :-1], plon[:, :-1], plat[:, 1:], plon[:, 1:])
        seg_len = np.repeat((d / (n_way - 1))[:, None], n_way - 1, axis=1)

        q = np.radians(np.stack([mlat.ravel(), mlon.ravel()], axis=1))
        cell = cell_tree.query(q, k=1, return_distance=False).ravel()

        acc.add_segments(cell, seg_len.ravel(), az.ravel())

        npath, nseg = mlat.shape
        pid = np.repeat(np.arange(npath), nseg)
        keyc = pid.astype(np.int64) * n_cells + cell
        uk = np.unique(keyc)                           # dedup: each path counted once per cell
        u_pid, u_cell = (uk // n_cells).astype(np.int64), (uk % n_cells).astype(np.int64)
        acc.add_paths(u_cell, sdays[sel][u_pid], d[u_pid])

        done += len(sel)
        el = time.time() - t0
        log("    [raster{}] {:,}/{:,} paths  {:.0f} paths/s  elapsed {:.0f}s".format(
            label, done, len(order), done / max(el, 1e-9), el))


def cell_metrics(acc, args, cell_area_km2):
    """Turn accumulators into the reported per-cell quantities."""
    import pandas as pd

    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.sqrt(acc.sum_cos2 ** 2 + acc.sum_sin2 ** 2) / np.where(acc.sumL > 0, acc.sumL, np.nan)
    R = np.clip(R, 0.0, 1.0)
    A = 1.0 - R
    kappa = np.where(A > 0, 2.0 / A - 1.0, np.inf)

    occupied = (acc.az_hist > 0).sum(axis=1)
    gap = np.full(acc.n, np.nan)
    nz = acc.n_paths > 0
    if nz.any():
        width = 180.0 / N_AZ_BINS
        occ = acc.az_hist[nz] > 0
        gaps = []
        for row in occ:
            idx = np.flatnonzero(row)
            if len(idx) == 0:
                gaps.append(180.0)
            elif len(idx) == 1:
                gaps.append(180.0)
            else:
                d = np.diff(idx)
                wrap = idx[0] + len(row) - idx[-1]
                gaps.append(max(d.max(), wrap) * width)
        gap[nz] = gaps

    med_S = hist_median(acc.S_hist, acc.S_edges)
    med_L = hist_median(acc.L_hist, acc.L_edges)
    fresnel = np.sqrt(args.fresnel_lambda_km * med_L / 2.0)
    rho = 1.0 - occupied / np.minimum(np.maximum(acc.n_paths, 1), N_AZ_BINS)

    cell_side = math.sqrt(cell_area_km2)
    df = pd.DataFrame(dict(
        n_paths=acc.n_paths,
        **{"n_ge_{}d".format(t): acc.ladder[t] for t in OVERLAP_LADDER},
        sum_traverse_km=acc.sumL, R=R, A=A, kappa=kappa,
        az_gap_deg=gap, az_bins_occupied=occupied, rho=rho,
        median_shared_days=med_S, median_path_km=med_L, fresnel_km=fresnel,
    ))
    df["resolution_matched"] = df["fresnel_km"] <= cell_side
    df["resolved"] = ((df["n_paths"] >= args.min_hits) & (df["A"] >= args.min_azq)
                      & (df["az_gap_deg"] <= args.max_gap_deg))
    return df


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stages", default="all", help="comma list of 0,1,2,3 or 'all'")
    p.add_argument("--root", default=None, help="campaign root (has results/ and packaged_h5/)")
    p.add_argument("--h5-dir", default=None, help="override the packaged_h5 directory")
    p.add_argument("--cache-dir", default=os.path.join(META_DIR, "delivered_cache"))
    p.add_argument("--out-dir", default=os.path.join(META_DIR, "delivered_network_reference"))
    p.add_argument("--manifest", default=os.path.join(META_DIR, "fps_stations_v2.csv"))
    p.add_argument("--planned-pairs", default=os.path.join(META_DIR, "fps_pairs.csv"))
    p.add_argument("--completed-only", action="store_true",
                   help="scan only package_ok=True stations (excludes in-flight files)")
    p.add_argument("--limit", type=int, default=0)
    # 3,000 cells = 412 km across. The PHYSICAL limit is finer than this: the first Fresnel
    # zone at these path lengths is ~200 km, which would justify ~12,000 cells. But the
    # delivered network currently yields only ~7.6k usable paths, and at 12,000 cells the
    # per-cell azimuth statistics are dominated by nearest-neighbour binning aliasing on the
    # quasi-regular cell lattice -- it renders as spiral banding that is an artifact, not
    # structure. 3,000 cells is the finest grid the present path count actually supports.
    # Revisit once the long-duration stations still in flight have landed.
    p.add_argument("--n-cells", type=int, default=3000)
    p.add_argument("--min-hits", type=int, default=10)
    p.add_argument("--min-azq", type=float, default=0.6)
    p.add_argument("--max-gap-deg", type=float, default=60.0)
    p.add_argument("--tier-split-km", type=float, default=1500.0)
    p.add_argument("--tier1-min-days", type=int, default=90)
    p.add_argument("--tier2-min-days", type=int, default=365)
    p.add_argument("--fresnel-lambda-km", type=float, default=100.0)
    # Must be well under the cell size, or a near-straight path "snaps" onto lattice rows
    # and manufactures banding in the azimuth field. ~1/16 of a cell is comfortably safe.
    p.add_argument("--step-km", type=float, default=25.0, help="waypoint spacing along a path")
    p.add_argument("--chunk", type=int, default=20000, help="paths rasterized per block")
    p.add_argument("--restrict-max-km", default="none,10000,5000,3000,2000,1500,1110")
    p.add_argument("--snr-ref-days", type=float, default=365.0)
    p.add_argument("--snr-exponent", type=float, default=0.5)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    stages = {0, 1, 2, 3} if args.stages == "all" else {int(s) for s in args.stages.split(",") if s.strip()}

    if 0 in stages:
        if not args.root:
            raise SystemExit("--root is required for stage 0")
        stage0_reconcile(args)
    if 1 in stages:
        rc = stage1_scan(args)
        if rc:
            return rc
    if stages & {2, 3}:
        from analysis_figures import run_analysis   # split out to keep this file readable
        run_analysis(args, stages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
