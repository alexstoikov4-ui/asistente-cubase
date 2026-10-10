"""Gestor de archivos seguro: nunca sobrescribe ni borra originales sin pasar por copia de seguridad."""
from __future__ import annotations

from pathlib import Path

AUDIO_EXT = {".wav", ".wave"}
MIDI_EXT = {".mid", ".midi"}
CUBASE_EXT = {".cpr"}


def unique_path(path: Path) -> Path:
    """Devuelve una ruta libre añadiendo _v2, _v3... si ya existe."""
    path = Path(path)
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem}_v{n}{path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def scan_folder(folder: Path) -> dict:
    folder = Path(folder)
    res = {"audio": [], "midi": [], "cubase": [], "other": []}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in AUDIO_EXT:
            res["audio"].append(p)
        elif ext in MIDI_EXT:
            res["midi"].append(p)
        elif ext in CUBASE_EXT:
            res["cubase"].append(p)
        else:
            res["other"].append(p)
    return res


def is_probably_in_use(path: Path) -> bool:
    """En Windows, un archivo abierto en exclusiva por Cubase no se puede renombrar.
    Se prueba renombrándolo a sí mismo (operación sin efecto). En otros sistemas devuelve False."""
    import os
    import sys
    if sys.platform != "win32":
        return False
    try:
        os.rename(path, path)
        return False
    except OSError:
        return True
