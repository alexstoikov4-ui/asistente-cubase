"""Emulación de una superficie Mackie Control para manejar el mezclador de Cubase 5 con precisión.

Cubase 5 admite Mackie Control de serie (Dispositivos > Configuración de dispositivos > + > Mackie Control).
La comunicación es bidireccional: la aplicación mueve faders, pulsa botones y gira codificadores; Cubase
devuelve la posición de los faders, el estado de los LEDs (mute/solo/select) y el texto de su pantalla
(nombres de pista arriba, valores abajo). Con esa respuesta se VERIFICA cada ajuste.

Protocolo (público, Mackie Control Universal):
- Faders: pitch bend 14 bits en los canales MIDI 0-7 (tiras) y 8 (master). Toque: nota 0x68+tira.
- Botones (nota, vel 127/0): REC 0x00+, SOLO 0x08+, MUTE 0x10+, SELECT 0x18+, V-POT 0x20+,
  TRACK 0x28, SEND 0x29, PAN 0x2A, PLUG-IN 0x2B, EQ 0x2C, INSTRUMENT 0x2D, BANK </> 0x2E/0x2F,
  CHANNEL </> 0x30/0x31, FLIP 0x32, NAME/VALUE 0x34, transporte 0x5B-0x5F.
- V-pots: CC 0x10+tira, valor relativo (bit 6 = sentido antihorario).
- Pantalla: SysEx F0 00 00 66 14 12 <posición> <texto> F7 (2 filas x 56 caracteres, 7 por tira).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

HEADER = bytes([0xF0, 0x00, 0x00, 0x66, 0x14])
BTN = {"rec": 0x00, "solo": 0x08, "mute": 0x10, "select": 0x18, "vpot": 0x20}
ASSIGN = {"track": 0x28, "send": 0x29, "pan": 0x2A, "plugin": 0x2B, "eq": 0x2C, "instrument": 0x2D}
NAV = {"bank_left": 0x2E, "bank_right": 0x2F, "channel_left": 0x30, "channel_right": 0x31, "flip": 0x32,
       "name_value": 0x34}
TRANSPORT = {"rewind": 0x5B, "forward": 0x5C, "stop": 0x5D, "play": 0x5E, "record": 0x5F}
SERIAL = b"AIENG01"


@dataclass
class SurfaceState:
    """Lo que Cubase ha devuelto a la superficie."""
    lcd: list = field(default_factory=lambda: [" "] * 112)
    faders: dict = field(default_factory=dict)  # tira -> 0..16383
    leds: dict = field(default_factory=dict)  # nota -> encendido
    vpot_rings: dict = field(default_factory=dict)
    online: bool = False
    updates: int = 0

    def lcd_row(self, row: int) -> str:
        return "".join(self.lcd[row * 56:(row + 1) * 56])

    def strip_text(self, strip: int, row: int) -> str:
        return self.lcd_row(row)[strip * 7:strip * 7 + 7].strip()

    def track_names(self) -> list[str]:
        return [self.strip_text(i, 0) for i in range(8)]


def parse_db(text: str) -> float | None:
    """Extrae un valor en dB de la pantalla ('-6.02', '0.00', '-oo', '+3.5dB')."""
    t = text.replace(",", ".").strip().lower()
    if t in ("-oo", "-inf", "-∞") or "oo" in t:
        return float("-inf")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def parse_pan(text: str) -> float | None:
    """'C' -> 0, 'L50'/'<50' -> -50, 'R30'/'30>' -> 30 (escala de Cubase -100..100)."""
    t = text.strip().upper().replace(" ", "")
    if t in ("C", "<C>", "0", "CENTER"):
        return 0.0
    m = re.search(r"(\d+)", t)
    if not m:
        return None
    v = float(m.group(1))
    return -v if ("L" in t or "<" in t) else v


class MackieSurface:
    def __init__(self, out_port, in_port):
        self.out = out_port
        self.inp = in_port
        self.state = SurfaceState()

    # ------------------------------------------------------------ salida
    def note(self, n: int, on: bool = True):
        self.out.send(bytes([0x90, n & 0x7F, 0x7F if on else 0x00]))

    def press(self, n: int, hold: float = 0.03):
        self.note(n, True)
        time.sleep(hold)
        self.note(n, False)

    def button(self, kind: str, strip: int):
        self.press(BTN[kind] + strip)

    def fader(self, strip: int, value: int, touch: bool = True):
        value = max(0, min(16383, int(value)))
        if touch:
            self.note(0x68 + strip, True)
        self.out.send(bytes([0xE0 | strip, value & 0x7F, (value >> 7) & 0x7F]))

    def release_fader(self, strip: int):
        self.note(0x68 + strip, False)

    def vpot(self, strip: int, ticks: int):
        """Gira un codificador: ticks > 0 en sentido horario."""
        while ticks:
            step = max(-15, min(15, ticks))
            val = step if step > 0 else 0x40 | (-step)
            self.out.send(bytes([0xB0, 0x10 + strip, val]))
            ticks -= step
            time.sleep(0.005)

    # ------------------------------------------------------------ entrada
    def poll(self, duration: float = 0.15) -> int:
        """Procesa lo que haya enviado Cubase durante 'duration' segundos. Devuelve el nº de mensajes."""
        end = time.time() + duration
        n = 0
        while True:
            msg = self.inp.receive(timeout=0.01)
            if msg:
                self.handle(msg)
                n += 1
            elif time.time() >= end:
                return n

    def handle(self, msg: bytes):
        st = self.state
        if not msg:
            return
        b0 = msg[0]
        if msg[:5] == HEADER and len(msg) > 6:
            cmd = msg[5]
            if cmd == 0x12:  # texto de pantalla
                pos = msg[6]
                for i, ch in enumerate(msg[7:-1]):
                    if 0 <= pos + i < 112:
                        st.lcd[pos + i] = chr(ch) if 32 <= ch < 127 else " "
                st.updates += 1
            elif cmd == 0x00:  # el host pregunta por la superficie
                self.out.send(HEADER + bytes([0x01]) + SERIAL + bytes([0, 0, 0, 0, 0xF7]))
            elif cmd == 0x02:  # el host confirma: conexión establecida
                self.out.send(HEADER + bytes([0x03]) + SERIAL + bytes([0xF7]))
                st.online = True
            elif cmd == 0x0F:  # 'go offline'
                st.online = False
            return
        kind = b0 & 0xF0
        if kind == 0xE0 and len(msg) >= 3:
            st.faders[b0 & 0x0F] = msg[1] | (msg[2] << 7)
            st.online = True
            st.updates += 1
        elif kind == 0x90 and len(msg) >= 3:
            st.leds[msg[1]] = msg[2] > 0
            st.online = True
        elif kind == 0xB0 and len(msg) >= 3 and 0x30 <= msg[1] <= 0x37:
            st.vpot_rings[msg[1] - 0x30] = msg[2]

    # ------------------------------------------------------------ alto nivel
    def is_on(self, kind: str, strip: int) -> bool | None:
        return self.state.leds.get(BTN[kind] + strip)

    def wake(self) -> bool:
        """Pide a Cubase que refresque la superficie (cambiando de modo) y comprueba que responde."""
        before = self.state.updates
        self.press(ASSIGN["pan"])
        self.poll(0.6)
        return self.state.updates > before or any(c != " " for c in self.state.lcd)
