import random

import pytest

from aiengineer.compose import melody as mel
from aiengineer.compose.analysis import analyze_song, detect_key
from aiengineer.compose.arranger import Arranger, CompositionRequest
from aiengineer.compose.styles import load_styles, resolve_style, validate_style, StyleError
from aiengineer.compose.transform import change_mode, keep_rhythm_new_pitches, reharmonize, vary_answer
from aiengineer.midi.smf import validate_roundtrip
from aiengineer.theory.core import Scale, parse_roman

STYLES = sorted(load_styles())


def test_ten_styles_and_validation():
    assert len(STYLES) >= 10
    assert resolve_style("un tema de uplifting trance", load_styles()) == "uplifting_trance"
    with pytest.raises(StyleError):
        validate_style({"name": "x"}, "x.json")


@pytest.mark.parametrize("style", STYLES)
def test_compose_every_style_valid_midi(tmp_path, style):
    song, info = Arranger().compose(CompositionRequest(style=style, mood="emotivo", seed=5))
    r = validate_roundtrip(song, tmp_path / f"{style}.mid")
    assert r["ok"], r["issues"]
    names = [m for _, m in song.markers]
    assert names[-1] == "Fin" and len(names) >= 4
    assert info.melody_metrics.get("strong_beat_chord_tone_ratio", 1) >= 0.8
    # sin solapes de la misma nota en la misma pista (los notes-off quedarían ambiguos)
    for t in song.tracks:
        last_end = {}
        for n in sorted(t.notes, key=lambda n: n.start):
            assert n.start >= last_end.get(n.pitch, 0) or t.channel == 9, (style, t.name)
            last_end[n.pitch] = n.end


def test_deterministic_and_seed_changes():
    a, _ = Arranger().compose(CompositionRequest(style="trance", seed=3))
    b, _ = Arranger().compose(CompositionRequest(style="trance", seed=3))
    c, _ = Arranger().compose(CompositionRequest(style="trance", seed=4))
    notes = lambda s: [(n.start, n.pitch) for n in s.track("Lead").notes]
    assert notes(a) == notes(b) and notes(a) != notes(c)


def test_user_key_bpm_progression():
    song, info = Arranger().compose(CompositionRequest(style="edm", key="Re mayor", bpm=128, progression="I-V-vi-IV"))
    assert info.key == "D major" and info.bpm == 128 and info.chord_names == ["D", "A", "Bm", "G"]
    assert song.key_signature == (2, 0)


def test_motif_quality_over_many_seeds():
    sc = Scale(9, "minor")
    prog = [parse_roman(r, sc) for r in "i-VI-III-VII".split("-")] * 2
    for seed in range(20):
        rng = random.Random(seed)
        mot = mel.generate_motif(rng, sc, prog[0], 72, {"density": 0.55, "syncopation": 0.45})
        ph = mel.compose_phrase(rng, sc, prog, mot, {"low": 62, "high": 86}, climax=True)
        m = mel.melody_metrics(ph, sc, lambda p: prog[min(p // 16, 7)])
        assert m["longest_scale_run"] <= 3, seed  # no hay escalas mecánicas de 5+ notas
        assert m["strong_beat_chord_tone_ratio"] >= 0.85
        assert m["in_scale_ratio"] >= 0.95  # se permiten notas de aproximación cromáticas
        assert m["big_leaps"] <= 1
        # repetición con variación: el compás 5 repite el ritmo del motivo
        bar = lambda b: [(n.pos % 16, n.dur) for n in ph if n.pos // 16 == b]
        assert bar(0) == bar(4)
        # el clímax (compás 7) contiene la nota más aguda o empata con ella
        assert max(n.pitch for n in ph if n.pos // 16 == 6) >= max(n.pitch for n in ph) - 2


def test_analysis_detects_key_and_roles():
    song, info = Arranger().compose(CompositionRequest(style="progressive_house", mood="nostalgico", seed=2))
    a = analyze_song(song)
    assert a["key"]["key"] in (info.key, a["key"]["relative_alternative"])
    assert a["roles"]["Bateria"] == "drums" and a["roles"]["Bajo"] == "bass"


def test_transforms_keep_what_was_asked():
    song, _ = Arranger().compose(CompositionRequest(style="trance", mood="epico", seed=2))
    new, log = reharmonize(song, mood="nostalgico")
    for t_old, t_new in zip(song.tracks, new.tracks):
        assert sorted(n.start for n in t_old.notes) == sorted(n.start for n in t_new.notes), t_old.name
    kr = keep_rhythm_new_pitches(song, "Lead", seed=9)
    old, nw = song.track("Lead").notes, kr.track("Lead").notes
    assert [(n.start, n.duration) for n in old] == [(n.start, n.duration) for n in nw]
    assert [n.pitch for n in old] != [n.pitch for n in nw]
    va = vary_answer(song, "Lead")
    tpb = song.ticks_per_bar()
    first_half = lambda s: [(n.start, n.pitch) for n in s.track("Lead").notes if (n.start // tpb) % 8 < 4]
    assert first_half(song) == first_half(va)


def test_change_mode_maps_degrees():
    song, info = Arranger().compose(CompositionRequest(style="edm", key="A major", progression="I-IV-V-I", seed=1))
    out = change_mode(song, "minor")
    pcs = {n.pitch % 12 for t in out.tracks if t.channel != 9 for n in t.notes}
    assert 1 not in pcs and 0 in pcs  # C# (3ª mayor de La) pasa a C
    assert detect_key(out)["mode"] == "minor" or detect_key(out)["relative_alternative"].endswith("minor")
