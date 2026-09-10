Subject: WaveNet-EpicAI — NVIDIA Workshop Notes: New Alpha Details + FAQ/Tutorial for You

Hi Tejwaswini and Chris,

I sat in on NVIDIA's Kickstart Workshop for Empire AI Alpha today, pulled the full
multi-part workshop series (not just one link — a Slurm cheat sheet, a PyTorch+Slurm
walkthrough, and three more linked Alpha docs on QoS, interactive sessions, and
Jupyter), caught the tail end of the live Q&A, and then went and directly tested
several things myself on Alpha rather than just trusting the docs. This turned up real
new facts we didn't have before, plus two documents for you to actually use.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEW FACTS WORTH KNOWING (already folded into CLAUDE.md)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Alpha's real GPU inventory, now fully resolved TWO independent ways (live in the
     workshop Q&A, AND directly via `sinfo` on Alpha itself): 224 GPUs across 28 nodes —
     H100 80GB on alphagpu01-18 (8/node), H200 141GB on alphagpu19-24 (8/node), and
     NVIDIA RTX PRO 6000 Blackwell on alphagpu51-54 (8/node = 32 total), single-GPU jobs
     only. This was an open question this morning (we only had an unconfirmed sysadmin
     email with no count) — now fully confirmed, including that it's the newer
     Blackwell generation specifically (a workshop slide's "A6000" shorthand briefly had
     people wondering if it meant the older Ampere card instead — Slurm's own gres
     string, rtx_pro_6000_blackwell, settles it).

  2. Real QoS tiers exist and matter — test/interactive/standard/long/priority/burst,
     each with different wall-time limits, GPU caps, and SU cost multipliers (test and
     long are half-price, priority is double). Full table in the FAQ. We'd only ever
     used --qos=test empirically before; now we know why and when to use the others.

  3. Per-hardware SU billing rates, from the Q&A (separate axis from the QoS multiplier
     above, exact combination not yet verified against the written docs): Beta's
     GB200s = 2 SU per GPU-hour, Alpha = 1 SU per GPU-hour, Grace nodes = 0.5 SU/hour.
     Billing starts Oct 1 either way (already knew the date).

  4. Starting Sept 18 (already knew the date, didn't know the detail): the "institutional
     partitions retire" transition isn't just "add --account" — the documented future
     pattern pairs --account WITH an explicit --qos. Submitting with account but no qos
     may not be the full fix once that date hits.

  5. Alpha and Grace are different CPU architectures — Alpha/cpu partitions are x86_64,
     Grace is ARM64. Any environment (venv, compiled dependency) built for one will NOT
     run on the other. This wasn't documented anywhere in our repo before.

  6. A concrete, Empire-AI-documented PyTorch install command for Alpha:
     pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
     Directly relevant to our still-open "no pinned PyTorch/CUDA environment" question
     from the ML pipeline plan (docs/ml_pipeline_stages/stage_b_model_definition.md).

  7. Two links worth bookmarking: the public status/maintenance page
     (https://rootly.com/teams/empireai/status-pages/public-system-status-page/public
     — check here before assuming a connection problem is on our end) and the
     Freshdesk solutions home (https://empireai.freshdesk.com/support/solutions —
     the actual source of truth; our FAQ is a curated excerpt, not a replacement).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
APPTAINER — I TESTED THIS MYSELF, NOT JUST READ THE DOCS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Someone asked whether we can build our own containers for Alpha. Rather than answer
  from the docs alone, I actually did it:

  1. Confirmed a real gotcha that isn't in the workshop material: running a container
     (`apptainer exec`/`run`) FAILS on the login node — it's missing squashfuse, and
     that's a hard FATAL error there, not the harmless INFO line the cheat sheet implies.
     You have to be inside an actual compute-node allocation (salloc/srun) to run one.
     Pulling/building an image, on the other hand, works fine on the login node.
     Reproduced this consistently across two different images.

  2. Confirmed building your own custom container from scratch works cleanly — write a
     small text file (a `.def`), one `apptainer build --fakeroot` command, done. No
     Docker, no registry push/pull needed at all. This is a genuinely simpler path than
     our self-hosted registry for Alpha-only work.

  3. Resolved: Apptainer CAN pull our own self-hosted registry image (`spec2vec`, the
     one that failed on Beta with a MANIFEST_UNKNOWN error) — no manifest problem at
     all on Alpha, unlike Beta. But it surfaced a real gotcha: this particular image is
     7.4GB, and pulling/converting it to a `.sif` on the **login node** got killed by
     what looks like an out-of-memory limit after climbing past 4-5GB of RAM (took over
     an hour before dying — not obviously a hang, easy to misjudge as one). Fix: run the
     pull inside an actual compute-node allocation with enough `--mem` instead
     (`srun ... --mem=32G ...`) — worked cleanly there, image builds and runs fine
     (`python3 --version` inside it: 3.14.6, conda-based). Side finding: this specific
     `spec2vec`-tagged image turned out to just be a generic Anaconda base distribution,
     not the actual spec2vec package — worth checking if that's a mislabeled/placeholder
     push in our registry, not something for you to debug.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TWO NEW DOCUMENTS FOR YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  docs/empireai_alpha_slurm_faq.md
    Reference doc — sbatch/salloc/srun differences, the full QoS table, GPU request
    patterns, the Apptainer login-node-vs-compute-node gotcha above, the custom-build
    recipe, common error messages, Jupyter/SSH tunnel setup. Clearly flags which parts
    of the original workshop material are workshop-only (temporary account/reservation/
    pinned nodes) vs. general knowledge to use with our real account.

  docs/empireai_alpha_slurm_tutorial.md
    A ~15-minute hands-on walkthrough using our actual project account
    (ro_tolugboji_planetary) — confirm your access, run a GPU sanity check, install
    PyTorch, submit a real batch job, build your own container from a `.def` file, and
    (optional but worth doing) tunnel Jupyter in and run our Stage A/B tutorial
    notebooks against real Alpha GPU hardware instead of just your laptop CPU.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT I'M ASKING YOU TO DO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

□ Read the FAQ once, then actually run the tutorial yourself — don't just skim it.
  Same spirit as the Stage A/B review ask from last week: report back what actually
  happened on your session, not what you assume should happen.

□ Specifically tell me:
  - Did `sacctmgr show assoc` show ro_tolugboji_planetary with alpha partition access
    for your account the way the tutorial expects?
  - Which GPU (H100, H200, or one of the 4 RTX Pro 6000 nodes) did you land on?
  - Did the documented PyTorch pip install command work cleanly for you?
  - If you try building your own `.def` container — did it work the same way for you?
  - If you do the optional Jupyter step and run our Stage A/B notebooks on real Alpha
    hardware — do the numbers (1.67% mask positive-fraction, overfit convergence) match
    what's already committed, or does real GPU hardware change anything?

□ If you spot anything in the FAQ/tutorial that's wrong, outdated, or missing — this
  was compiled from public Freshdesk docs, one workshop session, and my own testing,
  not exhaustive hands-on testing by either of you yet. Flag it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

No fixed deadline, but this is good groundwork before we push our ML pipeline work past
Stage A/B onto real Alpha hardware — the more of us who've actually driven Alpha
ourselves before then, the better.

— Tolu
