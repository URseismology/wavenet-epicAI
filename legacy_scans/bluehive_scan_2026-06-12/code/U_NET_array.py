#!/usr/bin/env python3
"""
UNet segmentation for FTAN dispersion curve extraction.

Input  : (1, 80, 400) - per-row normalized observed FTAN (rows 0-75)
                        + normalized extracted curve row (row 76)
                        + 3 zero-padded rows (rows 77-79)
Target : (1, 80, 400) - binary mask with thin band (±MASK_WIDTH bins) around
                        the theoretical group velocity curve (rows 0-75),
                        zeros in rows 76-79.
Output : raw logits -> sigmoid applied externally for loss/metrics/viz
"""

import os
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, random_split
from torch.optim import Adam
from torch.optim.lr_scheduler import ReduceLROnPlateau
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime
import csv
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PERIOD_BINS = 76
VEL_BINS    = 400
VEL_MIN     = 0.5
VEL_MAX     = 4.5
PAD_ROWS    = 4         # pad 76 -> 80
MASK_WIDTH  = 2         # ±2 bins around theoretical curve


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
class FTANSegDataset(Dataset):
    def __init__(self, numerical_dir, metadata_file):
        self.numerical_dir = Path(numerical_dir)

        self.samples = []
        with open(metadata_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row['data_type'] == 'observed'
                        and row.get('observed_array')
                        and row.get('theoretical_array')):
                    self.samples.append({
                        'observed_array':    self.numerical_dir / row['observed_array'],
                        'theoretical_array': self.numerical_dir / row['theoretical_array'],
                        'metadata': row
                    })

        print(f"Loaded {len(self.samples)} samples")

    def _make_curve_mask(self, theoretical_raw):
        """Build (76, 400) binary mask from theoretical curve row (row 76)."""
        mask     = np.zeros((PERIOD_BINS, VEL_BINS), dtype=np.float32)
        curve_vel = theoretical_raw[76, :]

        for p in range(PERIOD_BINS):
            v = curve_vel[p]
            if v <= 0.0:
                continue
            bin_idx = int((v - VEL_MIN) / (VEL_MAX - VEL_MIN) * VEL_BINS)
            bin_idx = int(np.clip(bin_idx, 0, VEL_BINS - 1))
            lo = max(0, bin_idx - MASK_WIDTH)
            hi = min(VEL_BINS - 1, bin_idx + MASK_WIDTH)
            mask[p, lo:hi+1] = 1.0

        return mask

    def _normalize_input(self, observed_raw):
        """
        FTAN rows already per-row normalized [0,1] by generate_ml_ftan.py.
        Only the curve row needs unit-range normalization.
        """
        ftan  = observed_raw[:PERIOD_BINS, :].copy()
        curve = observed_raw[PERIOD_BINS:, :].copy()

        ftan  = np.clip(ftan, 0.0, 1.0)
        curve = np.clip((curve - VEL_MIN) / (VEL_MAX - VEL_MIN), 0.0, 1.0)

        return np.vstack([ftan, curve]).astype(np.float32)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]

        observed_raw    = np.load(s['observed_array'])
        theoretical_raw = np.load(s['theoretical_array'])

        inp  = self._normalize_input(observed_raw)             # (77, 400)
        inp  = np.pad(inp,  ((0, PAD_ROWS), (0, 0)),
                      mode='constant', constant_values=0)      # (80, 400)

        mask = self._make_curve_mask(theoretical_raw)          # (76, 400)
        tgt  = np.pad(mask, ((0, PAD_ROWS), (0, 0)),
                      mode='constant', constant_values=0)      # (80, 400)

        return (torch.from_numpy(inp).unsqueeze(0),            # (1, 80, 400)
                torch.from_numpy(tgt).unsqueeze(0),            # (1, 80, 400)
                s['metadata'])


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class DoubleConv(nn.Module):
    def __init__(self, in_ch, out_ch, dropout=0.0):
        super().__init__()
        layers = [
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        ]
        if dropout > 0:
            layers.append(nn.Dropout2d(dropout))
        self.block = nn.Sequential(*layers)

    def forward(self, x):
        return self.block(x)


class UNetSeg(nn.Module):
    def __init__(self, in_channels=1, out_channels=1, features=(16, 32, 64, 128)):
        super().__init__()

        self.encoders = nn.ModuleList()
        self.pools    = nn.ModuleList()
        self.upconvs  = nn.ModuleList()
        self.decoders = nn.ModuleList()

        ch = in_channels
        for f in features:
            drop = 0.1 if f <= 32 else 0.2
            self.encoders.append(DoubleConv(ch, f, dropout=drop))
            self.pools.append(nn.MaxPool2d(2))
            ch = f

        self.bottleneck = DoubleConv(ch, ch * 2, dropout=0.3)
        ch = ch * 2

        for f in reversed(features):
            self.upconvs.append(nn.ConvTranspose2d(ch, f, kernel_size=2, stride=2))
            drop = 0.1 if f <= 32 else 0.2
            self.decoders.append(DoubleConv(f * 2, f, dropout=drop))
            ch = f

        self.head = nn.Conv2d(ch, out_channels, kernel_size=1)

    def forward(self, x):
        input_size = x.shape[2:]
        skips = []

        for enc, pool in zip(self.encoders, self.pools):
            x = enc(x)
            skips.append(x)
            x = pool(x)

        x = self.bottleneck(x)

        for up, dec, skip in zip(self.upconvs, self.decoders, reversed(skips)):
            x = up(x)
            if x.shape[2:] != skip.shape[2:]:
                x = F.interpolate(x, size=skip.shape[2:], mode='bilinear', align_corners=False)
            x = torch.cat([skip, x], dim=1)
            x = dec(x)

        x = self.head(x)
        if x.shape[2:] != input_size:
            x = F.interpolate(x, size=input_size, mode='bilinear', align_corners=False)
        if x.shape[2:] != input_size:
            x = F.interpolate(x, size=input_size, mode='bilinear', align_corners=False)

        # Raw logits - sigmoid applied externally
        return x


# ---------------------------------------------------------------------------
# Loss functions (from proven image pipeline, adapted for logit input)
# ---------------------------------------------------------------------------
class FocalLoss(nn.Module):
    def __init__(self, alpha=0.25, gamma=2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, pred, target):
        if pred.shape != target.shape:
            pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
        bce            = F.binary_cross_entropy_with_logits(pred, target, reduction='none')
        pred_prob      = torch.sigmoid(pred)
        p_t            = pred_prob * target + (1 - pred_prob) * (1 - target)
        alpha_factor   = self.alpha * target + (1 - self.alpha) * (1 - target)
        modulating     = (1 - p_t) ** self.gamma
        return (alpha_factor * modulating * bce).mean()


class DiceLoss(nn.Module):
    def __init__(self, smooth=1.0):
        super().__init__()
        self.smooth = smooth

    def forward(self, pred, target):
        pred   = torch.sigmoid(pred).contiguous().view(-1)
        target = target.contiguous().view(-1)
        inter  = (pred * target).sum()
        return 1 - (2 * inter + self.smooth) / (pred.sum() + target.sum() + self.smooth)


class WeightedBCELoss(nn.Module):
    def __init__(self, pos_weight=15.0):
        super().__init__()
        self.pos_weight = pos_weight

    def forward(self, pred, target):
        weight = target * self.pos_weight + (1 - target) * 1.0
        bce    = F.binary_cross_entropy_with_logits(pred, target, reduction='none')
        return (bce * weight).mean()


class SharpeningLoss(nn.Module):
    def forward(self, pred, target):
        prob       = torch.sigmoid(pred).clamp(1e-7, 1 - 1e-7)
        entropy    = -prob * torch.log(prob) - (1 - prob) * torch.log(1 - prob)
        curve_mask = (target > 0.5).float()
        n_curve    = curve_mask.sum()
        if n_curve > 0:
            return (entropy * curve_mask).sum() / n_curve
        return pred.new_zeros(1).squeeze()


class CombinedLoss(nn.Module):
    def __init__(self, focal_weight=0.3, dice_weight=0.3,
                 bce_weight=0.2, sharpen_weight=0.2):
        super().__init__()
        self.focal_w   = focal_weight
        self.dice_w    = dice_weight
        self.bce_w     = bce_weight
        self.sharpen_w = sharpen_weight

        self.focal   = FocalLoss(alpha=0.25, gamma=2.0)
        self.dice    = DiceLoss()
        self.bce     = WeightedBCELoss(pos_weight=15.0)
        self.sharpen = SharpeningLoss()

    def forward(self, pred, target):
        if pred.shape != target.shape:
            pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
        return (self.focal_w   * self.focal(pred, target)
              + self.dice_w    * self.dice(pred, target)
              + self.bce_w     * self.bce(pred, target)
              + self.sharpen_w * self.sharpen(pred, target))


# ---------------------------------------------------------------------------
# Metrics  (pred = raw logits)
# ---------------------------------------------------------------------------
def pixel_accuracy(pred, target, threshold=0.5):
    if pred.shape != target.shape:
        pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
    pred   = torch.sigmoid(pred) > threshold
    target = target > threshold
    return ((pred == target).float().sum() / torch.numel(pred)).item()


def iou_score(pred, target, threshold=0.5):
    if pred.shape != target.shape:
        pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
    pred   = torch.sigmoid(pred) > threshold
    target = target > threshold
    inter  = (pred & target).float().sum()
    union  = (pred | target).float().sum()
    if union == 0:
        return 1.0
    return (inter / union).item()


def dice_coefficient(pred, target, threshold=0.5):
    if pred.shape != target.shape:
        pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
    pred   = torch.sigmoid(pred) > threshold
    target = target > threshold
    inter  = (pred & target).float().sum()
    denom  = pred.float().sum() + target.float().sum()
    if denom == 0:
        return 1.0
    return (2 * inter / denom).item()


def velocity_error(pred, target):
    """Weighted centroid velocity RMSE on curve rows (rows 0-75)."""
    if pred.shape != target.shape:
        pred = F.interpolate(pred, size=target.shape[2:], mode='bilinear', align_corners=False)
    pred_prob    = torch.sigmoid(pred)
    pred_curve   = pred_prob[:, :, :PERIOD_BINS, :]
    target_curve = target[:, :, :PERIOD_BINS, :]

    bins     = torch.arange(VEL_BINS, dtype=torch.float32, device=pred.device)
    bins     = bins.view(1, 1, 1, VEL_BINS)

    pred_norm   = pred_curve   / (pred_curve.sum(dim=-1, keepdim=True)   + 1e-8)
    target_norm = target_curve / (target_curve.sum(dim=-1, keepdim=True) + 1e-8)

    pred_vel   = (pred_norm   * bins).sum(dim=-1) * (VEL_MAX - VEL_MIN) / VEL_BINS + VEL_MIN
    target_vel = (target_norm * bins).sum(dim=-1) * (VEL_MAX - VEL_MIN) / VEL_BINS + VEL_MIN

    return torch.sqrt(F.mse_loss(pred_vel, target_vel)).item()


# ---------------------------------------------------------------------------
# Early stopping
# ---------------------------------------------------------------------------
class EarlyStopping:
    def __init__(self, patience=25, delta=0.0, path='checkpoint.pt', verbose=True):
        self.patience   = patience
        self.delta      = delta
        self.path       = path
        self.verbose    = verbose
        self.counter    = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.inf

    def __call__(self, val_loss, model):
        score = -val_loss
        if self.best_score is None:
            self.best_score = score
            self._save(val_loss, model)
        elif score < self.best_score + self.delta:
            self.counter += 1
            if self.verbose:
                print(f'  EarlyStopping: {self.counter}/{self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            self._save(val_loss, model)
            self.counter = 0

    def _save(self, val_loss, model):
        if self.verbose:
            print(f'  Val loss improved ({self.val_loss_min:.6f} -> {val_loss:.6f}). Saving.')
        torch.save(model.state_dict(), self.path)
        self.val_loss_min = val_loss


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------
def _period_ticks(ax, n_rows=PERIOD_BINS, pmin=1.0, pmax=20.0):
    ticks  = np.linspace(0, n_rows - 1, 6, dtype=int)
    labels = [f"{pmin + (r / (n_rows-1)) * (pmax - pmin):.0f}s" for r in ticks]
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels)


def _vel_ticks(ax, n_cols=VEL_BINS):
    ticks  = np.linspace(0, n_cols - 1, 6, dtype=int)
    labels = [f"{VEL_MIN + (c / (n_cols-1)) * (VEL_MAX - VEL_MIN):.1f}" for c in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)


def save_validation_samples(model, dataloader, epoch, save_dir, device, num_samples=3):
    model.eval()
    save_dir = Path(save_dir)

    label     = f"epoch_{epoch:03d}" if isinstance(epoch, int) else f"epoch_{epoch}"
    epoch_dir = save_dir / label
    epoch_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(num_samples, 4,
                             figsize=(20, 5 * num_samples),
                             facecolor='#1a1a1a')
    if num_samples == 1:
        axes = axes.reshape(1, -1)

    collected = 0
    with torch.no_grad():
        for inputs, targets, metadata_batch in dataloader:
            if collected >= num_samples:
                break

            inputs  = inputs.to(device)
            targets = targets.to(device)
            logits  = model(inputs)
            probs   = torch.sigmoid(logits)

            for b in range(inputs.size(0)):
                if collected >= num_samples:
                    break

                inp_np  = inputs[b, 0, :PERIOD_BINS, :].cpu().numpy()
                tgt_np  = targets[b, 0, :PERIOD_BINS, :].cpu().numpy()
                out_np  = probs[b, 0, :PERIOD_BINS, :].cpu().numpy()
                # Curve guidance row
                curve_np = inputs[b, 0, PERIOD_BINS, :].cpu().numpy()
                curve_2d = np.tile(curve_np, (PERIOD_BINS, 1))

                def get_val(v):
                    return v[b] if isinstance(v, (list, tuple)) else v

                model_name = get_val(metadata_batch['model'])
                exp_num    = get_val(metadata_batch['experiment'])
                az_start   = get_val(metadata_batch['azimuth_start'])
                az_end     = get_val(metadata_batch['azimuth_end'])
                title_str  = f"Exp{exp_num} | {model_name} | az{az_start}-{az_end}"

                row = collected
                for col, (arr, cmap, title) in enumerate([
                    (inp_np,   'inferno', 'Input (Observed FTAN)'),
                    (curve_2d, 'plasma',  'Curve Guidance Row'),
                    (tgt_np,   'gray',    'Target (Curve Mask)'),
                    (out_np,   'hot',     f'Prediction (max={out_np.max():.2f})'),
                ]):
                    ax = axes[row, col]
                    ax.set_facecolor('black')
                    ax.imshow(arr, cmap=cmap, aspect='auto',
                              vmin=0, vmax=1, origin='upper',
                              interpolation='nearest')
                    ax.set_title(title, color='white', fontsize=9)
                    ax.set_xlabel('Group Velocity (km/s)', color='white', fontsize=7)
                    ax.set_ylabel('Period (s)', color='white', fontsize=7)
                    ax.tick_params(colors='white', labelsize=6)
                    for sp in ax.spines.values():
                        sp.set_edgecolor('#555555')
                    _vel_ticks(ax)
                    _period_ticks(ax)

                axes[row, 0].set_title(
                    f"{title_str}\n{axes[row, 0].get_title()}",
                    color='white', fontsize=9
                )

                collected += 1

    plt.tight_layout()
    plt.savefig(epoch_dir / 'validation_samples.png',
                dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close()


def plot_training_curves(train_losses, val_losses, train_m, val_m, save_path):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    panels = [
        ('Loss',             train_losses,          val_losses),
        ('Dice',             train_m['dice'],        val_m['dice']),
        ('IoU',              train_m['iou'],         val_m['iou']),
        ('Vel Error (km/s)', train_m['vel_error'],   val_m['vel_error']),
    ]
    for ax, (ylabel, tr, va) in zip(axes.flatten(), panels):
        ax.plot(tr, label='Train', linewidth=2)
        ax.plot(va, label='Val',   linewidth=2, linestyle='--')
        ax.set_xlabel('Epoch')
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel)
        ax.legend()
        ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(save_path, dpi=150)
    plt.close()


# ---------------------------------------------------------------------------
# Train / validate loops
# ---------------------------------------------------------------------------
def train_epoch(model, loader, criterion, optimizer, device):
    model.train()
    tot_loss = tot_acc = tot_iou = tot_dice = tot_vel = 0.0

    for inputs, targets, _ in tqdm(loader, desc='  Train', leave=False):
        inputs  = inputs.to(device)
        targets = targets.to(device)

        optimizer.zero_grad()
        logits = model(inputs)
        loss   = criterion(logits, targets)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        n = inputs.size(0)
        tot_loss += loss.item()              * n
        tot_acc  += pixel_accuracy(logits, targets) * n
        tot_iou  += iou_score(logits, targets)      * n
        tot_dice += dice_coefficient(logits, targets) * n
        tot_vel  += velocity_error(logits, targets)  * n

    N = len(loader.dataset)
    return tot_loss/N, tot_acc/N, tot_iou/N, tot_dice/N, tot_vel/N


def validate_epoch(model, loader, criterion, device):
    model.eval()
    tot_loss = tot_acc = tot_iou = tot_dice = tot_vel = 0.0

    with torch.no_grad():
        for inputs, targets, _ in tqdm(loader, desc='  Val  ', leave=False):
            inputs  = inputs.to(device)
            targets = targets.to(device)
            logits  = model(inputs)
            loss    = criterion(logits, targets)

            n = inputs.size(0)
            tot_loss += loss.item()                    * n
            tot_acc  += pixel_accuracy(logits, targets)  * n
            tot_iou  += iou_score(logits, targets)        * n
            tot_dice += dice_coefficient(logits, targets) * n
            tot_vel  += velocity_error(logits, targets)   * n

    N = len(loader.dataset)
    return tot_loss/N, tot_acc/N, tot_iou/N, tot_dice/N, tot_vel/N


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------
def train_model(config):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    run_dir   = Path(config['output_dir']) / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {run_dir}")

    with open(run_dir / 'config.json', 'w') as f:
        json.dump(config, f, indent=4)

    dataset    = FTANSegDataset(config['numerical_dir'], config['metadata_file'])
    train_size = int(0.8 * len(dataset))
    val_size   = len(dataset) - train_size
    train_ds, val_ds = random_split(dataset, [train_size, val_size])
    print(f"Train: {train_size}  |  Val: {val_size}")

    train_loader = DataLoader(train_ds, batch_size=config['batch_size'],
                              shuffle=True,  num_workers=4, pin_memory=True)
    val_loader   = DataLoader(val_ds,   batch_size=config['batch_size'],
                              shuffle=False, num_workers=4, pin_memory=True)

    model    = UNetSeg(in_channels=1, out_channels=1).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {n_params:,}")

    criterion = CombinedLoss(focal_weight=0.3, dice_weight=0.3,
                             bce_weight=0.2, sharpen_weight=0.2)
    optimizer = Adam(model.parameters(), lr=config['learning_rate'], weight_decay=1e-5)
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=5, verbose=True)
    stopper   = EarlyStopping(patience=config['patience'], path=run_dir / 'model_best.pt')

    train_losses = []; val_losses = []
    train_m = {'acc': [], 'iou': [], 'dice': [], 'vel_error': []}
    val_m   = {'acc': [], 'iou': [], 'dice': [], 'vel_error': []}

    print(f"\nTraining for {config['num_epochs']} epochs ...\n")

    for epoch in range(config['num_epochs']):
        print(f"Epoch {epoch+1}/{config['num_epochs']}")

        tr = train_epoch(model, train_loader, criterion, optimizer, device)
        va = validate_epoch(model, val_loader, criterion, device)

        scheduler.step(va[0])

        train_losses.append(tr[0]); val_losses.append(va[0])
        for key, ti, vi in zip(('acc','iou','dice','vel_error'), tr[1:], va[1:]):
            train_m[key].append(ti)
            val_m[key].append(vi)

        print(f"  Train  loss={tr[0]:.4f}  acc={tr[1]:.3f}  iou={tr[2]:.3f}  "
              f"dice={tr[3]:.3f}  vel={tr[4]:.4f} km/s")
        print(f"  Val    loss={va[0]:.4f}  acc={va[1]:.3f}  iou={va[2]:.3f}  "
              f"dice={va[3]:.3f}  vel={va[4]:.4f} km/s")

        save_validation_samples(model, val_loader, epoch+1,
                                run_dir / 'samples', device, num_samples=3)
        plot_training_curves(
            train_losses, val_losses,
            {'dice': train_m['dice'], 'iou': train_m['iou'], 'vel_error': train_m['vel_error']},
            {'dice': val_m['dice'],   'iou': val_m['iou'],   'vel_error': val_m['vel_error']},
            run_dir / 'training_curves.png'
        )

        stopper(va[0], model)
        if stopper.early_stop:
            print("\nEarly stopping.")
            break

    model.load_state_dict(torch.load(run_dir / 'model_best.pt'))
    va = validate_epoch(model, val_loader, criterion, device)
    print(f"\nBest model  loss={va[0]:.4f}  acc={va[1]:.3f}  iou={va[2]:.3f}  "
          f"dice={va[3]:.3f}  vel={va[4]:.4f} km/s")

    save_validation_samples(model, val_loader, 'final',
                            run_dir / 'samples', device, num_samples=4)
    print(f"\nDone. Results: {run_dir}")
    return model, run_dir


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    config = {
        'numerical_dir': 'FTAN_NUMERICAL',
        'metadata_file':  'FTAN_ML_INPUT/metadata.csv',
        'output_dir':     'FTAN_SEG_MODELS',
        'batch_size':     16,
        'learning_rate':  1e-4,
        'num_epochs':     200,
        'patience':       25,
    }

    print("UNet Segmentation - FTAN Dispersion Curve Extraction")
    print("=" * 55)
    for k, v in config.items():
        print(f"  {k}: {v}")
    print()

    train_model(config)


if __name__ == "__main__":
    main()