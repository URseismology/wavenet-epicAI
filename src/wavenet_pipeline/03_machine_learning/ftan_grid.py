"""
Canonical FTAN grid: period 1-20s (76 bins) x group velocity 2-5 km/s (300 bins),
padded to (80, 300). Pure numpy/scipy, no I/O — promoted and generalized from
chrisScripts/julyncf_pipeline/ML_pipeline/U_NET_array.py's _regrid_ftan/_build_mask/_pad
(verified against the real production HDF5 during Stage A, not just inherited as-is —
see docs/ml_pipeline_stages/stage_a_data_definition.md).

Grid note: this period/velocity grid is not an arbitrary choice — it matches exactly
what src/wavenet_pipeline/02_simulation/verify_main.py's compute_ftan_and_plot() already
uses for its own from-scratch FTAN recompute (`per = np.arange(1, 20, 0.25)`,
`vel = np.arange(2.0, 5.0, 0.01)`). validate_ftan.py's QA gate relies on this: the
independent recompute lands on this same grid natively, no extra regridding needed for it.
"""

import numpy as np
from scipy.interpolate import RegularGridInterpolator

PERIOD_MIN, PERIOD_MAX, PERIOD_BINS = 1.0, 20.0, 76
VEL_MIN, VEL_MAX, VEL_BINS = 2.0, 5.0, 300
PAD_ROWS = 4
MASK_WIDTH = 2

PERIOD_GRID = np.arange(PERIOD_MIN, PERIOD_MAX, 0.25)  # (76,)
VEL_GRID = np.arange(VEL_MIN, VEL_MAX, (VEL_MAX - VEL_MIN) / VEL_BINS)  # (300,)


def regrid_ftan(ftan_raw: np.ndarray, period_s: np.ndarray, velocity_kms: np.ndarray) -> np.ndarray:
    """
    Regrid a raw empirical FTAN image (native, non-uniform axes) onto the canonical
    (76, 300) grid, per-row max-normalized.

    The raw `velocity_kms` axis stored in the HDF5 spans 0-254 km/s (confirmed via direct
    inspection on terravibranium, 2026-09-04) because it includes the documented,
    intentionally-untouched divide-by-zero artifact from wvsim_main.py:369
    (`velocity_kms = np.where(travel_times > 0, sep_km / travel_times, 0.0)`).
    The `velocity_kms > 0` filter below excludes those artifact rows *before*
    interpolation — this is not a new fix, just documenting why it's already safe.
    """
    valid = velocity_kms > 0
    vel_axis = velocity_kms[valid][::-1]
    ftan_flip = ftan_raw[:, valid][:, ::-1]
    interp = RegularGridInterpolator(
        (period_s, vel_axis), ftan_flip,
        method="linear", bounds_error=False, fill_value=0.0,
    )
    P, V = np.meshgrid(PERIOD_GRID, VEL_GRID, indexing="ij")
    out = interp(np.stack([P, V], axis=-1)).astype(np.float32)
    for i in range(out.shape[0]):
        mx = out[i].max()
        if mx > 0:
            out[i] /= mx
    return out


def isolate_fundamental_mode(theory_period: np.ndarray, theory_gvel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Truncate a multi-mode-concatenated theoretical dispersion array (period non-monotonic
    at mode boundaries) to just the fundamental mode.

    Reconciles a small discrepancy found between the two pre-existing implementations
    this was inherited from (not previously verified against each other): chrisScripts'
    _build_mask used `np.diff(period) <= 0` (cuts on an exact tie too) while
    verify_main.py's wrap-detection used `np.diff(period) < 0` (strict decrease only).
    This function uses `<= 0` (the more conservative choice — also cuts on an exact
    period-value tie, avoiding any ambiguous duplicate-period fundamental-mode data).
    """
    cut = np.where(np.diff(theory_period) <= 0)[0]
    cut_idx = int(cut[0]) + 1 if len(cut) else len(theory_period)
    return theory_period[:cut_idx], theory_gvel[:cut_idx]


def build_target_mask(theory_period: np.ndarray, theory_gvel: np.ndarray) -> np.ndarray:
    """Binary mask (76, 300): +-MASK_WIDTH bins around the fundamental-mode group-velocity curve."""
    tp, tg = isolate_fundamental_mode(theory_period, theory_gvel)
    gvel = np.interp(PERIOD_GRID, tp, tg, left=np.nan, right=np.nan)
    mask = np.zeros((PERIOD_BINS, VEL_BINS), dtype=np.float32)
    for i, gv in enumerate(gvel):
        if np.isnan(gv):
            continue
        b = int(round((gv - VEL_MIN) / (VEL_MAX - VEL_MIN) * (VEL_BINS - 1)))
        lo, hi = max(0, b - MASK_WIDTH), min(VEL_BINS, b + MASK_WIDTH + 1)
        mask[i, lo:hi] = 1.0
    return mask


def pad_to_canonical(arr_76x300: np.ndarray) -> np.ndarray:
    """(76, 300) -> (80, 300), zero-padded (rows 76-79)."""
    return np.vstack([arr_76x300, np.zeros((PAD_ROWS, VEL_BINS), dtype=np.float32)])


def weighted_centroid_curve(image_76x300: np.ndarray, vel_grid: np.ndarray = VEL_GRID,
                             min_row_mass: float = 1e-3) -> np.ndarray:
    """
    Per-period-row weighted-centroid velocity (km/s) from any (76, 300) image
    (FTAN power or predicted mask probability). Rows with near-zero mass return NaN
    rather than a spurious near-zero-mass centroid. Used by validate_ftan.py's QA gate
    (Stage A) and will be reused by Stage E's extract_curve for scientific curve picking.
    """
    row_mass = image_76x300.sum(axis=-1)
    centroid = np.full(image_76x300.shape[0], np.nan, dtype=np.float64)
    detected = row_mass >= min_row_mass
    weights = image_76x300[detected]
    centroid[detected] = (weights * vel_grid[None, :]).sum(axis=-1) / weights.sum(axis=-1)
    return centroid


if __name__ == "__main__":
    # Minimal self-test with synthetic data (no HDF5 needed) — shape/dtype sanity only.
    rng = np.random.default_rng(0)
    fake_period_s = np.linspace(1.0, 132.0, 85)
    fake_velocity_kms = np.concatenate([[0.0, 0.0], np.linspace(0.1, 254.0, 999)])
    fake_ftan_raw = rng.random((85, 1001)).astype(np.float32)
    out = regrid_ftan(fake_ftan_raw, fake_period_s, fake_velocity_kms)
    assert out.shape == (PERIOD_BINS, VEL_BINS), out.shape
    assert out.dtype == np.float32

    fake_theory_period = np.concatenate([np.linspace(1.0, 20.0, 50), np.linspace(1.0, 15.0, 30)])
    fake_theory_gvel = rng.uniform(2.5, 4.0, size=fake_theory_period.shape[0]).astype(np.float64)
    mask = build_target_mask(fake_theory_period, fake_theory_gvel)
    assert mask.shape == (PERIOD_BINS, VEL_BINS)
    assert set(np.unique(mask)) <= {0.0, 1.0}

    padded = pad_to_canonical(mask)
    assert padded.shape == (PERIOD_BINS + PAD_ROWS, VEL_BINS)
    assert np.all(padded[PERIOD_BINS:] == 0.0)

    curve = weighted_centroid_curve(out)
    assert curve.shape == (PERIOD_BINS,)

    print("ftan_grid.py self-test OK:", out.shape, mask.shape, padded.shape, curve.shape)
