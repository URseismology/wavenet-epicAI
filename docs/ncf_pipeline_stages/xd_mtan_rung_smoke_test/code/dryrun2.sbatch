#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_dryrun3
#SBATCH -t 00:20:00
#SBATCH --mem=24G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun2.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun2.err
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate fastmspec_batch
python3 $R/code/correlation_smoke.py $R/scratch_work/dryrun/master.h5 $R/scratch_work/dryrun/out3
