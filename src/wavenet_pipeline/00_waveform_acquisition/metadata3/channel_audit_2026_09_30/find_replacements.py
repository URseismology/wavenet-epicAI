#!/usr/bin/env python3
"""Find replacement stations for the 208 manifest entries that have no seismometer.

PI rules (2026-09-30):
  * Replacement must carry displacement/velocity -- a real seismometer. NOT pressure (?D?),
    NOT electric potential (?Q?), NOT magnetometer (?F?), NOT accelerometer (?N?).
  * Band must be LH? or higher sampling rate (>= 1 Hz, i.e. Nyquist >= 0.5 Hz for the
    0.4 Hz lowpass / 1 Hz product). Among candidates prefer the LOWEST rate -- between MH
    and SH take MH.
  * Must be a triad (3 components).
  * Must be spatially close, and must NOT overlap the existing locked 2000-station set.
  * MUST actually have data. Metadata registration is not availability -- that confusion is
    exactly what produced the 433 "should have worked" stations. Confirm real waveforms,
    even a single day.

Resumable: one JSON per original station under OUT_DIR.
"""
import csv, json, os, sys, time
import concurrent.futures as cf

from obspy.clients.fdsn import Client
from obspy import UTCDateTime
from obspy.geodetics import gps2dist_azimuth

AUDIT = "/tmp/channel_audit_results.csv"
LOCKED = "/tmp/fps_stations.csv"
OUT_DIR = "/tmp/replacements"
OUT_CSV = "/tmp/replacement_candidates.csv"
RADII_DEG = [0.5, 1.0, 2.0, 5.0]
WORKERS = int(os.environ.get("REPL_WORKERS", "8"))
LIMIT = int(os.environ.get("REPL_LIMIT", "0"))

os.makedirs(OUT_DIR, exist_ok=True)

# 2nd SEED char: H = high-gain seismometer, L = low-gain seismometer. Everything else is a
# different instrument entirely (N accelerometer, D pressure, F magnetometer, Q electric
# potential, M mass position, ...). Prefer H; accept L only if nothing else exists.
GOOD_INSTR = {"H": 0, "L": 1}


def load_locked():
    locked, coords = set(), []
    with open(LOCKED) as f:
        for r in csv.DictReader(f):
            locked.add((r["network"], r["station"]))
            try:
                coords.append((float(r["lat"]), float(r["lon"])))
            except (ValueError, KeyError):
                pass
    return locked, coords


def targets():
    out = []
    with open(AUDIT) as f:
        for r in csv.DictReader(f):
            if r["category"] != "no_seismometer_on_station":
                continue
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except ValueError:
                continue
            out.append(dict(network=r["network"], station=r["station"], lat=lat, lon=lon,
                            y0=int(r["year_start"]), y1=int(r["year_end"])))
    return out


def km(a_lat, a_lon, b_lat, b_lon):
    return gps2dist_azimuth(a_lat, a_lon, b_lat, b_lon)[0] / 1000.0


def usable_bands(sta_obj, t0, t1):
    """Group a station's channels into (band -> components), keeping only real seismometers
    at >= 1 Hz that are active in the window. Returns a sort key preferring low rate."""
    bands = {}
    for ch in sta_obj.channels:
        code = ch.code
        if len(code) < 3 or ch.sample_rate is None:
            continue
        instr = code[1].upper()
        if instr not in GOOD_INSTR:
            continue
        if ch.sample_rate < 1.0:
            continue
        if ch.end_date is not None and ch.end_date < t0:
            continue
        if ch.start_date is not None and ch.start_date > t1:
            continue
        b = code[:2]
        e = bands.setdefault(b, dict(comps=set(), rate=ch.sample_rate, instr=instr,
                                     locs=set()))
        e["comps"].add(code[2])
        e["locs"].add(ch.location_code)
        e["rate"] = max(e["rate"], ch.sample_rate)
    return {b: e for b, e in bands.items() if len(e["comps"]) >= 3}


def has_data(client, net, sta, band, locs, t0, t1):
    """Confirm real waveforms exist -- metadata is not availability. Tries a few day-long
    windows spread across the station's active span before giving up."""
    span = t1 - t0
    for frac in (0.5, 0.25, 0.75, 0.1, 0.9):
        s = t0 + span * frac
        try:
            st = client.get_waveforms(network=net, station=sta, location="*",
                                       channel=band + "?", starttime=s, endtime=s + 86400)
        except Exception:
            continue
        n = sum(len(tr.data) for tr in st)
        if n > 0:
            return True, n, str(s)[:10]
    return False, 0, ""


def work(tgt, locked):
    key = f"{tgt['network']}.{tgt['station']}"
    path = os.path.join(OUT_DIR, key + ".json")
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except ValueError:
            pass
    client = Client("IRIS", timeout=90)
    t0 = UTCDateTime(tgt["y0"], 1, 1)
    t1 = UTCDateTime(tgt["y1"], 12, 31)
    res = dict(orig_network=tgt["network"], orig_station=tgt["station"],
               orig_lat=tgt["lat"], orig_lon=tgt["lon"], y0=tgt["y0"], y1=tgt["y1"],
               status="none_found", candidates_seen=0)

    cands = []
    for rad in RADII_DEG:
        try:
            inv = client.get_stations(latitude=tgt["lat"], longitude=tgt["lon"],
                                       maxradius=rad, level="channel",
                                       starttime=t0, endtime=t1)
        except Exception as e:
            res["status"] = f"fdsn_error:{type(e).__name__}"
            inv = None
        if inv is None:
            continue
        for net in inv:
            for sta in net:
                if (net.code, sta.code) in locked:
                    continue
                bands = usable_bands(sta, t0, t1)
                if not bands:
                    continue
                d = km(tgt["lat"], tgt["lon"], sta.latitude, sta.longitude)
                # lowest sampling rate wins; high-gain beats low-gain; then nearest
                for b, e in bands.items():
                    cands.append(dict(network=net.code, station=sta.code,
                                       lat=sta.latitude, lon=sta.longitude,
                                       band=b, rate=e["rate"], instr=e["instr"],
                                       ncomp=len(e["comps"]), dist_km=d,
                                       locs=sorted(e["locs"])))
        if cands:
            break

    res["candidates_seen"] = len(cands)
    # Distance is the primary criterion ("always choose the next closest triad"); the band
    # rule is a FILTER (seismometer, >= 1 Hz), and lowest-rate preference ("between MH and
    # SH go with MH") decides which band to use AT a station, not which station to take.
    # So: collapse each station to its cheapest usable band, then rank stations by distance.
    best_per_station = {}
    for c in cands:
        k = (c["network"], c["station"])
        cur = best_per_station.get(k)
        if cur is None or (c["rate"], GOOD_INSTR[c["instr"]]) < \
                (cur["rate"], GOOD_INSTR[cur["instr"]]):
            best_per_station[k] = c
    cands = sorted(best_per_station.values(), key=lambda c: (c["dist_km"], c["rate"]))

    # Verify data for real, nearest-and-cheapest first. Stop at the first confirmed one.
    for c in cands[:8]:
        ok, n, when = has_data(client, c["network"], c["station"], c["band"],
                                c["locs"], t0, t1)
        c["data_ok"] = ok
        c["data_samples"] = n
        c["data_day"] = when
        if ok:
            res["status"] = "replacement_found"
            res["pick"] = c
            break
    else:
        if cands:
            res["status"] = "candidates_but_no_data"
            res["pick"] = cands[0]
    res["top_candidates"] = cands[:8]
    with open(path, "w") as f:
        json.dump(res, f)
    return res


def main():
    locked, _ = load_locked()
    tg = targets()
    if LIMIT:
        tg = tg[:LIMIT]
    print(f"targets: {len(tg)}   locked set: {len(locked)}   workers: {WORKERS}", flush=True)
    done = 0
    results = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, t, locked): t for t in tg}
        for fu in cf.as_completed(futs):
            t = futs[fu]
            try:
                results.append(fu.result())
            except Exception as e:
                print(f"  {t['network']}.{t['station']}: {type(e).__name__}: {e}",
                      flush=True)
            done += 1
            if done % 10 == 0:
                print(f"  ...{done}/{len(tg)}", flush=True)

    cols = ["orig_network", "orig_station", "orig_lat", "orig_lon", "y0", "y1", "status",
            "new_network", "new_station", "new_lat", "new_lon", "band", "rate", "instr",
            "dist_km", "data_samples", "data_day", "candidates_seen"]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in cols}
            p = r.get("pick")
            if p:
                row.update(new_network=p["network"], new_station=p["station"],
                           new_lat=p["lat"], new_lon=p["lon"], band=p["band"],
                           rate=p["rate"], instr=p["instr"],
                           dist_km=round(p["dist_km"], 1),
                           data_samples=p.get("data_samples", ""),
                           data_day=p.get("data_day", ""))
            w.writerow(row)

    import collections
    c = collections.Counter(r["status"] for r in results)
    print("\n=== summary ===")
    for k, v in c.most_common():
        print(f"{v:5d}  {k}")
    print(f"\nwritten: {OUT_CSV}")


if __name__ == "__main__":
    main()
