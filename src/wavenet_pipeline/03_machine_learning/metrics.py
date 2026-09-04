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
