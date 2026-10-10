"""Arreglista: genera una canción completa multipista a partir de estilo, emoción y tonalidad."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..midi.smf import Note, Song, Track
from ..theory.core import NOTE_NAMES, Chord, Scale, key_signature, parse_key, parse_roman, voice_lead
from . import melody as mel
from .moods import MOODS, resolve_mood
from .styles import load_styles, resolve_style

DRUM_NOTES = {"kick": 36, "clap": 39, "snare": 38, "hat_closed": 42, "hat_open": 46, "ride": 51, "crash": 49,
              "shaker": 70, "tom_low": 45, "tom_mid": 47, "tom_high": 50}
CHANNELS = {"lead": 0, "counter": 1, "chords": 2, "arp": 3, "bass": 4, "sub": 5, "pluck": 6, "pad": 7, "drums": 9}
TRACK_NAMES = {"lead": "Lead", "counter": "Contramelodia", "chords": "Acordes", "arp": "Arpegio", "bass": "Bajo",
               "sub": "Subgrave", "pluck": "Pluck", "pad": "Pad", "drums": "Bateria"}
MAJOR_FAMILY = {"major", "ionian", "lydian", "mixolydian"}


@dataclass
class CompositionRequest:
    style: str | None = None
    mood: str | None = None
    key: str | None = None  # "A minor", "F#m", "Re menor"...
    bpm: float | None = None
    seed: int = 1
    progression: str | None = None  # numerales romanos separados por guiones
    sections: list[str] | None = None  # limitar a ciertas secciones (por nombre, p.ej. ["Drop"])
    ppq: int = 480
    lead_overrides: dict = field(default_factory=dict)  # p.ej. {"leap_prob": 0.4} desde preferencias del usuario
    extra_extensions: list = field(default_factory=list)


@dataclass
class CompositionInfo:
    style: str = ""
    mood: str = ""
    key: str = ""
    bpm: float = 0.0
    progression: list[str] = field(default_factory=list)
    chord_names: list[str] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    motif: list[tuple] = field(default_factory=list)
    melody_metrics: dict = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)


def _family(mode: str) -> str:
    return "major" if mode in MAJOR_FAMILY else "minor"


def enrich(chord: Chord, ext: str) -> Chord:
    """Añade una extensión compatible con la calidad del acorde (si no lo es, lo deja igual)."""
    table = {
        ("maj", "add9"): "add9", ("maj", "maj7"): "maj7", ("maj", "sus2"): "sus2", ("maj", "sus4"): "sus4",
        ("min", "add9"): "madd9", ("min", "min7"): "min7", ("min", "maj7"): "min7", ("min", "sus2"): "sus2",
    }
    q = table.get((chord.quality, ext))
    if not q:
        return chord
    return Chord(chord.root, q, chord.bass, chord.numeral, set(chord.tags) | {"extended"})


class Arranger:
    def __init__(self, styles: dict | None = None):
        self.styles = styles or load_styles()

    # ------------------------------------------------------------ plan armónico
    def plan(self, req: CompositionRequest):
        rng = random.Random(req.seed)
        style = self.styles[resolve_style(req.style, self.styles)]
        mood_name = resolve_mood(req.mood)
        mood = MOODS[mood_name]
        decisions = []
        if req.key:
            tonic, mode = parse_key(req.key)
            decisions.append(f"Tonalidad indicada por el usuario: {NOTE_NAMES[tonic]} {mode}.")
        else:
            tonic = parse_key(style.get("default_key", "A"))[0]
            mode = mood["mode"]
            decisions.append(f"Modo {mode} elegido por la intención '{mood['label']}' (convención, no regla).")
        scale = Scale(tonic, mode)
        if req.progression:
            numerals = [r.strip() for r in req.progression.replace(" ", "-").split("-") if r.strip()]
            decisions.append("Progresión indicada por el usuario.")
        else:
            options = mood["progressions"][_family(mode)]
            numerals = rng.choice(options).split("-")
            decisions.append(f"Progresión {'-'.join(numerals)} elegida entre {len(options)} opciones para "
                             f"'{mood['label']}'.")
        chords = [parse_roman(n, scale) for n in numerals]
        bpm = req.bpm or style["default_bpm"]
        lo, hi = style["bpm"]
        if not lo <= bpm <= hi:
            decisions.append(f"Aviso: {bpm} BPM está fuera del rango típico de {style['label']} ({lo}-{hi}).")
        return rng, style, mood_name, mood, scale, numerals, chords, bpm, decisions

    # ------------------------------------------------------------ canción completa
    def compose(self, req: CompositionRequest) -> tuple[Song, CompositionInfo]:
        rng, style, mood_name, mood, scale, numerals, chords, bpm, decisions = self.plan(req)
        ppq = req.ppq
        step = ppq // 4
        bar_ticks = ppq * 4
        hr = int(style.get("harmonic_rhythm_bars", 1))
        sections = style["sections"]
        if req.sections:
            wanted = {s.lower() for s in req.sections}
            sections = [s for s in sections if s["name"].lower() in wanted
                        or any(w in s["name"].lower() for w in wanted)] or sections
        song = Song(ppq=ppq, tempos=[(0, float(bpm))], time_signatures=[(0, 4, 4)],
                    key_signature=key_signature(scale.tonic, scale.mode))
        tracks: dict[str, Track] = {}

        def track(role: str) -> Track:
            if role not in tracks:
                tracks[role] = Track(name=TRACK_NAMES[role], channel=CHANNELS[role],
                                     program=None if role == "drums" else style.get("programs", {}).get(role),
                                     role=role)
            return tracks[role]

        lead_cfg = dict(style["lead"])
        center = 12 * (lead_cfg.get("octave", 5) + 1) + scale.tonic + mood.get("register_shift", 0)
        if center > 79:
            center -= 12
        params = {"density": lead_cfg.get("density", 0.5), "syncopation": lead_cfg.get("syncopation", 0.4),
                  "gate": lead_cfg.get("gate", 0.85) * (0.7 + 0.3 * mood.get("legato", 0.8)),
                  "contour": mood.get("contour", "arch"), "leap_prob": mood.get("leap_prob", 0.25),
                  "low": center - 9, "high": center + lead_cfg.get("range", 12)}
        for k, v in req.lead_overrides.items():
            params[k] = v
            decisions.append(f"Preferencia del usuario aplicada a la melodía: {k} = {v}.")
        motif = mel.generate_motif(rng, scale, chords[0], center, params)
        decisions.append(f"Motivo principal de {len(motif)} notas elegido entre 80 candidatos por calidad melódica.")
        # variante de tensión para los build-ups: último acorde suspendido
        extensions = list(mood.get("extensions", [])) + [e for e in req.extra_extensions
                                                         if e not in mood.get("extensions", [])]
        tick = 0
        info_sections = []
        all_lead: list[tuple[int, mel.MelNote]] = []
        lead_metrics: dict = {}
        for s_i, sec in enumerate(sections):
            bars = int(sec["bars"])
            energy = float(sec["energy"])
            roles = sec["roles"]
            name = sec["name"]
            song.markers.append((tick, name))
            bar_chords = [chords[(b // hr) % len(chords)] for b in range(bars)]
            if "build" in name.lower() and bars >= 2:
                bar_chords[-1] = enrich(bar_chords[-1], "sus4")
            next_is_drop = s_i + 1 < len(sections) and any(
                w in sections[s_i + 1]["name"].lower() for w in ("drop", "clímax", "climax", "tema"))
            sec_lead: list[mel.MelNote] = []
            for role in roles:
                base = role.split("_")[0]
                if base == "drums":
                    self._drums(track("drums"), style, role, tick, bars, energy, step, next_is_drop)
                elif base == "bass":
                    self._bass(track("bass"), style, bar_chords, tick, step, energy, light=role == "bass_light",
                               cut_last_beat=next_is_drop)
                elif base == "sub":
                    self._sub(track("sub"), bar_chords, tick, bar_ticks, step, energy)
                elif base == "chords":
                    self._chords(track("chords"), style, bar_chords, tick, step, energy, extensions, rng)
                elif base == "pad":
                    self._pad(track("pad"), style, bar_chords, tick, bar_ticks, energy, extensions)
                elif base == "arp":
                    self._arp(track("arp"), style, bar_chords, tick, step, energy, build="build" in name.lower())
                elif base == "pluck":
                    self._pluck(track("pluck"), style, bar_chords, tick, step, energy)
                elif base == "lead":
                    sec_lead = self._lead(rng, scale, motif, bar_chords, params, role, energy)
                    self._emit_mel(track("lead"), sec_lead, tick, step, energy,
                                   double_octave=role == "lead_climax")
                    all_lead.extend((tick, n) for n in sec_lead)
                    if not lead_metrics or (role in ("lead", "lead_climax") and lead_metrics.get("_soft")):
                        lead_metrics = mel.melody_metrics(
                            sec_lead, scale, lambda p, bc=bar_chords: bc[min(p // 16, len(bc) - 1)])
                        if role not in ("lead", "lead_climax"):
                            lead_metrics["_soft"] = True
            if "counter" in roles:
                cm = mel.counter_melody(rng, sec_lead, scale, bar_chords, low=params["low"] - 12,
                                        high=params["low"] + 7)
                self._emit_mel(track("counter"), cm, tick, step, energy * 0.8)
            info_sections.append({"name": name, "bars": bars, "energy": energy, "roles": roles,
                                  "start_bar": tick // bar_ticks + 1})
            tick += bars * bar_ticks
        order = ["lead", "counter", "chords", "pad", "arp", "pluck", "bass", "sub", "drums"]
        song.tracks = [tracks[r] for r in order if r in tracks]
        song.markers.append((tick, "Fin"))
        if not lead_metrics and all_lead:
            lead_metrics = mel.melody_metrics([n for _, n in all_lead], scale)
        lead_metrics.pop("_soft", None)
        info = CompositionInfo(
            style=style["name"], mood=mood_name, key=f"{NOTE_NAMES[scale.tonic]} {scale.mode}", bpm=float(bpm),
            progression=numerals, chord_names=[c.name() for c in chords], sections=info_sections,
            motif=[(n.pos, n.dur, n.pitch) for n in motif], melody_metrics=lead_metrics, decisions=decisions)
        return song, info

    # ------------------------------------------------------------ renderizadores
    @staticmethod
    def _vel(energy: float, accent: bool = False) -> int:
        return max(1, min(127, int(58 + 52 * energy + (10 if accent else 0))))

    def _drums(self, tr: Track, style, role, start, bars, energy, step, anticipation):
        pats = style["drums"]
        bar_ticks = step * 16
        for b in range(bars):
            t0 = start + b * bar_ticks
            last_bar = b == bars - 1
            phrase_end = (b + 1) % 8 == 0
            if b == 0 and energy >= 0.7:
                tr.notes.append(Note(t0, step * 4, DRUM_NOTES["crash"], self._vel(energy, True)))
            if role == "drums_build":
                # redoble que se acelera: negras -> corcheas -> semicorcheas; crescendo
                frac = b / max(1, bars - 1)
                rate = 4 if frac < 0.5 else (2 if frac < 0.75 else 1)
                for s in range(0, 16, rate):
                    if last_bar and anticipation and s >= 12:
                        break  # silencio antes del drop
                    v = int(50 + 70 * (b * 16 + s) / (bars * 16))
                    tr.notes.append(Note(t0 + s * step, step, DRUM_NOTES["snare"], min(127, v)))
                if frac < 0.75:
                    for s in range(0, 16, 4):
                        tr.notes.append(Note(t0 + s * step, step, DRUM_NOTES["kick"], self._vel(energy)))
                continue
            for name, pat in pats.items():
                if name not in DRUM_NOTES:
                    continue
                if role == "drums_light" and name in ("clap", "snare", "hat_open", "ride"):
                    continue
                for s, ch in enumerate(pat[:16]):
                    if ch not in "xX":
                        continue
                    if phrase_end and s >= 12 and name in ("hat_closed", "shaker"):
                        continue
                    if last_bar and anticipation and s >= 12:
                        continue
                    tr.notes.append(Note(t0 + s * step, step, DRUM_NOTES[name],
                                         self._vel(energy * (0.8 if role == "drums_light" else 1.0), s % 4 == 0)))
            if phrase_end and not (last_bar and anticipation) and role != "drums_light":
                for i, s in enumerate((12, 13, 14, 15)):  # relleno al final de cada frase de 8 compases
                    drum = ("tom_high", "tom_mid", "tom_low", "snare")[i]
                    tr.notes.append(Note(t0 + s * step, step, DRUM_NOTES[drum], self._vel(energy, i == 3)))

    def _bass(self, tr: Track, style, bar_chords, start, step, energy, light=False, cut_last_beat=False):
        cfg = style["bass"]
        pat = cfg["pattern"][:16]
        octave = cfg.get("octave", 2)
        gate = cfg.get("gate", 0.8)
        for b, chord in enumerate(bar_chords):
            t0 = start + b * step * 16
            root = 12 * (octave + 1) + chord.bass_pc()
            if root > 12 * (octave + 1) + 7:
                root -= 12
            for s, ch in enumerate(pat):
                if ch in ".-":
                    continue
                if light and s >= 8:
                    continue
                if cut_last_beat and b == len(bar_chords) - 1 and s >= 12:
                    continue
                length = 1
                while s + length < 16 and pat[s + length] == "-":
                    length += 1
                pitch = {"x": root, "o": root + 12, "f": root + 7}.get(ch, root)
                dur = max(1, int(round(length * step * gate)))
                tr.notes.append(Note(t0 + s * step, dur, pitch, self._vel(energy * (0.75 if light else 1), s % 4 == 2)))

    def _sub(self, tr, bar_chords, start, bar_ticks, step, energy):
        for b, chord in enumerate(bar_chords):
            if b and chord.bass_pc() == bar_chords[b - 1].bass_pc() and tr.notes:
                last = tr.notes[-1]
                tr.notes[-1] = Note(last.start, last.duration + bar_ticks, last.pitch, last.velocity)
                continue
            pitch = 24 + chord.bass_pc()
            if pitch > 31:
                pitch -= 12 if pitch - 12 >= 24 else 0
            tr.notes.append(Note(start + b * bar_ticks, bar_ticks - step, pitch, self._vel(energy * 0.9)))

    def _voicings(self, bar_chords, low, high, voices, extensions=(), rng=None):
        out = []
        prev = None
        for b, ch in enumerate(bar_chords):
            if extensions and b % 2 == 1:
                ch = enrich(ch, extensions[b // 2 % len(extensions)])
            v = voice_lead(prev, ch, low, high, n_voices=voices)
            out.append(v)
            prev = v
        return out

    def _chords(self, tr, style, bar_chords, start, step, energy, extensions, rng):
        cfg = style["chords"]
        pat = cfg["pattern"][:16]
        vs = self._voicings(bar_chords, cfg.get("low", 55), cfg.get("high", 76), cfg.get("voices", 4), extensions)
        for b, v in enumerate(vs):
            t0 = start + b * step * 16
            for s, ch in enumerate(pat):
                if ch != "x":
                    continue
                length = 1
                while s + length < 16 and pat[s + length] == "-":
                    length += 1
                for p in v:
                    tr.notes.append(Note(t0 + s * step, max(1, length * step - step // 4), p,
                                         self._vel(energy * 0.9, s % 4 == 0)))

    def _pad(self, tr, style, bar_chords, start, bar_ticks, energy, extensions):
        cfg = style.get("pad", {"low": 52, "high": 79, "voices": 4})
        ext = extensions[:1]
        vs = self._voicings(bar_chords, cfg["low"], cfg["high"], cfg["voices"], ext)
        b = 0
        while b < len(vs):
            e = b + 1
            while e < len(vs) and vs[e] == vs[b]:
                e += 1
            for p in vs[b]:
                tr.notes.append(Note(start + b * bar_ticks, (e - b) * bar_ticks - 10, p, self._vel(energy * 0.7)))
            b = e

    def _arp(self, tr, style, bar_chords, start, step, energy, build=False):
        cfg = style["arp"]
        octave = cfg.get("octave", 5)
        rate = 16 // cfg.get("rate", 16) if cfg.get("rate", 16) <= 16 else 1
        gate = cfg.get("gate", 0.5)
        for b, chord in enumerate(bar_chords):
            base = 12 * (octave + 1)
            tones = sorted({base + ((pc - base) % 12) for pc in chord.pcs[:4]})
            tones = tones + [t + 12 for t in tones[:2]]
            kind = cfg.get("type", "up")
            if kind == "down":
                seq = tones[::-1]
            elif kind == "updown":
                seq = tones + tones[-2:0:-1]
            elif kind == "pattern":
                seq = [tones[i % len(tones)] for i in cfg.get("steps", [0, 1, 2, 3])]
            else:
                seq = tones
            t0 = start + b * step * 16
            for i, s in enumerate(range(0, 16, rate)):
                v = self._vel(energy * 0.8, s % 4 == 0)
                if build:
                    v = min(127, int(v * (0.6 + 0.4 * (b * 16 + s) / (len(bar_chords) * 16))))
                tr.notes.append(Note(t0 + s * step, max(1, int(step * rate * gate)), seq[i % len(seq)], v))

    def _pluck(self, tr, style, bar_chords, start, step, energy):
        cfg = style.get("pluck", {"pattern": "x..x..x...x..x..", "octave": 5})
        pat = cfg["pattern"][:16]
        vs = self._voicings(bar_chords, 12 * (cfg.get("octave", 5) + 1) - 5, 12 * (cfg.get("octave", 5) + 1) + 12, 3)
        order = [2, 1, 2, 0, 2, 1, 0, 1]
        for b, v in enumerate(vs):
            t0 = start + b * step * 16
            k = 0
            for s, ch in enumerate(pat):
                if ch != "x":
                    continue
                tr.notes.append(Note(t0 + s * step, step, v[order[k % len(order)] % len(v)],
                                     self._vel(energy * 0.85, s % 4 == 0)))
                k += 1

    def _lead(self, rng, scale, motif, bar_chords, params, role, energy) -> list[mel.MelNote]:
        bars = len(bar_chords)
        out: list[mel.MelNote] = []
        if role == "lead_fragment":
            frag = mel.fragment(motif, 8)
            for b, chord in enumerate(bar_chords):
                lift = max(0, b - bars // 2)  # secuencia ascendente en la segunda mitad
                if b >= bars - 2 and bars >= 4:
                    # tensión final: notas repetidas en semicorcheas sobre una nota del acorde
                    top = mel.nearest_chord_tone(motif[0].pitch + 7, chord)
                    for s in range(0, 12 if b == bars - 1 else 16, 2 if b == bars - 2 else 1):
                        out.append(mel.MelNote(b * 16 + s, 1, top, 90))
                    continue
                bar = mel.fit_to_chord(frag, scale, chord, anchor=frag[0].pitch, center=frag[0].pitch)
                bar = [mel.MelNote(n.pos % 16 + b * 16, n.dur, scale.step(n.pitch, lift), n.vel) for n in bar]
                out.extend(bar)
            return out
        if role == "lead_soft":
            half = max(1, bars // 2)
            phr = mel.compose_phrase(rng, scale, bar_chords[::2][:half] if half >= 4 else bar_chords[:half],
                                     motif, params, climax=False, final=True)
            aug = mel.augment(phr, 2)
            return [mel.MelNote(n.pos, n.dur, n.pitch, 80) for n in aug if n.pos < bars * 16]
        climax = role == "lead_climax"
        for p0 in range(0, bars, 8):
            chunk = bar_chords[p0:p0 + 8]
            is_last = p0 + 8 >= bars
            phr = mel.compose_phrase(rng, scale, chunk, motif, params, climax=climax and is_last, final=is_last)
            out.extend(mel.MelNote(n.pos + p0 * 16, n.dur, n.pitch, n.vel) for n in phr)
        return out

    def _emit_mel(self, tr, notes, start, step, energy, double_octave=False):
        for n in notes:
            v = self._vel(energy * n.vel / 100, mel.metric_weight(n.pos) >= 3)
            tr.notes.append(Note(start + n.pos * step, max(1, n.dur * step - step // 8), n.pitch, v))
            if double_octave and n.pitch + 12 <= 108:
                tr.notes.append(Note(start + n.pos * step, max(1, n.dur * step - step // 8), n.pitch + 12,
                                     max(1, v - 20)))


def describe(info: CompositionInfo) -> str:
    lines = [f"Estilo: {info.style} | Emoción: {info.mood} | Tonalidad: {info.key} | {info.bpm:g} BPM",
             f"Progresión: {' - '.join(info.progression)}  ({' - '.join(info.chord_names)})",
             "Estructura: " + ", ".join(f"{s['name']} ({s['bars']} c.)" for s in info.sections)]
    if info.melody_metrics:
        m = info.melody_metrics
        lines.append(f"Melodía: ámbito {m.get('range')} semitonos, {m.get('distinct_intervals')} intervalos distintos, "
                     f"recorrido escalar máx. {m.get('longest_scale_run')}, notas del acorde en tiempos fuertes "
                     f"{(m.get('strong_beat_chord_tone_ratio') or 0) * 100:.0f}%.")
    lines += ["Decisiones:"] + [f"  - {d}" for d in info.decisions]
    return "\n".join(lines)
