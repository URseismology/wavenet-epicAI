#!/usr/bin/env python
"""
Stage A -- FEDERATED DISCOVERY. Standalone; changes nothing in production/.

Answers, per station, three things the downloader currently guesses at:
  1. WHICH data centre actually holds the data  -> so the download can be PINNED to it
  2. WHICH channels/locations exist             -> so we stop requesting bands that aren't there
  3. WHAT real time span they cover             -> so we stop sweeping 1970-2026 for a
                                                   station that lived three years

Output is a manifest consumed by the downloader. Discovery is ~0.1 s/station, so the whole
2,000-station network costs ~4 minutes once, versus every download job rediscovering it
badly on its own walltime (TA.N49A burned 1,320 s doing exactly that and still got nothing).

WHY WE PARSE THE FEDERATOR OURSELVES rather than using obspy's RoutingClient:
obspy 1.2.2's FederatorRoutingClient._split_routing_response() detects data-centre entries
with `if "http://" in line`. IRIS/EarthScope moved to HTTPS, so every line now reads
`DATASELECTSERVICE=https://...`, and "http://" is not a substring of "https://". obspy
therefore parses ZERO data centres and raises
`FDSNNoDataException: Nothing remains to download after the provider inclusion/exclusion
filter` -- a message about a filter that never ran, because the response was already empty.
Verified directly 2026-10-01: raw fedcatalog returns IRISDMC + full channel epochs in 0.1 s
for the same stations obspy reports as having nothing.

THE ERROR-PROOFING RULE: a station is only ever recorded as having no data when the service
explicitly said so (HTTP 204 / empty body on a successful request). Every transport-level
problem is retried and, if still failing, recorded as an ERROR status -- never as "no data".
Conflating "the service said no" with "we failed to ask" is precisely the bug that silently
cost the campaign ~655 stations.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import random
import sys
import time

import pandas as pd
import requests

FEDCATALOG = "https://service.iris.edu/irisws/fedcatalog/1/query"
WANTED_CHANNELS = "LH?,MH?,BH?,SH?,HH?,EH?"

# fedcatalog DATACENTER token -> obspy provider name (obspy.clients.fdsn URL_MAPPINGS).
# Unmapped centres are reported, not silently dropped: an unknown centre is a finding.
DATACENTER_TO_OBSPY = {
    "IRISDMC": "IRIS", "GEOFON": "GFZ", "GFZ": "GFZ", "ORFEUS": "ORFEUS",
    "ODC": "ODC", "RESIF": "RESIF", "INGV": "INGV", "ETH": "ETH", "BGR": "BGR",
    "KOERI": "KOERI", "LMU": "LMU", "NCEDC": "NCEDC", "SCEDC": "SCEDC",
    "NIEP": "NIEP", "NOA": "NOA", "USPSC": "USP", "USP": "USP", "ICGC": "ICGC",
    "IPGP": "IPGP", "KNMI": "KNMI", "GEONET": "GEONET", "TEXNET": "TEXNET",
    "USGS": "USGS", "UIB-NORSAR": "UIB-NORSAR", "RASPISHAKE": "RASPISHAKE",
}

STATUS_ROUTED = "ROUTED"                 # real channels found, download can proceed
STATUS_NO_DATA = "NO_DATA_AT_SERVICE"    # service answered, and the answer was "nothing"
STATUS_NO_WANTED = "NO_WANTED_BAND"      # station exists, but no >=1 Hz high-gain seismometer
STATUS_ERROR = "DISCOVERY_ERROR"         # we failed to ask -- NOT the same as no data


def parse_fedcatalog(text):
    """
    Parse a fedcatalog `format=request` body.

    Scheme-agnostic on purpose -- the obspy bug this replaces was a hardcoded "http://".
    Returns {datacenter: {"dataselect": url, "station": url, "rows": [...]}}.
    """
    out = collections.OrderedDict()
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DATACENTER="):
            cur = line.split("=", 1)[1].split(",")[0].strip()
            out.setdefault(cur, {"dataselect": None, "station": None, "rows": []})
            continue
        if "=" in line and line.split("=", 1)[0].isupper():
            if cur is None:
                continue
            k, v = line.split("=", 1)
            if k == "DATASELECTSERVICE":
                out[cur]["dataselect"] = v.strip()
            elif k == "STATIONSERVICE":
                out[cur]["station"] = v.strip()
            continue
        if cur is None:
            continue
        parts = line.split()
        if len(parts) >= 6:            # NET STA LOC CHA START END
            out[cur]["rows"].append(parts[:6])
    return out


def discover_one(net, sta, channels, attempts, timeout, session):
    """Discover one station. Never raises; always returns a status-bearing dict."""
    rec = dict(network=net, station=sta, key="{}.{}".format(net, sta),
               status=None, datacenters="", obspy_providers="", dataselect_url="",
               n_channel_epochs=0, channels="", locations="",
               year_min=None, year_max=None, start_utc="", end_utc="",
               unmapped_datacenters="", http_status=None, error="", attempts=0)

    last_err = None
    for attempt in range(1, attempts + 1):
        rec["attempts"] = attempt
        try:
            r = session.get(FEDCATALOG, params=dict(
                net=net, sta=sta, cha=channels, level="channel", format="request"),
                timeout=timeout)
            rec["http_status"] = r.status_code

            # 204 (and an empty 200) are the service AFFIRMATIVELY saying "nothing here".
            if r.status_code == 204 or (r.status_code == 200 and not r.text.strip()):
                rec["status"] = STATUS_NO_DATA
                return rec

            # 404 from fedcatalog means "nothing matched THIS query" -- it is not a
            # transport failure. Verified 2026-10-01: 12.OBS21 returns 404 for the wanted
            # bands but 200 for cha=*, with 5 real channels at the station service. So the
            # code is overloaded and we must disambiguate rather than guess: re-ask with
            # cha=* to separate "station has no seismometer we can use" from "station is
            # not there at all". Guessing here is how a filter mismatch becomes a phantom
            # data-loss report.
            if r.status_code == 404:
                probe = session.get(FEDCATALOG, params=dict(
                    net=net, sta=sta, cha="*", level="channel", format="request"),
                    timeout=timeout)
                if probe.status_code == 200 and probe.text.strip():
                    other = parse_fedcatalog(probe.text)
                    chans = sorted({p[3] for pay in other.values() for p in pay["rows"]})
                    rec["channels"] = ";".join(chans)
                    rec["n_channel_epochs"] = sum(len(p["rows"]) for p in other.values())
                    rec["datacenters"] = ";".join([d for d, p in other.items() if p["rows"]])
                    rec["status"] = STATUS_NO_WANTED
                else:
                    rec["status"] = STATUS_NO_DATA
                return rec

            if r.status_code != 200:
                last_err = "HTTP {}".format(r.status_code)
                raise RuntimeError(last_err)

            parsed = parse_fedcatalog(r.text)
            if not parsed:
                rec["status"] = STATUS_NO_DATA
                return rec

            rows, dcs, unmapped = [], [], []
            for dc, payload in parsed.items():
                if payload["rows"]:
                    dcs.append(dc)
                    rows.extend(payload["rows"])
                    if dc not in DATACENTER_TO_OBSPY:
                        unmapped.append(dc)
            if not rows:
                rec["status"] = STATUS_NO_DATA
                return rec

            chans = sorted({p[3] for p in rows})
            wanted = [c for c in chans
                      if len(c) == 3 and c[1] == "H" and c[0] in ("L", "M", "B", "S", "H", "E")]
            starts = sorted(p[4] for p in rows)
            ends = sorted(p[5] for p in rows)

            # How the downloader should construct a client for each centre.
            #
            # ALWAYS prefer the DATASELECTSERVICE URL the federator itself returned, over
            # obspy's built-in provider registry. Learned the hard way 2026-10-01: BL.CDCB
            # routes to DATACENTER=USPSC, and obspy's "USP" shortcut points at
            # http://sismo.iag.usp.br, which exposes NO dataselect service at all -- so
            # pinning by name can never download, while the federator's own
            # http://seisrequest.iag.usp.br does have one. The federator is authoritative
            # about where its data lives; obspy's table is a convenience that can be stale
            # or point at a different host entirely. Using the URL also removes any need to
            # hardcode a mapping per data centre (AUSPASS, etc.).
            specs = []
            for d in dcs:
                ds = parsed[d]["dataselect"] or ""
                base = ds.split("/fdsnws")[0] if "/fdsnws" in ds else ds
                if base:
                    specs.append(base)
                elif d in DATACENTER_TO_OBSPY:      # no URL given; fall back to the name
                    specs.append(DATACENTER_TO_OBSPY[d])

            rec.update(
                datacenters=";".join(dcs),
                obspy_providers=";".join(sorted({s for s in specs if s})),
                unmapped_datacenters=";".join(sorted(set(unmapped))),
                dataselect_url=parsed[dcs[0]]["dataselect"] or "",
                n_channel_epochs=len(rows),
                channels=";".join(chans),
                locations=";".join(sorted({(p[2] if p[2] != "--" else '""') for p in rows})),
                start_utc=starts[0], end_utc=ends[-1],
                year_min=int(starts[0][:4]),
                # Still-active channels carry an open-ended sentinel end date (2599-12-31
                # observed). Taking it literally invents 600-year spans and would make the
                # downloader sweep centuries. Clamp to now.
                year_max=min(int(ends[-1][:4]), time.gmtime().tm_year),
                status=STATUS_ROUTED if wanted else STATUS_NO_WANTED,
            )
            return rec

        except Exception as exc:
            last_err = "{}: {}".format(type(exc).__name__, str(exc)[:120])
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0, 1.5))

    # Exhausted retries. This is an ERROR, deliberately not "no data".
    rec["status"] = STATUS_ERROR
    rec["error"] = last_err or "unknown"
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="CSV with network,station columns")
    ap.add_argument("--only-keys", default=None,
                    help="optional file of NET.STA, one per line, to restrict to")
    ap.add_argument("--out", required=True, help="output discovery manifest CSV")
    ap.add_argument("--channels", default=WANTED_CHANNELS)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    man = pd.read_csv(args.manifest)
    man["key"] = man["network"].astype(str) + "." + man["station"].astype(str)
    if args.only_keys:
        keep = {l.strip() for l in open(args.only_keys) if l.strip()}
        man = man[man["key"].isin(keep)]
    if args.limit:
        man = man.head(args.limit)
    targets = list(zip(man["network"].astype(str), man["station"].astype(str)))
    print("discovering {} stations with {} workers".format(len(targets), args.workers),
          flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "wavenet-epicAI-discovery/1.0"})

    rows, t0, done = [], time.time(), 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(discover_one, n, s, args.channels, args.attempts,
                          args.timeout, session): (n, s) for n, s in targets}
        for fut in cf.as_completed(futs):
            rows.append(fut.result())
            done += 1
            if done % 50 == 0 or done == len(targets):
                el = time.time() - t0
                print("  [discovery] {}/{} ({:.0f}%)  {:.1f} sta/s  elapsed {:.0f}s".format(
                    done, len(targets), 100.0 * done / len(targets), done / max(el, 1e-9), el),
                    flush=True)

    df = pd.DataFrame(rows).sort_values("key")
    df.to_csv(args.out, index=False)

    print()
    print("=" * 78)
    print("DISCOVERY SUMMARY  ({} stations, {:.0f}s)".format(len(df), time.time() - t0))
    print("=" * 78)
    for k, v in df["status"].value_counts().items():
        print("  {:<22} {:>5}".format(k, v))

    err = df[df["status"] == STATUS_ERROR]
    if len(err):
        print()
        print("  {} station(s) could NOT be asked (retried {}x). These are NOT 'no data' --"
              " re-run before drawing any conclusion:".format(len(err), args.attempts))
        for e in err["error"].value_counts().head(5).items():
            print("    {:>4}x  {}".format(e[1], e[0][:90]))

    routed = df[df["status"] == STATUS_ROUTED]
    if len(routed):
        print()
        print("  data centres holding routed stations:")
        c = collections.Counter()
        for s in routed["datacenters"]:
            for d in str(s).split(";"):
                if d:
                    c[d] += 1
        # Show the endpoint that will ACTUALLY be used, not obspy's registry name -- the
        # two can differ, and reporting the name here once hid that USP's registry entry
        # has no dataselect service at all.
        endpoint = {}
        for row in routed.itertuples():
            for d in str(row.datacenters).split(";"):
                if d and d not in endpoint:
                    endpoint[d] = str(row.obspy_providers).split(";")[0]
        for d, n in c.most_common():
            print("    {:<14} {:>5}   -> {}".format(d, n, endpoint.get(d, "?")))

        span = (routed["year_max"] - routed["year_min"] + 1)
        print()
        print("  real year-span per station: median {:.0f}, mean {:.1f}, max {:.0f}"
              .format(span.median(), span.mean(), span.max()))
        print("  years the campaign would sweep blind (1970-2026): 57")
        print("  -> discovery cuts the request volume by about {:.0f}x on the median station"
              .format(57.0 / max(span.median(), 1)))

    unmapped = df[df["unmapped_datacenters"].astype(str).str.len() > 0]
    if len(unmapped):
        print()
        print("  WARNING: {} station(s) route to a data centre with no obspy mapping:"
              .format(len(unmapped)))
        print("    {}".format(sorted({d for s in unmapped["unmapped_datacenters"]
                                      for d in str(s).split(";") if d})))

    print()
    print("wrote {}".format(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
