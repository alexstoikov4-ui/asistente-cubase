"""Cubase simulado que habla Mackie Control (para pruebas y para la demostración sin Cubase).
Se comporta como el host: devuelve nombres de pista, valores en dB, paneo y LEDs."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .mackie import ASSIGN, BTN, HEADER, NAV
from .midi_ports import LoopbackPair


@dataclass
class FakeTrack:
    name: str
    db: float = 0.0
    pan: float = 0.0
    mute: bool = False
    solo: bool = False


def fake_taper(pos: int) -> float:
    """Curva propia (distinta de la estimada por la app) para comprobar que la corrección converge."""
    if pos <= 0:
        return float("-inf")
    return round(6.0 + 40.0 * math.log10(pos / 16383), 2)


class FakeCubase:
    def __init__(self, names: list[str], show_values: bool = True):
        self.tracks = [FakeTrack(n) for n in names]
        self.to_surface = LoopbackPair()  # lo que la superficie recibe
        self.show_values = show_values
        self.bank = 0
        self.mode = "pan"
        self.touched: set[int] = set()

    # la superficie llama a esto como si fuera su puerto de salida
    def send(self, data: bytes):
        b0 = data[0]
        kind = b0 & 0xF0
        if kind == 0xE0:
            strip = b0 & 0x0F
            t = self._track(strip)
            if t:
                t.db = fake_taper(data[1] | (data[2] << 7))
                self._show_value(strip)
        elif kind == 0x90 and data[2] > 0:
            n = data[1]
            if 0x68 <= n <= 0x6F:
                self.touched.add(n - 0x68)
                self._show_value(n - 0x68)
            elif n == NAV["bank_right"] and (self.bank + 1) * 8 < len(self.tracks):
                self.bank += 1
                self._refresh()
            elif n == NAV["bank_left"] and self.bank > 0:
                self.bank -= 1
                self._refresh()
            elif n in (ASSIGN["pan"], ASSIGN["track"]):
                self.mode = "pan"
                self._refresh()
            elif BTN["mute"] <= n < BTN["mute"] + 8:
                self._toggle("mute", n - BTN["mute"])
            elif BTN["solo"] <= n < BTN["solo"] + 8:
                self._toggle("solo", n - BTN["solo"])
        elif kind == 0x90 and data[2] == 0 and 0x68 <= data[1] <= 0x6F:
            self.touched.discard(data[1] - 0x68)
            self._refresh()
        elif kind == 0xB0 and 0x10 <= data[1] <= 0x17:
            strip = data[1] - 0x10
            t = self._track(strip)
            if t:
                v = data[2]
                ticks = -(v & 0x3F) if v & 0x40 else v
                t.pan = max(-100, min(100, t.pan + ticks))
                self._lcd_strip(strip, 1, self._pan_text(t.pan))

    def close(self):
        pass

    def _track(self, strip):
        i = self.bank * 8 + strip
        return self.tracks[i] if i < len(self.tracks) else None

    def _toggle(self, attr, strip):
        t = self._track(strip)
        if t:
            setattr(t, attr, not getattr(t, attr))
            self.to_surface.send(bytes([0x90, BTN[attr] + strip, 0x7F if getattr(t, attr) else 0]))

    @staticmethod
    def _pan_text(p):
        return "C" if p == 0 else (f"L{int(-p)}" if p < 0 else f"R{int(p)}")

    def _lcd_strip(self, strip, row, text):
        pos = row * 56 + strip * 7
        self.to_surface.send(HEADER + bytes([0x12, pos]) + text[:7].ljust(7).encode("ascii") + bytes([0xF7]))

    def _show_value(self, strip):
        t = self._track(strip)
        if t and self.show_values:
            self._lcd_strip(strip, 1, "-oo" if not math.isfinite(t.db) else f"{t.db:.2f}")

    def _refresh(self):
        for s in range(8):
            t = self._track(s)
            self._lcd_strip(s, 0, t.name if t else "")
            self._lcd_strip(s, 1, self._pan_text(t.pan) if t else "")
            if t:
                self.to_surface.send(bytes([0x90, BTN["mute"] + s, 0x7F if t.mute else 0]))
                self.to_surface.send(bytes([0x90, BTN["solo"] + s, 0x7F if t.solo else 0]))
