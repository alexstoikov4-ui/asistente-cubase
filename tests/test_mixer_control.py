import math

from aiengineer.app import Engine
from aiengineer.cubase.fake_cubase import FakeCubase
from aiengineer.cubase.mackie import MackieSurface, parse_db, parse_pan
from aiengineer.cubase.mixer_control import MixerControl
from aiengineer.nlp.intents import parse

NAMES = ["Kick", "Bajo", "Clap", "Hats", "Pad", "Lead", "Pluck", "Arp", "Sub", "FX"]


def make(show_values=True, tmp_path=None):
    cub = FakeCubase(NAMES, show_values=show_values)
    surf = MackieSurface(cub, cub.to_surface)
    return cub, MixerControl(surf, (tmp_path / "curve.json") if tmp_path else None, settle=0)


def test_parsers():
    assert parse_db("-6.02") == -6.02 and parse_db("-oo") == float("-inf") and parse_db("Pan") is None
    assert parse_pan("C") == 0 and parse_pan("L30") == -30 and parse_pan("R12") == 12


def test_connect_and_track_names(tmp_path):
    cub, m = make(tmp_path=tmp_path)
    r = m.connect()
    assert r.status == "verified" and "Kick" in r.message
    assert m.track_names() == NAMES  # recorre los bancos de 8
    assert m.find_track("sub") == 9 and m.find_track("lead") == 6


def test_set_volume_converges_and_learns(tmp_path):
    cub, m = make(tmp_path=tmp_path)
    m.connect()
    r = m.set_volume(2, -6.0)
    assert r.status == "verified" and abs(cub.tracks[1].db + 6.0) <= 0.3
    r = m.set_volume(9, -12.5)  # pista del segundo banco
    assert r.status == "verified" and abs(cub.tracks[8].db + 12.5) <= 0.3
    assert (tmp_path / "curve.json").exists()  # la curva aprendida se guarda
    cub.tracks[0].db = -3.0
    r = m.change_volume(1, -2.0)
    assert r.status == "verified" and abs(cub.tracks[0].db + 5.0) <= 0.3


def test_unverified_without_display(tmp_path):
    cub, m = make(show_values=False, tmp_path=tmp_path)
    r = m.set_volume(1, -6.0)
    assert r.status == "unverified"  # nunca dice 'hecho' sin poder comprobarlo


def test_mute_solo_pan(tmp_path):
    cub, m = make(tmp_path=tmp_path)
    m.connect()
    assert m.set_button("mute", 5, True).status == "verified" and cub.tracks[4].mute
    assert m.set_button("mute", 5, True).status == "verified" and cub.tracks[4].mute  # no lo vuelve a apagar
    assert m.set_button("mute", 5, False).status == "verified" and not cub.tracks[4].mute
    assert m.set_button("solo", 1, True).status == "verified" and cub.tracks[0].solo
    r = m.set_pan(4, 30)
    assert r.status == "verified" and abs(cub.tracks[3].pan - 30) <= 1
    assert m.set_pan(4, 0).status == "verified" and cub.tracks[3].pan == 0


def test_orders_end_to_end(tmp_path):
    eng = Engine(tmp_path / "home")
    eng.open_project("MX", create=True)
    eng.set_mode("autonomous")
    cub, m = make(tmp_path=tmp_path)
    eng.context["_mixer"] = m
    assert [i.name for i in parse("Sube el bajo 2 dB")] == ["mixer_control"]
    t = eng.run(eng.interpret("Pon el kick a -8 dB"))[0]
    assert t.status == "verified" and abs(cub.tracks[0].db + 8) <= 0.3
    t = eng.run(eng.interpret("Silencia el pad"))[0]
    assert t.status == "verified" and cub.tracks[4].mute
    t = eng.run(eng.interpret("Panea los hats 25 a la izquierda"))[0]
    assert t.status == "verified" and abs(cub.tracks[3].pan + 25) <= 1
    t = eng.run(eng.interpret("Sube la guitarra 3 dB"))[0]
    assert t.status == "failed" and "No encuentro" in t.result["summary"] and "Kick" in t.result["summary"]
    # aplicar los niveles de un plan de mezcla en Cubase
    eng.context["last_mix_plan"] = {"stems": [{"name": "Kick", "gain_db": -1.0}, {"name": "Bajo", "gain_db": 2.0},
                                              {"name": "Voz", "gain_db": -3.0}]}
    before = cub.tracks[1].db
    t = eng.run(eng.interpret("Aplica los niveles del plan de mezcla en Cubase"))[0]
    assert t.status == "verified" and abs(cub.tracks[1].db - (before + 2.0)) <= 0.3
    assert "Voz: no hay pista" in t.result["summary"]


def test_without_loopmidi_explains(tmp_path):
    eng = Engine(tmp_path / "home")
    eng.set_mode("autonomous")
    t = eng.run(eng.interpret("Sube el bajo 2 dB"))[0]
    assert t.status == "skipped" and "loopMIDI" in t.result["summary"]


def test_change_volume_without_display_uses_fader_position(tmp_path):
    cub, m = make(show_values=False, tmp_path=tmp_path)
    cub.to_surface.send(bytes([0xE0, 0x00, 0x6C]))  # Cubase informa de la posición del fader 1
    m.s.poll(0.05)
    r = m.change_volume(1, 3.0)
    assert r.status == "unverified" and "movido" in r.message
    assert cub.tracks[0].db > -999


def test_pan_without_display_uses_ring_leds(tmp_path):
    cub, m = make(show_values=False, tmp_path=tmp_path)
    cub.tracks[3].pan = -50
    r = m.set_pan(4, 30)
    assert r.status == "unverified" and 20 <= cub.tracks[3].pan <= 40
    r = m.set_pan(4, 0)
    assert r.status == "verified" and cub.tracks[3].pan == 0


def test_change_volume_asks_cubase_for_fader_when_no_text(tmp_path):
    # Como el Cubase del usuario: no envía texto de pantalla ni posiciones por iniciativa propia.
    cub = FakeCubase(NAMES, send_lcd=False)
    m = MixerControl(MackieSurface(cub, cub.to_surface), tmp_path / "curve.json", settle=0)
    cub.tracks[1].db = -10.0
    r = m.change_volume(2, 3.0)  # sin posición conocida: FLIP dos veces para que Cubase la reenvíe
    assert r.status == "unverified" and "movido" in r.message
    assert -10.0 < cub.tracks[1].db < -4.0  # ha subido (la curva de la app es solo aproximada)
    assert not cub.flipped
    # la siguiente orden relativa parte de donde la dejó la app
    before = cub.tracks[1].db
    assert m.change_volume(2, -3.0).status == "unverified" and cub.tracks[1].db < before
