"""Planificador: convierte intenciones en una secuencia ordenada de tareas con categoría de permiso."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..nlp.intents import Intent, parse

_ids = itertools.count(1)


@dataclass
class Task:
    action: str
    category: str
    params: dict = field(default_factory=dict)
    description: str = ""
    area: str = "técnica"  # técnica | musical | sistema
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "planned"
    result: dict | None = None


@dataclass
class Plan:
    text: str
    intents: list[Intent]
    tasks: list[Task] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Orden: {self.text}"]
        tech = [t for t in self.tasks if t.area == "técnica"]
        mus = [t for t in self.tasks if t.area == "musical"]
        sysm = [t for t in self.tasks if t.area == "sistema"]
        for label, group in (("Área técnica", tech), ("Área musical", mus), ("Sistema", sysm)):
            if group:
                lines.append(label + ":")
                lines += [f"  {i}. {t.description}  [{t.category}]" for i, t in enumerate(group, 1)]
        if self.questions:
            lines.append("Preguntas antes de empezar:")
            lines += [f"  - {q}" for q in self.questions]
        if self.limitations:
            lines.append("Limitaciones:")
            lines += [f"  - {x}" for x in self.limitations]
        return "\n".join(lines)


CUBASE_APPLY_LIMIT = ("Aplicar los ajustes dentro de Cubase todavía no es automático: se entrega el plan con valores "
                      "concretos, una previsualización offline y los archivos para importar.")


def _first(files, exts):
    return next((f for f in files if f.lower().endswith(exts)), None)


def build_plan(text: str, ctx: dict) -> Plan:
    intents = parse(text, ctx)
    plan = Plan(text, intents)
    for it in intents:
        e = it.entities
        files = e.get("files", [])
        wavs = [f for f in files if f.lower().endswith(".wav")]
        audio = wavs[0] if wavs else ctx.get("audio_file")
        midi = _first(files, (".mid", ".midi")) or ctx.get("midi_file")
        stems = ctx.get("stems_folder")
        style = e.get("style") or ctx.get("style")
        plan.questions += [q for q in it.questions if q not in plan.questions]
        add = plan.tasks.append
        n = it.name
        if n == "analyze":
            if audio:
                add(Task("analyze_audio", "analysis", {"path": audio}, f"Analizar la mezcla {audio}"))
            if stems:
                add(Task("analyze_stems", "analysis", {"folder": stems}, f"Analizar los stems de {stems}"))
            if midi:
                add(Task("analyze_midi", "analysis", {"path": midi}, f"Analizar la composición MIDI {midi}",
                         area="musical"))
        elif n in ("mix", "mix_fix", "kick_bass"):
            if stems:
                focus = {"mix": "general", "mix_fix": "frecuencias", "kick_bass": "kick_bajo"}[n]
                add(Task("mix_plan", "export_files", {"folder": stems, "focus": focus,
                                                      "bpm": e.get("bpm") or ctx.get("tempo")},
                         "Preparar el plan de mezcla (balance, EQ, dinámica, conflictos) y una previsualización"))
                if CUBASE_APPLY_LIMIT not in plan.limitations:
                    plan.limitations.append(CUBASE_APPLY_LIMIT)
        elif n in ("compose", "climax"):
            secs = e.get("sections")
            if n == "climax":
                secs = secs or ["Build-up", "Clímax", "Drop"]
            add(Task("compose", "compose", {"style": style, "mood": e.get("mood") or ("epico" if n == "climax" else None),
                                            "key": e.get("key") or ctx.get("key"), "bpm": e.get("bpm") or ctx.get("tempo"),
                                            "sections": secs, "seed": ctx.get("seed", 1)},
                     "Componer " + (", ".join(secs) if secs else "el arreglo completo") + " y exportar MIDI multipista",
                     area="musical"))
            add(Task("render_preview", "export_files", {"source": "last_midi"},
                     "Renderizar una previsualización WAV del MIDI generado (sintetizador interno)", area="musical"))
        elif n in ("reharmonize", "keep_rhythm_new_notes", "change_mode", "vary_answer"):
            if midi:
                desc = {"reharmonize": "Rearmonizar conservando el ritmo de todas las pistas",
                        "keep_rhythm_new_notes": "Conservar el ritmo de la melodía y escribir notas nuevas",
                        "change_mode": f"Cambiar el modo a {e.get('new_mode', '?')}",
                        "vary_answer": "Conservar el motivo y reescribir la respuesta de cada frase"}[n]
                add(Task(n, "compose", {"path": midi, "mood": e.get("mood"), "new_mode": e.get("new_mode"),
                                        "bars": e.get("bars")}, desc, area="musical"))
        elif n == "master":
            if audio:
                add(Task("master", "master", {"path": audio, "target": e.get("master_target") or "streaming",
                                              "lufs": e.get("lufs"), "ceiling": e.get("ceiling")},
                         f"Masterizar para {e.get('master_target') or 'streaming'} y verificar el archivo final"))
        elif n == "compare_reference":
            ref = wavs[1] if len(wavs) > 1 else ctx.get("reference_file")
            if audio and ref:
                add(Task("compare_reference", "analysis", {"mix": audio, "reference": ref},
                         "Comparar con la referencia a sonoridad igualada"))
        elif n == "iterate":
            add(Task("evaluate_last", "analysis", {}, "Medir el último resultado, compararlo con el anterior y "
                                                      "corregir si hay regresiones"))
            plan.limitations.append("La evaluación se basa en mediciones; la aplicación no puede escuchar el audio.")
        elif n == "feedback":
            add(Task("feedback", "analysis", {"text": text}, "Registrar tu observación y aplicarla en el próximo ciclo",
                     area="sistema"))
        elif n == "cubase_status":
            add(Task("cubase_status", "analysis", {}, "Detectar Cubase y el proyecto activo", area="sistema"))
        elif n == "scan_plugins":
            add(Task("scan_plugins", "analysis", {}, "Escanear plugins instalados y su arquitectura", area="sistema"))
        elif n == "cubase_save":
            add(Task("cubase_save", "cubase_control", {}, "Guardar el proyecto en Cubase (y verificarlo)", area="sistema"))
        elif n == "restore":
            add(Task("list_restore_points", "analysis", {}, "Mostrar puntos de recuperación y versiones", area="sistema"))
    return plan
