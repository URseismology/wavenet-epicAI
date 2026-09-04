"""
Sample enumeration and Dataset classes for the FTAN dispersion-curve pipeline.

A "sample" is one (model, geometry) pair, not one model — generalizes chrisScripts'
SimDataset (hardcoded to "separation_127.0km") to walk every populated geom_key, so a
future HDF5 with additional sep_km values is picked up with zero code changes.
"""

from collections import namedtuple
from pathlib import Path

import h5py
import numpy as np
import torch
from torch.utils.data import Dataset

from . import ftan_grid

SampleRef = namedtuple("SampleRef", ["h5_path", "sim_key", "geom_key", "sep_km", "family"])


def _parse_sep_km(geom_key: str) -> float:
    # geom_key looks like "separation_127.0km"
    return float(geom_key.replace("separation_", "").replace("km", ""))


def enumerate_samples(h5_paths: list[Path]) -> list[SampleRef]:
    """
    Walk simulations/{sim_key}/geometries/{geom_key} for every geom_key that has a
    populated empirical_ftan_dispersion group (skips the pending_ftan_computation
    placeholder some simulator runs may write if pycwt wasn't importable at run time).
    """
    samples = []
    for h5_path in h5_paths:
        h5_path = Path(h5_path)
        with h5py.File(h5_path, "r") as f:
            for sim_key in f["simulations"].keys():
                sim = f["simulations"][sim_key]
                for geom_key in sim["geometries"].keys():
                    ftan_grp = sim["geometries"][geom_key]["empirical_ftan_dispersion"]
                    if "FTAN_ZZ" not in ftan_grp:
                        continue  # placeholder / pending_ftan_computation — skip
                    samples.append(SampleRef(
                        h5_path=h5_path,
                        sim_key=sim_key,
                        geom_key=geom_key,
                        sep_km=_parse_sep_km(geom_key),
                        family=sim_key[:3],
                    ))
    return samples


class FTANDispersionDataset(Dataset):
    """
    Reads raw HDF5 data and computes the canonical (1, 80, 300) input/target on the fly.
    Used by build_ml_cache.py to precompute the cache, and standalone for validate_ftan.py.
    Lazily opens h5py.File per __getitem__ call (multiprocessing-DataLoader-worker safe,
    matching chrisScripts' existing correct pattern) rather than holding an open handle.
    """

    def __init__(self, samples: list[SampleRef]):
        self.samples = list(samples)

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        ref = self.samples[idx]
        with h5py.File(ref.h5_path, "r") as f:
            sim = f["simulations"][ref.sim_key]
            geom = sim["geometries"][ref.geom_key]["empirical_ftan_dispersion"]
            ftan_raw = geom["FTAN_ZZ"][:]
            period_s = geom["period_s"][:]
            velocity_kms = geom["velocity_kms"][:]
            theory_period = sim["theoretical"]["period"][:]
            theory_gvel = sim["theoretical"]["group_velocity_dispersion"][:]

        ftan = ftan_grid.pad_to_canonical(ftan_grid.regrid_ftan(ftan_raw, period_s, velocity_kms))
        mask = ftan_grid.pad_to_canonical(ftan_grid.build_target_mask(theory_period, theory_gvel))

        meta = {
            "sim_key": ref.sim_key,
            "geom_key": ref.geom_key,
            "sep_km": ref.sep_km,
            "family": ref.family,
        }
        return (
            torch.from_numpy(ftan).unsqueeze(0),
            torch.from_numpy(mask).unsqueeze(0),
            meta,
        )


class CachedFTANDataset(Dataset):
    """Indexes into a precomputed cache HDF5 (from build_ml_cache.py) — pure array reads,
    no SciPy/regridding cost per __getitem__. Intended for Stage C/D training use."""

    def __init__(self, cache_path: Path, indices: np.ndarray | None = None):
        self.cache_path = Path(cache_path)
        with h5py.File(self.cache_path, "r") as f:
            n = f["X"].shape[0]
        self.indices = np.arange(n) if indices is None else np.asarray(indices)

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, idx: int):
        i = int(self.indices[idx])
        with h5py.File(self.cache_path, "r") as f:
            x = f["X"][i]
            y = f["Y"][i]
            meta = {
                "sim_key": f["sim_key"][i].decode() if isinstance(f["sim_key"][i], bytes) else f["sim_key"][i],
                "geom_key": f["geom_key"][i].decode() if isinstance(f["geom_key"][i], bytes) else f["geom_key"][i],
                "sep_km": float(f["sep_km"][i]),
                "family": f["family"][i].decode() if isinstance(f["family"][i], bytes) else f["family"][i],
            }
        return torch.from_numpy(x).unsqueeze(0), torch.from_numpy(y).unsqueeze(0), meta
