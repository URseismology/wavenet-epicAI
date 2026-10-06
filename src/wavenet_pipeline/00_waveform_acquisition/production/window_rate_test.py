#!/usr/bin/env python
"""
R-14: find a download strategy that is BOTH complete and unthrottled.

Two measurements bracket the problem and neither alone is actionable:
  * a 1-day window delivers 3 of 3 files -- complete in isolation (2026-10-02);
  * ~1,300 daily requests per station draw HTTP 429/503 and deliver 20 of 1,344 days
    (ZT.WTBG, 2026-10-06), so the R-1 validation came in at 0.70x and failed its threshold.

The first is about WINDOW SIZE, the second about REQUEST RATE. This separates them.

A doubt worth testing rather than assuming: the earlier window sweep gave 1 day 100%, 1 week
14%, 1 month 26%, 3 months 22%, 6 months 11%, 1 year 43%. That is NOT monotonic. If multi-day
windows were merely throttled, longer windows would degrade smoothly. The erratic shape
suggests they may be INTRINSICALLY lossy, in which case no safe intermediate window exists and
the only viable strategy is paced daily requests. One station on one occasion is not enough to
conclude that, which is exactly what stage 1 settles.

    STAGE 0  ground truth   -- one station, one window size, FULLY SERIAL, heavily paced.
                               Establishes the true obtainable day count. Without a validated
                               denominator every later percentage is uninterpretable, which is
                               the error that produced a false "campaign failed" verdict on
                               2026-10-02.
    STAGE 1  window sweep   -- concurrency 1, windows 1/3/7/14/30 days. Isolates window size
                               from rate entirely.
    STAGE 2  rate sweep     -- best complete window, concurrency 1/8/32. Isolates rate.

Everything runs in an isolated root. Nothing is written to production.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

import pandas as pd

PROD = "/scratch/tolugboj_lab/wavenet_ncf_production_v2"
CODE = "/scratch/tolugboj_lab/wavenet_ncf/code/CURRENT/production"
ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_window_rate"
DISC = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"

# Chosen for a SHORT, FULLY-BOUNDED span so ground truth is cheap and a whole station fits
# inside a debug allocation. Short-lived temporary deployments, all previously truncated.
STATIONS = ["ZT.WTBG", "XH.RS08", "YC.SBA8"]
WINDOWS = [1, 3, 7, 14, 30]


def build():
    man = pd.read_csv(os.path.join(PROD, "manifest", "fps_stations.csv"))
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    sel = man[man["key"].isin(STATIONS)]
    if len(sel) != len(STATIONS):
        sys.exit("manifest matched {} of {} stations".format(len(sel), len(STATIONS)))

    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    tmp = os.path.join(os.path.dirname(ROOT), ".window_manifest.csv")
    sel.drop(columns=["key"]).to_csv(tmp, index=False)
    r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "init",
                        "--root", ROOT, "--manifest", tmp, "--discovery-manifest", DISC],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("init failed: " + r.stderr[-400:])

    env = dict(os.environ, WAVENET_DISCOVERY_MANIFEST=DISC)
    for i in range(len(sel)):
        subprocess.run([sys.executable, os.path.join(CODE, "fetch_station_metadata.py"),
                        "--root", ROOT, "--idx", str(i), "--force"], env=env, capture_output=True)

    d = pd.read_csv(DISC).set_index("key")
    print("root: {}".format(ROOT))
    print("")
    print("{:<10} {:>10} {:>10} {:>12} {:>10}".format(
        "STATION", "year_min", "year_max", "span_days", "prod_days"))
    for k in STATIONS:
        try:
            y0, y1 = int(d.loc[k, "year_min"]), int(d.loc[k, "year_max"])
            span = (y1 - y0 + 1) * 365
        except Exception:
            span = 0
        p = os.path.join(PROD, "packaged_h5", k + ".h5.daystate.json")
        pd_days = len(json.load(open(p))) if os.path.exists(p) else 0
        print("{:<10} {:>10} {:>10} {:>12,} {:>10}".format(k, y0, y1, span, pd_days))

    open(os.path.join(ROOT, "TEST_PLAN.txt"), "w").write("""R-14 WINDOW/RATE TEST -- decision rule, fixed BEFORE any result existed
======================================================================
question : is there a download strategy that is BOTH complete and unthrottled?

STAGE 0  ground truth: 1 station, 1-day windows, FULLY SERIAL, paced. Gives the true
         obtainable day count -- the denominator every later number is measured against.
STAGE 1  window sweep at concurrency 1: windows {w}. Isolates WINDOW from RATE.
STAGE 2  rate sweep at the best complete window: concurrency 1, 8, 32.

MEASURED per cell: delivered_days / ground_truth, HTTP 429 count, HTTP 503 count, wall-clock.

DECISION (committed; not renegotiable after seeing the numbers):
  delivered fraction FALLS with window size even at concurrency 1
      -> multi-day windows are INTRINSICALLY lossy. No safe intermediate window exists.
         Strategy B is dead; use paced daily requests.
  delivered fraction FLAT across windows at concurrency 1, but falls as concurrency rises
      -> the loss is purely RATE. Use the fastest complete window plus a concurrency cap
         and/or retry-on-429.
  both
      -> use the largest window that stays complete at concurrency 1, AND cap concurrency.

VOID IF
  ground truth cannot be established, or any stage shows zero 429/503 AND zero shortfall --
  that would mean the test never reproduced the condition it exists to measure.

NOTE: the request budget is GLOBAL across concurrent tasks, not per task. 96 tasks at 1 req/s
is 96 req/s at the provider. Concurrency is therefore a first-class factor here, not a
scheduling detail.
""".format(w=WINDOWS))
    print("")
    print("decision rule -> {}/TEST_PLAN.txt".format(ROOT))


if __name__ == "__main__":
    build()
