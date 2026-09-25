#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_fixed_test
#SBATCH --array=0-1
#SBATCH -t 00:40:00
#SBATCH --mem=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_test_%a.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_test_%a.err
# 4-day test of the FIXED orchestrator: 05-25 (partial first day = the anchor problem), 05-26, 05-27, 05-28 (corrupt day)
bash /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/code_fixed/run_fixed_chunk.sh fixed_test $SLURM_ARRAY_TASK_ID 19940525 19940528
