# Tutorial: Your First Slurm Job on Empire AI Alpha (WaveNet-EpicAI settings)

Adapted from NVIDIA's Kickstart Workshop materials (see `docs/empireai_alpha_slurm_faq.md`
for sources and the full command reference) — but using **our actual project account**,
not the workshop's temporary one. Do this after you've completed the one-time SSH/
ControlMaster setup in `docs/memos/2026-09-04-empireai-persistent-access.md`.

Time: ~15 minutes. You'll end up with a real GPU job run on Alpha, a Jupyter tunnel you
can use to run our existing tutorial notebooks on real hardware, and a template
`sbatch` script for the team to reuse.

---

## 0. Prerequisites

- You've completed the ControlMaster setup and can run `ssh empireai` yourself
  (interactive login, password + 2FA).
- You know your own Empire AI username (likely different from your axon-1 username —
  ask Tolu if unsure).

Everything below runs **on Alpha**, after `ssh empireai`.

---

## 1. Confirm your account/partition/QoS access

```bash
sacctmgr show assoc where user="$USER" format=User,Account%30,Partition,QOS%60
```
You should see `ro_tolugboji_planetary` listed with the `alpha` partition and at least
`standard`/`test`/`interactive` QoS tiers available. If you don't see this, stop here
and ask Tolu — something's not linked to your account yet.

---

## 2. A CPU-only sanity check first (no GPU, no queue wait)

```bash
mkdir -p logs
srun -p alpha -A ro_tolugboji_planetary --qos=test hostname
```
This should return almost immediately with a compute node hostname (e.g.
`alphagpu07`). If it hangs in `PD`, see the FAQ's "job sits in PD forever" section.

---

## 3. Request one GPU interactively and validate it

```bash
salloc --job-name=wavenet-gpu-check \
  --partition=alpha --account=ro_tolugboji_planetary --qos=interactive \
  --nodes=1 --ntasks=1 --cpus-per-task=4 --gres=gpu:1 --mem=16G --time=00:15:00

srun --pty bash -l
nvidia-smi -L
echo "$CUDA_VISIBLE_DEVICES"
```
Expect one GPU listed — an H100 (`alphagpu01`-`18`) or H200 (`alphagpu19`-`24`)
depending which node you landed on (see the FAQ for the full breakdown). When done:
```bash
exit                      # leave the srun shell
scancel "$SLURM_JOB_ID"   # release the allocation
```

**Do not run GPU work directly on the login node** — `salloc` only reserves resources,
you still need `srun --pty bash -l` (or a full `srun ... command`) to actually use them.

---

## 4. Get PyTorch working — no container needed for a first check

```bash
module load Python/3.10.15
python -m venv ~/venvs/wavenet_torch
source ~/venvs/wavenet_torch/bin/activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
python3 -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```
This should print a torch version, `True`, and a GPU name — inside the same `salloc`
session from step 3 (GPU still allocated). If `torch.cuda.is_available()` is `False`,
you're either not inside the allocation anymore, or forgot `--gres=gpu:1` somewhere.

This venv is a real candidate for our ML pipeline's still-open "no pinned PyTorch/CUDA
environment" question (`docs/ml_pipeline_stages/stage_b_model_definition.md`) — if this
works, it's worth recording as the validated Alpha-tier install recipe.

---

## 5. Write and submit a real batch job

Save as `~/wavenet_gpu_test.sbatch`:
```bash
#!/bin/bash
#SBATCH --job-name=wavenet-gpu-test
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#SBATCH --partition=alpha
#SBATCH --account=ro_tolugboji_planetary
#SBATCH --qos=test
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --mem=16G
#SBATCH --time=00:15:00

set -euo pipefail
hostname
nvidia-smi
source ~/venvs/wavenet_torch/bin/activate
python3 -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

```bash
mkdir -p logs
sbatch ~/wavenet_gpu_test.sbatch
```
`sbatch` prints a job ID (e.g. `Submitted batch job 12345`) — that's your handle for
everything below.

Monitor and inspect:
```bash
squeue -u "$USER"
squeue -j 12345
scontrol show job 12345
tail -f logs/wavenet-gpu-test-12345.out
sacct -j 12345 --format=JobID,JobName,State,Elapsed,ExitCode,AllocTRES
```
Status key: `PD` pending, `R` running, `CD` completed, `F` failed.

---

## 6. Optional: run our existing tutorial notebooks on real Alpha hardware

Our Stage A/B notebooks (`src/wavenet_pipeline/03_machine_learning/notebooks/`) were
built and verified to run on a laptop CPU — this step lets you run the *same* notebooks
against real Alpha GPU hardware instead, as an early look at the terravibranium-gpu/
Alpha tier (see `docs/ml_pipeline_stages/PROGRESS.md`).

```bash
# still inside the salloc session from step 3/4:
pip install notebook torchinfo pycwt h5py pandas pyarrow matplotlib
jupyter notebook --no-browser --port=8889
```
Note the token Jupyter prints, and note `hostname` (or `$SLURM_JOB_NODELIST`) for the
compute node you're on. Then, **in a new terminal on your own laptop**:
```bash
ssh -N -L 8889:localhost:8889 <your-empireai-username>@alpha1.empireai.edu
```
(If Jupyter is running on a compute node, not the login node, you may need a
double-hop tunnel — ask Tolu if `localhost:8889` doesn't connect directly.)

Open `http://localhost:8889/` in your browser, paste in the token, navigate to
`src/wavenet_pipeline/03_machine_learning/notebooks/` (you'll need to `git clone`/`git
pull` the repo into your Alpha home directory first), and run
`stage_a_data_definition.ipynb` / `stage_b_model_definition.ipynb` top to bottom.

**Report back**: do the results match what's already committed in those notebooks
(same 1.67% mask positive-fraction, same overfit convergence behavior), or does real
GPU hardware change anything? This is genuinely useful data for
`docs/ml_pipeline_stages/stage_{a,b}_*.md`'s Hardware Tier Log.

---

## 7. Optional: Apptainer instead of a venv

If you'd rather use a container (more reproducible, avoids venv drift):
```bash
module load apptainer/1.1.9
unset SINGULARITY_TMPDIR SINGULARITY_CACHEDIR
export APPTAINER_CACHEDIR="$HOME/.apptainer/cache"
export APPTAINER_TMPDIR="/tmp/$USER/apptainer-build"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

apptainer pull ~/wavenet_pytorch.sif docker://pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime
```
(This can sit quietly at "Creating SIF file..." for 10-20 minutes on a large image —
that's normal.) Then, in an `sbatch` script or interactive session:
```bash
export APPTAINER_TMPDIR="/tmp/$USER/apptainer-$SLURM_JOB_ID"
mkdir -p "$APPTAINER_TMPDIR"
apptainer exec --nv ~/wavenet_pytorch.sif python3 -c "import torch; print(torch.cuda.is_available())"
```
Remember: this is **Apptainer**, Alpha-specific — Beta uses a completely different
mechanism (Pyxis/Enroot, `.sif` vs. `--container-image=`, see `CLAUDE.md`). Don't
assume a recipe built for one cluster works on the other.

---

## 8. Clean up

```bash
scancel "$SLURM_JOB_ID"    # if you still have an interactive allocation open
```
Check `squeue -u "$USER"` shows nothing left running before you disconnect.

---

## What to report back

Per `docs/memos/2026-09-04-ml-pipeline-stage-ab-review.md`'s spirit — don't just
confirm "it worked," tell us:
- Did `sacctmgr show assoc` show what this tutorial expects for your account?
- Which GPU node/type did you land on, and did the PyTorch install recipe in step 4
  work cleanly?
- If you ran the notebooks in step 6, did the numbers match the committed versions?
- Anything in this tutorial that didn't match what actually happened on your session.
