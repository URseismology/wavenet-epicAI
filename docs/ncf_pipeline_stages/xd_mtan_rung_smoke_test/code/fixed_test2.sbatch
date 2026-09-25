#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_fixed_test2
#SBATCH --array=0-3
#SBATCH -t 00:40:00
#SBATCH --mem=8G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_test2_%a.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/fixed_test2_%a.err
# Hard cases from the ORIGINAL run: a day file with 1,728,001 raw samples followed by the day that was REFUSED
C=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/code_fixed/run_fixed_chunk.sh
case $SLURM_ARRAY_TASK_ID in
  0) bash $C fixed_test2_mtan_0919 0 19940919 19940921 ;;   # MTAN refused 09-20
  1) bash $C fixed_test2_rung_0602 1 19940602 19940604 ;;   # RUNG refused 06-03
  2) bash $C fixed_test2_rung_0915 1 19940915 19940917 ;;   # RUNG refused 09-16
  3) bash $C fixed_test2_rung_0922 1 19940922 19940924 ;;   # RUNG refused 09-23
esac
