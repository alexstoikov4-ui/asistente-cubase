"""Sonoridad según ITU-R BS.1770-4 / EBU R128: LUFS integrado, momentáneo, a corto plazo, LRA y true peak."""
from __future__ import annotations

import math

import numpy as np

from .dsp import biquad_filter, oversampled_peak, to_db

ABS_GATE = -70.0


def k_weighting_coeffs(fs: float):
    """Coeficientes K para cualquier frecuencia de muestreo (derivación usada por libebur128)."""
    f0 = 1681.974450955533
    G = 3.999843853973347
    Q = 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    pb = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    pa = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0 = 38.13547087602444
    Q = 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    d = 1 + K / Q + K * K
    rb = [1.0, -2.0, 1.0]
    ra = [1.0, 2 * (K * K - 1) / d, (1 - K / Q + K * K) / d]
    return (pb, pa), (rb, ra)


def channel_weights(n_channels: int) -> np.ndarray:
    # Para 5.1 (L R C LFE Ls Rs) el LFE se excluye y los surround pesan 1.41.
    if n_channels == 6:
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41])
    return np.ones(n_channels)


def _k_weighted_power(x: np.ndarray, fs: int) -> np.ndarray:
    """Potencia ponderada K por muestra, ya sumada entre canales con sus pesos."""
    if x.ndim == 1:
        x = x[:, None]
    (pb, pa), (rb, ra) = k_weighting_coeffs(fs)
    y = biquad_filter(rb, ra, biquad_filter(pb, pa, x))
    w = channel_weights(x.shape[1])
    return (y * y) @ w


def _block_loudness(power: np.ndarray, fs: int, block_s: float, hop_s: float) -> np.ndarray:
    n = int(round(block_s * fs))
    hop = int(round(hop_s * fs))
    if len(power) < n:
        return np.array([])
    cs = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(power) - n + 1, hop)
    z = (cs[starts + n] - cs[starts]) / n
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(np.maximum(z, 1e-30))


def _gated_mean(lk: np.ndarray, rel_offset: float) -> float:
    lk = lk[lk > ABS_GATE]
    if lk.size == 0:
        return float("-inf")
    rel = 10 * math.log10(np.mean(10 ** ((lk + 0.691) / 10))) - 0.691 + rel_offset
    lk = lk[lk > rel]
    if lk.size == 0:
        return float("-inf")
    return 10 * math.log10(np.mean(10 ** ((lk + 0.691) / 10))) - 0.691


def measure(x: np.ndarray, fs: int, true_peak: bool = True) -> dict:
    """Mediciones de sonoridad. Las de corta duración devuelven -inf si el audio es demasiado corto."""
    power = _k_weighted_power(x, fs)
    momentary = _block_loudness(power, fs, 0.4, 0.1)
    integrated = _gated_mean(momentary, -10.0) if momentary.size else float("-inf")
    short = _block_loudness(power, fs, 3.0, 0.1)
    lra = float("nan")
    st_max = float("-inf")
    if short.size:
        st_max = float(np.max(short))
        g = short[short > ABS_GATE]
        if g.size:
            rel = 10 * math.log10(np.mean(10 ** ((g + 0.691) / 10))) - 0.691 - 20.0
            g = g[g > rel]
            if g.size >= 2:
                lra = float(np.percentile(g, 95) - np.percentile(g, 10))
            elif g.size == 1:
                lra = 0.0
    res = {
        "integrated_lufs": float(integrated),
        "momentary_max_lufs": float(np.max(momentary)) if momentary.size else float("-inf"),
        "short_term_max_lufs": st_max,
        "lra_lu": lra,
        "short_term_curve": short[::10].round(2).tolist() if short.size else [],  # un valor por segundo
    }
    if true_peak:
        res["true_peak_dbtp"] = to_db(oversampled_peak(x, 4))
    return res


def integrated_lufs(x: np.ndarray, fs: int) -> float:
    return measure(x, fs, true_peak=False)["integrated_lufs"]
