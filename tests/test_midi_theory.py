import pytest

from aiengineer.midi.smf import MidiError, Note, Song, Track, read_midi, validate_roundtrip, write_midi
from aiengineer.theory.core import (Scale, chord_from_name, key_signature, parse_key, parse_note, parse_roman,
                                    voice_lead, voicings)


def test_smf_roundtrip(tmp_path):
    s = Song(ppq=480, tempos=[(0, 126.0), (1920 * 8, 128.0)], markers=[(0, "Intro"), (1920 * 4, "Drop")],
             key_signature=(0, 1))
    s.tracks = [Track("Lead", 0, 81, [Note(0, 240, 72, 100), Note(240, 240, 74, 90), Note(480, 960, 76, 110)]),
                Track("Bateria", 9, None, [Note(0, 120, 36, 120), Note(480, 120, 36, 120)])]
    r = validate_roundtrip(s, tmp_path / "a.mid")
    assert r["ok"], r
    back = read_midi(tmp_path / "a.mid")
    assert back.markers == [(0, "Intro"), (7680, "Drop")]
    assert [t for t, _ in back.tempos] == [0, 15360] and abs(back.tempos[1][1] - 128) < 0.01
    assert back.track("Bateria").channel == 9 and back.key_signature == (0, 1)


def test_smf_running_status(tmp_path):
    # pista escrita a mano con running status y note-on de velocidad 0 como note-off
    trk = bytes([0x00, 0x90, 60, 100, 0x60, 60, 0, 0x00, 62, 90, 0x60, 62, 0, 0x00, 0xFF, 0x2F, 0x00])
    data = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + (96).to_bytes(2, "big")
    data += b"MTrk" + len(trk).to_bytes(4, "big") + trk
    p = tmp_path / "rs.mid"
    p.write_bytes(data)
    s = read_midi(p)
    assert [(n.start, n.duration, n.pitch) for n in s.tracks[0].notes] == [(0, 96, 60), (96, 96, 62)]


def test_smf_rejects_invalid(tmp_path):
    with pytest.raises(MidiError):
        write_midi(Song(tracks=[Track("x", 0, None, [Note(0, 0, 60)])]), tmp_path / "x.mid")
    (tmp_path / "bad.mid").write_bytes(b"nope")
    with pytest.raises(MidiError):
        read_midi(tmp_path / "bad.mid")


def test_notes_keys():
    assert parse_note("C#") == 1 and parse_note("Db") == 1 and parse_note("Sol") == 7 and parse_note("sib") == 10
    assert parse_key("La menor") == (9, "minor") and parse_key("F#m") == (6, "minor")
    assert parse_key("re mayor") == (2, "major") and parse_key("D dorico") == (2, "dorian")
    assert key_signature(9, "minor") == (0, 1) and key_signature(2, "major") == (2, 0)
    assert key_signature(5, "major") == (-1, 0)


def test_roman_numerals():
    am, c = Scale(9, "minor"), Scale(0, "major")
    assert parse_roman("VI", am).name() == "F" and parse_roman("VII", am).name() == "G"
    assert parse_roman("i", am).name() == "Am"
    assert parse_roman("bVII", c).name() == "A#"
    v_of_v = parse_roman("V/V", c)
    assert v_of_v.name() == "D" and "secondary_dominant" in v_of_v.tags
    iv = parse_roman("iv", c)
    assert iv.name() == "Fm" and "borrowed" in iv.tags
    assert parse_roman("V/3", c).name() == "G/B"
    assert parse_roman("IVmaj7", c).name() == "Fmaj7"
    assert parse_roman("bII", am).name() == "A#"
    with pytest.raises(ValueError):
        parse_roman("X", c)


def test_chord_names():
    assert chord_from_name("Am7").pcs == [9, 0, 4, 7]
    assert chord_from_name("G/B").bass == 11


def test_voice_leading_is_smooth():
    c = Scale(0, "major")
    prog = [parse_roman(r, c) for r in ["I", "vi", "IV", "V", "I"]]
    prev = None
    for ch in prog:
        v = voice_lead(prev, ch, 52, 79, 4)
        assert all(p % 12 in ch.pcs for p in v)
        assert all(b - a <= 12 for a, b in zip(v, v[1:]))
        if prev:
            assert sum(abs(a - b) for a, b in zip(prev, v)) <= 10
        prev = v
    assert voicings(chord_from_name("Fm"), 55, 76, 4)  # duplica la quinta cuando no cabe la fundamental
