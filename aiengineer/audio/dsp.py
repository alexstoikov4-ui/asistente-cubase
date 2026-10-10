"""Utilidades DSP con numpy: convolución FFT, filtros biquad (RBJ), sobremuestreo."""
from __future__ import annotations

import math

import numpy as np


def fftconvolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Convolución lineal 1D por solapamiento-suma. Devuelve len(x) muestras (alineado al inicio)."""
    x = np.asarray(x, dtype=np.float64)
    h = np.asarray(h, dtype=np.float64)
    n, m = len(x), len(h)
    if n == 0:
        return x.copy()
    if m <= 64 or n <= 64:
        return np.convolve(x, h)[:n]
    block = 1 << max(12, int(math.ceil(math.log2(m))) + 1)
    step = block - m + 1
    H = np.fft.rfft(h, block)
    y = np.zeros(n + m - 1)
    for start in range(0, n, step):
        seg = x[start:start + step]
        ys = np.fft.irfft(np.fft.rfft(seg, block) * H, block)[:len(seg) + m - 1]
        y[start:start + len(ys)] += ys
    return y[:n]


def impulse_response(b, a, max_len: int = 1 << 17, tol: float = 1e-10) -> np.ndarray:
    """Respuesta al impulso de un filtro IIR de 2º orden hasta que la cola sea despreciable."""
    b0, b1, b2 = (float(v) / a[0] for v in b)
    a1, a2 = float(a[1]) / a[0], float(a[2]) / a[0]
    out = np.zeros(max_len)
    x1 = x2 = y1 = y2 = 0.0
    quiet = 0
    for i in range(max_len):
        xi = 1.0 if i == 0 else 0.0
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, xi
        y2, y1 = y1, yi
        out[i] = yi
        quiet = quiet + 1 if abs(yi) < tol else 0
        if quiet > 64 and i > 256:
            return out[:i + 1]
    return out


def biquad_filter(b, a, x: np.ndarray) -> np.ndarray:
    """Aplica un biquad estable a una señal (1D o (n, canales))."""
    h = impulse_response(b, a)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        return fftconvolve(x, h)
    return np.stack([fftconvolve(x[:, c], h) for c in range(x.shape[1])], axis=1)


def rbj(kind: str, fs: float, f0: float, q: float = 0.707, gain_db: float = 0.0):
    """Coeficientes del 'Audio EQ Cookbook' de R. Bristow-Johnson. Devuelve (b, a)."""
    f0 = min(max(f0, 1.0), fs * 0.49)
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "lowshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
        a = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
    elif kind == "highshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    else:
        raise ValueError(f"tipo de filtro desconocido: {kind}")
    return [v / a[0] for v in b], [1.0, a[1] / a[0], a[2] / a[0]]


def freq_response_db(b, a, freqs, fs) -> np.ndarray:
    w = 2 * np.pi * np.asarray(freqs, dtype=np.float64) / fs
    z = np.exp(-1j * w)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    return 20 * np.log10(np.abs(num / den) + 1e-300)


def interpolation_phases(factor: int = 4, taps_per_phase: int = 12) -> np.ndarray:
    """Filtro polifásico (sinc enventanada con Kaiser) para sobremuestreo x factor."""
    n = factor * taps_per_phase
    t = (np.arange(n) - (n - 1) / 2) / factor
    h = np.sinc(t) * np.kaiser(n, 8.0)
    h = h.reshape(taps_per_phase, factor).T  # fase p -> coeficientes h[p::factor]
    h /= h.sum(axis=1, keepdims=True)  # ganancia unitaria en continua por fase
    return h


def oversampled_peak(x: np.ndarray, factor: int = 4) -> float:
    """Pico absoluto de la señal reconstruida a factor x (estimación de true peak)."""
    phases = interpolation_phases(factor)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    for c in range(x.shape[1]):
        for p in range(factor):
            y = fftconvolve(x[:, c], phases[p])
            peak = max(peak, float(np.max(np.abs(y))))
    return peak


def to_db(v: float, floor: float = -150.0) -> float:
    return max(20 * math.log10(v), floor) if v > 0 else floor


def power_db(p: float, floor: float = -150.0) -> float:
    return max(10 * math.log10(p), floor) if p > 0 else floor
