#!/usr/bin/env python
"""
Validation script comparing NoisePy NCF output against the ADAMA reference cross-spectra.

Loads the time-domain NCF from Wavenet_ncfs.h5, converts it to a cross-spectrum,
and overlays it against the ADAMA frequency-domain reference (ADAMA_ncfs_ZZ_fr.h5).
Saves a multi-panel PNG with time-domain traces, cross-spectra, and squared loss.

Usage:
    python compare_plot.py \\
        --noisepy Wavenet_ncfs.h5 \\
        --adama ADAMA_ncfs_ZZ_fr.h5 \\
        --pair XD.RUNG-XD.MTAN \\
        --sensor LH_BH \\
        --out compare_plot.png
"""
import argparse
from typing import Tuple
import numpy as np
import h5py
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def load_wavenet_time_domain(filepath: str, pair: str, sensor: str, component: str = 'ZZ') -> Tuple[np.ndarray, float]:
    """Loads the time-domain NCF for a given pair/sensor/component from a Wavenet HDF5 file."""
    with h5py.File(filepath, 'r') as f:
        pair_grp = f[pair]
        dt = float(pair_grp.attrs['dt'])
        signal = pair_grp[sensor][component]['time_domain'][:]
    return signal, dt


def load_adama_time_domain(filepath: str, pair: str) -> Tuple[np.ndarray, float]:
    """Reconstructs the ADAMA time-domain NCF by inverse FFT of the frequency-domain reference."""
    freq_signal, _ = load_adama_freq_domain(filepath, pair)
    n = 2 * (len(freq_signal) - 1)
    time_signal = np.fft.fftshift(np.fft.irfft(freq_signal, n=n))
    return time_signal, 1.0


def load_adama_freq_domain(filepath: str, pair: str) -> Tuple[np.ndarray, np.ndarray]:
    """Loads the ZZ cross-spectrum from an ADAMA _fr HDF5 file, trying both pair orderings."""
    parts = pair.split('-')
    candidates = [pair]
    if len(parts) == 2:
        candidates.append(f"{parts[1]}-{parts[0]}")

    with h5py.File(filepath, 'r') as f:
        waveforms = f['waveforms']
        group = None
        for key in candidates:
            if key in waveforms:
                group = waveforms[key]
                break
        if group is None:
            raise KeyError(f"Pair '{pair}' not found in {filepath}. "
                           f"Tried: {candidates}")

        zz_group = group['.ZZ-.ZZ']
        time_key = list(zz_group.keys())[0]
        signal = zz_group[time_key][:]

    freq_axis = np.fft.rfftfreq(2 * (len(signal) - 1), d=1.0)
    return signal, freq_axis


def noisepy_to_cross_spectrum(time_signal: np.ndarray, dt: float) -> Tuple[np.ndarray, np.ndarray]:
    """Symmetrizes a time-domain NCF and returns its real cross-spectrum and frequency axis."""
    sym = (time_signal + time_signal[::-1]) / 2.0
    npts = len(sym)
    zero_lag_idx = npts // 2
    n_pad = max(4096, int(2**np.ceil(np.log2(npts))))
    padded = np.zeros(n_pad)
    right_len = npts - zero_lag_idx
    padded[0:right_len] = sym[zero_lag_idx:]
    left_len = zero_lag_idx
    padded[-left_len:] = sym[:zero_lag_idx]
    freqs = np.fft.rfftfreq(n_pad, d=dt)
    real_spec = np.real(np.fft.rfft(padded))
    return freqs, real_spec


def plot_cross_spectra(
    freqs_np: np.ndarray,
    spec_np: np.ndarray,
    freqs_ad: np.ndarray,
    spec_ad: np.ndarray,
    out_path: str,
    time_signal: np.ndarray = None,
    adama_time_signal: np.ndarray = None,
    dt: float = 1.0,
    zoom_s: float = 300.0,
    threshold_factor: float = 0.1,
) -> None:
    """Saves a multi-panel comparison PNG: time-domain overlay, cross-spectra, and squared loss."""
    def _normalize(spec):
        peak = np.max(np.abs(spec))
        return spec if peak == 0.0 else spec / peak * 0.2

    spec_np = _normalize(spec_np)
    spec_ad = _normalize(spec_ad)

    spec_ad_on_np = np.interp(freqs_np, freqs_ad, spec_ad)
    loss = (spec_ad_on_np - spec_np) ** 2
    threshold = threshold_factor * np.sqrt(np.max(np.abs(spec_ad_on_np)) ** 2)

    def _style_freq(ax, title, shared_y=True):
        ax.axhline(0, color='black', linewidth=0.8, zorder=1)
        ax.axvspan(1 / 60, 1 / 3, color='gray', alpha=0.08, label='ADAMA passband')
        ax.set_xlim(0, 0.5)
        if shared_y:
            ax.set_ylim(-0.3, 0.3)
        ax.set_xlabel('Frequency (Hz)')
        ax.set_ylabel('Cross-spectra ρ(f)')
        ax.set_title(title, fontsize=12)
        ax.grid(True, linewidth=0.5, alpha=0.6)
        ax.legend(fontsize=9, loc='upper right')

    has_time = time_signal is not None
    nrows = 3 if has_time else 2
    height_ratios = [1.2, 1, 0.8] if has_time else [1, 0.8]

    fig = plt.figure(figsize=(18, 5 * nrows))
    gs = fig.add_gridspec(nrows, 3, hspace=0.42, wspace=0.25,
                          height_ratios=height_ratios)

    row_spec = 1 if has_time else 0
    row_loss = 2 if has_time else 1

    if has_time:
        ax_t = fig.add_subplot(gs[0, :])

        def _norm(s):
            p = np.max(np.abs(s))
            return s / p if p > 0 else s

        sym_np = (time_signal + time_signal[::-1]) / 2.0
        npts = len(sym_np)
        maxlag = (npts - 1) / 2 * dt
        time_axis = np.linspace(-maxlag, maxlag, npts)
        ax_t.plot(time_axis, _norm(sym_np),
                  color='steelblue', linewidth=0.9, label='NoisePy (symmetrized)')

        if adama_time_signal is not None:
            npts_ad = len(adama_time_signal)
            maxlag_ad = (npts_ad - 1) / 2 * dt
            time_axis_ad = np.linspace(-maxlag_ad, maxlag_ad, npts_ad)
            ax_t.plot(time_axis_ad, _norm(adama_time_signal),
                      color='firebrick', linewidth=0.9, linestyle='--',
                      label='ADAMA (reconstructed)', alpha=0.85)

        ax_t.axvline(0, color='k', linewidth=0.8, linestyle='--', alpha=0.5)
        ax_t.set_xlim(-zoom_s, zoom_s)
        ax_t.set_xlabel('Lag time (seconds)', fontsize=11)
        ax_t.set_ylabel('Normalized amplitude', fontsize=11)
        ax_t.set_title(f'NCF — Time Domain  (zoomed ±{zoom_s:.0f} s)', fontsize=12)
        ax_t.grid(True, linewidth=0.5, alpha=0.6)
        ax_t.legend(fontsize=9, loc='upper right')

    ax0 = fig.add_subplot(gs[row_spec, 0])
    ax1 = fig.add_subplot(gs[row_spec, 1], sharey=ax0)
    ax2 = fig.add_subplot(gs[row_spec, 2], sharey=ax0)

    ax0.plot(freqs_np, spec_np, color='steelblue', linewidth=1.2, label='NoisePy')
    _style_freq(ax0, 'NoisePy')

    ax1.plot(freqs_ad, spec_ad, color='firebrick', linewidth=1.2, label='ADAMA')
    _style_freq(ax1, 'ADAMA')
    ax1.set_ylabel('')

    ax2.fill_between(freqs_np, spec_np, spec_ad_on_np,
                     where=(spec_np >= spec_ad_on_np),
                     color='steelblue', alpha=0.35, label='NoisePy > ADAMA')
    ax2.fill_between(freqs_np, spec_np, spec_ad_on_np,
                     where=(spec_np < spec_ad_on_np),
                     color='firebrick', alpha=0.35, label='ADAMA > NoisePy')
    ax2.plot(freqs_np, spec_np, color='steelblue', linewidth=1.2, label='NoisePy')
    ax2.plot(freqs_ad, spec_ad, color='firebrick', linewidth=1.2, linestyle='--', label='ADAMA')
    _style_freq(ax2, 'Overlay')
    ax2.set_ylabel('')

    ax3 = fig.add_subplot(gs[row_loss, :])
    ax3.fill_between(freqs_np, loss, alpha=0.4, color='steelblue', label=r'Loss $(A-N)^2$')
    ax3.plot(freqs_np, loss, color='steelblue', linewidth=1.0)
    ax3.plot(freqs_np, np.full_like(freqs_np, threshold), color='firebrick', linewidth=1.4,
             linestyle='--', label=f'Threshold {threshold_factor}' + r'$\cdot \sqrt{\max|A|^2}$')
    ax3.fill_between(freqs_np, loss, threshold,
                     where=(loss > np.full_like(freqs_np, threshold)),
                     color='orange', alpha=0.45, label='Loss > threshold')
    ax3.axvspan(1 / 60, 1 / 3, color='gray', alpha=0.08, label='ADAMA passband')
    ax3.axhline(0, color='black', linewidth=0.8)
    ax3.set_xlim(0, 0.5)
    ax3.set_ylim(bottom=0)
    ax3.set_xlabel('Frequency (Hz)')
    ax3.set_ylabel('Squared loss')
    ax3.set_title(
        r'Signal Loss $(ADAMA - NoisePy)^2$  —  threshold: '
        f'{threshold_factor}' + r'$\cdot \sqrt{\max|A|^2}$',
        fontsize=12,
    )
    ax3.grid(True, linewidth=0.5, alpha=0.6)
    ax3.legend(fontsize=9, loc='upper right')

    fig.suptitle('NoisePy vs ADAMA — Cross-Spectra Comparison', fontsize=13)
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def main() -> None:
    """Parses CLI arguments and runs the NoisePy vs ADAMA cross-spectra comparison."""
    parser = argparse.ArgumentParser(
        description="Compare NoisePy (time-domain) vs ADAMA (freq-domain) NCF cross-spectra."
    )
    parser.add_argument(
        "--noisepy",
        required=True,
        help="Path to stacked NoisePy HDF5 file (time-domain)",
    )
    parser.add_argument(
        "--adama",
        required=True,
        help="Path to ADAMA _fr HDF5 file (frequency-domain)",
    )
    parser.add_argument(
        "--pair",
        required=True,
        help="Station pair in ADAMA hyphen format, e.g. XD.RUNG-XD.MTAN",
    )
    parser.add_argument(
        "--sensor",
        default="LH_BH",
        help="Sensor pair key in Wavenet_ncfs.h5, e.g. LH_BH (default: LH_BH)",
    )
    parser.add_argument(
        "--out",
        default="compare_plot.png",
        help="Output PNG path",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Loss threshold multiplier k: threshold = k * sqrt(max(|A|)^2) (default: 0.05)",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=300.0,
        help="Half-width of the time-domain zoom window in seconds (default: 300)",
    )
    args = parser.parse_args()

    wavenet_pair = args.pair.replace('-', '_')
    noisepy_sig, dt = load_wavenet_time_domain(args.noisepy, wavenet_pair, args.sensor)
    adama_sig, adama_freqs = load_adama_freq_domain(args.adama, args.pair)
    adama_time_sig, _ = load_adama_time_domain(args.adama, args.pair)
    freqs_np, spec_np = noisepy_to_cross_spectrum(noisepy_sig, dt)
    plot_cross_spectra(freqs_np, spec_np, adama_freqs, adama_sig, args.out,
                       time_signal=noisepy_sig, adama_time_signal=adama_time_sig,
                       dt=dt, zoom_s=args.zoom,
                       threshold_factor=args.threshold)


if __name__ == "__main__":
    main()
