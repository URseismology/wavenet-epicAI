#!/bin/bash
#SBATCH -A tolugboj_lab
#SBATCH -p preempt
#SBATCH --qos preempt
#SBATCH -t 00:10:00
#SBATCH -n 1
#SBATCH -J wavenet_ncf_monitor
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_production_v2/state/monitor_%j.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_production_v2/state/monitor_%j.err

# Placement A (of two being tried, PI 2026-09-30: "try both of possible and report") --
# self-chained on BH3, same pattern as inspector.slurm, INCLUDING today's fix baked in
# from the start this time: `module load` before the self-chain sbatch call, since
# sbatch is not on PATH in a bare compute-node shell otherwise (this exact bug silently
# broke inspector's chain earlier today -- see docs/ncf_pipeline_stages/
# 2026-09-30_HANDOFF_pre_pruning.md #4.5).
#
# Deliberately on `preempt` (BH3 has no `debug` partition) -- this is a lightweight,
# low-priority check, appropriate to not compete with real campaign work for resources.
#
# Cadence: twice daily (every 12h), not inspector's 20-min pace -- PI, 2026-09-30:
# "only 2 reports a day otherwise becomes spam." This is a reporting cadence, not an
# alert-rate-limiter: every scheduled run sends a full digest (--digest below), success
# or failure content as actually found, so the team gets a steady, predictable signal
# rather than silence punctuated by unpredictable alerts.
#
# Ends automatically at campaign end via the SAME state/STOP file inspector.slurm and
# logger.slurm already honor -- no new termination mechanism, deliberately: whoever
# runs `master.py stop --root <root>` stops this chain along with everything else.
#
# Fast-failure guard raised to 1h (was inspector's 5min) to match the much longer
# intended cadence -- 5min would barely guard against anything at a 12h pace.
module load slurm/24.05.0.b1 >/dev/null 2>&1

ROOT=/scratch/tolugboj_lab/wavenet_ncf_production_v2
CHAIN_STAMP=$ROOT/state/monitor_last_chain
NOW=$(date +%s)
LAST=$(cat $CHAIN_STAMP 2>/dev/null || echo 0)
if [ -f $ROOT/state/STOP ]; then
    echo "[monitor_chain.slurm] STOP present -- not chaining a successor." >&2
elif [ $((NOW - LAST)) -lt 3600 ]; then
    echo "[monitor_chain.slurm] last chain was $((NOW - LAST))s ago -- refusing to chain (fast-failure guard)." >&2
else
    echo $NOW > $CHAIN_STAMP
    sbatch --export=NONE -p preempt --qos preempt \
           --dependency=afterany:$SLURM_JOB_ID --begin=now+12hours \
           /scratch/tolugboj_lab/monitor_chain.slurm >> $ROOT/state/monitor_chain.log 2>&1 \
        && echo "[monitor_chain.slurm] successor queued." >&2 \
        || echo "[monitor_chain.slurm] FAILED to queue successor -- chain is now broken, see monitor_chain.log" >&2
fi

source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 /scratch/tolugboj_lab/campaign_monitor.py --apply --digest
