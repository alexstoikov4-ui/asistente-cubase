"""Genera senal_prueba_ozone.wav: señal de medición para comparar AIE Exciter / AIE Imager con Ozone.

Estéreo, 44,1 kHz, 32 bits coma flotante, 50 s. Tramos (los tiempos los usa el analizador):
  1,0 s        impulso (latencia y fase)
  2-6 s        seno 100 Hz a -24, -12, -6 y 0 dBFS (1 s cada nivel)
  7-11 s       seno 1 kHz a los mismos niveles
  12-16 s      seno 5 kHz a los mismos niveles
  17-27 s      barrido logarítmico 20 Hz - 20 kHz a -12 dBFS
  28-32 s      golpes de batería sintéticos (transitorios)
  33-36 s      ruido rosa mono (L = R) a -18 dBFS
  37-40 s      ruido rosa solo lateral (L = -R) a -24 dBFS
  41-44 s      ruido rosa estéreo decorrelado a -18 dBFS
  45-49 s      dos tonos 60 Hz + 7 kHz (intermodulación) a -12 dBFS
"""
import os
import wave
import numpy as np

FS = 44100
DUR = 50.0
rng = np.random.default_rng(1234)


def db(x):
    return 10 ** (x / 20)


def pink(n):
    # ruido rosa por filtrado del espectro
    w = rng.standard_normal(n)
    f = np.fft.rfft(w)
    k = np.arange(len(f))
    k[0] = 1
    f /= np.sqrt(k)
    p = np.fft.irfft(f, n)
    return p / np.sqrt(np.mean(p ** 2))


def fade(x, ms=10):
    n = int(FS * ms / 1000)
    w = np.ones(len(x))
    w[:n] = np.linspace(0, 1, n)
    w[-n:] = np.linspace(1, 0, n)
    return x * w


def main():
    n = int(FS * DUR)
    L = np.zeros(n)
    R = np.zeros(n)
    t = np.arange(n) / FS

    def put(start, sig_l, sig_r=None):
        i = int(start * FS)
        L[i:i + len(sig_l)] += sig_l
        R[i:i + len(sig_l)] += sig_l if sig_r is None else sig_r

    imp = np.zeros(8)
    imp[0] = db(-6)
    put(1.0, imp)
    for start, f in ((2, 100), (7, 1000), (12, 5000)):
        for j, lv in enumerate((-24, -12, -6, 0)):
            m = int(FS * 0.95)
            s = db(min(lv, -0.1)) * np.sin(2 * np.pi * f * np.arange(m) / FS)  # 0 dBFS -> -0,1 para no recortar
            put(start + j, fade(s))
    m = int(FS * 10)
    tt = np.arange(m) / FS
    f0, f1 = 20.0, 20000.0
    k = np.log(f1 / f0)
    sweep = db(-12) * np.sin(2 * np.pi * f0 * 10 / k * (np.exp(tt / 10 * k) - 1))
    put(17, fade(sweep, 50))
    # batería: bombo + caja + charles cada 0,25 s
    m = int(FS * 4)
    tt = np.arange(m) / FS
    tb = np.mod(tt, 0.5)
    kick = 0.8 * np.exp(-tb * 18) * np.sin(2 * np.pi * (50 + 120 * np.exp(-tb * 40)) * tb)
    nz = rng.standard_normal(m)
    ts = np.mod(tt + 0.25, 0.5)
    snare = 0.35 * np.exp(-ts * 30) * nz
    th = np.mod(tt, 0.125)
    hat = 0.12 * np.exp(-th * 90) * np.diff(np.concatenate([[0], nz]))
    drums = kick + snare + hat
    drums *= db(-3) / np.abs(drums).max()  # pico de los golpes a -3 dBFS
    put(28, fade(drums))
    m = int(FS * 3)
    put(33, fade(db(-18) * pink(m)))
    s = fade(db(-24) * pink(m))
    put(37, s, -s)
    put(41, fade(db(-18) * pink(m)), fade(db(-18) * pink(m)))
    m = int(FS * 4)
    tt = np.arange(m) / FS
    two = db(-12) * (0.8 * np.sin(2 * np.pi * 60 * tt) + 0.2 * np.sin(2 * np.pi * 7000 * tt))
    put(45, fade(two))
    data = np.stack([L, R], 1).astype(np.float32)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "senal_prueba_ozone.wav")
    # WAV de 32 bits en coma flotante (formato 3)
    with open(out, "wb") as f:
        raw = data.tobytes()
        f.write(b"RIFF")
        f.write((36 + len(raw)).to_bytes(4, "little"))
        f.write(b"WAVEfmt ")
        f.write((16).to_bytes(4, "little"))
        f.write((3).to_bytes(2, "little"))
        f.write((2).to_bytes(2, "little"))
        f.write(FS.to_bytes(4, "little"))
        f.write((FS * 8).to_bytes(4, "little"))
        f.write((8).to_bytes(2, "little"))
        f.write((32).to_bytes(2, "little"))
        f.write(b"data")
        f.write(len(raw).to_bytes(4, "little"))
        f.write(raw)
    print("escrito", out, f"{os.path.getsize(out) / 1e6:.1f} MB, pico {np.abs(data).max():.3f}")


if __name__ == "__main__":
    main()
