"""Sintetizador mínimo para previsualizar un MIDI sin Cubase (y para generar material de prueba).

No pretende sonar como los instrumentos del usuario: osciladores simples con envolvente, una
batería sintética básica y paneo por pista. Sirve para escuchar la composición y para analizarla.
"""
from __future__ import annotations

import numpy as np

from ..midi.smf import Song

PAN = {"lead": 0.0, "counter": -0.3, "chords": 0.25, "pad": -0.2, "arp": 0.4, "pluck": -0.4, "bass": 0.0,
       "sub": 0.0, "drums": 0.0}
GAIN = {"lead": 0.22, "counter": 0.12, "chords": 0.07, "pad": 0.05, "arp": 0.08, "pluck": 0.09, "bass": 0.25,
        "sub": 0.3, "drums": 0.6}


def _tick_to_s(song: Song):
    spb = 60.0 / song.bpm
    return lambda t: t / song.ppq * spb


def _osc(kind: str, freq: float, n: int, fs: int) -> np.ndarray:
    t = np.arange(n) / fs
    ph = (freq * t) % 1.0
    if kind == "sine":
        return np.sin(2 * np.pi * freq * t)
    if kind == "saw":
        # sierra con algo de suavizado (suma de 8 armónicos para limitar el aliasing)
        return sum(np.sin(2 * np.pi * k * freq * t) / k for k in range(1, 9) if k * freq < fs / 2) * 0.6
    if kind == "square":
        return np.sign(np.sin(2 * np.pi * freq * t)) * 0.5
    return 2 * np.abs(2 * ph - 1) - 1


def _env(n: int, fs: int, a=0.005, d=0.1, s=0.7, r=0.05) -> np.ndarray:
    e = np.full(n, s)
    na, nd, nr = int(a * fs), int(d * fs), int(r * fs)
    na = min(na, n)
    e[:na] = np.linspace(0, 1, na, endpoint=False) if na else e[:na]
    nd2 = min(nd, max(0, n - na))
    if nd2:
        e[na:na + nd2] = np.linspace(1, s, nd2)
    if nr and n > nr:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


def _drum(note: int, fs: int, rng) -> np.ndarray:
    if note == 36:  # bombo: seno con barrido de tono
        n = int(0.35 * fs)
        t = np.arange(n) / fs
        f = 50 + 120 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(f) / fs) * np.exp(-t * 9)
    if note in (38, 39):  # caja / palmada: ruido filtrado con cuerpo
        n = int(0.2 * fs)
        t = np.arange(n) / fs
        return (rng.standard_normal(n) * 0.6 + np.sin(2 * np.pi * 190 * t) * 0.4) * np.exp(-t * 22)
    if note in (42, 46, 51, 70, 49):  # platos: ruido paso alto (diferencia)
        n = int((0.4 if note in (46, 49) else 0.06) * fs)
        x = np.diff(rng.standard_normal(n + 1)) * 0.3
        return x * np.exp(-np.arange(n) / fs * (8 if note in (46, 49) else 60))
    n = int(0.25 * fs)  # toms
    t = np.arange(n) / fs
    return np.sin(2 * np.pi * (80 + (note - 45) * 15) * t) * np.exp(-t * 12)


def render(song: Song, fs: int = 44100, tail_s: float = 1.0, seed: int = 0) -> np.ndarray:
    to_s = _tick_to_s(song)
    total = int((to_s(song.length_ticks()) + tail_s) * fs)
    out = np.zeros((total, 2))
    rng = np.random.default_rng(seed)
    cache: dict = {}
    for tr in song.tracks:
        role = tr.role or ("drums" if tr.channel == 9 else "lead")
        pan = PAN.get(role, 0.0)
        g = GAIN.get(role, 0.1)
        lg, rg = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        kind = {"sub": "sine", "bass": "saw", "pad": "tri", "chords": "saw", "lead": "saw", "arp": "square",
                "pluck": "tri", "counter": "tri"}.get(role, "saw")
        for n in tr.notes:
            start = int(to_s(n.start) * fs)
            if start >= total:
                continue
            vel = n.velocity / 127
            if tr.channel == 9:
                key = n.pitch
                if key not in cache:
                    cache[key] = _drum(n.pitch, fs, rng)
                sig = cache[key] * vel * g
            else:
                length = max(1, int(to_s(n.duration) * fs))
                freq = 440.0 * 2 ** ((n.pitch - 69) / 12)
                rel = 0.3 if role == "pad" else 0.04
                env = _env(length + int(rel * fs), fs, a=0.08 if role == "pad" else 0.004,
                           d=0.15 if role in ("pluck", "arp") else 0.1, s=0.25 if role == "pluck" else 0.7, r=rel)
                sig = _osc(kind, freq, len(env), fs) * env * vel * g
            end = min(total, start + len(sig))
            out[start:end, 0] += sig[:end - start] * lg
            out[start:end, 1] += sig[:end - start] * rg
    return out
