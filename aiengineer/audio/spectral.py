"""Análisis espectral: espectro medio, energía por bandas, resonancias persistentes, enmascaramiento."""
from __future__ import annotations

import numpy as np

# Bandas con nombre usadas en informes (Hz). Son convenciones de trabajo, no límites físicos.
BANDS = [
    ("subgrave", 20, 60),
    ("grave", 60, 120),
    ("medio-grave", 120, 250),
    ("barro", 250, 500),
    ("medios", 500, 2000),
    ("medio-agudos", 2000, 5000),
    ("presencia", 5000, 10000),
    ("aire", 10000, 20000),
]


def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def stft_power(x: np.ndarray, fs: int, n_fft: int = 4096, hop: int = 2048, max_frames: int = 4000):
    """Potencia por trama (tramas x bins). Si hay demasiadas tramas se submuestrean de forma uniforme."""
    x = mono(np.asarray(x, dtype=np.float64))
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n_frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_frames)
    if n_frames > max_frames:
        idx = np.linspace(0, n_frames - 1, max_frames).astype(int)
    win = np.hanning(n_fft)
    frames = np.stack([x[i * hop:i * hop + n_fft] for i in idx]) * win
    p = np.abs(np.fft.rfft(frames, axis=1)) ** 2 / (np.sum(win ** 2) * fs)
    p[:, 1:-1] *= 2
    freqs = np.fft.rfftfreq(n_fft, 1 / fs)
    times = idx * hop / fs
    return freqs, times, p


def band_energies(freqs: np.ndarray, power: np.ndarray, bands=BANDS) -> dict:
    """Energía relativa por banda (porcentaje del total 20 Hz-20 kHz y nivel en dB)."""
    spec = power.mean(axis=0) if power.ndim == 2 else power
    total = spec[(freqs >= 20) & (freqs < 20000)].sum() + 1e-30
    out = {}
    for name, lo, hi in bands:
        e = spec[(freqs >= lo) & (freqs < hi)].sum()
        out[name] = {"pct": float(100 * e / total), "db": float(10 * np.log10(e + 1e-30))}
    return out


def smooth_octave(freqs: np.ndarray, spec: np.ndarray, fraction: float = 3.0) -> np.ndarray:
    """Suavizado en fracción de octava (media en ventana proporcional a la frecuencia)."""
    out = np.empty_like(spec)
    cs = np.concatenate([[0.0], np.cumsum(spec)])
    k = 2 ** (1 / (2 * fraction))
    lo = np.searchsorted(freqs, freqs / k)
    hi = np.searchsorted(freqs, freqs * k, side="right")
    hi = np.maximum(hi, lo + 1)
    out[:] = (cs[hi] - cs[lo]) / (hi - lo)
    return out


def find_resonances(freqs, power, min_prominence_db: float = 6.0, min_persistence: float = 0.6,
                    fmin: float = 40.0, fmax: float = 16000.0, max_results: int = 8) -> list[dict]:
    """Picos estrechos que sobresalen del entorno de 1/3 de octava y persisten en el tiempo.

    Una nota musical cambia; una resonancia (sala, filtro, sintetizador) suele permanecer. Por eso
    se exige persistencia en la mayoría de las tramas con señal. Es una medición: decidir si hay que
    corregirla depende del papel del sonido.
    """
    spec = power.mean(axis=0)
    sm = smooth_octave(freqs, spec, 3.0)
    rel_db = 10 * np.log10((spec + 1e-30) / (sm + 1e-30))
    cands = []
    for i in range(1, len(freqs) - 1):
        f = freqs[i]
        if f < fmin or f > fmax:
            continue
        if rel_db[i] >= min_prominence_db and rel_db[i] >= rel_db[i - 1] and rel_db[i] >= rel_db[i + 1]:
            cands.append(i)
    if not cands:
        return []
    frame_energy = power.sum(axis=1)
    active = frame_energy > frame_energy.max() * 1e-4
    psm = np.stack([smooth_octave(freqs, fr, 3.0) for fr in power[active]]) if active.any() else None
    res = []
    for i in cands:
        if psm is None:
            break
        pa = power[active][:, i]
        persist = float(np.mean(10 * np.log10((pa + 1e-30) / (psm[:, i] + 1e-30)) > min_prominence_db / 2))
        if persist >= min_persistence:
            res.append({"freq_hz": round(float(freqs[i]), 1), "prominence_db": round(float(rel_db[i]), 1),
                        "persistence": round(persist, 2)})
    res.sort(key=lambda r: -r["prominence_db"])
    return res[:max_results]


def third_octave_centers(fmin=25.0, fmax=16000.0) -> np.ndarray:
    n = np.arange(np.floor(3 * np.log2(fmin / 1000)), np.ceil(3 * np.log2(fmax / 1000)) + 1)
    return 1000 * 2 ** (n / 3)


def third_octave_frames(freqs, power) -> tuple[np.ndarray, np.ndarray]:
    centers = third_octave_centers()
    k = 2 ** (1 / 6)
    bands = np.stack([power[:, (freqs >= c / k) & (freqs < c * k)].sum(axis=1) for c in centers], axis=1)
    return centers, bands


def masking_conflicts(a: np.ndarray, b: np.ndarray, fs: int, threshold_db: float = 6.0,
                      min_fraction: float = 0.3) -> list[dict]:
    """Bandas de tercio de octava en las que dos pistas tienen energía significativa al mismo tiempo
    con niveles próximos (diferencia < threshold_db). Devuelve las bandas ordenadas por gravedad."""
    n = min(len(a), len(b))
    fa, _, pa = stft_power(a[:n], fs)
    _, _, pb = stft_power(b[:n], fs)
    centers, ba = third_octave_frames(fa, pa)
    _, bb = third_octave_frames(fa, pb)
    da = 10 * np.log10(ba + 1e-30)
    db = 10 * np.log10(bb + 1e-30)
    # "significativa": a menos de 30 dB del máximo de la propia pista en esa banda
    sig_a = da > (da.max(axis=0, keepdims=True) - 30)
    sig_b = db > (db.max(axis=0, keepdims=True) - 30)
    # además, la banda debe ser relevante dentro del espectro de cada pista
    rel_a = ba.mean(axis=0) / (ba.mean(axis=0).sum() + 1e-30)
    rel_b = bb.mean(axis=0) / (bb.mean(axis=0).sum() + 1e-30)
    close = np.abs(da - db) < threshold_db
    both = sig_a & sig_b & close
    frac = both.mean(axis=0)
    out = []
    for i, c in enumerate(centers):
        if frac[i] >= min_fraction and rel_a[i] > 0.02 and rel_b[i] > 0.02:
            louder = "A" if np.median(da[:, i] - db[:, i]) > 0 else "B"
            out.append({"band_hz": round(float(c), 1), "fraction_of_time": round(float(frac[i]), 2),
                        "weight": round(float(frac[i] * min(rel_a[i], rel_b[i]) * 100), 2), "dominant": louder})
    out.sort(key=lambda r: -r["weight"])
    return out
