━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE A — DATA DEFINITION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  A canonical, schema-correct Dataset/cache-builder can be built by promoting and
  generalizing chrisScripts/julyncf_pipeline/ML_pipeline/U_NET_array.py's FTAN
  regrid/mask logic — reading directly from the two production CPS HDF5 files, handling
  multiple geometries per model, and splitting train/val/test by family across both
  files without leaking the same underlying Earth model across splits.

SETUP
  Commit: c3c0505 (base before this work; this stage's files are new/uncommitted as of
  writing). Data: 50-model extraction (5 per family x 10 families) pulled live from
  terravibranium's wavenetv2_dataset_10k_full.h5 (sep=127km) and
  wavenetv2_dataset_10k_sep100km.h5 (sep=100km) into
  .venv_ml-adjacent .local_test_data/ (git-ignored, not committed — ad-hoc verification
  data only, not the plan's eventual data_samples/tutorial_subset.h5 deliverable).
  Local venv: Python 3.10.5, numpy 1.26.4, scipy 1.15.3, h5py 3.16.0, pycwt 0.4.0b0,
  torch 2.2.2 (see OPEN QUESTIONS re: version pinning — this combo was reconciled ad hoc
  for local Intel-Mac testing, not yet the plan's formal env/requirements.txt).

WHAT WAS TRIED
  - ftan_grid.py: promoted regrid_ftan/build_target_mask/pad_to_canonical from
    chrisScripts, VERIFIED (not just inherited) via: (1) a synthetic-data self-test
    (shapes/dtypes), (2) actual runs against 100 real samples (below). Reconciled a
    small, previously-unnoticed discrepancy between chrisScripts' fundamental-mode cut
    (`np.diff(period) <= 0`) and verify_main.py's (`< 0`) — standardized on `<= 0`
    (documented in the function docstring) since it's more conservative.
  - dataset.py: generalized chrisScripts' SimDataset (hardcoded to "separation_127.0km")
    into enumerate_samples() walking every populated geom_key + FTANDispersionDataset.
    VERIFIED: enumerate_samples found exactly 100 populated (model, geometry) samples
    across 2 test files x 50 models each, 10 families — matches expectation exactly (no
    samples silently skipped as "pending_ftan_computation" placeholders in this subset).
  - splits.py: generalized chrisScripts' 2-way _split_keys into a 3-way (7/2/1 family)
    split. VERIFIED on the real 100-sample subset: train=70 (families M01,M03-M08),
    val=20 (M02,M10), test=10 (M09) — exactly matches the 7/2/1 family-count design.
  - build_ml_cache.py: VERIFIED end-to-end against real data, both --tutorial-sample
    (40 samples) and full modes. Output cache inspected directly: X in [0,1] (per-row
    normalized as designed), Y strictly binary {0,1}, mask positive-pixel fraction
    1.67% (matches the plan's independent a-priori estimate of "~1.7%" almost exactly),
    zero all-zero (degenerate) masks across 100 samples, padded rows (76-79) confirmed
    all-zero in both X and Y.
  - Cross-file Model_ID identity assertion: RE-VERIFIED (not just trusted from the
    earlier terravibranium spot-check) on this independently-extracted subset — 10/50
    shared sim_keys checked, H_km/VP_kms byte-identical across both files every time.
    This directly validates the family-split-across-files design.
  - validate_ftan.py: extracted recompute_ftan_from_coherence from
    verify_main.py's compute_ftan_and_plot, VERIFIED end-to-end against 50 real samples
    (see RESULTS — this surfaced a real, non-trivial finding, not a clean pass).

RESULTS
  - build_ml_cache.py (tutorial-sample, n=40): wrote 7.7MB cache, (40,80,300) X+Y.
  - build_ml_cache.py (full, 100 samples): wrote 19.2MB cache + splits_seed42.json;
    70/20/10 train/val/test split by family, cross-file identity check passed.
  - validate_ftan.py (50 samples, seeded, stratified across 10 families):
    all 50 processed without error (0 skipped). Centroid RMSE (precomputed FTAN_ZZ vs.
    independently recomputed): mean=0.0498 km/s, median=0.0425, p95=0.0913 km/s.
    17/50 (34%) exceeded the originally-proposed 0.05 km/s flag threshold.
    HOWEVER: even the worst cases show pixel correlation 0.93-0.96 and thresholded IoU
    ~0.90-0.95 (see per-family breakdown below) — the two FTAN computations agree
    structurally throughout; the flagged discrepancy reflects genuine, modest,
    family-dependent sensitivity to the CWT parameter choice (dj=1/12 gradient-of-CCF
    vs dj=1/24 bandpassed-coherence-derived), not a bug in either implementation.
    Per-family mean RMSE ranged from 0.033 (M08) to 0.074 km/s (M02) — some Earth-model
    archetypes produce more CWT-parameter-sensitive FTAN images than others.

  ASSESSMENT: the precomputed empirical_ftan_dispersion/FTAN_ZZ is safe to use as the
  primary training input (high structural agreement with an independent method) — but
  the originally-proposed 0.05 km/s "flag for manual review" threshold was calibrated
  too tight for real data (34% flag rate driven by expected cross-method variation, not
  data quality problems). See OPEN QUESTIONS.

  Tutorial notebook (notebooks/stage_a_data_definition.ipynb) written against a proper
  official 10-model (1-per-family), 2-geometry tutorial artifact
  (data_samples/tutorial_subset_sep{127,100}km.h5 + tutorial_manifest.csv, 11.7MB each,
  extracted directly from the real production files, not the ad-hoc .local_test_data/
  used above) and EXECUTED end-to-end via `jupyter nbconvert --execute` — zero errors,
  same 1.67% mask positive-fraction result reproduced independently.

HARDWARE TIER LOG
  | Tier                | Status | Date       | Job ID | Log link |
  |---------------------|--------|------------|--------|----------|
  | Local               | PASS   | 2026-09-04 |  n/a   | this doc, notebooks/stage_a_data_definition.ipynb, .local_test_data/ (git-ignored) |
  | terravibranium-gpu  | not started | | | |
  | Alpha               | not started | | | |
  | Beta                | not started | | | |

DECISION
  Precomputed FTAN_ZZ confirmed as the canonical training input (per the earlier
  AskUserQuestion decision, now empirically backed rather than just assumed). Canonical
  grid (76x300, 2-5km/s) and fundamental-mode isolation (`<= 0` cut) confirmed correct
  against real data. Family-based 7/2/1 split confirmed valid (cross-file identity
  re-verified on independent data).

OPEN QUESTIONS FOR PI
  1. The QA gate's flag threshold (0.05 km/s) should likely be raised — real-data
     evidence suggests ~0.08-0.10 km/s (near the observed p95) better separates "normal
     cross-method variation" from an actual anomaly. Left the code's threshold constant
     unchanged pending your sign-off, per the plan's explicit "confirm during review"
     note — do not treat this as decided.
  2. Local venv version pins (numpy<2 + pycwt==0.4.0b0 + torch==2.2.2) were reconciled
     ad hoc for Intel Mac (x86_64) testing — PyPI has no torch wheel newer than 2.2.2
     for this platform. This is NOT yet the plan's formal env/requirements.txt (that
     work wasn't in this round's scope) — flagging so it isn't mistaken for a final
     environment spec, and so whoever tests on terravibranium-gpu/Alpha/Beta (different
     platforms, newer torch available) knows this constraint is Intel-Mac-specific, not
     universal.
  3. This stage was verified against a 100-sample hand-extracted subset (5 models per
     family from each of the 2 production files, ad hoc, git-ignored), not the full
     20,000-sample dataset — running build_ml_cache.py + validate_ftan.py against the
     FULL production files (on terravibranium or via a cluster job) is the natural next
     step before treating Stage A as fully done, not just locally verified.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
