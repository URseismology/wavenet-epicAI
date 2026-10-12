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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE D FOLLOW-UP — POS_WEIGHT AND PER-ROW-PEAK DECODER (job 2109273, VAL ONLY)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  (a) The 6-13% mask width bias (open question 3 above) comes from WeightedBCELoss's
  pos_weight 15; lowering it narrows the predicted band. (b) The soft-centroid vRMSE is
  inflated by probability mass away from the curve; reading each row at its peak lowers
  the velocity error. Both judged on val only. Test was not scored in this experiment.

SETUP
  Branch stage-d-ablations, code commit e676a7c (git_commit in each config.json; the
  Bluehive3 rsync copy was sha256-checked against e676a7c before submission).
  Array job 2109273 (slurm/posweight_ablation_bluehive3.sbatch), coe, 1 H100 per task:
    A  pos_weight 15: final_lr3e-3_2107428 seed_{42,43,44}/best.pt re-scored on val
       with the new decoder, no retraining (that directory was only read)
    B  pos_weight 5, seeds 42/43/44, trained from scratch
    C  pos_weight 1, seeds 42/43/44, trained from scratch
  B and C use the final run's recipe unchanged (LR 3e-3, up to 100 epochs, plateau
  patience 5, early-stop patience 25, grad clip 1.0, best.pt on the 5-epoch trailing
  mean of val IoU). Only train.py --pos-weight differs. Checkpoint selection is still
  on val IoU; neither vRMSE variant was used to select anything.

  Per-row-peak decoder (metrics.peak_row_velocity): per period row, the argmax velocity
  bin of the sigmoid output, refined by the probability-weighted mean bin over +-3 bins
  around it (clipped at the grid edges). The reference is the same target-mask centroid
  over the same 76 rows as velocity_rmse, and metrics.velocity_error is unchanged.
  Each decoder is reported two ways: per-batch (mean of per-batch RMSEs, weighted by
  batch size, which is what train.py logs) and pooled (RMSE over every row of every val
  sample, as heldout_analysis.py computes it).

  Check: A's re-scored soft vRMSE reproduces the final run's logged values exactly
  (0.0226 / 0.0202 / 0.0232 per-batch; pooled 0.0249 / 0.0226 / 0.0258, the same as
  heldout_analysis.json), so the new code did not move the existing metric.

RESULTS (val, selected checkpoint, mean +- sd over seeds 42/43/44)

  All val (M02 + M10, n = 4000):
  | Config        | IoU             | Dice            | pred/tgt        | vRMSE soft batch / pooled           | vRMSE peak batch / pooled           |
  |---------------|-----------------|-----------------|-----------------|-------------------------------------|-------------------------------------|
  | A  pw 15      | 0.641 +- 0.021  | 0.781 +- 0.016  | 1.108 +- 0.023  | 0.0220 +- 0.0016 / 0.0244 +- 0.0016 | 0.0224 +- 0.0021 / 0.0249 +- 0.0025 |
  | B  pw 5       | 0.643 +- 0.012  | 0.782 +- 0.009  | 1.047 +- 0.013  | 0.0223 +- 0.0011 / 0.0245 +- 0.0015 | 0.0227 +- 0.0013 / 0.0250 +- 0.0017 |
  | C  pw 1       | 0.614 +- 0.024  | 0.761 +- 0.018  | 0.983 +- 0.002  | 0.0296 +- 0.0043 / 0.0344 +- 0.0087 | 0.0314 +- 0.0067 / 0.0383 +- 0.0139 |

  M02 (n = 2000):
  | Config        | IoU             | Dice            | pred/tgt        | vRMSE soft batch / pooled           | vRMSE peak batch / pooled           |
  |---------------|-----------------|-----------------|-----------------|-------------------------------------|-------------------------------------|
  | A  pw 15      | 0.560 +- 0.026  | 0.718 +- 0.021  | 1.101 +- 0.032  | 0.0290 +- 0.0025 / 0.0305 +- 0.0023 | 0.0298 +- 0.0037 / 0.0313 +- 0.0036 |
  | B  pw 5       | 0.571 +- 0.019  | 0.727 +- 0.016  | 1.038 +- 0.014  | 0.0293 +- 0.0027 / 0.0304 +- 0.0028 | 0.0302 +- 0.0028 / 0.0313 +- 0.0029 |
  | C  pw 1       | 0.549 +- 0.043  | 0.708 +- 0.037  | 0.981 +- 0.008  | 0.0315 +- 0.0045 / 0.0329 +- 0.0044 | 0.0332 +- 0.0045 / 0.0350 +- 0.0049 |

  M10 (n = 2000):
  | Config        | IoU             | Dice            | pred/tgt        | vRMSE soft batch / pooled           | vRMSE peak batch / pooled           |
  |---------------|-----------------|-----------------|-----------------|-------------------------------------|-------------------------------------|
  | A  pw 15      | 0.729 +- 0.017  | 0.843 +- 0.011  | 1.115 +- 0.014  | 0.0150 +- 0.0011 / 0.0162 +- 0.0013 | 0.0150 +- 0.0007 / 0.0162 +- 0.0008 |
  | B  pw 5       | 0.720 +- 0.007  | 0.837 +- 0.005  | 1.057 +- 0.013  | 0.0153 +- 0.0006 / 0.0167 +- 0.0010 | 0.0151 +- 0.0006 / 0.0165 +- 0.0010 |
  | C  pw 1       | 0.687 +- 0.002  | 0.814 +- 0.001  | 0.985 +- 0.008  | 0.0277 +- 0.0076 / 0.0349 +- 0.0155 | 0.0295 +- 0.0108 / 0.0400 +- 0.0228 |

  Per seed (all val): IoU A 0.625/0.665/0.632, B 0.635/0.656/0.637, C 0.627/0.630/0.587;
  pred/tgt A 1.13/1.09/1.10, B 1.03/1.05/1.06, C 0.98/0.98/0.98; soft vRMSE per-batch
  A 0.0226/0.0202/0.0232, B 0.0219/0.0215/0.0236, C 0.0335/0.0250/0.0303.
  Training: B ran the full 100 epochs on every seed (selected epochs 99/99/95); C seeds
  42/43 ran 100 (selected 95/97) and seed 44 early-stopped at 91 (selected 65). Both
  were essentially flat at the cap: the tracked val IoU rose by at most 0.007 over the
  last 20 epochs, well under seed noise. Two C seeds predicted an empty mask for their
  first 1-2 epochs before recovering. No errors or non-finite losses.

  What exceeds seed noise (sd ~0.02 val IoU; per-seed ranges compared directly):
  - Per-row-peak decoder: NO improvement. On every configuration it is equal to or
    slightly worse than the soft centroid (A: 0.0224 vs 0.0220 per-batch, 0.0249 vs
    0.0244 pooled; per seed +0.0001 / 0.0000 / +0.0011), all within seed noise. The
    velocity error is not coming from mass spread away from the curve. It comes from
    where the band sits, which a different read-out of the same probabilities cannot fix.
  - pos_weight 5: narrows the mask beyond noise (pred/tgt 1.108 -> 1.047; the seed ranges
    1.09-1.13 and 1.03-1.06 do not overlap; the width bias is roughly halved, in both
    families). IoU (+0.002), Dice (+0.002) and both vRMSE variants (+0.0003 / +0.0001)
    do not change beyond noise, overall or per family (M02 IoU +0.011, M10 IoU -0.010,
    each inside its seed sd). It is narrower at no measured cost, but not better on IoU or vRMSE.
  - pos_weight 1: removes the width bias (0.98, now ~2% too narrow) but COSTS accuracy.
    Velocity error rises beyond noise: per-batch 0.0220 -> 0.0296; every C seed
    (0.0250-0.0335) is worse than every A seed (0.0202-0.0232). The rise is concentrated
    in M10 (0.0150 -> 0.0277), where pooled is much higher than per-batch (0.0349), so it
    comes from a minority of samples with large row errors, worst in seed 42. M10 IoU
    falls beyond noise (0.729 -> 0.687; all C seeds 0.685-0.688, below every A seed,
    0.712-0.745). Overall IoU falls 0.026, about one seed sd, with overlapping per-seed
    ranges, so overall IoU alone is borderline. This is a trade-off: a narrower mask for
    a worse curve.

STATUS / DECISION
  No configuration wins on val IoU or val vRMSE. The per-row-peak decoder does not
  reduce velocity error and is not adopted for reporting. pos_weight 5 is the one change
  that moves something (mask width) beyond noise without a measurable cost elsewhere.
  Whether that is worth adopting (and, if so, whether to re-score test) is a decision
  for the PI. Test (M09) was NOT scored for B or C, and the reference result for
  the canonical configuration (pos_weight 15) is unchanged.

  Run outputs: Bluehive3 /scratch/tolugboj_lab/Prj_Wavenet/epic_production/FTAN_ML_RUNS/
  ablate_pw_2109273/pw{15,5,1}/seed_{42,43,44}/ (pw15 holds val_eval.json only);
  SLURM logs FTAN_ML_RUNS/logs/ablate_pw_2109273_{0..8}.out. Copies of the configs,
  train logs and val_eval.json files (no checkpoints) are in
  docs/ml_pipeline_stages/stage_d_results/ablate_pw_2109273/.

OPEN QUESTIONS FOR PI
  4. Adopt pos_weight 5? It halves the width bias (pred/tgt 1.11 -> 1.05) with IoU and
     vRMSE unchanged within noise. Adopting it changes the canonical loss, and reporting
     a test number for it means scoring M09 a second time, with a different
     configuration chosen after seeing val.
  5. Width bias was not what limited IoU or vRMSE. Narrowing the band to the target width
     (pos_weight 1) did not raise IoU and made the curve worse. The remaining val error is
     mostly the band being in the wrong place (M02 especially, see the hard example
     drifting below theory past ~12 s), which points back to open question 2 (transfer
     across families) rather than to the loss weighting.
