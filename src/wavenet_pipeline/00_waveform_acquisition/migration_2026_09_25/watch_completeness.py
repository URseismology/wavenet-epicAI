"""Wait for N stations to complete under the patched pipeline, then measure whether the
download is actually COMPLETE -- days obtained vs what the key index says exists. This is
the metric that would have caught the ~86% loss months earlier, so it is now the gate."""
import glob, json, os, sys, time
import pandas as pd, numpy as np

R = "/scratch/tolugboj_lab/wavenet_ncf_production"
want = int(sys.argv[1]) if len(sys.argv) > 1 else 25
s = pd.read_csv("/scratch/tolugboj_lab/wavenet_ncf_framework/metadata3/"
                "key_index_summary/station_summary.csv")[["network","station","total_days"]]
exp = {(r.network, r.station): r.total_days for r in s.itertuples()}

deadline = time.time() + 3 * 3600
while time.time() < deadline:
    rows, retries, errs = [], 0, 0
    for f in glob.glob(R + "/results/*.json"):
        try: r = json.load(open(f))
        except Exception: continue
        if r.get("patch_level") != 2 or not r.get("package_ok"): continue
        retries += r.get("n_download_retries") or 0
        errs += len(r.get("year_errors") or {})
        e = exp.get((r["network"], r["station"]))
        if e and e > 0:
            rows.append(min((r.get("n_days_processed") or 0) / e, 2.0))
    if len(rows) >= want:
        a = np.array(rows)
        print(f"PATCHED COMPLETIONS: {len(rows)} (with a key-index expectation)")
        print(f"  days obtained / expected : median {np.median(a):.2f}  mean {a.mean():.2f}")
        print(f"  fraction below 50%       : {100*(a<0.5).mean():.0f}%   (was 84% pre-fix)")
        print(f"  download retries used    : {retries}")
        print(f"  years still failing      : {errs}")
        print("  VERDICT: " + ("COMPLETE -- fix holds at scale" if np.median(a) > 0.9
                                else "STILL LOSING DATA -- investigate"))
        sys.exit(0)
    time.sleep(90)
print(f"timed out; only {len(rows)} patched completions so far")
