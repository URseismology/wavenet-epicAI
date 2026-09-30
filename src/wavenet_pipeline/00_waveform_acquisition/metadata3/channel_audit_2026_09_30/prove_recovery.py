#!/usr/bin/env python3
"""Do the proposed config fixes ACTUALLY retrieve data? Test, don't assert.

Categories 1 (has_BH_LH_should_have_worked, 433) and 2 (missed_other_seismometer_band, 312)
have been called "recoverable by config change" on the strength of metadata inspection plus a
12-station availability sample. That is inference, not proof -- and the same conflation of
"registered in FDSN" with "data exists" is what produced category 1 in the first place.

This runs the REAL production download path -- ObsPy MassDownloader with the same
Restrictions shape orchestrator.py uses -- twice per station:

  CURRENT : location_priorities=["", "00", "10"], channel="BH?,LH?"   (what production does today)
  FIXED   : location_priorities=["*"],            channels widened to every high-gain
            seismometer band >= 1 Hz, in lowest-rate-first order (LH, MH, BH, SH, HH, EH)

A station counts as recovered only if FIXED writes real waveform bytes where CURRENT wrote
none. One day per station is enough to settle it.
"""
import csv, os, random, shutil, sys, warnings
import collections

warnings.filterwarnings("ignore")
from obspy import UTCDateTime
from obspy.clients.fdsn.mass_downloader import (RectangularDomain, Restrictions,
                                                 MassDownloader)

AUDIT = "/tmp/channel_audit_results.csv"
SCRATCH = "/RAID6/lab_archive/_recovery_test"
N_PER_CAT = int(sys.argv[1]) if len(sys.argv) > 1 else 10

# Lowest-rate-first, high-gain seismometers only, all >= 1 Hz (Nyquist >= 0.5 Hz).
WIDE_PRIORITIES = ["LH[ZNE123]", "MH[ZNE123]", "BH[ZNE123]",
                   "SH[ZNE123]", "HH[ZNE123]", "EH[ZNE123]"]
WIDE_CHANNELS = "LH?,MH?,BH?,SH?,HH?,EH?"

ERRORS = []


def attempt(net, sta, lat, lon, year, tag, locs, chans, prios):
    out = os.path.join(SCRATCH, f"{net}.{sta}.{tag}")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    dom = RectangularDomain(minlatitude=lat - 0.5, maxlatitude=lat + 0.5,
                            minlongitude=lon - 0.5, maxlongitude=lon + 0.5)
    t0 = UTCDateTime(year, 6, 1)
    res = Restrictions(starttime=t0, endtime=t0 + 86400, chunklength_in_sec=86400,
                       network=net, station=sta, channel=chans,
                       reject_channels_with_gaps=False, minimum_length=0.0,
                       channel_priorities=prios, location_priorities=locs)
    # Never swallow this. An earlier version used `except Exception: pass` and reported
    # 0/20 recovered -- which was entirely an artefact: on a machine saturated with 40
    # packaging workers, MassDownloader could not even construct itself
    # ("RuntimeError: can't start new thread") and no request was ever issued. A silent
    # except turned a broken harness into what looked like a definitive scientific result.
    try:
        MassDownloader().download(dom, res, mseed_storage=os.path.join(out, "w"),
                                  stationxml_storage=os.path.join(out, "x"))
    except Exception as e:
        ERRORS.append(f"{net}.{sta}/{tag}: {type(e).__name__}: {str(e)[:120]}")
    nbytes = 0
    chset = set()
    wdir = os.path.join(out, "w")
    for root, _, files in os.walk(wdir):
        for fn in files:
            p = os.path.join(root, fn)
            nbytes += os.path.getsize(p)
            parts = fn.split(".")
            if len(parts) >= 4:
                chset.add(parts[3])
    shutil.rmtree(out, ignore_errors=True)
    return nbytes, sorted(chset)


def main():
    rows = list(csv.DictReader(open(AUDIT)))
    random.seed(20260930)
    os.makedirs(SCRATCH, exist_ok=True)
    summary = {}

    for cat in ("has_BH_LH_should_have_worked", "missed_other_seismometer_band"):
        pool = [r for r in rows if r["category"] == cat and r["lat"] and r["lon"]]
        sample = random.sample(pool, min(N_PER_CAT, len(pool)))
        print(f"\n{'=' * 74}\n{cat}  ({len(pool)} total, testing {len(sample)})\n{'=' * 74}")
        rec = cur_ok = 0
        for r in sample:
            net, sta = r["network"], r["station"]
            lat, lon = float(r["lat"]), float(r["lon"])
            yr = (int(r["year_start"]) + int(r["year_end"])) // 2
            b_cur, c_cur = attempt(net, sta, lat, lon, yr, "cur",
                                   ["", "00", "10"], "BH?,LH?",
                                   ["LH[ZNE12]", "BH[ZNE12]"])
            b_fix, c_fix = attempt(net, sta, lat, lon, yr, "fix",
                                   ["*"], WIDE_CHANNELS, WIDE_PRIORITIES)
            if b_cur > 0:
                cur_ok += 1
            gained = b_fix > 0 and b_cur == 0
            if gained:
                rec += 1
            mark = "RECOVERED" if gained else ("both got data" if b_fix and b_cur
                                               else "still nothing")
            print(f"  {net}.{sta:7s} {yr}  current {b_cur/1e3:8.1f} kB {str(c_cur):>22}"
                  f" | fixed {b_fix/1e3:9.1f} kB {str(c_fix):>26}  {mark}")
        summary[cat] = (len(sample), cur_ok, rec)

    print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
    for cat, (n, cur, rec) in summary.items():
        print(f"{cat}")
        print(f"   tested {n}, current config got data for {cur}, FIXED config recovered {rec}"
              f"  ({100*rec/n:.0f}%)")
    # A run where every attempt errored is a broken harness, not a finding. Say so loudly.
    print(f"\nerrors raised by download(): {len(ERRORS)}")
    for e in ERRORS[:12]:
        print(f"   {e}")
    if ERRORS:
        print("\n*** RESULTS ARE NOT TRUSTWORTHY WHILE download() IS RAISING. ***")
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    main()
