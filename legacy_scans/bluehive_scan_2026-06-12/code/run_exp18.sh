#!/bin/bash

ROOT="/scratch/tolugboj_lab/Prj_Wavenet/epic_production"
cd "$ROOT"

# Step 1: Copy worker and apply fix
cp worker_point_forces.py worker_point_forces_exp18.py

python3 << 'PYEOF'
with open('worker_point_forces_exp18.py', 'r') as f:
    src = f.read()

old = "workdir = f'/scratch/tolugboj_lab/Prj_Wavenet/epic_production/tmp_wavenet/task_{sim_id}/rank_{rank}'"
new = "workdir = f'/scratch/tolugboj_lab/Prj_Wavenet/epic_production/tmp_wavenet/task_{sim_id}_{os.environ.get(\"SLURM_JOB_ID\", \"local\")}/rank_{rank}'"

if old in src:
    src = src.replace(old, new)
    with open('worker_point_forces_exp18.py', 'w') as f:
        f.write(src)
    print("Fixed: worker_point_forces_exp18.py")
else:
    print("WARNING: pattern not found - check manually")
PYEOF

# Step 2: Update submit script to use new worker and new cleanup
sed -i 's|worker_point_forces.py|worker_point_forces_exp18.py|' submit_exp18.sh
sed -i 's|rm -rf ${ROOT}/tmp_wavenet/task_${TASK_ID}|rm -rf ${ROOT}/tmp_wavenet/task_${TASK_ID}_${SLURM_JOB_ID}|' submit_exp18.sh

echo ""
echo "Verification:"
grep "worker_point_forces" submit_exp18.sh
grep "rm -rf" submit_exp18.sh
grep "workdir" worker_point_forces_exp18.py | head -2

# Step 3: Clean up leftover tmp dirs from failed runs
echo ""
echo "Cleaning up leftover tmp dirs..."
rm -rf ${ROOT}/tmp_wavenet/task_2
rm -rf ${ROOT}/tmp_wavenet/task_3
rm -rf ${ROOT}/tmp_wavenet/task_4

# Step 4: Submit
echo ""
echo "Submitting exp18 jobs..."
/software/slurm/current/bin/sbatch --array=2 submit_exp18.sh
/software/slurm/current/bin/sbatch --array=3 submit_exp18.sh
/software/slurm/current/bin/sbatch --array=4 submit_exp18.sh

echo ""
echo "Done. Monitor with: squeue -u \$USER"
