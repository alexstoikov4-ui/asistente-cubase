"""Motor de teoría musical: notas, escalas, modos, acordes, numerales romanos y conducción de voces."""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ES = {"DO": "C", "RE": "D", "MI": "E", "FA": "F", "SOL": "G", "LA": "A", "SI": "B"}

MODES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
}

CHORD_QUALITIES = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "maj7": [0, 4, 7, 11],
    "7": [0, 4, 7, 10],
    "min7": [0, 3, 7, 10],
    "m7b5": [0, 3, 6, 10],
    "dim7": [0, 3, 6, 9],
    "add9": [0, 4, 7, 14],
    "madd9": [0, 3, 7, 14],
    "maj9": [0, 4, 7, 11, 14],
    "min9": [0, 3, 7, 10, 14],
    "9": [0, 4, 7, 10, 14],
    "7sus4": [0, 5, 7, 10],
}

_SUFFIX = {"maj": "", "min": "m", "dim": "dim", "aug": "+", "sus2": "sus2", "sus4": "sus4", "maj7": "maj7",
           "7": "7", "min7": "m7", "m7b5": "m7b5", "dim7": "dim7", "add9": "add9", "madd9": "m(add9)",
           "maj9": "maj9", "min9": "m9", "9": "9", "7sus4": "7sus4"}


def parse_note(name: str) -> int:
    """'C#', 'Db', 'Do#', 'sib' -> clase de altura 0-11."""
    s = name.strip()
    up = s.upper()
    for es, en in sorted(_ES.items(), key=lambda kv: -len(kv[0])):
        if up.startswith(es):
            s = en + s[len(es):]
            break
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]*)", s.strip())
    if not m:
        raise ValueError(f"nota no reconocida: {name!r}")
    pc = _PC[m.group(1).upper()]
    for ch in m.group(2):
        pc += 1 if ch in "#♯" else -1
    return pc % 12


def note_name(pitch: int, flats: bool = False) -> str:
    names = FLAT_NAMES if flats else NOTE_NAMES
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def parse_key(text: str) -> tuple[int, str]:
    """'A minor', 'Am', 'F# menor', 'La menor', 'D dorian' -> (tónica, modo)."""
    t = text.strip()
    m = re.match(r"^\s*((?:do|re|mi|fa|sol|la|si|[A-Ga-g])[#b♯♭]?)\s*(.*)$", t, re.IGNORECASE)
    if not m:
        raise ValueError(f"tonalidad no reconocida: {text!r}")
    tonic = parse_note(m.group(1))
    rest = m.group(2).strip().lower()
    aliases = {"": "major", "m": "minor", "min": "minor", "menor": "minor", "minor": "minor", "maj": "major",
               "mayor": "major", "major": "major", "dórico": "dorian", "dorico": "dorian", "frigio": "phrygian",
               "lidio": "lydian", "mixolidio": "mixolydian", "eolio": "aeolian", "locrio": "locrian"}
    mode = aliases.get(rest, rest)
    if mode not in MODES:
        raise ValueError(f"modo desconocido: {rest!r}")
    return tonic, mode


@dataclass(frozen=True)
class Scale:
    tonic: int
    mode: str = "major"

    @property
    def intervals(self) -> list[int]:
        return MODES[self.mode]

    @property
    def pcs(self) -> list[int]:
        return [(self.tonic + i) % 12 for i in self.intervals]

    def contains(self, pitch: int) -> bool:
        return pitch % 12 in self.pcs

    def degree_pitch(self, degree: int, octave: int = 4) -> int:
        """Grado 0-based (puede ser negativo o >6) -> altura MIDI. octave 4 => C4 = 60."""
        o, d = divmod(degree, 7)
        return 12 * (octave + 1 + o) + self.tonic + self.intervals[d]

    def degree_of(self, pitch: int) -> int | None:
        pc = (pitch - self.tonic) % 12
        return self.intervals.index(pc) if pc in self.intervals else None

    def step(self, pitch: int, steps: int) -> int:
        """Desplaza una altura un número de grados de la escala (ajusta antes al grado más cercano)."""
        p = self.nearest(pitch)
        octv, pc = divmod(p - self.tonic, 12)
        d = self.intervals.index(pc) + steps
        o2, d2 = divmod(d, 7)
        return self.tonic + 12 * (octv + o2) + self.intervals[d2]

    def nearest(self, pitch: int, direction: int = 0) -> int:
        if self.contains(pitch):
            return pitch
        for k in range(1, 7):
            cands = []
            if direction >= 0:
                cands.append(pitch + k)
            if direction <= 0:
                cands.append(pitch - k)
            for c in cands:
                if self.contains(c):
                    return c
        return pitch

    def name(self) -> str:
        return f"{NOTE_NAMES[self.tonic]} {self.mode}"


@dataclass
class Chord:
    root: int  # clase de altura
    quality: str = "maj"
    bass: int | None = None  # clase de altura del bajo (inversiones / acordes con barra)
    numeral: str = ""
    tags: set = field(default_factory=set)  # p.ej. {"secondary_dominant", "borrowed"}

    @property
    def pcs(self) -> list[int]:
        return [(self.root + i) % 12 for i in CHORD_QUALITIES[self.quality]]

    @property
    def triad_pcs(self) -> list[int]:
        return self.pcs[:3]

    def name(self, flats: bool = False) -> str:
        names = FLAT_NAMES if flats else NOTE_NAMES
        s = names[self.root] + _SUFFIX[self.quality]
        if self.bass is not None and self.bass != self.root:
            s += "/" + names[self.bass]
        return s

    def bass_pc(self) -> int:
        return self.root if self.bass is None else self.bass


_ROMAN = {"I": 0, "II": 1, "III": 2, "IV": 3, "V": 4, "VI": 5, "VII": 6}
_MAJOR = MODES["major"]


def _triad_quality(scale: Scale, degree: int) -> str:
    root = scale.intervals[degree]
    third = (scale.intervals[(degree + 2) % 7] - root) % 12
    fifth = (scale.intervals[(degree + 4) % 7] - root) % 12
    if third == 4 and fifth == 7:
        return "maj"
    if third == 3 and fifth == 7:
        return "min"
    if third == 3 and fifth == 6:
        return "dim"
    if third == 4 and fifth == 8:
        return "aug"
    return "maj"


def diatonic_chord(scale: Scale, degree: int, seventh: bool = False) -> Chord:
    q = _triad_quality(scale, degree % 7)
    root = (scale.tonic + scale.intervals[degree % 7]) % 12
    if seventh:
        sev = (scale.intervals[(degree + 6) % 7] - scale.intervals[degree % 7]) % 12
        q = {("maj", 11): "maj7", ("maj", 10): "7", ("min", 10): "min7", ("dim", 10): "m7b5",
             ("dim", 9): "dim7", ("min", 11): "min7"}.get((q, sev), q)
    return Chord(root, q)


def parse_roman(numeral: str, scale: Scale) -> Chord:
    """Numerales romanos relativos a la escala dada.

    Mayúsculas = acorde mayor, minúsculas = menor. Acepta alteraciones (bVII, #iv), sufijos
    (7, maj7, 9, add9, sus2, sus4, dim, o, ø, +), inversiones (/3, /5 => bajo en 3ª o 5ª) y
    dominantes secundarias (V/V, V7/vi). Los grados se miden sobre la escala activa (en La menor,
    VI = Fa y VII = Sol); las alteraciones desplazan ese grado (en Do mayor, bVII = Si bemol).
    En las secundarias el intervalo se mide sobre la escala mayor del acorde objetivo.
    """
    text = numeral.strip()
    secondary = None
    inversion = None
    if "/" in text:
        head, tail = text.split("/", 1)
        if tail in ("3", "5", "7"):
            inversion = int(tail)
        else:
            secondary = tail
        text = head
    m = re.fullmatch(r"([b#]?)(VII|VI|V|IV|III|II|I|vii|vi|v|iv|iii|ii|i)(.*)", text)
    if not m:
        raise ValueError(f"numeral no reconocido: {numeral!r}")
    acc, rn, suf = m.groups()
    degree = _ROMAN[rn.upper()]
    base_tonic = scale.tonic
    if secondary:
        target = parse_roman(secondary, scale)
        base_tonic = target.root
    steps = _MAJOR if secondary else scale.intervals
    root = (base_tonic + steps[degree] + (1 if acc == "#" else -1 if acc == "b" else 0)) % 12
    upper = rn.isupper()
    suf_map = {
        "": "maj" if upper else "min", "7": "7" if upper else "min7", "maj7": "maj7", "M7": "maj7",
        "9": "9" if upper else "min9", "maj9": "maj9", "add9": "add9" if upper else "madd9",
        "sus2": "sus2", "sus4": "sus4", "sus": "sus4", "dim": "dim", "o": "dim", "o7": "dim7",
        "ø": "m7b5", "ø7": "m7b5", "m7b5": "m7b5", "+": "aug", "7sus4": "7sus4",
    }
    if suf not in suf_map:
        raise ValueError(f"sufijo de acorde desconocido en {numeral!r}")
    quality = suf_map[suf]
    tags = set()
    if secondary:
        tags.add("secondary_dominant" if rn.upper() in ("V", "VII") else "secondary")
    else:
        # ¿prestado de otro modo? si alguna nota del acorde no pertenece a la escala
        if any(pc not in scale.pcs for pc in Chord(root, quality).triad_pcs):
            tags.add("borrowed")
    ch = Chord(root, quality, numeral=numeral, tags=tags)
    if inversion == 3:
        ch.bass = ch.pcs[1]
    elif inversion == 5:
        ch.bass = ch.pcs[2]
    elif inversion == 7 and len(ch.pcs) >= 4:
        ch.bass = ch.pcs[3]
    return ch


def chord_from_name(name: str) -> Chord:
    m = re.fullmatch(r"([A-G][#b]?)(.*?)(?:/([A-G][#b]?))?", name.strip())
    if not m:
        raise ValueError(f"acorde no reconocido: {name!r}")
    root = parse_note(m.group(1))
    suffix = m.group(2)
    inv = {v: k for k, v in _SUFFIX.items()}
    inv.update({"min": "min", "M": "maj", "maj": "maj", "-": "min", "madd9": "madd9", "m(add9)": "madd9"})
    if suffix not in inv:
        raise ValueError(f"tipo de acorde desconocido: {suffix!r}")
    bass = parse_note(m.group(3)) if m.group(3) else None
    return Chord(root, inv[suffix], bass=bass)


def voicings(chord: Chord, low: int, high: int, n_voices: int = 4) -> list[tuple[int, ...]]:
    """Disposiciones razonables del acorde dentro del rango (sin cruces, sin huecos > octava).
    Si faltan voces se duplica la fundamental, la quinta o la tercera (en ese orden de preferencia);
    si el rango es demasiado estrecho se reduce el número de voces."""
    pcs = chord.pcs
    for nv in range(n_voices, 1, -1):
        if len(pcs) >= nv:
            # acordes con más notas que voces: se omite antes la quinta que la tercera o la séptima
            base = pcs[:nv] if len(pcs) == nv else ([pcs[0], pcs[1]] + pcs[3:] + [pcs[2]])[:nv]
            needs = [base]
        else:
            dups = [pcs[0], pcs[2] if len(pcs) > 2 else pcs[0], pcs[1]]
            needs = [list(pcs) + [d] * (nv - len(pcs)) for d in dups]
        result = set()
        for need in needs:
            options = [[p for p in range(low, high + 1) if p % 12 == pc] for pc in need]
            for combo in itertools.product(*options):
                v = tuple(sorted(combo))
                if len(set(v)) < len(v) or v[-1] - v[0] > 24:
                    continue
                if any(v[i + 1] - v[i] > 12 for i in range(len(v) - 1)):
                    continue
                result.add(v)
        if result:
            return sorted(result)
    return []


def voice_lead(prev: tuple[int, ...] | None, chord: Chord, low: int = 52, high: int = 79,
               n_voices: int = 4, center: int | None = None) -> tuple[int, ...]:
    """Elige la disposición que minimiza el movimiento total (notas comunes se mantienen) y
    penaliza quintas/octavas paralelas y alejarse del centro del registro."""
    cands = voicings(chord, low, high, n_voices)
    if not cands:
        raise ValueError(f"no hay disposición para {chord.name()} en {low}-{high}")
    center = center if center is not None else (low + high) // 2
    if prev is None:
        return min(cands, key=lambda v: abs(sum(v) / len(v) - center) + (v[-1] - v[0]) * 0.1)

    def cost(v):
        mv = sum(abs(a - b) for a, b in zip(prev, v)) if len(prev) == len(v) else abs(sum(prev) / len(prev) - sum(v) / len(v)) * len(v)
        par = 0
        if len(prev) == len(v):
            for i in range(len(v)):
                for j in range(i + 1, len(v)):
                    i1, i2 = (prev[j] - prev[i]) % 12, (v[j] - v[i]) % 12
                    if i1 == i2 and i1 in (0, 7) and prev[i] != v[i]:
                        par += 3
        drift = abs(sum(v) / len(v) - center) * 0.3
        return mv + par + drift

    return min(cands, key=cost)


def key_signature(tonic: int, mode: str) -> tuple[int, int]:
    """(número de sostenidos/bemoles, 0 mayor / 1 menor) para el meta-evento de armadura."""
    rel_major = tonic
    if mode in ("minor", "aeolian", "harmonic_minor", "melodic_minor"):
        rel_major = (tonic + 3) % 12
    elif mode == "dorian":
        rel_major = (tonic - 2) % 12
    elif mode == "phrygian":
        rel_major = (tonic - 4) % 12
    elif mode == "lydian":
        rel_major = (tonic - 5) % 12
    elif mode == "mixolydian":
        rel_major = (tonic - 7) % 12
    elif mode == "locrian":
        rel_major = (tonic - 11) % 12
    sharps = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 5: -1, 10: -2, 3: -3, 8: -4, 1: -5}[rel_major]
    return sharps, 1 if mode in ("minor", "aeolian", "harmonic_minor", "melodic_minor") else 0
