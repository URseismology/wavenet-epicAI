import glob, collections
from obspy import read_inventory
cnt = collections.Counter(); per_sta = {}
for fn in sorted(glob.glob("/scratch/tolugboj_lab/wavenet_ncf_quicktest/scratch_work/*/stationxml/*.xml")):
    try:
        inv = read_inventory(fn)
    except Exception as e:
        continue
    for net in inv:
        for sta in net:
            us = set()
            for ch in sta:
                if ch.code[:2] in ("BH", "LH"):
                    try: us.add(ch.response.instrument_sensitivity.input_units.upper())
                    except Exception: us.add("NO-SENSITIVITY")
            per_sta[f"{net.code}.{sta.code}"] = sorted(us)
            for u in us: cnt[u] += 1
print("stations surveyed:", len(per_sta)); print("input-unit tally (stations declaring each):", dict(cnt))
print({k: v for k, v in per_sta.items() if v != ["M/S"]})
