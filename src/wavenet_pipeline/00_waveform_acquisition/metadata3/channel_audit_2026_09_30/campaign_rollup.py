#!/usr/bin/env python3
"""What does the channel audit mean for the 2,000-station campaign as a whole?

Pulls together every strand: the locked manifest, the 954 stations that returned no data,
the four audit categories, and the replacement search. Answers two questions directly --
how many station LOCATIONS are still outstanding, and for each of those, how far away the
nearest usable station actually is.

"Outstanding" means: the manifest slot still has no viable source of data after everything
analysed so far.
"""
import csv, os, collections

LOCKED = "/tmp/fps_stations.csv"
AUDIT = "/tmp/channel_audit_results.csv"
CAND = "/tmp/replacement_candidates.csv"
ACCEPT = "/tmp/replacements_accepted.csv"

locked = list(csv.DictReader(open(LOCKED)))
audit = list(csv.DictReader(open(AUDIT)))
cand = {(r["orig_network"], r["orig_station"]): r for r in csv.DictReader(open(CAND))}
accepted = {(r["orig_network"], r["orig_station"]): r for r in csv.DictReader(open(ACCEPT))}

n_locked = len(locked)
by_cat = collections.Counter(r["category"] for r in audit)
n_nodata = len(audit)
n_withdata = n_locked - n_nodata

print("=" * 64)
print("CAMPAIGN ROLLUP -- 2,000-station locked network")
print("=" * 64)
print(f"locked manifest stations          : {n_locked}")
print(f"  returned data                   : {n_withdata}  ({100*n_withdata/n_locked:.1f}%)")
print(f"  returned NO data (audited)      : {n_nodata}  ({100*n_nodata/n_locked:.1f}%)")
print()
print("the no-data stations, by cause:")
for k, v in by_cat.most_common():
    print(f"  {v:4d}  {k}")

# --- disposition of each audit category ---------------------------------------------
print()
print("=" * 64)
print("DISPOSITION")
print("=" * 64)

recoverable_config = by_cat["has_BH_LH_should_have_worked"]
recoverable_band = by_cat["missed_other_seismometer_band"]
n_repl_accept = len(accepted)
mars = 1
unsolved_noseis = by_cat["no_seismometer_on_station"] - n_repl_accept - mars
defect = by_cat["manifest_defect"]

print(f"RECOVERABLE by config change (no new station needed):")
print(f"  {recoverable_config:4d}  has_BH_LH  -- location_priorities '*' (01/02/30/31/32 excluded today)")
print(f"  {recoverable_band:4d}  other band -- widen channel request (MH/SH/HH/EH all >= 1 Hz)")
print(f"  {recoverable_config + recoverable_band:4d}  subtotal")
print()
print(f"NEEDS A DIFFERENT STATION:")
print(f"  {n_repl_accept:4d}  replacement found, data confirmed, within 0.5 deg")
print(f"  {mars:4d}  deleted (XB.ELYH0 is on Mars)")
print(f"  {unsolved_noseis:4d}  OUTSTANDING -- no acceptable replacement")
print()
print(f"MANIFEST DEFECT:")
print(f"  {defect:4d}  nan.SABA (empty network code)")

outstanding = unsolved_noseis + defect
print()
print("=" * 64)
print(f"OUTSTANDING LOCATIONS (no viable source today): {outstanding}")
print("=" * 64)

# --- for the outstanding ones, how far IS the nearest usable station? ----------------
rows = []
for r in audit:
    if r["category"] != "no_seismometer_on_station":
        continue
    k = (r["network"], r["station"])
    if k in accepted or k == ("XB", "ELYH0"):
        continue
    c = cand.get(k)
    if not c:
        rows.append((None, r["network"], r["station"], "no candidate record", ""))
        continue
    if c["status"] == "none_found":
        rows.append((None, r["network"], r["station"], "nothing within 5 deg", ""))
    elif c["status"] == "candidates_but_no_data":
        d = float(c["dist_km"]) if c["dist_km"] else None
        rows.append((d, r["network"], r["station"], "nearest has NO data",
                     f"{c['new_network']}.{c['new_station']}"))
    else:
        d = float(c["dist_km"]) if c["dist_km"] else None
        rows.append((d, r["network"], r["station"], "beyond 0.5 deg",
                     f"{c['new_network']}.{c['new_station']}"))

why = collections.Counter(r[3] for r in rows)
print("\nwhy each is outstanding:")
for k, v in why.most_common():
    print(f"  {v:4d}  {k}")

haved = sorted([r for r in rows if r[0] is not None], key=lambda r: r[0])
if haved:
    ds = [r[0] for r in haved]
    print(f"\ndistance to the nearest usable station, for those that have one ({len(ds)}):")
    print(f"  min {ds[0]:.1f} km   median {ds[len(ds)//2]:.1f} km   max {ds[-1]:.1f} km")
    for lim in (75, 100, 150, 200, 300):
        print(f"    within {lim:3d} km: {sum(1 for x in ds if x <= lim):3d}")
    print("\n  closest 12 (these are the cheapest to rescue if 0.5 deg is relaxed):")
    for d, net, sta, reason, newsta in haved[:12]:
        print(f"    {d:7.1f} km  {net}.{sta:7s} -> {newsta:12s} ({reason})")

nod = [r for r in rows if r[0] is None]
print(f"\n  no candidate at any distance searched (<=5 deg): {len(nod)}")
for _, net, sta, reason, _x in nod[:10]:
    print(f"    {net}.{sta}")
