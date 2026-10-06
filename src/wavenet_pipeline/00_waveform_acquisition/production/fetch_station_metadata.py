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
# Ordering matters for South American temporary-network archives (confirmed 2026-09-28,
# SAmericaNoise survey): GFZ (GEOFON) hosts many of these -- 2B.NS25 and 3D.LT13 both
# returned nothing from IRIS but a full response from GFZ. IRIS stays first since it
# resolves permanent global-network stations (G, IU) directly and federates across most
# other centres; GFZ moved up from 4th to 2nd rather than reached only after 3 failures.
# Fallback chain, tried in order when the federator cannot be consulted. It is a FALLBACK
# now, not the primary route: a fixed list of obspy provider names cannot know where a
# given station's metadata actually lives, and silently returns "no response available"
# for anything held elsewhere. Measured across the full 1,999-station network 2026-10-01,
# stations route to data centres this list does not contain at all -- AUSPASS (38
# stations), USPSC (37), ICGC (3), SED (2), BATS (1), UIB-NORSAR (1) -- and obspy's own
# registry does not even have names for AUSPASS, BATS or SED. Worse, obspy's "USP" entry
# points at a host with NO dataselect service, so a name that IS in the list can still be
# the wrong endpoint. The federator knows; ask it first (see federator_endpoints).
PROVIDER_CHAIN = ["IRIS", "GFZ", "ORFEUS", "RESIF", "INGV", "ETH", "BGR", "KOERI",
                  "NCEDC", "SCEDC", "NOA", "NIEP", "LMU", "KNMI"]

# Optional discovery manifest (discovery/discover_stations.py). When present, the endpoint
# it recorded for a station is tried BEFORE the fixed chain above, so metadata is fetched
# from wherever the federator says the data actually is. Absent or unreadable, behaviour is
# exactly the old fixed-chain behaviour -- this is strictly additive.
DISCOVERY_MANIFEST = os.environ.get("WAVENET_DISCOVERY_MANIFEST", "")
_DISCO = {}


def federator_endpoints(network, station):
    """Endpoints the federator routed this station to, most specific first. [] if unknown."""
    if not DISCOVERY_MANIFEST:
        return []
    if "df" not in _DISCO:
        try:
            import pandas as _pd
            _DISCO["df"] = _pd.read_csv(DISCOVERY_MANIFEST)
        except Exception:
            _DISCO["df"] = None
    df = _DISCO["df"]
    if df is None:
        return []
    try:
        m = df[(df["network"].astype(str) == str(network))
               & (df["station"].astype(str) == str(station))]
        if not len(m):
            return []
        return [p for p in str(m.iloc[0].get("obspy_providers", "")).split(";") if p]
    except Exception:
        return []
# MUST match orchestrator.py's CHANNELS. This was left at the old narrow "BH?,LH?" when
# the download side was widened to six bands (4dd8859, 2026-09-30), so responses were only
# ever fetched for BH/LH. Measured 2026-10-01: all 77 stations the coverage gate reported
# as "HAS DATA but NO response" carry ONLY HH/EH/SH/MH channels (HH 69, EH 20, SH 7, MH 3)
# and zero BH/LH -- the fetcher could not match them, so they were guaranteed to package in
# raw counts. Exactly the same defect class as the stage never being wired in at all: a fix
# applied on one side of the pipeline and not the other.
CHANNELS = os.environ.get("WAVENET_CHANNELS", "LH?,MH?,BH?,SH?,HH?,EH?")


def discovery_row(network, station):
    """This station's row in the discovery manifest, or None. The manifest is the SAME
    source orchestrator.py derives its download window and provider pinning from, so using
    it here keeps the two sides of the pipeline in agreement by construction."""
    if not DISCOVERY_MANIFEST or not os.path.exists(DISCOVERY_MANIFEST):
        return None
    try:
        df = pd.read_csv(DISCOVERY_MANIFEST)
        m = df[(df["network"].astype(str) == str(network))
               & (df["station"].astype(str) == str(station))]
        return m.iloc[0] if len(m) else None
    except Exception:
        return None


def target_channel_codes(network, station):
    """Channel codes discovery says this station HAS -- the coverage target the fetched
    response must meet. Without an explicit target there is nothing to check completeness
    against, which is how 410 channels across 240 stations ended up packaged in raw counts
    while the Stage 1.5 gate reported 82% healthy: it counted response FILES present, not
    CHANNELS covered."""
    row = discovery_row(network, station)
    if row is None:
        return set()
    return {c.strip() for c in str(row.get("channels", "")).replace(";", ",").split(",")
            if c.strip()}


def station_window(root, network, station):
    """Full deployment span (+/-1yr) so every response EPOCH is captured -- a decades-long
    station changes instruments, and orchestrator.py looks the response up per trace by
    time, so a single-epoch inventory would silently fail for part of the history.

    Prefers the DISCOVERY MANIFEST, because that is what orchestrator.py bounds its download
    with. Taking the window from a different source (station_summary.csv) let the two drift:
    BL.NUPA's metadata window admitted a single 7-week BHZ epoch while the download obtained
    SHE/SHN/SHZ across a far wider span, so every one of its channels packaged in counts."""
    row = discovery_row(network, station)
    if row is not None:
        try:
            y0, y1 = int(row["year_min"]), int(row["year_max"])
            return UTCDateTime(y0 - 1, 1, 1), UTCDateTime(y1 + 1, 12, 31)
        except Exception:
            pass
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
    # Federator-routed endpoints first (where the data actually is), then the fixed chain.
    # Client() accepts either a registered provider name or a base URL, so a centre obspy
    # has never heard of (AUSPASS, BATS, SED) is reachable by URL alone.
    chain = federator_endpoints(network, station) + [
        p for p in PROVIDER_CHAIN if p not in federator_endpoints(network, station)]
    # MERGE across providers; do NOT stop at the first one that returns anything.
    #
    # This used to `break` as soon as a provider returned one channel with a response, and
    # never checked that the inventory covered the channels the DOWNLOAD would obtain. The
    # download is pinned to the provider that holds the waveforms and takes every band it
    # finds; the metadata fetch took whatever the first reachable provider happened to have.
    # Nothing required the two to agree. Measured 2026-10-05: 240 of 1,264 packaged stations
    # carried 410 channels with no response -- TM.PANO's first provider returned only BH
    # while the download also obtained HH; XL.HD25's returned LH against a downloaded HH.
    #
    # Coverage target comes from discovery's own channel list, so "complete" is a checkable
    # claim rather than "some provider answered".
    want = target_channel_codes(network, station)
    merged, providers_used = None, []
    for prov in chain:
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
        merged = inv if merged is None else merged + inv
        providers_used.append(prov)
        have = {c.code for n in merged for s in n for c in s.channels if c.response is not None}
        if want and want.issubset(have):
            break        # provably complete against discovery's channel list -- stop early

    if merged is not None:
        allc = [c for n in merged for s in n for c in s.channels]
        with_resp = [c for c in allc if c.response is not None]
        have = sorted({c.code for c in with_resp})
        # Atomic: write beside, then rename. A re-fetch can run while a live campaign is
        # reading station_metadata/ to remove instrument responses, and a half-written
        # StationXML read mid-flight would fail a station for a reason that has nothing to
        # do with its data.
        tmp_xml = xml_path + ".tmp"
        merged.write(tmp_xml, format="STATIONXML")
        os.replace(tmp_xml, xml_path)
        status.update(ok=True, provider=";".join(providers_used),
                       providers_used=providers_used,
                       n_channels=len(allc), n_with_response=len(with_resp),
                       channels=have,
                       # Recorded so the gate can check COVERAGE instead of file presence.
                       # The old gate passed at 82% while 19% of packaged stations held
                       # uncorrectable channels, because it only asked "is there a file?".
                       channels_wanted=sorted(want),
                       channels_missing=sorted(want - set(have)) if want else [],
                       coverage_complete=bool(want) and want.issubset(set(have)),
                       epochs=sorted({str(c.start_date)[:10] for c in with_resp}),
                       xml_path=xml_path, xml_bytes=os.path.getsize(xml_path))

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
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even when a response already exists (REPAIR mode); "
                         "the default skip is for RESUMING a partly-finished run")
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
        #
        # --force defeats that, and exists because the guard made a REPAIR impossible. The
        # 2026-10-05 re-fetch -- whose entire purpose was to replace narrow, first-provider
        # responses with merged complete ones -- skipped 790 of 1,001 stations with "already
        # had a response", i.e. it did nothing for exactly the stations that needed fixing.
        # Resumability and repair want opposite behaviour from the same check, so the caller
        # must say which one it means.
        p = os.path.join(md, f"{row['network']}.{row['station']}.json")
        if os.path.exists(p) and not args.force:
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
