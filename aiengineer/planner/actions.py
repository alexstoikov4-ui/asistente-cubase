"""Acciones ejecutables. Cada una devuelve un resultado con estado verificable, nunca simulado.

Estados: verified (comprobado tras ejecutar), done (ejecutado; no requiere verificación externa),
unverified (enviado pero sin forma de comprobarlo), failed, skipped.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..audio.analyzer import analyze_file
from ..audio.render import render
from ..audio.wavio import write_wav
from ..compose import transform
from ..compose.analysis import analyze_song
from ..compose.arranger import Arranger, CompositionRequest, describe
from ..cubase.bridge import deliver
from ..evaluate.compare import compare_to_reference, evaluate_change
from ..master.engine import MasterSettings, master_file, plugin_chain_suggestion
from ..midi.smf import read_midi, validate_roundtrip
from ..mix.engine import plan_mix, plan_to_markdown, write_preview
from ..plugins.scanner import default_vst_dirs, roles_index, save_inventory, scan
from ..project.files import unique_path


def result(status: str, summary: str, artifacts=None, data=None) -> dict:
    return {"status": status, "summary": summary, "artifacts": artifacts or [], "data": data or {}}


def _out(engine, name: str) -> Path:
    d = engine.output_dir()
    return unique_path(d / name)


def _save_json(engine, name: str, obj) -> str:
    p = _out(engine, name)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return str(p)


def analyze_audio(engine, params) -> dict:
    rep = analyze_file(params["path"])
    path = _save_json(engine, f"analisis_{Path(params['path']).stem}.json", rep)
    m = rep["measurements"]
    lou, dyn = m["loudness"], m["dynamics"]
    lines = [f"{Path(params['path']).name}: {lou['integrated_lufs']:.1f} LUFS, true peak {lou['true_peak_dbtp']:.2f} dBTP, "
             f"cresta {dyn['crest_factor_db']:.1f} dB, LRA {lou['lra_lu'] if lou['lra_lu'] is not None else '-'} LU"]
    if m.get("tempo", {}).get("bpm"):
        lines.append(f"Tempo estimado {m['tempo']['bpm']} BPM (confianza {m['tempo']['confidence']}); "
                     f"tonalidad estimada {m['key'].get('key')} (alternativa {m['key'].get('alternative')}).")
    for o in rep["observations"]:
        lines.append(f"[{o['kind']}/{o['severity']}] {o['text']}")
    if not rep["observations"]:
        lines.append("No se han encontrado problemas técnicos claros en las mediciones.")
    engine.remember("problems_detected", [o["text"] for o in rep["observations"]])
    engine.context["last_analysis"] = m
    engine.context["audio_file"] = params["path"]
    return result("done", "\n".join(lines), [path], {"report": rep})


def analyze_stems(engine, params) -> dict:
    plan = plan_mix(Path(params["folder"]))
    lines = [f"{len(plan['stems'])} pistas: " + ", ".join(f"{s['name']} ({s['role']})" for s in plan["stems"])]
    lines += [f"Aviso: {w}" for w in plan["warnings"]]
    lines += [f"Conflicto: {c['solution']['text']}" for c in plan["conflicts"]]
    engine.remember("tracks", [{"name": s["name"], "role": s["role"]} for s in plan["stems"]])
    return result("done", "\n".join(lines), [], {"plan": {k: v for k, v in plan.items() if k != "_preview"}})


def analyze_midi(engine, params) -> dict:
    song = read_midi(params["path"])
    a = analyze_song(song)
    path = _save_json(engine, f"analisis_{Path(params['path']).stem}_midi.json", a)
    lines = [f"{a['bars']} compases, {a['bpm']:g} BPM, {a['time_signature']}, tonalidad estimada {a['key'].get('key')} "
             f"(o su relativa {a['key'].get('relative_alternative')})",
             "Pistas: " + ", ".join(f"{t['name']}={t['role']}" for t in a["tracks"]),
             "Acordes (primeros 8 compases): " + " | ".join(str(c["chord"]) for c in a["chords"][:8])]
    lines += [f"[{p['kind']}] {p['track'] or 'general'}: {p['text']}" for p in a["problems"]]
    engine.context["midi_file"] = params["path"]
    engine.remember("tempo", a["bpm"])
    if a["key"].get("key"):
        engine.remember("key", a["key"]["key"])
    return result("done", "\n".join(lines), [path], {"analysis": a})


def mix_plan(engine, params) -> dict:
    fb = engine.memory_list("feedback")
    plan = plan_mix(Path(params["folder"]), bpm=params.get("bpm"), feedback=fb)
    if params.get("focus") == "kick_bajo":
        plan["conflicts"] = [c for c in plan["conflicts"] if {"kick", "bass", "sub"} & {
            s["role"] for s in plan["stems"] if s["name"] in (c["a"], c["b"])}] or plan["conflicts"]
    md = plan_to_markdown(plan)
    avail = roles_index(engine.plugins)
    md += "\n\n## Plugins disponibles por función\n" + "\n".join(f"- {r}: {', '.join(v)}" for r, v in sorted(avail.items())) \
        if avail else "\n\n(No hay inventario de plugins: ejecuta 'escanea los plugins' para sugerir plugins concretos.)"
    p_md = _out(engine, "plan_mezcla.md")
    p_md.write_text(md, encoding="utf-8")
    p_json = _save_json(engine, "plan_mezcla.json", {k: v for k, v in plan.items() if k != "_preview"})
    arts = [str(p_md), p_json]
    prev = write_preview(plan, _out(engine, "previsualizacion_mezcla.wav"))
    status = "done"
    lines = [f"Plan de mezcla para {len(plan['stems'])} pistas con {len(plan['conflicts'])} conflictos resueltos."]
    if prev:
        arts.append(prev)
        rep = analyze_file(prev, musical=False)
        engine.context["audio_file"] = prev
        engine.context["previous_analysis"] = engine.context.get("last_analysis")
        engine.context["last_analysis"] = rep["measurements"]
        status = "verified"
        lines.append(f"Previsualización offline: {rep['measurements']['loudness']['integrated_lufs']:.1f} LUFS, "
                     f"pico {rep['measurements']['dynamics']['sample_peak_dbfs']:.1f} dBFS (margen para el máster comprobado).")
        lines += [f"  [{o['kind']}] {o['text']}" for o in rep["observations"]]
    lines.append("Los ajustes deben aplicarse en Cubase (el plan indica valores y plugins); la aplicación automática "
                 "dentro del DAW está pendiente.")
    engine.remember("processing_applied", {"ts": time.time(), "type": "plan_mezcla", "file": str(p_md)})
    return result(status, "\n".join(lines), arts, {"plan": {k: v for k, v in plan.items() if k != "_preview"}})


def compose(engine, params) -> dict:
    prefs = engine.memory_list("user_preferences")
    req = CompositionRequest(style=params.get("style"), mood=params.get("mood"), key=params.get("key"),
                             bpm=params.get("bpm"), sections=params.get("sections"), seed=int(params.get("seed") or 1))
    if "melodia_menos_lineal" in prefs:
        req.lead_overrides.update({"leap_prob": 0.45, "syncopation": 0.65})
    if "mas_emocion" in prefs:
        req.extra_extensions += ["add9", "sus4"]
    arranger = Arranger(engine.styles)
    song, info = arranger.compose(req)
    name = f"{info.style}_{info.mood}_{int(time.time())}.mid"
    path = _out(engine, name)
    check = validate_roundtrip(song, path)
    if not check["ok"]:
        return result("failed", "El MIDI generado no superó la verificación: " + "; ".join(check["issues"]), [str(path)])
    delivered = deliver(path, engine.config.exchange_dir, "midi")
    engine.context["midi_file"] = str(path)
    engine.context["last_song"] = song
    engine.context["style"] = info.style
    for k, v in (("style", info.style), ("tempo", info.bpm), ("key", info.key),
                 ("structure", [f"{s['name']} ({s['bars']})" for s in info.sections])):
        engine.remember(k, v)
    engine.remember("generated_files", str(path))
    text = describe(info) + f"\nMIDI verificado ({check['tracks']} pistas, {check['notes']} notas): {path}\n" \
        f"Copia para Cubase: {delivered['file']}\n" + "\n".join(f"  · {s}" for s in delivered["steps"])
    return result("verified", text, [str(path), delivered["file"]], {"info": info.__dict__})


def render_preview(engine, params) -> dict:
    song = engine.context.get("last_song")
    if song is None:
        src = engine.context.get("midi_file")
        if not src:
            return result("skipped", "No hay MIDI que previsualizar.")
        song = read_midi(src)
    x = render(song)
    peak = float(abs(x).max()) or 1.0
    x = x / peak * 10 ** (-3 / 20)
    path = _out(engine, Path(engine.context.get("midi_file", "preview")).stem + "_preview.wav")
    write_wav(path, x, 44100, bits=24)
    return result("verified" if path.exists() else "failed",
                  f"Previsualización (sintetizador interno, no tus VST): {path}", [str(path)])


def _transform_common(engine, params, new_song, label, log=None) -> dict:
    src = Path(params["path"])
    out = _out(engine, f"{src.stem}_{label}.mid")
    check = validate_roundtrip(new_song, out)
    if not check["ok"]:
        return result("failed", "; ".join(check["issues"]), [str(out)])
    delivered = deliver(out, engine.config.exchange_dir, "midi")
    engine.context["midi_file"] = str(out)
    engine.context["last_song"] = new_song
    engine.remember("generated_files", str(out))
    lines = (log or []) + [f"Original intacto: {src}", f"Nueva versión verificada: {out}",
                           f"Copia para Cubase: {delivered['file']}"]
    return result("verified", "\n".join(lines), [str(out), delivered["file"]])


def _lead_track(song):
    from ..compose.analysis import identify_role
    return next((t.name for t in song.tracks if identify_role(t) == "lead"), song.tracks[0].name if song.tracks else None)


def reharmonize(engine, params) -> dict:
    song = read_midi(params["path"])
    new, log = transform.reharmonize(song, mood=params.get("mood"), seed=engine.context.get("seed", 1))
    return _transform_common(engine, params, new, "rearmonizado", log)


def keep_rhythm_new_notes(engine, params) -> dict:
    src = engine.context.get("midi_file") if engine.context.get("midi_file", "").endswith("_rearmonizado.mid") else params["path"]
    song = read_midi(src)
    lead = _lead_track(song)
    if not lead:
        return result("failed", "El MIDI no tiene pistas melódicas.")
    new = transform.keep_rhythm_new_pitches(song, lead, seed=engine.context.get("seed", 1), mood=params.get("mood"),
                                            bars=params.get("bars"))
    return _transform_common(engine, {"path": src}, new, "notas_nuevas", [f"Pista transformada: {lead} (ritmo intacto)"])


def change_mode(engine, params) -> dict:
    if not params.get("new_mode"):
        return result("skipped", "Falta el modo de destino.")
    song = read_midi(params["path"])
    new = transform.change_mode(song, params["new_mode"], params.get("bars"))
    return _transform_common(engine, params, new, f"modo_{params['new_mode']}")


def vary_answer(engine, params) -> dict:
    song = read_midi(params["path"])
    lead = _lead_track(song)
    new = transform.vary_answer(song, lead, seed=engine.context.get("seed", 1))
    return _transform_common(engine, params, new, "respuesta_nueva", [f"Pista: {lead}; motivo conservado."])


def master(engine, params) -> dict:
    from ..master.engine import TARGETS
    ref_bands = engine.context.get("reference_bands")
    st = MasterSettings(target=params.get("target") or "streaming", lufs=params.get("lufs"),
                        ceiling_dbtp=params.get("ceiling"), reference_bands=ref_bands)
    pref_note = ""
    if "menos_compresion" in engine.memory_list("user_preferences"):
        if params.get("lufs") is None:
            st.lufs = TARGETS.get(st.target, TARGETS["streaming"])["lufs"] - 1.5
        st.allow_saturation = False
        st.allow_compression = False
        pref_note = f"Preferencia aplicada (menos compresión): objetivo {st.lufs} LUFS, sin compresión ni saturación de bus."
    out = _out(engine, f"{Path(params['path']).stem}_master_{st.target}.wav")
    res = master_file(params["path"], out, st, stop_flag=engine.stop_event)
    if not res.output:
        return result("failed", "\n".join(res.warnings) or "No se pudo masterizar.")
    chain = plugin_chain_suggestion(res.chain, roles_index(engine.plugins))
    report = {"before": res.before, "after": res.after, "chain": res.chain, "stages": res.stages,
              "comparison": res.comparison, "warnings": res.warnings, "plugin_chain": chain}
    rp = _save_json(engine, f"master_{st.target}_informe.json", report)
    c = res.comparison
    lines = [f"Máster ({st.target}): {c['achieved_lufs']:.1f} LUFS (objetivo {c['target_lufs']}), true peak "
             f"{res.after['loudness']['true_peak_dbtp']:.2f} dBTP, verificación {'OK' if res.verified else 'FALLIDA'} "
             f"({', '.join(k for k, v in c['checks'].items() if v)}).",
             f"A sonoridad igualada: cambio de cresta {c.get('crest_change_db')} dB, transitorios {c.get('transient_crest_change_db')} dB.",
             "Cadena decidida:"] + [f"  - {s['stage']}: {s.get('why', '')}" for s in res.chain]
    lines += ["Equivalente con tus plugins:"] + [f"  - {p['stage']}: {p['plugin']} {p['settings']}" for p in chain]
    lines += [f"Aviso: {w}" for w in res.warnings]
    if pref_note:
        lines.append(pref_note)
    lines.append("El archivo es un máster de referencia hecho con el procesado interno; la cadena equivalente en "
                 "Cubase debe configurarse con los valores indicados.")
    engine.context["previous_analysis"] = res.before
    engine.context["last_analysis"] = res.after
    engine.context["last_master"] = {"path": str(out), "params": params, "comparison": c, "warnings": res.warnings}
    engine.remember("processing_applied", {"ts": time.time(), "type": "master", "target": st.target,
                                           "lufs": c["achieved_lufs"], "file": str(out)})
    return result("verified" if res.verified else "failed", "\n".join(lines), [str(out), rp], {"report": report})


def compare_reference(engine, params) -> dict:
    rep = compare_to_reference(params["mix"], params["reference"])
    engine.context["reference_bands"] = rep["reference_bands_pct"]
    engine.context["reference_file"] = params["reference"]
    engine.remember("references", params["reference"])
    path = _save_json(engine, "comparacion_referencia.json", {k: v for k, v in rep.items()
                                                              if k not in ("mix_measurements", "reference_measurements")})
    lines = [rep["note"], "Diferencias por banda (dB, mezcla - referencia): " +
             ", ".join(f"{k} {v:+.1f}" for k, v in rep["band_difference_db"].items())]
    lines += [f"- [{p['area']}] {p['text']}" for p in rep["proposals"]] or ["Sin diferencias relevantes."]
    lines.append("Las bandas de la referencia se usarán como orientación (a mitad de camino y ±2 dB máx.) si después "
                 "pides masterizar.")
    return result("done", "\n".join(lines), [path], {"comparison": rep})


def evaluate_last(engine, params) -> dict:
    """Mide el último resultado y decide si hay regresiones; corrige automáticamente las que sabe corregir."""
    before, after = engine.context.get("previous_analysis"), engine.context.get("last_analysis")
    if not before or not after:
        return result("skipped", "No hay dos resultados que comparar todavía (ejecuta antes una mezcla o un máster).")
    lm = engine.context.get("last_master")
    lines = ["Medido (no escuchado):"]
    if lm and lm.get("path") and Path(lm["path"]).exists():
        # Un máster sube el nivel por diseño: se juzga a sonoridad igualada y contra el techo pedido.
        c = lm["comparison"]
        lines += [f"  comprobaciones del archivo final: " + ", ".join(f"{k}={'OK' if v else 'FALLA'}" for k, v in c["checks"].items()),
                  f"  a sonoridad igualada: cresta {c.get('crest_change_db')} dB, transitorios {c.get('transient_crest_change_db')} dB",
                  f"  sonoridad: {c['achieved_lufs']:.1f} LUFS (objetivo {c['target_lufs']})"]
        lost_punch = (c.get("transient_crest_change_db") or 0) < -3
        failed = not all(c["checks"].values())
        if (lost_punch or failed) and not params.get("retry"):
            p = dict(lm["params"])
            p["lufs"] = round(c["achieved_lufs"] - 1.5, 1)
            engine.record_decision(f"master_{p.get('target')}_{c['achieved_lufs']:.1f}", approved=True, effective=False)
            lines.append(f"Regresión detectada ({'pérdida de pegada' if lost_punch else 'verificación fallida'}): "
                         f"se repite el máster a {p['lufs']} LUFS.")
            r2 = master(engine, p)
            lines.append(r2["summary"])
            return result(r2["status"], "\n".join(lines), r2["artifacts"])
        lines.append("Sin regresiones en el máster: pegada conservada y archivo final verificado.")
        return result("done", "\n".join(lines))
    ev = evaluate_change(before, after)
    lines += [f"  mejora {i['metric']}: {i['before']} -> {i['after']}" for i in ev["improved"]]
    lines += [f"  empeora {i['metric']}: {i['before']} -> {i['after']}" for i in ev["regressed"]]
    lines += ev["notes"]
    if not ev["regressed"]:
        lines.append("No se detectan regresiones en las métricas comprobadas.")
    return result("done", "\n".join(lines), [], {"evaluation": ev})


FEEDBACK_RULES = [
    (("bajo", "cuerpo"), "bajo_mas_cuerpo", "En la próxima mezcla el bajo sube ~1,5 dB y no se le recortan graves."),
    (("kick", "demasiad"), "kick_menos_pegada", "En la próxima mezcla el kick baja ~1,5 dB y su compresión es más suave."),
    (("lineal",), "melodia_menos_lineal", "Las próximas melodías tendrán más saltos, síncopa y silencios."),
    (("mecanic",), "melodia_menos_lineal", "Las próximas melodías tendrán más variación rítmica."),
    (("comprimid",), "menos_compresion", "Próximos másteres: objetivo de sonoridad 1,5 LU más bajo y sin compresión de bus."),
    (("emocion",), "mas_emocion", "Próximas composiciones: más extensiones (add9/sus) y clímax más alto."),
]


def feedback(engine, params) -> dict:
    from ..nlp.intents import norm
    t = norm(params["text"])
    engine.remember("feedback", params["text"])
    applied = []
    for words, pref, expl in FEEDBACK_RULES:
        if all(w in t for w in words):
            engine.remember("user_preferences", pref)
            applied.append(expl)
    if not applied:
        return result("done", "Observación guardada en la memoria del proyecto. No tengo una regla automática para "
                              "ella; se mostrará en los próximos informes.")
    return result("done", "Observación guardada. " + " ".join(applied))


def cubase_status(engine, params) -> dict:
    st = engine.cubase.status()
    if st.project_name:
        engine.remember("cubase_project", st.project_name)
    return result("done", st.summary() + ("\n" + "\n".join(st.notes) if st.notes else ""), [], {"status": st.__dict__})


def scan_plugins(engine, params) -> dict:
    st = engine.cubase.status()
    dirs = default_vst_dirs() + [Path(p) for p in engine.config.settings.extra_vst_paths]
    if not dirs:
        return result("skipped", "No se han encontrado carpetas de plugins. Añade rutas en Configuración > Plugins.")
    plugins = scan(dirs, host_arch=st.arch)
    engine.plugins = plugins
    inv = engine.config.home / "plugins.json"
    save_inventory(plugins, inv)
    idx = roles_index(plugins)
    incompatible = [p.name for p in plugins if p.compatible is False]
    lines = [f"{len(plugins)} plugins encontrados en {len(dirs)} carpetas (arquitectura de Cubase: {st.arch or 'desconocida'})."]
    lines += [f"  {r}: {', '.join(v[:8])}" for r, v in sorted(idx.items())]
    if incompatible:
        lines.append("No cargables en tu Cubase: " + ", ".join(incompatible[:20]))
    return result("done", "\n".join(lines), [str(inv)])


def cubase_save(engine, params) -> dict:
    cpr = engine.project.memory.get("cubase_project") if engine.project else None
    r = engine.cubase.save_project(cpr if cpr and cpr.lower().endswith(".cpr") else None)
    return result(r.status, r.detail)


def list_restore_points(engine, params) -> dict:
    if not engine.project:
        return result("skipped", "No hay proyecto abierto.")
    bks = engine.project.backups.list()
    vers = engine.project.versions()
    lines = [f"{len(vers)} versiones de la memoria del proyecto; {len(bks)} puntos de recuperación de archivos."]
    lines += [f"  {b['id']}: {b['reason']} ({len(b['files'])} archivos)" for b in bks[-10:]]
    lines.append("Para restaurar, usa el historial (GUI) o: python -m aiengineer restore <id>.")
    return result("done", "\n".join(lines))


def screen_task(engine, params) -> dict:
    """Ejecuta una tarea en Cubase mirando la pantalla y usando ratón/teclado (modelo de visión local)."""
    from ..screen.agent import ScreenAgent, result_json
    from ..screen.brains import OllamaBrain
    from ..screen.desktop import DesktopUnavailable, WindowsDesktop
    st = engine.config.settings
    try:
        desktop = engine.context.get("_desktop") or WindowsDesktop()
    except DesktopUnavailable as e:
        return result("skipped", str(e))
    brain = engine.context.get("_brain")
    if brain is None:
        models = OllamaBrain.available_models(st.ollama_url)
        if not models:
            return result("skipped", "Ollama no está en marcha o no tiene modelos. Instala Ollama (gratis) y un modelo "
                                     "de visión, p.ej. 'ollama pull qwen2.5vl:7b', y elígelo en 'Control de pantalla'.")
        model = st.vision_model if st.vision_model in models else None
        if model is None:
            return result("skipped", "Elige un modelo de visión en la pestaña 'Control de pantalla'. Instalados: "
                                     + ", ".join(models))
        brain = OllamaBrain(model, st.ollama_url)
    log_dir = engine.output_dir() / "agente" / time.strftime("%Y%m%d-%H%M%S")
    agent = ScreenAgent(desktop, brain, engine.stop_event, max_steps=int(params.get("max_steps") or st.agent_max_steps),
                        log_dir=log_dir, progress=engine.context.get("_progress"),
                        confirm=engine.context.get("_confirm"))
    res = agent.run(params["task"])
    (log_dir / "registro.json").parent.mkdir(parents=True, exist_ok=True)
    (log_dir / "registro.json").write_text(result_json(res), encoding="utf-8")
    status = {"done_by_model": "unverified", "stopped": "cancelled"}.get(res.status, "failed")
    lines = [res.message, f"{len(res.steps)} acciones; capturas de cada paso en {log_dir}"]
    return result(status, "\n".join(lines), [str(log_dir / "registro.json")] + res.screenshots[-1:])


REGISTRY = {f.__name__: f for f in (screen_task, analyze_audio, analyze_stems, analyze_midi, mix_plan, compose, render_preview,
                                    reharmonize, keep_rhythm_new_notes, change_mode, vary_answer, master,
                                    compare_reference, evaluate_last, feedback, cubase_status, scan_plugins,
                                    cubase_save, list_restore_points)}
