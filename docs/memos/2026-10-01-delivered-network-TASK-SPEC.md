Subject: TASK SPEC AND EVALUATION RUBRIC — Delivered-Network Footprint and Connectivity (PI copy)

PI-facing companion to `2026-10-01-delivered-network-footprint-and-connectivity.md`, which
is the version wavenet_junior receives. This copy carries the analytical specification in
full, the reference answers, and a rubric for grading the returned work. Not for
distribution to the team.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. WHY THIS TASK EXISTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every coverage artifact in `metadata3/` describes the PLANNED network — the station manifest
joined against a data-centre availability inventory. None of it describes the DELIVERED
network. The v1 defect that cost 954 stations came from exactly that conflation, so the gap
is not academic.

The task is also pitched to move the junior from monitoring to analysis: there is a defined
result to produce and defend, not a status report. The script is written and verified, so
failure modes are interpretive rather than mechanical — which is what makes the work
gradeable.

Secondary purpose: `kappa` (resolution-ellipse aspect ratio) and the Fresnel width give a
quantitative, checkable claim about the resolution this dataset supports. That is the form
an external reviewer or funding agency can verify, unlike a station count.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2. ANALYTICAL SPECIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

2.1 Measurement chain

  Stage 0  reconcile manifest (1,999) / result JSONs (1,933) / `.h5` on disk (1,080),
           classifying every disagreement. Catches real data loss (`ok_json_without_h5`)
           and distinguishes it from the benign in-flight case.
  Stage 1  per station, read group attrs + `_coverage` bitmaps only. BH3 sbatch, ~2 min,
           emits a 240 kB cache. Never slices a channel dataset (~1.2e9 float32, 4.9 GB).
  Stage 2  footprint, packaged volume, channel-band mix.
  Stage 3  all-pairs shared days, ray-path rasterization, per-cell metrics, figures.

2.2 Pair construction

  Shared days for all pairs at once via boolean matrix product: with M the station-by-day
  coverage matrix, M M^T gives concurrent recording days for every pair. Self-checked against
  explicit logical-AND on 20 random pairs each run (prints PASS/FAIL; hard-fails on FAIL).

  Gate retained from `build_pairs.py`: >=90 shared days under 1,500 km, >=365 over. Applied
  with NO distance band — the headline analysis reports all separations. Restriction is a
  sub-analysis, and every restriction level is reported in absolute terms and differenced
  against the unrestricted baseline, never chained against the previous row.

2.3 Grid

  Equal-area Fibonacci (golden-angle) sphere, nearest-cell assignment by haversine.
  Equal-area and compact; healpy is unavailable on every machine in this project.

  Physical motivation: Rayleigh waves at 10-40 s with group velocity 3-4 km/s give
  lambda ~30-160 km. Resolution is bounded not by lambda but by the first Fresnel zone,
  w = sqrt(lambda L / 2) — ~224 km for lambda=100 km over L=1,000 km. That justifies ~12,000
  cells (206 km).

  BUT the delivered network currently yields only 7,575 usable paths, and at 12,000 cells the
  per-cell azimuth statistics are dominated by nearest-neighbour binning aliasing on the cell
  lattice — it renders as spiral banding that is an artifact, not structure. Default is
  therefore 3,000 cells (412 km): the finest grid the present path count supports. This is
  itself a result, and should be revisited once the long-duration in-flight stations land.

2.4 Quality, per ray path

  S_ij  concurrent recording days. NCF SNR grows as sqrt(stacked days), so S is the primary
        per-path quality measure, not merely a gate.

  Deliberately NOT collapsed to a single normalized score. The sqrt scaling is sound, but the
  prefactor — how many days suffice — varies by close to an order of magnitude with noise
  source distribution and seasonality, station noise floor, distance and period band. The
  script reports the raw distribution and counts at a ladder of standards (90/180/365/730 d).
  A weighted form q = (S/S_ref)^p is available but opt-in, with S_ref and p printed in the
  caption. Settling the constant properly requires the NCF convergence experiment in §6.

2.5 Quality, per cell

  n_c          path count. More is better — better averaging, room to reject data. High
               redundancy is desirable; nothing here recommends pruning.
  ladder       n_c restricted to paths meeting each overlap standard. Divergence from raw
               n_c localises coverage that looks adequate but is temporally thin.
  A_c          azimuthal spread. With traverse length L_k and LOCAL along-path azimuth
               theta_k (azimuth rotates >10 deg over 1,000 km at mid-latitude):

                   R_c = | sum_k L_k exp(i 2 theta_k) | / sum_k L_k
                   A_c = 1 - R_c

               Angle doubling makes the statistic respect that ray paths are undirected
               (theta == theta+180). Without it, two identical parallel paths cancel and
               score as perfectly spread — the classic error.
  kappa_c      (1+R)/(1-R) = 2/A - 1. Aspect ratio of the cell's resolution ellipse.
  gap          max azimuth gap mod 180. Catches A_c's blind spot: two orthogonal families
               score A=1 but leave a 90 deg gap, and 2-psi anisotropy needs >=3 orientations.
  w_c          median Fresnel width over the cell's crossings, in km, directly comparable to
               the 412 km cell side. Converts "shorter paths are better" into a resolution
               length rather than an arbitrary score.
  rho_c        azimuthal redundancy, descriptive only.

  Resolved := n_c >= 10 AND A_c >= 0.6 (kappa <= 2.33) AND gap <= 60 deg.
  Thresholds are conventions, parameterized, and swept — the script reports sensitivity.

2.6 Delivered vs planned

  Station recovery f_sta = 1023/1999 = 0.512 is the wrong headline: pairs scale roughly as
  the square of local station density. Report instead

      psi = f_path / f_sta^2

  psi ~ 1 losses random; psi < 1 losses concentrated in dense clusters (cheap, since those
  pairs were largely redundant); psi > 1 losses concentrated in sparse regions (expensive,
  since those pairs were the only ones crossing their cells).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
3. REFERENCE RESULTS (PI run, 2026-10-01)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Reference output committed at `metadata3/delivered_network_reference/`.

  FOOTPRINT
    manifest stations                 1,999
    delivered (packaged, with data)   1,023  (51.2%)
    days of real data                 510,501
    packaged on disk                  502.1 GB
    median days per station           229
    band mix                          BH 767, LH 155, HH 80, EH 15, SH 5, MH 1

  RAY PATHS (all separations)
    usable station pairs              7,575   (of 522,753 geometrically possible)
    median shared days per pair       493
    >=90 / 180 / 365 / 730 d          7,575 / 6,882 / 6,233 / 1,779
    median path length                7,752 km

  ILLUMINATION (3,000 cells, 412 km)
    cells with >=1 path               3,000 (100%)
    cells resolved                    1,490 (49.7%)
    median A_c                        0.607  (kappa 2.30)
    median Fresnel width              831 km
    Gini of paths per cell            0.352

  DELIVERED vs PLANNED (110-1110 km band)
    psi                               0.48   -> losses fell in dense regions (cheap)

  RESTRICTION SWEEP
    max km      pairs     % lit   % resolved   Fresnel km
      1110      1,222      18.5          1.4          186
      1500      1,642      24.8          2.3          238
      2000      1,732      27.4          2.5          238
      3000      1,957      36.9          2.8          270
      5000      2,586      56.1          5.8          393
     10000      4,862      93.2         17.1          647
      none      7,575     100.0         49.7          831

  TEMPORAL BIAS OF THE CURRENT SNAPSHOT
    delivered        n=976  median 624 advertised days   28% with 5+ yr
    still in flight  n= 53  median 3,917 advertised days  96% with 5+ yr

  Defensible resolution statement supported by the above: at the current 96%-complete
  snapshot, the delivered network illuminates 100% of the globe at ~831 km effective
  resolution, and meets the resolved criterion (>=10 paths, aspect ratio <=2.33, azimuth gap
  <=60 deg) over 49.7% of global area. Restricting to the 110-1110 km production band
  instead gives ~186 km resolution over 18.5% of the globe. Both are measured, not asserted.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4. WHAT THE JUNIOR WAS ASKED FOR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Mechanical: build the axon-1 venv; sbatch the BH3 scan; pull the cache; run stages 2-3;
compare against the reference run.

Interpretive, the part that is actually graded — four questions:

  Q1  Which maximum path length would you choose for a global study, and why?
  Q2  Why are resolved cells over continents and poorly resolved ones over ocean basins?
  Q3  Is the 90-day minimum overlap right, and what would you measure to settle it?
  Q4  Is the delivered network good enough to do science with yet, or do we wait?

Deliverables: `stage_5_delivered_network_coverage.md` filled in, plus a Slack post following
the 8-part template.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5. EVALUATION RUBRIC
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Mechanical competence — necessary, not sufficient
  [ ] venv built; scan submitted and completed; cache transferred; stages 2-3 run clean
  [ ] SUMMARY.txt reproduces the reference within campaign drift
  [ ] notices and explains any divergence rather than reporting it silently
  [ ] stage doc fields all filled; Slack post follows the template's structure and voice

Q1 — path length trade-off
  Strong    engages the trade-off as a trade-off: short paths give ~186 km resolution over
            18.5% of the globe, unrestricted gives ~831 km everywhere; picks one and ties
            the choice to an actual scientific target (global model vs regional detail);
            may note the 110 km floor exists for far-field validity.
  Adequate  reads both curves correctly, picks one, gives a reason.
  Weak      restates the numbers; or declares one "better" with no stated objective.

Q2 — continental vs oceanic resolution
  Strong    identifies that stations are overwhelmingly on land, so paths crossing an ocean
            basin run between continental margins and sample a narrow set of orientations;
            connects this to LOW A_c (anisotropic class) rather than to low path count, and
            notices the distinction — basins are lit but badly conditioned, not unlit.
  Adequate  "stations are on land, so ocean coverage is worse."
  Weak      attributes it to too few paths without checking that illumination is 100%.

Q3 — the 90-day threshold
  Strong    recognizes it as a convention rather than a measurement; proposes the
            convergence experiment (stack 30/60/.../730 days on real pairs, measure SNR vs
            stack length, find where it plateaus); notes it varies with distance, band and
            site noise, so one global constant is suspect; may point at
            `ncf_candidate_pairs.csv` as the input.
  Adequate  says it should be tested empirically, without specifying how.
  Weak      accepts 90 days because it is what the code does.

Q4 — is it usable yet
  Strong    argues from the temporal-bias evidence that 7,575 is a floor, since in-flight
            stations are 96% long-duration and will pair broadly; distinguishes "usable for
            method development now" from "usable for a publishable global model"; may note
            psi=0.48 means remaining failures are low-priority relative to finishing.
  Adequate  takes a position supported by at least one real number.
  Weak      yes/no with no quantitative support.

Bonus credit
  [ ] independently questions a methodological choice (grid resolution, the resolved
      thresholds, the Fresnel lambda, the gate) with reasoning
  [ ] spots something in the figures that looks wrong and says so
  [ ] identifies a result the PI did not flag

Automatic concern — regardless of the rest
  [ ] writes into `packaged_h5/` or violates the hard-link rule
  [ ] slices a channel dataset
  [ ] runs anything on terravibranium
  [ ] reports numbers that cannot be reproduced from their own output

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6. OPEN ITEMS THIS TASK FEEDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Re-run at campaign completion. The usable-pair count should rise substantially; the
     12,000-cell (206 km) grid may become supportable. Cheap to repeat by design.
  2. NCF convergence experiment (wavenet_senior). `ncf_candidate_pairs.csv` is the input.
     Replaces the 90-day convention with a measured number and retires the arbitrary
     normalization constant discussed in §2.4.
  3. Median delivered duration (229 d) versus the availability inventory's median for the
     same stations (624 d). Inventory-methodology artifact, or genuine retrieval shortfall?
     Not investigated. The download window is unbounded (1970 to present), so it is not a
     configured cap.
  4. `build_coverage_density_analysis.py` carries three confirmed defects — straight lat/lon
     interpolation instead of great circles (also wrong across the antimeridian), a
     non-equal-area 2-degree grid, and a chained differential mask across a moving band edge.
     Its published numbers need a caveat. Fix, supersede, or annotate?
  5. Mirror automation, unchanged from the previous memo: the three mirror scripts remain
     one-time snapshots. terravibranium is now current (1,023 files / 468 GB) following the
     targeted 9-file copy, but will drift again.

— Tolu
