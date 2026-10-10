"""Motor de masterización: analiza, decide la cadena, la previsualiza offline y verifica el resultado."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..audio import loudness
from ..audio.analyzer import analyze_array
from ..audio.dsp import to_db
from ..audio.spectral import band_energies, stft_power
from ..audio.wavio import read_wav, write_wav
from . import processing as proc

# Objetivos orientativos por destino. No son obligatorios: el motor no los persigue si para
# alcanzarlos hay que destruir la dinámica. max_limiting_db limita la reducción de ganancia del
# limitador en el percentil 95 de los bloques con señal (es decir, la que sufre cada golpe de
# bombo, no un pico aislado); min_plr es la relación pico/sonoridad mínima aceptable.
TARGETS = {
    "streaming": {"label": "Streaming (Spotify/Apple/YouTube)", "lufs": -14.0, "ceiling": -1.0, "max_limiting_db": 3.0, "min_plr": 9.0},
    "comercial": {"label": "Lanzamiento comercial / descarga", "lufs": -10.0, "ceiling": -1.0, "max_limiting_db": 6.0, "min_plr": 7.5},
    "club": {"label": "Club", "lufs": -8.5, "ceiling": -0.5, "max_limiting_db": 7.0, "min_plr": 7.0},
    "festival": {"label": "Festival / sistema de gran formato", "lufs": -7.5, "ceiling": -0.5, "max_limiting_db": 8.0, "min_plr": 6.5},
    "cd": {"label": "CD", "lufs": -9.0, "ceiling": -0.3, "max_limiting_db": 6.0, "min_plr": 7.0},
}

# Rangos amplios de equilibrio tonal (porcentaje de energía) por familia de estilo. Fuera de estos
# márgenes el motor propone correcciones suaves; dentro, no toca nada.
TONAL_RANGES = {
    "electronic": {"subgrave": (10, 45), "grave": (12, 45), "barro": (2, 14), "medios": (4, 25),
                   "medio-agudos": (1, 12), "presencia": (0.3, 8), "aire": (0.02, 3)},
}


@dataclass
class MasterSettings:
    target: str = "streaming"
    lufs: float | None = None
    ceiling_dbtp: float | None = None
    sample_rate: int | None = None
    bits: int = 24
    style_family: str = "electronic"
    allow_saturation: bool = True
    allow_compression: bool = True
    reference_bands: dict | None = None  # bandas de una referencia (pct) si el usuario la aporta


@dataclass
class MasterResult:
    output: str = ""
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    chain: list = field(default_factory=list)
    stages: list = field(default_factory=list)
    comparison: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    verified: bool = False


def decide_chain(meas: dict, st: MasterSettings) -> tuple[list[dict], list[str]]:
    """Decide la cadena a partir de las mediciones. Cada etapa incluye su justificación."""
    chain, notes = [], []
    bands = meas["bands"]
    stereo = meas["stereo"]
    dyn = meas["dynamics"]
    if dyn["clipping_runs"] > 0:
        notes.append("La mezcla ya llega recortada: la masterización no puede reparar el clipping; "
                     "conviene exportar de nuevo la mezcla con margen.")
    if any(abs(d) > 0.002 for d in dyn["dc_offset"]) or bands["subgrave"]["pct"] > 0 and meas.get("_infrasonic_pct", 0) > 2:
        chain.append({"stage": "eq", "type": "highpass", "freq": 20.0, "q": 0.707,
                      "why": "Eliminar DC/infrasónicos que consumen margen sin aportar nada audible."})
    ranges = TONAL_RANGES.get(st.style_family, TONAL_RANGES["electronic"])
    eq_moves = []
    centers = {"subgrave": (40, "lowshelf", 0.7), "grave": (90, "peak", 0.9), "barro": (350, "peak", 1.0),
               "medios": (1000, "peak", 0.8), "medio-agudos": (3200, "peak", 0.9), "presencia": (7000, "peak", 0.8),
               "aire": (12000, "highshelf", 0.7)}
    for band, (lo, hi) in ranges.items():
        pct = bands[band]["pct"]
        ref = st.reference_bands.get(band) if st.reference_bands else None
        gain = 0.0
        if ref is not None and ref > 0:
            diff_db = 10 * math.log10(max(ref, 1e-6) / max(pct, 1e-6))
            if abs(diff_db) > 3:  # solo diferencias claras, y como mucho la mitad del camino
                gain = max(-2.0, min(2.0, diff_db / 2))
        elif pct > hi:
            gain = -min(2.0, 10 * math.log10(pct / hi))
        elif pct < lo:
            gain = min(1.5, 10 * math.log10(lo / max(pct, 1e-6)) / 2)
        if abs(gain) >= 0.5:
            f, kind, q = centers[band]
            eq_moves.append({"stage": "eq", "type": kind, "freq": f, "gain_db": round(gain, 1), "q": q,
                             "why": f"Banda '{band}' en {pct:.1f}% de la energía "
                                    f"({'referencia ' + format(ref, '.1f') + '%' if ref is not None else f'rango orientativo {lo}-{hi}%'}); "
                                    "corrección amplia y moderada."})
    chain += eq_moves[:3]  # nunca más de tres movimientos de EQ en el máster
    if stereo.get("channels", 1) >= 2 and stereo.get("low_end_correlation_below_150hz") is not None \
            and stereo["low_end_correlation_below_150hz"] < 0.9 and not stereo.get("is_dual_mono"):
        chain.append({"stage": "mono_bass", "freq": 100.0,
                      "why": f"Correlación de graves {stereo['low_end_correlation_below_150hz']}: se hace mono por "
                             "debajo de 100 Hz para que el grave sea sólido en sistemas de club/festival."})
    crest = dyn["crest_factor_db"]
    if crest > 14 and st.allow_compression:
        chain.append({"stage": "compress", "threshold_offset_db": -6.0, "ratio": 1.5, "attack_ms": 30.0,
                      "release_ms": 200.0, "why": f"Factor de cresta alto ({crest:.1f} dB): compresión suave de "
                                                  "cohesión (1.5:1, ataque lento para conservar transitorios)."})
    else:
        notes.append(f"Factor de cresta {crest:.1f} dB: no se añade compresión de bus; la mezcla ya está controlada.")
    if st.allow_saturation and crest > 12 and bands["presencia"]["pct"] < 1.0:
        chain.append({"stage": "saturate", "drive_db": 2.0, "mix": 0.25,
                      "why": "Poca energía en presencia y dinámica amplia: saturación paralela suave para densidad."})
    chain.append({"stage": "limit", "why": "Limitador true peak para alcanzar el objetivo sin superar el techo."})
    return chain, notes


def _pre_limiter(x: np.ndarray, fs: int, chain: list[dict]) -> tuple[np.ndarray, list]:
    """Aplica todas las etapas anteriores al limitador (no dependen de la ganancia de entrada)."""
    stages = []
    y = x
    for st in chain:
        if st["stage"] == "eq":
            y = proc.apply_eq(y, fs, [st])
            stages.append({"stage": "eq", "params": {k: st[k] for k in ("type", "freq", "gain_db", "q") if k in st}})
        elif st["stage"] == "mono_bass":
            y = proc.mono_below(y, fs, st["freq"])
            stages.append({"stage": "mono_bass", "params": {"freq": st["freq"]}})
        elif st["stage"] == "compress":
            rms = 10 * math.log10(np.mean(y ** 2) + 1e-12)
            y, s = proc.compress(y, fs, rms + st["threshold_offset_db"] + 6, st["ratio"], st["attack_ms"], st["release_ms"])
            stages.append({"stage": "compress", "params": {"ratio": st["ratio"]}, "stats": s})
        elif st["stage"] == "saturate":
            y = proc.soft_saturate(y, st["drive_db"], st["mix"])
            stages.append({"stage": "saturate", "params": {"drive_db": st["drive_db"], "mix": st["mix"]}})
    return y, stages


def loudness_matched_comparison(a: np.ndarray, b: np.ndarray, fs: int) -> dict:
    """Compara b con a tras igualar su sonoridad integrada (evita confundir más volumen con mejor)."""
    la, lb = loudness.integrated_lufs(a, fs), loudness.integrated_lufs(b, fs)
    g = 10 ** ((la - lb) / 20) if np.isfinite(la) and np.isfinite(lb) else 1.0
    bm = b * g
    fa, _, pa = stft_power(a, fs)
    _, _, pb = stft_power(bm, fs)
    ea, eb = band_energies(fa, pa), band_energies(fa, pb)
    def crest(x):
        pk = float(np.max(np.abs(x))) or 1e-12
        return to_db(pk) - 10 * math.log10(np.mean(x ** 2) + 1e-12)
    return {
        "matched_gain_db": round(20 * math.log10(g), 2),
        "band_change_db": {k: round(eb[k]["db"] - ea[k]["db"], 2) for k in ea},
        "crest_change_db": round(crest(bm) - crest(a), 2),
    }


def master_file(path, out_path, settings: MasterSettings, stop_flag=None) -> MasterResult:
    x, info = read_wav(path)
    fs = info.sample_rate
    res = MasterResult()
    before = analyze_array(x, fs, musical=False, role="mix")
    res.before = before["measurements"]
    tgt = TARGETS.get(settings.target, TARGETS["streaming"])
    lufs_target = settings.lufs if settings.lufs is not None else tgt["lufs"]
    ceiling = settings.ceiling_dbtp if settings.ceiling_dbtp is not None else tgt["ceiling"]
    infra = band_energies(*stft_power(x, fs)[::2], bands=[("infra", 0, 20)])["infra"]["pct"]
    meas = dict(res.before)
    meas["_infrasonic_pct"] = infra
    chain, notes = decide_chain(meas, settings)
    res.chain = chain
    res.warnings += notes
    current = res.before["loudness"]["integrated_lufs"]
    if current is None:
        res.warnings.append("No se puede medir la sonoridad (archivo silencioso o demasiado corto).")
        return res
    pre, stages = _pre_limiter(x, fs, chain)
    env = proc.peak_envelope(pre)
    pre_lufs = loudness.integrated_lufs(pre, fs)

    def attempt(g):
        y_, s_ = proc.limit(pre, fs, ceiling, input_gain_db=g, peak_env=env)
        l_ = loudness.integrated_lufs(y_, fs)
        tp_ = to_db(float(np.max(env)) * 10 ** (g / 20) * 10 ** (-s_["max_gr_db"] / 20))
        ok_ = s_["p95_gr_db"] <= tgt["max_limiting_db"] and (min(tp_, ceiling) - l_) >= tgt["min_plr"]
        return y_, s_, l_, ok_

    # Búsqueda de la ganancia: la mayor que no supere el objetivo ni los límites de limitación/PLR.
    lo_g = ceiling - to_db(float(np.max(env)))  # sin reducción de ganancia
    hi_g = lufs_target - pre_lufs + 1.0
    best = None
    y, lim_stats, achieved, ok = attempt(min(hi_g - 1.0, max(lo_g, hi_g - 1.0)))
    if ok and achieved <= lufs_target + 0.3:
        best = (hi_g - 1.0, y, lim_stats, achieved)
    else:
        lo, hi = min(lo_g, hi_g - 1.0), hi_g
        for _ in range(8):
            if stop_flag is not None and stop_flag.is_set():
                res.warnings.append("Proceso detenido por el usuario.")
                return res
            mid = (lo + hi) / 2
            y_m, s_m, l_m, ok_m = attempt(mid)
            if ok_m and l_m <= lufs_target + 0.3:
                best = (mid, y_m, s_m, l_m)
                lo = mid
            else:
                hi = mid
        if best is None:
            y_m, s_m, l_m, _ = attempt(min(lo_g, hi_g - 1.0))
            best = (lo_g, y_m, s_m, l_m)
    gain, y, lim_stats, achieved = best
    stages.append({"stage": "limit", "params": {"input_gain_db": round(gain, 2), "ceiling_dbtp": ceiling},
                   "stats": lim_stats})
    if achieved < lufs_target - 0.5:
        res.warnings.append(
            f"Objetivo de {lufs_target} LUFS no alcanzado: se ha parado en {achieved:.1f} LUFS porque más sonoridad "
            f"exigiría más de {tgt['max_limiting_db']} dB de limitación sostenida o un PLR inferior a {tgt['min_plr']} dB. "
            "Para llegar sin aplastar los transitorios hay que controlar los picos en la mezcla (p.ej. el kick).")
    # comprobación de seguridad del techo (picos entre muestras residuales de la interpolación)
    tp = loudness.measure(y, fs)["true_peak_dbtp"]
    if tp > ceiling + 0.05:
        y = y * 10 ** ((ceiling - tp - 0.05) / 20)
    sr_out = settings.sample_rate or fs
    if sr_out != fs:
        y = proc.resample_fft(y, fs, sr_out)
        res.warnings.append(f"Remuestreado de {fs} a {sr_out} Hz (FFT, limitado en banda).")
        tp = loudness.measure(y, sr_out)["true_peak_dbtp"]
        if tp > ceiling:
            y = y * 10 ** ((ceiling - tp - 0.05) / 20)
    if settings.bits == 16:
        y = proc.tpdf_dither(y, 16)
    write_wav(out_path, y, sr_out, bits=settings.bits)
    # verificación: se vuelve a leer el archivo exportado y se mide
    y2, info2 = read_wav(out_path)
    after = analyze_array(y2, info2.sample_rate, musical=False, role="master")
    res.after = after["measurements"]
    res.stages = stages
    res.output = str(out_path)
    if info2.sample_rate == fs:
        res.comparison = loudness_matched_comparison(x, y2, fs)
    a_l = res.after["loudness"]
    checks = {
        "true_peak_ok": a_l["true_peak_dbtp"] <= ceiling + 0.1,
        "no_new_clipping": res.after["dynamics"]["clipping_runs"] <= res.before["dynamics"]["clipping_runs"],
        "format_ok": info2.sample_rate == sr_out and info2.bits == settings.bits,
    }
    res.verified = all(checks.values())
    res.comparison["checks"] = checks
    res.comparison["target_lufs"] = lufs_target
    res.comparison["achieved_lufs"] = a_l["integrated_lufs"]
    tb = res.before["dynamics"]["transient_crest_50ms_db"]
    ta = res.after["dynamics"]["transient_crest_50ms_db"]
    res.comparison["transient_crest_change_db"] = round(ta - tb, 2)
    if ta - tb < -3:
        res.warnings.append(f"La cresta de los transitorios (ventanas de 50 ms) baja {tb - ta:.1f} dB: la pegada se "
                            "ha reducido; considera un objetivo menos agresivo.")
    return res


def plugin_chain_suggestion(chain: list[dict], available: dict) -> list[dict]:
    """Asigna cada etapa a un plugin realmente instalado (si lo hay). available: rol -> [nombres]."""
    pref = {"eq": ["FabFilter Pro-Q 3", "Q10", "Renaissance Equalizer", "LinEQ", "StudioEQ"],
            "highpass": ["FabFilter Pro-Q 3", "Q10", "StudioEQ"],
            "mono_bass": ["FabFilter Pro-Q 3", "S1 Imager", "StudioEQ"],
            "compress": ["Renaissance Compressor", "C1", "SSLComp", "VintageCompressor", "Compressor"],
            "saturate": ["FabFilter Saturn 2", "Saturn 2", "Kramer Tape", "Magneto", "DaTube"],
            "limit": ["Limiter 6", "Limiter No6", "L2", "L3", "L1", "Maximizer", "Limiter"]}
    role_of = {"eq": "eq", "mono_bass": "eq", "compress": "compressor", "saturate": "saturation", "limit": "limiter"}
    out = []
    for st in chain:
        role = role_of.get(st["stage"], st["stage"])
        names = available.get(role, [])
        choice = None
        for p in pref.get(st["stage"], []):
            for n in names:
                if p.lower().replace(" ", "") in n.lower().replace(" ", ""):
                    choice = n
                    break
            if choice:
                break
        if not choice and names:
            choice = names[0]
        out.append({"stage": st["stage"], "plugin": choice or "(no se ha encontrado un plugin instalado para este papel)",
                    "settings": {k: v for k, v in st.items() if k not in ("stage", "why")}, "why": st.get("why", "")})
    return out
