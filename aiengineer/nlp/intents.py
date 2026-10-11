"""Intérprete local de órdenes en español (reglas, sin conexión).

Convierte una orden en una o varias intenciones con entidades (estilo, emoción, tonalidad,
secciones, objetivos de loudness, archivos, restricciones de "conservar"). Si falta información
imprescindible devuelve preguntas breves. Diseñado para poder sustituirse o complementarse con un
modelo de lenguaje local o remoto sin cambiar el planificador.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..compose.moods import ALIASES as MOOD_ALIASES, MOODS
from ..compose.styles import STYLE_ALIASES


def norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


@dataclass
class Intent:
    name: str
    entities: dict = field(default_factory=dict)
    questions: list = field(default_factory=list)
    clause: str = ""


SECTION_WORDS = {"intro": "Intro", "drop": "Drop", "breakdown": "Breakdown", "build": "Build-up", "subida": "Build-up",
                 "climax": "Clímax", "outro": "Outro", "estribillo": "Drop", "puente": "Breakdown", "verso": "Verso"}
TARGET_WORDS = {"festival": "festival", "club": "club", "discoteca": "club", "streaming": "streaming",
                "spotify": "streaming", "youtube": "streaming", "apple": "streaming", "cd": "cd",
                "comercial": "comercial", "radio": "comercial"}
KEEP_WORDS = {"ritmo": "rhythm", "intro": "intro", "motivo": "motif", "tempo": "tempo", "melodia": "melody",
              "armonia": "harmony", "estructura": "structure", "drop": "drop", "bajo": "bass"}

# (intención, patrones). El orden importa: lo más específico primero.
RULES: list[tuple[str, list[str]]] = [
    ("feedback", [r"\bha perdido\b", r"\bdemasiad[oa]s?\b.*\b(pegada|grave|brillo|comprimid|ancho|reverb)",
                  r"\bsigue siendo\b", r"\bsuena (demasiado|muy|poco)\b", r"\bme gusta\b", r"\bno me gusta\b",
                  r"\bquiero mas\b.*\by menos\b", r"\bmenos sensacion\b"]),
    ("screen_task", [r"^en cubase\b", r"\b(abre|abrir|muestra|cierra)\b.{0,20}\b(mezclador|mixer|panel de transporte|"
                     r"conexiones vst|instrumentos vst)\b", r"\b(dale al play|reproduce el proyecto|para la reproduccion)\b", r"\b(haz clic|pulsa el boton|abre el (mezclador|menu)|cierra la ventana)\b",
                     r"\b(inserta|carga|pon|abre|ajusta|mueve|selecciona|activa|desactiva)\b.{0,40}\b(plugin|pro-?q|saturn|"
                     r"limiter|waves|mezclador|fader|insert|ranura|canal|ventana|envio|eq de cubase)\b"]),
    ("cubase_status", [r"\b(estado|esta abierto|detecta).{0,20}cubase\b", r"\bcubase\b.{0,20}\b(abierto|estado)\b"]),
    ("cubase_save", [r"\bguarda(r)? (el )?proyecto\b"]),
    ("scan_plugins", [r"\b(escanea|detecta|busca|lista)\b.{0,20}\bplugins?\b", r"\bque plugins\b"]),
    ("restore", [r"\b(deshaz|deshacer|restaura|vuelve a la version)\b"]),
    ("compare_reference", [r"\bcompara\b", r"\breferencia\b"]),
    ("master", [r"\bmasteriza", r"\bmaster(izacion)?\b", r"\bversion masterizada\b", r"\bloudness\b"]),
    ("kick_bass", [r"\bkick\b.*\bbajo\b.*\b(pegada|punch|profundidad)", r"\bbajo\b.*\bkick\b.*\b(pegada|punch)"]),
    ("mix_fix", [r"\bbalance de frecuencias\b", r"\benmascaramiento\b", r"\bproblemas de mezcla\b",
                 r"\bcorrige\b.*\bmezcla\b", r"\bcorrige\b.*\bbalance\b"]),
    ("mix", [r"\bmezcla(r)?\b(?!.*\breferencia\b)", r"\bmezcla todas\b", r"\bprepara el procesamiento\b"]),
    ("keep_rhythm_new_notes", [r"\b(conserva|manten|mantener|deja)\b.{0,30}\britmo\b.{0,40}\b(cambia|nuevas?)\b.{0,15}\bnotas\b"]),
    ("vary_answer", [r"\brespuesta (diferente|distinta|nueva)\b", r"\bsegunda frase\b"]),
    ("change_mode", [r"\bcambia (el )?modo\b", r"\bmodo (mayor|menor|dorico|frigio|lidio|mixolidio)\b"]),
    ("reharmonize", [r"\b(rehaz|rehacer|cambia|reescribe|rearmoniza|transforma)\b.{0,20}\barmonia\b",
                     r"\barmonia mas\b", r"\bprogresion\b.{0,30}\b(mas|version)\b", r"\bacordes con mas\b"]),
    ("climax", [r"\bclimax\b", r"\b(crea|haz|compon|mejora)\b.{0,40}\btransicion"]),
    ("compose", [r"\b(compon|componer|crea|genera|haz|escribe|desarrolla)\b.{0,40}\b(drop|tema|cancion|melodia|"
                 r"progresion|track|intro|breakdown|arreglo|build|outro|pista)\b", r"\bdesarrolla la melodia\b",
                 r"\bmejora la estructura\b"]),
    ("iterate", [r"\bescucha el resultado\b", r"\bsigue trabajando\b", r"\bhasta mejorarlo\b", r"\bitera\b"]),
    ("analyze", [r"\banaliza\b", r"\bque problemas\b", r"\brevisa\b", r"\bidentifica lo que\b", r"\bdiagnostic"]),
]

PIPELINE_ORDER = ["screen_task", "cubase_status", "scan_plugins", "feedback", "analyze", "compare_reference", "mix_fix", "mix",
                  "kick_bass", "reharmonize", "keep_rhythm_new_notes", "change_mode", "vary_answer", "compose", "climax",
                  "master", "iterate", "cubase_save", "restore"]


def extract_entities(text: str) -> dict:
    t = norm(text)
    ent: dict = {}
    for alias in sorted(STYLE_ALIASES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(norm(alias)) + r"\b", t):
            ent["style"] = STYLE_ALIASES[alias]
            break
    mood_hits = []
    for word in set(MOOD_ALIASES) | set(MOODS):
        m = re.search(r"\b" + re.escape(norm(word)) + r"\b", t)
        if m:
            mood_hits.append((m.start(), -len(word), MOOD_ALIASES.get(word, word)))
    if mood_hits:
        ent["mood"] = min(mood_hits)[2]  # la primera emoción mencionada
    m = re.search(r"\b(?:en|tonalidad( de)?)\s+((?:do|re|mi|fa|sol|la|si|[a-g])(?:#|b| sostenido| bemol)?)\s*"
                  r"(mayor|menor|major|minor|m\b|dorico|frigio|lidio|mixolidio)", t)
    if m:
        note = m.group(2).replace(" sostenido", "#").replace(" bemol", "b")
        mode = {"m": "menor"}.get(m.group(3), m.group(3))
        ent["key"] = f"{note} {mode}"
    m = re.search(r"(\d{2,3}(?:[.,]\d+)?)\s*bpm", t)
    if m:
        ent["bpm"] = float(m.group(1).replace(",", "."))
    secs = []
    for w, name in SECTION_WORDS.items():
        if re.search(r"\b" + w, t) and name not in secs:
            secs.append(name)
    if secs:
        ent["sections"] = secs
    for w, tgt in TARGET_WORDS.items():
        if re.search(r"\b" + w + r"\b", t):
            ent["master_target"] = tgt
            break
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*lufs", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        ent["lufs"] = -abs(v)
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*dbtp", t)
    if m:
        ent["ceiling"] = -abs(float(m.group(1).replace(",", ".")))
    keep = []
    for m in re.finditer(r"\b(conserva|conservando|manten|manteniendo|sin cambiar|respeta)\b\s+(?:el |la |los |las )?(\w+)", t):
        k = KEEP_WORDS.get(m.group(2))
        if k and k not in keep:
            keep.append(k)
    if keep:
        ent["keep"] = keep
    files = re.findall(r"[\"']([^\"']+\.(?:wav|mid|midi))[\"']", text, re.IGNORECASE)
    unquoted = re.sub(r"[\"'][^\"']+[\"']", " ", text)
    files += [f for f in re.findall(r"(\S+\.(?:wav|mid|midi))\b", unquoted, re.IGNORECASE) if f not in files]
    if files:
        ent["files"] = files
    m = re.search(r"\b(?:modo|a)\s+(mayor|menor|dorico|frigio|lidio|mixolidio)\b", t)
    if m:
        ent["new_mode"] = {"mayor": "major", "menor": "minor", "dorico": "dorian", "frigio": "phrygian",
                           "lidio": "lydian", "mixolidio": "mixolydian"}[m.group(1)]
    m = re.search(r"\bcompases?\s+(\d+)\s*(?:a|-|al)\s*(\d+)", t)
    if m:
        ent["bars"] = (int(m.group(1)), int(m.group(2)))
    return ent


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[,;.]|\by luego\b|\bdespues\b|\by (?=(?:corrige|mezcla|masteriza|compon|crea|analiza|mejora|"
                     r"desarrolla|prepara|haz|rehaz|compara)\b)", norm(text))
    return [p.strip() for p in parts if p and p.strip()]


def parse(text: str, context: dict | None = None) -> list[Intent]:
    """Devuelve las intenciones de la orden, ordenadas según el flujo de trabajo."""
    context = context or {}
    ent_all = extract_entities(text)
    found: dict[str, Intent] = {}
    whole = norm(text)
    if re.search(r"\b(conserva|manten|mantener|deja)\w*\b.{0,30}\britmo\b", whole) and \
            re.search(r"\b(cambia|nuevas?|otras)\b.{0,15}\bnotas\b", whole):
        found["keep_rhythm_new_notes"] = Intent("keep_rhythm_new_notes", dict(ent_all), clause=whole)
    for clause in split_clauses(text) or [whole]:
        for name, pats in RULES:
            if any(re.search(p, clause) for p in pats):
                if name not in found:
                    found[name] = Intent(name, dict(ent_all), clause=clause)
                break
    if not found:
        return [Intent("unknown", ent_all, ["No he entendido la orden. ¿Quieres analizar, mezclar, componer o masterizar?"],
                       clause=norm(text))]
    intents = sorted(found.values(), key=lambda i: PIPELINE_ORDER.index(i.name) if i.name in PIPELINE_ORDER else 99)
    for it in intents:
        it.questions = _questions(it, context)
    return intents


def _questions(it: Intent, ctx: dict) -> list[str]:
    q = []
    e = it.entities
    has_audio = bool(ctx.get("audio_file") or ctx.get("stems_folder") or any(f.lower().endswith(".wav") for f in e.get("files", [])))
    has_midi = bool(ctx.get("midi_file") or any(f.lower().endswith((".mid", ".midi")) for f in e.get("files", [])))
    if it.name in ("master", "compare_reference") and not has_audio:
        q.append("¿Qué archivo WAV de la mezcla quieres usar?")
    if it.name == "compare_reference" and not (ctx.get("reference_file") or len([f for f in e.get("files", []) if f.lower().endswith(".wav")]) >= 2):
        q.append("¿Cuál es el archivo de referencia (WAV)?")
    if it.name in ("mix", "mix_fix", "kick_bass") and not ctx.get("stems_folder"):
        q.append("¿En qué carpeta están los stems (WAV) de las pistas?")
    if it.name in ("reharmonize", "keep_rhythm_new_notes", "change_mode", "vary_answer") and not has_midi:
        q.append("¿Qué archivo MIDI quieres transformar?")
    if it.name == "analyze" and not (has_audio or has_midi):
        q.append("¿Qué quieres analizar: un WAV de la mezcla, una carpeta de stems o un MIDI?")
    if it.name in ("compose", "climax") and not (e.get("style") or ctx.get("style")):
        q.append("¿En qué estilo? (por defecto: progressive house)")
    if it.name == "change_mode" and not e.get("new_mode"):
        q.append("¿A qué modo quieres cambiar (mayor, menor, dórico...)?")
    return q
