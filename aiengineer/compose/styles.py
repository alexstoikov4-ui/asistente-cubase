"""Perfiles de estilo configurables (archivos JSON en aiengineer/data/styles; el usuario puede añadir más)."""
from __future__ import annotations

import json
from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "data" / "styles"
REQUIRED = {"name", "label", "bpm", "default_bpm", "sections", "drums", "bass", "chords", "lead", "arp"}


class StyleError(ValueError):
    pass


def load_styles(extra_dir: Path | None = None) -> dict[str, dict]:
    styles = {}
    for d in [BUILTIN_DIR] + ([Path(extra_dir)] if extra_dir else []):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            validate_style(data, f.name)
            styles[data["name"]] = data
    return styles


def validate_style(data: dict, origin: str = "") -> None:
    missing = REQUIRED - set(data)
    if missing:
        raise StyleError(f"{origin}: faltan campos {sorted(missing)}")
    for s in data["sections"]:
        if not {"name", "bars", "energy", "roles"} <= set(s):
            raise StyleError(f"{origin}: sección incompleta {s}")
        if s["bars"] <= 0 or not 0 <= s["energy"] <= 1:
            raise StyleError(f"{origin}: valores de sección fuera de rango {s}")


STYLE_ALIASES = {
    "progressive house": "progressive_house", "progressive": "progressive_house", "prog house": "progressive_house",
    "uplifting trance": "uplifting_trance", "uplifting": "uplifting_trance",
    "trance": "trance", "tech house": "tech_house", "techhouse": "tech_house",
    "edm": "edm", "edm comercial": "edm", "big room": "edm",
    "jumpstyle": "jumpstyle", "newstyle": "newstyle", "hardcore": "hardcore",
    "hard dance": "hard_dance", "hardstyle": "hard_dance",
    "melodic techno": "melodic_techno", "techno melódico": "melodic_techno", "techno melodico": "melodic_techno",
}


def resolve_style(text: str | None, styles: dict) -> str:
    if not text:
        return "progressive_house"
    t = text.lower().strip()
    if t in styles:
        return t
    for alias in sorted(STYLE_ALIASES, key=len, reverse=True):
        if alias in t and STYLE_ALIASES[alias] in styles:
            return STYLE_ALIASES[alias]
    return "progressive_house"
