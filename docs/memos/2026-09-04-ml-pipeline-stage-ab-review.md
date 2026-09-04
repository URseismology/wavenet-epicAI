Subject: WaveNet-EpicAI — New ML Pipeline (Stage A/B): Please Break It, Don't Just Run It

Hi Tejwaswini and Chris,

I've built the first two stages of a new, staged FTAN dispersion-curve U-Net pipeline —
consolidating the three scattered ML implementations that existed in this repo into one
documented location, verified against real data. This memo asks you to actually test it
yourselves and report back, not to take my word for it.

Full technical writeup: docs/ml_pipeline_stages/stage_a_data_definition.md and
stage_b_model_definition.md. This memo is the "please go poke holes in this" version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT EXISTS NOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  New location: src/wavenet_pipeline/03_machine_learning/
    (supersedes src/machine_learning/U_NET_array.py,
     src/data_processing/{h5_wavenet_tools,build_ml_dataset}.py, and
     chrisScripts/julyncf_pipeline/ML_pipeline/ — all three are marked superseded
     with pointers, not deleted, in case you need the history.)

  Two notebooks you can run TODAY, no HPC access, no 2FA, no terravibranium SSH:
    notebooks/stage_a_data_definition.ipynb   — FTAN regrid, mask construction,
                                                 family split, visualized
    notebooks/stage_b_model_definition.ipynb  — model architecture summary,
                                                 1-sample overfit sanity check

  They run against a small real-data tutorial set already committed:
    data_samples/tutorial_subset_sep{127,100}km.h5 + tutorial_manifest.csv
    (10 Earth models, 1 per family, both station geometries — pulled directly from
    the real production HDF5 files on terravibranium, not synthetic data)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT I NEED FROM YOU — DO NOT ASSUME THIS IS CORRECT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

I ran both notebooks myself before committing (jupyter nbconvert --execute, zero
errors) — but I ran them in my own ad-hoc environment that I hand-assembled this
session, on my own machine, hitting my own assumptions the whole way. That is exactly
the kind of single-reviewer blind spot that needs a second, independent pass. Please:

□ STEP 1 — Set up your OWN environment from scratch

  There is no requirements.txt / environment.yml yet (known gap, not an oversight —
  see stage_a_data_definition.md's Open Questions). You'll need to figure out your own
  numpy/scipy/h5py/pandas/pyarrow/pycwt/torch/matplotlib/torchinfo/jupyter install.
  This is itself a useful test: if you hit version conflicts I didn't (I hit a real
  numpy 2.x vs. torch 2.2.2 vs. pycwt incompatibility on my Intel Mac — documented in
  the stage doc, but your machine may behave differently), that's exactly the kind of
  thing to report back, not silently work around.

□ STEP 2 — Run both notebooks top to bottom yourself

    git pull
    jupyter notebook src/wavenet_pipeline/03_machine_learning/notebooks/

  Run every cell. Don't just read my already-executed outputs and assume a re-run would
  match — actually re-run them. If your results differ from what's already in the
  committed notebook (different numbers, different plot, different convergence
  behavior), that discrepancy matters more than either of us assuming the other is
  right.

□ STEP 3 — Actively question the results, don't just check "it ran without crashing"

  Specific things I want a second opinion on, not just a "looks fine":

  1. Stage A's FTAN images/masks (cell 5 of the notebook) — does the extracted mask
     actually track the visible ridge in the FTAN image, for all 4 sample families
     shown? I eyeballed this myself but a second set of eyes matters here, especially
     Tejwaswini's — this is the actual scientific content of the pipeline.

  2. Stage A's QA-gate finding (in the stage doc, not the notebook) — the precomputed
     FTAN and an independent recompute disagree by up to ~0.10 km/s in the worst cases,
     34% exceeded my proposed 0.05 km/s flag threshold, even though pixel correlation
     stays 0.93-0.96. I concluded this is normal cross-method variation (different CWT
     parameters), not a bug — but I want that conclusion actively challenged, not
     rubber-stamped. If you disagree, say so.

  3. Stage B's overfit-convergence finding — at lr=1e-4 (the plan's assumed training
     config) a single sample doesn't converge in the ~50 iterations we expected; it
     needed lr=3e-3 and ~800 iterations. I've flagged this as a real risk for Stage D's
     full training schedule. Does this match your intuition for this loss composition
     (Focal+Dice+WeightedBCE+Sharpening), or does it suggest something is actually off
     with the loss weighting rather than just "needs more iterations"?

  4. The family-based 7/2/1 train/val/test split (only 10 families total, so this is
     coarse) — is this an acceptable evaluation design for a real paper/report, or does
     it need rethinking before Stage D produces numbers anyone will cite?

□ STEP 4 — Tell me what I MISSED, not just what I got wrong

  I verified everything I thought to verify. I did not verify everything there is to
  verify. If something about the FTAN physics, the mask width choice, the loss
  function, the split design, or anything else looks off given your own domain
  knowledge — even if you can't immediately prove it's wrong — raise it. New
  information I didn't have when writing the plan is exactly what this review is for.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO REPORT BACK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ Post findings in the Claude Teams Project (so it's visible to all three of us),
    OR add directly to the relevant stage doc's OPEN QUESTIONS FOR PI field and commit
    it — either is fine, whichever is faster for you.
  ✓ "I ran it, got the same numbers, no concerns" is a completely valid and useful
    report — I'm not fishing for problems that aren't there, just asking for an actual
    independent check rather than a courtesy skim.
  ✓ If your environment setup hits different issues than mine, please paste the exact
    error — that directly feeds into writing the real requirements.txt/environment.yml,
    which is next up after this review.
  ✗ Please don't just reply "LGTM" without actually running it — that defeats the
    purpose of asking for a second pass at all.

No fixed deadline on this, but I'd like to hear back before we push into Stage C
(input batching) — no sense building further on a foundation nobody but me has
actually kicked the tires on.

— Tolu
