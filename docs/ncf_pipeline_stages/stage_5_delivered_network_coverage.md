Stage report for the delivered-network coverage analysis. HYPOTHESIS and SETUP are filled
in by the PI; wavenet_junior fills WHAT WAS TRIED, RESULTS, HARDWARE TIER LOG, DECISION and
OPEN QUESTIONS FOR PI from their own run. Task memo:
`docs/memos/2026-10-01-delivered-network-footprint-and-connectivity.md`.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 5 — DELIVERED NETWORK FOOTPRINT AND RAY-PATH COVERAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  Stages 0-4 describe the network we PLANNED to acquire, derived from a data-centre
  availability inventory (`fps_stations.csv`, `fps_pairs.csv`, `connection_heatmap.png`).
  This stage establishes what we actually DELIVERED, measured from the packaged HDF5 on
  disk, and whether its ray-path geometry is adequate for global surface-wave tomography.

  Specifically it claims to establish, as measured rather than inferred quantities:
    1. the delivered station footprint, packaged volume, and channel-band mix;
    2. the number of station pairs that can actually form an NCF, gated on real concurrent
       recording days read from each file's per-day coverage bitmap;
    3. per-cell illumination quality on an equal-area global grid — path count, temporal
       overlap, azimuthal spread A and aspect ratio kappa, and effective (Fresnel)
       resolution — and therefore where on Earth this dataset can support an inversion;
    4. how that coverage responds to restricting maximum inter-station separation;
    5. whether station losses fell in dense or sparse regions (the psi diagnostic), which
       is the question station-count recovery cannot answer.

  Availability is not delivery. That assumption is what produced the v1 defect that cost
  954 stations, so this stage measures delivery directly and never infers it.

SETUP
  Commit hash          : <FILL IN — commit that adds measure_delivered_network.py>
  Code                 : `src/wavenet_pipeline/00_waveform_acquisition/metadata3/`
                         `measure_delivered_network.py` (stages 0-1: reconcile + scan)
                         `analysis_figures.py`          (stages 2-3: metrics + figures)
                         `requirements-analysis.txt`    (axon-1 venv, pinned wheels)
  Data                 : `/scratch/tolugboj_lab/wavenet_ncf_production_v2/` on BlueHive3
                         (v2 campaign root; `results/*.json` + `packaged_h5/*.h5`)
  Manifest             : `metadata3/fps_stations_v2.csv` (1,999 stations)
  Planned baseline     : `metadata3/fps_pairs.csv` (9,743 pairs, 110-1110 km, day-gated)
  Reference output     : `metadata3/delivered_network_reference/` (PI run, 2026-10-01)

  Key parameters (all CLI-tunable; report any you changed)
    n_cells            : 3000 equal-area Fibonacci cells (412 km across)
    step_km            : 25 km waypoint spacing along each great-circle path
    day gate           : >=90 shared days under 1500 km, >=365 over
    overlap ladder     : 90 / 180 / 365 / 730 days
    resolved cell      : n>=10 paths AND A>=0.6 (kappa<=2.33) AND max azimuth gap <=60 deg
    Fresnel lambda     : 100 km reference wavelength

  Deliberate constraints
    - Stage 1 reads ONLY group attributes and `_coverage` bitmaps. Channel datasets are
      ~1.2e9 float32 (~4.9 GB each) and are never sliced.
    - Stage 1 scans only `package_ok=True` stations, so every file read is finished and
      quiescent and the in-flight files the live campaign is writing are excluded by
      construction rather than by a timing heuristic.
    - terravibranium runs NOTHING in this stage (saturated at load 42-44/48 by the
      SAmericaNoise packaging campaign). It is a backup destination only.
    - Read-only throughout. The v1/v2 hard-link rule is not violated.

WHAT WAS TRIED
  <FILL IN — your run. Include anything you changed and why, anything that failed, and
  anything you confirmed by direct test rather than assuming. Dead ends are worth writing
  down; so is "I expected X and got Y".>

RESULTS
  <FILL IN — your SUMMARY.txt numbers and the figures. Then answer the four questions from
  the memo with reasoning, not just values:
     1. Which maximum path length would you choose for a global study, and why?
        (fig15_restriction_tradeoff.png)
     2. Why are the resolved cells over continents and the poorly resolved ones over ocean
        basins? (fig13_map_quality_classes.png)
     3. Is the 90-day minimum overlap the right threshold, and what would you have to
        measure to answer that rather than guess? (fig04_shared_days_hist.png)
     4. Is this network good enough to do science with yet, or do we wait?
  Note any discrepancy against the PI reference run and whether the campaign having
  advanced explains it.>

HARDWARE TIER LOG
  | Tier             | Status | Date | Job ID | Log link |
  |------------------|--------|------|--------|----------|
  | axon-1 (local)   |        |      |  n/a   |          |
  | mothership       |  n/a   |      |  n/a   | not used |
  | terravibranium   |  n/a   |      |  n/a   | not used — saturated, backup destination only |
  | Bluehive3        |        |      |        | `scan.slurm`, stage 1 only |

DECISION
  <FILL IN — what should be treated as canonical going forward. Candidates: the delivered
  pair count as a floor rather than a final number; the grid resolution the present data
  supports; whether any maximum-separation limit should be adopted; whether the 90-day gate
  stands pending measurement.>

OPEN QUESTIONS FOR PI
  <FILL IN. Known open items at the time this skeleton was written:
     - Re-run after the campaign completes: 96% of in-flight stations carry 5+ years of
       data versus 28% of delivered, so the usable-pair count should rise substantially.
       How much does it actually rise?
     - The 90-day gate is a convention, not a measurement. Settling it needs the NCF
       convergence test (End-notes G of the memo), which depends on wavenet_senior's NCF
       step. Should that be scheduled now?
     - Median delivered duration (229 days) is well under the availability inventory's
       median (624 days) for the same stations. Is that an inventory-methodology artifact
       or a genuine retrieval shortfall? Not yet investigated.
     - `build_coverage_density_analysis.py` has three defects documented in the memo
       (non-great-circle paths, non-equal-area grid, chained differential band mask). Its
       published numbers should carry a caveat. Fix, supersede, or leave as-is?>

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
