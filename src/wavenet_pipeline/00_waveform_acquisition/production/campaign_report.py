#!/usr/bin/env python
"""
State of the NCF campaign: what is CLEANLY packaged, what is outstanding, and what fixing
each outstanding issue buys in coverage terms.

Runs on LEGACY BlueHive while BlueHive3 runs the campaign (they share /scratch/tolugboj_lab),
so reporting never competes with production for slots.

    python3 campaign_report.py                  # v1 + v2, writes a dated markdown report
    python3 campaign_report.py --settle 300     # stricter in-flight exclusion

WHY "CLEAN" DAYS, NOT JUST DAYS
    A day recorded on a channel that could not be response-corrected (`units='counts'`) is
    not usable for cross-correlation -- amplitudes are in digitiser counts, orders of
    magnitude off, and cannot be mixed with displacement. This report therefore counts days
    only on response-corrected channels. Total days and clean days differ, and the gap is
    exactly the repair backlog.

WHY DAYS DECIDE COVERAGE (2026-10-01 footprint analysis, docs/memos/)
    A pair of stations contributes a ray path only over days they were BOTH recording. At
    1,023 stations there are 522,753 possible pairs and only 7,575 survived, because the gate
    needs 90 shared days under 1,500 km and 365 over it, against a median station duration of
    229 days. Time overlap -- not geometry -- is the binding constraint, and 49.7% of cells
    were resolved. So depth per station converts into surviving pairs, and surviving pairs
    into resolved cells. That is why a truncated station is not a small loss.

WHY THESE NUMBERS CAN BE TRUSTED
    On 2026-10-02 a healthy campaign was declared a near-total failure and cancelled on
    measurement errors alone. Three safeguards, each aimed at one of those errors:
      * the denominator is VALIDATED before use -- target days come from the manifest `days`
        column (key index of real availability), checked for plausibility. The failed verdict
        divided by discovery epochs running to 2599, so every station scored ~0%.
      * every count names the POPULATION it covers. "4 of 254" was true and useless because
        the 254 were the residual hard tail, reported as the whole campaign.
      * everything is read from the HDF5 FILES. Result JSONs appear only where they are the
        sole record (per-channel response failures).

RACE SAFETY -- this runs against roots a live campaign is writing:
      * the file list is snapshotted once, then read.
      * files modified within --settle seconds are IN FLIGHT: excluded from every statistic,
        not counted as broken.
      * HDF5 opened read-only, locking disabled; BlockingIOError is a state, not a fault.
      * only `grp.attrs` and `grp['_coverage'][cha]` are read -- a channel dataset is ~1.2e9
        float32 (4.9 GB) and slicing one would read the archive.
      * the report is written OUTSIDE every scanned root.
"""
import argparse
import glob
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import h5py
import numpy as np
import pandas as pd

V2 = "/scratch/tolugboj_lab/wavenet_ncf_production_v2"
V1 = "/scratch/tolugboj_lab/wavenet_ncf_production"
DISC = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"
OUTDIR = "/scratch/tolugboj_lab/wavenet_ncf/reports"
MAX_PLAUSIBLE_DAYS = 60 * 365
GATE_NEAR, GATE_FAR = 90, 365          # shared-day gates from the footprint analysis


def read_shard(path):
    """attrs + _coverage only. Separates TOTAL days from CLEAN (response-corrected) days."""
    try:
        with h5py.File(path, "r", locking=False) as f:
            g = f[list(f.keys())[0]]
            ch = sorted(c for c in g if not c.startswith("_"))
            if not ch:
                return dict(days=0, clean_days=0, chans=[], counts=[], rates=set())
            counts = [c for c in ch if str(g[c].attrs.get("units")) != "m"]
            clean = [c for c in ch if c not in counts]
            rates = {round(float(g[c].attrs["sampling_rate"]), 3) for c in ch}
            def cov(c):
                if "_coverage" not in g or c not in g["_coverage"]:
                    return 0
                return int((np.asarray(g["_coverage"][c][:]) > 0).sum())
            return dict(days=cov(ch[0]), clean_days=cov(clean[0]) if clean else 0,
                        chans=ch, counts=counts, rates=rates)
    except BlockingIOError:
        return "in_flight"
    except OSError as e:
        return "in_flight" if "unable to lock" in str(e).lower() else "unreadable"
    except Exception:
        return "unreadable"


def scan(root, settle):
    now = time.time()
    snap = sorted(glob.glob(os.path.join(root, "packaged_h5", "*.h5")))
    rows, inflight, unreadable, nbytes = {}, [], [], 0
    for p in snap:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key, nbytes = os.path.basename(p)[:-3], nbytes + st.st_size
        if now - st.st_mtime < settle:
            inflight.append(key); continue
        r = read_shard(p)
        if r == "in_flight":
            inflight.append(key)
        elif r == "unreadable":
            unreadable.append(key)
        else:
            rows[key] = r
    md = os.path.join(root, "station_metadata")
    xml = {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(md, "*.xml"))}
    stat = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(md, "*.json"))} - {"_coverage"}
    return dict(root=root, snap=len(snap), rows=rows, inflight=inflight,
                unreadable=unreadable, nbytes=nbytes, xml=xml, stat=stat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", default=[V2, V1])
    ap.add_argument("--settle", type=int, default=120)
    args = ap.parse_args()

    man = pd.read_csv(os.path.join(V2, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    # Validate the denominator EXPLICITLY, including NaN. A first version of this check used
    # `days <= 0`, which silently admitted NaN because `NaN <= 0` is False -- the same shape of
    # error as the 2599-epoch denominator it exists to prevent. A station with an unusable
    # target is kept in the station count but excluded from every ratio, rather than being
    # quietly dropped or quietly counted as zero.
    raw_target = dict(zip(man["key"], man["days"]))
    target, bad = {}, {}
    for k, v in raw_target.items():
        ok = isinstance(v, (int, float)) and v == v and 0 < v <= MAX_PLAUSIBLE_DAYS
        (target if ok else bad)[k] = v
    tot_target = int(sum(target.values()))

    routed = set()
    if os.path.exists(DISC):
        d = pd.read_csv(DISC)
        routed = set(d[d["status"] == "ROUTED"]["key"])

    L = ["# NCF campaign — packaging state and repair backlog", "",
         "Generated {} on `{}`.".format(time.strftime("%Y-%m-%d %H:%M"), os.uname()[1]), "",
         "**Target:** {:,} stations with a usable target, **{:,} expected station-days** (manifest "
         "`days`, the key index of real availability). Denominator validated before use: {} of "
         "{:,} manifest rows have an unusable target (NaN or implausible) and are excluded from "
         "every ratio.".format(
             len(target), tot_target, len(bad), len(raw_target)),
         "",
         "**Why days matter:** a pair contributes a ray path only over days BOTH stations "
         "recorded. At 1,023 stations, 522,753 pairs were possible and **7,575 survived** the "
         "90-day (<1,500 km) / 365-day gate, against a median duration of 229 days. Time "
         "overlap, not geometry, is the binding constraint. Depth per station converts into "
         "surviving pairs, and pairs into resolved cells (49.7% at last measurement).", ""]

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M")

    for root in args.roots:
        if not os.path.isdir(root):
            continue
        s = scan(root, args.settle)
        rows, name = s["rows"], os.path.basename(root)
        have = [k for k, r in rows.items() if r["clean_days"] > 0]
        tot_days = sum(r["days"] for r in rows.values())
        tot_clean = sum(r["clean_days"] for r in rows.values())
        mixed = [k for k, r in rows.items() if r["counts"] and len(r["counts"]) < len(r["chans"])]
        allcnt = [k for k, r in rows.items() if r["chans"] and len(r["counts"]) == len(r["chans"])]
        badrate = [k for k, r in rows.items() if r["rates"] - {1.0}]
        noshard = sorted(set(raw_target) - set(rows) - set(s["inflight"]))
        shallow = [k for k in have if 0 < target.get(k, 0) <= MAX_PLAUSIBLE_DAYS
                   and rows[k]["clean_days"] < 0.25 * target[k]]
        gate_near = sum(1 for k in have if rows[k]["clean_days"] >= GATE_NEAR)
        gate_far = sum(1 for k in have if rows[k]["clean_days"] >= GATE_FAR)

        # ---- what each repair buys, in clean station-days ----
        gain_r1 = sum(max(target.get(k, 0) - rows[k]["clean_days"], 0) for k in have
                      if 0 < target.get(k, 0) <= MAX_PLAUSIBLE_DAYS)
        gain_r2 = sum(target.get(k, 0) for k in noshard if k in routed)
        gain_r3 = sum(rows[k]["days"] - rows[k]["clean_days"] for k in mixed) + \
                  sum(rows[k]["days"] for k in allcnt)

        print("")
        print("=" * 74)
        print(name)
        print("=" * 74)
        print("  shards on disk {:,} ({:.1f} GB) | settled {:,} | in flight {:,} | unreadable {:,}"
              .format(s["snap"], s["nbytes"] / 1e9, len(rows), len(s["inflight"]), len(s["unreadable"])))
        print("")
        print("  1. CLEANLY PACKAGED   (population: all {:,} manifest stations)".format(len(target)))
        print("     stations with clean data : {:,} / {:,}  ({:.1f}%)".format(
            len(have), len(target), 100.0 * len(have) / len(target)))
        print("     clean station-days       : {:,} / {:,}  ({:.1f}% of target)".format(
            tot_clean, tot_target, 100.0 * tot_clean / tot_target))
        print("     total days incl. uncorrected: {:,}   -> {:,} days are NOT usable".format(
            tot_days, tot_days - tot_clean))
        if have:
            cd = sorted(rows[k]["clean_days"] for k in have)
            print("     clean days per station   : median {:,}  max {:,}".format(
                cd[len(cd) // 2], cd[-1]))
        print("     clear 90-day gate        : {:,} stations".format(gate_near))
        print("     clear 365-day gate       : {:,} stations".format(gate_far))
        print("")
        print("  2. OUTSTANDING ISSUES -- and what fixing each buys")
        print("     {:<42} {:>7}  {:>14}".format("issue (affected stations)", "count", "clean-days gain"))
        print("     {:<42} {:>7}  {:>14}".format("R-1 truncated, <25% of own target", len(shallow), "{:,}".format(gain_r1)))
        print("     {:<42} {:>7}  {:>14}".format("R-2 no shard (routed, recoverable)",
              sum(1 for k in noshard if k in routed), "{:,}".format(gain_r2)))
        print("     {:<42} {:>7}  {:>14}".format("R-3 mixed units (some chans in counts)", len(mixed), ""))
        print("     {:<42} {:>7}  {:>14}".format("R-4 entirely raw counts", len(allcnt), "{:,}".format(gain_r3)))
        print("     {:<42} {:>7}  {:>14}".format("    no StationXML at all", len(s["stat"] - s["xml"]), ""))
        print("     {:<42} {:>7}  {:>14}".format("R-5 no shard, NOT routable", sum(1 for k in noshard if k not in routed), "n/a"))
        print("     {:<42} {:>7}  {:>14}".format("R-7 off-nominal sampling rate", len(badrate), ""))
        print("")
        print("  3. IF ALL REPAIRS LAND")
        proj = tot_clean + gain_r1 + gain_r2 + gain_r3
        print("     clean station-days {:,} -> {:,}  ({:.1f}% -> {:.1f}% of target)".format(
            tot_clean, proj, 100.0 * tot_clean / tot_target, 100.0 * min(proj, tot_target) / tot_target))
        print("     stations with data {:,} -> {:,}".format(
            len(have), len(have) + sum(1 for k in noshard if k in routed) + len(allcnt)))
        print("     more stations clearing the 90/365-day gates means more surviving pairs,")
        print("     which is what sets resolved-cell coverage (49.7% at last measurement).")

        # per-station table + per-issue station lists, so repairs are actionable
        rec = [dict(key=k, expected=int(target.get(k, 0)), total_days=rows[k]["days"],
                    clean_days=rows[k]["clean_days"],
                    pct=round(100.0 * rows[k]["clean_days"] / target[k], 1) if target.get(k) else None,
                    n_chan=len(rows[k]["chans"]), n_counts=len(rows[k]["counts"]))
               for k in sorted(rows)]
        for k in noshard:
            rec.append(dict(key=k, expected=int(target.get(k, 0)), total_days=0, clean_days=0,
                            pct=0.0, n_chan=0, n_counts=0))
        pd.DataFrame(rec).sort_values("expected", ascending=False).to_csv(
            os.path.join(OUTDIR, "{}_stations_{}.csv".format(name, stamp)), index=False)
        iss = ([dict(key=k, issue="R-1 truncated", action="STEP 3 re-download") for k in shallow]
               + [dict(key=k, issue="R-2 no shard (routed)", action="STEP 3 re-download")
                  for k in noshard if k in routed]
               + [dict(key=k, issue="R-3 mixed units", action="STEP 1+4 re-fetch, re-package")
                  for k in mixed]
               + [dict(key=k, issue="R-4 all raw counts", action="STEP 1+4 re-fetch, re-package")
                  for k in allcnt]
               + [dict(key=k, issue="R-5 not routable", action="decision, not a repair")
                  for k in noshard if k not in routed]
               + [dict(key=k, issue="R-7 sample rate", action="STEP 4 re-package") for k in badrate])
        pd.DataFrame(iss).to_csv(os.path.join(OUTDIR, "{}_issues_{}.csv".format(name, stamp)),
                                 index=False)

        L += ["## {}".format(name), "",
              "### 1. Cleanly packaged", "",
              "| metric | value | population |", "|---|---|---|",
              "| stations with clean data | **{:,}** ({:.1f}%) | of {:,} manifest |".format(
                  len(have), 100.0 * len(have) / len(target), len(target)),
              "| clean station-days | **{:,}** ({:.1f}%) | of {:,} target |".format(
                  tot_clean, 100.0 * tot_clean / tot_target, tot_target),
              "| days not usable (uncorrected) | {:,} | of {:,} total packaged |".format(
                  tot_days - tot_clean, tot_days),
              "| clear 90-day gate | {:,} | stations |".format(gate_near),
              "| clear 365-day gate | {:,} | stations |".format(gate_far),
              "| in flight, excluded | {:,} | — |".format(len(s["inflight"])), "",
              "### 2. Outstanding issues", "",
              "| issue | stations | clean-day gain | repair |", "|---|---|---|---|",
              "| R-1 truncated (<25% of own target) | {:,} | {:,} | STEP 3 re-download |".format(len(shallow), gain_r1),
              "| R-2 no shard, routed | {:,} | {:,} | STEP 3 re-download |".format(
                  sum(1 for k in noshard if k in routed), gain_r2),
              "| R-3 mixed units | {:,} | — | STEP 1+4 re-fetch + re-package |".format(len(mixed)),
              "| R-4 all raw counts | {:,} | {:,} | STEP 1+4 re-fetch + re-package |".format(len(allcnt), gain_r3),
              "| R-4 no StationXML | {:,} | — | STEP 1 re-fetch |".format(len(s["stat"] - s["xml"])),
              "| R-5 not routable | {:,} | n/a | decision, not a repair |".format(
                  sum(1 for k in noshard if k not in routed)),
              "| R-7 off-nominal rate | {:,} | — | STEP 4 re-package |".format(len(badrate)), "",
              "### 3. If all repairs land", "",
              "Clean station-days **{:,} -> {:,}** ({:.1f}% -> {:.1f}% of target); stations with "
              "data **{:,} -> {:,}**. More stations clearing the 90/365-day gates means more "
              "surviving pairs, which is what sets resolved-cell coverage.".format(
                  tot_clean, proj, 100.0 * tot_clean / tot_target,
                  100.0 * min(proj, tot_target) / tot_target, len(have),
                  len(have) + sum(1 for k in noshard if k in routed) + len(allcnt)), "",
              "Per-station detail: `{}_stations_{}.csv` · per-issue station lists: "
              "`{}_issues_{}.csv`".format(name, stamp, name, stamp), ""]

    p = os.path.join(OUTDIR, "report_{}.md".format(stamp))
    open(p, "w").write("\n".join(L))
    print("")
    print("report -> {}".format(p))


if __name__ == "__main__":
    sys.exit(main())
