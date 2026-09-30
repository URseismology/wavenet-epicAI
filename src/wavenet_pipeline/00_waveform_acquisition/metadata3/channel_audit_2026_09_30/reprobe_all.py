#!/usr/bin/env python3
"""Re-probe every "candidates_but_no_data" case with a sound availability test.

The original probe sampled 5 day-windows spread across the ORIGINAL station's years. That
produced at least one confirmed false negative (XH.SAR -> XH.KUL had data inside the window
and was still rejected), and it could not distinguish "no data" from "recorded in a
different period".

This version:
  * reads the candidate's real channel epochs,
  * intersects them with the original station's window,
  * probes many windows inside that intersection (not a fixed 5),
  * and, if the intersection is empty or dry, reports whether data exists ELSEWHERE so the
    window mismatch is visible rather than silently fatal.

Classification: overlap_data (usable now) / no_overlap_but_data_elsewhere (science call) /
truly_empty (rejection was right).
"""
import csv, json, os
import concurrent.futures as cf
from obspy.clients.fdsn import Client
from obspy import UTCDateTime

SRC = "/tmp/replacement_candidates.csv"
OUT = "/tmp/reprobe_results.json"
PROBES = 12

rows = [r for r in csv.DictReader(open(SRC)) if r["status"] == "candidates_but_no_data"]


def probe(client, net, sta, band, t0, t1, n):
    if t1 <= t0:
        return None
    for i in range(n):
        s = t0 + (t1 - t0) * (i + 0.5) / n
        try:
            st = client.get_waveforms(network=net, station=sta, location="*",
                                       channel=band + "?", starttime=s, endtime=s + 86400)
        except Exception:
            continue
        if sum(len(tr.data) for tr in st) > 0:
            return s
    return None


def work(r):
    client = Client("IRIS", timeout=90)
    net, sta, band = r["new_network"], r["new_station"], r["band"]
    oy0, oy1 = int(r["y0"]), int(r["y1"])
    ow0, ow1 = UTCDateTime(oy0, 1, 1), UTCDateTime(oy1, 12, 31)
    out = dict(orig=f"{r['orig_network']}.{r['orig_station']}", cand=f"{net}.{sta}",
               band=band, dist_km=float(r["dist_km"]) if r["dist_km"] else None)
    try:
        inv = client.get_stations(network=net, station=sta, channel=band + "?",
                                   level="channel")
    except Exception as e:
        out["verdict"] = "error"
        out["detail"] = type(e).__name__
        return out
    eps = [(c.start_date, c.end_date or UTCDateTime())
           for n_ in inv for s_ in n_ for c in s_.channels if c.start_date]
    if not eps:
        out["verdict"] = "error"
        out["detail"] = "no epochs"
        return out
    e0, e1 = min(e[0] for e in eps), max(e[1] for e in eps)
    out["epoch"] = [str(e0)[:10], str(e1)[:10]]

    got = probe(client, net, sta, band, max(e0, ow0), min(e1, ow1), PROBES)
    if got is not None:
        out["verdict"] = "overlap_data"
        out["day"] = str(got)[:10]
        return out
    got = probe(client, net, sta, band, e0, e1, PROBES)
    if got is not None:
        out["verdict"] = "no_overlap_but_data_elsewhere"
        out["day"] = str(got)[:10]
    else:
        out["verdict"] = "truly_empty"
    return out


print(f"re-probing {len(rows)} cases with {PROBES} windows each\n")
res = []
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for r in ex.map(work, rows):
        res.append(r)
        d = f"{r['dist_km']:.1f} km" if r.get("dist_km") is not None else "?"
        print(f"  {r['orig']:14s} -> {r['cand']:12s} {d:>10}  {r['verdict']}"
              + (f"  ({r.get('day','')})" if r.get("day") else ""))

json.dump(res, open(OUT, "w"), indent=1)
import collections
print("\n=== verdict ===")
for k, v in collections.Counter(x["verdict"] for x in res).most_common():
    print(f"{v:4d}  {k}")
print(f"\nwritten: {OUT}")
