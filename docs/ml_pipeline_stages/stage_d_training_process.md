━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE D — TRAINING PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  UNetSeg + CombinedLoss (Stage B), fed by the Stage C DataLoaders over the full
  20,000-sample production cache, trains to a stable, reproducible model on a single
  H100: the learning rate can be chosen from a short sweep, the selected checkpoint is
  not an artefact of one noisy validation epoch, and the held-out score is reported
  once, on a family the model never saw.

SETUP
  Code: train.py, eval_by_family.py, slurm/{lr_sweep,lr_sweep_long,final_train}_bluehive3.sbatch
  (commits 04c39d8 and 318d144). The final run's job log records sha256 of dataset.py,
  stage_c_input_batching.py, train.py and eval_by_family.py; all four match 318d144
  (config.json says git_commit "unknown" because the Bluehive3 code copy is an rsync,
  not a clone — the sha256 lines are the provenance).
  Data: FTAN_ML_CACHE/full_production_cache.h5 (20,000 samples = 10,000 models x
  2 geometries, sep 127 and 100 km) with splits_seed42_full.json, split by family:
  train 14,000 (M01, M03-M08), val 4,000 (M02, M10), test 2,000 (M09).
  Hardware: Bluehive3 `coe` partition, account nkoziol_lab, 1x H100 NVL per run,
  torch 2.4.0 (module pytorch/2.4 + venv /scratch/scratch/tbalamur/envs/wavenet-ml).
  Fixed across every run: batch 16, AdamW weight decay 1e-4, ReduceLROnPlateau
  (factor 0.5, min 1e-6), threshold 0.5 for IoU/Dice, ~12 s train + 1 s val per epoch.
  Final config: LR 3e-3, up to 100 epochs, plateau patience 5, early-stop patience 25,
  gradient clipping at global L2 norm 1.0, best.pt chosen on the 5-epoch trailing mean
  of val IoU (the plateau scheduler and early stopping track the same smoothed value),
  seeds 42/43/44. --seed changes weight init and shuffle order only; the split is fixed.

METRIC DEFINITIONS
  IoU / Dice: pixel counts at threshold 0.5 (sigmoid > 0.5 vs target > 0.5), summed over
  the whole split (or family) before dividing — dataset-level, not a mean of per-sample
  or per-batch scores. All 80 rows count, including the 4 always-empty pad rows.

  pred/tgt: predicted positive pixels / target positive pixels over the split. 1.0 means
  the predicted band is on average as wide as the target band.

  velocity_rmse (km/s) — metrics.velocity_error, exactly:
    1. Rows: the 76 curve rows only (period 1.00 to 19.75 s in 0.25 s steps); the 4 pad
       rows are excluded. Every period row is used — there is no period weighting and
       no period cut.
    2. Predicted curve: for each period row, the probability-weighted centroid of the
       *unthresholded* sigmoid output over the 300 velocity bins,
         v_pred = 2.0 + 0.01 * sum_j(p_j * j) / (sum_j p_j + 1e-8),  j = 0..299
       So it is a soft centroid of the whole row, not a curve picked from the
       thresholded mask and not an argmax; low background probability far from the
       curve still pulls the centroid (toward the middle of the 2-5 km/s range).
    3. Reference curve: the same centroid of the target mask row. The target mask is
       the CPS *theoretical* fundamental-mode group-velocity curve (isolate_fundamental_mode,
       interpolated onto the period grid) rasterised as a 5-bin band (centre bin +-2,
       ftan_grid.build_target_mask), so its centroid is the theoretical curve
       quantised to the 0.01 km/s bin grid (within +-0.005 km/s; the rasteriser maps
       velocity to bins with a 299/300 scale, so bin units differ from km/s by 0.3%,
       negligible here). It is not compared against the FTAN energy ridge.
       If a row had no theoretical value its target centroid would fall back to
       2.0 km/s and still be scored; checked on the cache: val and test have no such
       rows, train has 2 samples with any (10 rows out of 1.06 M).
    4. Aggregation: RMSE over all rows of a batch (16 samples x 76 rows), then the mean
       of those per-batch RMSEs weighted by batch size. This is what train.py logs and
       what val_eval.json / test_eval.json report. Because it averages RMSEs rather than
       squared errors it is lower than the RMSE pooled over every row
       (heldout_analysis.py reports both — see RESULTS); the gap is largest on test,
       meaning the velocity error is concentrated in a minority of samples.
  Stage E will report curves with ftan_grid.weighted_centroid_curve (NaN for empty rows,
  intended for scientific reporting). velocity_rmse is a training-time monitor and
  should be described that way in any write-up.

WHAT WAS TRIED
  - Round 1 LR sweep (job 2019070, 2026-09-30): LR 1e-4 / 1e-3 / 3e-3, 15 epochs, no
    scheduler or early stopping (patience > budget), one seed each. Purpose: check
    every LR trains at all on the full cache and compare early convergence.
  - Per-family val diagnostic (eval_by_family.py, job 2091854): showed M02 is harder
    than M10 at every LR. train.py was changed to log val.by_family every epoch.
  - Round 2 LR sweep (job 2091906, 2026-10-08): LR 1e-4 / 3e-4 / 3e-3, up to 100
    epochs, plateau patience 5, early-stop patience 15 on raw val IoU, one seed each.
    All three early-stopped. Raw val IoU jumped by 0.20-0.46 between consecutive
    epochs at some point in every run (the largest jumps early in training), and
    1e-4's best raw epoch (0.649, epoch 28) came with vRMSE 0.049, about twice what
    3e-3 reached; 1e-4's later epochs got worse (last-10-epoch IoU sd 0.073 vs 0.008
    for 3e-3).
  - Gradient-norm probe (job 2092023, 2026-10-08): at 3e-3 the mean global grad norm
    was 0.14-0.41 and a clip at 1.0 fired on <5% of steps, only in epoch 0. Adopted
    clip 1.0 as a spike guard, not a hidden LR cut.
  - Smoothed selection (commit 318d144): best.pt, the plateau scheduler and early
    stopping all track the 5-epoch trailing mean of val IoU, so a single noisy epoch
    can neither pick the checkpoint nor end the run. Early-stop patience raised to 25.
  - Final run (job 2107428, 2026-10-11): LR 3e-3, seeds 42/43/44, then each seed's
    best.pt scored on val and, once, on test. The sbatch refuses to reuse an output
    directory, so test cannot be re-scored by accident.
  - Held-out analysis (heldout_analysis.py, job 2107750): per-sample IoU, pred/tgt and
    pooled velocity RMSE for each seed's best.pt on val + test, plus example predictions
    at the 10th/50th/90th percentile of per-sample IoU per family for the figures.
    Re-running metrics.velocity_error with the logged aggregation reproduced every
    logged val/test vRMSE to 4 decimals, confirming the definition above.

RESULTS
  LR sweep (val IoU, one seed per LR; "smoothed" = best 5-epoch trailing mean, the
  final run's selection rule, computed afterwards from the logs):

  | Round | LR   | Epochs run | Best raw val IoU (epoch) | Best smoothed | val vRMSE at raw best |
  |-------|------|-----------:|--------------------------|--------------:|----------------------:|
  | 1     | 1e-4 | 15         | 0.632 (14)               | 0.587         | 0.026                 |
  | 1     | 1e-3 | 15         | 0.581 (14)               | 0.548         | 0.030                 |
  | 1     | 3e-3 | 15         | 0.605 (14)               | 0.547         | 0.024                 |
  | 2     | 1e-4 | 44         | 0.649 (28)               | 0.578         | 0.049                 |
  | 2     | 3e-4 | 30         | 0.613 (14)               | 0.579         | 0.035                 |
  | 2     | 3e-3 | 64         | 0.647 (48)               | 0.642         | 0.024                 |

  **The sweep did not separate the learning rates beyond seed noise.** At a fixed LR
  (3e-3, final run below) the three seeds' best val IoU spans 0.625-0.665, a range of
  0.04 (sd 0.021). The round-1 ordering (1e-4 best) reversed in round 2, and every
  round-2 raw best lies inside a 0.04-wide band. The one gap larger than that — 3e-3's
  smoothed 0.642 vs ~0.58 for the others — comes from one seed per LR under an
  early-stopping rule driven by raw, noisy val IoU, which stopped 1e-4 and 3e-4 after
  their scores had started to fall. What round 2 does show is that 3e-3 was the only
  LR that settled (last-10-epoch sd 0.008) under that protocol. 3e-3 was chosen for that
  stability, not because it was shown to reach a higher IoU.

  Final run, LR 3e-3, selected checkpoint per seed (val_eval.json / test_eval.json):

  | Metric            | seed 42 | seed 43 | seed 44 | mean +- sd       |
  |-------------------|--------:|--------:|--------:|------------------|
  | Epochs run        | 93 (early stop) | 100 (cap) | 100 (cap) |        |
  | Selected epoch    | 67      | 92      | 77      |                  |
  | Val IoU           | 0.625   | 0.665   | 0.632   | 0.641 +- 0.021   |
  |   M02 IoU         | 0.547   | 0.590   | 0.543   | 0.560 +- 0.026   |
  |   M10 IoU         | 0.712   | 0.745   | 0.731   | 0.729 +- 0.017   |
  | Val Dice          | 0.769   | 0.798   | 0.775   | 0.781 +- 0.016   |
  | Val vRMSE (logged)| 0.0226  | 0.0202  | 0.0232  | 0.0220 +- 0.0016 |
  |   M02 vRMSE       | 0.0290  | 0.0265  | 0.0315  | 0.0290 +- 0.0025 |
  |   M10 vRMSE       | 0.0161  | 0.0139  | 0.0149  | 0.0150 +- 0.0011 |
  | Val vRMSE (pooled)| 0.0249  | 0.0226  | 0.0258  | 0.0244 +- 0.0017 |
  | Val pred/tgt      | 1.13    | 1.09    | 1.10    |                  |
  | **Test (M09) IoU**| 0.767   | 0.792   | 0.789   | **0.783 +- 0.014** |
  | Test Dice         | 0.868   | 0.884   | 0.882   | 0.878 +- 0.009   |
  | Test vRMSE (logged)| 0.0172 | 0.0151  | 0.0179  | 0.0167 +- 0.0015 |
  | Test vRMSE (pooled)| 0.0251 | 0.0230  | 0.0277  | 0.0253 +- 0.0024 |
  | Test pred/tgt     | 1.09    | 1.06    | 1.07    |                  |
  | Train IoU, last epoch | 0.865 | 0.885 | 0.870   |                  |
  | Train pred/tgt, last epoch | 1.07 | 1.05 | 1.06  |                  |

  sd is the sample sd over the 3 seeds. Per-sample IoU varies far more within a family
  than across seeds: for seed 44 the 10th-90th percentile range is 0.37-0.76 on M02,
  0.52-0.88 on M10 and 0.65-0.91 on M09 (stage_d_results/final_lr3e-3_2107428/
  heldout_analysis.json). Training was clean throughout: no non-finite losses, grad
  clipping fired only in epoch 0 (plus once in seed 42's epoch 43), and the selected epoch's re-scored val
  metrics match the logged ones exactly.

  Figures (white background, 300 DPI, made by plot_stage_d.py):
  - figures/stage_d_training_curves.png — train/val loss, IoU and val vRMSE per epoch
    for the three seeds, selected checkpoint marked.
  - figures/stage_d_family_iou.png — per-family IoU, mean +- sd with each seed shown.
  - figures/stage_d_examples.png — FTAN input with the true and predicted masks for an
    easy (M10, 90th percentile), typical (M09, 50th) and hard (M02, 10th) sample. In the
    hard sample the prediction follows the theory curve to ~12 s and then drifts below it
    at longer periods.

  Run outputs (checkpoints, logs): Bluehive3
  /scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/{lr_sweep_2019070,
  lr_sweep_long_2091906,final_lr3e-3_2107428}; SLURM logs in FTAN_ML_RUNS/logs/.
  Copies of every config.json, train_log.jsonl and eval JSON (no checkpoints) are in
  docs/ml_pipeline_stages/stage_d_results/.

HARDWARE TIER LOG
  | Tier                | Status | Date       | Job ID | Log link |
  |---------------------|--------|------------|--------|----------|
  | Local               | not run as a tier (axon-1 has no GPU) | | n/a | |
  | Bluehive3 (H100)    | PASS   | 2026-10-11 | 2107428 (final), 2019070 / 2091906 (sweeps), 2092023 (clip probe), 2107750 (held-out analysis) | FTAN_ML_RUNS/logs/final_2107428_{0,1,2}.out, stage_d_results/ |
  | terravibranium-gpu  | not started | | | |
  | Alpha               | not started | | | |
  | Beta                | not started | | | |

DECISION
  Canonical Stage D configuration: LR 3e-3, AdamW wd 1e-4, batch 16, grad clip 1.0,
  plateau patience 5, early-stop patience 25, checkpoint selection on the 5-epoch
  trailing mean of val IoU. Report held-out results per family and as mean +- sd over
  3 seeds, never as a single seed or a single pooled number. Reference result: test
  (M09) IoU 0.783 +- 0.014, val IoU 0.641 +- 0.021 (M02 0.560, M10 0.729). Test has now
  been scored once per seed; any further tuning must be judged on val only, and these
  test numbers stay the reported ones for this configuration.

OPEN QUESTIONS FOR PI
  1. Test is a single family. M09 is the whole test split, and it scores well above both
     val families (0.783 vs 0.560 / 0.729), so the test number reflects how easy M09
     is as much as how well the model generalises. Should the final benchmark rotate
     the held-out family (e.g. leave-one-family-out over a few folds), or at least
     report test alongside per-family val as is done here?
  2. Train/val gap. Train IoU ~0.87 vs val ~0.64 (the train figure is accumulated
     during the training pass, not a separate eval, but the gap is far larger than that
     could explain). Families
     differ in a way the model does not transfer across, M02 most of all. Options:
     augmentation (velocity shifts, noise, amplitude), stronger regularisation, or more
     families in training. Which, if any, before Stage E?
  3. Mask width bias of 6-13%. The predicted band is consistently wider than the 5-bin
     target: pred/tgt 1.09-1.13 on val, 1.06-1.09 on test, and 1.05-1.07 on train too.
     Because it appears on train, it is most likely from the loss (WeightedBCELoss
     pos_weight = 15 rewards extra positives), not from generalisation. It costs IoU
     but barely moves the centroid curve. Fixing it means either raising the decision
     threshold (tuned on val) or changing the loss weights — both are ML-regime
     decisions that need your sign-off per CLAUDE.md.
  4. velocity_rmse as defined is a soft-centroid, batch-averaged training monitor (see
     METRIC DEFINITIONS). For the AkiNet/iRADNet benchmark, should Stage E's reported
     velocity error instead use a curve picked from the thresholded mask and pooled over
     all rows, compared against the theoretical curve at full precision?
  5. Seed 43 was still creeping up when it reached the 100-epoch cap (selected epoch 92;
     smoothed val IoU +0.002 over its last 10 epochs). Seed 44 also hit the cap but had
     levelled off, and seed 42 early-stopped at 93. The effect is small, but a 150-epoch
     budget would remove the question.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
