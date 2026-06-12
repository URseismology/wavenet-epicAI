#!/usr/bin/env python3
"""
Train UNet for FTAN Dispersion Curve Segmentation - FIXED FOR OLD GPUs

Features:
- Binary target masks (threshold 0.1)
- Focal Loss + Dice Loss + Weighted BCE (Sharpening disabled for GPU compatibility)
- Metadata display above each validation sample
- Format: Exp# | model | az###-###° | ###km | rad###-###km
"""

import os
import sys
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, random_split
from torch.optim import Adam
from torch.optim.lr_scheduler import ReduceLROnPlateau
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from pathlib import Path
from datetime import datetime
import csv
from tqdm import tqdm
import torchvision.transforms.functional as TF
import random


# ============================================================================
# Dataset
# ============================================================================

class FTANDataset(Dataset):
    def __init__(self, data_dir, metadata_file, transform=None, augment=False):
        self.data_dir = Path(data_dir)
        self.transform = transform
        self.augment = augment
        
        # Load metadata
        self.pairs = []
        with open(metadata_file, 'r') as f:
            reader = csv.DictReader(f)
            metadata = list(reader)
        
        # Create input-target pairs
        observed_files = [m for m in metadata if m['data_type'] == 'observed']
        
        for obs in observed_files:
            theo_filename = obs['filename'].replace('ftan_', 'ftan_theoretical_')
            
            input_path = self.data_dir / obs['filename']
            target_path = self.data_dir / 'theoretical' / theo_filename
            
            if input_path.exists() and target_path.exists():
                self.pairs.append({
                    'input': input_path,
                    'target': target_path,
                    'metadata': obs
                })
    
    def __len__(self):
        return len(self.pairs)
    
    def __getitem__(self, idx):
        pair = self.pairs[idx]
        
        # Load images
        input_img = Image.open(pair['input']).convert('RGB')
        target_img = Image.open(pair['target']).convert('RGB')
        
        # Convert to tensors
        input_tensor = TF.to_tensor(input_img)
        target_tensor = TF.to_tensor(target_img)
        
        # Binarize target
        target_gray = target_tensor.mean(dim=0, keepdim=True)
        target_binary = (target_gray > 0.1).float()
        
        # Apply augmentation
        if self.augment:
            if random.random() > 0.5:
                input_tensor = TF.hflip(input_tensor)
                target_binary = TF.hflip(target_binary)
            
            if random.random() > 0.5:
                input_tensor = TF.vflip(input_tensor)
                target_binary = TF.vflip(target_binary)
            
            angle = random.uniform(-10, 10)
            input_tensor = TF.rotate(input_tensor, angle)
            target_binary = TF.rotate(target_binary, angle)
        
        return input_tensor, target_binary, pair['metadata']


# ============================================================================
# UNet Model
# ============================================================================

class UNet(nn.Module):
    def __init__(self, in_channels=3, out_channels=1):
        super(UNet, self).__init__()
        
        self.elu = nn.ELU()
        self.maxpool = nn.MaxPool2d(kernel_size=2, stride=2)
        self.dropout1 = nn.Dropout(0.1)
        self.dropout2 = nn.Dropout(0.2)
        self.dropout3 = nn.Dropout(0.3)
        
        # Encoder
        self.conv11 = nn.Conv2d(in_channels, 16, kernel_size=3, padding=1)
        self.conv12 = nn.Conv2d(16, 16, kernel_size=3, padding=1)
        
        self.conv21 = nn.Conv2d(16, 32, kernel_size=3, padding=1)
        self.conv22 = nn.Conv2d(32, 32, kernel_size=3, padding=1)
        
        self.conv31 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.conv32 = nn.Conv2d(64, 64, kernel_size=3, padding=1)
        
        self.conv41 = nn.Conv2d(64, 128, kernel_size=3, padding=1)
        self.conv42 = nn.Conv2d(128, 128, kernel_size=3, padding=1)
        
        # Bottleneck
        self.conv51 = nn.Conv2d(128, 256, kernel_size=3, padding=1)
        self.conv52 = nn.Conv2d(256, 256, kernel_size=3, padding=1)
        
        # Decoder
        self.uconv6 = nn.ConvTranspose2d(256, 128, kernel_size=2, stride=2)
        self.conv61 = nn.Conv2d(256, 128, kernel_size=3, padding=1)
        self.conv62 = nn.Conv2d(128, 128, kernel_size=3, padding=1)
        
        self.uconv7 = nn.ConvTranspose2d(128, 64, kernel_size=2, stride=2)
        self.conv71 = nn.Conv2d(128, 64, kernel_size=3, padding=1)
        self.conv72 = nn.Conv2d(64, 64, kernel_size=3, padding=1)
        
        self.uconv8 = nn.ConvTranspose2d(64, 32, kernel_size=2, stride=2)
        self.conv81 = nn.Conv2d(64, 32, kernel_size=3, padding=1)
        self.conv82 = nn.Conv2d(32, 32, kernel_size=3, padding=1)
        
        self.uconv9 = nn.ConvTranspose2d(32, 16, kernel_size=2, stride=2)
        self.conv91 = nn.Conv2d(32, 16, kernel_size=3, padding=1)
        self.conv92 = nn.Conv2d(16, 16, kernel_size=3, padding=1)
        
        # Output
        self.conv93 = nn.Conv2d(16, out_channels, kernel_size=1, padding=0)
    
    def forward(self, x):
        # Encoder
        x = self.conv11(x)
        x = self.elu(x)
        x = self.dropout1(x)
        x = self.conv12(x)
        x1d = self.elu(x)
        x = self.maxpool(x1d)
        
        x = self.conv21(x)
        x = self.elu(x)
        x = self.dropout1(x)
        x = self.conv22(x)
        x2d = self.elu(x)
        x = self.maxpool(x2d)
        
        x = self.conv31(x)
        x = self.elu(x)
        x = self.dropout2(x)
        x = self.conv32(x)
        x3d = self.elu(x)
        x = self.maxpool(x3d)
        
        x = self.conv41(x)
        x = self.elu(x)
        x = self.dropout2(x)
        x = self.conv42(x)
        x4d = self.elu(x)
        x = self.maxpool(x4d)
        
        # Bottleneck
        x = self.conv51(x)
        x = self.elu(x)
        x = self.dropout3(x)
        x = self.conv52(x)
        x5d = self.elu(x)
        
        # Decoder
        x6u = self.uconv6(x5d)
        x = torch.cat((x4d, x6u), 1)
        x = self.conv61(x)
        x = self.elu(x)
        x = self.dropout2(x)
        x = self.conv62(x)
        x = self.elu(x)
        
        x7u = self.uconv7(x)
        x = torch.cat((x3d, x7u), 1)
        x = self.conv71(x)
        x = self.elu(x)
        x = self.dropout2(x)
        x = self.conv72(x)
        x = self.elu(x)
        
        x8u = self.uconv8(x)
        x = torch.cat((x2d, x8u), 1)
        x = self.conv81(x)
        x = self.elu(x)
        x = self.dropout1(x)
        x = self.conv82(x)
        x = self.elu(x)
        
        x9u = self.uconv9(x)
        x = torch.cat((x1d, x9u), 1)
        x = self.conv91(x)
        x = self.elu(x)
        x = self.dropout1(x)
        x = self.conv92(x)
        x = self.elu(x)
        
        # Output
        x = self.conv93(x)
        return x


# ============================================================================
# Loss Functions
# ============================================================================

class FocalLoss(nn.Module):
    def __init__(self, alpha=0.25, gamma=2.0):
        super(FocalLoss, self).__init__()
        self.alpha = alpha
        self.gamma = gamma
    
    def forward(self, pred, target):
        bce = F.binary_cross_entropy_with_logits(pred, target, reduction='none')
        pred_prob = torch.sigmoid(pred)
        p_t = pred_prob * target + (1 - pred_prob) * (1 - target)
        alpha_factor = self.alpha * target + (1 - self.alpha) * (1 - target)
        modulating_factor = (1 - p_t) ** self.gamma
        loss = alpha_factor * modulating_factor * bce
        return loss.mean()


class DiceLoss(nn.Module):
    def __init__(self, smooth=1.0):
        super(DiceLoss, self).__init__()
        self.smooth = smooth
    
    def forward(self, pred, target):
        pred = torch.sigmoid(pred)
        pred = pred.contiguous().view(-1)
        target = target.contiguous().view(-1)
        
        intersection = (pred * target).sum()
        dice = (2. * intersection + self.smooth) / (pred.sum() + target.sum() + self.smooth)
        
        return 1 - dice


class WeightedBCELoss(nn.Module):
    def __init__(self, pos_weight=15.0):
        super(WeightedBCELoss, self).__init__()
        self.pos_weight = pos_weight
    
    def forward(self, pred, target):
        weight = target * self.pos_weight + (1 - target) * 1.0
        bce = F.binary_cross_entropy_with_logits(pred, target, reduction='none')
        weighted_bce = (bce * weight).mean()
        return weighted_bce


class SharpeningLoss(nn.Module):
    """DISABLED - kept for compatibility"""
    def __init__(self):
        super(SharpeningLoss, self).__init__()
    
    def forward(self, pred, target):
        return torch.tensor(0.0, device=pred.device)


class CombinedLoss(nn.Module):
    def __init__(self, focal_weight=0.4, dice_weight=0.4, bce_weight=0.2, sharpen_weight=0.0):
        super(CombinedLoss, self).__init__()
        self.focal_weight = focal_weight
        self.dice_weight = dice_weight
        self.bce_weight = bce_weight
        self.sharpen_weight = sharpen_weight
        
        self.focal = FocalLoss(alpha=0.25, gamma=2.0)
        self.dice = DiceLoss()
        self.bce = WeightedBCELoss(pos_weight=15.0)
        self.sharpen = SharpeningLoss()
    
    def forward(self, pred, target):
        focal_loss = self.focal(pred, target)
        dice_loss = self.dice(pred, target)
        bce_loss = self.bce(pred, target)
        
        # Only compute sharpening if weight > 0
        if self.sharpen_weight > 0:
            sharpen_loss = self.sharpen(pred, target)
        else:
            sharpen_loss = torch.tensor(0.0, device=pred.device)
        
        total = (self.focal_weight * focal_loss + 
                self.dice_weight * dice_loss + 
                self.bce_weight * bce_loss +
                self.sharpen_weight * sharpen_loss)
        
        return total


# ============================================================================
# Metrics
# ============================================================================

def pixel_accuracy(pred, target, threshold=0.5):
    pred = torch.sigmoid(pred) > threshold
    target = target > threshold
    correct = (pred == target).float().sum()
    total = torch.numel(pred)
    return (correct / total).item()


def iou_score(pred, target, threshold=0.5):
    pred = torch.sigmoid(pred) > threshold
    target = target > threshold
    
    intersection = (pred & target).float().sum()
    union = (pred | target).float().sum()
    
    if union == 0:
        return 1.0
    return (intersection / union).item()


def dice_coefficient(pred, target, threshold=0.5):
    pred = torch.sigmoid(pred) > threshold
    target = target > threshold
    
    intersection = (pred & target).float().sum()
    
    if pred.sum() + target.sum() == 0:
        return 1.0
    return (2. * intersection / (pred.sum() + target.sum())).item()


# ============================================================================
# Early Stopping
# ============================================================================

class EarlyStopping:
    def __init__(self, patience=20, verbose=True, delta=0, path='checkpoint.pt'):
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_loss_min = np.Inf
        self.delta = delta
        self.path = path
    
    def __call__(self, val_loss, model):
        score = -val_loss
        
        if self.best_score is None:
            self.best_score = score
            self.save_checkpoint(val_loss, model)
        elif score < self.best_score + self.delta:
            self.counter += 1
            if self.verbose:
                print(f'EarlyStopping counter: {self.counter} out of {self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = score
            self.save_checkpoint(val_loss, model)
            self.counter = 0
    
    def save_checkpoint(self, val_loss, model):
        if self.verbose:
            print(f'Validation loss decreased ({self.val_loss_min:.6f} --> {val_loss:.6f}). Saving model...')
        torch.save(model.state_dict(), self.path)
        self.val_loss_min = val_loss


# ============================================================================
# Visualization WITH METADATA
# ============================================================================

def save_validation_samples(model, dataloader, epoch, save_dir, device, num_samples=2):
    """Save validation samples WITH FULL METADATA DISPLAY"""
    model.eval()
    save_dir = Path(save_dir)
    
    if isinstance(epoch, int):
        epoch_dir = save_dir / f'epoch_{epoch:03d}'
    else:
        epoch_dir = save_dir / f'epoch_{epoch}'
    epoch_dir.mkdir(parents=True, exist_ok=True)
    
    fig, axes = plt.subplots(num_samples, 3, figsize=(14, 4.5*num_samples))
    if num_samples == 1:
        axes = axes.reshape(1, -1)
    
    with torch.no_grad():
        for i, (inputs, targets, metadata_batch) in enumerate(dataloader):
            if i >= num_samples:
                break
            
            inputs = inputs.to(device)
            targets = targets.to(device)
            
            outputs = model(inputs)
            outputs_prob = torch.sigmoid(outputs)
            
            # Get first sample
            input_img = inputs[0].cpu().numpy().transpose(1, 2, 0)
            target_img = targets[0, 0].cpu().numpy()
            output_img = outputs_prob[0, 0].cpu().numpy()
            input_img = np.clip(input_img, 0, 1)
            
            # Get metadata for first sample in batch
            metadata = metadata_batch[0] if isinstance(metadata_batch, list) else metadata_batch
            
            # Parse metadata
            def get_val(v):
                return v[0] if isinstance(v, (list, tuple)) else v
            model_name = get_val(metadata['model'])
            exp_num = get_val(metadata['experiment'])
            az_start = int(get_val(metadata['azimuth_start']))
            az_end = int(get_val(metadata['azimuth_end']))
            distance = int(float(get_val(metadata['distance_km'])))
            rad_min = int(float(get_val(metadata['radius_min'])))
            rad_max = int(float(get_val(metadata['radius_max'])))
            
            # Compact format
            metadata_str = f"Exp{exp_num} | {model_name} | az{az_start:03d}-{az_end:03d}° | {distance}km | rad{rad_min}-{rad_max}km"
            
            # Add as text above row
            y_pos = 1 - (i + 0.5) / num_samples + 0.012
            fig.text(0.5, y_pos, metadata_str,
                    ha='center', va='bottom', fontsize=10, fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='lightgray', edgecolor='black', alpha=0.9))
            
            # Plot images
            axes[i, 0].imshow(input_img)
            axes[i, 0].set_title('Input (Observed)', fontsize=10)
            axes[i, 0].axis('off')
            
            axes[i, 1].imshow(target_img, cmap='hot', vmin=0, vmax=1)
            axes[i, 1].set_title('Target (Theoretical)', fontsize=10)
            axes[i, 1].axis('off')
            
            axes[i, 2].imshow(output_img, cmap='hot', vmin=0, vmax=1)
            axes[i, 2].set_title(f'Prediction (max:{output_img.max():.2f})', fontsize=10)
            axes[i, 2].axis('off')
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(epoch_dir / 'validation_samples.png', dpi=150, bbox_inches='tight')
    plt.close()


def plot_training_curves(train_losses, val_losses, train_metrics, val_metrics, save_path):
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    
    # Loss
    axes[0, 0].plot(train_losses, label='Train Loss', linewidth=2, color='#1f77b4')
    axes[0, 0].plot(val_losses, label='Val Loss', linewidth=2, color='#ff7f0e')
    axes[0, 0].set_xlabel('Epoch', fontsize=12)
    axes[0, 0].set_ylabel('Loss', fontsize=12)
    axes[0, 0].set_title('Training and Validation Loss', fontsize=14, fontweight='bold')
    axes[0, 0].legend(fontsize=11)
    axes[0, 0].grid(True, alpha=0.3)
    
    # Pixel Accuracy
    axes[0, 1].plot(train_metrics['accuracy'], label='Train Accuracy', linewidth=2, color='#1f77b4')
    axes[0, 1].plot(val_metrics['accuracy'], label='Val Accuracy', linewidth=2, color='#ff7f0e')
    axes[0, 1].set_xlabel('Epoch', fontsize=12)
    axes[0, 1].set_ylabel('Accuracy', fontsize=12)
    axes[0, 1].set_title('Pixel Accuracy', fontsize=14, fontweight='bold')
    axes[0, 1].legend(fontsize=11)
    axes[0, 1].grid(True, alpha=0.3)
    
    # IoU
    axes[1, 0].plot(train_metrics['iou'], label='Train IoU', linewidth=2, color='#1f77b4')
    axes[1, 0].plot(val_metrics['iou'], label='Val IoU', linewidth=2, color='#ff7f0e')
    axes[1, 0].set_xlabel('Epoch', fontsize=12)
    axes[1, 0].set_ylabel('IoU', fontsize=12)
    axes[1, 0].set_title('Intersection over Union', fontsize=14, fontweight='bold')
    axes[1, 0].legend(fontsize=11)
    axes[1, 0].grid(True, alpha=0.3)
    
    # Dice
    axes[1, 1].plot(train_metrics['dice'], label='Train Dice', linewidth=2, color='#1f77b4')
    axes[1, 1].plot(val_metrics['dice'], label='Val Dice', linewidth=2, color='#ff7f0e')
    axes[1, 1].set_xlabel('Epoch', fontsize=12)
    axes[1, 1].set_ylabel('Dice Coefficient', fontsize=12)
    axes[1, 1].set_title('Dice Coefficient', fontsize=14, fontweight='bold')
    axes[1, 1].legend(fontsize=11)
    axes[1, 1].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()


# ============================================================================
# Training
# ============================================================================

def train_epoch(model, dataloader, criterion, optimizer, device):
    model.train()
    epoch_loss = 0
    epoch_accuracy = 0
    epoch_iou = 0
    epoch_dice = 0
    
    for inputs, targets, _ in tqdm(dataloader, desc='Training', leave=False):
        inputs = inputs.to(device)
        targets = targets.to(device)
        
        optimizer.zero_grad()
        
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        
        loss.backward()
        optimizer.step()
        
        epoch_loss += loss.item() * inputs.size(0)
        epoch_accuracy += pixel_accuracy(outputs, targets) * inputs.size(0)
        epoch_iou += iou_score(outputs, targets) * inputs.size(0)
        epoch_dice += dice_coefficient(outputs, targets) * inputs.size(0)
    
    n = len(dataloader.dataset)
    return epoch_loss / n, epoch_accuracy / n, epoch_iou / n, epoch_dice / n


def validate_epoch(model, dataloader, criterion, device):
    model.eval()
    epoch_loss = 0
    epoch_accuracy = 0
    epoch_iou = 0
    epoch_dice = 0
    
    with torch.no_grad():
        for inputs, targets, _ in tqdm(dataloader, desc='Validation', leave=False):
            inputs = inputs.to(device)
            targets = targets.to(device)
            
            outputs = model(inputs)
            loss = criterion(outputs, targets)
            
            epoch_loss += loss.item() * inputs.size(0)
            epoch_accuracy += pixel_accuracy(outputs, targets) * inputs.size(0)
            epoch_iou += iou_score(outputs, targets) * inputs.size(0)
            epoch_dice += dice_coefficient(outputs, targets) * inputs.size(0)
    
    n = len(dataloader.dataset)
    return epoch_loss / n, epoch_accuracy / n, epoch_iou / n, epoch_dice / n


def train_model(config):
    # Setup
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Create output directory
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    run_dir = Path(config['output_dir']) / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {run_dir}")
    
    # Save config
    with open(run_dir / 'config.json', 'w') as f:
        json.dump(config, f, indent=4)
    
    # Dataset
    print("Loading dataset...")
    dataset = FTANDataset(
        data_dir=config['data_dir'],
        metadata_file=config['metadata_file'],
        augment=config['augment']
    )
    
    # Split dataset
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
    
    print(f"Training samples: {train_size}")
    print(f"Validation samples: {val_size}")
    
    # Dataloaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=config['batch_size'],
        shuffle=True,
        num_workers=4,
        pin_memory=True
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=config['batch_size'],
        shuffle=False,
        num_workers=4,
        pin_memory=True
    )
    
    # Model
    print("Initializing model...")
    model = UNet(in_channels=3, out_channels=1).to(device)
    
    # Count parameters
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Total parameters: {total_params:,}")
    
    # Loss and optimizer (Sharpening disabled)
    criterion = CombinedLoss(focal_weight=0.4, dice_weight=0.4, bce_weight=0.2, sharpen_weight=0.0)
    optimizer = Adam(model.parameters(), lr=config['learning_rate'])
    scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=5, verbose=True)
    
    # Early stopping
    early_stopping = EarlyStopping(
        patience=config['patience'],
        verbose=True,
        path=run_dir / 'model_best.pt'
    )
    
    # Training history
    train_losses = []
    val_losses = []
    train_metrics = {'accuracy': [], 'iou': [], 'dice': []}
    val_metrics = {'accuracy': [], 'iou': [], 'dice': []}
    
    # Training loop
    print(f"\nStarting training for {config['num_epochs']} epochs...")
    print("="*70)
    
    for epoch in range(config['num_epochs']):
        print(f"\nEpoch {epoch+1}/{config['num_epochs']}")
        print("-" * 50)
        
        # Train
        train_loss, train_acc, train_iou, train_dice = train_epoch(
            model, train_loader, criterion, optimizer, device
        )
        
        # Validate
        val_loss, val_acc, val_iou, val_dice = validate_epoch(
            model, val_loader, criterion, device
        )
        
        # Update scheduler
        scheduler.step(val_loss)
        
        # Save metrics
        train_losses.append(train_loss)
        val_losses.append(val_loss)
        train_metrics['accuracy'].append(train_acc)
        train_metrics['iou'].append(train_iou)
        train_metrics['dice'].append(train_dice)
        val_metrics['accuracy'].append(val_acc)
        val_metrics['iou'].append(val_iou)
        val_metrics['dice'].append(val_dice)
        
        # Print metrics
        print(f"Train - Loss: {train_loss:.4f} | Acc: {train_acc:.4f} | IoU: {train_iou:.4f} | Dice: {train_dice:.4f}")
        print(f"Val   - Loss: {val_loss:.4f} | Acc: {val_acc:.4f} | IoU: {val_iou:.4f} | Dice: {val_dice:.4f}")
        
        # Save validation samples EVERY epoch
        save_validation_samples(model, val_loader, epoch+1, run_dir / 'samples', device, num_samples=2)
        
        # Plot training curves EVERY epoch
        plot_training_curves(train_losses, val_losses, train_metrics, val_metrics, run_dir / 'training_curve.png')
        
        # Early stopping
        early_stopping(val_loss, model)
        if early_stopping.early_stop:
            print("\nEarly stopping triggered!")
            break
    
    # Load best model
    print("\nLoading best model...")
    model.load_state_dict(torch.load(run_dir / 'model_best.pt'))
    
    # Final validation
    print("\nFinal validation on best model...")
    final_val_loss, final_val_acc, final_val_iou, final_val_dice = validate_epoch(
        model, val_loader, criterion, device
    )
    print(f"Final Val - Loss: {final_val_loss:.4f} | Acc: {final_val_acc:.4f} | IoU: {final_val_iou:.4f} | Dice: {final_val_dice:.4f}")
    
    # Save final samples
    print("Generating final validation samples...")
    save_validation_samples(model, val_loader, 'final', run_dir / 'samples', device, num_samples=4)
    
    print("\n" + "="*70)
    print(f"Training complete! Results saved to: {run_dir}")
    print("="*70)
    
    return model, run_dir


# ============================================================================
# Main
# ============================================================================

def main():
    config = {
        'data_dir': 'FTAN_ML_INPUT',
        'metadata_file': 'FTAN_ML_INPUT/metadata.csv',
        'output_dir': 'FTAN_ML_MODELS',
        'batch_size': 8,
        'learning_rate': 1e-4,
        'num_epochs': 200,
        'patience': 20,
        'augment': True
    }
    
    print("="*70)
    print("UNet Training for FTAN Dispersion Curve Segmentation")
    print("="*70)
    print("\nConfiguration:")
    print(f"  Input: 256x256 RGB (observed FTAN)")
    print(f"  Output: 256x256 Grayscale (binary mask)")
    print(f"  Architecture: UNet (3→1 channels)")
    print(f"  Loss: Focal + Dice + Weighted BCE (Sharpening disabled)")
    print(f"  Metadata display: Exp# | model | az###-###° | ###km | rad###-###km")
    print()
    for key, value in config.items():
        print(f"  {key}: {value}")
    print()
    
    model, run_dir = train_model(config)
    
    print("\nTraining Complete!")
    print(f"Model: {run_dir / 'model_best.pt'}")
    print(f"Curves: {run_dir / 'training_curve.png'}")
    print(f"Samples: {run_dir / 'samples/'}")


if __name__ == "__main__":
    main()