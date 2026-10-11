"""Lectura de Key Commands.xml de Cubase para usar los atajos REALES del usuario.

Cubase guarda los atajos en %APPDATA%\\Steinberg\\Cubase 5\\Key Commands.xml. La estructura
(listas anidadas de categorías y comandos con 'Name' y 'Key') se lee de forma tolerante; si el
formato del equipo difiere, el lector devuelve lo que pueda interpretar y lo indica.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# Atajos por defecto documentados de Cubase (se usan solo si el usuario no los ha cambiado
# y siempre se marcan como "por defecto, no verificados").
DEFAULTS = {
    ("File", "Save"): "Ctrl+S",
    ("File", "Open"): "Ctrl+O",
    ("Transport", "Start"): "Enter",
    ("Transport", "Stop"): "Num 0",
    ("Transport", "Start/Stop"): "Space",
    ("Edit", "Undo"): "Ctrl+Z",
    ("Devices", "Mixer"): "F3",
    ("Devices", "Transport Panel"): "F2",
    ("Devices", "VST Connections"): "F4",
    ("Devices", "VST Instruments"): "F11",
    ("Edit", "Redo"): "Ctrl+Shift+Z",
}


def parse_key_commands(path: Path) -> dict[tuple[str, str], list[str]]:
    """Devuelve {(categoría, comando): [atajos]}."""
    tree = ET.parse(path)
    root = tree.getroot()
    out: dict[tuple[str, str], list[str]] = {}

    def strings(item):
        return {s.get("name"): s.get("value") for s in item.findall("string")}

    for cat_item in root.iter("item"):
        st = strings(cat_item)
        cmd_list = None
        for lst in cat_item.findall("list"):
            if lst.get("name") == "Commands":
                cmd_list = lst
        if cmd_list is None or "Name" not in st:
            continue
        category = st["Name"]
        for cmd in cmd_list.findall("item"):
            cs = strings(cmd)
            name = cs.get("Name")
            if not name:
                continue
            keys = []
            if cs.get("Key"):
                keys.append(cs["Key"])
            for lst in cmd.findall("list"):
                if lst.get("name") == "Key":
                    keys += [s.get("value") for s in lst.findall("string") if s.get("value")]
            if keys:
                out[(category, name)] = keys
    return out


def find_key_commands_file(appdata_dirs: list[str]) -> Path | None:
    for d in appdata_dirs:
        p = Path(d) / "Key Commands.xml"
        if p.exists():
            return p
    return None


class KeyMap:
    def __init__(self, user: dict | None = None):
        self.user = user or {}

    @classmethod
    def load(cls, appdata_dirs: list[str]) -> "KeyMap":
        p = find_key_commands_file(appdata_dirs)
        if p is None:
            return cls({})
        try:
            return cls(parse_key_commands(p))
        except ET.ParseError:
            return cls({})

    def shortcut(self, category: str, command: str) -> tuple[str | None, str]:
        """(atajo, origen) con origen 'usuario', 'por defecto' o 'ninguno'."""
        if (category, command) in self.user:
            return self.user[(category, command)][0], "usuario"
        if (category, command) in DEFAULTS:
            return DEFAULTS[(category, command)], "por defecto"
        return None, "ninguno"
