#!/usr/bin/env python
"""Stage 1.5: fetch each station's instrument response ONCE, independently of waveform
download, and verify coverage before any processing runs.

Why this exists as its own stage (PI, 2026-09-25): station response metadata is small,
static and needed exactly once, so it has nothing in common with bulk waveform retrieval
except the provider. Coupling the two -- letting MassDownloader deposit StationXML as a
side effect of downloading waveforms -- meant that any quirk in the waveform path silently
cost us the response, and the pipeline then fell through to raw counts without complaint.
That is not hypothetical: a survey of the first 667 packaged stations found 283 with NO
StationXML stored at all and 36.9% of channels (787/2130) left in raw counts, while a
direct query showed the metadata was available the whole time (2H.BTIE: 3/3 channels with
response from IRIS). Raw counts cannot be compared in amplitude across stations, so that
silently compromised roughly a third of the archive.

Decoupled, this stage is cheap (~0.1 MB/station), re-runnable, independent of waveform
availability, reusable across reprocessing without re-fetching, and -- most importantly --
verifiable UP FRONT: you know which stations lack a response before spending the
preprocessing budget, instead of discovering it in the packaged output afterwards.

A station with no obtainable response is then a recorded, deliberate decision (see
report_coverage), never a silent degradation.

Usage:
    fetch_station_metadata.py --root ROOT --idx N          # one station (array task)
    fetch_station_metadata.py --root ROOT --report         # coverage report over all
"""
import argparse
import glob
import json
import os
import sys

import pandas as pd
from obspy import UTCDateTime
from obspy.clients.fdsn import Client

# Tried in order. The IRIS federator routes across FDSN data centres, so it resolves most
# stations on its own; the rest are fallbacks for centres it does not route to.
PROVIDER_CHAIN = ["IRIS", "ORFEUS", "RESIF", "GFZ", "INGV", "ETH", "BGR", "KOERI",
                  "NCEDC", "SCEDC", "NOA", "NIEP", "LMU", "KNMI"]
CHANNELS = os.environ.get("WAVENET_CHANNELS", "BH?,LH?")


def station_window(root, network, station):
    """Full deployment span (+/-1yr) so every response EPOCH is captured -- a decades-long
    station changes instruments, and orchestrator.py looks the response up per trace by
    time, so a single-epoch inventory would silently fail for part of the history."""
    summary = os.path.join(os.path.dirname(__file__), "..", "metadata3",
                            "key_index_summary", "station_summary.csv")
    if os.path.exists(summary):
        df = pd.read_csv(summary)
        m = df[(df["network"] == network) & (df["station"] == station)]
        if len(m) and pd.notna(m.iloc[0].get("year_min")) and pd.notna(m.iloc[0].get("year_max")):
            return (UTCDateTime(int(m.iloc[0]["year_min"]) - 1, 1, 1),
                    UTCDateTime(int(m.iloc[0]["year_max"]) + 1, 12, 31))
    return UTCDateTime(1970, 1, 1), UTCDateTime(UTCDateTime.now().date)


def fetch_one(root, idx):
    manifest = pd.read_csv(os.path.join(root, "manifest", "fps_stations.csv"))
    sta = manifest.iloc[idx]
    network, station = sta["network"], sta["station"]
    out_dir = os.path.join(root, "station_metadata")
    os.makedirs(out_dir, exist_ok=True)
    xml_path = os.path.join(out_dir, f"{network}.{station}.xml")
    status_path = os.path.join(out_dir, f"{network}.{station}.json")
    t0, t1 = station_window(root, network, station)

    status = dict(network=network, station=station, idx=int(idx), ok=False,
                   provider=None, n_channels=0, n_with_response=0, epochs=[], errors={})
    for prov in PROVIDER_CHAIN:
        try:
            inv = Client(prov, timeout=90).get_stations(
                network=network, station=station, location="*", channel=CHANNELS,
                level="response", starttime=t0, endtime=t1)
        except Exception as e:
            status["errors"][prov] = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        chans = [c for n in inv for s in n for c in s.channels]
        with_resp = [c for c in chans if c.response is not None]
        if not with_resp:
            status["errors"][prov] = f"returned {len(chans)} channel(s), none with a response"
            continue
        inv.write(xml_path, format="STATIONXML")
        status.update(ok=True, provider=prov, n_channels=len(chans),
                       n_with_response=len(with_resp),
                       channels=sorted({c.code for c in with_resp}),
                       epochs=sorted({str(c.start_date)[:10] for c in with_resp}),
                       xml_path=xml_path, xml_bytes=os.path.getsize(xml_path))
        break

    tmp = status_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, status_path)
    print(f"[metadata] {network}.{station}: "
          + (f"OK via {status['provider']} -- {status['n_with_response']}/{status['n_channels']} "
             f"channels with response {status.get('channels')}"
             if status["ok"] else f"NO RESPONSE FOUND ({len(status['errors'])} provider(s) tried)"))
    return status["ok"]


def report_coverage(root):
    """The gate before processing resumes.

    The question that matters is NOT "does every station have a response" -- plenty of
    stations in the fixed 2,000 network have no BH?/LH? channels at all and therefore
    neither waveforms nor a relevant response (X3.OBS01, for example, carries only
    EDH/EL1/EL2/ELZ; it returned no data AND no response, which is correct, not a fault).
    The real gate is: does every station that HAS waveform data also have a response?
    Those are the stations that would otherwise be packaged in raw counts and silently
    fail to be amplitude-comparable with anything else."""
    md = os.path.join(root, "station_metadata")
    manifest = pd.read_csv(os.path.join(root, "manifest", "fps_stations.csv"))
    n_total = len(manifest)

    has_data = set()
    known = set()
    results_dir = os.path.join(root, "results")
    if os.path.isdir(results_dir):
        for rf in glob.glob(os.path.join(results_dir, "*.json")):
            try:
                r = json.load(open(rf))
            except (ValueError, OSError):
                continue
            key = f"{r.get('network')}.{r.get('station')}"
            known.add(key)
            if r.get("package_ok"):
                has_data.add(key)

    resp_ok, resp_none, not_queried = set(), set(), set()
    for _, row in manifest.iterrows():
        key = f"{row['network']}.{row['station']}"
        p = os.path.join(md, key + ".json")
        if not os.path.exists(p):
            not_queried.add(key)
            continue
        try:
            s = json.load(open(p))
        except (ValueError, OSError):
            not_queried.add(key)
            continue
        (resp_ok if s.get("ok") else resp_none).add(key)

    blocking = sorted(has_data & resp_none)            # data but no response -- the problem
    benign = sorted(resp_none - has_data)              # no response and no data -- fine
    unknown = sorted((resp_none | not_queried) - known)  # not processed yet, status unknown

    print(f"station response coverage over {n_total} stations:")
    print(f"  response available        : {len(resp_ok)} ({100*len(resp_ok)/max(n_total,1):.1f}%)")
    print(f"  not yet queried           : {len(not_queried)}")
    print(f"  no response, but ALSO no waveform data (benign): {len(benign)}")
    print(f"  >> HAS DATA but NO response (blocking)        : {len(blocking)}")
    if blocking:
        print("     " + ", ".join(blocking[:40]) + (" ..." if len(blocking) > 40 else ""))
    if unknown:
        print(f"  no response, station not processed yet (unknown): {len(unknown)}")
    out = os.path.join(md, "_coverage.json")
    with open(out, "w") as f:
        json.dump(dict(n_total=n_total, n_response_ok=len(resp_ok),
                        blocking=blocking, benign=benign, unknown=unknown,
                        not_queried=sorted(not_queried)), f, indent=2)
    print(f"  written: {out}")
    clean = not blocking and not not_queried
    print("  GATE: " + ("PASS -- safe to resume processing" if clean else
                         "HOLD -- resolve the blocking/unqueried stations first"))
    return clean


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--idx", type=int, default=None, help="manifest row (SLURM array task)")
    ap.add_argument("--count", type=int, default=1,
                    help="how many consecutive stations this task handles. Batching matters: "
                         "the per-task cost here is conda activation (tens of seconds), not "
                         "the ~2s query, so one station per array task spends almost all of "
                         "its wall time on overhead")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        sys.exit(0 if report_coverage(args.root) else 1)

    n_rows = len(pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv")))
    start = args.idx + int(os.environ.get("WAVENET_IDX_OFFSET", 0))
    manifest = pd.read_csv(os.path.join(args.root, "manifest", "fps_stations.csv"))
    md = os.path.join(args.root, "station_metadata")
    n_done = n_skip = 0
    for idx in range(start, min(start + args.count, n_rows)):
        row = manifest.iloc[idx]
        # Idempotent: a station already fetched successfully is not re-queried, so this is
        # safe to re-run over a range that partly completed.
        p = os.path.join(md, f"{row['network']}.{row['station']}.json")
        if os.path.exists(p):
            try:
                if json.load(open(p)).get("ok"):
                    n_skip += 1
                    continue
            except (ValueError, OSError):
                pass
        fetch_one(args.root, idx)
        n_done += 1
    print(f"[metadata] task done: {n_done} fetched, {n_skip} already had a response")


if __name__ == "__main__":
    main()
