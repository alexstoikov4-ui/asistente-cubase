"""Órdenes de mezclador en español: 'sube el bajo 2 dB', 'pon la pista 3 a -6 dB', 'silencia el pad',
'pon en solo el kick', 'panea los hats 30 a la derecha', 'centra el bajo'."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .intents import norm

NUM = r"(-?\d+(?:[.,]\d+)?)"


@dataclass
class MixerCommand:
    action: str  # set_db | change_db | mute | unmute | solo | unsolo | pan
    target: str  # número de pista ("3") o nombre ("bajo")
    value: float | None = None


def _target(t: str) -> str | None:
    m = re.search(r"\b(?:pista|canal|track)\s*(\d+)", t)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:el|la|los|las|del|de la|al|a la)\s+([a-z0-9#\- ]+?)(?=\s+(?:a|en|un|unos|\d|-|hacia|al|todo)\b|$|\s+-?\d)", t)
    if m:
        name = re.sub(r"\b(volumen|fader|nivel|paneo|pan|mute|solo)\b", "", m.group(1)).strip()
        name = re.sub(r"^(del|de la|de|el|la|los|las)\s+", "", name).strip()
        return name or None
    return None


def parse_mixer(text: str) -> MixerCommand | None:
    t = norm(text).replace(",", ".")
    t = re.sub(r"\s+", " ", t).strip(" .")
    if re.search(r"\b(quita|desactiva|saca)\b.{0,15}\b(el )?mute\b|\bdesmutea|\bdesilencia|\bactiva el sonido\b", t):
        tg = _target(re.sub(r".*?\bmute\b", "", t) or t) or _target(t)
        return MixerCommand("unmute", tg) if tg else None
    if re.search(r"\b(quita|desactiva|saca)\b.{0,15}\bsolo\b", t):
        tg = _target(re.sub(r".*?\bsolo\b", "", t)) or _target(t)
        return MixerCommand("unsolo", tg) if tg else None
    if re.search(r"\b(silencia|mutea|enmudece)\b", t):
        tg = _target(t)
        return MixerCommand("mute", tg) if tg else None
    if re.search(r"\b(pon|deja|activa)\b.{0,15}\ben solo\b|\bsolea\b|\bsolo (a|al|en) ", t):
        tg = _target(re.sub(r"\ben solo\b", "", t))
        return MixerCommand("solo", tg) if tg else None
    if re.search(r"\bcentra\b", t):
        tg = _target(t)
        return MixerCommand("pan", tg, 0.0) if tg else None
    m = re.search(r"\bpanea\b", t)
    if m or re.search(r"\b(pan|paneo)\b", t):
        tg = _target(re.sub(r"\b(panea|paneo|pan)\b", "", t))
        v = re.search(r"(\d+)", re.sub(r"\b(?:pista|canal)\s*\d+", "", t))
        amount = float(v.group(1)) if v else 50.0
        if "izquierda" in t:
            amount = -amount
        elif "derecha" not in t and "centro" in t:
            amount = 0.0
        return MixerCommand("pan", tg, amount) if tg else None
    m = re.search(r"\b(sube|subele|aumenta|baja|bajale|reduce)\b.*?" + NUM + r"\s*db", t)
    if m:
        sign = -1 if m.group(1).startswith(("baja", "reduce")) else 1
        tg = _target(re.sub(NUM + r"\s*db", "", t))
        return MixerCommand("change_db", tg, sign * abs(float(m.group(2)))) if tg else None
    m = re.search(r"\b(pon|deja|ajusta|lleva|fija)\b.*?\ba\s+" + NUM + r"\s*(db)?", t)
    if m and ("db" in t or "volumen" in t or "fader" in t or "nivel" in t):
        tg = _target(re.sub(r"\ba\s+" + NUM + r"\s*(db)?", "", t))
        return MixerCommand("set_db", tg, float(m.group(2))) if tg else None
    m = re.search(r"\b(sube|baja)\b", t)
    if m and re.search(r"\b(volumen|fader|nivel|pista|canal)\b", t):
        tg = _target(t)
        return MixerCommand("change_db", tg, 2.0 if m.group(1) == "sube" else -2.0) if tg else None
    return None
