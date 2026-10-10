"""Orquestador del análisis de audio: mediciones objetivas + observaciones interpretadas por separado."""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from . import loudness
from .dynamics import analyze_dynamics
from .musical import estimate_key, estimate_tempo, segment_structure
from .spectral import band_energies, find_resonances, stft_power
from .stereo import analyze_stereo
from .wavio import read_wav


def _clean(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def analyze_array(x: np.ndarray, fs: int, *, musical: bool = True, role: str = "mix") -> dict:
    t0 = time.time()
    if x.ndim == 1:
        x = x[:, None]
    freqs, _, power = stft_power(x, fs)
    meas = {
        "sample_rate": fs,
        "channels": int(x.shape[1]),
        "loudness": loudness.measure(x, fs),
        "dynamics": analyze_dynamics(x, fs),
        "bands": band_energies(freqs, power),
        "resonances": find_resonances(freqs, power),
        "stereo": analyze_stereo(x, fs),
    }
    if meas["loudness"]["true_peak_dbtp"] is not None and np.isfinite(meas["loudness"]["integrated_lufs"]):
        meas["loudness"]["plr_db"] = round(meas["loudness"]["true_peak_dbtp"] - meas["loudness"]["integrated_lufs"], 2)
    if musical:
        tempo = estimate_tempo(x, fs)
        meas["tempo"] = tempo
        meas["key"] = estimate_key(x, fs)
        meas["structure"] = segment_structure(x, fs, tempo["bpm"] if tempo["confidence"] >= 0.4 else None)
    meas["analysis_seconds"] = round(time.time() - t0, 2)
    report = {"measurements": _clean(meas), "observations": observations(meas, role)}
    return report


def analyze_file(path, **kw) -> dict:
    x, info = read_wav(path)
    rep = analyze_array(x, info.sample_rate, **kw)
    rep["file"] = str(Path(path))
    rep["format"] = {"sample_rate": info.sample_rate, "bits": info.bits, "channels": info.channels,
                     "float": info.format_tag == 3, "duration_s": round(info.duration, 3)}
    return rep


def _obs(kind: str, severity: str, text: str, evidence: dict, area: str) -> dict:
    """kind: 'medición' (hecho objetivo) | 'técnico' (problema técnico claro) | 'artístico' (depende del contexto)."""
    return {"kind": kind, "severity": severity, "area": area, "text": text, "evidence": evidence}


def observations(m: dict, role: str = "mix") -> list[dict]:
    obs = []
    dyn, lou, st, bands = m["dynamics"], m["loudness"], m["stereo"], m["bands"]
    tp = lou.get("true_peak_dbtp")
    if dyn["clipping_runs"] > 0:
        obs.append(_obs("técnico", "alta", f"Se detectan {dyn['clipping_runs']} tramos de muestras recortadas "
                        f"({dyn['clipped_samples']} muestras).", {"clipping_runs": dyn["clipping_runs"]}, "dinámica"))
    if tp is not None and tp > 0.0:
        obs.append(_obs("técnico", "alta", f"True peak de {tp:.2f} dBTP: habrá distorsión al convertir a "
                        "analógico o a formatos con pérdida.", {"true_peak_dbtp": tp}, "dinámica"))
    elif tp is not None and tp > -1.0 and role == "master":
        obs.append(_obs("técnico", "media", f"True peak de {tp:.2f} dBTP: para streaming se suele dejar "
                        "un techo de -1 dBTP.", {"true_peak_dbtp": tp}, "dinámica"))
    if role == "mix" and tp is not None and tp > -3.0:
        obs.append(_obs("técnico", "media", f"La mezcla llega a {tp:.2f} dBTP: queda poco margen para la "
                        "masterización (recomendable dejar 3-6 dB).", {"true_peak_dbtp": tp}, "dinámica"))
    for i, dc in enumerate(dyn["dc_offset"]):
        if abs(dc) > 0.005:
            obs.append(_obs("técnico", "media", f"Desplazamiento DC en canal {i + 1} ({dc:+.4f}).",
                            {"dc": dc}, "dinámica"))
    if dyn["crest_factor_db"] < 8 and dyn["duration_s"] > 5:
        obs.append(_obs("artístico", "media", f"Factor de cresta de {dyn['crest_factor_db']:.1f} dB: la señal "
                        "está muy comprimida/limitada. Puede ser intencionado según el estilo, pero limita la "
                        "pegada de los transitorios.", {"crest_factor_db": dyn["crest_factor_db"]}, "dinámica"))
    sub, grave = bands["subgrave"]["pct"], bands["grave"]["pct"]
    if role in ("mix", "master"):
        if sub + grave > 70:
            obs.append(_obs("artístico", "media", f"El {sub + grave:.0f}% de la energía está por debajo de "
                            "120 Hz. En EDM los graves dominan la energía, pero valores tan altos suelen indicar "
                            "acumulación de graves; conviene revisar kick, bajo y subgrave.",
                            {"sub_pct": sub, "bass_pct": grave}, "frecuencia"))
        if bands["barro"]["pct"] > 18:
            obs.append(_obs("artístico", "baja", f"Energía elevada en 250-500 Hz ({bands['barro']['pct']:.0f}%): "
                            "posible sensación de 'barro'. Compruébalo en contexto antes de recortar.",
                            {"pct": bands["barro"]["pct"]}, "frecuencia"))
        if bands["aire"]["pct"] < 0.05 and bands["presencia"]["pct"] < 0.5:
            obs.append(_obs("artístico", "baja", "Muy poca energía por encima de 5 kHz: la mezcla puede sonar "
                            "apagada, o el material puede venir de una fuente de banda limitada.",
                            {"presence_pct": bands["presencia"]["pct"], "air_pct": bands["aire"]["pct"]}, "frecuencia"))
    for r in m["resonances"][:3]:
        obs.append(_obs("medición", "baja", f"Pico persistente en {r['freq_hz']:.0f} Hz (+{r['prominence_db']:.1f} dB "
                        f"sobre su entorno, presente en el {100 * r['persistence']:.0f}% del tiempo). Puede ser una "
                        "resonancia o una nota pedal intencionada.", r, "frecuencia"))
    if st.get("channels", 1) >= 2:
        if st.get("is_dual_mono"):
            obs.append(_obs("medición", "baja", "Los dos canales son idénticos (mono duplicado).", {}, "estéreo"))
        else:
            if st["correlation"] < 0.0:
                obs.append(_obs("técnico", "alta", f"Correlación global negativa ({st['correlation']}): posible "
                                "inversión de polaridad o problema de fase grave.", {"correlation": st["correlation"]},
                                "estéreo"))
            elif st.get("correlation_p5_400ms") is not None and st["correlation_p5_400ms"] < -0.2:
                obs.append(_obs("técnico", "media", "Hay pasajes con correlación negativa: riesgo de cancelación en mono.",
                                {"p5": st["correlation_p5_400ms"]}, "estéreo"))
            if st["low_end_correlation_below_150hz"] < 0.8:
                obs.append(_obs("técnico", "media", f"Graves (<150 Hz) poco correlacionados "
                                f"({st['low_end_correlation_below_150hz']}): en sistemas de club los graves suelen ser "
                                "mono; revisa anchura de bajo/sub y reverbs.",
                                {"low_corr": st["low_end_correlation_below_150hz"]}, "estéreo"))
            if st["mono_loudness_change_db"] is not None and st["mono_loudness_change_db"] < -3:
                obs.append(_obs("técnico", "media", f"En mono la sonoridad cae {abs(st['mono_loudness_change_db']):.1f} dB.",
                                {"mono_change": st["mono_loudness_change_db"]}, "estéreo"))
            if abs(st["lr_balance_db"]) > 1.5:
                obs.append(_obs("medición", "media", f"Desequilibrio de nivel L/R de {st['lr_balance_db']:+.1f} dB.",
                                {"lr_balance_db": st["lr_balance_db"]}, "estéreo"))
    return obs
