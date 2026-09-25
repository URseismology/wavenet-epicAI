"""Read-only targeted extraction of one pair's ADAMA reference products (no full-file scans)."""
import json, sys
import h5py, numpy as np
D = "/scratch/tolugboj_lab/FastMSPEC_dispcurve_batch/"
OUT = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/results/adama_reference/"
import os; os.makedirs(OUT, exist_ok=True)
PAIRS = ["XD.RUNG-XD.MTAN", "XD.MTAN-XD.RUNG"]
summary = {}
for fn in ["ADAMAraw_co_ral.h5", "ADAMAraw_co_love.h5", "ADAMAraw_cf_love.h5"]:
    entry = {"pair_found": None, "leaves": {}}
    with h5py.File(D + fn, "r") as f:
        wf = f["waveforms"]
        for pair in PAIRS:
            if pair in wf:      # direct path lookup, no key listing
                entry["pair_found"] = pair
                g = wf[pair]
                for chpair in g.keys():
                    for leaf in g[chpair].keys():
                        ds = g[chpair][leaf]
                        arr = ds[()]
                        attrs = {k: (v.tolist() if hasattr(v, "tolist") else str(v)) for k, v in ds.attrs.items()}
                        key = f"{fn}|{pair}|{chpair}|{leaf}"
                        entry["leaves"][key] = {"n": int(arr.size), "attrs": attrs,
                                                "nonzero": int((arr != 0).sum())}
                        np.save(OUT + key.replace("|", "__").replace("/", "_") + ".npy", arr)
                break
    summary[fn] = entry
    print(fn, "->", entry["pair_found"], "leaves:", len(entry["leaves"]), flush=True)
json.dump(summary, open(OUT + "adama_pair_summary.json", "w"), indent=1, default=str)
print(json.dumps(summary, indent=1, default=str)[:3500])
