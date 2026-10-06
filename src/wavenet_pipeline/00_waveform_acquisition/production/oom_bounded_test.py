#!/usr/bin/env python
"""
R-11 BOUNDED TEST -- where does the memory go, and how fast?

182 campaign tasks were OOM-killed at MaxRSS 6.8-7.2 GB against a 7 GB limit. Verified: it
happens during PACKAGING (91 of 92 checkable tasks wrote a day-state checkpoint during their
own run) and scales with HISTORY LENGTH (0% OOM below 500 target days, 16% at 1,500-4,000),
NOT with per-day cost (OOM is higher for low-rate stations than 100 Hz ones). The cause is
not established; three hypotheses were offered and all three disproved.

DESIGN

* PACKAGING ONLY. `WAVENET_SKIP_DOWNLOAD=1` against stations whose raw SEED is already on
  disk, so the whole walltime goes into the loop where the kill was verified to occur, and no
  network time is wasted. Raw directories are symlinked READ-ONLY from production; with
  downloads off nothing writes to them.

* DO NOT REPRODUCE THE KILL -- MEASURE THE SLOPE. The kill takes 1.5-2.3 h and debug caps at
  1 h. If RSS grows with days packaged, 45 minutes gives plenty of points to fit MB-per-day
  and extrapolate to the 7 GB ceiling. That answers "how fast" and "how far can a station
  get" without needing the crash.

* SAMPLE RSS EXTERNALLY. A shell loop reads /proc/<pid>/status every 10 s alongside the
  day-state count. RSS (Resident Set Size) is the physical RAM the process actually holds --
  what SLURM reports as MaxRSS and what the OOM killer acts on. Sampling externally means NO
  instrumentation is added to orchestrator.py, so production code is untouched and a later
  measurement is not confounded. That matters: speculative mid-campaign patches already cost
  us one clean baseline (see R-11).

* ISOLATED ROOT, per the testing policy. Nothing is written into production.

This test also doubles as the STEP 4 feasibility check, because STEP 4 (re-package 432
stations from retained raw SEED to fix mixed units) is exactly packaging-only work.
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
ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_oom_20261005"
DISC = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"
# Stations chosen for the most retained raw SEED and the most days already packaged -- if
# memory scales with days, these reach a conclusive slope fastest.
PICK = ["G.CRZF", "G.PAF", "II.ALE"]

man = pd.read_csv(os.path.join(PROD, "manifest", "fps_stations.csv"))
man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
sel = man[man["key"].isin(PICK)]
if len(sel) != len(PICK):
    sys.exit("expected {} stations, manifest matched {}".format(len(PICK), len(sel)))

shutil.rmtree(ROOT, ignore_errors=True)
tmp = "/scratch/tolugboj_lab/wavenet_ncf/run/.oom_manifest.csv"
os.makedirs(os.path.dirname(tmp), exist_ok=True)
sel.drop(columns=["key"]).to_csv(tmp, index=False)
r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "init",
                    "--root", ROOT, "--manifest", tmp, "--discovery-manifest", DISC],
                   capture_output=True, text=True)
if r.returncode != 0:
    sys.exit("init failed: " + r.stderr[-400:])
print("isolated root: {}".format(ROOT))

# Symlink the retained raw SEED. Read-only in effect: WAVENET_SKIP_DOWNLOAD=1 means
# MassDownloader never runs, so nothing writes into these directories.
os.makedirs(os.path.join(ROOT, "scratch_work"), exist_ok=True)
for k in PICK:
    src = os.path.join(PROD, "scratch_work", k.replace(".", "_"))
    dst = os.path.join(ROOT, "scratch_work", k.replace(".", "_"))
    if os.path.isdir(src) and not os.path.exists(dst):
        os.symlink(src, dst)
    n = len(os.listdir(os.path.join(src, "mseed"))) if os.path.isdir(os.path.join(src, "mseed")) else 0
    print("  {:<10} raw files available: {:,}".format(k, n))

env = dict(os.environ, WAVENET_DISCOVERY_MANIFEST=DISC)
for i in range(len(sel)):
    subprocess.run([sys.executable, os.path.join(CODE, "fetch_station_metadata.py"),
                    "--root", ROOT, "--idx", str(i), "--force"], env=env, capture_output=True)
print("Stage 1.5: {} StationXML".format(len(glob.glob(os.path.join(ROOT, "station_metadata", "*.xml")))))

open(os.path.join(ROOT, "TEST_PLAN.txt"), "w").write("""R-11 BOUNDED TEST -- decision rule, fixed BEFORE any result existed
===================================================================
question : during PACKAGING, does resident memory (RSS) grow with the number of days
           packaged, and at what rate?

method   : packaging only (WAVENET_SKIP_DOWNLOAD=1) on 3 stations with large retained raw
           SEED, RSS sampled externally every 10 s from /proc/<pid>/status alongside the
           day-state count. No instrumentation added to orchestrator.py.

DECISION (committed; not to be renegotiated after seeing the data):
  RSS rises monotonically with days packaged
      -> LEAK CONFIRMED IN PACKAGING. The slope gives the safe day-ceiling per memory
         setting, which sets both the R-1 debug/BH3 split and STEP 4 batch sizes.
  RSS flat, sawtoothing, or plateauing
      -> NOT a packaging leak. The verified "dies in packaging" finding must be re-examined;
         the download phase's residue returns as a candidate.
  RSS jumps at a specific day index
      -> that operation is the cause; identify it from the day where the jump occurs.

VOID IF
  fewer than ~300 days are packaged -- too few points to fit a slope.

ALSO PRODUCES
  * MB per 100 days -> extrapolated day-ceiling at 7 GB, and what a higher --mem-per-cpu buys
  * STEP 4 feasibility: this IS packaging-only work, so the result applies directly to
    re-packaging the 432 mixed-units/raw-counts stations.
""")
print("decision rule -> {}/TEST_PLAN.txt".format(ROOT))
print("")
print("next: submit oom_test.slurm on debug")
