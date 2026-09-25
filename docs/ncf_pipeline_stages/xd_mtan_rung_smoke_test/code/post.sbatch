#!/bin/bash
#SBATCH -A tolugboj_lab -p urseismo
#SBATCH --job-name=xd_pair_post
#SBATCH -t 01:30:00
#SBATCH --mem=64G
#SBATCH -n 1
#SBATCH --dependency=afterok:31374839
#SBATCH -o /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/post.out
#SBATCH -e /scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/logs/post.err
# Runs only after BOTH orchestrator array tasks (31374839_0, _1) finish successfully.
R=/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis
python3 $R/code/rover_download/build_master_h5.py --inputs-dir $R/packaged_h5 --master $R/master.h5
python3 $R/code/rover_download/load_master_h5.py $R/master.h5
python3 $R/code/rover_download/load_master_h5.py $R/master.h5 XD MTAN BHZ
python3 $R/code/rover_download/load_master_h5.py $R/master.h5 XD RUNG BHZ
conda activate fastmspec_batch
python3 $R/code/correlation_smoke.py $R/master.h5 $R/results/correlation
