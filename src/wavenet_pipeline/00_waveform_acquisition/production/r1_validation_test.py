#!/usr/bin/env python
"""
R-1 VALIDATION TEST -- does re-downloading a truncated station with daily windows actually
recover the missing days, and what does it cost?

R-1 is the largest lever in the repair plan: 452 stations holding under 25% of their target,
1,530,178 recoverable days, which would take clean coverage from 545,905 to 2,076,083 days
(3.8x). That projection assumes the missing days come back. This measures whether they do,
BEFORE committing 452 full-history re-downloads.

Follows the testing policy (PI, 2026-10-05):
  * runs in an ISOLATED root under wavenet_ncf/run/ -- never inside production;
  * saves into production only after success, then is archived;
  * production changes only if the test FAILS;
  * the decision rule is written to disk before any result exists.

Design choices, each fixing a defect from an earlier canary:
  * RANDOM sample, fixed seed, written to disk before launch -- not hand-picked.
  * submitted through master.py, which sets per-partition walltimes. A bare sbatch once
    inherited `#SBATCH -t 00:30:00` and SLURM killed the only station that was working.
  * cost probes are separate from the recovery metric; a slow station is not a failed one.
  * the binding (code tree + pinning) is verified and the run ABORTS if it fails.
  * Stage 1.5 runs inside the test root with the FIXED fetcher, so this also exercises the
    R-3 fix rather than assuming it.
"""
import glob
import json
import os
import random
import shutil
import subprocess
import sys

import pandas as pd

PROD = "/scratch/tolugboj_lab/wavenet_ncf_production_v2"
CODE = "/scratch/tolugboj_lab/wavenet_ncf/code/CURRENT/production"
ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_r1_20261005"
DISC = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"
REPORTS = "/scratch/tolugboj_lab/wavenet_ncf/reports"
SEED, N_SAMPLE, N_PROBE = 20261005, 20, 3
# --dry-run: show the population and the sample WITHOUT creating a root or submitting, so the
# selection can be inspected before anything runs. The launch itself must wait for the Stage
# 1.5 refetch: re-downloading against stale responses would land these stations straight back
# in the R-3 repair queue, paying the expensive step twice.
DRY = "--dry-run" in sys.argv
# Primary sample is capped so the answer arrives in hours, not weeks. The cap is STATED, not
# hidden: stations above it are sampled separately as cost probes, so the stratification is
# visible rather than a silent bias toward easy stations.
PRIMARY_TARGET_CAP = 2000

# ---------------------------------------------------------- 1. population, from the report
csvs = sorted(glob.glob(os.path.join(REPORTS, "*_stations_*.csv")))
if not csvs:
    sys.exit("no station report found -- run campaign_report.py first")
st = pd.read_csv(csvs[-1])
print("population source: {}".format(os.path.basename(csvs[-1])))

trunc = st[(st["clean_days"] > 0) & (st["expected"] > 0)
           & (st["clean_days"] < 0.25 * st["expected"])]
print("R-1 population (has a shard, <25% of its own target): {:,} stations".format(len(trunc)))
print("  recoverable days in that population: {:,}".format(
    int((trunc["expected"] - trunc["clean_days"]).sum())))

small = trunc[trunc["expected"] <= PRIMARY_TARGET_CAP]
large = trunc[trunc["expected"] > PRIMARY_TARGET_CAP]
print("  of those, target <= {:,} days: {:,}  (primary sample drawn here)".format(
    PRIMARY_TARGET_CAP, len(small)))
print("  target >  {:,} days: {:,}  (cost probes drawn here)".format(PRIMARY_TARGET_CAP, len(large)))

base_pre = st.set_index("key")
rng = random.Random(SEED)
primary = sorted(rng.sample(list(small["key"]), min(N_SAMPLE, len(small))))
probes = sorted(large.nlargest(N_PROBE, "expected")["key"].tolist()) if len(large) else []

if DRY:
    print("")
    print("DRY RUN -- nothing created, nothing submitted")
    print("  primary sample ({}): {}".format(len(primary), ", ".join(primary)))
    print("  cost probes   ({}): {}".format(len(probes), ", ".join(probes)))
    tot = int((trunc["expected"] - trunc["clean_days"]).sum())
    print("")
    print("  sampled stations hold {:,} of {:,} expected days ({:.1f}%)".format(
        int(base_pre.loc[primary, "clean_days"].sum()),
        int(base_pre.loc[primary, "expected"].sum()),
        100.0 * base_pre.loc[primary, "clean_days"].sum() / base_pre.loc[primary, "expected"].sum()))
    print("  full R-1 population would recover {:,} days".format(tot))
    sys.exit(0)

# ---------------------------------------------------------- 2. isolated root
man = pd.read_csv(os.path.join(PROD, "manifest", "fps_stations.csv"))
man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
sel = man[man["key"].isin(primary + probes)]
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs("/scratch/tolugboj_lab/wavenet_ncf/run", exist_ok=True)
tmp = "/scratch/tolugboj_lab/wavenet_ncf/run/.r1_manifest.csv"
sel.drop(columns=["key"]).to_csv(tmp, index=False)
r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "init",
                    "--root", ROOT, "--manifest", tmp, "--discovery-manifest", DISC],
                   capture_output=True, text=True)
if r.returncode != 0:
    sys.exit("init failed: " + r.stderr[-400:])
print("\nisolated root: {}".format(ROOT))

base = st.set_index("key")
rows = [dict(key=k, role="recovery" if k in primary else "cost_probe",
             expected=int(base.loc[k, "expected"]),
             prod_clean_days=int(base.loc[k, "clean_days"]))
        for k in primary + probes]
pd.DataFrame(rows).to_csv(os.path.join(ROOT, "TEST_STATIONS.csv"), index=False)
print("sample: {} recovery + {} cost probes -> TEST_STATIONS.csv".format(len(primary), len(probes)))

# ---------------------------------------------------------- 3. Stage 1.5, fixed fetcher
env = dict(os.environ, WAVENET_DISCOVERY_MANIFEST=DISC)
tman = pd.read_csv(os.path.join(ROOT, "manifest", "fps_stations.csv"))
for i in range(len(tman)):
    subprocess.run([sys.executable, os.path.join(CODE, "fetch_station_metadata.py"),
                    "--root", ROOT, "--idx", str(i)], env=env, capture_output=True)
nxml = len(glob.glob(os.path.join(ROOT, "station_metadata", "*.xml")))
full = 0
for p in glob.glob(os.path.join(ROOT, "station_metadata", "*.json")):
    try:
        full += 1 if json.load(open(p)).get("coverage_complete") else 0
    except Exception:
        pass
print("Stage 1.5: {} StationXML for {} stations, {} with COMPLETE channel coverage".format(
    nxml, len(tman), full))

# ---------------------------------------------------------- 4. binding check, or abort
slurm = open(os.path.join(ROOT, "orchestrator.slurm")).read()
code_line = next((l.strip() for l in slurm.splitlines() if "orchestrator.py $SLURM" in l), "")
pin_line = next((l.strip() for l in slurm.splitlines()
                 if l.startswith("export WAVENET_DISCOVERY_MANIFEST=")), "")
src = open(os.path.join(CODE, "orchestrator.py")).read()
ok = (code_line and "CURRENT" in code_line and pin_line
      and "WINDOW_DAYS" in src and "def discovery_lookup" in src)
print("\nBINDING CHECK")
print("  code    : {}".format(code_line or "MISSING"))
print("  pinning : {}".format(pin_line or "MISSING"))
if not ok:
    sys.exit("ABORT: binding check failed -- nothing submitted")

# ---------------------------------------------------------- 5. decision rule, written FIRST
open(os.path.join(ROOT, "TEST_PLAN.txt"), "w").write("""R-1 VALIDATION -- decision rule, fixed BEFORE any result existed
================================================================
question : does re-downloading a truncated station with DAILY windows recover the days that
           year-wide windows silently dropped, and what does it cost per station?

population : {npop} stations holding <25% of their own target ({ndays:,} recoverable days)
sample     : {ns} drawn at random (seed {seed}) from those with target <= {cap:,} days, so the
             answer arrives in hours. The cap is stated, not hidden; larger stations are
             sampled separately as cost probes and excluded from the recovery metric.

PRIMARY METRIC
    improvement = test_clean_days / production_clean_days, per station, median over the sample

DECISION (committed; not to be renegotiated after seeing the number):
    >= 2.0      -> run the full R-1 repair on all {npop} stations
    <  1.2      -> do NOT. Truncation is not the limiting factor; the missing days are not
                   obtainable and the 1,530,178-day projection is wrong. Re-investigate.
    1.2 - 2.0   -> run it, but revise the projected gain DOWN by the measured ratio before
                   quoting it anywhere.

SECONDARY (cost, not pass/fail)
    median wall-clock per station -> ETA for the full 452, and whether BH3 is required or
    legacy BlueHive can carry it.

VOID IF
    any log lacks a '[discovery] pinned' line. Pinning absent means the test measured the old
    failure, not the fix, and no conclusion may be drawn.

ALSO RECORDED
    test_clean_days / expected -- absolute completeness, which says whether the key-index
    target is itself realistic or optimistic.
""".format(npop=len(trunc), ndays=int((trunc["expected"] - trunc["clean_days"]).sum()),
           ns=len(primary), seed=SEED, cap=PRIMARY_TARGET_CAP))

# ---------------------------------------------------------- 6. submit via master.py
r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "submit",
                    "--root", ROOT, "--array-limit", str(len(tman)),
                    "--orchestrator-partitions", "urseismo,standard"],
                   capture_output=True, text=True)
print("\nSUBMIT")
for l in (r.stdout or "").splitlines():
    if any(w in l for w in ("chunk", "submitted", "Stage 1.5")):
        print("  " + l.strip())
if r.returncode != 0:
    print("  FAILED: " + (r.stderr or "")[-400:])
    sys.exit(1)
print("\nroot: {}".format(ROOT))
