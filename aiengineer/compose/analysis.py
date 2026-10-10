"""Análisis musical de archivos MIDI: tonalidad, acordes, papel de cada pista y calidad melódica."""
from __future__ import annotations

from collections import Counter

import numpy as np

from ..midi.smf import Song, Track
from ..theory.core import CHORD_QUALITIES, NOTE_NAMES, Chord, Scale
from .melody import MelNote, melody_metrics

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
TEMPLATE_QUALITIES = ["maj", "min", "dim", "sus4", "sus2", "7", "min7", "maj7"]

ROLE_HINTS = {
    "drums": ["drum", "bater", "kick", "bombo", "perc", "hat", "clap", "snare"],
    "bass": ["bass", "bajo", "sub", "808"],
    "lead": ["lead", "melod", "solo", "hook", "vocal", "voz"],
    "pad": ["pad", "string", "cuerda", "atmo"],
    "chords": ["chord", "acord", "piano", "keys", "stab", "supersaw"],
    "arp": ["arp", "seq", "secuen"],
    "pluck": ["pluck"],
    "counter": ["counter", "contra"],
}


def is_drum(track: Track) -> bool:
    return track.channel == 9


def pitch_class_histogram(song: Song, tracks: list[Track] | None = None) -> np.ndarray:
    h = np.zeros(12)
    for t in tracks if tracks is not None else song.tracks:
        if is_drum(t):
            continue
        for n in t.notes:
            h[n.pitch % 12] += n.duration
    return h


def _bass_histogram(song: Song, phrase_starts_only: bool = False) -> np.ndarray:
    """Peso de cada clase de altura en el registro grave (nota más grave de cada compás).
    Con phrase_starts_only se cuentan solo los compases 1, 5, 9... (inicio de frase), donde en
    la música tonal suele situarse la tónica."""
    tpb = song.ticks_per_bar()
    marker_ticks = {t for t, _ in song.markers}
    lowest: dict[int, tuple[int, int]] = {}
    for t in song.tracks:
        if is_drum(t):
            continue
        for n in t.notes:
            b = n.start // tpb
            if b not in lowest or n.pitch < lowest[b][0]:
                lowest[b] = (n.pitch, n.duration)
    h = np.zeros(12)
    for b, (p, d) in lowest.items():
        if phrase_starts_only and b % 8 and b * tpb not in marker_ticks:
            continue
        h[p % 12] += 1
    return h


def detect_key(song: Song, tracks: list[Track] | None = None) -> dict:
    """Krumhansl-Schmuckler sobre el histograma de duraciones, con desempate entre relativos
    (p.ej. Fa# menor / La mayor) usando la nota del bajo y el primer/último compás."""
    h = pitch_class_histogram(song, tracks)
    if h.sum() == 0:
        return {"key": None}
    scores = []
    for tonic in range(12):
        for mode, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            scores.append((float(np.corrcoef(h, np.roll(prof, tonic))[0, 1]), tonic, mode))
    scores.sort(reverse=True)
    r, tonic, mode = scores[0]
    rel_tonic, rel_mode = ((tonic + 9) % 12, "minor") if mode == "major" else ((tonic + 3) % 12, "major")
    rel_r = next(s[0] for s in scores if s[1] == rel_tonic and s[2] == rel_mode)
    if r - rel_r < 0.15:
        starts = _bass_histogram(song, phrase_starts_only=True)
        bass = _bass_histogram(song)
        ev = lambda pc: 2 * starts[pc] / max(1, starts.sum()) + bass[pc] / max(1, bass.sum())
        if ev(rel_tonic) > ev(tonic) * 1.2:
            tonic, mode, r = rel_tonic, rel_mode, rel_r
    second = max(s[0] for s in scores if (s[1], s[2]) != (tonic, mode))
    alt_t, alt_m = ((tonic + 9) % 12, "minor") if mode == "major" else ((tonic + 3) % 12, "major")
    return {"key": f"{NOTE_NAMES[tonic]} {mode}", "tonic": tonic, "mode": mode, "correlation": round(r, 3),
            "margin": round(r - second, 3), "relative_alternative": f"{NOTE_NAMES[alt_t]} {alt_m}"}


def identify_role(track: Track) -> str:
    name = (track.name or "").lower()
    for role, words in ROLE_HINTS.items():
        if any(w in name for w in words):
            return role
    if is_drum(track):
        return "drums"
    if not track.notes:
        return "unknown"
    pitches = [n.pitch for n in track.notes]
    mean = sum(pitches) / len(pitches)
    starts = Counter(n.start for n in track.notes)
    poly = sum(starts.values()) / len(starts)
    avg_dur = sum(n.duration for n in track.notes) / len(track.notes)
    if mean < 48:
        return "bass"
    if poly >= 2.5:
        return "pad" if avg_dur > 1500 else "chords"
    durs = Counter(n.duration for n in track.notes)
    if durs.most_common(1)[0][1] / len(track.notes) > 0.8 and avg_dur <= 140:
        return "arp"
    return "lead"


def chord_from_pcs(weights: np.ndarray, bass_pc: int | None = None,
                   scale: Scale | None = None) -> tuple[Chord | None, float]:
    """Plantillas de acordes ponderadas por duración. Devuelve (acorde, puntuación 0-1).
    Si solo suenan una o dos notas (p.ej. un bajo solo) y se conoce la escala, se asume el acorde
    diatónico sobre esa fundamental, con confianza reducida."""
    if weights.sum() == 0:
        return None, 0.0
    w = weights / weights.sum()
    if scale is not None and int((w > 0.05).sum()) <= 2:
        root = bass_pc if bass_pc is not None else int(np.argmax(w))
        if scale.contains(root):
            from ..theory.core import diatonic_chord
            return diatonic_chord(scale, scale.degree_of(root)), 0.4
    best, best_s = None, -1.0
    for root in range(12):
        for q in TEMPLATE_QUALITIES:
            pcs = {(root + i) % 12 for i in CHORD_QUALITIES[q]}
            inside = sum(w[p] for p in pcs)
            s = inside - 0.1 * len(pcs) / 3  # penalizar plantillas grandes que siempre "cubren" más
            if root == bass_pc:
                s += 0.08
            if w[root] < 0.05:
                s -= 0.2
            if s > best_s:
                best, best_s = Chord(root, q), s
    if best is not None and bass_pc is not None and bass_pc != best.root and bass_pc in best.pcs:
        best.bass = bass_pc
    return best, max(0.0, min(1.0, best_s))


def chords_per_bar(song: Song, harmony_tracks: list[Track] | None = None, split: int = 1,
                   scale: Scale | None = None) -> list[dict]:
    """Acorde estimado por compás (o por fracción de compás con split=2)."""
    tpb = song.ticks_per_bar()
    seg = tpb // split
    n_seg = max(1, (song.length_ticks() + seg - 1) // seg)
    tracks = harmony_tracks or [t for t in song.tracks if not is_drum(t)]
    out = []
    for i in range(n_seg):
        a, b = i * seg, (i + 1) * seg
        w = np.zeros(12)
        lowest = None
        for t in tracks:
            for n in t.notes:
                ov = min(b, n.end) - max(a, n.start)
                if ov > 0:
                    w[n.pitch % 12] += ov
                    if lowest is None or n.pitch < lowest:
                        lowest = n.pitch
        chord, score = chord_from_pcs(w, lowest % 12 if lowest is not None else None, scale)
        out.append({"segment": i, "start": a, "chord": chord.name() if chord else None,
                    "chord_obj": chord, "confidence": round(score, 2)})
    return out


def to_melnotes(track: Track, ppq: int, offset_ticks: int = 0) -> list[MelNote]:
    step = ppq // 4
    out = []
    for n in sorted(track.notes, key=lambda n: (n.start, -n.pitch)):
        if out and out[-1].pos == (n.start - offset_ticks) // step:
            continue  # nos quedamos con la voz superior en caso de notas simultáneas
        out.append(MelNote((n.start - offset_ticks) // step, max(1, n.duration // step), n.pitch, n.velocity))
    return out


def bar_signatures(track: Track, tpb: int) -> list[tuple]:
    bars: dict[int, list] = {}
    for n in track.notes:
        bars.setdefault(n.start // tpb, []).append((n.start % tpb, n.duration, n.pitch))
    total = max(bars) + 1 if bars else 0
    return [tuple(sorted(bars.get(i, []))) for i in range(total)]


def analyze_song(song: Song) -> dict:
    tpb = song.ticks_per_bar()
    roles = {t.name or f"pista {i + 1}": identify_role(t) for i, t in enumerate(song.tracks)}
    melodic = [t for t in song.tracks if not is_drum(t)]
    key = detect_key(song, melodic)
    scale = Scale(key["tonic"], key["mode"]) if key.get("key") else None
    harmony = [t for t in melodic if identify_role(t) in ("chords", "pad", "bass")] or melodic
    chords = chords_per_bar(song, harmony, scale=scale)
    tracks = []
    problems = []
    for t in song.tracks:
        role = identify_role(t)
        entry = {"name": t.name, "role": role, "notes": len(t.notes), "channel": t.channel + 1}
        sigs = bar_signatures(t, tpb)
        non_empty = [s for s in sigs if s]
        if non_empty:
            c = Counter(non_empty)
            entry["distinct_bars"] = len(c)
            entry["most_repeated_bar_count"] = c.most_common(1)[0][1]
            entry["repetition_ratio"] = round(1 - len(c) / len(non_empty), 2)
        if role in ("lead", "counter") and scale is not None and t.notes:
            mn = to_melnotes(t, song.ppq)
            chord_list = [c["chord_obj"] for c in chords]

            def chord_at(pos, cl=chord_list):
                idx = min(len(cl) - 1, pos * (song.ppq // 4) // tpb)
                return cl[idx] or Chord(scale.tonic, "maj" if scale.mode == "major" else "min")

            m = melody_metrics(mn, scale, chord_at)
            entry["melody"] = m
            if m["longest_scale_run"] >= 4:
                problems.append({"track": t.name, "kind": "artístico",
                                 "text": f"La melodía contiene recorridos escalares largos ({m['longest_scale_run'] + 1} "
                                         "notas seguidas por grados conjuntos en la misma dirección): suena lineal."})
            if m["distinct_intervals"] <= 3 and m["notes"] > 16:
                problems.append({"track": t.name, "kind": "artístico",
                                 "text": "Poca variedad interválica: la melodía puede resultar monótona."})
            if m["rest_ratio"] < 0.05 and m["notes"] > 16:
                problems.append({"track": t.name, "kind": "artístico",
                                 "text": "Casi no hay silencios: la melodía no 'respira' entre frases."})
            if entry.get("repetition_ratio", 0) > 0.85 and len(non_empty) > 16:
                problems.append({"track": t.name, "kind": "artístico",
                                 "text": "El mismo compás se repite casi sin variación durante todo el tema."})
        tracks.append(entry)
    density = []
    n_bars = max(1, (song.length_ticks() + tpb - 1) // tpb)
    for b in range(n_bars):
        density.append(sum(1 for t in song.tracks for n in t.notes if b * tpb <= n.start < (b + 1) * tpb))
    if len(density) >= 16 and np.std(density) < 0.1 * (np.mean(density) + 1e-9):
        problems.append({"track": None, "kind": "artístico",
                         "text": "La densidad de notas apenas cambia: falta contraste entre secciones."})
    return {
        "bpm": song.bpm, "ppq": song.ppq, "bars": n_bars,
        "time_signature": f"{song.time_signatures[0][1]}/{song.time_signatures[0][2]}",
        "key": key, "markers": [(t // tpb + 1, name) for t, name in song.markers],
        "roles": roles, "chords": [{"bar": c["segment"] + 1, "chord": c["chord"], "confidence": c["confidence"]}
                                   for c in chords],
        "tracks": tracks, "density_per_bar": density, "problems": problems,
    }
