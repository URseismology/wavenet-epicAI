import os
import subprocess

ROOT = "/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test"

print("==========================================")
print("WaveSim Python Orchestrator Started")
print("==========================================")

print("[1/2] Generating JobDiff Pending Map...")
os.system('python3 generate_jobmap_diff.py')

try:
    with open('job_map_pending.csv', 'r') as f:
        pending = len(f.readlines())
except FileNotFoundError:
    pending = 0

if pending == 0:
    print("No pending jobs found. All simulations complete!")
    exit(0)

print(f"[2/2] Total Pending Simulations: {pending}")

# We submit exactly 1 configuration per Slurm task.
SUBJOBS = 1

# Slurm restricts MaxArrayTaskID to 1000 on Bluehive.
# We will split the submissions into perfectly sized 999-job arrays.
CHUNK_SIZE = 999

for i in range(0, pending, CHUNK_SIZE):
    chunk_len = min(CHUNK_SIZE, pending - i)
    base_offset = i
    
    # We route exclusively to 'preempt' to avoid 'standard' node core-crashes.
    # 3 hours is plenty for 1 configuration.
    cmd = f"sbatch -p preempt --time=03:00:00 --array=1-{chunk_len} submit_wavesim_batch.sh 1 {SUBJOBS} {base_offset}"
    print(f"Executing: {cmd}")
    os.system(cmd)

print("==========================================")
print("WaveSim Orchestrator Complete")
print("==========================================")
