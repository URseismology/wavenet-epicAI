"""
Loss functions for FTAN mask segmentation. Promoted verbatim from
chrisScripts/julyncf_pipeline/ML_pipeline/U_NET_array.py (byte-identical to the
"official" src/machine_learning/U_NET_array.py copy) — the architecture/loss design
itself is not new, only its promotion into a documented, schema-correct pipeline is.

CombinedLoss's weights (0.3/0.3/0.2/0.2) and pos_weight=15.0 are kept as the starting
point (reasonable prior given the mask is ~1.7% positive pixels), but their tuning
provenance against *this* schema is unverifiable from the inherited code alone — treat
as a starting point pending a sensitivity check once real training is underway, per the
plan's cross-cutting "verify, don't inherit" principle, not as a proven-final answer.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class FocalLoss(nn.Module):
    def __init__(self, alpha: float = 0.25, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        if pred.shape != target.shape:
            pred = F.interpolate(pred, size=target.shape[2:], mode="bilinear", align_corners=False)
        bce = F.binary_cross_entropy_with_logits(pred, target, reduction="none")
        pred_prob = torch.sigmoid(pred)
        p_t = pred_prob * target + (1 - pred_prob) * (1 - target)
        alpha_factor = self.alpha * target + (1 - self.alpha) * (1 - target)
        modulating = (1 - p_t) ** self.gamma
        return (alpha_factor * modulating * bce).mean()


class DiceLoss(nn.Module):
    def __init__(self, smooth: float = 1.0):
        super().__init__()
        self.smooth = smooth

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        pred = torch.sigmoid(pred).contiguous().view(-1)
        target = target.contiguous().view(-1)
        inter = (pred * target).sum()
        return 1 - (2 * inter + self.smooth) / (pred.sum() + target.sum() + self.smooth)


class WeightedBCELoss(nn.Module):
    def __init__(self, pos_weight: float = 15.0):
        super().__init__()
        self.pos_weight = pos_weight

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        weight = target * self.pos_weight + (1 - target) * 1.0
        bce = F.binary_cross_entropy_with_logits(pred, target, reduction="none")
        return (bce * weight).mean()


class SharpeningLoss(nn.Module):
    """Entropy penalty confined to curve pixels only (encourages confident predictions
    exactly where the mask is positive, rather than penalizing entropy everywhere)."""

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        prob = torch.sigmoid(pred).clamp(1e-7, 1 - 1e-7)
        entropy = -prob * torch.log(prob) - (1 - prob) * torch.log(1 - prob)
        curve_mask = (target > 0.5).float()
        n_curve = curve_mask.sum()
        if n_curve > 0:
            return (entropy * curve_mask).sum() / n_curve
        return pred.new_zeros(1).squeeze()


class CombinedLoss(nn.Module):
    def __init__(self, focal_weight: float = 0.3, dice_weight: float = 0.3,
                 bce_weight: float = 0.2, sharpen_weight: float = 0.2):
        super().__init__()
        self.focal_w = focal_weight
        self.dice_w = dice_weight
        self.bce_w = bce_weight
        self.sharpen_w = sharpen_weight

        self.focal = FocalLoss(alpha=0.25, gamma=2.0)
        self.dice = DiceLoss()
        self.bce = WeightedBCELoss(pos_weight=15.0)
        self.sharpen = SharpeningLoss()

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        if pred.shape != target.shape:
            pred = F.interpolate(pred, size=target.shape[2:], mode="bilinear", align_corners=False)
        return (self.focal_w * self.focal(pred, target)
                + self.dice_w * self.dice(pred, target)
                + self.bce_w * self.bce(pred, target)
                + self.sharpen_w * self.sharpen(pred, target))
