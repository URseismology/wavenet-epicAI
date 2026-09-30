#!/bin/bash
# Mirror VERIFIED packaged NCF shards from BlueHive3 to repovibranium (2026-09-30).
#
# PI guidance: "all shards should be mirrored on repo, atos, and terra (no distinction on
# v1, v2, v?)". This is the repo leg, started first because that path is proven (direct
# rsync, confirmed working from BH3 itself this session) while atos/terra access is sorted.
#
# SAFETY: the campaign is LIVE and packaged_h5/ is written to continuously. A plain
# `rsync -a packaged_h5/` could catch a station's .h5 mid-write and mirror a corrupt file.
# So this only syncs files for stations whose result JSON already says package_ok=True --
# those are finished, and (per the packaging design) never reopened for writing again.
#
# Run FROM BH3 itself: BH3 has its own working, non-interactive path to repovibranium
# (confirmed this session), separate from axon-1's. Running here also means rsync's own
# checksum-on-transfer is the integrity check, with no relay needed.
set -e

SRC=/scratch/tolugboj_lab/wavenet_ncf_production_v2
DEST_HOST=tolugboj@terravibranium.earth.rochester.edu
DEST_DIR=/RAID6/lab_archive/wavenet_ncf_packaged_h5/packaged_h5
LOG=$SRC/state/terra_mirror_log.jsonl
FILELIST=/tmp/mirror_filelist_terra_$$.txt

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis

echo "=== building list of verified-complete stations ==="
python3 - "$SRC" "$FILELIST" <<'PYEOF'
import glob, json, os, sys
root, out = sys.argv[1], sys.argv[2]
names = []
for f in glob.glob(os.path.join(root, "results", "*.json")):
    try:
        r = json.load(open(f))
    except Exception:
        continue
    if r.get("package_ok") and r.get("network") and r.get("station"):
        h5 = f"{r['network']}.{r['station']}.h5"
        if os.path.exists(os.path.join(root, "packaged_h5", h5)):
            names.append(h5)
with open(out, "w") as f:
    f.write("\n".join(sorted(names)) + "\n")
print(f"{len(names)} verified shards to mirror")
PYEOF

N=$(wc -l < "$FILELIST")
echo "=== syncing $N files to $DEST_HOST:$DEST_DIR ==="
ssh -o BatchMode=yes "$DEST_HOST" "mkdir -p $DEST_DIR"
rsync -a --info=stats2 --files-from="$FILELIST" "$SRC/packaged_h5/" "$DEST_HOST:$DEST_DIR/"

echo "=== spot-check: independent byte-size comparison, 8 files ==="
BAD=0
for f in $(shuf -n 8 "$FILELIST" 2>/dev/null || head -8 "$FILELIST"); do
    LS=$(stat -c%s "$SRC/packaged_h5/$f" 2>/dev/null)
    RS=$(ssh -o BatchMode=yes "$DEST_HOST" "stat -c%s '$DEST_DIR/$f' 2>/dev/null")
    if [ "$LS" != "$RS" ]; then
        echo "  MISMATCH: $f local=$LS remote=$RS"
        BAD=1
    fi
done
if [ "$BAD" = "1" ]; then
    echo "*** spot-check found a mismatch -- investigate before trusting this sync ***"
    exit 1
fi
echo "spot-check OK"

echo "=== recording ==="
mkdir -p "$(dirname "$LOG")"
python3 -c "
import json, time
print(json.dumps(dict(ts=time.time(), n_files=$N, dest='$DEST_HOST:$DEST_DIR',
                       source_root='$SRC')))" >> "$LOG"
echo "log: $LOG"
rm -f "$FILELIST"
echo "=== done: $N shards mirrored to repovibranium ==="
