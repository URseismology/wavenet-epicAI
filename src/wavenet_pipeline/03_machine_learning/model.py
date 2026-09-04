"""
U-Net architecture for FTAN mask segmentation. Promoted from
chrisScripts/julyncf_pipeline/ML_pipeline/U_NET_array.py.

Input  : (1, 80, 300) - per-row normalized FTAN regridded to 1-20s x 2-5 km/s (rows 0-75)
                        + 4 zero-padded rows (rows 76-79).
Target : (1, 80, 300) - binary mask +-MASK_WIDTH bins around theoretical group velocity
                        (rows 0-75), zeros in rows 76-79.
Output : raw logits - sigmoid applied externally for loss/metrics/viz.

Fix on promotion (verified, not just inherited — see docs/ml_pipeline_stages/
stage_b_model_definition.md): the source had two near-identical post-`self.head`
F.interpolate-if-mismatched checks stacked in a row. Traced the actual shape math for
this (80, 300) grid through 4 pooling levels: height 80->40->20->10->5 divides cleanly at
every level; width 300->150->75->37->18 does NOT divide cleanly at levels 3-4
(floor(75/2)=37) — so the per-level defensive interpolate inside the decoder loop is
genuinely necessary and correctly implemented, but by the time execution reaches
`self.head` the upsampling path has already landed back on exactly width 300 (150 is
even), so the second, post-head check was dead code for this specific grid. Deduped to
one guarded call; kept as cheap insurance against a future grid-size change (with this
comment explaining why), backed by the shape-assertion self-test below instead of a
second silent interpolate.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, dropout: float = 0.0):
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

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class UNetSeg(nn.Module):
    def __init__(self, in_channels: int = 1, out_channels: int = 1,
                 features: tuple[int, ...] = (16, 32, 64, 128)):
        super().__init__()

        self.encoders = nn.ModuleList()
        self.pools = nn.ModuleList()
        self.upconvs = nn.ModuleList()
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

    def forward(self, x: torch.Tensor) -> torch.Tensor:
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
                # Necessary for this grid: width 300 -> 150 -> 75 -> 37 -> 18 doesn't
                # divide cleanly (floor(75/2)=37), unlike height (80->40->20->10->5).
                x = F.interpolate(x, size=skip.shape[2:], mode="bilinear", align_corners=False)
            x = torch.cat([skip, x], dim=1)
            x = dec(x)

        x = self.head(x)
        if x.shape[2:] != input_size:
            # Cheap insurance for a future grid-size change, not needed for (80,300)
            # today — the decoder's last upsample (150->300) already lands exactly on
            # input_size by this point. Backed by the shape-assertion test below.
            x = F.interpolate(x, size=input_size, mode="bilinear", align_corners=False)

        return x  # raw logits - sigmoid applied externally


if __name__ == "__main__":
    model = UNetSeg(in_channels=1, out_channels=1)
    x = torch.zeros(2, 1, 80, 300)
    y = model(x)
    assert y.shape == (2, 1, 80, 300), y.shape
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model.py self-test OK: output shape {tuple(y.shape)}, {n_params:,} parameters")
