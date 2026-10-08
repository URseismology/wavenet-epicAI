"""
Sample enumeration and Dataset classes for the FTAN dispersion-curve pipeline.

A "sample" is one (model, geometry) pair, not one model — generalizes chrisScripts'
SimDataset (hardcoded to "separation_127.0km") to walk every populated geom_key, so a
future HDF5 with additional sep_km values is picked up with zero code changes.
"""

import os
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


def _decode(values) -> list[str]:
    return [v.decode() if isinstance(v, bytes) else v for v in values]


class CachedFTANDataset(Dataset):
    """Indexes into a precomputed cache HDF5 (from build_ml_cache.py) — pure array reads,
    no SciPy/regridding cost per __getitem__. Intended for Stage C/D training use.

    Stage D fix (2026-09-30): the original version reopened the cache file on every
    __getitem__ and read 4 vlen-string metadata columns per sample, which is why more
    DataLoader workers barely helped in Stage C (35.8s -> 30.7s/epoch). Now:
      - metadata columns are read once at __init__ (a few MB for 20k rows);
      - X/Y are read through ONE persistent h5py handle per process, opened lazily on
        first access. The handle is keyed by PID and dropped from pickled state, so each
        DataLoader worker (fork or spawn) opens its own — h5py handles must never be
        shared across processes.
    """

    def __init__(self, cache_path: Path, indices: np.ndarray | None = None):
        self.cache_path = Path(cache_path)
        with h5py.File(self.cache_path, "r") as f:
            n = f["X"].shape[0]
            self._sim_key = _decode(f["sim_key"][:])
            self._geom_key = _decode(f["geom_key"][:])
            self._family = _decode(f["family"][:])
            self._sep_km = f["sep_km"][:].astype(np.float64)
        self.indices = np.arange(n) if indices is None else np.asarray(indices)
        self._h5 = None
        self._h5_pid = None

    def _file(self) -> h5py.File:
        pid = os.getpid()
        if self._h5 is None or self._h5_pid != pid:
            self._h5 = h5py.File(self.cache_path, "r")
            self._h5_pid = pid
        return self._h5

    def __getstate__(self):
        state = self.__dict__.copy()
        state["_h5"] = None
        state["_h5_pid"] = None
        return state

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, idx: int):
        i = int(self.indices[idx])
        f = self._file()
        x = f["X"][i]
        y = f["Y"][i]
        meta = {
            "sim_key": self._sim_key[i],
            "geom_key": self._geom_key[i],
            "sep_km": float(self._sep_km[i]),
            "family": self._family[i],
        }
        return torch.from_numpy(x).unsqueeze(0), torch.from_numpy(y).unsqueeze(0), meta
