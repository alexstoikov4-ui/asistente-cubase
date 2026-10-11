"""Interfaz gráfica (tkinter, incluido con Python en Windows). Capa fina sobre aiengineer.app.Engine.

Todo trabajo largo se ejecuta en un hilo; la interfaz se actualiza a través de una cola.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..app import Engine
from ..compose.moods import MOODS
from ..core.permissions import CATEGORIES, MODES
from ..core.status import FEATURES
from ..master.engine import TARGETS
from ..planner.planner import Plan, Task

MODE_LABELS = {"assisted": "Asistido", "semi": "Semiautónomo", "autonomous": "Autónomo"}
DECISION_LABELS = {"allow": "Permitir", "ask": "Preguntar", "deny": "Denegar"}


class App:
    def __init__(self, root: tk.Tk, engine: Engine):
        self.root = root
        self.engine = engine
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.current_plan: Plan | None = None
        root.title("AI Audio Engineer — Cubase 5")
        root.geometry("1180x780")
        root.minsize(900, 600)
        self._build()
        self._refresh_projects()
        self.root.after(100, self._poll_queue)
        self.root.after(500, self._refresh_cubase_status)

    # ================================================================ construcción
    def _build(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Proyecto:").pack(side="left")
        self.project_var = tk.StringVar()
        self.project_box = ttk.Combobox(top, textvariable=self.project_var, width=24, state="readonly")
        self.project_box.pack(side="left", padx=4)
        self.project_box.bind("<<ComboboxSelected>>", lambda e: self._open_project(self.project_var.get()))
        ttk.Button(top, text="Nuevo…", command=self._new_project).pack(side="left")
        ttk.Button(top, text="Fuentes…", command=self._choose_sources).pack(side="left", padx=4)
        ttk.Button(top, text="📁 Carpeta de archivos", command=self._open_files_folder).pack(side="left")
        ttk.Label(top, text="Modo:").pack(side="left", padx=(16, 2))
        self.mode_var = tk.StringVar(value=MODE_LABELS[self.engine.policy.mode])
        mode_box = ttk.Combobox(top, textvariable=self.mode_var, values=list(MODE_LABELS.values()), width=14,
                                state="readonly")
        mode_box.pack(side="left")
        mode_box.bind("<<ComboboxSelected>>", self._mode_changed)
        status = ttk.Frame(self.root, padding=(6, 2))
        status.pack(side="bottom", fill="x")
        self.cubase_lbl = ttk.Label(status, text="Cubase: comprobando…", anchor="w")
        self.cubase_lbl.pack(side="left")
        self.sources_lbl = ttk.Label(status, text="(sin archivos de trabajo)", foreground="#555", anchor="e")
        self.sources_lbl.pack(side="right")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._tab_orders()
        self._tab_tasks()
        self._tab_analysis()
        self._tab_compose()
        self._tab_mix()
        self._tab_master()
        self._tab_screen()
        self._tab_plugins()
        self._tab_history()
        self._tab_errors()
        self._tab_features()

    def _text(self, parent, height=20):
        t = ScrolledText(parent, wrap="word", height=height, font=("Consolas", 10))
        t.configure(state="disabled")
        return t

    def _write(self, widget, text, clear=False):
        widget.configure(state="normal")
        if clear:
            widget.delete("1.0", "end")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _tab_orders(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Órdenes")
        self.chat = self._text(f, 24)
        self.chat.pack(fill="both", expand=True)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        self.order = tk.Text(row, height=3, wrap="word", font=("Segoe UI", 10))
        self.order.pack(side="left", fill="x", expand=True)
        self.order.bind("<Control-Return>", lambda e: (self._execute_order(), "break"))
        btns = ttk.Frame(row)
        btns.pack(side="left", padx=4)
        ttk.Button(btns, text="Planificar", command=self._plan_order).pack(fill="x")
        self.run_btn = ttk.Button(btns, text="Ejecutar ▶", command=self._execute_order)
        self.run_btn.pack(fill="x", pady=2)
        ttk.Button(btns, text="Detener ■", command=self._stop).pack(fill="x")
        self._write(self.chat, "Escribe una orden (Ctrl+Enter para ejecutar). Ejemplos:\n"
                               "  · Analiza este proyecto y dime qué problemas tiene\n"
                               "  · Compón un drop progressive house con una melodía emotiva y memorable\n"
                               "  · Masteriza el tema para un sistema de sonido de festival\n"
                               "  · El bajo ha perdido cuerpo\n")

    def _tab_tasks(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Tareas")
        cols = ("id", "tarea", "categoría", "estado")
        self.tasks_tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        for c, w in zip(cols, (50, 560, 140, 120)):
            self.tasks_tree.heading(c, text=c.capitalize())
            self.tasks_tree.column(c, width=w, anchor="w")
        self.tasks_tree.pack(fill="x")
        self.tasks_tree.bind("<<TreeviewSelect>>", self._show_task_detail)
        self.task_detail = self._text(f, 16)
        self.task_detail.pack(fill="both", expand=True, pady=4)

    def _file_row(self, parent, label, var, kind="file", types=(("WAV", "*.wav"),)):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=18).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

        def pick():
            p = filedialog.askdirectory() if kind == "dir" else filedialog.askopenfilename(filetypes=list(types))
            if p:
                var.set(p)
        ttk.Button(row, text="…", width=3, command=pick).pack(side="left", padx=2)

    def _tab_analysis(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Análisis")
        self.an_file = tk.StringVar()
        self._file_row(f, "Archivo (WAV/MIDI):", self.an_file, types=(("Audio o MIDI", "*.wav *.mid *.midi"),))
        ttk.Button(f, text="Analizar", command=self._analyze).pack(anchor="w", pady=4)
        self.an_out = self._text(f, 26)
        self.an_out.pack(fill="both", expand=True)

    def _tab_compose(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Composición MIDI")
        g = ttk.LabelFrame(f, text="Nueva composición", padding=6)
        g.pack(fill="x")
        self.c_style = tk.StringVar(value="progressive_house")
        self.c_mood = tk.StringVar(value="emotivo")
        self.c_key = tk.StringVar()
        self.c_bpm = tk.StringVar()
        self.c_seed = tk.StringVar(value="1")
        self.c_sections = tk.StringVar()
        fields = [("Estilo", ttk.Combobox(g, textvariable=self.c_style, values=sorted(self.engine.styles), state="readonly")),
                  ("Emoción", ttk.Combobox(g, textvariable=self.c_mood, values=sorted(MOODS), state="readonly")),
                  ("Tonalidad (p.ej. La menor)", ttk.Entry(g, textvariable=self.c_key)),
                  ("BPM (vacío = estilo)", ttk.Entry(g, textvariable=self.c_bpm)),
                  ("Semilla", ttk.Entry(g, textvariable=self.c_seed)),
                  ("Secciones (vacío = todas)", ttk.Entry(g, textvariable=self.c_sections))]
        for i, (lab, w) in enumerate(fields):
            ttk.Label(g, text=lab).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=4, pady=2)
            w.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="ew", padx=4, pady=2)
        g.columnconfigure(1, weight=1)
        g.columnconfigure(3, weight=1)
        ttk.Button(g, text="Componer y exportar MIDI", command=self._compose).grid(row=3, column=0, columnspan=4, pady=4)
        t = ttk.LabelFrame(f, text="Transformar un MIDI existente (el original no se modifica)", padding=6)
        t.pack(fill="x", pady=6)
        self.t_file = tk.StringVar()
        self._file_row(t, "MIDI:", self.t_file, types=(("MIDI", "*.mid *.midi"),))
        row = ttk.Frame(t)
        row.pack(fill="x")
        self.t_op = tk.StringVar(value="Rearmonizar (conservar ritmo)")
        self.t_ops = {"Rearmonizar (conservar ritmo)": "reharmonize", "Conservar ritmo, notas nuevas": "keep_rhythm_new_notes",
                      "Cambiar modo": "change_mode", "Nueva respuesta (conservar motivo)": "vary_answer"}
        ttk.Combobox(row, textvariable=self.t_op, values=list(self.t_ops), state="readonly", width=34).pack(side="left")
        ttk.Label(row, text="Emoción:").pack(side="left", padx=4)
        self.t_mood = tk.StringVar(value="nostalgico")
        ttk.Combobox(row, textvariable=self.t_mood, values=sorted(MOODS), state="readonly", width=12).pack(side="left")
        ttk.Label(row, text="Modo:").pack(side="left", padx=4)
        self.t_mode = tk.StringVar(value="major")
        ttk.Combobox(row, textvariable=self.t_mode, values=["major", "minor", "dorian", "phrygian", "lydian", "mixolydian"],
                     state="readonly", width=11).pack(side="left")
        ttk.Button(row, text="Transformar", command=self._transform).pack(side="left", padx=6)
        self.c_out = self._text(f, 14)
        self.c_out.pack(fill="both", expand=True)

    def _tab_mix(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Mezcla")
        self.m_folder = tk.StringVar()
        self._file_row(f, "Carpeta de stems:", self.m_folder, kind="dir")
        ttk.Button(f, text="Crear plan de mezcla y previsualización", command=self._mix).pack(anchor="w", pady=4)
        self.m_out = self._text(f, 26)
        self.m_out.pack(fill="both", expand=True)

    def _tab_master(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Masterización")
        self.ms_file = tk.StringVar()
        self.ms_ref = tk.StringVar()
        self._file_row(f, "Mezcla (WAV):", self.ms_file)
        self._file_row(f, "Referencia (opcional):", self.ms_ref)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=4)
        self.ms_target = tk.StringVar(value="streaming")
        self.ms_lufs = tk.StringVar()
        self.ms_ceiling = tk.StringVar()
        ttk.Label(row, text="Destino:").pack(side="left")
        ttk.Combobox(row, textvariable=self.ms_target, values=list(TARGETS), state="readonly", width=12).pack(side="left")
        ttk.Label(row, text="LUFS (vacío = destino):").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=self.ms_lufs, width=7).pack(side="left")
        ttk.Label(row, text="Techo dBTP:").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=self.ms_ceiling, width=7).pack(side="left")
        ttk.Button(row, text="Comparar con referencia", command=self._compare).pack(side="left", padx=8)
        ttk.Button(row, text="Masterizar", command=self._master).pack(side="left")
        self.ms_out = self._text(f, 24)
        self.ms_out.pack(fill="both", expand=True)

    def _tab_screen(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Control de pantalla")
        info = ("El agente mira la pantalla y usa ratón y teclado en Cubase, como Claude Desktop, pero con un modelo de "
                "visión LOCAL y gratuito (Ollama). Instala Ollama y un modelo de visión, p.ej.:  ollama pull qwen2.5vl:7b\n"
                "Seguridad: solo actúa sobre ventanas de Cubase; Detener o llevar el ratón a la esquina superior "
                "izquierda lo para al instante; cada paso se guarda como captura.")
        ttk.Label(f, text=info, wraplength=1050, foreground="#444").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Modelo:").pack(side="left")
        self.vm_var = tk.StringVar(value=self.engine.config.settings.vision_model)
        self.vm_box = ttk.Combobox(row, textvariable=self.vm_var, width=30)
        self.vm_box.pack(side="left", padx=4)
        self.vm_box.bind("<<ComboboxSelected>>", lambda e: self._save_vision_model())
        ttk.Button(row, text="Buscar modelos", command=self._refresh_models).pack(side="left")
        ttk.Label(row, text="Pasos máx.:").pack(side="left", padx=(12, 2))
        self.steps_var = tk.StringVar(value=str(self.engine.config.settings.agent_max_steps))
        ttk.Entry(row, textvariable=self.steps_var, width=5).pack(side="left")
        self.ollama_lbl = ttk.Label(row, text="", foreground="#555")
        self.ollama_lbl.pack(side="left", padx=10)
        row2 = ttk.Frame(f)
        row2.pack(fill="x")
        ttk.Label(row2, text="Tarea en Cubase:").pack(side="left")
        self.sc_task = tk.StringVar(value="Abre el mezclador e inserta FabFilter Pro-Q 3 en la primera ranura del canal 1")
        ttk.Entry(row2, textvariable=self.sc_task).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row2, text="Ejecutar ▶", command=self._screen_run).pack(side="left")
        ttk.Button(row2, text="Detener ■", command=self._stop).pack(side="left", padx=4)
        self.sc_out = self._text(f, 22)
        self.sc_out.pack(fill="both", expand=True, pady=6)

    def _refresh_models(self):
        from ..screen.brains import OllamaBrain
        models = OllamaBrain.available_models(self.engine.config.settings.ollama_url)
        self.vm_box["values"] = models
        if models:
            self.ollama_lbl.configure(text=f"Ollama en marcha: {len(models)} modelos")
            if self.vm_var.get() not in models:
                self.vm_var.set(models[0])
            self._save_vision_model()
        else:
            self.ollama_lbl.configure(text="Ollama no responde en " + self.engine.config.settings.ollama_url)

    def _save_vision_model(self):
        self.engine.config.settings.vision_model = self.vm_var.get().strip()
        self.engine.config.save()

    def _screen_run(self):
        self._save_vision_model()
        try:
            self.engine.config.settings.agent_max_steps = max(1, int(self.steps_var.get()))
            self.engine.config.save()
        except ValueError:
            pass
        task = self.sc_task.get().strip()
        if not task:
            return
        self._write(self.sc_out, f"Tarea: {task}", clear=True)
        self._single("screen_task", "cubase_control", {"task": task}, "Agente de pantalla", self.sc_out)

    def _tab_plugins(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Plugins y permisos")
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ttk.Button(bar, text="Escanear plugins", command=self._scan_plugins).pack(side="left")
        ttk.Button(bar, text="Añadir carpeta VST…", command=self._add_vst_dir).pack(side="left", padx=4)
        cols = ("nombre", "formato", "arquitectura", "compatible", "funciones")
        self.pl_tree = ttk.Treeview(left, columns=cols, show="headings")
        for c, w in zip(cols, (220, 90, 90, 90, 260)):
            self.pl_tree.heading(c, text=c.capitalize())
            self.pl_tree.column(c, width=w, anchor="w")
        self.pl_tree.pack(fill="both", expand=True, pady=4)
        self._fill_plugins()
        right = ttk.LabelFrame(f, text="Permisos por categoría", padding=6)
        right.pack(side="left", fill="y", padx=6)
        self.perm_vars = {}
        for i, (cat, info) in enumerate(CATEGORIES.items()):
            ttk.Label(right, text=info["label"]).grid(row=i, column=0, sticky="w")
            v = tk.StringVar(value=DECISION_LABELS[self.engine.policy.decide(cat).value])
            cb = ttk.Combobox(right, textvariable=v, values=list(DECISION_LABELS.values()), width=10, state="readonly")
            cb.grid(row=i, column=1, padx=4, pady=1)
            cb.bind("<<ComboboxSelected>>", lambda e, c=cat, var=v: self._perm_changed(c, var))
            self.perm_vars[cat] = v
        n = len(CATEGORIES)
        self.keys_var = tk.BooleanVar(value=self.engine.config.settings.keystroke_control_enabled)
        ttk.Checkbutton(right, text="Permitir enviar atajos a Cubase (experimental)", variable=self.keys_var,
                        command=lambda: self.engine.set_keystroke_control(self.keys_var.get())).grid(
            row=n, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(right, text=f"Carpeta de intercambio con Cubase:\n{self.engine.config.exchange_dir}",
                  wraplength=280, foreground="#555").grid(row=n + 1, column=0, columnspan=2, sticky="w", pady=6)

    def _tab_history(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Historial")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="Actualizar", command=self._fill_history).pack(side="left")
        ttk.Button(bar, text="Puntos de recuperación…", command=self._restore_dialog).pack(side="left", padx=4)
        cols = ("hora", "proyecto", "categoría", "acción", "estado")
        self.hist_tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, w in zip(cols, (140, 140, 120, 200, 100)):
            self.hist_tree.heading(c, text=c.capitalize())
            self.hist_tree.column(c, width=w, anchor="w")
        self.hist_tree.pack(fill="both", expand=True, pady=4)
        self._fill_history()

    def _tab_errors(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Registro de errores")
        ttk.Button(f, text="Actualizar", command=self._fill_errors).pack(anchor="w")
        self.err_out = self._text(f, 30)
        self.err_out.pack(fill="both", expand=True, pady=4)
        self._fill_errors()

    def _tab_features(self):
        f = ttk.Frame(self.nb, padding=6)
        self.nb.add(f, text="Estado de funciones")
        cols = ("área", "función", "estado")
        tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, w in zip(cols, (140, 640, 200)):
            tree.heading(c, text=c.capitalize())
            tree.column(c, width=w, anchor="w")
        for row in FEATURES:
            tree.insert("", "end", values=row)
        tree.pack(fill="both", expand=True)

    # ================================================================ proyectos
    def _refresh_projects(self):
        names = self.engine.projects.list()
        self.project_box["values"] = names
        if self.engine.project:
            self.project_var.set(self.engine.project_name)

    def _new_project(self):
        win = tk.Toplevel(self.root)
        win.title("Nuevo proyecto")
        name = tk.StringVar()
        ttk.Label(win, text="Nombre:").pack(padx=8, pady=4)
        e = ttk.Entry(win, textvariable=name, width=32)
        e.pack(padx=8)
        e.focus_set()

        def ok():
            n = name.get().strip()
            if not n:
                return
            try:
                self.engine.open_project(n, create=True)
            except (FileExistsError, OSError) as ex:
                messagebox.showerror("Proyecto", str(ex))
                return
            win.destroy()
            self._refresh_projects()
            self._update_sources_label()
            self._write(self.chat, f"Proyecto '{n}' creado.")
        ttk.Button(win, text="Crear", command=ok).pack(pady=6)
        win.bind("<Return>", lambda ev: ok())

    def _open_project(self, name):
        try:
            self.engine.open_project(name)
        except FileNotFoundError as ex:
            messagebox.showerror("Proyecto", str(ex))
            return
        self._update_sources_label()
        mem = self.engine.project.memory
        self._write(self.chat, f"Proyecto '{name}' abierto. Estilo: {mem.get('style') or '-'}, tonalidad: "
                               f"{mem.get('key') or '-'}, tempo: {mem.get('tempo') or '-'}. "
                               f"{len(mem.get('decisions', []))} decisiones y {len(mem.get('feedback', []))} observaciones guardadas.")

    def _choose_sources(self):
        p = filedialog.askdirectory(title="Carpeta con stems / mezcla / MIDI")
        if not p:
            p = filedialog.askopenfilename(filetypes=[("Audio o MIDI", "*.wav *.mid *.midi")])
        if p:
            found = self.engine.set_sources(p)
            self._update_sources_label()
            self._write(self.chat, "Archivos de trabajo: " + (", ".join(f"{k}={v}" for k, v in found.items()) or
                                                               "no se encontraron WAV ni MIDI."))

    def _open_files_folder(self):
        """Abre en el Explorador la carpeta con los MIDI/WAV generados (la del proyecto si hay uno abierto)."""
        folder = self.engine.output_dir() if self.engine.project else self.engine.config.exchange_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(str(folder))  # solo existe en Windows
            self._write(self.chat, f"Abierta la carpeta: {folder}\n(Copia para importar en Cubase: "
                                   f"{self.engine.config.exchange_dir})")
        except (AttributeError, OSError):
            self._write(self.chat, f"Los archivos están en: {folder}\nCopia para Cubase: {self.engine.config.exchange_dir}")

    def _update_sources_label(self):
        c = self.engine.context
        parts = [f"{lab}: {Path(c[k]).name}" for k, lab in (("stems_folder", "stems"), ("audio_file", "mezcla"),
                                                            ("midi_file", "MIDI")) if c.get(k)]
        self.sources_lbl.configure(text=" | ".join(parts) or "(sin archivos de trabajo)")
        if c.get("stems_folder"):
            self.m_folder.set(c["stems_folder"])
        if c.get("audio_file"):
            self.ms_file.set(c["audio_file"])
            self.an_file.set(c["audio_file"])
        if c.get("midi_file"):
            self.t_file.set(c["midi_file"])

    def _mode_changed(self, _e=None):
        mode = {v: k for k, v in MODE_LABELS.items()}[self.mode_var.get()]
        self.engine.set_mode(mode)
        for cat, v in self.perm_vars.items():
            v.set(DECISION_LABELS[self.engine.policy.decide(cat).value])
        self._write(self.chat, f"Modo: {MODES[mode]}")

    def _perm_changed(self, cat, var):
        dec = {v: k for k, v in DECISION_LABELS.items()}[var.get()]
        self.engine.set_permission(cat, dec)

    # ================================================================ ejecución
    def _busy(self) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("En curso", "Ya hay un trabajo en ejecución. Puedes detenerlo con 'Detener'.")
            return True
        return False

    def _approve(self, task: Task) -> bool:
        """Se llama desde el hilo de trabajo: pide confirmación en el hilo de la interfaz y espera."""
        ev = threading.Event()
        box = {}
        self.q.put(("ask", task, ev, box))
        ev.wait()
        return box.get("ok", False)

    def _run_plan(self, plan: Plan, out_widget=None):
        if self._busy():
            return
        self.current_plan = plan
        self._fill_tasks(plan)
        out = out_widget or self.chat

        def work():
            try:
                tasks = self.engine.run(plan, approve=self._approve, progress=lambda s: self.q.put(("say", out, s)))
                for t in tasks:
                    self.q.put(("say", out, f"\n== {t.description} [{t.status}]\n{(t.result or {}).get('summary', '')}"))
                    for a in (t.result or {}).get("artifacts", []):
                        self.q.put(("say", out, f"   archivo: {a}"))
            except Exception as ex:  # noqa: BLE001
                self.q.put(("say", out, f"Error inesperado: {ex}"))
            finally:
                self.q.put(("done", plan))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _plan_order(self):
        text = self.order.get("1.0", "end").strip()
        if not text:
            return None
        plan = self.engine.interpret(text)
        self._write(self.chat, "\n> " + text + "\n" + plan.summary())
        self._fill_tasks(plan)
        return plan

    def _execute_order(self):
        plan = self._plan_order()
        if plan is None:
            return
        if plan.questions and not plan.tasks:
            self._write(self.chat, "Necesito esa información para empezar (usa 'Fuentes…' para indicar archivos).")
            return
        self._run_plan(plan)
        self.order.delete("1.0", "end")

    def _stop(self):
        self.engine.stop()
        self._write(self.chat, "■ Detención solicitada: se parará en el siguiente punto seguro.")

    def _single(self, action, category, params, desc, out):
        self._run_plan(Plan(desc, [], [Task(action, category, params, desc)]), out)

    def _analyze(self):
        p = self.an_file.get().strip()
        if not p:
            return
        action = "analyze_midi" if p.lower().endswith((".mid", ".midi")) else "analyze_audio"
        self._write(self.an_out, f"Analizando {p}…", clear=True)
        self._single(action, "analysis", {"path": p}, f"Analizar {Path(p).name}", self.an_out)

    def _compose(self):
        params = {"style": self.c_style.get(), "mood": self.c_mood.get(), "key": self.c_key.get().strip() or None,
                  "bpm": float(self.c_bpm.get()) if self.c_bpm.get().strip() else None,
                  "seed": int(self.c_seed.get() or 1),
                  "sections": [s.strip() for s in self.c_sections.get().split(",") if s.strip()] or None}
        plan = Plan("componer", [], [Task("compose", "compose", params, "Componer y exportar MIDI", area="musical"),
                                     Task("render_preview", "export_files", {}, "Previsualización WAV", area="musical")])
        self._write(self.c_out, "Componiendo…", clear=True)
        self._run_plan(plan, self.c_out)

    def _transform(self):
        p = self.t_file.get().strip()
        if not p:
            return
        op = self.t_ops[self.t_op.get()]
        self._write(self.c_out, f"Transformando {p}…", clear=True)
        self._single(op, "compose", {"path": p, "mood": self.t_mood.get(), "new_mode": self.t_mode.get()},
                     self.t_op.get(), self.c_out)

    def _mix(self):
        p = self.m_folder.get().strip()
        if not p:
            return
        self._write(self.m_out, f"Analizando stems de {p}…", clear=True)
        self._single("mix_plan", "export_files", {"folder": p, "focus": "general"}, "Plan de mezcla", self.m_out)

    def _master(self):
        p = self.ms_file.get().strip()
        if not p:
            return
        try:
            lufs = float(self.ms_lufs.get()) if self.ms_lufs.get().strip() else None
            ceil = float(self.ms_ceiling.get()) if self.ms_ceiling.get().strip() else None
        except ValueError:
            messagebox.showerror("Masterización", "LUFS y techo deben ser números (p.ej. -9 y -1).")
            return
        self._write(self.ms_out, "Masterizando (análisis, cadena, verificación)…", clear=True)
        self._single("master", "master", {"path": p, "target": self.ms_target.get(), "lufs": lufs, "ceiling": ceil},
                     f"Masterizar para {self.ms_target.get()}", self.ms_out)

    def _compare(self):
        a, r = self.ms_file.get().strip(), self.ms_ref.get().strip()
        if not (a and r):
            messagebox.showinfo("Referencia", "Indica la mezcla y la referencia.")
            return
        self._write(self.ms_out, "Comparando a sonoridad igualada…", clear=True)
        self._single("compare_reference", "analysis", {"mix": a, "reference": r}, "Comparar con referencia", self.ms_out)

    def _scan_plugins(self):
        plan = Plan("escanear", [], [Task("scan_plugins", "analysis", {}, "Escanear plugins", area="sistema")])
        self._run_plan(plan, self.chat)

    def _add_vst_dir(self):
        p = filedialog.askdirectory(title="Carpeta de plugins VST")
        if p and p not in self.engine.config.settings.extra_vst_paths:
            self.engine.config.settings.extra_vst_paths.append(p)
            self.engine.config.save()
            self._write(self.chat, f"Carpeta añadida: {p}. Pulsa 'Escanear plugins'.")

    # ================================================================ refrescos
    def _fill_tasks(self, plan: Plan):
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        for t in plan.tasks:
            self.tasks_tree.insert("", "end", iid=str(t.id), values=(t.id, t.description, t.category, t.status))

    def _show_task_detail(self, _e=None):
        if not self.current_plan:
            return
        sel = self.tasks_tree.selection()
        for t in self.current_plan.tasks:
            if sel and str(t.id) == sel[0]:
                self._write(self.task_detail, json.dumps({"params": t.params, "status": t.status,
                                                          "summary": (t.result or {}).get("summary"),
                                                          "artifacts": (t.result or {}).get("artifacts")},
                                                         indent=2, ensure_ascii=False, default=str), clear=True)

    def _fill_plugins(self):
        self.pl_tree.delete(*self.pl_tree.get_children())
        for p in self.engine.plugins:
            comp = {True: "sí", False: "no", None: "?"}[p.compatible]
            self.pl_tree.insert("", "end", values=(p.name, p.format, p.arch or "?", comp, ", ".join(p.roles)))

    def _fill_history(self):
        self.hist_tree.delete(*self.hist_tree.get_children())
        for op in self.engine.oplog.recent(300):
            self.hist_tree.insert("", "end", values=(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(op["ts"])),
                                                     op["project"], op["category"], op["action"], op["status"]))

    def _fill_errors(self):
        p = self.engine.config.logs_dir / "errors.log"
        text = p.read_text(encoding="utf-8", errors="replace")[-20000:] if p.exists() else ""
        self._write(self.err_out, text or "Sin errores registrados.", clear=True)

    def _restore_dialog(self):
        if not self.engine.project:
            messagebox.showinfo("Recuperación", "Abre un proyecto primero.")
            return
        bks = self.engine.project.backups.list()
        vers = self.engine.project.versions()
        win = tk.Toplevel(self.root)
        win.title("Puntos de recuperación")
        lb = tk.Listbox(win, width=90, height=16)
        lb.pack(padx=6, pady=6)
        items = [("backup", b["id"], f"[archivos] {b['id']} — {b['reason']}") for b in bks] + \
                [("version", str(v), f"[memoria] {v.name}") for v in vers]
        for _, _, label in items:
            lb.insert("end", label)

        def restore():
            sel = lb.curselection()
            if not sel:
                return
            kind, ident, label = items[sel[0]]
            if not messagebox.askyesno("Restaurar", f"¿Restaurar {label}? El estado actual se guarda antes."):
                return
            if kind == "backup":
                files = self.engine.project.backups.restore(ident)
                self._write(self.chat, "Restaurados: " + ", ".join(files))
            else:
                self.engine.project.restore_version(Path(ident))
                self._write(self.chat, f"Memoria restaurada a {Path(ident).name}.")
            self.engine.oplog.record("restaurar", "verified", project=self.engine.project_name, detail=label)
            win.destroy()
        ttk.Button(win, text="Restaurar seleccionado", command=restore).pack(pady=4)

    def _refresh_cubase_status(self):
        def work():
            try:
                st = self.engine.cubase.status()
                if not st.platform_supported:
                    txt = "Cubase: no disponible (no es Windows)"
                elif st.running:
                    txt = f"Cubase: ABIERTO — {st.project_name or 'proyecto desconocido'}"
                else:
                    txt = "Cubase: cerrado" + ("" if st.installed else " (no encontrado)")
            except Exception as ex:  # noqa: BLE001
                txt = f"Cubase: error de detección ({ex})"
            self.q.put(("cubase", txt))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(5000, self._refresh_cubase_status)

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "say":
                    self._write(msg[1], msg[2])
                elif kind == "ask":
                    _, task, ev, box = msg
                    box["ok"] = messagebox.askyesno("Confirmación", f"¿Aprobar esta tarea?\n\n{task.description}\n"
                                                                    f"Categoría: {CATEGORIES[task.category]['label']}")
                    ev.set()
                elif kind == "done":
                    self._fill_tasks(msg[1])
                    self._fill_history()
                    self._fill_plugins()
                    self._update_sources_label()
                elif kind == "cubase":
                    self.cubase_lbl.configure(text=msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)


def run_gui(home: str | None = None) -> int:
    engine = Engine(Path(home) if home else None)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, engine)
    root.mainloop()
    return 0
