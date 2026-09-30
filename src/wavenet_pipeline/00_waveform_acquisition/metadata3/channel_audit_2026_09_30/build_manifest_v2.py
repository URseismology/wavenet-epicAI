#!/usr/bin/env python3
"""Build fps_stations_v2.csv: apply the 108 accepted replacements, drop the Mars entry.

The 2,000-station farthest-point-sampled network is a LOCKED decision (CLAUDE.md), so this
writes a NEW file and leaves the original untouched. Adopting it is a separate, deliberate
step.

Changes, all from the 2026-09-30 channel audit of the BlueHive3 campaign:
  * 108 stations that carry no seismometer at all (only magnetometer / electric potential /
    pressure / accelerometer / mass-position channels) are swapped for the nearest station
    within 0.5 deg that has a high-gain seismometer at >= 1 Hz AND confirmed waveform data.
  * XB.ELYH0 is REMOVED, not replaced: it is the InSight lander on Mars (4.502N 135.623E).
  * 99 stations keep their original entry -- no acceptable replacement was found. They are
    listed in the report so the gap is explicit rather than silently carried.

`rank` is preserved so the farthest-point ordering is unchanged; `days` is cleared for
swapped rows because the replacement's true day count is not yet measured and carrying the
original's number forward would be a fabricated value.
"""
import csv, os, shutil

REPO = "/Users/urseismoadmin/wavenet-epicAI"
SRC = os.path.join(REPO, "src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations.csv")
DST = os.path.join(REPO, "src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations_v2.csv")
ACC = "/private/tmp/claude-501/-Users-urseismoadmin-wavenet-epicAI/c246bfdc-9fe9-4e47-a45b-eb301c9d8216/scratchpad/replacements_accepted.csv"
REPORT = "/private/tmp/claude-501/-Users-urseismoadmin-wavenet-epicAI/c246bfdc-9fe9-4e47-a45b-eb301c9d8216/scratchpad/manifest_v2_changes.csv"
MARS = ("XB", "ELYH0")

repl = {}
for r in csv.DictReader(open(ACC)):
    repl[(r["orig_network"], r["orig_station"])] = r

rows = list(csv.DictReader(open(SRC)))
fields = list(rows[0].keys())
out, changes = [], []
n_sub = n_del = 0

for row in rows:
    key = (row["network"], row["station"])
    if key == MARS:
        n_del += 1
        changes.append(dict(action="DELETED", rank=row["rank"],
                            old=f"{key[0]}.{key[1]}", new="", band="", dist_deg="",
                            note="InSight lander on Mars"))
        continue
    r = repl.get(key)
    if r:
        n_sub += 1
        new = dict(row)
        new["network"] = r["new_network"]
        new["station"] = r["new_station"]
        new["lat"] = r["new_lat"]
        new["lon"] = r["new_lon"]
        if "days" in new:
            new["days"] = ""          # unmeasured for the replacement; do not invent one
        out.append(new)
        changes.append(dict(action="REPLACED", rank=row["rank"],
                            old=f"{key[0]}.{key[1]}",
                            new=f"{r['new_network']}.{r['new_station']}",
                            band=r["band"], dist_deg=r.get("dist_deg", ""),
                            note=f"data confirmed {r.get('data_day','')}"))
    else:
        out.append(row)

with open(DST, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(out)

with open(REPORT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["action", "rank", "old", "new", "band",
                                       "dist_deg", "note"])
    w.writeheader()
    w.writerows(changes)

# every station code must still be unique -- a replacement could collide with an existing row
seen = {}
dups = []
for r in out:
    k = (r["network"], r["station"])
    if k in seen:
        dups.append(k)
    seen[k] = 1

print(f"original manifest : {len(rows)} stations")
print(f"replaced          : {n_sub}")
print(f"deleted (Mars)    : {n_del}")
print(f"new manifest      : {len(out)} stations")
print(f"duplicate codes introduced: {len(dups)}  {dups[:5] if dups else ''}")
print(f"\nwritten: {DST}")
print(f"changes: {REPORT}")
