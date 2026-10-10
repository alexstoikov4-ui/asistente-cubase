"""Niveles de autonomía y permisos por categoría."""
from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Categorías de operación. "destructive" marca las que necesitan copia de seguridad previa.
CATEGORIES = {
    "analysis": {"label": "Análisis (solo lectura)", "destructive": False},
    "compose": {"label": "Generar MIDI nuevo", "destructive": False},
    "export_files": {"label": "Escribir archivos nuevos (exportaciones, informes)", "destructive": False},
    "levels": {"label": "Niveles y paneo", "destructive": False},
    "eq": {"label": "Ecualización", "destructive": False},
    "dynamics": {"label": "Compresión / dinámica", "destructive": False},
    "effects": {"label": "Efectos y envíos", "destructive": False},
    "master": {"label": "Masterización", "destructive": False},
    "cubase_control": {"label": "Enviar teclas/comandos a Cubase", "destructive": False},
    "structure": {"label": "Modificar estructura de la canción", "destructive": True},
    "overwrite": {"label": "Sobrescribir archivos", "destructive": True},
    "delete": {"label": "Eliminar pistas o archivos", "destructive": True},
}

ROUTINE = {"analysis", "compose", "export_files", "levels", "eq", "dynamics", "effects"}

MODES = {
    "assisted": "Asistido: analiza y propone; espera aprobación antes de aplicar.",
    "semi": "Semiautónomo: ejecuta lo rutinario, pide aprobación para lo importante.",
    "autonomous": "Autónomo: ejecuta lo autorizado sin confirmar cada paso.",
}


def default_decision(mode: str, category: str) -> Decision:
    if category not in CATEGORIES:
        raise KeyError(f"categoría desconocida: {category}")
    if mode not in MODES:
        raise KeyError(f"modo desconocido: {mode}")
    if category == "analysis":
        return Decision.ALLOW  # leer y medir nunca cambia nada
    if CATEGORIES[category]["destructive"]:
        return Decision.ASK  # nunca autorizado implícitamente, ni en modo autónomo
    if mode == "assisted":
        return Decision.ASK
    if mode == "semi":
        return Decision.ALLOW if category in ROUTINE else Decision.ASK
    return Decision.ALLOW


class PermissionPolicy:
    def __init__(self, mode: str = "assisted", overrides: dict | None = None):
        if mode not in MODES:
            raise KeyError(f"modo desconocido: {mode}")
        self.mode = mode
        self.overrides = {k: Decision(v) for k, v in (overrides or {}).items() if k in CATEGORIES}

    def decide(self, category: str) -> Decision:
        if category in self.overrides:
            return self.overrides[category]
        return default_decision(self.mode, category)

    def as_dict(self) -> dict:
        return {c: self.decide(c).value for c in CATEGORIES}
