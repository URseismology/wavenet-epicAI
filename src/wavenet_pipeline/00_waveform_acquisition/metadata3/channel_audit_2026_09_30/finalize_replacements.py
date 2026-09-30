#!/usr/bin/env python3
"""Apply the PI's acceptance rules to the 168 confirmed replacements (2026-09-30).

  * Remove XB.ELYH0 -- it is the InSight lander at 4.502N 135.623E, on MARS (rank 693 in
    the locked manifest). It is deleted outright, not replaced: its "replacement" XB.ELYSE
    is the InSight seismometer, which is correct by every rule here and useless for
    terrestrial ambient noise.
  * Drop low-gain (?L?) picks -- they measure velocity but are built for strong motion.
  * Closeness threshold: 0.5 degrees of great-circle arc between original and replacement.

Distance is measured in DEGREES (locations2degrees), not km, because that is what the
threshold was specified in and 0.5 deg is only ~55.6 km at the equator -- converting via a
fixed km factor would quietly change the criterion at high latitude.
"""
import csv
from obspy.geodetics import locations2degrees, gps2dist_azimuth

SRC = "/tmp/replacement_candidates.csv"
OUT = "/tmp/replacements_accepted.csv"
REJ = "/tmp/replacements_rejected.csv"
MAX_DEG = 0.5
MARS = {("XB", "ELYH0")}

rows = list(csv.DictReader(open(SRC)))
found = [r for r in rows if r["status"] == "replacement_found"]

accepted, rejected = [], []
for r in found:
    orig = (r["orig_network"], r["orig_station"])
    deg = locations2degrees(float(r["orig_lat"]), float(r["orig_lon"]),
                            float(r["new_lat"]), float(r["new_lon"]))
    r["dist_deg"] = round(deg, 4)
    if orig in MARS:
        r["reject_reason"] = "original is on Mars (InSight) -- delete, do not replace"
        rejected.append(r)
    elif r["instr"] == "L":
        r["reject_reason"] = f"low-gain seismometer ({r['band']})"
        rejected.append(r)
    elif deg > MAX_DEG:
        r["reject_reason"] = f"{deg:.2f} deg away, beyond the {MAX_DEG} deg threshold"
        rejected.append(r)
    else:
        accepted.append(r)

cols = ["orig_network", "orig_station", "orig_lat", "orig_lon", "y0", "y1",
        "new_network", "new_station", "new_lat", "new_lon", "band", "rate", "instr",
        "dist_km", "dist_deg", "data_samples", "data_day"]
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(accepted)
with open(REJ, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols + ["reject_reason"], extrasaction="ignore")
    w.writeheader()
    w.writerows(rejected)

import collections
print(f"confirmed replacements in     : {len(found)}")
print(f"ACCEPTED (<= {MAX_DEG} deg, high-gain): {len(accepted)}")
print(f"rejected                      : {len(rejected)}")
print()
print("rejection reasons:")
for k, v in collections.Counter(
        r["reject_reason"].split(",")[0].split(" deg away")[0][:40] if "deg away" not in
        r["reject_reason"] else "beyond 0.5 deg" for r in rejected).most_common():
    print(f"  {v:4d}  {k}")
print()
b = collections.Counter(r["band"] for r in accepted)
print("bands in accepted set:", dict(b.most_common()))
d = sorted(float(r["dist_deg"]) for r in accepted)
if d:
    print(f"distance deg: min {d[0]:.3f}  median {d[len(d)//2]:.3f}  max {d[-1]:.3f}")
print()
print(f"total originals needing attention : 208")
print(f"  solved by an accepted replacement: {len(accepted)}")
print(f"  deleted (Mars)                   : 1")
print(f"  still unsolved                   : {208 - len(accepted) - 1}")
print(f"\nwritten: {OUT}\n         {REJ}")
