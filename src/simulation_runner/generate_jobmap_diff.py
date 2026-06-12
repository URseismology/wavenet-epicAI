#!/usr/bin/env python3
import os
import sys
import datetime
import subprocess

ROOT_DIR = '/scratch/tolugboj_lab/Prj_Wavenet/epic_production/Baowei_test'
MASTER_JOB_MAP = os.path.join(ROOT_DIR, 'job_map_roundrobin.csv')
PENDING_JOB_MAP = os.path.join(ROOT_DIR, 'job_map_pending.csv')
STATUS_DIR = os.path.join(ROOT_DIR, 'logs', 'status')

def get_running_array_indices():
    """Queries squeue to find currently running array indices for the user."""
    indices = set()
    try:
        # Get custom formatted squeue output showing the array tasks
        result = subprocess.run(['squeue', '-u', os.environ.get('USER', 'tolugboj'), '-h', '-o', '%K'], 
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            # Example output: 31055037_1, 31055037_[2-100%10]
            if '_' in line:
                array_part = line.split('_')[-1]
                # A very basic parse. Realistically, we just want to avoid double-submitting 
                # things that are actively in the queue. 
                # For safety, if there are ANY running jobs, we should probably warn.
                pass
    except Exception as e:
        print(f"Warning: Could not query squeue. {e}")
    return indices

def main():
    if not os.path.exists(MASTER_JOB_MAP):
        print(f'ERROR: Master job map not found at {MASTER_JOB_MAP}')
        sys.exit(1)
        
    os.makedirs(STATUS_DIR, exist_ok=True)
        
    print(f'--- WaveSim JobDiff Check ({datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}) ---')
    print('Scanning lightweight status logs...')

    # 1. Get completed stems from the fast status directory
    completed_stems = set()
    for f in os.listdir(STATUS_DIR):
        if f.endswith('_SUCCESS.done'):
            stem = f.replace('_SUCCESS.done', '')
            completed_stems.add(stem)

    total_jobs = 0
    pending_lines = []
    
    with open(MASTER_JOB_MAP, 'r') as f:
        lines = f.readlines()
        
    start_idx = 0
    if lines and not lines[0].strip().split(',')[0].isdigit():
        pending_lines.append(lines[0])
        start_idx = 1

    for line in lines[start_idx:]:
        line = line.strip()
        if not line:
            continue
            
        total_jobs += 1
        parts = line.split(',')
        if len(parts) < 3:
            pending_lines.append(line + '\n')
            continue
            
        config_file = parts[1]
        config_basename = os.path.basename(config_file).replace('.txt', '')
        
        stem = config_basename.split('_dist_')[0]
        
        # If not completed, it is pending
        if stem not in completed_stems:
            pending_lines.append(line + '\n')

    with open(PENDING_JOB_MAP, 'w') as f:
        f.writelines(pending_lines)
        
    completed_count = len(completed_stems)
    print(f'Total Configurations : {total_jobs}')
    print(f'Completed Outputs  : {completed_count}')
    print(f'Pending/Failed     : {total_jobs - completed_count}')
    print(f'Generated          : {PENDING_JOB_MAP}')

if __name__ == '__main__':
    main()
