"""Análisis espacial: correlación, anchura M/S, compatibilidad mono, equilibrio L/R."""
from __future__ import annotations

import numpy as np

from .dsp import biquad_filter, power_db, rbj
from .loudness import integrated_lufs


def _corr(l: np.ndarray, r: np.ndarray) -> float:
    den = np.sqrt(np.sum(l * l) * np.sum(r * r))
    return float(np.sum(l * r) / den) if den > 0 else 1.0


def analyze_stereo(x: np.ndarray, fs: int) -> dict:
    if x.ndim == 1 or x.shape[1] == 1:
        return {"channels": 1, "note": "archivo mono: no hay imagen estéreo que analizar"}
    l, r = x[:, 0], x[:, 1]
    mid = (l + r) / 2
    side = (l - r) / 2
    win = int(0.4 * fs)
    corrs = []
    for s in range(0, len(l) - win, win):
        ls, rs = l[s:s + win], r[s:s + win]
        if np.sum(ls * ls) + np.sum(rs * rs) > win * 1e-8:
            corrs.append(_corr(ls, rs))
    b, a = rbj("lowpass", fs, 150.0, 0.707)
    low = biquad_filter(b, a, x[:, :2])
    stereo_lufs = integrated_lufs(x[:, :2], fs)
    mono_sum = np.stack([mid, mid], axis=1)
    mono_lufs = integrated_lufs(mono_sum, fs)
    pl, pr = float(np.mean(l * l)), float(np.mean(r * r))
    return {
        "channels": int(x.shape[1]),
        "correlation": round(_corr(l, r), 3),
        "correlation_min_400ms": round(float(min(corrs)), 3) if corrs else None,
        "correlation_p5_400ms": round(float(np.percentile(corrs, 5)), 3) if corrs else None,
        "low_end_correlation_below_150hz": round(_corr(low[:, 0], low[:, 1]), 3),
        "side_to_mid_db": round(power_db(float(np.mean(side * side))) - power_db(float(np.mean(mid * mid))), 2),
        "mono_loudness_change_db": round(mono_lufs - stereo_lufs, 2)
        if np.isfinite(mono_lufs) and np.isfinite(stereo_lufs) else None,
        "lr_balance_db": round(power_db(pl) - power_db(pr), 2),
        "is_dual_mono": bool(np.max(np.abs(l - r)) < 1e-6),
    }
