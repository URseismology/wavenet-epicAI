"""
Training-time metrics for the FTAN mask segmentation U-Net. Promoted from
chrisScripts/julyncf_pipeline/ML_pipeline/U_NET_array.py, generalized to import grid
constants from ftan_grid.py (single source of truth) instead of re-declaring them.

velocity_error is a *training* metric — it must stay differentiable/defined everywhere,
hence the +1e-8-stabilized centroid even for near-zero-mass rows. This is intentionally
different from Stage E's scientific curve extraction (ftan_grid.weighted_centroid_curve),
which returns NaN for undetected rows instead — correct for reporting, wrong for a metric
that must never blow up mid-training.
"""

import torch
import torch.nn.functional as F

from .ftan_grid import PERIOD_BINS, VEL_BINS, VEL_MIN, VEL_MAX


def _align(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    if pred.shape != target.shape:
        pred = F.interpolate(pred, size=target.shape[2:], mode="bilinear", align_corners=False)
    return pred


def pixel_accuracy(pred: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> float:
    pred = _align(pred, target)
    pred_b = torch.sigmoid(pred) > threshold
    target_b = target > threshold
    return ((pred_b == target_b).float().sum() / torch.numel(pred_b)).item()


def iou_score(pred: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> float:
    pred = _align(pred, target)
    pred_b = torch.sigmoid(pred) > threshold
    target_b = target > threshold
    inter = (pred_b & target_b).float().sum()
    union = (pred_b | target_b).float().sum()
    if union == 0:
        return 1.0
    return (inter / union).item()


def dice_coefficient(pred: torch.Tensor, target: torch.Tensor, threshold: float = 0.5) -> float:
    pred = _align(pred, target)
    pred_b = torch.sigmoid(pred) > threshold
    target_b = target > threshold
    inter = (pred_b & target_b).float().sum()
    denom = pred_b.float().sum() + target_b.float().sum()
    if denom == 0:
        return 1.0
    return (2 * inter / denom).item()


def velocity_error(pred: torch.Tensor, target: torch.Tensor) -> float:
    """Weighted-centroid velocity RMSE (km/s) on curve rows (0:PERIOD_BINS)."""
    pred = _align(pred, target)
    pred_prob = torch.sigmoid(pred)
    pred_curve = pred_prob[:, :, :PERIOD_BINS, :]
    target_curve = target[:, :, :PERIOD_BINS, :]

    bins = torch.arange(VEL_BINS, dtype=torch.float32, device=pred.device).view(1, 1, 1, VEL_BINS)

    pred_norm = pred_curve / (pred_curve.sum(dim=-1, keepdim=True) + 1e-8)
    target_norm = target_curve / (target_curve.sum(dim=-1, keepdim=True) + 1e-8)

    pred_vel = (pred_norm * bins).sum(dim=-1) * (VEL_MAX - VEL_MIN) / VEL_BINS + VEL_MIN
    target_vel = (target_norm * bins).sum(dim=-1) * (VEL_MAX - VEL_MIN) / VEL_BINS + VEL_MIN

    return torch.sqrt(F.mse_loss(pred_vel, target_vel)).item()


# --- Per-row-peak decoder (Stage D ablations) ------------------------------------------
# velocity_error above takes the soft centroid of the whole row, so probability mass far
# from the curve (a too-wide band, side lobes) pulls the estimate. The decoder below reads
# each period row at its most probable velocity bin instead, refined by a probability-
# weighted mean over +/- PEAK_HALF_WINDOW bins around that peak. The target side is the
# same target-mask centroid as velocity_error, over the same PERIOD_BINS curve rows, so
# the two decoders are compared against an identical reference.

PEAK_HALF_WINDOW = 3


def _bins_to_velocity(b: torch.Tensor) -> torch.Tensor:
    # same bin -> km/s mapping as velocity_error
    return b * (VEL_MAX - VEL_MIN) / VEL_BINS + VEL_MIN


def _target_row_velocity(target: torch.Tensor) -> torch.Tensor:
    """(B, 1, PERIOD_BINS) target-mask centroid velocity, exactly as in velocity_error."""
    t = target[:, :, :PERIOD_BINS, :]
    bins = torch.arange(VEL_BINS, dtype=torch.float32, device=t.device)
    return _bins_to_velocity((t / (t.sum(dim=-1, keepdim=True) + 1e-8) * bins).sum(dim=-1))


def soft_row_velocity(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """(B, 1, PERIOD_BINS) soft-centroid velocity of each row, exactly as in velocity_error."""
    pred = _align(pred, target)
    p = torch.sigmoid(pred)[:, :, :PERIOD_BINS, :]
    bins = torch.arange(VEL_BINS, dtype=torch.float32, device=p.device)
    return _bins_to_velocity((p / (p.sum(dim=-1, keepdim=True) + 1e-8) * bins).sum(dim=-1))


def peak_row_velocity(pred: torch.Tensor, target: torch.Tensor,
                      half_window: int = PEAK_HALF_WINDOW) -> torch.Tensor:
    """(B, 1, PERIOD_BINS) per-row-peak velocity: argmax bin of the sigmoid probability,
    refined by the probability-weighted mean bin over [peak - half_window, peak +
    half_window] (clipped to the grid, no padding)."""
    pred = _align(pred, target)
    p = torch.sigmoid(pred)[:, :, :PERIOD_BINS, :]
    peak = p.argmax(dim=-1, keepdim=True)                                      # (B,1,R,1)
    offs = torch.arange(-half_window, half_window + 1, device=p.device)       # (W,)
    idx = peak + offs                                                          # (B,1,R,W)
    valid = (idx >= 0) & (idx < VEL_BINS)
    idx_c = idx.clamp(0, VEL_BINS - 1)
    w = torch.gather(p, -1, idx_c) * valid
    b = (w * idx_c).sum(dim=-1) / w.sum(dim=-1).clamp_min(1e-12)
    return _bins_to_velocity(b)


def velocity_sq_err(pred: torch.Tensor, target: torch.Tensor, decoder: str = "soft") -> torch.Tensor:
    """(B,) per-sample sum over the PERIOD_BINS curve rows of the squared row-velocity
    error (km/s)^2, for pooling: pooled RMSE = sqrt(sum / (n_samples * PERIOD_BINS))."""
    fn = {"soft": soft_row_velocity, "peak": peak_row_velocity}[decoder]
    return ((fn(pred, target) - _target_row_velocity(target)) ** 2).flatten(1).sum(dim=1)


def velocity_error_peak(pred: torch.Tensor, target: torch.Tensor,
                        half_window: int = PEAK_HALF_WINDOW) -> float:
    """Per-row-peak velocity RMSE (km/s) on curve rows, same reduction as velocity_error
    (RMSE over all rows of the batch)."""
    err = peak_row_velocity(pred, target, half_window) - _target_row_velocity(target)
    return torch.sqrt((err ** 2).mean()).item()
