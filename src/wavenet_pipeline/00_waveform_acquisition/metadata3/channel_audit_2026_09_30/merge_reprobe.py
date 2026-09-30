#!/usr/bin/env python3
"""Fold the re-probe recoveries into the accepted replacement list.

The first availability probe sampled 5 fixed windows across the original station's years and
wrongly rejected 9 of 23 candidates that do have contemporaneous data (39% false negatives).
The epoch-aware re-probe found them. This merges those back in, applying the same 0.5 deg
closeness rule, and reports the corrected campaign position.
"""
import csv, json
from obspy.geodetics import locations2degrees

MAX_DEG = 0.5
acc = list(csv.DictReader(open("/tmp/replacements_accepted.csv")))
cand = {(r["orig_network"], r["orig_station"]): r
        for r in csv.DictReader(open("/tmp/replacement_candidates.csv"))}
re_ = json.load(open("/tmp/reprobe_results.json"))

have = {(r["orig_network"], r["orig_station"]) for r in acc}
added, too_far, elsewhere, empty = [], [], [], []

for x in re_:
    onet, osta = x["orig"].split(".", 1)
    r = cand.get((onet, osta))
    if not r:
        continue
    if x["verdict"] == "overlap_data":
        deg = locations2degrees(float(r["orig_lat"]), float(r["orig_lon"]),
                                float(r["new_lat"]), float(r["new_lon"]))
        if deg <= MAX_DEG and (onet, osta) not in have:
            r = dict(r)
            r["dist_deg"] = round(deg, 4)
            r["data_day"] = x.get("day", "")
            added.append(r)
        elif deg > MAX_DEG:
            too_far.append((deg, x))
    elif x["verdict"] == "no_overlap_but_data_elsewhere":
        elsewhere.append(x)
    elif x["verdict"] == "truly_empty":
        empty.append(x)

cols = list(acc[0].keys()) if acc else []
with open("/tmp/replacements_accepted.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(acc + added)

print(f"recovered by the corrected probe and within {MAX_DEG} deg : {len(added)}")
for r in added:
    print(f"   {r['dist_deg']:.3f} deg  {r['orig_network']}.{r['orig_station']:8s}"
          f" -> {r['new_network']}.{r['new_station']:8s} {r['band']}  data {r['data_day']}")
print(f"\nrecovered but beyond {MAX_DEG} deg : {len(too_far)}")
for deg, x in sorted(too_far):
    print(f"   {deg:.2f} deg  {x['orig']} -> {x['cand']}")
print(f"\ndata exists but in a different era (science call) : {len(elsewhere)}")
for x in elsewhere:
    print(f"   {x['orig']:14s} -> {x['cand']:12s} epochs {x['epoch'][0]}..{x['epoch'][1]}")
print(f"\ngenuinely empty : {len(empty)}")

total_acc = len(acc) + len(added)
print("\n" + "=" * 58)
print(f"ACCEPTED replacements   : {total_acc}")
print(f"deleted (Mars)          : 1")
print(f"OUTSTANDING locations   : {208 - total_acc - 1}")
print("=" * 58)
