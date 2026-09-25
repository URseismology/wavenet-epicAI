#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_dryrun4
#SBATCH -t 00:40:00
#SBATCH --mem=32G
#SBATCH -n 1
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun4.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/dryrun4.err
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate fastmspec_batch
python3 $R/code/correlation_smoke.py $R/scratch_work/dryrun/master.h5 $R/scratch_work/dryrun/out4 --timing-json $R/results/placement_errors.json
