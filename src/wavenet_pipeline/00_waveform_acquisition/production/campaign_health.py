#!/usr/bin/env python
"""
Campaign health, measured the way the 2026-10-02 retrospective says to measure.

That day a healthy campaign (935 shards, 0 empty, 637 GB) was reported as a near-total
failure and cancelled. Nothing was wrong with the data. Three things were wrong with the
measurement, and this script is built so none of them can recur:

  1. UNVALIDATED DENOMINATOR. "Days obtained vs station span" divided real days by
     discovery's year_min/year_max, which are routinely open-ended (end_utc = 2599-12-31).
     Every station scored ~0%. -> Here, any ratio whose denominator fails a sanity check is
     printed as "n/a", never as a number, and the span source is always named.

  2. WRONG POPULATION, STATED AS THE WHOLE. The verdict came from result JSONs carrying a
     field only the newest orchestrator wrote, which silently selected the residual hard
     tail and excluded everything already packaged. -> Here, EVERY number is printed with
     the population it covers, and the default population is all shards on disk.

  3. NEVER OPENED THE DATA. Every number came from result JSONs. The first read of an
     actual HDF5 file inverted the verdict. -> Here, result JSONs are not read at all.

The order of the report is the lesson itself: the crudest global observable first, compared
against a known-good reference, before anything per-station. If v2 holds 89% of v1's bytes,
it is not broken, and no per-station analysis should be allowed to override that.

Reads only `grp.attrs` and `grp['_coverage'][cha]`. NEVER slices a channel dataset: one
channel is ~1.2e9 float32 (4.9 GB), and slicing it would read the whole archive.

    python campaign_health.py --root /scratch/tolugboj_lab/wavenet_ncf_production_v2 \
                              --reference /scratch/tolugboj_lab/wavenet_ncf_production
"""
import argparse
import glob
import os
import sys
import warnings

warnings.filterwarnings("ignore")
import h5py
import numpy as np

# A span wider than this cannot be a real station lifetime -- it is an open-ended epoch
# leaking in (the 2599-12-31 case). Any ratio built on it is reported as n/a.
MAX_PLAUSIBLE_SPAN_YEARS = 70


def disk_summary(root):
    """The crudest global observable: how many shards, how many bytes. No interpretation."""
    paths = glob.glob(os.path.join(root, "packaged_h5", "*.h5"))
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return len(paths), total


def read_shard(path):
    """Everything this script knows about a station, read from the file itself."""
    with h5py.File(path, "r") as f:
        g = f[list(f.keys())[0]]
        chans = sorted(c for c in g if not c.startswith("_"))
        if not chans:
            return dict(days=0, nchan=0, bands=(), corrected=False, full_band=False)
        # _coverage is one byte per day; reading it is cheap. The channel datasets are not.
        days = int((np.asarray(g["_coverage"][chans[0]][:]) > 0).sum())
        units = {str(g[c].attrs.get("units")) for c in chans}
        rates = {round(float(g[c].attrs["sampling_rate"]), 4) for c in chans}
        bands = {c[:2] for c in chans}
        return dict(
            days=days, nchan=len(chans), bands=tuple(sorted(bands)),
            corrected=(units == {"m"} and rates == {1.0}
                       and g.attrs.get("patch_level") == 3 and "_stationxml_raw" in g),
            full_band=any(len([c for c in chans if c.startswith(b)]) >= 3 for b in bands))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--reference", help="a known-good campaign root to compare bytes against")
    ap.add_argument("--min-days", type=int, default=90)
    args = ap.parse_args()

    # ---- STEP 1: the crude global observable, before any hypothesis ----
    n_shards, n_bytes = disk_summary(args.root)
    print("=" * 74)
    print("STEP 1 -- GLOBAL OBSERVABLE (the whole campaign, nothing filtered)")
    print("=" * 74)
    print("  shards on disk : {:,}".format(n_shards))
    print("  bytes on disk  : {:.1f} GB".format(n_bytes / 1e9))
    if args.reference:
        r_shards, r_bytes = disk_summary(args.reference)
        print("  reference      : {:,} shards, {:.1f} GB  ({})".format(
            r_shards, r_bytes / 1e9, os.path.basename(args.reference.rstrip("/"))))
        if r_bytes:
            pct = 100.0 * n_bytes / r_bytes
            print("  this campaign holds {:.0f}% of the reference's bytes".format(pct))
            print("  >> {}".format(
                "COMPARABLE TO A KNOWN-GOOD RUN -- do not call this a failure"
                if pct >= 50 else
                "WELL BELOW the reference -- a systematic problem is plausible"))
    if not n_shards:
        print("\n  no shards -- nothing further to measure")
        return 0

    # ---- STEP 2: the data itself ----
    rows, unreadable = [], []
    for p in glob.glob(os.path.join(args.root, "packaged_h5", "*.h5")):
        try:
            r = read_shard(p)
            r["key"] = os.path.basename(p)[:-3]
            rows.append(r)
        except Exception as exc:
            unreadable.append((os.path.basename(p), type(exc).__name__))

    usable = [r for r in rows if r["days"] >= args.min_days and r["corrected"] and r["full_band"]]
    partial = [r for r in rows if 0 < r["days"] < args.min_days]
    empty = [r for r in rows if r["days"] == 0]

    print("")
    print("=" * 74)
    print("STEP 2 -- MEASURED FROM THE HDF5 FILES   (population: all {:,} shards)".format(len(rows)))
    print("=" * 74)
    print("  usable   (>={}d, corrected to m @1Hz, full 3-comp band) : {:>5}".format(
        args.min_days, len(usable)))
    print("  partial  (1-{}d)                                        : {:>5}".format(
        args.min_days - 1, len(partial)))
    print("  empty    (0 days)                                       : {:>5}".format(len(empty)))
    print("  unreadable                                              : {:>5}".format(len(unreadable)))

    d = sorted(r["days"] for r in rows if r["days"] > 0)
    if d:
        print("")
        print("  days per non-empty shard: median {:,}  max {:,}  total {:,}".format(
            d[len(d) // 2], d[-1], sum(d)))

    # ---- STEP 3: depth, WITHOUT an unvalidated denominator ----
    # Deliberately NOT expressed as "% of station span". That ratio is what produced the
    # false verdict: the span came from open-ended epochs, so every station read ~0%.
    # Absolute depth bands carry the same information and cannot be corrupted by a bad
    # denominator.
    print("")
    print("=" * 74)
    print("STEP 3 -- DEPTH (absolute days; no span ratio, see module docstring)")
    print("=" * 74)
    for lo, hi, lab in [(0, 1, "0 days"), (1, 90, "1-89"), (90, 365, "90-364"),
                        (365, 1095, "1-3 yr"), (1095, 10 ** 9, ">3 yr")]:
        n = sum(1 for r in rows if lo <= r["days"] < hi)
        bar = "#" * int(40.0 * n / max(len(rows), 1))
        print("  {:<8} {:>5}  {}".format(lab, n, bar))

    print("")
    print("  VERDICT: {}".format(
        "campaign is producing usable data" if len(usable) >= 0.25 * len(rows)
        else "usable fraction is low -- investigate, but confirm against STEP 1 first"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
