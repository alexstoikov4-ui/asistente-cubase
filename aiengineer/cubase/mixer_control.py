"""Mezclador de Cubase controlado por Mackie Control, con comprobación de cada ajuste.

- Niveles: se mueve el fader y se lee el valor en dB que Cubase escribe en la pantalla de la superficie
  (fila inferior). Se corrige en varios pasos hasta quedar a ±0,3 dB. Si Cubase no muestra el valor,
  se usa la curva aprendida y el resultado se informa como no verificado.
- Mute/solo: se pulsa el botón y se comprueba el LED que devuelve Cubase.
- Pistas: los nombres se leen de la pantalla (fila superior); para llegar a la pista 9 en adelante se
  cambia de banco (8 pistas por banco).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from .mackie import ASSIGN, NAV, MackieSurface, parse_db, parse_pan

# Curva inicial aproximada posición -> dB (se refina con cada lectura real y se guarda).
DEFAULT_CURVE = [(0, -120.0), (1500, -60.0), (4000, -36.0), (7000, -20.0), (10000, -10.0), (12500, -3.0),
                 (13900, 0.0), (15200, 3.0), (16383, 6.0)]


@dataclass
class Result:
    status: str  # verified | unverified | failed
    message: str
    value: float | None = None


class MixerControl:
    def __init__(self, surface: MackieSurface, curve_path: Path | None = None, settle: float = 0.25):
        self.s = surface
        self.settle = settle
        self.curve_path = Path(curve_path) if curve_path else None
        self.curve = self._load_curve()
        self.bank = 0  # banco actual (0 = pistas 1-8)

    # ------------------------------------------------------------ curva de fader
    def _load_curve(self):
        if self.curve_path and self.curve_path.exists():
            try:
                pts = [tuple(p) for p in json.loads(self.curve_path.read_text(encoding="utf-8"))]
                if len(pts) >= 3:
                    return sorted(pts)
            except (ValueError, TypeError):
                pass
        return list(DEFAULT_CURVE)

    def _learn(self, pos: int, db: float):
        if not math.isfinite(db):
            return
        # se mantiene la curva monótona: se descartan los puntos que contradicen la lectura real
        keep = [p for p in self.curve if abs(p[0] - pos) > 150
                and not (p[0] < pos and p[1] >= db) and not (p[0] > pos and p[1] <= db)]
        self.curve = sorted(keep + [(pos, db)])
        if self.curve_path:
            self.curve_path.write_text(json.dumps(self.curve), encoding="utf-8")

    def pos_for_db(self, db: float) -> int:
        c = self.curve
        if db <= c[0][1]:
            return c[0][0]
        for (p0, d0), (p1, d1) in zip(c, c[1:]):
            if d0 <= db <= d1:
                return int(round(p0 + (p1 - p0) * (db - d0) / (d1 - d0 or 1e-9)))
        return c[-1][0]

    def db_for_pos(self, pos: int) -> float:
        c = self.curve
        if pos <= c[0][0]:
            return c[0][1]
        for (p0, d0), (p1, d1) in zip(c, c[1:]):
            if p0 <= pos <= p1:
                return d0 + (d1 - d0) * (pos - p0) / ((p1 - p0) or 1)
        return c[-1][1]

    # ------------------------------------------------------------ pistas
    def connect(self) -> Result:
        if self.s.wake():
            st = self.s.state
            names = [n for n in st.track_names() if n]
            msg = f"Cubase responde por Mackie Control: {st.stats()}.\n"
            if names:
                return Result("verified", msg + "Pistas visibles: " + ", ".join(names))
            if st.sysex == 0:
                msg += ("No llega ningún SysEx, que es por donde Cubase envía los nombres y los valores en dB. "
                        "Las luces y los faders sí llegan.\n")
            else:
                msg += "Llegan SysEx pero no con el texto de la pantalla.\n"
            msg += "Primeros mensajes recibidos (para diagnóstico):\n  " + "\n  ".join(st.samples)
            return Result("unverified", msg)
        return Result("failed", "Cubase no responde. Revisa en Cubase: Dispositivos > Configuración de dispositivos > "
                                "Mackie Control, con entrada = puerto 'Cubase a AIE' y salida = puerto 'AIE a Cubase'.")

    def go_to_track(self, track: int) -> int:
        """Selecciona el banco que contiene la pista (1-based) y devuelve la tira 0-7."""
        target_bank = (track - 1) // 8
        while self.bank < target_bank:
            self.s.press(NAV["bank_right"])
            self.bank += 1
        while self.bank > target_bank:
            self.s.press(NAV["bank_left"])
            self.bank -= 1
        self.s.poll(0.3)
        if not any(self.s.state.track_names()):
            self.s.wake()  # pide a Cubase que vuelva a enviar nombres y valores a la superficie
        return (track - 1) % 8

    def find_track(self, name: str, max_banks: int = 8) -> int | None:
        """Busca una pista por nombre (coincidencia parcial, sin mayúsculas) recorriendo los bancos."""
        want = name.lower().strip()
        for bank in range(max_banks):
            self.go_to_track(bank * 8 + 1)
            names = self.s.state.track_names()
            for i, n in enumerate(names):
                if n and (want in n.lower() or n.lower() in want):
                    return bank * 8 + i + 1
            if not any(names):
                break
        return None

    def track_names(self, max_banks: int = 8) -> list[str]:
        out = []
        for bank in range(max_banks):
            self.go_to_track(bank * 8 + 1)
            names = self.s.state.track_names()
            if not any(names) or (out and names == out[-8:]):
                break
            out += names
        return [n for n in out if n]

    # ------------------------------------------------------------ nivel
    def read_db(self, strip: int) -> float | None:
        return parse_db(self.s.state.strip_text(strip, 1))

    def set_volume(self, track: int, db: float, tol: float = 0.3, max_iter: int = 8) -> Result:
        strip = self.go_to_track(track)
        pos = self.pos_for_db(db)
        last = None
        readings: list[tuple[int, float]] = []
        for _ in range(max_iter):
            self.s.fader(strip, pos, touch=True)
            time.sleep(self.settle)
            self.s.poll(0.2)
            got = self.read_db(strip)
            if got is None:
                self.s.release_fader(strip)
                return Result("unverified", f"Fader de la pista {track} movido a la posición estimada para {db:+.1f} dB; "
                                            "Cubase no muestra el valor en la pantalla, así que no se puede verificar.")
            self._learn(pos, got)
            last = got
            if math.isfinite(got) and abs(got - db) <= tol:
                self.s.release_fader(strip)
                return Result("verified", f"Pista {track}: volumen {got:+.2f} dB (pedido {db:+.1f}).", got)
            if math.isfinite(got):
                readings.append((pos, got))
            if len(readings) >= 2 and readings[-1][1] != readings[-2][1]:
                # método de la secante con las dos últimas lecturas reales (converge en pocos pasos)
                (p0, d0), (p1, d1) = readings[-2], readings[-1]
                pos = int(round(p1 + (db - d1) * (p1 - p0) / (d1 - d0)))
                pos = max(0, min(16383, pos))
            else:
                pos = self.pos_for_db(db)
        self.s.release_fader(strip)
        return Result("failed", f"No se alcanzó {db:+.1f} dB en la pista {track} (último valor leído: {last}).", last)

    def change_volume(self, track: int, delta_db: float) -> Result:
        strip = self.go_to_track(track)
        self.s.note(0x68 + strip, True)  # tocar el fader hace que Cubase muestre su valor
        self.s.poll(0.3)
        cur = self.read_db(strip)
        self.s.release_fader(strip)
        if cur is not None and math.isfinite(cur):
            return self.set_volume(track, cur + delta_db)
        # Sin texto en pantalla: se usa la posición del fader que Cubase devuelve a la superficie
        pos = self.s.state.faders.get(strip)
        if pos is None:
            self.s.wake(attempts=1)
            pos = self.s.state.faders.get(strip)
        if pos is None:
            return Result("failed", f"No se pudo leer ni el valor ni la posición del fader de la pista {track}.")
        est = self.db_for_pos(pos)
        target = self.pos_for_db(est + delta_db)
        self.s.fader(strip, target, touch=True)
        time.sleep(self.settle)
        self.s.poll(0.3)
        self.s.release_fader(strip)
        back = self.s.state.faders.get(strip)
        landed = back is not None and abs(back - target) <= 64
        return Result("unverified", f"Pista {track}: fader movido unos {delta_db:+.1f} dB (estimado con la curva del "
                                    f"fader, de ~{est:.1f} a ~{est + delta_db:.1f} dB"
                                    f"{'; Cubase confirma la nueva posición' if landed else ''}). Cubase no envía el "
                                    "valor exacto en dB, así que no se puede verificar al décimo.")

    # ------------------------------------------------------------ mute / solo
    def set_button(self, kind: str, track: int, on: bool) -> Result:
        strip = self.go_to_track(track)
        state = self.s.is_on(kind, strip)
        if state is not None and state == on:
            return Result("verified", f"Pista {track}: {kind} ya estaba {'activado' if on else 'desactivado'}.")
        self.s.button(kind, strip)
        self.s.poll(0.3)
        state = self.s.is_on(kind, strip)
        if state is None:
            return Result("unverified", f"Botón {kind} de la pista {track} pulsado; Cubase no devolvió el LED.")
        ok = state == on
        return Result("verified" if ok else "failed",
                      f"Pista {track}: {kind} {'activado' if state else 'desactivado'}.")

    # ------------------------------------------------------------ paneo
    def set_pan(self, track: int, pan: float, max_iter: int = 12) -> Result:
        """pan en la escala de Cubase (-100 izquierda ... 0 centro ... 100 derecha)."""
        strip = self.go_to_track(track)
        self.s.press(ASSIGN["pan"])
        self.s.poll(0.3)
        for _ in range(max_iter):
            cur = parse_pan(self.s.state.strip_text(strip, 1))
            if cur is None:
                return Result("unverified", "Cubase no muestra el valor de paneo en la pantalla de la superficie.")
            if abs(cur - pan) < 0.5:
                return Result("verified", f"Pista {track}: paneo {self.s.state.strip_text(strip, 1)}.", cur)
            ticks = int(max(-15, min(15, round((pan - cur) / 2))))  # ~2 unidades por paso (se corrige con la lectura)
            self.s.vpot(strip, ticks or (1 if pan > cur else -1))
            self.s.poll(0.2)
        return Result("failed", f"No se alcanzó el paneo {pan} en la pista {track}.")
