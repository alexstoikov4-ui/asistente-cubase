"""Traducción de intenciones emocionales a decisiones musicales concretas.

Las asociaciones emoción -> armonía son convenciones culturales frecuentes en la música
electrónica occidental, no reglas universales: por eso cada emoción ofrece varias opciones y
el usuario puede forzar modo, progresión o tonalidad.
"""
from __future__ import annotations

MOODS = {
    "emotivo": {
        "label": "Emotivo",
        "mode": "minor",
        "progressions": {
            "minor": ["i-VI-III-VII", "VI-VII-i-i", "i-VI-iv-VII", "VI-III-VII-i"],
            "major": ["vi-IV-I-V", "I-V-vi-IV", "IV-V-vi-I"],
        },
        "extensions": ["add9", "sus2"],
        "contour": "arch", "leap_prob": 0.25, "legato": 0.8, "register_shift": 0,
    },
    "nostalgico": {
        "label": "Nostálgico",
        "mode": "major",
        "progressions": {
            "major": ["IV-I-V-vi", "I-iii-IV-iv", "IVmaj7-iii7-vi7-V", "I-V/3-vi-IV"],
            "minor": ["VI-III-VII-iv", "i-VII-VI-VII"],
        },
        "extensions": ["maj7", "add9"],
        "contour": "descending", "leap_prob": 0.2, "legato": 0.9, "register_shift": -2,
    },
    "euforico": {
        "label": "Eufórico",
        "mode": "major",
        "progressions": {
            "major": ["I-V-vi-IV", "vi-IV-I-V", "IV-V-vi-vi", "I-IV-vi-V"],
            "minor": ["VI-VII-i-i", "i-VII-VI-VII", "III-VII-VI-VII"],
        },
        "extensions": ["sus4", "add9"],
        "contour": "ascending", "leap_prob": 0.35, "legato": 0.6, "register_shift": 2,
    },
    "melancolico": {
        "label": "Melancólico",
        "mode": "minor",
        "progressions": {
            "minor": ["i-iv-VI-v", "i-VI-III-VII", "i-v-VI-iv", "iv-i-VII-VI"],
            "major": ["vi-iii-IV-I", "vi-IV-ii-iii"],
        },
        "extensions": ["min7", "add9"],
        "contour": "descending", "leap_prob": 0.15, "legato": 0.9, "register_shift": -3,
    },
    "esperanzador": {
        "label": "Esperanzador",
        "mode": "major",
        "progressions": {
            "major": ["vi-IV-I-V", "IV-V-vi-I", "I-V-IV-IV", "IV-I-V-V"],
            "minor": ["VI-VII-III-III", "iv-VI-VII-III"],
        },
        "extensions": ["sus2", "add9"],
        "contour": "ascending", "leap_prob": 0.3, "legato": 0.75, "register_shift": 1,
    },
    "oscuro": {
        "label": "Oscuro",
        "mode": "minor",
        "progressions": {
            "minor": ["i-bII-i-VII", "i-VI-iv-V7", "i-i-VI-bII", "i-iv-i-VI"],
            "major": ["vi-bVI-vi-V"],
        },
        "extensions": ["min7"],
        "contour": "low", "leap_prob": 0.2, "legato": 0.5, "register_shift": -5,
    },
    "epico": {
        "label": "Épico",
        "mode": "minor",
        "progressions": {
            "minor": ["i-VI-III-VII", "VI-VII-i-i", "i-VII-VI-VII", "VI-VII-V/V-V"],
            "major": ["I-V-vi-IV", "IV-V-V/vi-vi"],
        },
        "extensions": ["sus4"],
        "contour": "ascending", "leap_prob": 0.4, "legato": 0.7, "register_shift": 3,
    },
    "agridulce": {
        "label": "Agridulce",
        "mode": "major",
        "progressions": {
            "major": ["I-V-vi-iv", "IV-iv-I-V", "I-iii-IV-iv", "vi-IV-I-bVII"],
            "minor": ["i-VI-III-V", "VI-iv-i-V"],
        },
        "extensions": ["maj7", "add9"],
        "contour": "arch", "leap_prob": 0.25, "legato": 0.8, "register_shift": 0,
    },
}

ALIASES = {
    "emotiva": "emotivo", "emocional": "emotivo", "emotivo": "emotivo",
    "nostalgica": "nostalgico", "nostálgico": "nostalgico", "nostálgica": "nostalgico",
    "euforica": "euforico", "eufórico": "euforico", "eufórica": "euforico", "festivalero": "euforico",
    "melancolica": "melancolico", "melancólico": "melancolico", "melancólica": "melancolico", "triste": "melancolico",
    "esperanzadora": "esperanzador", "optimista": "esperanzador",
    "oscura": "oscuro", "dark": "oscuro", "siniestro": "oscuro",
    "épico": "epico", "épica": "epico", "epica": "epico",
    "agridulce": "agridulce", "bittersweet": "agridulce",
}


def resolve_mood(word: str | None) -> str:
    if not word:
        return "emotivo"
    w = word.lower().strip()
    if w in MOODS:
        return w
    return ALIASES.get(w, "emotivo")
