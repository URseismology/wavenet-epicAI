#!/usr/bin/env python
"""
RECOVERY TEST -- do the 557 zero-shard stations recover data once provider pinning is
actually active, and at what rate?

Runs in an ISOLATED root under wavenet_ncf/run/. Nothing is written into the production
root. The cost is re-downloading ~40 mostly short-history stations if the test says "rerun";
the benefit is that no test artifact can ever be mistaken for campaign output, which is the
ambiguity that caused every problem this week.

Launch-once-and-leave: picks the sample, guarantees Stage 1.5, verifies the code/pinning
binding, and writes the committed decision rule to disk BEFORE any result exists.

Each design choice fixes a specific defect in the canary this replaces (2026-10-05):
  * sample is RANDOM, fixed seed, written to disk before launch. The previous one was
    hand-picked for provider spread and accidentally drew only 1-year temporary deployments,
    the hardest class in the set.
  * submitted through master.py, which sets per-partition walltimes. The previous canary was
    submitted with a bare sbatch, inherited the script's `#SBATCH -t 00:30:00`, and SLURM
    killed OK.RLO2 at 678 files mid-download -- which I then recorded as a failure.
  * urseismo + standard only (9-day / 4-day caps). No preempt, no interactive (11 h).
  * Stage 1.5 runs first, or packaging refuses and we measure the wrong failure.
  * the binding is verified from the generated slurm and the run ABORTS if it fails.
  * throughput probes are tracked separately so a slow station cannot depress the rate.
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
ROOT = "/scratch/tolugboj_lab/wavenet_ncf/run/TEST_recovery_20261005"
DISC = "/scratch/tolugboj_lab/delivered_network_analysis/discovery_ALL_1999.csv"
SEED, N_SAMPLE, N_THROUGHPUT = 20261005, 40, 3

# ------------------------------------------------- 1. population, from the DATA
man = pd.read_csv(os.path.join(PROD, "manifest", "fps_stations.csv"))
man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
have = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(PROD, "packaged_h5", "*.h5"))}
missing = man[~man["key"].isin(have)]
disc = pd.read_csv(DISC)
routed = set(disc[disc["status"] == "ROUTED"]["key"])
elig = missing[missing["key"].isin(routed)]
print("manifest rows          : {:,}".format(len(man)))
print("no shard (population)  : {:,}".format(len(missing)))
print("of those, ROUTED       : {:,}  <- eligible; unrouted cannot be pinned".format(len(elig)))

# ------------------------------------------------- 2. sample
rng = random.Random(SEED)
idx_all = sorted(elig.index.tolist())
sample = sorted(rng.sample(idx_all, min(N_SAMPLE, len(idx_all))))

# Throughput probes: longest-history eligible stations. Daily windows make request count
# scale with history length, so we need the cost before committing to 557 -- but a slow
# station is not a failed one, so these never enter the recovery rate.
d = disc.set_index("key")
rest = [i for i in idx_all if i not in set(sample)]
def yrs(i):
    try:
        return int(d.loc[man.loc[i, "key"], "year_max"]) - int(d.loc[man.loc[i, "key"], "year_min"])
    except Exception:
        return 0
throughput = sorted(sorted(rest, key=lambda i: -yrs(i))[:N_THROUGHPUT])

rows = [(i, man.loc[i, "key"], "recovery") for i in sample] + \
       [(i, man.loc[i, "key"], "throughput") for i in throughput]

# ------------------------------------------------- 3. isolated root
shutil.rmtree(ROOT, ignore_errors=True)
sub = man.loc[[i for i, _, _ in rows]].drop(columns=["key"])
os.makedirs("/scratch/tolugboj_lab/wavenet_ncf/run", exist_ok=True)
tmp_manifest = "/scratch/tolugboj_lab/wavenet_ncf/run/.test_manifest.csv"
sub.to_csv(tmp_manifest, index=False)
r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "init",
                    "--root", ROOT, "--manifest", tmp_manifest,
                    "--discovery-manifest", DISC], capture_output=True, text=True)
print("\ninit: {}".format("ok" if r.returncode == 0 else r.stderr.strip()[:300]))
if r.returncode != 0:
    sys.exit(1)

# the test root's own manifest order is what idx means from here on
tman = pd.read_csv(os.path.join(ROOT, "manifest", "fps_stations.csv"))
tman["key"] = tman["network"].astype(str) + "." + tman["station"].astype(str)
role = {k: rl for _, k, rl in rows}
tman["role"] = tman["key"].map(role)
tman[["network", "station", "key", "role"]].to_csv(os.path.join(ROOT, "TEST_STATIONS.csv"))
print("sample: {} recovery + {} throughput -> {}/TEST_STATIONS.csv".format(
    len(sample), len(throughput), ROOT))

# ------------------------------------------------- 4. Stage 1.5
env = dict(os.environ, WAVENET_DISCOVERY_MANIFEST=DISC)
for i in range(len(tman)):
    subprocess.run([sys.executable, os.path.join(CODE, "fetch_station_metadata.py"),
                    "--root", ROOT, "--idx", str(i)], env=env, capture_output=True)
n_meta = len(glob.glob(os.path.join(ROOT, "station_metadata", "*")))
print("Stage 1.5: {} metadata files for {} stations".format(n_meta, len(tman)))

# ------------------------------------------------- 5. verify the binding, or abort
slurm = open(os.path.join(ROOT, "orchestrator.slurm")).read()
code_line = next((l.strip() for l in slurm.splitlines() if "orchestrator.py $SLURM" in l), "")
pin_line = next((l.strip() for l in slurm.splitlines()
                 if l.startswith("export WAVENET_DISCOVERY_MANIFEST=")), "")
src = open(os.path.join(CODE, "orchestrator.py")).read()
has_fixes = "WINDOW_DAYS" in src and "def discovery_lookup" in src
print("\nBINDING CHECK -- the check that was missing when the campaign ran the wrong tree")
print("  code    : {}".format(code_line or "MISSING"))
print("  pinning : {}".format(pin_line or "MISSING"))
print("  tree has daily-window + pinning: {}".format(has_fixes))
if not (code_line and pin_line and "CURRENT" in code_line and has_fixes):
    sys.exit("ABORT: binding check failed -- nothing submitted")

# ------------------------------------------------- 6. decision rule, written FIRST
open(os.path.join(ROOT, "TEST_PLAN.txt"), "w").write("""RECOVERY TEST -- decision rule, fixed BEFORE any result existed
==============================================================
question   : of the {npop} stations that produced no shard, what fraction recover data once
             provider pinning is actually active?
population : {npop} with no shard, of which {nelig} are ROUTED (eligible)
sample     : {ns} drawn at random, seed {seed}; see TEST_STATIONS.csv
measure    : fraction packaging >=1 day, read from the HDF5 FILES, not from result JSONs
void if    : any log lacks a '[discovery] pinned' line. Then the test proves nothing and no
             conclusion may be drawn from it.

DECISION (committed; not to be renegotiated after seeing the number):
  >= 25%  -> rerun all {npop} in production
  <  10%  -> do NOT rerun. The set is genuinely unavailable; record as an issue and stop.
  10-25%  -> rerun only the recovering subclass, defined by whatever separates it

The {nt} throughput probes measure COST ONLY and are excluded from the rate: daily windows
make request count scale with history length, and a slow station is not a failed station.
""".format(npop=len(missing), nelig=len(elig), ns=len(sample), seed=SEED, nt=len(throughput)))
print("decision rule -> {}/TEST_PLAN.txt".format(ROOT))

# ------------------------------------------------- 7. submit via master.py
r = subprocess.run([sys.executable, os.path.join(CODE, "master.py"), "submit",
                    "--root", ROOT, "--array-limit", "43",
                    "--orchestrator-partitions", "urseismo,standard"],
                   capture_output=True, text=True)
print("\nSUBMIT")
for l in (r.stdout or "").splitlines():
    if "chunk" in l or "submitted" in l or "Stage 1.5" in l:
        print("  " + l.strip())
if r.returncode != 0:
    print("  FAILED: " + (r.stderr or "").strip()[:400])
    sys.exit(1)
json.dump({"root": ROOT, "seed": SEED}, open(os.path.join(ROOT, "TEST_JOBS.json"), "w"))
print("\nroot: {}".format(ROOT))
