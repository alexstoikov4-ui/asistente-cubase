"""Compositor de melodías basado en motivos.

Idea general: se generan muchos motivos candidatos (ritmo + contorno), se puntúan con criterios
de calidad melódica (notas del acorde en tiempos fuertes, resolución de saltos, evitar escalas
mecánicas, variedad interválica, silencios, síncopa moderada) y se elige el mejor. Con ese motivo
se construyen frases de pregunta y respuesta, secuencias sobre la armonía, una variación de
clímax y una cadencia final. Todo es determinista dada la semilla.

Unidad de tiempo interna: semicorcheas (16 por compás de 4/4).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace

from ..theory.core import Chord, Scale

STEPS_PER_BAR = 16


@dataclass(frozen=True)
class MelNote:
    pos: int  # semicorcheas desde el inicio de la frase
    dur: int
    pitch: int
    vel: int = 100


def metric_weight(pos: int) -> int:
    p = pos % STEPS_PER_BAR
    if p == 0:
        return 4
    if p == 8:
        return 3
    if p % 4 == 0:
        return 2
    if p % 2 == 0:
        return 1
    return 0


# ---------------------------------------------------------------- ritmo

def gen_rhythm(rng: random.Random, density: float = 0.5, syncopation: float = 0.4,
               gate: float = 0.85) -> list[tuple[int, int]]:
    """Ritmo de un compás: lista de (posición, duración) en semicorcheas."""
    for _ in range(50):
        onsets = []
        start = 0 if rng.random() < 0.7 else rng.choice([2, 3])
        for p in range(start, STEPS_PER_BAR):
            w = metric_weight(p)
            prob = {4: 0.85, 3: 0.55, 2: 0.45, 1: 0.35 + 0.4 * syncopation, 0: 0.08 + 0.25 * syncopation}[w]
            prob *= 0.5 + density
            if p == start or rng.random() < prob:
                onsets.append(p)
        # evitar demasiadas o muy pocas notas
        target_lo, target_hi = 3, 4 + int(round(5 * density))
        if not target_lo <= len(onsets) <= target_hi:
            continue
        rhythm = []
        for i, p in enumerate(onsets):
            nxt = onsets[i + 1] if i + 1 < len(onsets) else STEPS_PER_BAR
            gap = nxt - p
            if gap >= 4 and rng.random() < 0.45:
                dur = rng.choice([2, 3]) if gap > 3 else gap  # silencio intencionado
            else:
                dur = max(1, int(round(gap * gate))) if gap > 1 else 1
            rhythm.append((p, dur))
        return rhythm
    return [(0, 3), (4, 2), (6, 4), (12, 3)]


def rhythm_score(rhythm: list[tuple[int, int]], syncopation: float) -> float:
    onsets = {p for p, _ in rhythm}
    sync = sum(1 for p in onsets if metric_weight(p) <= 1 and (p - (p % 4)) not in onsets)
    rests = 0
    for i, (p, d) in enumerate(rhythm):
        nxt = rhythm[i + 1][0] if i + 1 < len(rhythm) else STEPS_PER_BAR
        if nxt - (p + d) >= 2:
            rests += 1
    durs = {d for _, d in rhythm}
    s = 0.0
    s -= abs(sync - syncopation * 3) * 0.6
    s += 0.8 if rests >= 1 else -0.8
    s += 0.5 * min(len(durs), 3)
    return s


# ---------------------------------------------------------------- calidad melódica

def _chord_tone(pitch: int, chord: Chord) -> bool:
    return pitch % 12 in chord.pcs


def melody_metrics(notes: list[MelNote], scale: Scale, chord_at=None) -> dict:
    """Métricas objetivas de una línea melódica. También se usan para analizar MIDI existente."""
    if not notes:
        return {"notes": 0}
    ns = sorted(notes, key=lambda n: n.pos)
    pitches = [n.pitch for n in ns]
    ints = [b - a for a, b in zip(pitches, pitches[1:])]
    # recorrido escalar en la misma dirección (escala mecánica)
    longest_run = run = 0
    last_dir = 0
    for iv in ints:
        d = (iv > 0) - (iv < 0)
        if d != 0 and 1 <= abs(iv) <= 2 and d == last_dir:
            run += 1
        elif d != 0 and 1 <= abs(iv) <= 2:
            run = 1
        else:
            run = 0
        last_dir = d if 1 <= abs(iv) <= 2 else 0
        longest_run = max(longest_run, run)
    rep = longest_rep = 0
    for iv in ints:
        rep = rep + 1 if iv == 0 else 0
        longest_rep = max(longest_rep, rep)
    leaps = [i for i, iv in enumerate(ints) if abs(iv) >= 5]
    recovered = sum(1 for i in leaps if i + 1 < len(ints) and ints[i + 1] * ints[i] < 0 and abs(ints[i + 1]) <= 4)
    same_dir_thirds = 0
    for a, b in zip(ints, ints[1:]):
        if 3 <= abs(a) <= 4 and 3 <= abs(b) <= 4 and a * b > 0:
            same_dir_thirds += 1
    strong = [n for n in ns if metric_weight(n.pos) >= 3]
    ct_ratio = None
    if chord_at is not None and strong:
        ct_ratio = sum(1 for n in strong if _chord_tone(n.pitch, chord_at(n.pos))) / len(strong)
    in_scale = sum(1 for p in pitches if scale.contains(p)) / len(pitches)
    total = max(n.pos + n.dur for n in ns) - ns[0].pos
    sounding = sum(n.dur for n in ns)
    return {
        "notes": len(ns),
        "range": max(pitches) - min(pitches),
        "distinct_intervals": len(set(ints)),
        "longest_scale_run": longest_run,
        "longest_repeated_pitch": longest_rep,
        "leaps": len(leaps),
        "leap_recovery": (recovered / len(leaps)) if leaps else 1.0,
        "arpeggio_chains": same_dir_thirds,
        "strong_beat_chord_tone_ratio": ct_ratio,
        "in_scale_ratio": in_scale,
        "rest_ratio": 1 - sounding / total if total > 0 else 0.0,
        "big_leaps": sum(1 for iv in ints if abs(iv) > 12),
        "tritones": sum(1 for iv in ints if abs(iv) == 6),
    }


def melody_score(notes: list[MelNote], scale: Scale, chord_at, ideal_range=(5, 12)) -> float:
    m = melody_metrics(notes, scale, chord_at)
    if m["notes"] < 2:
        return -10.0
    s = 0.0
    s += 4.0 * (m["strong_beat_chord_tone_ratio"] or 0)
    s -= 1.5 * max(0, m["longest_scale_run"] - 2)  # 4+ notas por grados conjuntos en la misma dirección
    s -= 1.0 * max(0, m["longest_repeated_pitch"] - 1)
    lo, hi = ideal_range
    if m["range"] < lo:
        s -= (lo - m["range"]) * 0.4
    elif m["range"] > hi:
        s -= (m["range"] - hi) * 0.6
    s += 1.5 * m["leap_recovery"]
    s += 0.4 * min(m["distinct_intervals"], 5)
    s -= 1.2 * m["arpeggio_chains"]
    s -= 3.0 * m["big_leaps"]
    s -= 1.0 * m["tritones"]
    s -= 4.0 * (1 - m["in_scale_ratio"])
    return s


# ---------------------------------------------------------------- generación de motivos

CONTOUR_BIAS = {
    "arch": lambda f: 0.6 if f < 0.5 else -0.6,
    "ascending": lambda f: 0.4,
    "descending": lambda f: -0.4,
    "low": lambda f: -0.2 if f < 0.5 else 0.2,
    "valley": lambda f: -0.6 if f < 0.5 else 0.6,
}


def nearest_chord_tone(pitch: int, chord: Chord, direction: int = 0) -> int:
    best = None
    for d in range(0, 7):
        for c in ((pitch + d, pitch - d) if direction == 0 else ((pitch + d,) if direction > 0 else (pitch - d,))):
            if c % 12 in chord.pcs:
                best = c
                break
        if best is not None:
            return best
    return pitch


def gen_pitches(rng: random.Random, rhythm, scale: Scale, chord: Chord, start: int, contour: str,
                leap_prob: float) -> list[int]:
    bias = CONTOUR_BIAS.get(contour, CONTOUR_BIAS["arch"])
    pitches = []
    cur = nearest_chord_tone(start, chord)
    last_leap = 0
    for i, (pos, _) in enumerate(rhythm):
        if i == 0:
            pitches.append(cur)
            continue
        f = pos / STEPS_PER_BAR
        if last_leap:
            steps = -last_leap // abs(last_leap) * rng.choice([1, 1, 2])  # recuperar el salto
            last_leap = 0
        else:
            r = rng.random()
            if r < 0.12:
                steps = 0
            elif r < 0.12 + leap_prob * 0.5:
                steps = rng.choice([3, 4, 5]) * (1 if rng.random() < 0.5 + bias(f) * 0.5 else -1)
                last_leap = steps
            else:
                mag = 1 if rng.random() < 0.7 else 2
                steps = mag * (1 if rng.random() < 0.5 + bias(f) * 0.5 else -1)
        nxt = scale.step(cur, steps)
        if metric_weight(pos) >= 2 or rhythm[i][1] >= 4:
            nxt = nearest_chord_tone(nxt, chord)
        cur = nxt
        pitches.append(cur)
    return pitches


def generate_motif(rng: random.Random, scale: Scale, chord: Chord, center: int, params: dict,
                   candidates: int = 80) -> list[MelNote]:
    """Genera el motivo de un compás eligiendo el mejor de muchos candidatos."""
    best, best_s = None, -1e9
    density = params.get("density", 0.5)
    sync = params.get("syncopation", 0.4)
    gate = params.get("gate", 0.85)
    contour = params.get("contour", "arch")
    leap = params.get("leap_prob", 0.25)
    for _ in range(candidates):
        rhythm = gen_rhythm(rng, density, sync, gate)
        start = center + rng.choice([-3, -1, 0, 2, 4])
        pitches = gen_pitches(rng, rhythm, scale, chord, start, contour, leap)
        notes = [MelNote(p, d, pitch) for (p, d), pitch in zip(rhythm, pitches)]
        # un motivo de un compás debe ser compacto (ámbito ideal de 4 a 9 semitonos)
        s = melody_score(notes, scale, lambda _pos: chord, ideal_range=(4, 9)) + rhythm_score(rhythm, sync)
        # identidad: favorecer motivos con una nota claramente más larga (ancla memorable)
        durs = sorted(d for _, d in rhythm)
        if len(durs) >= 2 and durs[-1] >= 2 * durs[0]:
            s += 0.6
        if s > best_s:
            best, best_s = notes, s
    return best


# ---------------------------------------------------------------- variaciones

def shift(notes, steps_pos: int = 0, semis: int = 0):
    return [replace(n, pos=n.pos + steps_pos, pitch=n.pitch + semis) for n in notes]


def abs_degree(scale: Scale, pitch: int) -> int:
    """Número de grado absoluto (7 por octava) de una altura ajustada a la escala."""
    p = scale.nearest(pitch)
    octv, pc = divmod(p - scale.tonic, 12)
    return 7 * octv + scale.intervals.index(pc)


def fit_to_chord(notes: list[MelNote], scale: Scale, chord: Chord, anchor: int | None = None,
                 center: int | None = None) -> list[MelNote]:
    """Traslada el motivo por grados de la escala para que encaje con un nuevo acorde (secuencia),
    conservando su contorno; las notas fuertes se ajustan a notas del acorde.

    anchor: altura cerca de la que debe empezar (continuidad con lo anterior).
    center: registro de referencia, evita que las secuencias deriven hacia el grave o el agudo.
    """
    if not notes:
        return []
    first = notes[0].pitch
    anchor = first if anchor is None else anchor
    center = first if center is None else center
    mean = sum(n.pitch for n in notes) / len(notes)
    best = None
    for c in range(anchor - 9, anchor + 10):
        if c % 12 not in chord.pcs or not scale.contains(c):
            continue
        steps = abs_degree(scale, c) - abs_degree(scale, first)
        cost = abs(c - anchor) + 0.6 * abs(mean + (c - first) - center)
        if best is None or cost < best[0]:
            best = (cost, steps)
    steps = best[1] if best else 0
    out = []
    for n in notes:
        p = scale.step(n.pitch, steps)
        if metric_weight(n.pos) >= 3 or n.dur >= 4:
            p = nearest_chord_tone(p, chord)
        out.append(replace(n, pitch=p))
    return out


def fit_register(bar: list[MelNote], low: int, high: int, prev: int | None = None,
                 tolerance: int = 4) -> list[MelNote]:
    """Desplaza el compás entero por octavas (no nota a nota) para respetar el registro,
    tolerando pequeños excesos si así se evita un salto brusco desde la nota anterior."""
    if not bar:
        return bar
    lo, hi = min(n.pitch for n in bar), max(n.pitch for n in bar)

    def cost(sh):
        over = max(0, low - tolerance - (lo + sh)) + max(0, (hi + sh) - high - tolerance)
        soft = max(0, low - (lo + sh)) + max(0, (hi + sh) - high)
        jump = abs(bar[0].pitch + sh - prev) if prev is not None else 0
        return over * 10 + soft + jump / 4 + (30 if jump > 12 else 0) + abs(sh) / 24

    best = min((-24, -12, 0, 12, 24), key=cost)
    return [replace(n, pitch=n.pitch + best) for n in bar]


def vary_ending(rng: random.Random, notes: list[MelNote], scale: Scale, chord: Chord, final_pc: int | None = None,
                long_final: bool = True) -> list[MelNote]:
    """Cambia el ritmo y las notas de la segunda mitad del compás (respuesta / final de frase)."""
    head = [n for n in notes if n.pos % STEPS_PER_BAR < 8]
    base = (notes[0].pos // STEPS_PER_BAR) * STEPS_PER_BAR if notes else 0
    last = head[-1].pitch if head else (notes[0].pitch if notes else 72)
    tail = []
    options = [[(8, 2), (10, 6)], [(8, 3), (11, 5)], [(10, 6)], [(8, 1), (9, 2), (12, 4)], [(8, 4), (12, 4)]]
    pattern = rng.choice(options if long_final else options[3:] + [[(8, 2), (10, 2), (12, 2), (14, 2)]])
    cur = last
    for i, (p, d) in enumerate(pattern):
        if i == len(pattern) - 1 and final_pc is not None:
            cands = [c for c in range(cur - 7, cur + 8) if c % 12 == final_pc]
            cur = min(cands, key=lambda c: abs(c - cur)) if cands else cur
        else:
            cur = scale.step(cur, rng.choice([-2, -1, 1, 2]))
            if metric_weight(p) >= 2:
                cur = nearest_chord_tone(cur, chord)
        tail.append(MelNote(base + p, d, cur))
    return head + tail


def augment(notes: list[MelNote], factor: int = 2) -> list[MelNote]:
    if not notes:
        return []
    base = notes[0].pos
    return [replace(n, pos=base + (n.pos - base) * factor, dur=n.dur * factor) for n in notes]


def fragment(notes: list[MelNote], length: int = 8) -> list[MelNote]:
    base = notes[0].pos if notes else 0
    return [n for n in notes if n.pos - base < length]


def add_approach(rng: random.Random, notes: list[MelNote], scale: Scale) -> list[MelNote]:
    """Añade una nota de aproximación (diatónica o cromática) antes de una nota fuerte si hay hueco."""
    out = list(notes)
    for i in range(1, len(notes)):
        prev, n = notes[i - 1], notes[i]
        if metric_weight(n.pos) >= 3 and n.pos - (prev.pos + prev.dur) >= 1 and rng.random() < 0.5:
            appr = n.pitch - 1 if rng.random() < 0.5 else scale.step(n.pitch, 1)
            out.append(MelNote(n.pos - 1, 1, appr, max(1, n.vel - 15)))
            break
    return sorted(out, key=lambda x: x.pos)


# ---------------------------------------------------------------- frases

def compose_phrase(rng: random.Random, scale: Scale, chords: list[Chord], motif: list[MelNote], params: dict,
                   climax: bool = False, final: bool = True) -> list[MelNote]:
    """Frase de 4 u 8 compases a partir del motivo (len(chords) = número de compases)."""
    bars = len(chords)
    tonic_pc = scale.tonic
    out: list[MelNote] = []
    # respuesta: mismo arranque rítmico, contorno contrario y otro final
    response = vary_ending(rng, motif, scale, chords[1 % bars], long_final=False)
    response = [replace(n, pitch=scale.step(n.pitch, -1)) if n.pos >= 8 else n for n in response]
    if bars >= 8:
        plan = ["M", "R", "S", "Q", "M", "R2", "C", "A"]
    elif bars >= 4:
        plan = ["M", "R", "S", "A"]
    else:
        plan = ["M", "A"][:bars] if bars > 1 else ["A"]
    plan = (plan + ["S"] * bars)[:bars]
    prev_pitch = motif[0].pitch
    center = motif[0].pitch
    low, high = params.get("low", 60), params.get("high", 88)
    for b, kind in enumerate(plan):
        chord = chords[b]
        if kind == "M":
            bar = fit_to_chord(motif, scale, chord, anchor=motif[0].pitch, center=center)
        elif kind == "R":
            bar = fit_to_chord(response, scale, chord, anchor=prev_pitch, center=center)
        elif kind == "R2":
            bar = add_approach(rng, fit_to_chord(response, scale, chord, anchor=prev_pitch, center=center), scale)
        elif kind == "S":
            bar = fit_to_chord(motif, scale, chord, anchor=prev_pitch, center=center)
        elif kind == "Q":
            q_pc = scale.pcs[4] if scale.pcs[4] in chord.pcs else chord.pcs[1]
            bar = vary_ending(rng, fit_to_chord(motif, scale, chord, anchor=prev_pitch, center=center), scale, chord, q_pc)
        elif kind == "C":
            bar = fit_to_chord(motif, scale, chord, anchor=motif[0].pitch + (4 if climax else 2),
                               center=center + (4 if climax else 2))
            bar = fit_register(bar, low, high, prev=prev_pitch)
            # el clímax contiene la nota más aguda de la frase, sin salir del registro permitido
            phrase_top = max([n.pitch for n in out] + [n.pitch for n in bar])
            if max(n.pitch for n in bar) < phrase_top - 2 and max(n.pitch for n in bar) + 12 <= high + 3:
                bar = [replace(n, pitch=n.pitch + 12) for n in bar]  # subir el compás entero una octava
            ceiling = min(phrase_top + (5 if climax else 2), high + 2)
            # la cima va en una nota interior (no la primera), sin saltos de más de una sexta con sus vecinas
            cands = range(1, len(bar)) if len(bar) > 1 else range(len(bar))
            peak_i = max(cands, key=lambda i: (metric_weight(bar[i].pos), bar[i].dur))
            neigh = [bar[j].pitch for j in (peak_i - 1, peak_i + 1) if 0 <= j < len(bar)]
            ceiling = min([ceiling] + [p + 9 for p in neigh])
            peak = nearest_chord_tone(ceiling, chord, -1)
            if peak > max(n.pitch for n in bar):
                bar[peak_i] = replace(bar[peak_i], pitch=peak)
        else:  # "A": respuesta final que resuelve
            end_pc = tonic_pc if (final and tonic_pc in chord.pcs) else (chord.pcs[0] if final else chord.pcs[1])
            bar = vary_ending(rng, fit_to_chord(motif, scale, chord, anchor=prev_pitch, center=center), scale, chord, end_pc)
        bar = shift(bar, steps_pos=b * STEPS_PER_BAR - (bar[0].pos // STEPS_PER_BAR) * STEPS_PER_BAR)
        if kind != "C":
            # en frases con clímax se reserva la parte alta del registro para el compás culminante
            bar = fit_register(bar, low, high - 4 if climax else high, prev=prev_pitch if b else None)
        out.extend(bar)
        prev_pitch = bar[-1].pitch
    out = smooth_leaps(_tidy(out, low - 5, high + 5))
    if climax and "C" in plan:
        out = _ensure_climax_peak(out, plan.index("C"), chords[plan.index("C")], high + 3)
    return out


def _ensure_climax_peak(notes: list[MelNote], bar: int, chord: Chord, ceiling: int) -> list[MelNote]:
    """Garantiza que el compás de clímax contenga la nota más aguda de la frase (o empate),
    sin crear saltos de más de una octava con las notas vecinas."""
    idx = [i for i, n in enumerate(notes) if n.pos // STEPS_PER_BAR == bar]
    others = [n.pitch for i, n in enumerate(notes) if i not in idx]
    if not idx or not others:
        return notes
    top_other = max(others)
    if max(notes[i].pitch for i in idx) >= top_other:
        return notes
    target = nearest_chord_tone(top_other, chord, 1)
    if target > ceiling:
        target = nearest_chord_tone(top_other, chord, -1)
    for i in sorted(idx, key=lambda i: (-metric_weight(notes[i].pos), -notes[i].dur)):
        neigh = [notes[j].pitch for j in (i - 1, i + 1) if 0 <= j < len(notes)]
        if all(abs(target - p) <= 12 for p in neigh):
            notes = list(notes)
            notes[i] = replace(notes[i], pitch=target)
            break
    return notes


def smooth_leaps(notes: list[MelNote], max_leap: int = 12) -> list[MelNote]:
    """Corrige saltos mayores que max_leap moviendo de octava la nota de llegada (hacia la anterior)
    cuando así no se crea otro salto excesivo con la siguiente. Mantiene la clase de altura."""
    out = list(notes)
    for i in range(1, len(out)):
        d = out[i].pitch - out[i - 1].pitch
        if abs(d) <= max_leap:
            continue
        cand = out[i].pitch - 12 * (1 if d > 0 else -1) * ((abs(d) - 1) // 12)
        nxt = out[i + 1].pitch if i + 1 < len(out) else None
        if nxt is None or abs(nxt - cand) <= max_leap:
            out[i] = replace(out[i], pitch=cand)
    return out


def _tidy(notes: list[MelNote], low: int, high: int) -> list[MelNote]:
    """Ordena, recorta solapamientos y lleva al registro por octavas sin romper el contorno."""
    ns = sorted(notes, key=lambda n: (n.pos, n.pitch))
    out = []
    for i, n in enumerate(ns):
        p = n.pitch
        while p < low:
            p += 12
        while p > high:
            p -= 12
        nxt = ns[i + 1].pos if i + 1 < len(ns) else None
        dur = n.dur if nxt is None else max(1, min(n.dur, nxt - n.pos))
        if out and out[-1].pos == n.pos:
            continue  # una sola nota por posición en una línea melódica
        out.append(MelNote(n.pos, dur, p, n.vel))
    return out


def counter_melody(rng: random.Random, lead: list[MelNote], scale: Scale, chords: list[Chord],
                   low: int = 55, high: int = 74) -> list[MelNote]:
    """Línea de notas guía (3ª/7ª del acorde) que se mueve por grados y entra donde la melodía descansa."""
    out = []
    prev = None
    for b, chord in enumerate(chords):
        bar_start = b * STEPS_PER_BAR
        busy = [False] * STEPS_PER_BAR
        for n in lead:
            for s in range(max(n.pos, bar_start), min(n.pos + n.dur, bar_start + STEPS_PER_BAR)):
                busy[s - bar_start] = True
        guide = [chord.pcs[1]] + ([chord.pcs[3]] if len(chord.pcs) > 3 else [chord.pcs[2]])
        cands = [p for p in range(low, high + 1) if p % 12 in guide]
        pitch = min(cands, key=lambda p: abs(p - prev)) if prev is not None else cands[len(cands) // 2]
        # buscar el hueco más largo de la melodía en el compás
        best_start, best_len, cur_start, cur_len = 0, 0, None, 0
        for s in range(STEPS_PER_BAR):
            if not busy[s]:
                cur_start = s if cur_start is None else cur_start
                cur_len += 1
                if cur_len > best_len:
                    best_start, best_len = cur_start, cur_len
            else:
                cur_start, cur_len = None, 0
        if best_len >= 3:
            out.append(MelNote(bar_start + best_start, best_len, pitch, 80))
        else:
            out.append(MelNote(bar_start + 4, 8, pitch, 70))  # nota sostenida por debajo de la melodía
        prev = pitch
    return out
