"""Motor de mezcla: analiza stems, identifica funciones, propone balance, EQ, dinámica y resuelve conflictos.

Esta versión genera un PLAN de mezcla verificable (con valores concretos y justificación) y una
mezcla de previsualización offline de los stems con las ganancias propuestas. Aplicar los ajustes
dentro de Cubase de forma automática depende del canal de control (ver cubase/), todavía pendiente.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..audio import loudness
from ..audio.dsp import to_db
from ..audio.dynamics import analyze_dynamics
from ..audio.spectral import band_energies, masking_conflicts, stft_power
from ..audio.wavio import read_wav, write_wav

ROLE_PATTERNS = [
    ("kick", r"kick|bombo|bd\b|bassdrum"),
    ("sub", r"\bsub|808"),
    ("bass", r"bass|bajo|reese"),
    ("snare", r"snare|caja|clap|palma"),
    ("hats", r"hat|hh|cymbal|ride|crash|plato|shaker"),
    ("perc", r"perc|tom|conga|bongo|top|loop"),
    ("vocal", r"vox|vocal|voz|voice|chant"),
    ("lead", r"lead|melod|hook|solo"),
    ("pad", r"pad|string|atmo|ambient|choir"),
    ("chords", r"chord|acord|stab|piano|keys|supersaw|saw"),
    ("arp", r"arp|pluck|seq"),
    ("fx", r"fx|riser|sweep|impact|noise|uplifter|downlifter|crash fx"),
]

# Sonoridad orientativa de cada función respecto al kick (LU). Es un punto de partida para
# música electrónica, no un valor fijo: el motor la ajusta con el análisis y el feedback.
ROLE_LEVEL_LU = {"kick": 0.0, "sub": -3.0, "bass": -3.0, "snare": -4.0, "hats": -11.0, "perc": -9.0,
                 "vocal": -2.0, "lead": -4.0, "chords": -7.0, "pad": -11.0, "arp": -9.0, "fx": -13.0, "other": -8.0}
# Por debajo de esta frecuencia el contenido rara vez aporta en ese papel (para proponer paso alto)
ROLE_HPF_HZ = {"hats": 300, "perc": 120, "vocal": 90, "lead": 120, "chords": 120, "pad": 150, "arp": 150,
               "fx": 100, "snare": 80, "other": 60}
GROUPS = {"Batería": ["kick", "snare", "hats", "perc"], "Graves": ["sub", "bass"],
          "Sintes": ["lead", "chords", "pad", "arp"], "Voces": ["vocal"], "FX": ["fx"]}
CONFLICT_PAIRS = [("kick", "bass"), ("kick", "sub"), ("bass", "chords"), ("lead", "pad"), ("vocal", "lead"),
                  ("vocal", "chords"), ("snare", "chords"), ("perc", "arp")]


def guess_role(name: str, meas: dict | None = None) -> str:
    n = name.lower()
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, n):
            return role
    if meas:
        b = meas["bands"]
        if b["subgrave"]["pct"] + b["grave"]["pct"] > 70:
            return "bass"
        if b["presencia"]["pct"] + b["aire"]["pct"] > 50:
            return "hats"
    return "other"


@dataclass
class StemInfo:
    path: str
    name: str
    role: str
    measurements: dict
    gain_db: float = 0.0
    actions: list = field(default_factory=list)


def _cumulative_freq(freqs: np.ndarray, spec: np.ndarray, fraction: float) -> float:
    c = np.cumsum(spec)
    if c[-1] <= 0:
        return 0.0
    return float(freqs[np.searchsorted(c, fraction * c[-1])])


def analyze_stem(path: Path) -> tuple[np.ndarray, int, dict]:
    x, info = read_wav(path)
    fs = info.sample_rate
    freqs, _, power = stft_power(x, fs)
    spec = power.mean(axis=0)
    meas = {
        "loudness": loudness.measure(x, fs, true_peak=False),
        "dynamics": analyze_dynamics(x, fs),
        "bands": band_energies(freqs, power),
        "f_0_5pct_hz": _cumulative_freq(freqs, spec, 0.005),  # por debajo de aquí solo hay el 0,5% de la energía
        "f_2pct_hz": _cumulative_freq(freqs, spec, 0.02),
        "pct_below_hz": {f: float(100 * spec[freqs < f].sum() / (spec.sum() + 1e-30)) for f in (60, 80, 100, 120, 150, 300)},
        "f_50pct_hz": _cumulative_freq(freqs, spec, 0.5),
        "silent": bool(np.max(np.abs(x)) < 1e-5),
    }
    return x, fs, meas


def compression_for(role: str, dyn: dict, bpm: float | None) -> dict | None:
    """Ajustes de compresión según el material; None si no hace falta."""
    crest, dr = dyn["crest_factor_db"], dyn["dynamic_range_db"]
    beat_ms = 60000 / bpm if bpm else 480
    if role in ("kick", "snare"):
        if crest > 18:
            return {"ratio": 4.0, "attack_ms": 15.0, "release_ms": round(beat_ms / 4), "gr_target_db": 3,
                    "why": f"Cresta {crest:.0f} dB: ataque lento (15 ms) deja pasar el golpe y da pegada."}
        return None
    if role in ("bass", "sub"):
        if dr > 6 or crest > 12:
            return {"ratio": 4.0, "attack_ms": 10.0, "release_ms": round(beat_ms / 2), "gr_target_db": 4,
                    "why": f"Rango dinámico {dr:.0f} dB en el bajo: nivel estable para que el grave sea constante."}
        return None
    if role in ("vocal", "lead"):
        if dr > 8:
            return {"ratio": 3.0, "attack_ms": 5.0, "release_ms": 80.0, "gr_target_db": 4,
                    "why": f"Rango dinámico {dr:.0f} dB: controlar la línea principal para que no se pierda."}
        return None
    if role in ("pad", "chords", "arp") and dr > 12:
        return {"ratio": 2.0, "attack_ms": 20.0, "release_ms": 150.0, "gr_target_db": 2,
                "why": "Dinámica amplia en un elemento de relleno: compresión suave."}
    return None


def plan_mix(folder: Path, bpm: float | None = None, feedback: list[str] | None = None) -> dict:
    """Plan de mezcla completo para una carpeta de stems WAV."""
    folder = Path(folder)
    files = sorted(p for p in folder.glob("*.wav"))
    if not files:
        raise FileNotFoundError(f"no hay archivos WAV en {folder}")
    stems: list[StemInfo] = []
    signals = {}
    rates = set()
    warnings = []
    for f in files:
        x, fs, meas = analyze_stem(f)
        rates.add(fs)
        if meas["silent"]:
            warnings.append(f"{f.name}: pista en silencio; se ignora.")
            continue
        role = guess_role(f.stem, meas)
        stems.append(StemInfo(str(f), f.stem, role, meas))
        signals[f.stem] = (x, fs)
    if len(rates) > 1:
        warnings.append(f"Frecuencias de muestreo distintas entre stems ({sorted(rates)}): no se puede previsualizar la "
                        "mezcla sin remuestrear; conviene exportar todos los stems con la misma.")
    # pistas duplicadas: misma forma de onda
    seen = {}
    for s in stems:
        x = signals[s.name][0]
        key = (len(x), round(float(np.sum(x[:: max(1, len(x) // 5000)] ** 2)), 6))
        if key in seen:
            warnings.append(f"{s.name} parece un duplicado de {seen[key]}.")
        else:
            seen[key] = s.name
    fb = " ".join(feedback or []).lower()
    level_bias = {}
    if "bajo" in fb and ("cuerpo" in fb or "poco" in fb or "flojo" in fb):
        level_bias["bass"] = 1.5
        level_bias["sub"] = 1.0
    if "kick" in fb and "demasiad" in fb:
        level_bias["kick"] = -1.5
    # balance: referencia = kick (o el elemento más fuerte si no hay kick)
    ref = next((s for s in stems if s.role == "kick"), None)
    ref_l = ref.measurements["loudness"]["integrated_lufs"] if ref else max(
        (s.measurements["loudness"]["integrated_lufs"] or -70) for s in stems)
    for s in stems:
        cur = s.measurements["loudness"]["integrated_lufs"]
        if cur is None or not math.isfinite(cur):
            continue
        target = ref_l + ROLE_LEVEL_LU.get(s.role, -8.0) + level_bias.get(s.role, 0.0)
        s.gain_db = round(max(-24.0, min(12.0, target - cur)), 1)
        s.actions.append({"type": "level", "gain_db": s.gain_db,
                          "why": f"Función '{s.role}': punto de partida {ROLE_LEVEL_LU.get(s.role, -8):+.0f} LU respecto al kick."})
        # paso alto solo si casi no hay energía útil por debajo del punto propuesto
        hpf = ROLE_HPF_HZ.get(s.role)
        low_pct = s.measurements["bands"]["subgrave"]["pct"] + s.measurements["bands"]["grave"]["pct"]
        if hpf and s.role not in ("kick", "bass", "sub"):
            below = s.measurements["pct_below_hz"].get(hpf, s.measurements["pct_below_hz"][120])
            if 0.001 < below < 3:
                # hay algo de energía grave (ruido, bleed, cola de reverb) pero no es el contenido principal:
                # el filtro se coloca por debajo de donde empieza el 2% de la energía para no tocar el carácter
                freq = round(min(hpf, max(30.0, 0.7 * s.measurements["f_2pct_hz"])))
                s.actions.append({"type": "eq", "band": "highpass", "freq": freq, "slope_db_oct": 12,
                                  "why": f"{below:.2f}% de la energía está por debajo de {hpf} Hz y no forma parte "
                                         "del sonido principal: el filtro limpia sin quitar carácter."})
            elif below <= 0.001:
                s.actions.append({"type": "note", "why": "No hay contenido grave que filtrar: no se añade paso alto."})
            elif low_pct >= 8:
                s.actions.append({"type": "note", "why": f"{low_pct:.0f}% de energía en graves: NO se filtra paso "
                                  "alto automáticamente; puede formar parte del sonido. Revisar en contexto."})
        comp = compression_for(s.role, s.measurements["dynamics"], bpm)
        if comp:
            s.actions.append({"type": "compressor", **comp})
        if s.measurements["dynamics"]["clipping_runs"]:
            s.actions.append({"type": "warning", "why": "El stem llega recortado: exportarlo de nuevo con margen."})
    # conflictos de enmascaramiento
    conflicts = []
    by_role: dict[str, list[StemInfo]] = {}
    for s in stems:
        by_role.setdefault(s.role, []).append(s)
    for ra, rb in CONFLICT_PAIRS:
        for a in by_role.get(ra, []):
            for b in by_role.get(rb, []):
                xa, fa = signals[a.name]
                xb, fb_ = signals[b.name]
                if fa != fb_:
                    continue
                ga, gb = 10 ** (a.gain_db / 20), 10 ** (b.gain_db / 20)
                c = masking_conflicts(xa * ga, xb * gb, fa)
                if not c:
                    continue
                top = c[0]
                sol = _resolve(ra, rb, a, b, top, xa, xb, fa)
                conflicts.append({"a": a.name, "b": b.name, "bands": c[:3], "solution": sol})
                for target, act in sol.get("actions", []):
                    (a if target == "a" else b).actions.append(act)
    groups = {g: [s.name for s in stems if s.role in roles] for g, roles in GROUPS.items()}
    groups = {g: v for g, v in groups.items() if v}
    preview = None
    if len(rates) == 1 and stems:
        fs = rates.pop()
        n = max(len(signals[s.name][0]) for s in stems)
        mix = np.zeros((n, 2))
        for s in stems:
            x = signals[s.name][0]
            x2 = x if x.shape[1] >= 2 else np.repeat(x, 2, axis=1)
            mix[:len(x2)] += x2[:, :2] * 10 ** (s.gain_db / 20)
        pk = float(np.max(np.abs(mix))) or 1.0
        trim = min(0.0, -6.0 - to_db(pk))  # dejar 6 dB de margen para el máster
        if trim:
            for s in stems:
                s.gain_db = round(s.gain_db + trim, 1)
                s.actions[0]["gain_db"] = s.gain_db
        preview = {"mix": mix * 10 ** (trim / 20), "fs": fs, "trim_db": round(trim, 1)}
    return {
        "folder": str(folder),
        "stems": [{"name": s.name, "role": s.role, "gain_db": s.gain_db, "actions": s.actions,
                   "lufs": s.measurements["loudness"]["integrated_lufs"],
                   "crest_db": s.measurements["dynamics"]["crest_factor_db"]} for s in stems],
        "groups": groups, "conflicts": conflicts, "warnings": warnings,
        "bus": {"headroom_target_dbfs": -6.0, "trim_db": preview["trim_db"] if preview else None},
        "_preview": preview,
    }


def _resolve(ra, rb, a: StemInfo, b: StemInfo, band: dict, xa, xb, fs) -> dict:
    """Elige la herramienta menos invasiva para un conflicto concreto."""
    f = band["band_hz"]
    if {ra, rb} <= {"kick", "bass", "sub"}:
        # ¿coinciden en el tiempo? correlación de envolventes graves
        ea = _envelope(xa, fs)
        eb = _envelope(xb, fs)
        n = min(len(ea), len(eb))
        corr = float(np.corrcoef(ea[:n], eb[:n])[0, 1]) if n > 10 and ea[:n].std() > 0 and eb[:n].std() > 0 else 0.0
        bass_t = "b" if ra == "kick" else "a"
        if corr > -0.2:
            return {"tool": "sidechain", "text": f"Kick y bajo coinciden en el tiempo alrededor de {f:.0f} Hz: sidechain "
                    "del bajo desde el kick (ataque ~1 ms, liberación ~1/8 de tiempo, 3-6 dB).",
                    "time_overlap_corr": round(corr, 2),
                    "actions": [(bass_t, {"type": "sidechain", "source": a.name if bass_t == "b" else b.name,
                                          "depth_db": 4, "why": "Separar kick y bajo en el tiempo."})]}
        return {"tool": "eq", "text": f"Kick y bajo no coinciden mucho en el tiempo; un recorte suave de 2 dB en "
                f"{f:.0f} Hz en el bajo basta.", "actions": [(bass_t, {"type": "eq", "band": "peak", "freq": f,
                                                                       "gain_db": -2.0, "q": 1.5,
                                                                       "why": "Espacio para el kick."})]}
    # el elemento con menor prioridad cede
    priority = ["vocal", "lead", "kick", "snare", "bass", "sub", "chords", "arp", "perc", "pad", "hats", "fx", "other"]
    loser = "b" if priority.index(rb if rb in priority else "other") > priority.index(ra if ra in priority else "other") else "a"
    gain = -2.5 if band["fraction_of_time"] > 0.6 else -1.5
    tool = "eq_dinamica" if band["fraction_of_time"] < 0.5 else "eq"
    name = (b if loser == "b" else a).name
    return {"tool": tool, "text": f"{a.name} y {b.name} compiten en {f:.0f} Hz el {band['fraction_of_time'] * 100:.0f}% "
            f"del tiempo; cede {name} ({'EQ dinámica, solo cuando coinciden' if tool == 'eq_dinamica' else 'EQ'} "
            f"{gain} dB).",
            "actions": [(loser, {"type": "eq", "band": "peak", "freq": f, "gain_db": gain, "q": 1.4,
                                 "dynamic": tool == "eq_dinamica", "why": "Resolver enmascaramiento."})]}


def _envelope(x, fs, cutoff_bins: int = 8):
    m = x.mean(axis=1) if x.ndim == 2 else x
    hop = int(0.01 * fs)
    n = len(m) // hop
    return np.sqrt(np.mean(m[:n * hop].reshape(n, hop) ** 2, axis=1))


def write_preview(plan: dict, out_path: Path) -> str | None:
    pv = plan.get("_preview")
    if not pv:
        return None
    write_wav(out_path, pv["mix"], pv["fs"], bits=24)
    return str(out_path)


def plan_to_markdown(plan: dict) -> str:
    lines = [f"# Plan de mezcla — {Path(plan['folder']).name}", ""]
    if plan["warnings"]:
        lines += ["## Avisos"] + [f"- {w}" for w in plan["warnings"]] + [""]
    lines += ["## Pistas", "", "| Pista | Función | Ganancia | Acciones |", "|---|---|---|---|"]
    for s in plan["stems"]:
        acts = "; ".join(_act_text(a) for a in s["actions"][1:]) or "—"
        lines.append(f"| {s['name']} | {s['role']} | {s['gain_db']:+.1f} dB | {acts} |")
    lines += ["", "## Grupos propuestos"] + [f"- **{g}**: {', '.join(v)}" for g, v in plan["groups"].items()]
    if plan["conflicts"]:
        lines += ["", "## Conflictos de frecuencia"] + [f"- {c['solution']['text']}" for c in plan["conflicts"]]
    if plan["bus"]["trim_db"] is not None:
        lines += ["", f"Ajuste global de {plan['bus']['trim_db']:+.1f} dB para dejar 6 dB de margen en el bus."]
    return "\n".join(lines)


def _act_text(a: dict) -> str:
    t = a["type"]
    if t == "eq":
        if a["band"] == "highpass":
            return f"paso alto {a['freq']} Hz"
        return f"{'EQ dinámica' if a.get('dynamic') else 'EQ'} {a['freq']:.0f} Hz {a['gain_db']:+.1f} dB"
    if t == "compressor":
        return f"compresor {a['ratio']}:1, ataque {a['attack_ms']} ms, liberación {a['release_ms']} ms"
    if t == "sidechain":
        return f"sidechain desde {a['source']} ({a['depth_db']} dB)"
    return a.get("why", t)
