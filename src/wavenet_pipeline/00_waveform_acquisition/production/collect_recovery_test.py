#!/usr/bin/env python
"""
Collect the recovery test and apply the decision rule written in TEST_PLAN.txt.

Safe to run at any time, as often as you like -- it reports progress while the test is
still running and only applies the decision once the 40 recovery stations have all finished.

Reads recovery from the HDF5 FILES. Result JSONs are deliberately not trusted for the
verdict: they are what reported success on truncated stations and what hid 536 silent
zero-byte failures behind a guard that could not fire.

    python3 collect_recovery_test.py            # prints, and writes CANARY_RESULT.txt
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

ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_recovery_20261005"
OUT = os.path.join(ROOT, "CANARY_RESULT.txt")

st = pd.read_csv(os.path.join(ROOT, "TEST_STATIONS.csv"))
recovery = st[st["role"] == "recovery"]["key"].tolist()
probes = st[st["role"] == "throughput"]["key"].tolist()

# ---- the VOID condition, checked first. If pinning did not happen the test proves nothing.
logs = glob.glob(os.path.join(ROOT, "logs", "*.out"))
pinned = {m.group(1) for f in logs
          for m in re.finditer(r"\[discovery\] (\S+) pinned to", open(f, errors="ignore").read())}
finished = {}
for f in glob.glob(os.path.join(ROOT, "results", "*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    finished["{}.{}".format(d["network"], d["station"])] = d


def days_in_shard(key):
    """Days actually present, read from the file. -1 means no shard exists."""
    p = os.path.join(ROOT, "packaged_h5", key + ".h5")
    if not os.path.exists(p):
        return -1
    try:
        with h5py.File(p, "r") as f:
            g = f[list(f.keys())[0]]
            ch = sorted(c for c in g if not c.startswith("_"))
            if not ch or "_coverage" not in g:
                return 0
            return int((np.asarray(g["_coverage"][ch[0]][:]) > 0).sum())
    except Exception:
        return -2        # unreadable, usually because it is being written right now


L = []
L.append("RECOVERY TEST -- collected result")
L.append("=" * 68)
L.append("root : {}".format(ROOT))
L.append("")
done_rec = [k for k in recovery if k in finished]
L.append("recovery stations : {} of {} finished".format(len(done_rec), len(recovery)))
L.append("pinning observed  : {} of {} stations logged a '[discovery] pinned' line"
         .format(len(pinned & set(recovery)), len(recovery)))

if done_rec and not (pinned & set(done_rec)):
    L.append("")
    L.append("*** TEST VOID: no finished station shows pinning. The fix did not take effect,")
    L.append("*** so this run says nothing about recovery. Fix the binding and rerun.")
    print("\n".join(L)); open(OUT, "w").write("\n".join(L)); sys.exit(0)

rows = []
for k in recovery:
    d = finished.get(k)
    rows.append((k, days_in_shard(k), (d or {}).get("download_bytes") or 0, k in pinned, bool(d)))
rec_done = [r for r in rows if r[4]]
recovered = [r for r in rec_done if r[1] >= 1]

L.append("")
L.append("{:<12} {:>7} {:>14} {:>8}".format("STATION", "days", "bytes", "pinned"))
L.append("-" * 46)
for k, dy, by, pin, fin in sorted(rows, key=lambda r: -r[1]):
    L.append("{:<12} {:>7} {:>14,} {:>8}".format(
        k, dy if fin else "running", by, "yes" if pin else "-"))

L.append("")
if rec_done:
    rate = 100.0 * len(recovered) / len(rec_done)
    L.append("RECOVERED (>=1 day packaged): {} of {} finished  =  {:.0f}%".format(
        len(recovered), len(rec_done), rate))
    if len(rec_done) < len(recovery):
        L.append("(still running -- decision applies once all {} have finished)".format(len(recovery)))
    else:
        L.append("")
        L.append("DECISION, per TEST_PLAN.txt written before any result existed:")
        if rate >= 25:
            L.append("  {:.0f}% >= 25%  ->  RERUN ALL 557 in production".format(rate))
        elif rate < 10:
            L.append("  {:.0f}% < 10%  ->  DO NOT RERUN. Record as an issue and stop.".format(rate))
        else:
            L.append("  {:.0f}% in 10-25%  ->  rerun only the recovering subclass".format(rate))

L.append("")
L.append("THROUGHPUT PROBES (cost only -- excluded from the rate)")
for k in probes:
    mdir = os.path.join(ROOT, "scratch_work", k.replace(".", "_"), "mseed")
    n = len(os.listdir(mdir)) if os.path.isdir(mdir) else 0
    L.append("  {:<12} raw files so far: {:>7,}   days packaged: {}".format(
        k, n, days_in_shard(k)))

txt = "\n".join(L)
print(txt)
open(OUT, "w").write(txt)
print("\nwritten -> {}".format(OUT))
