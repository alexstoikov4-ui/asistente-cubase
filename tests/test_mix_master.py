import numpy as np
import pytest

from aiengineer.audio import loudness
from aiengineer.audio.wavio import read_wav, write_wav
from aiengineer.master import processing as proc
from aiengineer.master.engine import MasterSettings, master_file, plugin_chain_suggestion
from aiengineer.mix.engine import guess_role, plan_mix
from tests.conftest import kick_track, sine


@pytest.fixture
def stems(tmp_path, fs):
    d = tmp_path / "stems"
    d.mkdir()
    dur = 8
    kick = kick_track(126, dur, fs, 0.9)
    t = np.arange(int(dur * fs)) / fs
    bass = 0.4 * np.sign(np.sin(2 * np.pi * 55 * t)) * (np.sin(2 * np.pi * 126 / 60 * t) > 0)
    rng = np.random.default_rng(1)
    hats = np.diff(rng.standard_normal(len(t) + 1)) * 0.05 + 0.002 * np.sin(2 * np.pi * 50 * t)  # con zumbido
    pad = 0.2 * (sine(220, dur, fs) + sine(277.2, dur, fs) + sine(329.6, dur, fs))
    for name, x in (("Kick", kick), ("Bass", bass), ("Hats", hats), ("Pad", pad)):
        write_wav(d / f"{name}.wav", np.stack([x, x], 1), fs)
    return d


def test_roles():
    assert guess_role("Kick_01") == "kick" and guess_role("Subgrave") == "sub" and guess_role("Lead Synth") == "lead"


def test_mix_plan(stems):
    plan = plan_mix(stems, bpm=126)
    by = {s["name"]: s for s in plan["stems"]}
    assert by["Kick"]["role"] == "kick" and by["Bass"]["role"] == "bass"
    hpf = [a for a in by["Hats"]["actions"] if a["type"] == "eq" and a["band"] == "highpass"]
    assert hpf and 50 < hpf[0]["freq"] <= 300  # elimina el zumbido de 50 Hz
    assert not any(a.get("band") == "highpass" for a in by["Pad"]["actions"])  # el pad tiene contenido a 220 Hz
    assert not any(a.get("band") == "highpass" for a in by["Bass"]["actions"])  # nunca filtrar el bajo
    assert any(c["solution"]["tool"] in ("sidechain", "eq") for c in plan["conflicts"] if {c["a"], c["b"]} == {"Kick", "Bass"})
    mix = plan["_preview"]["mix"]
    assert 20 * np.log10(np.max(np.abs(mix))) <= -5.9  # margen de 6 dB para el máster
    fb = plan_mix(stems, bpm=126, feedback=["El bajo ha perdido cuerpo"])
    assert {s["name"]: s for s in fb["stems"]}["Bass"]["gain_db"] > by["Bass"]["gain_db"]


def test_limiter_respects_ceiling(fs):
    x = np.stack([kick_track(128, 6, fs, 0.9)] * 2, 1) + 0.05 * np.random.default_rng(0).standard_normal((6 * fs, 2))
    y, st = proc.limit(x, fs, -1.0, input_gain_db=9.0)
    tp = loudness.measure(y, fs)["true_peak_dbtp"]
    assert tp <= -0.9 and st["max_gr_db"] > 3


def test_resample_and_dither(fs):
    x = sine(1000, 1, fs, 0.5)
    y = proc.resample_fft(x, fs, 48000)
    assert abs(len(y) - 48000) <= 2
    spec = np.abs(np.fft.rfft(y[1000:-1000]))
    f = np.fft.rfftfreq(len(y) - 2000, 1 / 48000)
    assert abs(f[np.argmax(spec)] - 1000) < 2
    d = proc.tpdf_dither(np.zeros(1000), 16)
    assert 0 < np.max(np.abs(d)) <= 2 / 32768


def test_master_file_verified(tmp_path, fs):
    x = np.stack([kick_track(128, 12, fs, 0.5) + sine(110, 12, fs, 0.1)] * 2, 1) * 0.5
    src = write_wav(tmp_path / "mix.wav", x, fs)
    res = master_file(src, tmp_path / "m.wav", MasterSettings(target="streaming", bits=16, sample_rate=48000))
    assert res.verified and res.output
    y, info = read_wav(res.output)
    assert info.sample_rate == 48000 and info.bits == 16
    assert res.after["loudness"]["true_peak_dbtp"] <= -0.9
    assert res.after["loudness"]["integrated_lufs"] <= -14 + 0.5
    assert any(s["stage"] == "limit" for s in res.stages)


def test_plugin_chain_uses_installed_only():
    chain = [{"stage": "eq", "type": "peak", "freq": 300, "gain_db": -1, "why": ""}, {"stage": "limit", "why": ""}]
    out = plugin_chain_suggestion(chain, {"eq": ["FabFilter Pro-Q 3"], "limiter": ["Limiter 6", "L2"]})
    assert out[0]["plugin"] == "FabFilter Pro-Q 3" and out[1]["plugin"] == "Limiter 6"
    none = plugin_chain_suggestion(chain, {})
    assert "no se ha encontrado" in none[0]["plugin"]
