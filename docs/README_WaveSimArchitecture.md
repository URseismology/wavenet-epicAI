# WaveSim Simulation Architecture (V6)

## 1. Overview
This directory orchestrates the massive parallel deployment of the WaveSim simulation (`Baowei_test`) across the University of Rochester's Bluehive HPC cluster. To handle 100,000+ individual simulations safely across multiple queues, the pipeline relies on a highly resilient, auto-checkpointing 'Status File' architecture.

## 2. Core Components

### A. The Master Orchestrator (`launch_wavesim_auto.sh`)
You do not manually submit array indices. Instead, you deploy this Orchestrator.
- **Execution:** `sbatch launch_wavesim_auto.sh`
- **Role:** This script wakes up on the `urseismo` compute node, checks the Slurm queue to protect actively running jobs, runs the Python JobDiff logic, and automatically launches the massive payload arrays across the `urseismo`, `standard`, and `preempt` partitions with maxed-out walltimes.
- **Restart Strategy:** If the preempt or standard queues fail, get killed, or finish, simply run `sbatch launch_wavesim_auto.sh` again. The Orchestrator will automatically resubmit the missing/failed jobs without duplicating anything running on `urseismo`.

### B. The JobDiff Progress Tracker (`generate_jobmap_diff.py`)
- **Role:** Rather than scanning 60GB of binary outputs to check progress, this script simply counts the lightweight `.done` files in the `logs/status/` directory. 
- It compares these completions against the master `job_map.csv` and generates a fresh `job_map_pending.csv` for the auto-launcher to execute.

### C. The Payload Executable (`submit_wavesim_batch.sh`)
- **Role:** The actual simulation engine. It runs the `mpirun python3 worker_point_forces_bl.py` script.
- **Sub-job Chunking:** To prevent overwhelming the Slurm scheduler, a single Slurm Array ID will loop over `N` sub-jobs sequentially.
- **Status Files & Fail-safes:** When a sub-job succeeds, it executes `touch logs/status/STEM_SUCCESS.done`. If this file already exists at the start of a run, a Bash fail-safe immediately skips the job to prevent accidental data overwrites.

### D. The Legacy Bridge (`build_legacy_status.py`)
- **Role:** A one-time utility used to bridge older simulations (which lacked status logs) into the modern tracking system. It reads the God Mode Agent's JSON topology map to instantly backfill the `.done` logs for previously completed output folders.
