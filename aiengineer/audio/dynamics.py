"""Análisis dinámico: picos, RMS, cresta, clipping, DC, transitorios."""
from __future__ import annotations

import numpy as np

from .dsp import power_db, to_db


def _runs(mask: np.ndarray) -> np.ndarray:
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return np.where(d == -1)[0] - np.where(d == 1)[0]


def clipping_runs(x: np.ndarray, min_run: int = 3) -> dict:
    """Detección de recorte.

    - A fondo de escala: tramos de >= min_run muestras con |x| >= 0,999.
    - Recorte por debajo de 0 dBFS (mezcla recortada y luego bajada de nivel): tramos de >= 4
      muestras EXACTAMENTE iguales en el valor máximo del archivo, repetidos al menos 10 veces.
      Una senoidal grave tiene muestras muy próximas en su cresta, pero no idénticas en coma flotante;
      exigir repetición sistemática evita falsos positivos con material cuantificado a 16 bits.
    """
    if x.ndim == 1:
        x = x[:, None]
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0:
        return {"runs": 0, "clipped_samples": 0, "peak": 0.0}
    runs = 0
    clipped = 0
    for c in range(x.shape[1]):
        a = np.abs(x[:, c])
        if peak >= 0.999:
            lengths = _runs(a >= 0.999)
            long = lengths[lengths >= min_run]
            runs += len(long)
            clipped += int(long.sum())
        else:
            at = a == peak
            eq_next = np.concatenate([at[1:] & at[:-1], [False]])
            lengths = _runs(at)
            long = lengths[lengths >= 4]
            if len(long) >= 10 and eq_next.any():
                runs += len(long)
                clipped += int(long.sum())
    return {"runs": runs, "clipped_samples": clipped, "peak": peak}


def onset_envelope(x: np.ndarray, fs: int, hop: int = 512, n_fft: int = 2048):
    """Flujo espectral positivo (log-compresión). Devuelve (envolvente, tasa de tramas en Hz)."""
    m = x.mean(axis=1) if x.ndim == 2 else x
    if len(m) < n_fft:
        return np.zeros(1), fs / hop
    n = 1 + (len(m) - n_fft) // hop
    win = np.hanning(n_fft)
    env = np.zeros(n)
    prev = None
    chunk = 512
    for s in range(0, n, chunk):
        idx = np.arange(s, min(n, s + chunk))
        frames = np.stack([m[i * hop:i * hop + n_fft] for i in idx]) * win
        mag = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
        if prev is not None:
            mag_full = np.vstack([prev[None], mag])
        else:
            mag_full = np.vstack([mag[:1], mag])
        flux = np.maximum(np.diff(mag_full, axis=0), 0).sum(axis=1)
        env[idx] = flux
        prev = mag[-1]
    return env, fs / hop


def analyze_dynamics(x: np.ndarray, fs: int) -> dict:
    if x.ndim == 1:
        x = x[:, None]
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    ms = float(np.mean(x * x)) if x.size else 0.0
    rms_db = power_db(ms)
    win = int(0.05 * fs)
    m = x.mean(axis=1)
    n = len(m) // win
    if n >= 2:
        frames = m[:n * win].reshape(n, win)
        rms_frames = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-20)
        active = rms_frames[rms_frames > rms_db - 40]
        dyn_range = float(np.percentile(active, 95) - np.percentile(active, 10)) if active.size else 0.0
        peak_frames = np.max(np.abs(frames), axis=1)
        # pegada de transitorios: cresta media por ventana de 50 ms en las zonas activas
        crest_frames = 20 * np.log10((peak_frames + 1e-12) / np.sqrt(np.mean(frames ** 2, axis=1) + 1e-24))
        transient_crest = float(np.median(crest_frames[rms_frames > rms_db - 20])) if (rms_frames > rms_db - 20).any() else 0.0
    else:
        dyn_range = 0.0
        transient_crest = 0.0
    env, rate = onset_envelope(x, fs)
    thr = np.median(env) + 2 * np.std(env)
    peaks = (env[1:-1] > thr) & (env[1:-1] >= env[:-2]) & (env[1:-1] > env[2:])
    duration = len(x) / fs
    clip = clipping_runs(x)
    return {
        "sample_peak_dbfs": round(to_db(peak), 2),
        "rms_dbfs": round(rms_db, 2),
        "crest_factor_db": round(to_db(peak) - rms_db, 2) if peak > 0 else 0.0,
        "dynamic_range_db": round(dyn_range, 2),
        "transient_crest_50ms_db": round(transient_crest, 2),
        "onsets_per_second": round(float(peaks.sum()) / duration, 2) if duration else 0.0,
        "dc_offset": [round(float(v), 6) for v in x.mean(axis=0)],
        "clipping_runs": clip["runs"],
        "clipped_samples": clip["clipped_samples"],
        "duration_s": round(duration, 3),
    }
