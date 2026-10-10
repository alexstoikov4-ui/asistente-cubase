"""Análisis musical desde audio: tempo, tonalidad y segmentación por energía/novedad (estimaciones)."""
from __future__ import annotations

import numpy as np

from .dynamics import onset_envelope

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Perfiles de Krumhansl-Kessler
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_tempo(x: np.ndarray, fs: int, bpm_min: float = 60, bpm_max: float = 200,
                   prior_bpm: float = 125.0) -> dict:
    """Autocorrelación de la envolvente de ataques con armónicos y una preferencia suave (log-normal)
    por tempos cercanos a prior_bpm, que reduce los errores de mitad/doble tempo en música de baile."""
    env, rate = onset_envelope(x, fs)
    if len(env) < 64 or env.std() == 0:
        return {"bpm": None, "confidence": 0.0, "candidates": []}
    e = env - env.mean()
    n = len(e)
    spec = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= ac[0] + 1e-12
    lags = np.arange(n)
    bpms = 60 * rate / np.maximum(lags, 1)
    valid = (bpms >= bpm_min) & (bpms <= bpm_max)
    # Puntuación con armónicos: un pulso real también correlaciona a 2x y 4x el periodo.
    score = np.zeros(n)
    for lag in np.where(valid)[0]:
        s = ac[lag]
        for mult, w in ((2, 0.5), (4, 0.25)):
            if lag * mult < n:
                s += w * ac[lag * mult]
        bpm_l = 60 * rate / lag
        score[lag] = s * float(np.exp(-0.5 * (np.log2(bpm_l / prior_bpm) / 0.5) ** 2))
    order = np.argsort(score)[::-1]
    cands = []
    for lag in order:
        if not valid[lag] or score[lag] <= 0:
            continue
        # refinamiento parabólico del máximo
        if 0 < lag < n - 1:
            y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
            d = (y0 - y2) / (2 * (y0 - 2 * y1 + y2) + 1e-12)
            true_lag = lag + float(np.clip(d, -0.5, 0.5))
        else:
            true_lag = float(lag)
        bpm = 60 * rate / true_lag
        if all(abs(bpm - c["bpm"]) / c["bpm"] > 0.03 for c in cands):
            cands.append({"bpm": round(float(bpm), 2), "score": round(float(score[lag]), 3)})
        if len(cands) >= 3:
            break
    if not cands:
        return {"bpm": None, "confidence": 0.0, "candidates": []}
    conf = cands[0]["score"] / (sum(c["score"] for c in cands) + 1e-12)
    return {"bpm": cands[0]["bpm"], "confidence": round(float(conf), 2), "candidates": cands}


def chroma(x: np.ndarray, fs: int, n_fft: int = 8192, hop: int = 4096, fmin: float = 55, fmax: float = 4000):
    m = x.mean(axis=1) if x.ndim == 2 else x
    if len(m) < n_fft:
        m = np.pad(m, (0, n_fft - len(m)))
    freqs = np.fft.rfftfreq(n_fft, 1 / fs)
    sel = (freqs >= fmin) & (freqs <= fmax)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0)).astype(int) + 9) % 12
    n = 1 + (len(m) - n_fft) // hop
    idx = np.arange(n)
    if n > 1500:
        idx = np.linspace(0, n - 1, 1500).astype(int)
    win = np.hanning(n_fft)
    out = np.zeros((len(idx), 12))
    for j, i in enumerate(idx):
        mag = np.abs(np.fft.rfft(m[i * hop:i * hop + n_fft] * win))[sel]
        out[j] = np.bincount(pcs, weights=mag ** 2, minlength=12)
    return out


def estimate_key(x: np.ndarray, fs: int) -> dict:
    c = chroma(x, fs)
    if c.sum() == 0:
        return {"key": None, "confidence": 0.0}
    prof = np.sqrt(c).sum(axis=0)  # compresión para que no domine un solo bin
    scores = []
    for tonic in range(12):
        for mode, p in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(prof, np.roll(p, tonic))[0, 1]
            scores.append((r, tonic, mode))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    name = NOTE_NAMES[best[1]] + ("" if best[2] == "major" else "m")
    return {"key": name, "tonic": NOTE_NAMES[best[1]], "mode": best[2], "correlation": round(float(best[0]), 3),
            "confidence": round(float(best[0] - second[0]), 3),
            "alternative": NOTE_NAMES[second[1]] + ("" if second[2] == "major" else "m")}


def segment_structure(x: np.ndarray, fs: int, bpm: float | None, beats_per_bar: int = 4,
                      bars_per_unit: int = 4) -> list[dict]:
    """Divide el tema en bloques de N compases y marca cambios de energía/timbre significativos.
    Sin tempo fiable usa bloques de 8 s. Los nombres de sección se deducen por energía relativa
    (heurístico: 'alta energía' suele ser drop/estribillo en música electrónica)."""
    m = x.mean(axis=1) if x.ndim == 2 else x
    unit_s = (60.0 / bpm) * beats_per_bar * bars_per_unit if bpm else 8.0
    unit = int(unit_s * fs)
    if unit <= 0 or len(m) < unit:
        return []
    n = len(m) // unit
    feats = []
    energies = []
    for i in range(n):
        seg = m[i * unit:(i + 1) * unit]
        spec = np.abs(np.fft.rfft(seg[:: max(1, len(seg) // 16384)]))
        bands = np.array_split(spec, 8)
        f = np.log10(np.array([b.sum() for b in bands]) + 1e-9)
        feats.append(f)
        energies.append(10 * np.log10(np.mean(seg ** 2) + 1e-12))
    feats = np.array(feats)
    energies = np.array(energies)
    hi, lo = np.percentile(energies, 75), np.percentile(energies, 25)
    sections = []
    for i in range(n):
        novelty = float(np.linalg.norm(feats[i] - feats[i - 1])) if i else 0.0
        level = "alta" if energies[i] >= hi - 1 else ("baja" if energies[i] <= lo + 1 else "media")
        sections.append({"start_s": round(i * unit_s, 2), "end_s": round((i + 1) * unit_s, 2),
                         "energy_db": round(float(energies[i]), 1), "energy_level": level,
                         "novelty": round(novelty, 2)})
    if len(sections) > 2:
        nov = np.array([s["novelty"] for s in sections[1:]])
        thr = nov.mean() + nov.std()
        for s in sections[1:]:
            s["boundary"] = bool(s["novelty"] > thr)
    return sections
