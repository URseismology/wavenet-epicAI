"""Every refused (day, channel) in the ORIGINAL orchestrator run, with the raw-file facts that explain it.

Unlike why_refused.py (which replays BHZ only), this starts from the orchestrator's own `day_errors`, so a day refused for
some channels only (XD.RUNG 1994-10-20: BHE and BHN, not BHZ) is included. Writes results/refusals_detail.json.
Run on Bluehive in the `instaseis` environment.
"""
import glob, json, math
from obspy import read, UTCDateTime
R = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"

def info(sta, ch, day):
    f = sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*{ch}__{day}T*"))
    if not f: return None
    st = read(f[0], headonly=True)
    s = min(t.stats.starttime for t in st); e = max(t.stats.endtime for t in st); fs = st[0].stats.sampling_rate
    n_records = sum(t.stats.npts for t in st)                 # samples actually present in the day file
    n_span = int(round((e - s) * fs)) + 1                      # samples after merging the record segments (gaps zero-filled)
    return dict(first_sample=str(s), last_sample=str(e), n_raw=n_span, n_raw_in_records=int(n_records), n_segments=len(st),
                n_decimated=math.ceil(n_span / 20), runs_past_midnight_s=round(max(0.0, float(e - UTCDateTime(day) - 86400)), 3))

out = []
for sta, fn in (("MTAN", "0000_XD_MTAN.json"), ("RUNG", "0001_XD_RUNG.json")):
    de = json.load(open(f"{R}/results/{fn}")).get("day_errors") or {}
    by_day = {}
    for key, msg in de.items():
        day, ch = key.split("/"); by_day.setdefault(day, {})[ch] = msg
    for day in sorted(by_day):
        prev = (UTCDateTime(day) - 86400).strftime("%Y%m%d")
        chans = sorted(by_day[day])
        out.append(dict(station=sta, day=day, channels_refused=chans, previous_day=prev,
                        previous_day_info={c: info(sta, c, prev) for c in ("BHE", "BHN", "BHZ")},
                        refused_day_info={c: info(sta, c, day) for c in ("BHE", "BHN", "BHZ")},
                        message=by_day[day][chans[0]]))
json.dump(out, open(f"{R}/results/refusals_detail.json", "w"), indent=1)
for r in out:
    print(r["station"], r["day"], r["channels_refused"], "previous", r["previous_day"],
          {c: (v["n_raw"], v["runs_past_midnight_s"]) for c, v in r["previous_day_info"].items() if v})
