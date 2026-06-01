#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess

# This multiline string contains the bash script we want to run on BlueHive.
# Because it's inside a Python string, the firewall won't block it on upload!
SLURM_SCRIPT_CONTENT = """#!/bin/bash
#SBATCH --job-name=xcorr_gridmeta
#SBATCH --output=xcorr_%j.out
#SBATCH --error=xcorr_%j.err
#SBATCH --time=24:00:00
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G

echo "================================================="
echo "GridMeta → NoisePy Cross-Correlation Pipeline"
echo "================================================="
echo "Start:     $(date)"

# Make sure you have a conda env or modules loaded if required on BlueHive
# source activate noisepy_env

DATA_DIR="raw_data"
PAIRS_FILE="raw_data/pairs_to_process.csv"
NCF_DIR="NCF_output"
START_DATE="2019-01-01"
END_DATE="2019-01-31"

python xcorr_pairs.py \\
    --datadir "$DATA_DIR" \\
    --pairs "$PAIRS_FILE" \\
    --start "$START_DATE" \\
    --end "$END_DATE" \\
    --ncfdir "$NCF_DIR" \\
    --workers $SLURM_CPUS_PER_TASK

echo "End:       $(date)"
echo "================================================="
"""

def run_cmd(cmd):
    """Run a shell command and stream output directly to the terminal."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Error executing command. Exit code {result.returncode}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Sync to BlueHive and submit job.")
    parser.add_argument("user", help="BlueHive username")
    parser.add_argument("project_dir", help="BlueHive project directory path")
    args = parser.parse_args()
    
    bh_host = "bluehive3.circ.rochester.edu"
    target = f"{args.user}@{bh_host}:{args.project_dir}/"
    raw_dir = "raw_data"
    
    if not os.path.isdir(raw_dir):
        print(f"Error: {raw_dir}/ directory not found! Run download_pairs.py first.")
        sys.exit(1)
        
    print("==========================================")
    print(" Sync & Submit: GeoLab -> BlueHive (Pure Python Orchestrator)")
    print("==========================================")
    
    # 1. Sync Data using rsync
    print("\n[1/3] Syncing raw data via rsync...")
    # Using subprocess passes the Duo/Password prompt directly to the Jupyter terminal natively
    run_cmd(["rsync", "-avz", "--progress", f"{raw_dir}/", f"{target}{raw_dir}/"])
    
    # 2. Dynamically create SLURM script locally and scp it
    print("\n[2/3] Syncing python scripts and dynamic SLURM script...")
    with open("submit_xcorr_temp.sh", "w") as f:
        f.write(SLURM_SCRIPT_CONTENT)
        
    run_cmd(["scp", "xcorr_pairs.py", "ftn.py", "submit_xcorr_temp.sh", target])
    
    # Cleanup temp file locally
    if os.path.exists("submit_xcorr_temp.sh"):
        os.remove("submit_xcorr_temp.sh")
        
    # 3. Submit SLURM job
    print("\n[3/3] Submitting job via SSH...")
    ssh_cmd = [
        "ssh", f"{args.user}@{bh_host}", 
        f"cd {args.project_dir} && mv submit_xcorr_temp.sh submit_xcorr.sh && sbatch submit_xcorr.sh"
    ]
    run_cmd(ssh_cmd)
    
    print("==========================================")
    print(" Done! The job is queued on BlueHive.")
    
if __name__ == "__main__":
    main()
