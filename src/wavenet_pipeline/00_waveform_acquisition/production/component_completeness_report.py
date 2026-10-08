#!/usr/bin/env python
"""Per-DAY three-component completeness across packaged shards.

The question is not "how many channels" but "does each packaged DAY carry a full
3-component set". Two things have to be handled before the number means anything:

  1. HORIZONTAL NAMING. 1/2 are the same physical slots as N/E -- a borehole or
     non-oriented sensor just names them differently. BH1/BH2/BHZ IS a complete set.
     Counting raw channels per band treats a station that renamed its horizontals
     between epochs as having 5-6 channels in one band, so no day can ever have all
     of them, and every day is scored as a deficit. Collapse to SLOTS: Z, H1, H2.

  2. NON-OVERLAPPING EPOCHS. Even after collapsing, a band only has a 3-component
     expectation on days the station was actually operating that band.  A day with
     nothing at all is not an incomplete day, it is a day outside coverage.
"""
import collections
import glob
import os
import warnings

import h5py
import numpy as np

warnings.filterwarnings("ignore")

ROOT = "/scratch/tolugboj_lab/wavenet_ncf_production_v2/packaged_h5"

# component letter -> slot.  1/2 are N/E under a different convention; 3 is the
# vertical of a 1/2/3 triple.  X/Y are rare orthogonal namings.
SLOT = {"Z": "Z", "3": "Z",
        "N": "H1", "1": "H1", "X": "H1",
        "E": "H2", "2": "H2", "Y": "H2"}

hist = collections.Counter()          # days by number of slots filled
per_station_frac = []                 # fraction of operating days that are complete
band_hist = collections.Counter()     # same, split by band code
slot_missing = collections.Counter()  # which slot is absent on an incomplete day
n_bands = 0
n_stations = 0
skipped = collections.Counter()

for p in sorted(glob.glob(os.path.join(ROOT, "*.h5"))):
    try:
        with h5py.File(p, "r", locking=False) as f:
            key = list(f.keys())[0]
            g = f[key]
            if "_coverage" not in g:
                skipped["no _coverage"] += 1
                continue
            cov = g["_coverage"]
            chans = [c for c in cov if not c.startswith("_")]
            if not chans:
                skipped["empty _coverage"] += 1
                continue

            # band -> slot -> OR of every channel mapping to that slot
            bands = collections.defaultdict(dict)
            for c in chans:
                s = SLOT.get(c[-1].upper())
                if s is None:
                    skipped["odd component " + c[-1]] += 1
                    continue
                b = c[:-1]                      # e.g. BH, LH -- keeps location-free band
                a = np.asarray(cov[c][:]) > 0
                prev = bands[b].get(s)
                if prev is None:
                    bands[b][s] = a
                else:                           # same slot, two epochs/namings: union
                    n = max(len(prev), len(a))
                    pp = np.zeros(n, bool); pp[:len(prev)] = prev
                    aa = np.zeros(n, bool); aa[:len(a)] = a
                    bands[b][s] = pp | aa

            st_complete = st_operating = 0
            for b, slots in bands.items():
                if len(slots) < 3:
                    # band genuinely never had 3 slots packaged at all -- counted
                    # separately below, not as "incomplete days"
                    skipped["band with %d slot(s)" % len(slots)] += 1
                    continue
                n_bands += 1
                n = max(len(v) for v in slots.values())
                stack = np.zeros((3, n), bool)
                for i, s in enumerate(("Z", "H1", "H2")):
                    v = slots[s]
                    stack[i, :len(v)] = v
                filled = stack.sum(axis=0)
                operating = filled > 0
                for k in (1, 2, 3):
                    hist[k] += int((filled == k).sum())
                    band_hist[(b, k)] += int((filled == k).sum())
                inc = operating & (filled < 3)
                for i, s in enumerate(("Z", "H1", "H2")):
                    slot_missing[s] += int((inc & ~stack[i]).sum())
                st_complete += int((filled == 3).sum())
                st_operating += int(operating.sum())

            if st_operating:
                n_stations += 1
                per_station_frac.append(st_complete / st_operating)
    except Exception as e:
        skipped["read error: " + type(e).__name__] += 1

tot = sum(hist.values())
print("PER-DAY 3-COMPONENT COMPLETENESS  (slots Z/H1/H2; 1->H1, 2->H2)")
print("  {:,} stations, {:,} three-slot bands, {:,} operating channel-days".format(
    n_stations, n_bands, tot))
print()
for k in (3, 2, 1):
    print("    {} of 3 slots : {:>12,}  ({:6.2f}%)".format(k, hist[k], 100.0 * hist[k] / max(tot, 1)))
print()

f = np.array(per_station_frac)
print("PER-STATION fraction of operating days that are COMPLETE")
print("    median {:.3f}   mean {:.3f}".format(np.median(f), f.mean()))
for lo, hi, lab in ((0.999, 1.01, "100%   (every day complete)"),
                    (0.95, 0.999, " 95-100%"),
                    (0.80, 0.95, " 80-95%"),
                    (0.50, 0.80, " 50-80%"),
                    (0.0, 0.50, "  <50%  (chronically incomplete)")):
    n = int(((f >= lo) & (f < hi)).sum())
    print("    {:<34} {:>5} stations  ({:5.1f}%)".format(lab, n, 100.0 * n / len(f)))
print()

print("ON AN INCOMPLETE DAY, WHICH SLOT IS ABSENT")
t = sum(slot_missing.values())
for s in ("Z", "H1", "H2"):
    print("    {:<4} {:>12,}  ({:5.1f}%)".format(s, slot_missing[s], 100.0 * slot_missing[s] / max(t, 1)))
print()

print("BY BAND  (complete share of that band's operating days)")
codes = sorted({b for b, _ in band_hist}, key=lambda b: -sum(band_hist[(b, k)] for k in (1, 2, 3)))
for b in codes[:8]:
    tt = sum(band_hist[(b, k)] for k in (1, 2, 3))
    print("    {:<4} {:>12,} days   {:6.2f}% complete".format(b, tt, 100.0 * band_hist[(b, 3)] / max(tt, 1)))
print()
if skipped:
    print("NOT COUNTED")
    for k, v in skipped.most_common(10):
        print("    {:<28} {:>6}".format(k, v))
