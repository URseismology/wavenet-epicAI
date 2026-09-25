#!/usr/bin/env python
"""Select the completed-but-incomplete stations for a full patched redo, and move their
pre-patch artefacts aside (PI 2026-09-25: "restart with new fixes to aim for completeness").

Why these stations must be REDONE rather than topped up: their shards were written by
pre-patch code, and a channel's grid is anchored at its first stored sample, so appending
newly-downloaded days to an existing pre-patch shard produces a within-station timing
discontinuity. Wiping the shard means they rebuild wholly at patch_level 2.

Selection: a completed station is redone if the orchestrator recorded a year_errors entry
(a KNOWN failed download) or if it obtained under half the days the key index says exist.
Stations that hit no error and are complete are left alone -- they are timing-shifted only,
and their exact offsets are already banked.

Artefacts are MOVED, not copied: within one GPFS filesystem a rename is a metadata
operation, so preserving them for later pre/post comparison costs nothing and stays fully
reversible.

Usage: select_redo.py <prod_root> <aside_dir> [--apply]
"""
import glob
import json
import os
import shutil
import sys

import pandas as pd

root, aside = sys.argv[1], sys.argv[2]
apply_changes = "--apply" in sys.argv

summary = pd.read_csv("/scratch/tolugboj_lab/wavenet_ncf_framework/metadata3/"
                      "key_index_summary/station_summary.csv")[["network", "station", "total_days"]]
expected = {(r.network, r.station): r.total_days for r in summary.itertuples()}

redo, keep = [], []
for rf in sorted(glob.glob(os.path.join(root, "results", "*.json"))):
    try:
        r = json.load(open(rf))
    except (ValueError, OSError):
        continue
    if not r.get("package_ok"):
        continue
    key = f"{r['network']}.{r['station']}"
    exp = expected.get((r["network"], r["station"])) or 0
    got = r.get("n_days_processed") or 0
    ratio = (got / exp) if exp > 0 else None
    why = []
    if r.get("year_errors"):
        why.append(f"{len(r['year_errors'])} year_error(s)")
    if ratio is not None and ratio < 0.5:
        why.append(f"only {ratio:.0%} of expected days")
    (redo if why else keep).append(dict(key=key, rf=rf, got=got, exp=exp, why="; ".join(why)))

print(f"completed stations: {len(redo) + len(keep)}")
print(f"  REDO (incomplete / errored): {len(redo)}")
print(f"  KEEP (complete, pre-patch, offsets banked): {len(keep)}")
tot_days = sum(x["got"] for x in redo)
exp_days = sum(x["exp"] for x in redo)
print(f"  redo set currently holds {tot_days:,} days of ~{exp_days:,} expected "
      f"({100*tot_days/max(exp_days,1):.1f}%) -- this is the data being recovered")
for x in redo[:5]:
    print(f"     e.g. {x['key']}: {x['got']}/{x['exp']} days ({x['why']})")

if not apply_changes:
    print("\n(dry run -- pass --apply to move artefacts aside)")
    sys.exit(0)

os.makedirs(aside, exist_ok=True)
pkg = os.path.join(root, "packaged_h5")
moved = 0
for x in redo:
    for suffix in (".h5", ".h5.daystate.json", ".h5.qc.json"):
        p = os.path.join(pkg, x["key"] + suffix)
        if os.path.exists(p):
            shutil.move(p, os.path.join(aside, os.path.basename(p)))
    # The result JSON is what the orchestrator's idempotency check reads, so moving it is
    # what actually re-enables the station.
    shutil.move(x["rf"], os.path.join(aside, os.path.basename(x["rf"])))
    moved += 1
with open(os.path.join(aside, "_redo_manifest.json"), "w") as f:
    json.dump(dict(n=len(redo), stations=redo), f, indent=2)
print(f"\nmoved aside: {moved} station(s) -> {aside}")
print("raw SEED left in place: re-download RESUMES from it rather than starting over")
