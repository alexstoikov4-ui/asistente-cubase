"""Transformaciones de MIDI existente: conservar lo pedido y modificar el resto."""
from __future__ import annotations

import copy
import random
from collections import defaultdict

from ..midi.smf import Note, Song, Track
from ..theory.core import Chord, Scale, parse_roman, voice_lead
from . import melody as mel
from .analysis import chords_per_bar, detect_key, identify_role, is_drum
from .moods import MOODS, resolve_mood


def _bar_range_filter(song: Song, bars: tuple[int, int] | None):
    tpb = song.ticks_per_bar()
    if bars is None:
        return lambda tick: True
    a, b = bars  # compases 1-based inclusivos
    return lambda tick: (a - 1) * tpb <= tick < b * tpb


def song_scale(song: Song) -> Scale:
    k = detect_key(song)
    if not k.get("key"):
        raise ValueError("no se puede deducir la tonalidad: el MIDI no tiene notas melódicas")
    return Scale(k["tonic"], k["mode"])


def keep_rhythm_new_pitches(song: Song, track_name: str, *, seed: int = 1, mood: str | None = None,
                            new_chords: list[Chord] | None = None, bars: tuple[int, int] | None = None) -> Song:
    """Conserva el ritmo (posiciones y duraciones) de una pista y genera notas nuevas, compás a compás,
    eligiendo entre varios candidatos el de mejor puntuación melódica."""
    out = copy.deepcopy(song)
    tr = out.track(track_name)
    if tr is None:
        raise KeyError(f"no existe la pista {track_name!r}")
    scale = song_scale(song)
    rng = random.Random(seed)
    tpb = out.ticks_per_bar()
    step = out.ppq // 4
    in_range = _bar_range_filter(out, bars)
    chords = new_chords or [c["chord_obj"] or Chord(scale.tonic) for c in chords_per_bar(
        song, [t for t in song.tracks if t is not song.track(track_name) and not is_drum(t)] or None, scale=scale)]
    m = MOODS[resolve_mood(mood)]
    by_bar: dict[int, list[Note]] = defaultdict(list)
    for n in tr.notes:
        if in_range(n.start):
            by_bar[n.start // tpb].append(n)
    keep = [n for n in tr.notes if not in_range(n.start)]
    pitches = [n.pitch for n in tr.notes] or [72]
    center = int(sum(pitches) / len(pitches))
    prev = None
    new_notes = []
    for bar in sorted(by_bar):
        notes = sorted(by_bar[bar], key=lambda n: n.start)
        chord = chords[bar % len(chords)] if chords else Chord(scale.tonic)
        rhythm = [((n.start % tpb) // step, max(1, n.duration // step)) for n in notes]
        best, best_s = None, -1e9
        for _ in range(40):
            start = prev if prev is not None else center
            ps = mel.gen_pitches(rng, rhythm, scale, chord, start + rng.choice([-2, 0, 2]), m["contour"], m["leap_prob"])
            cand = [mel.MelNote(p, d, x) for (p, d), x in zip(rhythm, ps)]
            s = mel.melody_score(cand, scale, lambda _p: chord) - abs(sum(ps) / len(ps) - center) * 0.15
            if s > best_s:
                best, best_s = ps, s
        for n, p in zip(notes, best):
            new_notes.append(Note(n.start, n.duration, p, n.velocity))
        prev = best[-1]
    tr.notes = sorted(keep + new_notes, key=lambda n: n.start)
    return out


def _group_by_onset(notes: list[Note]) -> list[list[Note]]:
    groups: dict[int, list[Note]] = defaultdict(list)
    for n in notes:
        groups[n.start].append(n)
    return [sorted(groups[k], key=lambda n: n.pitch) for k in sorted(groups)]


def reharmonize(song: Song, *, progression: str | None = None, mood: str | None = None, seed: int = 1,
                keep_melody_exact: bool = False) -> tuple[Song, list[str]]:
    """Cambia la armonía conservando el ritmo de todas las pistas.

    - Acordes/pads: mismo ritmo, nuevas disposiciones con conducción de voces.
    - Bajo: mismo ritmo; cada nota conserva su relación con la fundamental (fundamental, quinta, octava).
    - Melodía: mismas notas salvo las de tiempo fuerte que choquen con el nuevo acorde, que se mueven a la
      nota del acorde más cercana (a menos que keep_melody_exact=True).
    """
    out = copy.deepcopy(song)
    scale = song_scale(song)
    rng = random.Random(seed)
    tpb = out.ticks_per_bar()
    harmony_old = chords_per_bar(song, [t for t in song.tracks if identify_role(t) in ("chords", "pad", "bass")] or None,
                                 scale=scale)
    # ritmo armónico: cada cuántos compases cambia el acorde original
    changes = [i for i in range(1, len(harmony_old)) if harmony_old[i]["chord"] != harmony_old[i - 1]["chord"]]
    hr = 1
    if changes:
        gaps = [b - a for a, b in zip([0] + changes, changes)]
        hr = max(1, min(4, round(sum(gaps) / len(gaps))))
    if progression:
        numerals = [r for r in progression.replace(" ", "-").split("-") if r]
    else:
        fam = "major" if scale.mode in ("major", "ionian", "lydian", "mixolydian") else "minor"
        numerals = rng.choice(MOODS[resolve_mood(mood)]["progressions"][fam]).split("-")
    new = [parse_roman(r, scale) for r in numerals]
    n_bars = len(harmony_old)
    bar_chord = [new[(b // hr) % len(new)] for b in range(n_bars)]
    log = [f"Tonalidad detectada: {scale.name()}", f"Ritmo armónico original: {hr} compás(es) por acorde",
           "Nueva progresión: " + " - ".join(f"{r} ({c.name()})" for r, c in zip(numerals, new))]
    for tr in out.tracks:
        if is_drum(tr):
            continue
        role = identify_role(tr)
        if role in ("chords", "pad"):
            new_notes = []
            prev = None
            for g in _group_by_onset(tr.notes):
                b = min(n_bars - 1, g[0].start // tpb)
                lo, hi = min(n.pitch for n in g), max(n.pitch for n in g)
                v = voice_lead(prev, bar_chord[b], max(36, lo - 4), min(96, max(hi + 4, lo + 16)),
                               n_voices=max(2, min(5, len(g))))
                prev = v
                ref = g[0]
                for p in v:
                    new_notes.append(Note(ref.start, ref.duration, p, ref.velocity))
            tr.notes = new_notes
        elif role == "bass":
            for i, n in enumerate(tr.notes):
                b = min(n_bars - 1, n.start // tpb)
                old = harmony_old[b]["chord_obj"]
                old_root = old.bass_pc() if old else scale.tonic
                rel = (n.pitch - old_root) % 12
                rel = rel if rel in (0, 7) else 0  # fundamental o quinta; el resto se lleva a la fundamental
                target = bar_chord[b].bass_pc() + rel
                cands = [p for p in range(n.pitch - 8, n.pitch + 9) if p % 12 == target % 12]
                tr.notes[i] = Note(n.start, n.duration, min(cands, key=lambda p: abs(p - n.pitch)), n.velocity)
        elif role in ("lead", "counter", "arp", "pluck") and not keep_melody_exact:
            step = out.ppq // 4
            moved = 0
            for i, n in enumerate(tr.notes):
                b = min(n_bars - 1, n.start // tpb)
                ch = bar_chord[b]
                strong = mel.metric_weight((n.start % tpb) // step) >= 3 or n.duration >= tpb // 4
                if role in ("arp", "pluck") or (strong and n.pitch % 12 not in ch.pcs):
                    p = mel.nearest_chord_tone(n.pitch, ch)
                    if p != n.pitch:
                        moved += 1
                    tr.notes[i] = Note(n.start, n.duration, p, n.velocity)
                elif not scale.contains(n.pitch):
                    tr.notes[i] = Note(n.start, n.duration, scale.nearest(n.pitch), n.velocity)
            log.append(f"{tr.name}: {moved} notas ajustadas al nuevo acorde; ritmo intacto.")
    return out, log


def change_mode(song: Song, new_mode: str, bars: tuple[int, int] | None = None) -> Song:
    """Cambia el modo (misma tónica) en un rango de compases: cada nota conserva su grado."""
    out = copy.deepcopy(song)
    old = song_scale(song)
    new = Scale(old.tonic, new_mode)
    in_range = _bar_range_filter(out, bars)
    for tr in out.tracks:
        if is_drum(tr):
            continue
        for i, n in enumerate(tr.notes):
            if not in_range(n.start):
                continue
            d = old.degree_of(n.pitch)
            if d is None:
                continue  # notas cromáticas se mantienen
            octv = (n.pitch - old.tonic) // 12
            p = old.tonic + 12 * octv + new.intervals[d]
            tr.notes[i] = Note(n.start, n.duration, p, n.velocity)
    return out


def vary_answer(song: Song, track_name: str, phrase_bars: int = 8, seed: int = 1) -> Song:
    """Conserva el motivo (primera mitad de cada frase) y reescribe la segunda mitad como respuesta distinta."""
    out = copy.deepcopy(song)
    tr = out.track(track_name)
    if tr is None:
        raise KeyError(f"no existe la pista {track_name!r}")
    scale = song_scale(song)
    rng = random.Random(seed)
    tpb = out.ticks_per_bar()
    step = out.ppq // 4
    chords = [c["chord_obj"] or Chord(scale.tonic) for c in chords_per_bar(song, scale=scale)]
    half = phrase_bars // 2
    new_notes = []
    by_bar: dict[int, list[Note]] = defaultdict(list)
    for n in tr.notes:
        by_bar[n.start // tpb].append(n)
    for bar, notes in sorted(by_bar.items()):
        if bar % phrase_bars < half or not notes:
            new_notes.extend(notes)
            continue
        ch = chords[min(bar, len(chords) - 1)]
        mn = [mel.MelNote((n.start % tpb) // step, max(1, n.duration // step), n.pitch, n.velocity) for n in notes]
        final = bar % phrase_bars == phrase_bars - 1
        varied = mel.vary_ending(rng, mn, scale, ch, final_pc=scale.tonic if final and scale.tonic in ch.pcs else None,
                                 long_final=final)
        for m in varied:
            new_notes.append(Note(bar * tpb + m.pos * step, m.dur * step, m.pitch, m.vel))
    tr.notes = sorted(new_notes, key=lambda n: n.start)
    return out
