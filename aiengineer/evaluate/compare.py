"""Comparación con referencias y evaluación antes/después."""
from __future__ import annotations

import math

import numpy as np

from ..audio.analyzer import analyze_array
from ..audio.wavio import read_wav


def compare_to_reference(mix_path, ref_path) -> dict:
    """Compara mezcla y referencia a sonoridad igualada. Devuelve diferencias y propuestas prudentes."""
    x, ix = read_wav(mix_path)
    r, ir = read_wav(ref_path)
    mx = analyze_array(x, ix.sample_rate, musical=True)["measurements"]
    mr = analyze_array(r, ir.sample_rate, musical=True)["measurements"]
    diffs = {}
    for band in mx["bands"]:
        a, b = mx["bands"][band]["pct"], mr["bands"][band]["pct"]
        diffs[band] = round(10 * math.log10(max(a, 1e-6) / max(b, 1e-6)), 2)  # dB relativos (energía normalizada)
    props = []
    for band, d in diffs.items():
        if abs(d) > 3:
            props.append({"area": "frecuencia", "text": f"'{band}': la mezcla tiene {abs(d):.1f} dB "
                          f"{'más' if d > 0 else 'menos'} energía relativa que la referencia. Revisa qué "
                          f"instrumentos ocupan esa banda antes de ecualizar el bus.", "delta_db": d})
    dl = {}
    for key, sect, name in (("crest_factor_db", "dynamics", "cresta"), ("dynamic_range_db", "dynamics", "rango dinámico"),
                            ("transient_crest_50ms_db", "dynamics", "pegada de transitorios")):
        dl[key] = round(mx[sect][key] - mr[sect][key], 2)
    lx, lr = mx["loudness"], mr["loudness"]
    dl["integrated_lufs"] = round((lx["integrated_lufs"] or -70) - (lr["integrated_lufs"] or -70), 2)
    if lx.get("lra_lu") is not None and lr.get("lra_lu") is not None:
        dl["lra_lu"] = round(lx["lra_lu"] - lr["lra_lu"], 2)
    if dl["transient_crest_50ms_db"] < -2:
        props.append({"area": "dinámica", "text": "Los transitorios de la mezcla son menos marcados que en la "
                      "referencia: revisa compresión/limitación de batería y bus.", "delta_db": dl["transient_crest_50ms_db"]})
    elif dl["transient_crest_50ms_db"] > 3:
        props.append({"area": "dinámica", "text": "La mezcla tiene transitorios más picudos que la referencia: puede "
                      "sonar menos densa; valora compresión de grupo o saturación suave.", "delta_db": dl["transient_crest_50ms_db"]})
    sx, sr = mx["stereo"], mr["stereo"]
    if sx.get("channels", 1) >= 2 and sr.get("channels", 1) >= 2:
        dw = round(sx["side_to_mid_db"] - sr["side_to_mid_db"], 2)
        dl["side_to_mid_db"] = dw
        if abs(dw) > 4:
            props.append({"area": "estéreo", "text": f"La mezcla es {'más' if dw > 0 else 'menos'} ancha que la "
                          f"referencia ({abs(dw):.1f} dB de lateral relativo).", "delta_db": dw})
    if mx.get("dynamics", {}).get("onsets_per_second") and mr.get("dynamics", {}).get("onsets_per_second"):
        ratio = mx["dynamics"]["onsets_per_second"] / max(mr["dynamics"]["onsets_per_second"], 1e-6)
        if ratio < 0.6 or ratio > 1.6:
            props.append({"area": "arreglo", "text": f"Densidad de ataques muy distinta (x{ratio:.2f}): la diferencia "
                          "puede ser de arreglo, no de mezcla.", "ratio": round(ratio, 2)})
    return {
        "mix": str(mix_path), "reference": str(ref_path),
        "note": "Comparación a sonoridad igualada. La referencia orienta; no es una curva a copiar.",
        "band_difference_db": diffs, "differences": dl, "proposals": props,
        "reference_bands_pct": {k: v["pct"] for k, v in mr["bands"].items()},
        "mix_measurements": mx, "reference_measurements": mr,
    }


def evaluate_change(before: dict, after: dict, goal: str = "") -> dict:
    """Compara dos conjuntos de mediciones: qué mejoró, qué empeoró y si cambió el nivel."""
    improved, regressed, neutral = [], [], []

    def get(m, path):
        cur = m
        for p in path:
            if cur is None:
                return None
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    checks = [
        (("dynamics", "clipping_runs"), "clipping", -1),
        (("loudness", "true_peak_dbtp"), "true peak", -1),
        (("dynamics", "crest_factor_db"), "factor de cresta", +1),
        (("stereo", "low_end_correlation_below_150hz"), "correlación de graves", +1),
        (("stereo", "correlation"), "correlación estéreo", +1),
    ]
    for path, label, better in checks:
        a, b = get(before, path), get(after, path)
        if a is None or b is None:
            continue
        d = b - a
        if abs(d) < 0.05:
            neutral.append(label)
        elif d * better > 0:
            improved.append({"metric": label, "before": a, "after": b})
        else:
            regressed.append({"metric": label, "before": a, "after": b})
    la, lb = get(before, ("loudness", "integrated_lufs")), get(after, ("loudness", "integrated_lufs"))
    level_change = round(lb - la, 2) if la is not None and lb is not None else None
    notes = []
    if level_change is not None and abs(level_change) > 1:
        notes.append(f"El nivel cambió {level_change:+.1f} LU: compara a volumen igualado antes de juzgar.")
    return {"goal": goal, "improved": improved, "regressed": regressed, "unchanged": neutral,
            "level_change_lu": level_change, "notes": notes}
