"""Genera senal_prueba_ozone.wav y su mapa senal_prueba_ozone.json.

Señal de medición para comparar AIE Exciter / AIE Imager con Ozone. Pensada para versiones de demostración
que meten silencios: cada medida aparece TRES veces en momentos distintos y el analizador usa las copias que
no caen en un silencio. Estéreo, 44,1 kHz, 32 bits coma flotante, ~70 s.

Medidas: impulsos (latencia), senos de 100 Hz / 1 kHz / 5 kHz a -24, -12, -6 y -0,1 dBFS (0,6 s), barridos
20 Hz - 20 kHz a -12 dBFS (4 s), golpes de batería (2 s), ruido rosa mono / lateral / estéreo (1,5 s) y dos
tonos 60 Hz + 7 kHz (1 s).
"""
import json
import os
import numpy as np

FS = 44100
rng = np.random.default_rng(1234)
HERE = os.path.dirname(os.path.abspath(__file__))


def db(x):
    return 10 ** (x / 20)


def pink(n):
    w = rng.standard_normal(n)
    f = np.fft.rfft(w)
    k = np.arange(len(f)).astype(float)
    k[0] = 1
    f /= np.sqrt(k)
    p = np.fft.irfft(f, n)
    return p / np.sqrt(np.mean(p ** 2))


def fade(x, ms=8):
    n = int(FS * ms / 1000)
    w = np.ones(len(x))
    w[:n] = np.linspace(0, 1, n)
    w[-n:] = np.linspace(1, 0, n)
    return x * w


def items():
    """Lista de (tipo, parámetros, L, R)."""
    out = []
    for f in (100, 1000, 5000):
        for lv in (-24, -12, -6, -0.1):
            m = int(FS * 0.6)
            s = fade(db(lv) * np.sin(2 * np.pi * f * np.arange(m) / FS))
            out.append(("seno", {"f": f, "nivel": lv}, s, s))
    m = int(FS * 4)
    tt = np.arange(m) / FS
    k = np.log(20000 / 20)
    sw = fade(db(-12) * np.sin(2 * np.pi * 20 * 4 / k * (np.exp(tt / 4 * k) - 1)), 30)
    out.append(("barrido", {"f0": 20, "f1": 20000, "dur": 4, "nivel": -12}, sw, sw))
    m = int(FS * 2)
    tt = np.arange(m) / FS
    tb = np.mod(tt, 0.5)
    kick = 0.8 * np.exp(-tb * 18) * np.sin(2 * np.pi * (50 + 120 * np.exp(-tb * 40)) * tb)
    nz = rng.standard_normal(m)
    snare = 0.35 * np.exp(-np.mod(tt + 0.25, 0.5) * 30) * nz
    hat = 0.12 * np.exp(-np.mod(tt, 0.125) * 90) * np.diff(np.concatenate([[0], nz]))
    dr = kick + snare + hat
    dr = fade(dr * db(-3) / np.abs(dr).max())
    out.append(("bateria", {}, dr, dr))
    m = int(FS * 1.5)
    s = fade(db(-18) * pink(m))
    out.append(("ruido_mono", {"nivel": -18}, s, s))
    s = fade(db(-24) * pink(m))
    out.append(("ruido_lateral", {"nivel": -24}, s, -s))
    out.append(("ruido_estereo", {"nivel": -18}, fade(db(-18) * pink(m)), fade(db(-18) * pink(m))))
    m = int(FS * 1.0)
    tt = np.arange(m) / FS
    s = fade(db(-12) * (0.8 * np.sin(2 * np.pi * 60 * tt) + 0.2 * np.sin(2 * np.pi * 7000 * tt)))
    out.append(("dos_tonos", {"f1": 60, "f2": 7000, "nivel": -12}, s, s))
    return out


def main():
    base = items()
    gap = int(FS * 0.15)
    segs, chunks = [], []
    pos = 0

    def add(kind, prm, l, r, copy):
        nonlocal pos
        segs.append({"tipo": kind, **prm, "copia": copy, "inicio": pos, "fin": pos + len(l)})
        chunks.append((pos, l, r))
        pos += len(l) + gap

    for copy in range(3):
        imp = np.zeros(int(FS * 0.2))
        imp[0] = db(-6)
        add("impulso", {}, imp, imp, copy)
        order = list(range(len(base)))
        rng.shuffle(order)  # cada copia en otro orden: un silencio no borra lo mismo dos veces
        for i in order:
            kind, prm, l, r = base[i]
            add(kind, prm, l, r, copy)
    n = pos + FS
    L = np.zeros(n)
    R = np.zeros(n)
    for p, l, r in chunks:
        L[p:p + len(l)] += l
        R[p:p + len(r)] += r
    data = np.stack([L, R], 1).astype(np.float32)
    wav = os.path.join(HERE, "senal_prueba_ozone.wav")
    raw = data.tobytes()
    with open(wav, "wb") as f:
        f.write(b"RIFF" + (36 + len(raw)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little"))
        f.write((3).to_bytes(2, "little") + (2).to_bytes(2, "little") + FS.to_bytes(4, "little"))
        f.write((FS * 8).to_bytes(4, "little") + (8).to_bytes(2, "little") + (32).to_bytes(2, "little"))
        f.write(b"data" + len(raw).to_bytes(4, "little") + raw)
    with open(os.path.join(HERE, "senal_prueba_ozone.json"), "w", encoding="utf-8") as f:
        json.dump({"fs": FS, "segmentos": segs}, f, indent=1, ensure_ascii=False)
    print(f"escrito {wav}: {n / FS:.1f} s, pico {np.abs(data).max():.3f}, {len(segs)} segmentos")


if __name__ == "__main__":
    main()
