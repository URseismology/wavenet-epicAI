#!/usr/bin/env python
"""
Collect the R-1 validation test and apply the decision rule in TEST_PLAN.txt.

Answers two questions, kept strictly apart:

  RECOVERY -- does re-downloading a truncated station with daily windows actually recover the
  days year-wide windows dropped? Measured as median(test_days / production_days). This
  decides whether the full 452-station repair runs at all.

  COST -- how long does a station take, as a function of how much history it has? Daily
  windows make request count scale with history length, so cost should be roughly linear in
  target days. Fitting that gives a MEASURED threshold for splitting the full repair between
  legacy BlueHive (short stations; it has more short-job capacity, 91 standard / 21
  interactive / 12 debug nodes against BH3's 20 / 1 / none) and BH3 (long stations; 9-15 day
  walltimes and 260+ concurrent tasks).

Safe to run at any time; reports progress while the test is still running.

    python3 collect_r1_test.py
"""
import glob
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
import h5py
import numpy as np
import pandas as pd

ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_r1_20261005"
OUT = os.path.join(ROOT, "R1_RESULT.txt")


def clean_days(key):
    """Days on response-corrected channels, read from the file. -1 = no shard yet."""
    p = os.path.join(ROOT, "packaged_h5", key + ".h5")
    if not os.path.exists(p):
        return -1
    try:
        with h5py.File(p, "r", locking=False) as f:
            g = f[list(f.keys())[0]]
            ch = sorted(c for c in g if not c.startswith("_"))
            clean = [c for c in ch if str(g[c].attrs.get("units")) == "m"]
            if not clean or "_coverage" not in g:
                return 0
            return int((np.asarray(g["_coverage"][clean[0]][:]) > 0).sum())
    except Exception:
        return -2        # being written right now


st = pd.read_csv(os.path.join(ROOT, "TEST_STATIONS.csv"))
res = {}
for f in glob.glob(os.path.join(ROOT, "results", "*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    res["{}.{}".format(d["network"], d["station"])] = d

# ---- VOID check first: pinning must have happened, or the test measured the old failure ----
pinned = set()
for f in glob.glob(os.path.join(ROOT, "logs", "*.out")):
    try:
        pinned.update(re.findall(r"\[discovery\] (\S+) pinned to", open(f, errors="ignore").read()))
    except OSError:
        pass

L = ["R-1 VALIDATION -- collected result", "=" * 70, "root: {}".format(ROOT), ""]
rec = st[st["role"] == "recovery"]
done = [k for k in rec["key"] if k in res]
L.append("recovery stations : {} of {} finished".format(len(done), len(rec)))
L.append("pinning observed  : {} of {} logged '[discovery] pinned'".format(
    len(pinned & set(rec["key"])), len(rec)))
if done and not (pinned & set(done)):
    L += ["", "*** TEST VOID: no finished station shows pinning. This measured the old",
          "*** failure, not the fix. No conclusion may be drawn."]
    print("\n".join(L)); open(OUT, "w").write("\n".join(L)); sys.exit(0)

rows = []
for _, r in st.iterrows():
    k = r["key"]
    new = clean_days(k)
    d = res.get(k, {})
    rows.append(dict(key=k, role=r["role"], expected=int(r["expected"]),
                     prod=int(r["prod_clean_days"]), test=new,
                     secs=d.get("download_elapsed_s"), finished=k in res))

L += ["", "{:<12} {:>6} {:>8} {:>8} {:>8} {:>9}".format(
    "STATION", "role", "expected", "prod", "test", "x"), "-" * 58]
ratios = []
for r in sorted(rows, key=lambda r: -r["expected"]):
    x = ""
    if r["finished"] and r["test"] > 0 and r["prod"] > 0:
        v = r["test"] / r["prod"]
        if r["role"] == "recovery":
            ratios.append(v)
        x = "{:.1f}x".format(v)
    L.append("{:<12} {:>6} {:>8,} {:>8,} {:>8} {:>9}".format(
        r["key"], r["role"][:6], r["expected"], r["prod"],
        r["test"] if r["finished"] else "running", x))

L.append("")
if ratios:
    ratios.sort()
    med = ratios[len(ratios) // 2]
    L.append("PRIMARY METRIC  median(test/production) = {:.2f}x   over {} finished".format(
        med, len(ratios)))
    fin = [r for r in rows if r["role"] == "recovery" and r["finished"] and r["expected"]]
    if fin:
        L.append("absolute completeness: test holds {:,} of {:,} expected days ({:.1f}%)".format(
            sum(r["test"] for r in fin if r["test"] > 0),
            sum(r["expected"] for r in fin),
            100.0 * sum(max(r["test"], 0) for r in fin) / sum(r["expected"] for r in fin)))
    if len(ratios) < len(rec):
        L.append("(decision applies once all {} recovery stations finish)".format(len(rec)))
    else:
        L += ["", "DECISION, per TEST_PLAN.txt written before any result existed:"]
        if med >= 2.0:
            L.append("  {:.2f}x >= 2.0  ->  RUN the full R-1 repair on all 452".format(med))
        elif med < 1.2:
            L.append("  {:.2f}x < 1.2  ->  DO NOT. Truncation is not the limiting factor;".format(med))
            L.append("                     the 1.3M-day projection is wrong. Re-investigate.")
        else:
            L.append("  {:.2f}x in 1.2-2.0  ->  run, but revise the projected gain DOWN".format(med))
            L.append("                          by this ratio before quoting it.")

# ---- COST: wall-clock vs history length, which sets the BH / BH3 split ----
cost = [(r["expected"], r["secs"]) for r in rows if r["finished"] and r["secs"]]
L += ["", "COST (sets the legacy-BH / BH3 split; not a pass/fail)"]
if len(cost) >= 3:
    x = np.array([c[0] for c in cost], float)
    y = np.array([c[1] for c in cost], float) / 3600.0
    rate = float(np.sum(x * y) / np.sum(x * x)) if np.sum(x * x) else 0.0
    L.append("  hours per 1,000 target days : {:.2f}   (fit through origin, n={})".format(
        rate * 1000, len(cost)))
    for cap, lab in ((4, "legacy BH 'standard' 4 h"), (12, "BH interactive 12 h"),
                     (96, "BH3 standard 4 d")):
        if rate > 0:
            L.append("    fits within {:<22}: stations up to ~{:,.0f} target days".format(
                lab, cap / rate))
    small = sum(1 for _, r2 in zip(x, y) if r2 <= 4)
    L.append("  of {} measured, {} finished inside 4 h".format(len(cost), small))
else:
    L.append("  not enough finished stations yet ({} with timing)".format(len(cost)))

L += ["", "COST PROBES (largest truncated stations; excluded from the metric)"]
for r in [r for r in rows if r["role"] == "cost_probe"]:
    m = os.path.join(ROOT, "scratch_work", r["key"].replace(".", "_"), "mseed")
    n = len(os.listdir(m)) if os.path.isdir(m) else 0
    L.append("  {:<12} expected {:>6,}  raw files so far {:>7,}  test days {}".format(
        r["key"], r["expected"], n, r["test"] if r["finished"] else "running"))

txt = "\n".join(L)
print(txt)
open(OUT, "w").write(txt)
print("\nwritten -> {}".format(OUT))
