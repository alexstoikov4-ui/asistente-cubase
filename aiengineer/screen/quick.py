"""Órdenes sencillas que se resuelven con un atajo de Cubase, sin modelo de visión (instantáneo y gratis).

Se usan los atajos reales del usuario (Key Commands.xml) y, si no existen, los de fábrica de Cubase.
Tras pulsar el atajo se compara la pantalla para comprobar que algo cambió.
"""
from __future__ import annotations

import re
import time

from ..nlp.intents import norm
from .vision import changed_fraction

# (patrón de la orden, categoría y comando de Cubase, descripción)
QUICK = [
    (r"\b(abre|abrir|muestra|ensena|cierra|cerrar|oculta)\b.{0,20}\b(mezclador|mixer|consola)\b", ("Devices", "Mixer"), "mezclador"),
    (r"\b(abre|muestra|cierra)\b.{0,20}\b(panel de transporte|transporte)\b", ("Devices", "Transport Panel"), "panel de transporte"),
    (r"\b(abre|muestra|cierra)\b.{0,20}\b(conexiones vst|conexiones)\b", ("Devices", "VST Connections"), "conexiones VST"),
    (r"\b(abre|muestra|cierra)\b.{0,20}\b(instrumentos vst|instrumentos)\b", ("Devices", "VST Instruments"), "instrumentos VST"),
    (r"\b(reproduce|play|dale al play|para la reproduccion|stop|pausa)\b", ("Transport", "Start/Stop"), "reproducción (play/stop)"),
    (r"\b(guarda|guardar|salva)\b.{0,20}\b(proyecto|cancion|tema)?\b", ("File", "Save"), "guardar proyecto"),
    (r"\b(deshaz|deshacer)\b", ("Edit", "Undo"), "deshacer"),
    (r"\b(rehaz|rehacer)\b", ("Edit", "Redo"), "rehacer"),
]


def match_quick(text: str):
    t = norm(text)
    for pat, cmd, label in QUICK:
        if re.search(pat, t):
            return cmd, label
    return None


def run_quick(desktop, keymap, text: str, settle: float = 0.8):
    """Devuelve (estado, mensaje) o None si la orden no es de las rápidas."""
    m = match_quick(text)
    if not m:
        return None
    (cat, cmd), label = m
    key, origin = keymap.shortcut(cat, cmd)
    if not key:
        return "failed", f"No hay atajo para '{label}' en tu Cubase. Asígnalo en Archivo > Comandos de teclado."
    if hasattr(desktop, "focus_process") and not desktop.focus_process("cubase"):
        proc = (desktop.foreground_process() or "").lower()
        if "cubase" not in proc:
            return "failed", "Cubase no está abierto o no se pudo poner delante."
    before = desktop.screenshot()
    desktop.key(key.lower().replace("num ", "num"))
    time.sleep(settle)
    changed = changed_fraction(before, desktop.screenshot()) > 0.0005
    if changed:
        return "verified", f"Hecho: {label} (atajo {key}, {origin}). La pantalla cambió."
    return "unverified", (f"Atajo {key} ({origin}) enviado para '{label}', pero la pantalla no cambió: puede que ya "
                          "estuviera en ese estado o que el atajo sea otro en tu Cubase.")
