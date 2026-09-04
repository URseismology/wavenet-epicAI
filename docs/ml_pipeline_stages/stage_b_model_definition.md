━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE B — MODEL DEFINITION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  The chrisScripts U-Net architecture (DoubleConv/UNetSeg) and loss composition
  (CombinedLoss: Focal+Dice+WeightedBCE+Sharpening) can be promoted as-is for the
  canonical (1,80,300) grid, with one real bug-vs-not-a-bug question to resolve: is the
  duplicated post-head shape-check dead code or is it hiding something?

SETUP
  Commit: c3c0505 (base). Model: UNetSeg(in_channels=1, out_channels=1,
  features=(16,32,64,128)), 1,943,761 parameters. Verified locally (Python 3.10.5,
  torch 2.2.2, CPU only — no GPU available on axon-1).

WHAT WAS TRIED
  - Traced the actual shape math through 4 pooling levels for the (80,300) grid:
    height 80->40->20->10->5 divides cleanly at every level; width
    300->150->75->37->18 does NOT (floor(75/2)=37) at levels 3-4. CONCLUSION (verified
    by direct computation, not assumed): the per-level defensive F.interpolate inside
    the decoder loop is genuinely necessary; the second, duplicate post-`self.head`
    check was dead code specifically for this grid (the last upsample, 150->300, already
    lands exactly on input_size). Deduped to one guarded call with an explanatory
    comment + a shape-assertion self-test (model.py's `if __name__` block).
  - Ran model.py's self-test: `UNetSeg()(zeros(2,1,80,300)).shape == (2,1,80,300)` —
    PASSED, confirming the dedup didn't break anything for this grid.
  - Ran a 1-sample overfit test against a REAL cached sample (from Stage A's cache, not
    synthetic data) to verify the architecture/loss/gradient-flow actually work
    end-to-end, not just shape-check: at lr=1e-4 (the plan's originally-assumed
    training-config LR) and 150 iterations, loss decreased monotonically
    (0.77 -> 0.38) but IoU only reached ~0.54-0.60 — did NOT hit the plan's optimistic
    "~50 iterations to converge" expectation. Re-ran at lr=3e-3, 800 iterations:
    converged cleanly to loss=0.0085, IoU=0.977, Dice=0.988 by iteration 799 (with one
    transient dip around iteration 400, recovered). CONCLUSION: architecture, losses,
    and gradient flow are all correct — the earlier "50 iterations" estimate in the plan
    was just optimistic for CombinedLoss's composition (Focal+Dice+WeightedBCE+
    Sharpening converges slower than a single loss would); not an architecture problem.
  - Confirmed BatchNorm2d layers (used throughout DoubleConv) function correctly even
    with the overfit test's batch_size=1, despite batch-of-1 BatchNorm being a known
    general risk area — no NaNs, no divergence, eventual clean convergence.

RESULTS
  - Shape-assertion self-test: PASS, (2,1,80,300) -> (2,1,80,300).
  - 1-sample overfit (lr=1e-4, 150 iters): loss 0.77->0.38, IoU 0.02->0.60 (not
    converged — LR/iteration budget insufficient for this loss composition).
  - 1-sample overfit (lr=3e-3, 800 iters): loss 0.64->0.0085, IoU 0.03->0.977,
    Dice 0.06->0.988 (converged).

HARDWARE TIER LOG
  | Tier                | Status | Date       | Job ID | Log link |
  |---------------------|--------|------------|--------|----------|
  | Local               | PASS   | 2026-09-04 |  n/a   | this doc, .local_test_data/overfit_test.py (git-ignored, ad hoc) |
  | terravibranium-gpu  | not started | | | |
  | Alpha               | not started | | | |
  | Beta                | not started | | | |

DECISION
  Architecture (DoubleConv/UNetSeg) and CombinedLoss promoted as-is from chrisScripts,
  now with the shape-check dedup fix. Confirmed functionally correct against real data,
  not just synthetic shape checks.

OPEN QUESTIONS FOR PI
  1. The plan's assumption that a 1-sample overfit test converges "in ~50 iterations"
     (at the eventual training config's lr=1e-4) does not hold in practice — it needed
     ~800 iterations at a much higher lr=3e-3 to fully converge. This has a real
     implication for Stage D: the full training run's lr=1e-4/200-epoch budget should be
     checked against this — if 1e-4 genuinely converges this slowly even on a trivial
     1-sample case, the full multi-epoch training schedule may need a higher initial LR
     or a longer patience budget than the inherited defaults assume. Flagging now rather
     than discovering it deep into a real Stage D training run.
  2. CombinedLoss's weights/pos_weight remain unverified-but-plausible (per the plan's
     original note) — the overfit test doesn't by itself validate these are well-tuned,
     only that the loss is learnable at all.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
