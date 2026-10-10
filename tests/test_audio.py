import numpy as np
import pytest

from aiengineer.audio import loudness
from aiengineer.audio.analyzer import analyze_array
from aiengineer.audio.dynamics import analyze_dynamics, clipping_runs
from aiengineer.audio.musical import estimate_key, estimate_tempo
from aiengineer.audio.spectral import band_energies, find_resonances, masking_conflicts, stft_power
from aiengineer.audio.stereo import analyze_stereo
from aiengineer.audio.wavio import read_wav, write_wav
from tests.conftest import kick_track, sine


@pytest.mark.parametrize("bits,flt", [(16, False), (24, False), (32, False), (32, True)])
def test_wav_roundtrip(tmp_path, fs, bits, flt):
    x = np.stack([sine(440, 0.2, fs, 0.5), sine(660, 0.2, fs, 0.25)], axis=1)
    p = write_wav(tmp_path / "t.wav", x, fs, bits=bits, float_format=flt)
    y, info = read_wav(p)
    assert info.sample_rate == fs and info.channels == 2 and info.bits == bits
    assert np.max(np.abs(y - x)) < 2.0 / (1 << (bits - 1)) + 1e-7


def test_ebu_reference_sine():
    fs = 48000
    x = np.stack([sine(997, 20, fs, 10 ** (-23 / 20))] * 2, axis=1)
    m = loudness.measure(x, fs)
    assert abs(m["integrated_lufs"] + 23.0) < 0.1
    assert m["lra_lu"] < 0.5


def test_true_peak_intersample():
    fs = 48000
    # seno a fs/4 con fase de 45°: las muestras valen 0,707 pero la forma de onda llega a 1,0
    x = sine(fs / 4, 1.0, fs, 1.0, np.pi / 4)
    sample_peak = 20 * np.log10(np.max(np.abs(x)))
    m = loudness.measure(x, fs)
    assert sample_peak < -2.9
    assert abs(m["true_peak_dbtp"]) < 0.6


def test_lra_detects_dynamics():
    fs = 16000
    quiet = sine(500, 10, fs, 0.05)  # -26 dBFS: queda por encima de la puerta relativa de -20 LU
    loud = sine(500, 10, fs, 0.3)
    m = loudness.measure(np.concatenate([quiet, loud]), fs)
    assert 12 < m["lra_lu"] < 17  # diferencia real de 15,6 dB


def test_clipping_detection(fs):
    x = sine(40, 1, fs, 0.8)
    assert clipping_runs(x)["runs"] == 0
    assert clipping_runs(np.round(x * 32767) / 32768)["runs"] == 0
    assert clipping_runs(np.clip(sine(100, 1, fs, 1.5), -1, 1))["runs"] > 100
    assert clipping_runs(0.5 * np.clip(sine(100, 1, fs, 1.5), -1, 1))["runs"] > 100


def test_stereo_metrics(fs):
    s = sine(200, 2, fs, 0.5)
    same = analyze_stereo(np.stack([s, s], 1), fs)
    assert same["is_dual_mono"] and same["correlation"] == 1.0
    inv = analyze_stereo(np.stack([s, -s], 1), fs)
    assert inv["correlation"] < -0.99
    assert inv["mono_loudness_change_db"] is None or inv["mono_loudness_change_db"] < -20
    bal = analyze_stereo(np.stack([s, 0.5 * s], 1), fs)
    assert abs(bal["lr_balance_db"] - 6.02) < 0.1


def test_bands_and_resonance(fs):
    rng = np.random.default_rng(0)
    x = sine(1000, 2, fs, 0.5)
    f, _, p = stft_power(x, fs)
    assert band_energies(f, p)["medios"]["pct"] > 95
    noise = rng.standard_normal(fs * 3) * 0.05 + sine(2500, 3, fs, 0.05)
    f, _, p = stft_power(noise, fs)
    res = find_resonances(f, p)
    assert res and abs(res[0]["freq_hz"] - 2500) < 15


def test_masking(fs):
    a = sine(80, 3, fs, 0.5)
    b = sine(82, 3, fs, 0.5)
    c = sine(3000, 3, fs, 0.5)
    assert masking_conflicts(a, b, fs)
    assert not masking_conflicts(a, c, fs)


def test_tempo_estimation(fs):
    x = kick_track(128, 20, fs)
    t = estimate_tempo(x, fs)
    assert abs(t["bpm"] - 128) < 1.5


def test_key_estimation(fs):
    # Am - F - C - G con notas sostenidas
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]
    seg = []
    for _ in range(3):
        for ch in chords:
            seg.append(sum(sine(440 * 2 ** ((n - 69) / 12), 1.0, fs, 0.2) for n in ch + [ch[0] - 12]))
    k = estimate_key(np.concatenate(seg), fs)
    assert k["key"] in ("Am", "C")  # relativos: la ambigüedad se informa con 'alternative'


def test_analyzer_observations(fs):
    x = np.clip(sine(60, 6, fs, 1.4), -1, 1)
    rep = analyze_array(np.stack([x, x], 1), fs, musical=False)
    kinds = [o["text"] for o in rep["observations"]]
    assert any("recortadas" in t for t in kinds)
    d = analyze_dynamics(sine(100, 2, fs, 0.5), fs)
    assert abs(d["crest_factor_db"] - 3.01) < 0.1
