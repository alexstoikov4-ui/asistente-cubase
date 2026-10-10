from pathlib import Path

import numpy as np

from aiengineer.app import Engine
from aiengineer.audio.wavio import write_wav
from aiengineer.midi.smf import read_midi
from tests.conftest import kick_track, sine


def test_compose_order_end_to_end(tmp_path):
    eng = Engine(tmp_path / "home")
    eng.open_project("E2E", create=True)
    eng.set_mode("semi")
    plan = eng.interpret("Compón un drop de trance épico en Fa# menor a 136 bpm")
    assert [t.action for t in plan.tasks] == ["compose", "render_preview"]
    tasks = eng.run(plan, approve=lambda t: True)
    assert [t.status for t in tasks] == ["verified", "verified"]
    mid = Path(eng.context["midi_file"])
    song = read_midi(mid)
    assert abs(song.bpm - 136) < 0.01 and song.tracks
    assert any(Path(a).parent.name == "exchange" for a in tasks[0].result["artifacts"])
    hist = eng.oplog.recent(10, project="E2E")
    assert {h["status"] for h in hist} >= {"verified"}
    # la memoria se conserva entre sesiones
    eng2 = Engine(tmp_path / "home")
    eng2.open_project("E2E")
    assert eng2.project.memory["style"] == "trance" and eng2.context["midi_file"] == str(mid)


def test_permissions_and_stop(tmp_path):
    eng = Engine(tmp_path / "home")
    eng.open_project("P", create=True)
    plan = eng.interpret("Compón un drop progressive house")
    tasks = eng.run(plan, approve=lambda t: False)  # modo asistido: pide aprobación y se rechaza
    assert all(t.status == "skipped" for t in tasks)
    eng.set_permission("compose", "deny")
    eng.set_mode("autonomous")
    assert eng.run(eng.interpret("Compón un drop progressive house"))[0].status == "skipped"
    eng.set_permission("compose", "allow")
    plan = eng.interpret("Compón un drop progressive house")
    orig = eng.stop_event.clear
    eng.stop_event.clear = lambda: (orig(), eng.stop_event.set())  # se pulsa "detener" nada más empezar
    assert all(t.status == "cancelled" for t in eng.run(plan))


def test_feedback_changes_next_cycle(tmp_path, fs):
    eng = Engine(tmp_path / "home")
    eng.open_project("F", create=True)
    eng.set_mode("autonomous")
    eng.run(eng.interpret("La melodía sigue siendo demasiado lineal"))
    assert "melodia_menos_lineal" in eng.project.memory["user_preferences"]
    t = eng.run(eng.interpret("Compón un drop progressive house"))[0]
    assert "leap_prob" in t.result["summary"]  # la preferencia se aplicó de verdad


def test_master_and_iterate(tmp_path, fs):
    eng = Engine(tmp_path / "home")
    eng.open_project("M", create=True)
    eng.set_mode("autonomous")
    x = np.stack([kick_track(128, 10, fs, 0.5) + sine(110, 10, fs, 0.1)] * 2, 1) * 0.5
    src = write_wav(tmp_path / "mix.wav", x, fs)
    eng.set_sources(str(src))
    tasks = eng.run(eng.interpret("Masteriza el tema para club"))
    assert tasks[0].status == "verified"
    ev = eng.run(eng.interpret("sigue trabajando hasta mejorarlo"))[0]
    assert ev.status in ("done", "verified") and "Medido" in ev.result["summary"]
