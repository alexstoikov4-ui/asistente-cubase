"""Genera plugins_vst/aieexciter/ozone_curves.h a partir de los renders de Ozone 7 (Exciter, Amount 5 y 15).

Uso: python generar_curvas.py carpeta_con_los_wav
Nombres esperados en la carpeta (basta con que el nombre contenga la clave): tube_al_5 / tube, tape_al_5 / tape,
retro_al_5 / retro, warm_al_5 / warm_al_15, triode_al_5 / triode, dual_triode_al_5 / dual_triode.

Lo medido (senos de 100 Hz y 1 kHz a 4 niveles):
- Cada perfil es una curva fija (sin memoria) seguida de un paso alto de 1er orden: 15 Hz en Tube, Retro y
  Warm, ~50 Hz en Triode y Dual Triode, ~24 Hz en Tape. Se deshace el paso alto y se obtiene la curva y=f(x).
- Amount empuja la señal contra la curva (ganancia de entrada) y reescala la salida: F_A(x) = c(A) T(g(A) x).
  Entre Amount 5 y 15 la ganancia sube R veces (Tube 36 dB, Retro 10 dB, Warm 24 dB...). Se guarda la curva
  base T en un dominio que cubre ambos casos (Amount 5 dentro, Amount 15 fuera).
- Tape, Triode y Dual Triode tienen memoria (histéresis de cinta, polarización que sigue al nivel): una curva
  fija medida no basta. Para ellos se usa la curva estática ajustada por ajustar_armonicos.py (guardada en
  ajustes_ozone7.json) que da la misma ganancia y armónicos H2-H5 que Ozone a 4 niveles, una por Amount
  (5 y 15), y el plugin las mezcla.
"""
import glob
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analizar as A  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "plugins_vst", "aieexciter", "ozone_curves.h")
NPTS = 1025
ALPHA = 5.0
# perfil: (clave Amount 5, clave Amount 15, paso alto posterior en Hz, modelo, varios niveles)
PROFILES = [
    ("tube", "tube_al_5", "prueba_tube.", 15.0, "drive", True),
    ("tape", "tape_al_5", "prueba_tape.", 24.0, "ajuste", False),
    ("retro", "retro_al_5", "prueba_retro.", 15.0, "drive", True),
    ("warm", "warm_al_5", "warm_al_15", 15.0, "drive", True),
    ("triode", "prueba_triode_al_5", "prueba_triode.", 50.0, "ajuste", False),
    ("dual", "dual_triode_al_5", "dual_triode.", 49.4, "ajuste", False),
]


def find(folder, key):
    c = [f for f in glob.glob(os.path.join(folder, "*.wav")) if key in os.path.basename(f).replace("pruebadual", "prueba_dual")]
    if len(c) != 1:
        raise SystemExit(f"no encuentro un único wav con '{key}': {c}")
    return c[0]


def inv_hp(y, fc):
    n = len(y)
    Y = np.fft.rfft(y)
    fr = np.fft.rfftfreq(n, 1 / 44100)
    fr[0] = 1
    Z = Y / (1j * fr / (1j * fr + fc))
    Z[0] = 0
    return np.fft.irfft(Z, n)


def static_curve(ref, mp, out, fc, smooth=False, levels=(-24, -12, -6, -0.1)):
    """curva y=f(x) juntando los niveles del seno de 100 Hz (los flojos dan detalle cerca de 0). En los perfiles
    que no son del todo estáticos (Tape, Triode, Dual Triode) los niveles no casan y se usa solo el de -0,1 dBFS"""
    curves = []
    for lv in levels:
        for s in mp["segmentos"]:
            if s["tipo"] == "seno" and s["f"] == 100 and s["nivel"] == lv and A.clean(ref, out, s["inicio"], s["fin"]):
                a0 = s["inicio"] + 4410
                n = 441 * 30
                x, y = ref[a0:a0 + n, 0], inv_hp(out[a0:a0 + n, 0], fc)
                a = np.abs(x).max() * 0.995
                g = np.linspace(-a, a, 801 if len(levels) == 1 else (161 if lv > -20 else 121))
                idx = np.digitize(x, g) - 1
                c = 0.5 * (g[1:] + g[:-1])
                v = np.array([y[idx == i].mean() if (idx == i).any() else np.nan for i in range(len(c))])
                m = np.isfinite(v)
                curves.append((a, c[m], v[m]))
                break
    curves.sort(key=lambda t: t[0])
    X, Y = curves[0][1], curves[0][2] - np.interp(0, curves[0][1], curves[0][2])
    for a, c, v in curves[1:]:
        amax = X.max()
        m = np.abs(c) < amax * 0.9
        v = v - (np.mean(v[m] - np.interp(c[m], X, Y)) if m.sum() > 3 else np.interp(0, c, v))
        keep = np.abs(c) > amax
        X = np.concatenate([X, c[keep]])
        Y = np.concatenate([Y, v[keep]])
        o = np.argsort(X)
        X, Y = X[o], Y[o]
    # suavizado opcional (spline con penalización elegida por validación cruzada); borra rodillas reales,
    # por eso no se usa
    if smooth:
        from scipy.interpolate import make_smoothing_spline
        X, idx = np.unique(X, return_index=True)
        Y = make_smoothing_spline(X, Y[idx])(X)
    return X, Y - np.interp(0, X, Y)


def drive_fit(X5, Y5, X15, Y15):
    """R y c tales que F15(x) ~ c F5(R x) donde ambas se conocen"""
    a5 = X5.max()
    best = None
    for R in np.geomspace(1.05, 120, 400):
        xs = np.linspace(-a5 / R, a5 / R, 300)
        f15 = np.interp(xs, X15, Y15)
        f5 = np.interp(R * xs, X5, Y5)
        M = np.vstack([f5, np.ones_like(f5)]).T
        sol = np.linalg.lstsq(M, f15, rcond=None)[0]
        e = np.sqrt(np.mean((M @ sol - f15) ** 2) / np.mean((f15 - f15.mean()) ** 2))
        if best is None or e < best[0]:
            best = (e, R, sol[0])
    return best


def grid(U):
    t = np.linspace(-1, 1, NPTS)
    return U * np.sinh(ALPHA * t) / np.sinh(ALPHA)


def fmt(v):
    lines = []
    for i in range(0, len(v), 8):
        lines.append("    " + ", ".join(f"{x:.7e}f" for x in v[i:i + 8]) + ",")
    return "\n".join(lines)


def main():
    folder = sys.argv[1]
    ref = A.load(os.path.join(HERE, "senal_prueba_ozone.wav"))
    mp = json.load(open(os.path.join(HERE, "senal_prueba_ozone.json")))
    rows = []
    ajustes = json.load(open(os.path.join(HERE, "ajustes_ozone7.json")))
    from scipy.interpolate import PchipInterpolator
    kn = np.array([0.02, 0.04, 0.063, 0.1, 0.16, 0.25, 0.35, 0.5, 0.7, 1.0, 1.3])
    kx = np.concatenate([-kn[::-1], [0], kn])
    for name, k5, k15, fc, model, multi in PROFILES:
        if model == "ajuste":  # curvas ajustadas a los armónicos (dominio de entrada [-1,3, 1,3])
            def knots(p):  # log de la pendiente de cada tramo -> valores en los nudos (curva creciente, y(0)=0)
                inc = np.exp(np.array(p)) * np.diff(kx)
                m = len(kn)
                return np.concatenate([-np.cumsum(inc[:m][::-1])[::-1], [0], np.cumsum(inc[m:])])
            c5, c15 = [PchipInterpolator(kx, knots(ajustes[name][a])) for a in ("5", "15")]
            U = 1.3
            u = grid(U)
            T, T2 = c5(u), c15(u)
            slope = float(c5(1e-3) - c5(-1e-3)) / 2e-3
            print(f"{name:7s} ajuste (mezcla de las curvas de Amount 5 y 15)  pendiente(Amount 5)={slope:.3f}")
            rows.append((name, model, 5.0, 1.0, U, fc, slope, T, T2))
            continue
        o5, o15 = A.load(find(folder, k5)), A.load(find(folder, k15))
        lv = (-24, -12, -6, -0.1) if multi else (-0.1,)
        X5, Y5 = static_curve(ref, mp, o5, fc, False, lv)
        X15, Y15 = static_curve(ref, mp, o15, fc, False, lv)
        a5 = X5.max()
        if model == "drive":
            e, R, c = drive_fit(X5, Y5, X15, Y15)
            U = R * X15.max()
            u = grid(U)
            inner = np.interp(u, X5, Y5)
            outer = np.interp(u / R, X15, Y15) / c
            w = np.clip((np.abs(u) - a5 * 0.8) / (a5 * 0.18), 0, 1)
            T = (1 - w) * inner + w * outer
            T2 = None
        else:  # mezcla de las dos curvas medidas (dominio de entrada [-1, 1])
            e, R, c = 0.0, 5.0, 1.0
            U = X15.max()
            u = grid(U)
            T = np.interp(u, X5, Y5)
            T2 = np.interp(u, X15, Y15)
        T -= np.interp(0, u, T)
        m = np.abs(X5) < (0.04 if multi else 0.06)  # ganancia en señal pequeña de Amount 5 (recta por mínimos cuadrados)
        slope = float(np.polyfit(X5[m], Y5[m], 1)[0])
        print(f"{name:7s} {model:5s} R={R:7.3f} ({20*np.log10(R):5.1f} dB)  c15={c:.4f}  error {e*100:.2f}%  U={U:.2f}  pendiente(Amount 5)={slope:.3f}")
        rows.append((name, model, R, c, U, fc, slope, T, T2))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// Generado por herramientas/medicion_ozone/generar_curvas.py a partir de medidas de Ozone 7 Exciter.\n")
        f.write("// No editar a mano. Curva base T(u) en una rejilla u = U*sinh(a*t)/sinh(a), t en [-1, 1].\n#pragma once\n\n")
        f.write(f"namespace ozc {{\nstatic const int kPts = {NPTS};\nstatic const double kAlpha = {ALPHA};\n\n")
        for name, model, R, c, U, fc, slope, T, T2 in rows:
            f.write(f"static const float k{name.capitalize()}[kPts] = {{\n{fmt(T)}\n}};\n")
            if T2 is not None:
                f.write(f"static const float k{name.capitalize()}15[kPts] = {{\n{fmt(T2)}\n}};\n")
        f.write("\nstruct Curve {\n    const float* t5;   // curva a Amount 5 (dentro de |u|<=1) y, si es 'drive', también la de Amount 15\n")
        f.write("    const float* t15;  // solo modelo de mezcla: curva a Amount 15\n    double U;          // extremo de la rejilla\n")
        f.write("    double R;          // la ganancia de entrada se multiplica por R entre Amount 5 y 15\n")
        f.write("    double c15;        // escala de salida a Amount 15 (relativa a Amount 5)\n")
        f.write("    double hp;         // paso alto posterior (Hz)\n    double slope5;     // ganancia en señal pequeña a Amount 5\n};\n")
        f.write("// orden: Tube, Tape, Retro, Warm, Triode, Dual Triode\nstatic const Curve kCurves[6] = {\n")
        for name, model, R, c, U, fc, slope, T, T2 in rows:
            t15 = f"k{name.capitalize()}15" if T2 is not None else "nullptr"
            f.write(f"    {{k{name.capitalize()}, {t15}, {U:.6g}, {R:.6g}, {c:.6g}, {fc:.6g}, {slope:.6g}}},\n")
        f.write("};\n}  // namespace ozc\n")
    print("escrito", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
