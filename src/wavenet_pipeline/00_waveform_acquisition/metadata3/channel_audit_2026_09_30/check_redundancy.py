#!/usr/bin/env python3
"""Sanity-check the 168 confirmed replacements before anyone adopts them.

The locked set is a farthest-point-sampled network, so a replacement is only useful if it
adds coverage. Excluding overlap by station CODE (which find_replacements.py does) is not
enough: a different code sitting 3 km from an existing locked station is redundant.

Also flags two things the band filter allows but which deserve a human decision:
  * ?L? low-gain seismometers -- they do measure velocity, but are built for strong motion
    and are poor for ambient noise.
  * replacements so far from the original that they change the network geometry.
"""
import csv
from obspy.geodetics import gps2dist_azimuth

locked = []
for r in csv.DictReader(open("/tmp/fps_stations.csv")):
    try:
        locked.append((r["network"], r["station"], float(r["lat"]), float(r["lon"])))
    except (ValueError, KeyError):
        pass

rows = [r for r in csv.DictReader(open("/tmp/replacement_candidates.csv"))
        if r["status"] == "replacement_found"]

scored = []
for r in rows:
    la, lo = float(r["new_lat"]), float(r["new_lon"])
    # The station being replaced is ITSELF in the locked set, so it must be excluded here --
    # otherwise "distance to the nearest locked station" just re-measures the distance to the
    # original, and a perfect nearby replacement looks maximally redundant. The question is
    # whether the replacement duplicates some OTHER station already in the network.
    orig = (r["orig_network"], r["orig_station"])
    d = min(gps2dist_azimuth(la, lo, a, b)[0] / 1000.0
            for n, s, a, b in locked if (n, s) != orig)
    scored.append({"d_to_locked": d, "row": r})
scored.sort(key=lambda x: x["d_to_locked"])

print(f"confirmed replacements: {len(rows)}\n")
print("distance from each NEW station to the nearest EXISTING locked station:")
for lim in (5, 10, 25, 50, 100):
    n = sum(1 for s in scored if s["d_to_locked"] < lim)
    print(f"  closer than {lim:3d} km : {n:3d}")

print("\n10 most redundant (nearest to a station already in the network):")
for s in scored[:10]:
    r = s["row"]
    print(f"  {s['d_to_locked']:7.1f} km  {r['new_network']}.{r['new_station']:6s}"
          f"  replacing {r['orig_network']}.{r['orig_station']}")

low = [s["row"] for s in scored if s["row"]["instr"] == "L"]
print(f"\nlow-gain (?L?) picks -- flagged for your call: {len(low)}")
for r in low:
    print(f"  {r['new_network']}.{r['new_station']} band={r['band']} "
          f"replacing {r['orig_network']}.{r['orig_station']}")

far = sorted((s["row"] for s in scored), key=lambda r: -float(r["dist_km"]))
far = [r for r in far if float(r["dist_km"]) > 200]
print(f"\nreplacements further than 200 km from the original: {len(far)}")
for r in far[:8]:
    print(f"  {float(r['dist_km']):6.1f} km  {r['orig_network']}.{r['orig_station']}"
          f" -> {r['new_network']}.{r['new_station']}")

keep = [s for s in scored if s["d_to_locked"] >= 25 and float(s["row"]["dist_km"]) <= 200
        and s["row"]["instr"] != "L"]
print(f"\nif you required: >=25 km from an existing station, <=200 km from the original,")
print(f"and high-gain only -> {len(keep)} of {len(rows)} survive")
