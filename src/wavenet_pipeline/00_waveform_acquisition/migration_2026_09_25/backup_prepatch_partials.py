#!/usr/bin/env python
"""Snapshot the PRE-PATCH partial shards before they are wiped and reprocessed under the
patched pipeline (PI, 2026-09-25: "wipe redo but back-up when possible for further
comparison tests later").

A "partial" is a station that has a day-state checkpoint but no result JSON with
package_ok -- i.e. it was mid-flight when the orchestrator array was cancelled. These are
exactly the stations that CANNOT be fixed by resuming under patched code (the per-channel
grid is anchored at the first stored sample, so patched days appended to a pre-patch
anchor are still misplaced -- see docs/.../xd_mtan_rung_smoke_test/REPORT.md 4.1), so they
get wiped and redone. Backing them up first gives a controlled pre/post pair on real
production stations: same raw input, two pipelines.

Copies per station: the shard .h5, its .daystate.json, and .qc.json if present.
Writes manifest.json (station list, sizes, day counts) next to the copies.

Usage: backup_prepatch_partials.py <prod_root> <backup_root>
"""
import glob
import json
import os
import subprocess
import sys
import time

prod_root, backup_root = sys.argv[1], sys.argv[2]
pkg = os.path.join(prod_root, "packaged_h5")
os.makedirs(backup_root, exist_ok=True)

done_ok = set()
for f in glob.glob(os.path.join(prod_root, "results", "*.json")):
    try:
        r = json.load(open(f))
    except Exception:
        continue
    if r.get("package_ok"):
        done_ok.add((r["network"], r["station"]))

entries, rel_files = [], []
for ds in sorted(glob.glob(os.path.join(pkg, "*.daystate.json"))):
    base = os.path.basename(ds)[: -len(".h5.daystate.json")]
    net, sta = base.split(".", 1)
    if (net, sta) in done_ok:
        continue
    h5 = os.path.join(pkg, f"{net}.{sta}.h5")
    if not os.path.exists(h5):
        continue
    try:
        n_days = len(json.load(open(ds)))
    except Exception:
        n_days = -1
    e = dict(network=net, station=sta, days_checkpointed=n_days,
             shard_bytes=os.path.getsize(h5))
    for suffix in (".h5", ".h5.daystate.json", ".h5.qc.json"):
        p = os.path.join(pkg, f"{net}.{sta}{suffix}")
        if os.path.exists(p):
            rel_files.append(os.path.basename(p))
    entries.append(e)

total = sum(e["shard_bytes"] for e in entries)
print(f"[backup] {len(entries)} pre-patch partial stations, {total/1e9:.1f} GB, "
      f"{len(rel_files)} files", flush=True)

list_path = os.path.join(backup_root, "_files.txt")
with open(list_path, "w") as f:
    f.write("\n".join(rel_files) + "\n")

t0 = time.time()
subprocess.run(["rsync", "-a", "--partial", f"--files-from={list_path}", pkg + "/",
                backup_root + "/"], check=True)
elapsed = time.time() - t0

missing, size_mismatch = [], []
for e in entries:
    src = os.path.join(pkg, f"{e['network']}.{e['station']}.h5")
    dst = os.path.join(backup_root, f"{e['network']}.{e['station']}.h5")
    if not os.path.exists(dst):
        missing.append(f"{e['network']}.{e['station']}")
    elif os.path.getsize(dst) != os.path.getsize(src):
        size_mismatch.append(f"{e['network']}.{e['station']}")

manifest = dict(created=time.strftime("%Y-%m-%dT%H:%M:%S"), prod_root=prod_root,
                purpose="pre-patch snapshot of mid-flight shards, for pre/post comparison",
                n_stations=len(entries), total_shard_bytes=total,
                copy_elapsed_s=round(elapsed, 1), missing=missing,
                size_mismatch=size_mismatch, stations=entries)
with open(os.path.join(backup_root, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print(f"[backup] copied in {elapsed/60:.1f} min; missing={len(missing)} "
      f"size_mismatch={len(size_mismatch)}", flush=True)
print("[backup] VERIFY " + ("OK" if not missing and not size_mismatch else "FAILED"), flush=True)
