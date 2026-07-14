import h5py
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import RegularGridInterpolator
import sys

H5  = "wavenetv2_dataset_10k_full.h5"
KEY = sys.argv[1] if len(sys.argv) > 1 else "M01_0000"

PER_MIN, PER_MAX, PER_STEP = 1.0, 20.0, 0.25
VEL_MIN, VEL_MAX, VEL_STEP = 2.0,  5.0, 0.01

per_grid = np.arange(PER_MIN, PER_MAX, PER_STEP)   # (76,)
vel_grid = np.arange(VEL_MIN, VEL_MAX, VEL_STEP)   # (300,)

with h5py.File(H5, "r") as f:
    sim  = f["simulations"][KEY]
    geom = sim["geometries"]["separation_127.0km"]["empirical_ftan_dispersion"]
    ftan_raw     = geom["FTAN_ZZ"][:]        # (85, 1001) — CWT power
    period_s     = geom["period_s"][:]       # (85,) increasing
    velocity_kms = geom["velocity_kms"][:]   # (1001,) DECREASING, non-uniform

    theory_per  = sim["theoretical"]["period"][:]
    theory_gvel = sim["theoretical"]["group_velocity_dispersion"][:]

# velocity_kms is decreasing — flip both axes so interpolator gets monotonic input
valid = velocity_kms > 0
vel_axis  = velocity_kms[valid][::-1]        # now increasing
ftan_flip = ftan_raw[:, valid][:, ::-1]      # flip columns to match

# RegularGridInterpolator needs strictly increasing axes
interp = RegularGridInterpolator(
    (period_s, vel_axis), ftan_flip,
    method="linear", bounds_error=False, fill_value=0.0,
)
P, V = np.meshgrid(per_grid, vel_grid, indexing="ij")
ftan_grid = interp(np.stack([P, V], axis=-1))   # (76, 300)

# Per-row normalise
for i in range(ftan_grid.shape[0]):
    mx = ftan_grid[i].max()
    if mx > 0:
        ftan_grid[i] /= mx

# Fundamental mode
cut = np.where(np.diff(theory_per) <= 0)[0]
cut_idx = int(cut[0]) + 1 if len(cut) else len(theory_per)
fund_per  = theory_per[:cut_idx]
fund_gvel = theory_gvel[:cut_idx]

fig, ax = plt.subplots(figsize=(10, 5), facecolor="#0d0d0d")
ax.set_facecolor("#111111")

ax.imshow(
    ftan_grid.T,
    aspect="auto",
    origin="lower",
    extent=[per_grid[0], per_grid[-1], vel_grid[0], vel_grid[-1]],
    cmap="inferno",
    vmin=0, vmax=1,
)
ax.plot(fund_per, fund_gvel, color="lime", lw=1.5, ls="--", label="Theory GVel")

ax.set_xlim(PER_MIN, PER_MAX)
ax.set_ylim(VEL_MIN, VEL_MAX)
ax.set_xlabel("Period (s)", color="white")
ax.set_ylabel("Group Velocity (km/s)", color="white")
ax.set_title(f"FTAN_ZZ (re-gridded) — {KEY}", color="white")
ax.tick_params(colors="white")
ax.legend(facecolor="#222", labelcolor="white")
for sp in ax.spines.values():
    sp.set_edgecolor("#444")

plt.tight_layout()
out = f"ftan_{KEY}.png"
plt.savefig(out, dpi=120, facecolor=fig.get_facecolor())
print(f"Saved → {out}")
