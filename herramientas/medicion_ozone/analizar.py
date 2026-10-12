"""Mide archivos procesados (por Ozone o por nuestros plugins) contra senal_prueba_ozone.wav.

Uso: python analizar.py procesado1.wav [procesado2.wav ...]   (escribe también procesado.json)

- Alinea por correlación (latencia).
- Detecta silencios de versiones de demostración: un tramo cuenta solo si ninguna ventana de 10 ms queda
  40 dB por debajo de la señal original; de las tres copias de cada medida se usa la primera limpia.
- Senos: ganancia y armónicos H2-H7 (dB respecto al fundamental) por frecuencia y nivel.
- Barrido: respuesta en frecuencia. Ruidos: cambio de centro (Mid) y lados (Side). Dos tonos: intermodulación.
"""
import json
import os
import subprocess
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", "44100", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def latency(ref, out, maxlag=8192):
    a = ref[: 44100 * 20].sum(1)
    b = out[: 44100 * 20].sum(1)
    n = 1 << int(np.ceil(np.log2(len(a) + maxlag)))
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    return int(np.argmax(np.abs(c[:maxlag])))


def clean(ref, out, s, e, win=441):
    """True si en [s, e) no hay ventanas silenciadas en la salida."""
    for i in range(s, e - win, win):
        r = np.sqrt(np.mean(ref[i:i + win] ** 2))
        o = np.sqrt(np.mean(out[i:i + win] ** 2))
        if r > 1e-4 and o < r * 0.01:
            return False
    return True


def harmonics(x, f, fs, nh=7):
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w))
    fr = np.fft.rfftfreq(len(x), 1 / fs)
    amp = []
    for h in range(1, nh + 1):
        fh = f * h
        if fh >= fs / 2 - 200:
            amp.append(np.nan)
            continue
        k = np.argmin(np.abs(fr - fh))
        amp.append(X[max(0, k - 3):k + 4].max() * 2 / w.sum())
    return np.array(amp)


def analyze(path):
    fs = 44100
    meta = json.load(open(os.path.join(HERE, "senal_prueba_ozone.json"), encoding="utf-8"))
    ref = load(os.path.join(HERE, "senal_prueba_ozone.wav"))
    out = load(path)
    lag = latency(ref, out)
    out = out[lag:]
    if len(out) < len(ref):
        out = np.vstack([out, np.zeros((len(ref) - len(out), 2))])
    rm, om = ref.mean(1), out.mean(1)
    res = {"archivo": os.path.basename(path), "latencia_muestras": lag, "senos": [], "silencios_evitados": 0}
    used = set()
    lost = 0
    for seg in meta["segmentos"]:
        key = (seg["tipo"], seg.get("f"), seg.get("nivel"))
        if key in used:
            continue
        s, e = seg["inicio"], seg["fin"]
        if not clean(rm, om, s, e):
            lost += 1
            continue
        used.add(key)
        if seg["tipo"] == "seno":
            mid = (s + e) // 2
            n = int(0.4 * fs)
            a_in = harmonics(rm[mid - n // 2: mid + n // 2], seg["f"], fs)
            a_out = harmonics(om[mid - n // 2: mid + n // 2], seg["f"], fs)
            res["senos"].append({
                "f": seg["f"], "nivel": seg["nivel"],
                "ganancia_db": float(20 * np.log10(a_out[0] / a_in[0])),
                "armonicos_db": [None if np.isnan(v) else float(20 * np.log10(max(v, 1e-12) / a_out[0])) for v in a_out[1:]],
            })
        elif seg["tipo"] == "barrido":
            X = np.fft.rfft(rm[s:e]); Y = np.fft.rfft(om[s:e])
            fr = np.fft.rfftfreq(e - s, 1 / fs)
            resp = {}
            for f in (30, 60, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 18000):
                k = (fr > f / 2 ** (1 / 12)) & (fr < f * 2 ** (1 / 12))
                resp[str(f)] = float(10 * np.log10(np.sum(np.abs(Y[k]) ** 2) / np.sum(np.abs(X[k]) ** 2)))
            res["respuesta_db"] = resp
        elif seg["tipo"].startswith("ruido"):
            Mi = (ref[s:e, 0] + ref[s:e, 1]) / 2; Si = (ref[s:e, 0] - ref[s:e, 1]) / 2
            Mo = (out[s:e, 0] + out[s:e, 1]) / 2; So = (out[s:e, 0] - out[s:e, 1]) / 2
            eps = 1e-12
            res[seg["tipo"]] = {
                "mid_db": float(10 * np.log10((np.mean(Mo ** 2) + eps) / (np.mean(Mi ** 2) + eps))),
                "side_db": float(10 * np.log10((np.mean(So ** 2) + eps) / (np.mean(Si ** 2) + eps))),
                "side_sobre_mid_salida_db": float(10 * np.log10((np.mean(So ** 2) + eps) / (np.mean(Mo ** 2) + eps))),
            }
        elif seg["tipo"] == "dos_tonos":
            x = om[s + 2000:e - 2000]
            w = np.hanning(len(x)); X = np.abs(np.fft.rfft(x * w)); fr = np.fft.rfftfreq(len(x), 1 / fs)
            def lev(f):
                k = np.argmin(np.abs(fr - f)); return X[max(0, k - 2):k + 3].max()
            side = max(lev(7000 - 60), lev(7000 + 60), lev(7000 - 120), lev(7000 + 120))
            res["intermodulacion_db"] = float(20 * np.log10(side / lev(7000)))
    res["silencios_evitados"] = lost
    return res


def report(r):
    print(f"\n=== {r['archivo']} ===  latencia {r['latencia_muestras']} muestras, copias descartadas por silencios: {r['silencios_evitados']}")
    print("  seno      nivel   ganancia   H2     H3     H4     H5     H6     H7  (dB respecto al fundamental)")
    for s in sorted(r["senos"], key=lambda x: (x["f"], x["nivel"])):
        h = "  ".join(f"{v:6.1f}" if v is not None else "   -  " for v in s["armonicos_db"])
        print(f"  {s['f']:>5} Hz {s['nivel']:>6.1f}  {s['ganancia_db']:+6.2f}   {h}")
    if "respuesta_db" in r:
        print("  respuesta: " + "  ".join(f"{k}:{v:+.2f}" for k, v in r["respuesta_db"].items()))
    for k in ("ruido_mono", "ruido_lateral", "ruido_estereo"):
        if k in r:
            v = r[k]
            print(f"  {k}: Mid {v['mid_db']:+.2f} dB, Side {v['side_db']:+.2f} dB, Side/Mid salida {v['side_sobre_mid_salida_db']:+.1f} dB")
    if "intermodulacion_db" in r:
        print(f"  intermodulación (bandas laterales de 7 kHz): {r['intermodulacion_db']:.1f} dB")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = analyze(p)
        report(r)
        json.dump(r, open(os.path.splitext(p)[0] + "_medidas.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
