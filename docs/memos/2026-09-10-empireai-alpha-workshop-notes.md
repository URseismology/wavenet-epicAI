Subject: WaveNet-EpicAI — NVIDIA Workshop Notes: New Alpha Details + FAQ/Tutorial for You

Hi Tejwaswini and Chris,

I sat in on NVIDIA's Kickstart Workshop for Empire AI Alpha today and pulled the full
multi-part workshop series (not just the one link I first grabbed — there's a Slurm
cheat sheet, a PyTorch+Slurm walkthrough, and three more linked Alpha docs covering
QoS, interactive sessions, and Jupyter). This turned up real new facts about Alpha we
didn't have before, and I've written them up plus two documents for you to actually use.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEW FACTS WORTH KNOWING (already folded into CLAUDE.md)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Alpha's real GPU inventory: 192 GPUs across 24 nodes, 8/node — H100 80GB on
     alphagpu01-18, H200 141GB on alphagpu19-24. Our existing docs also mention RTX Pro
     6000 GPUs on Alpha (from an earlier sysadmin email) that this particular source
     doesn't cover — likely a newer addition, not a contradiction, but I haven't
     confirmed that. If either of you talks to Empire AI support before I do, ask.

  2. Real QoS tiers exist and matter — test/interactive/standard/long/priority/burst,
     each with different wall-time limits, GPU caps, and SU cost multipliers (test and
     long are half-price, priority is double). Full table in the FAQ. We'd only ever
     used --qos=test empirically before; now we know why and when to use the others.

  3. Starting Sept 18 (already knew the date, didn't know the detail): the "institutional
     partitions retire" transition isn't just "add --account" — the documented future
     pattern pairs --account WITH an explicit --qos. Submitting with account but no qos
     may not be the full fix once that date hits.

  4. Alpha and Grace are different CPU architectures — Alpha/cpu partitions are x86_64,
     Grace is ARM64. Any environment (venv, compiled dependency) built for one will NOT
     run on the other. This wasn't documented anywhere in our repo before.

  5. Alpha's container mechanism is Apptainer (.sif images), completely different from
     Beta's Pyxis/Enroot. If you're used to Beta's --container-image= flag from the
     Empire AI memo, don't reach for it on Alpha — see the FAQ for the actual recipe.

  6. A concrete, Empire-AI-documented PyTorch install command for Alpha:
     pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
     This is directly relevant to our still-open "no pinned PyTorch/CUDA environment"
     question from the ML pipeline plan (docs/ml_pipeline_stages/stage_b_model_definition.md).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TWO NEW DOCUMENTS FOR YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  docs/empireai_alpha_slurm_faq.md
    Reference doc — sbatch/salloc/srun differences, the full QoS table, GPU request
    patterns, Apptainer recipe, common error messages and what they mean, Jupyter/SSH
    tunnel setup. Written to clearly flag which parts of the original workshop material
    are workshop-only (their temporary account/reservation/pinned nodes) vs. general
    knowledge you should use with our real account.

  docs/empireai_alpha_slurm_tutorial.md
    A ~15-minute hands-on walkthrough using our actual project account
    (ro_tolugboji_planetary) — confirm your access, run a GPU sanity check, install
    PyTorch, submit a real batch job, and (optional but worth doing) tunnel Jupyter in
    and run our existing Stage A/B tutorial notebooks against real Alpha GPU hardware
    instead of just your laptop CPU.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT I'M ASKING YOU TO DO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

□ Read the FAQ once, then actually run the tutorial yourself — don't just skim it.
  Same spirit as the Stage A/B review ask from last week: report back what actually
  happened on your session, not what you assume should happen.

□ Specifically tell me:
  - Did `sacctmgr show assoc` show ro_tolugboji_planetary with alpha partition access
    for your account the way the tutorial expects?
  - Which GPU (H100 or H200, which node) did you land on?
  - Did the documented PyTorch pip install command work cleanly for you?
  - If you do the optional Jupyter step and run our Stage A/B notebooks on real Alpha
    hardware — do the numbers (1.67% mask positive-fraction, overfit convergence) match
    what's already committed, or does real GPU hardware change anything?

□ If you spot anything in the FAQ/tutorial that's wrong, outdated, or missing — this
  was compiled from public Freshdesk docs plus one workshop session, not exhaustive
  hands-on testing by either of you yet. Flag it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

No fixed deadline, but this is good groundwork before we push our ML pipeline work past
Stage A/B onto real Alpha hardware — the more of us who've actually driven Alpha
ourselves before then, the better.

— Tolu
