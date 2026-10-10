"""Procesado offline propio (numpy) para previsualizar y verificar decisiones de masterización.

No sustituye a los plugins del usuario: sirve para medir el efecto de una cadena antes de
aplicarla en Cubase, y para entregar un máster de referencia cuando no se puede controlar el DAW.
"""
from __future__ import annotations

import math

import numpy as np

from ..audio.dsp import biquad_filter, fftconvolve, interpolation_phases, rbj


def apply_eq(x: np.ndarray, fs: int, bands: list[dict]) -> np.ndarray:
    """bands: [{"type": "peak"|"lowshelf"|"highshelf"|"highpass"|"lowpass", "freq":, "gain_db":, "q":}]"""
    y = x
    for b in bands:
        coeffs = rbj(b["type"], fs, b["freq"], b.get("q", 0.707), b.get("gain_db", 0.0))
        y = biquad_filter(*coeffs, y)
    return y


def mono_below(x: np.ndarray, fs: int, freq: float = 100.0) -> np.ndarray:
    """Hace mono el grave por debajo de freq filtrando paso alto el canal lateral (M/S)."""
    if x.ndim == 1 or x.shape[1] < 2:
        return x
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    b, a = rbj("highpass", fs, freq, 0.707)
    side = biquad_filter(b, a, biquad_filter(b, a, side))  # 24 dB/oct
    return np.stack([mid + side, mid - side], axis=1)


def _smooth_gain(target_db: np.ndarray, attack_blocks: float, release_blocks: float) -> np.ndarray:
    """Seguidor de ganancia por bloques: baja rápido (ataque) y sube despacio (liberación)."""
    a_att = math.exp(-1.0 / max(attack_blocks, 1e-3))
    a_rel = math.exp(-1.0 / max(release_blocks, 1e-3))
    out = np.empty_like(target_db)
    g = 0.0
    for i, t in enumerate(target_db):
        coef = a_att if t < g else a_rel
        g = coef * g + (1 - coef) * t
        out[i] = g
    return out


def compress(x: np.ndarray, fs: int, threshold_db: float, ratio: float, attack_ms: float = 30.0,
             release_ms: float = 200.0, knee_db: float = 6.0, makeup_db: float = 0.0, block: int = 32) -> tuple[np.ndarray, dict]:
    """Compresor RMS enlazado en estéreo con rodilla suave, calculado por bloques."""
    m = x if x.ndim == 2 else x[:, None]
    n = len(m)
    nb = (n + block - 1) // block
    pad = np.pad(m, ((0, nb * block - n), (0, 0)))
    level = 10 * np.log10(np.mean(pad.reshape(nb, block, -1) ** 2, axis=(1, 2)) + 1e-12)
    over = level - threshold_db
    gr = np.where(over <= -knee_db / 2, 0.0,
                  np.where(over >= knee_db / 2, over * (1 / ratio - 1),
                           (1 / ratio - 1) * (over + knee_db / 2) ** 2 / (2 * knee_db)))
    bs = block / fs * 1000
    g = _smooth_gain(gr, attack_ms / bs, release_ms / bs)
    gain = np.interp(np.arange(n), np.arange(nb) * block + block / 2, g)
    lin = 10 ** ((gain + makeup_db) / 20)
    y = m * lin[:, None]
    stats = {"max_gr_db": round(float(-g.min()), 2), "avg_gr_db": round(float(-g[level > threshold_db - 20].mean()), 2)
             if (level > threshold_db - 20).any() else 0.0}
    return (y if x.ndim == 2 else y[:, 0]), stats


def soft_saturate(x: np.ndarray, drive_db: float = 2.0, mix: float = 0.3) -> np.ndarray:
    """Saturación tanh con compensación de nivel; mezcla en paralelo para no destruir transitorios."""
    d = 10 ** (drive_db / 20)
    wet = np.tanh(x * d) / np.tanh(d)
    return (1 - mix) * x + mix * wet


def peak_envelope(x: np.ndarray, factor: int = 4) -> np.ndarray:
    """Pico absoluto por muestra de la señal reconstruida (sobremuestreo x4), máximo entre canales.
    Es la parte costosa del limitador; como escala linealmente con la ganancia, se calcula una vez."""
    m = x if x.ndim == 2 else x[:, None]
    phases = interpolation_phases(factor)
    peak = np.max(np.abs(m), axis=1)
    for c in range(m.shape[1]):
        for p in range(factor):
            peak = np.maximum(peak, np.abs(fftconvolve(m[:, c], phases[p])))
    return peak


def limit(x: np.ndarray, fs: int, ceiling_dbtp: float = -1.0, release_ms: float = 80.0,
          lookahead_ms: float = 3.0, block: int = 16, input_gain_db: float = 0.0,
          peak_env: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Limitador con anticipación que estima picos entre muestras (sobremuestreo x4).
    peak_env permite reutilizar la envolvente de picos de la señal sin ganancia."""
    m = x if x.ndim == 2 else x[:, None]
    n = len(m)
    g_in = 10 ** (input_gain_db / 20)
    ceiling = 10 ** (ceiling_dbtp / 20)
    peak = (peak_env if peak_env is not None else peak_envelope(m)) * g_in
    nb = (n + block - 1) // block
    bpk = np.pad(peak, (0, nb * block - n)).reshape(nb, block).max(axis=1)
    need = np.minimum(0.0, 20 * np.log10(ceiling / np.maximum(bpk, 1e-12)))
    la = max(1, int(round(lookahead_ms / 1000 * fs / block)))
    # mínimo centrado en ±la bloques y media móvil de la misma anchura: la ganancia empieza a bajar
    # antes del pico y en ningún bloque supera la reducción necesaria (cada ventana contiene el pico).
    padded = np.concatenate([np.zeros(la), need, np.zeros(la)])
    held = np.lib.stride_tricks.sliding_window_view(padded, 2 * la + 1).min(axis=1)
    hp = np.concatenate([np.full(la, held[0]), held, np.full(la, held[-1])])
    smooth = np.lib.stride_tricks.sliding_window_view(hp, 2 * la + 1).mean(axis=1)
    rel_blocks = release_ms / 1000 * fs / block
    g_s = np.minimum(_smooth_gain(smooth, 1e-4, rel_blocks), need)
    gain = np.interp(np.arange(n), np.arange(nb) * block + block / 2, g_s)
    gain = np.minimum(gain, np.repeat(need, block)[:n])
    y = m * (g_in * 10 ** (gain / 20))[:, None]
    active = bpk > np.max(bpk) * 10 ** (-40 / 20) if nb else np.zeros(0, bool)
    gr_active = -g_s[active] if active.any() else np.zeros(1)
    stats = {"max_gr_db": round(float(-g_s.min()), 2), "avg_gr_db": round(float(-g_s.mean()), 2),
             "p95_gr_db": round(float(np.percentile(gr_active, 95)), 2),
             "blocks_limited_pct": round(float(np.mean(g_s < -0.1) * 100), 1)}
    return (y if x.ndim == 2 else y[:, 0]), stats


def tpdf_dither(x: np.ndarray, bits: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    lsb = 1.0 / (1 << (bits - 1))
    return x + (rng.random(x.shape) - rng.random(x.shape)) * lsb


def resample_fft(x: np.ndarray, fs_in: int, fs_out: int) -> np.ndarray:
    """Cambio de frecuencia de muestreo por FFT (limitado en banda). Rellena para reducir efectos de borde."""
    if fs_in == fs_out:
        return x
    m = x if x.ndim == 2 else x[:, None]
    pad = int(0.1 * fs_in)
    mp = np.pad(m, ((pad, pad), (0, 0)))
    n_out = int(round(len(mp) * fs_out / fs_in))
    X = np.fft.rfft(mp, axis=0)
    n_bins = n_out // 2 + 1
    if n_bins <= X.shape[0]:
        Y = X[:n_bins]
    else:
        Y = np.vstack([X, np.zeros((n_bins - X.shape[0], X.shape[1]), dtype=complex)])
    y = np.fft.irfft(Y, n_out, axis=0) * (n_out / len(mp))
    cut = int(round(pad * fs_out / fs_in))
    y = y[cut:len(y) - cut]
    return y if x.ndim == 2 else y[:, 0]
