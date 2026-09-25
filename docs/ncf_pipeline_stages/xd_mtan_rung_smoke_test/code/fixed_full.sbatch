#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_fixed_full
#SBATCH --array=0-9
#SBATCH -t 01:45:00
#SBATCH --mem=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_full_%a.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_full_%a.err
# FIXED pipeline over the full 1994-05-25..10-30 window: 5 date-range chunks x 2 stations (task = chunk*2 + station)
C=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/code_fixed/run_fixed_chunk.sh
CH=$((SLURM_ARRAY_TASK_ID / 2)); STA=$((SLURM_ARRAY_TASK_ID % 2))
RANGES=("19940525 19940625" "19940626 19940727" "19940728 19940828" "19940829 19940929" "19940930 19941030")
set -- ${RANGES[$CH]}
bash $C fixed_full/chunk$((CH+1)) $STA $1 $2
