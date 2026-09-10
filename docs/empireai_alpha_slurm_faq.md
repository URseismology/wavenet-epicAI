# Empire AI Alpha — Slurm & Apptainer FAQ

Sources (NVIDIA Kickstart Workshop series + linked Alpha docs, pulled 2026-09-10,
Tolu attended live):
- [00 — Slurm Cheat Sheet](https://empireai.freshdesk.com/support/solutions/articles/157000376515-00-nvidia-kickstart-workshop-empire-ai-alpha-slurm-cheat-sheet)
- [01 — PyTorch + Slurm](https://empireai.freshdesk.com/support/solutions/articles/157000376513-01-nvidia-kickstart-workshop-pytorch-slurm)
- [Interactive Slurm Quickstart](https://empireai.freshdesk.com/support/solutions/articles/157000374475-empire-ai-alpha-interactive-slurm-quickstart)
- [Job Submission and QoS Overview](https://empireai.freshdesk.com/support/solutions/articles/157000374474-empire-ai-alpha-job-submission-and-qos-overview)
- [Jupyter, SSH & Tunnels](https://empireai.freshdesk.com/support/solutions/articles/157000374476-empire-ai-alpha-jupyter-ssh-tunnels)

This FAQ separates **workshop-only** settings (temporary, tied to the NVIDIA session's
reservation — won't work outside it) from **general Alpha knowledge** you should use on
our actual project account. See `docs/empireai_alpha_slurm_tutorial.md` for a
step-by-step walkthrough with our real settings, and `CLAUDE.md` / `docs/HANDOFF.md`
§4.4 for full Empire AI access mechanics (SSH aliases, 2FA/ControlMaster, etc).

---

### Q: What's the difference between `sbatch`, `salloc`, and `srun`?

| Command | Use it for | What happens |
|---|---|---|
| `sbatch script.sbatch` | Reproducible, non-interactive jobs (real training runs) | Queues the job, returns a job ID immediately, output goes to log files |
| `salloc <options>` | Interactive development/debugging | Reserves resources; you then `srun --pty bash -l` into them |
| `srun <options> <command>` | A one-off test or quick command | Runs immediately under Slurm, can request its own resources |

**Rule of thumb:** `sbatch` for anything you want a record of; `salloc`+`srun` for poking
around interactively; bare `srun` for a quick one-liner like `nvidia-smi -L`.

---

### Q: What account/partition/QoS do I actually use (not the workshop's)?

The workshop cheat sheet uses `xx_micalves_workshop` / `nvidia_workshop` reservation /
pinned nodes (`alphagpu[11,16]`) / MIG slices — **these are workshop-only and will not
work outside that session.** For our actual project work on Alpha:

```
--partition=alpha
--account=ro_tolugboji_planetary
```
No `--reservation` or `--nodelist` — let Slurm pick a node from the full pool.

**QoS matters more than we'd previously documented** — pick one deliberately, don't just
omit it (the default QoS may not be what you want):

| QoS | Best for | Wall time | GPU limit | SU factor |
|---|---|---|---|---|
| `test` | Quick validation/script checks | 2h | 8 GPUs, 1 node | 0.5x (cheap) |
| `interactive` | Live GPU shell for debugging/setup | 2h | 4 GPUs, 1 session | 1.0x |
| `standard` | Default production jobs | 48h | 32 GPUs | 1.0x |
| `long` | Longer, cost-sensitive runs | 7 days | 32 GPUs | 0.5x (cheap) |
| `priority` | Deadline-driven urgent work | 24h | 64 GPUs | 2.0x (expensive) |
| `burst` | System-assigned overflow | 7 days | 32 GPUs | free/system-assigned |

Add `--qos=<tier>`, e.g. `--qos=test` for a sanity check, `--qos=standard` for a real
run, `--qos=long` if a training run needs more than 48h at half the SU cost.

**Also new:** the "current vs future" partition pattern confirms and refines what
`CLAUDE.md` already says about the 2026-09-18 institutional-partition retirement — the
future pattern isn't just "add `--account`", it explicitly pairs with `--qos`:
```
sbatch --partition=alpha --qos=<tier> --account=ro_tolugboji_planetary ... job.sh
```

Check your own real association any time with:
```bash
sacctmgr show assoc where user="$USER" format=User,Account%30,Partition,QOS%60
```

---

### Q: What GPU hardware does Alpha actually have?

**192 GPUs across 24 nodes, 8 GPUs per node** — two tiers:

| GPU type | Nodes | VRAM | Request pattern |
|---|---|---|---|
| H100 | `alphagpu01`–`alphagpu18` (18 nodes) | 80 GB | `--gres=gpu:N` or `--gres=gpu:nvidia_h100_80gb_hbm3:N` |
| H200 | `alphagpu19`–`alphagpu24` (6 nodes) | 141 GB | `--gres=gpu:nvidia_h200:N` |

**Reconcile with what we already had documented:** `CLAUDE.md` currently says Alpha
also has NVIDIA RTX Pro 6000 GPUs "added...to assist processing single-GPU jobs" (per
sysadmin emails, 2026-08/09). This Alpha-docs article doesn't mention RTX Pro 6000 at
all — most likely the RTX Pro 6000 nodes are a newer addition alongside this
established H100/H200 fleet (not a replacement, not a discrepancy), but **this isn't
confirmed** — worth a quick question to Empire AI support/office hours rather than
assuming either doc is wrong.

**MIG vs full GPU:** the workshop used a MIG slice (`--gres=gpu:1g.10gb:1`, a 10GB
partition of one H100) — smaller, isolated, less memory/compute than a full card. Our
actual usage should request full GPUs (`--gres=gpu:1`, `--gres=gpu:4`, etc.) unless
there's a specific reason to use a MIG slice. Don't mix a full-GPU request with a MIG
request in the same job.

Validate whatever you got:
```bash
nvidia-smi -L
echo "$CUDA_VISIBLE_DEVICES"
python3 -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

---

### Q: SU billing — what's the actual formula?

**SU = GPUs × Hours × SU-rate-for-that-QoS.** Examples straight from Empire AI's docs:
- 4 GPUs × 10h on `standard` (1.0x) = 40 SU
- 8 GPUs × 2h on `test` (0.5x) = 8 SU
- 8 GPUs × 24h on `long` (0.5x) = 96 SU
- 8 GPUs × 12h on `priority` (2.0x) = 192 SU

Billing starts 2026-10-01 (already in `CLAUDE.md`) — worth picking QoS deliberately once
that hits, not just defaulting to `standard`/`priority` out of habit.

---

### Q: Alpha vs. Grace — do I need different environments?

**Yes — this is new, and important.** Alpha and the `cpu` partition are **x86_64**;
**Grace is ARM64/aarch64**. A Python venv or any compiled dependency built on Alpha will
NOT run on Grace, and vice versa — you need separate environments per architecture if
you ever use both. (Grace was previously mentioned in our `empireai_connections.md`
reference doc but this architecture detail wasn't recorded before.)

```bash
# CPU-only interactive session (x86_64, alphacpu01-class node)
salloc -p cpu -A ro_tolugboji_planetary -c 4 --mem=16G -t 0:30:00 --job-name=cpu-interactive
srun --pty bash -l

# Grace interactive session (ARM64 — separate env needed)
salloc -p grace --qos=interactive -c 4 --mem=16G -t 01:00:00 --job-name=grace-interactive
srun --pty bash -l
```

---

### Q: How do I run a container on Alpha?

**Apptainer**, not Docker, not Pyxis (Pyxis/Enroot is **Beta's** mechanism — see
`CLAUDE.md`, don't assume Alpha's recipe transfers). Alpha uses `.sif` container images:

```bash
module load apptainer/1.1.9
export APPTAINER_TMPDIR="/tmp/$USER/apptainer-$SLURM_JOB_ID"
mkdir -p "$APPTAINER_TMPDIR"

apptainer exec --nv \
  /path/to/your-image.sif \
  python3 -c "import torch; print(torch.cuda.is_available())"
```
- `--nv` exposes the allocated GPU + host NVIDIA driver libraries inside the container —
  always include it for GPU work.
- `APPTAINER_TMPDIR` **must be on node-local `/tmp`**, not home or project storage —
  Apptainer expands/packages the image there, and home/project filesystems can reject
  the extended-attribute operations used during unpacking. Set and `mkdir -p` it before
  every `apptainer exec`/`apptainer pull`.
- `APPTAINER_CACHEDIR` (for `apptainer pull`, building your own image) should point at
  home storage so downloaded registry layers are reused across builds:
  ```bash
  unset SINGULARITY_TMPDIR SINGULARITY_CACHEDIR   # legacy Singularity vars, unset them
  export APPTAINER_CACHEDIR="$HOME/.apptainer/cache"
  export APPTAINER_TMPDIR="/tmp/$USER/apptainer-build"
  mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
  apptainer pull my-image.sif docker://pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime
  ```
  Pulling a large image can sit quietly at "Creating SIF file..." for 10-20 minutes
  while it compresses — that's normal, not a hang.
- `squashfuse`/`fuse2fs`/`underlay bind-mount` INFO lines in the output are harmless if
  the command finishes and your CUDA check succeeds (Apptainer falls back to converting
  the SIF to a temporary sandbox when optional FUSE helpers aren't available).

This directly matters for our ML pipeline's open environment question (see
`docs/ml_pipeline_stages/stage_b_model_definition.md`) — Alpha and Beta use **different**
container runtimes (Apptainer vs. Pyxis/Enroot); don't assume one recipe works on both.

---

### Q: Do I need a container at all? How do I get PyTorch without one?

No, plain modules work too:
```bash
module load Python/3.10.15
python -m venv ~/venvs/torch
source ~/venvs/torch/bin/activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```
This is a concrete, Empire-AI-documented install recipe (CUDA 12.4 wheels) — directly
useful for our own pipeline's still-open "no PyTorch/CUDA version pinned anywhere"
question. Worth trying this exact command as the Alpha-tier baseline before reaching
for a container.

**Common gotcha:** `ModuleNotFoundError: No module named torch` inside a job but not on
the login node usually means the job is using host Python instead of your venv/container
— check what's actually active in your `sbatch` script.

---

### Q: How do I run Jupyter on Alpha and connect from my laptop?

```bash
# On Alpha, inside a GPU interactive session:
module load Python/3.10.15
module load cuda12.4/toolkit/12.4.1
pip install notebook
salloc -p alpha -A ro_tolugboji_planetary -c 4 --mem=16G -t 0:30:00 \
  --job-name=gpu-interactive --qos=interactive --gres=gpu:1
srun --pty bash -l
jupyter notebook --no-browser --port=8889
```
```bash
# On your laptop, in a separate terminal:
ssh -N -L 8889:localhost:8889 <remote_user>@<remote_host>
```
Then open `http://localhost:8889/` and paste in the token Jupyter printed. `<remote_host>`
here is whatever compute node your `salloc` landed on (check `$SLURM_JOB_NODELIST` /
`hostname`), tunneled through the Alpha login node if it's not directly reachable.

**This is directly useful for us**: our Stage A/B tutorial notebooks
(`src/wavenet_pipeline/03_machine_learning/notebooks/`) currently only run locally —
this gives us a path to run them on real Alpha GPU hardware instead, once we're ready
for that tier.

---

### Q: My job sits in `PD` (pending) forever — what do I check?

```bash
squeue -u "$USER"
scontrol show job <JOBID>          # look at the "Reason" field
```
If Reason is priority-related with no ETA, our known fix (already in `CLAUDE.md`) is
adding `--qos=test` for a quick check — now with the fuller QoS picture above, pick
whichever tier actually matches what you're doing (test/interactive/standard/long).

---

### Q: What do these common messages mean?

| Message | What to do |
|---|---|
| `Requested node configuration is not available` | Partition/account/GPU request doesn't match your association or the reservation/node combo isn't valid — check `sacctmgr show assoc` |
| Job stuck `PD` | `scontrol show job <id>`, check `Reason`; pick an appropriate `--qos` |
| `ModuleNotFoundError: No module named torch` | Running on host Python, not your venv/container — fix inside the job, or use Apptainer with `apptainer exec --nv ... python3` |
| `torch.cuda.is_available()` is `False` | Didn't actually request a GPU (`--gres=gpu:...`), or forgot `apptainer exec --nv` |
| `could not create temporary sandbox: stat /tmp/...` | Set + `mkdir -p` `APPTAINER_TMPDIR` on node-local `/tmp` before `apptainer exec`/`pull` |
| `Couldn't determine user account information: unknown userid` | Node can't resolve your UID via the identity service — report to Empire AI admins, not self-fixable |
| Apptainer image build fails with certificate/xattr/filesystem errors | Build on the login node, `module load apptainer/1.1.9` first, unset legacy `SINGULARITY_*` vars, use local `/tmp` for `APPTAINER_TMPDIR` |

---

### Q: What are the useful job-monitoring commands?

```bash
squeue -u "$USER"                       # your jobs, state (PD/R/CD/F)
squeue -j <JOBID>                       # one job
scontrol show job <JOBID>               # full detail incl. pending Reason
sacct -j <JOBID> --format=JobID,JobName,State,Elapsed,ExitCode,AllocTRES
tail -f logs/<name>-<JOBID>.out         # live stdout
tail -f logs/<name>-<JOBID>.err         # live stderr
scancel <JOBID>                         # kill it
```
Status codes: `PD` pending, `R` running, `CD` completed, `F` failed.

**Gotcha already burned into our own workflow:** the `logs/` directory must exist
*before* you `sbatch` — `mkdir -p logs` first, or the job fails to write output at all.

---

### Q: Where do I find the full option reference?

| Option | Meaning |
|---|---|
| `--job-name=NAME` | Label for queue/logs |
| `--output=PATH` / `--error=PATH` | stdout/stderr destinations (`%x`=job name, `%j`=job ID) |
| `--partition=alpha` | Selects the partition |
| `--account=ACCOUNT` | Charges the account (use `ro_tolugboji_planetary`) |
| `--qos=QOS` | Scheduling policy — see the tiers table above |
| `--nodes=N` | Node count |
| `--ntasks=N` | Process/task count |
| `--ntasks-per-node=N` | Processes per node — useful for multi-GPU/multi-node |
| `--cpus-per-task=N` | CPU cores per process |
| `--gres=gpu:...` | GPU/MIG request |
| `--mem=SIZE` | System memory request |
| `--time=HH:MM:SS` | Wall-time limit |
| `--exclusive` | Whole node — only with explicit admin permission |
| `--array=0-9` | Indexed copies of one script, `$SLURM_ARRAY_TASK_ID` inside |
| `--dependency=afterok:<jobid>` | Start only after another job succeeds |

Command-line flags override matching `#SBATCH` directives in the script:
```bash
sbatch --time=00:30:00 --job-name=longer-test my_job.sbatch
```

Useful env vars inside a job: `$SLURM_JOB_ID`, `$SLURM_JOB_NODELIST`,
`$SLURM_CPUS_PER_TASK`, `$CUDA_VISIBLE_DEVICES`.

---

### Q: SSH keys — anything Alpha-specific?

Standard key setup, nothing unusual:
```bash
ssh-keygen -t rsa
ssh-copy-id -i ~/.ssh/id_rsa.pub <YourUserName>@alpha1.empireai.edu
eval $(ssh-agent)
ssh-add ~/.ssh/id_rsa
```
This is separate from (and doesn't replace) the password+2FA login our ControlMaster
setup already handles — see `docs/memos/2026-09-04-empireai-persistent-access.md`.
