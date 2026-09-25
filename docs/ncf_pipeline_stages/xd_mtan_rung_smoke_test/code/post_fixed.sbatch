#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_fixed_post
#SBATCH -t 02:00:00
#SBATCH --mem=64G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_post.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_post.err
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
F=$R/fixed_full
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
rm -f $R/master_fixed.h5
INPUTS=""
for c in 1 2 3 4 5; do INPUTS="$INPUTS $F/chunk$c/packaged_h5/XD.MTAN.h5 $F/chunk$c/packaged_h5/XD.RUNG.h5"; done
python3 $R/code_fixed/rover_download/build_master_h5.py --inputs $INPUTS --master $R/master_fixed.h5
python3 $R/code_fixed/rover_download/load_master_h5.py $R/master_fixed.h5
python3 $R/code_fixed/rover_download/load_master_h5.py $R/master_fixed.h5 XD MTAN BHZ
python3 $R/code_fixed/rover_download/load_master_h5.py $R/master_fixed.h5 XD RUNG BHZ
python3 - <<PY
import json, glob
qc = {}
for f in sorted(glob.glob("$F/chunk*/packaged_h5/*.qc.json")):
    sta = f.split("/")[-1].split(".")[1]
    qc.setdefault(sta, {}).update(json.load(open(f)))
json.dump(qc, open("$R/results/fixed_qc_all.json", "w"))
for s, d in qc.items():
    fl = [k for k, v in d.items() if any(c.get("flag") for c in v.values())]
    print(s, "days with QC record:", len(d), " flagged:", len(fl), fl)
res = {}
for f in sorted(glob.glob("$F/chunk*/results/*.json")):
    d = json.load(open(f)); res.setdefault(d["station"], []).append({k: d.get(k) for k in ["n_days_processed", "n_days_refused", "n_days_qc_flagged", "day_errors", "day_notes", "preprocess_elapsed_s", "h5_size_bytes"]})
json.dump(res, open("$R/results/fixed_chunk_results.json", "w"), indent=1)
for s, L in res.items():
    print(s, "days processed:", sum(x["n_days_processed"] for x in L), " refused:", sum(x["n_days_refused"] for x in L), " day_errors:", [x["day_errors"] for x in L if x["day_errors"]], " notes:", [x["day_notes"] for x in L if x["day_notes"]])
PY
conda activate fastmspec_batch
python3 $R/code/correlation_smoke.py $R/master_fixed.h5 $R/results/correlation_fixed
