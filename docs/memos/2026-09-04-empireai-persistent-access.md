Subject: WaveNet-EpicAI — Empire AI GPU Access & Job Submission for Your Own Claude Code Sessions

Hi Tejwaswini and Chris,

We now have working SSH access to both Empire AI clusters (Alpha and
Beta — our target hardware for the PyTorch U-Net training once the CPS
datasets are ready), plus a confirmed job submission protocol for each.
This memo explains how the access works and walks you through setting
up the same thing under your own axon-1 account, so your own Claude
Code sessions can reach Empire AI directly too.

Full technical details: docs/HANDOFF.md §4.4. This memo is the "how do
I get this working for me" version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TWO CLUSTERS, NOT ONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Alpha — Grace ARM CPU nodes + NVIDIA RTX Pro 6000 GPUs. Good for
    single-GPU jobs. This is what `ssh empireai` connects to.

  Beta — newer, separate cluster: NVIDIA GB200 NVL72 SuperPOD,
    Blackwell B200 GPUs. Every job needs a MINIMUM of 4 GPUs — Beta
    is not for single-GPU work, use Alpha for that. This is what
    `ssh empireai-beta` connects to. Beta's nodes have a deliberately
    minimal software install — Empire AI's own docs say "all workloads
    should run within containers" via Pyxis/Enroot. Plain (non-
    container) jobs still run fine today, but don't plan on `module
    load`-ing scientific software stacks here going forward — see
    JOB SUBMISSION below for how containers actually work on Beta.

(Earlier internal notes said "H100/H200 GPUs" for Empire AI — that was
wrong, an unverified guess. Corrected above from Empire AI's own
sysadmin emails and direct verification on both clusters.)

Both use the same project account: ro_tolugboji_planetary (project
580). Put this in every sbatch script — see JOB SUBMISSION below.

Starting September 18, 2026, Alpha retires institution-based
partitions entirely — job submissions MUST use the project account
from that date on. Already confirmed live in Alpha's own login banner.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY THIS NEEDS A MANUAL STEP — READ THIS FIRST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Empire AI requires password + 2FA (authenticator app code) on every
login, on both clusters. Claude Code cannot complete a 2FA challenge —
there's no way around that, and we shouldn't try to work around it.
So Claude can never be the one to *open* the first connection.

What it CAN do is reuse a connection you already opened. SSH has a
feature called ControlMaster/ControlPersist: the first login (yours,
with 2FA) becomes a "master" connection, and every subsequent `ssh`/
`scp` that matches the same config entry reuses that same
authenticated socket — no new password, no new 2FA code — for as long
as the socket stays alive (we've set it to 12 hours of inactivity).

This is the exact same pattern already in use for Bluehive. If you've
used Bluehive from axon-1 before, this will feel familiar.

Important: the socket is scoped to YOUR axon-1 account's home
directory. My having a live connection does not give your Claude Code
session access — you each need to do this setup once, under your own
account, for EACH cluster (Alpha and Beta are separate sockets).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP — DO THIS ONCE PER ACCOUNT, PER CLUSTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You'll need your own Empire AI account first (password set, 2FA app
enrolled, project 580 allocation linked). If you don't have one yet,
let me know — I'll help you get that set up before you do the steps
below.

□ STEP 1 — Add both blocks to ~/.ssh/config in YOUR axon-1 home dir

  (SSH into axon-1 as yourself first: ssh wavenet-senior@10.17.6.243 or
  ssh wavenet-junior@10.17.6.243, then edit ~/.ssh/config there.)

    Host empireai alpha1.empireai.edu alpha.empire-ai.org alpha1.empire-ai.org
      HostName alpha1.empireai.edu
      User <your-empireai-username>
      ControlMaster auto
      ControlPath ~/.ssh/cm-%r@%h:%p
      ControlPersist 12h
      ServerAliveInterval 30
      ServerAliveCountMax 6

    Host empireai-beta beta.empireai.edu
      HostName beta.empireai.edu
      User <your-empireai-username>
      ControlMaster auto
      ControlPath ~/.ssh/cm-%r@%h:%p
      ControlPersist 12h
      ServerAliveInterval 30
      ServerAliveCountMax 6

  Replace <your-empireai-username> with your own Empire AI login — this
  is very likely NOT the same as your axon-1 username. Ask me if unsure.

□ STEP 2 — Open each master connection yourself, in your own terminal

    ssh empireai
    ssh empireai-beta

  Enter your password, then your 6-digit authenticator code, for each
  one. You should land on a welcome banner for each cluster. Leave
  those sessions open, or just exit them — the connections stay alive
  either way, for 12 hours of inactivity, per cluster.

  Known quirk (Alpha): the very first connection attempt sometimes
  fails with "Connection refused" even with everything configured
  correctly, then succeeds immediately on retry. This is flakiness on
  Empire AI's side (their support KB describes it), not a problem with
  your setup. Just try again once before assuming something's wrong.

□ STEP 3 — Confirm both sockets are live

    ssh -O check empireai
    ssh -O check empireai-beta

  Expected for each: "Master running (pid=...)"

□ STEP 4 — Start (or continue) your Claude Code session on axon-1

  From this point on, for the next 12 hours of inactivity on each
  socket, Claude Code running under your account can run
  `ssh empireai '...'` / `ssh empireai-beta '...'` and
  `scp ... empireai:...` directly — no prompts, no copy-pasting output
  back and forth.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JOB SUBMISSION — CONFIRMED PROTOCOL, PLAIN SLURM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Good news: you do NOT need Docker or any container to submit a job on
either cluster. A normal sbatch script works as-is — the only thing
that's non-optional is the account flag. Verified end-to-end on Alpha
(job ran and completed); verified as accepted/queued on Beta (job
submission mechanics, account, and QOS all confirmed working).

  Minimal Alpha example (ran successfully 2026-09-04):

    #!/bin/bash
    #SBATCH -J my_job
    #SBATCH -A ro_tolugboji_planetary
    #SBATCH -p alpha
    #SBATCH -N 1
    #SBATCH -n 1
    #SBATCH -t 00:02:00
    hostname
    date

  Minimal Beta example (4-GPU minimum applies):

    #!/bin/bash
    #SBATCH -J my_job
    #SBATCH -A ro_tolugboji_planetary
    #SBATCH -p beta
    #SBATCH -N 1
    #SBATCH --gres=gpu:b200:4
    #SBATCH -t 00:02:00
    nvidia-smi -L

  Submit either with: sbatch your_script.sbatch
  Check status with:  squeue -u <your-empireai-username>

□ Containers on Beta go through Slurm's Pyxis plugin, not `docker run`
  (Docker itself exists only as a module — it's not what actually runs
  your job). NGC images are one option; our OWN self-hosted registry
  at urseismogate.earth.rochester.edu works exactly the same way —
  confirmed working end-to-end 2026-09-04, including from an actual
  compute node (Beta's compute nodes have full outbound internet, so
  no relay/staging step is needed to reach our registry):

    srun --container-image=urseismogate.earth.rochester.edu/<image>:<tag> \
         --account=ro_tolugboji_planetary --partition=beta \
         --gres=gpu:b200:4 python train.py

  GOTCHA — hit this on our first real test, will hit you too if you
  push the normal modern way: if you build with
  `docker buildx build --push` (Docker's current default builder),
  it silently adds provenance/SBOM attestations and pushes an OCI
  multi-platform image INDEX instead of a plain manifest. Pyxis/enroot
  can't read that — the job fails with `MANIFEST_UNKNOWN: "OCI index
  found, but accept header does not support OCI indexes"` even though
  the image is right there in the registry. Fix: push with

    docker buildx build --provenance=false --sbom=false \
      -t urseismogate.earth.rochester.edu/<image>:<tag> --push .

  (or use the classic builder: `DOCKER_BUILDKIT=0 docker build ...`).
  Either avoids the OCI index and Pyxis pulls it fine. If a container
  job fails with MANIFEST_UNKNOWN, this is almost certainly why —
  check before assuming it's a registry or network problem.

  Use containers when you want a reproducible/pinned environment
  (increasingly expected on Beta, see above); plain Slurm is fine for
  everything else, including our current CPS/PyTorch stack on Alpha.

□ QOS gotcha: submitting with no --qos can leave a job sitting
  PD (Priority) with squeue --start showing N/A — no ETA at all, not
  just a long one. Add --qos=test for a quick sanity check (2h max
  wall time, higher scheduling priority than the default).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEEPING IT ALIVE / OTHER GOTCHAS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ Each socket dies after 12 hours with no traffic through it — just
    repeat Steps 2–3 for that cluster to refresh it. There's no way to
    make this fully unattended; a human has to be the one who does the
    2FA step, on each cluster, separately.
  ✓ If Claude Code reports a connection error partway through a
    session, the socket has likely expired — just log in again.
  ✓ For a long unattended job (training run that will outlive 12
    hours), don't rely on the live socket to fetch results afterward.
    Have the job write its output somewhere both sides can already
    reach without 2FA (e.g. push to repovibranium at the end of the
    job), the same pattern we already use for Bluehive.
  ✓ Beta's 4-GPU minimum is a real resource commitment on a shared
    cluster — even a 2-minute sanity job reserves a full node while
    queued. Billing is deferred until October 1, 2026, but please
    don't spin up test jobs casually; ask first if unsure.

  ✗ Don't share your ~/.ssh/config ControlPath or try to point two
    accounts at the same socket file — each account's socket is tied
    to that account's authenticated session.
  ✗ Don't script around the 2FA step (no auto-typing codes, no storing
    the authenticator secret anywhere) — that defeats the point of 2FA
    and Empire AI's own security monitoring will flag it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMPLIANCE — DON'T SKIP THIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Any paper, presentation, or grant application that uses either cluster
must include this acknowledgment:

  "We gratefully acknowledge use of the research computing resources
  of the Empire AI Consortium, Inc, with support from Empire State
  Development of the State of New York, the Simons Foundation, and the
  Secunda Family Foundation."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Questions, or if Step 1 (getting an Empire AI account) hasn't happened
yet for you — come find me or ask in the Claude Teams Project.

— Tolu
