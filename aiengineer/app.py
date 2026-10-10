"""Motor de la aplicación: une configuración, proyectos, memoria, permisos, plugins, Cubase y planificador.
La GUI y la línea de comandos son capas finas sobre esta clase."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from .compose.styles import load_styles
from .core.config import Config
from .core.logging_setup import setup_logging
from .core.oplog import OperationLog
from .core.permissions import PermissionPolicy
from .cubase.control import CubaseController
from .cubase.detect import appdata_dirs
from .cubase.keycommands import KeyMap
from .planner.executor import execute
from .planner.planner import Plan, build_plan
from .plugins.scanner import load_inventory
from .project.manager import Project, ProjectManager

log = logging.getLogger("aiengineer")


class Engine:
    def __init__(self, home: Path | None = None):
        self.config = Config(home)
        setup_logging(self.config.logs_dir)
        self.oplog = OperationLog(self.config.home / "operations.db")
        self.projects = ProjectManager(self.config.projects_dir)
        self.project: Project | None = None
        self.policy = PermissionPolicy(self.config.settings.autonomy_mode, self.config.settings.permissions)
        self.plugins = load_inventory(self.config.home / "plugins.json")
        self.styles = load_styles(self.config.home / "styles")
        keymap = KeyMap.load([str(p) for p in appdata_dirs()])
        self.cubase = CubaseController(keymap, enabled=self.config.settings.keystroke_control_enabled)
        self.stop_event = threading.Event()
        self.context: dict = {}

    # ------------------------------------------------------------ proyecto y memoria
    @property
    def project_name(self) -> str:
        return self.project.name if self.project else ""

    def open_project(self, name: str, create: bool = False, source_folder: str = "") -> Project:
        if create and name not in self.projects.list():
            self.project = self.projects.create(name, source_folder=source_folder)
        else:
            self.project = self.projects.open(name)
        mem = self.project.memory
        self.context = {k: mem.get(k) for k in ("style", "tempo", "key") if mem.get(k)}
        if mem.get("source_folder"):
            self.set_sources(mem["source_folder"])
        self.context.update(mem.get("context") or {})
        self.oplog.record("abrir_proyecto", "done", project=self.project_name)
        return self.project

    def set_sources(self, folder_or_file: str) -> dict:
        """Registra en el contexto los archivos de trabajo (carpeta de stems, WAV, MIDI)."""
        p = Path(folder_or_file)
        found = {}
        if p.is_dir():
            wavs = sorted(p.glob("*.wav"))
            mids = sorted(list(p.glob("*.mid")) + list(p.glob("*.midi")))
            if len(wavs) > 1:
                found["stems_folder"] = str(p)
            elif wavs:
                found["audio_file"] = str(wavs[0])
            if mids:
                found["midi_file"] = str(mids[0])
        elif p.suffix.lower() == ".wav":
            found["audio_file"] = str(p)
        elif p.suffix.lower() in (".mid", ".midi"):
            found["midi_file"] = str(p)
        self.context.update(found)
        self.save_context()
        return found

    def output_dir(self) -> Path:
        if self.project:
            return self.project.output_dir
        d = self.config.home / "output"
        d.mkdir(exist_ok=True)
        return d

    def remember(self, key: str, value) -> None:
        if self.project:
            self.project.remember(key, value)

    def memory_list(self, key: str) -> list:
        return list(self.project.memory.get(key, [])) if self.project else []

    def record_decision(self, decision: str, approved: bool, effective=None) -> None:
        if self.project:
            self.project.record_decision(decision, approved, effective)

    # ------------------------------------------------------------ autonomía
    def set_mode(self, mode: str) -> None:
        self.policy = PermissionPolicy(mode, self.config.settings.permissions)
        self.config.settings.autonomy_mode = mode
        self.config.save()

    def set_permission(self, category: str, decision: str) -> None:
        self.config.settings.permissions[category] = decision
        self.policy = PermissionPolicy(self.config.settings.autonomy_mode, self.config.settings.permissions)
        self.config.save()

    def set_keystroke_control(self, enabled: bool) -> None:
        self.config.settings.keystroke_control_enabled = enabled
        self.cubase.enabled = enabled
        self.config.save()

    # ------------------------------------------------------------ órdenes
    def interpret(self, text: str) -> Plan:
        # cada composición nueva usa otra semilla (resultado distinto pero reproducible)
        self.context["seed"] = len(self.memory_list("generated_files")) + 1
        return build_plan(text, self.context)

    PERSISTED = ("audio_file", "stems_folder", "midi_file", "reference_file", "reference_bands", "style",
                 "last_analysis", "previous_analysis", "last_master", "seed")

    def run(self, plan: Plan, approve=None, progress=None):
        try:
            return execute(self, plan, approve=approve, progress=progress)
        finally:
            self.save_context()

    def save_context(self) -> None:
        """Guarda archivos de trabajo y últimos resultados en la memoria para retomar el trabajo."""
        if self.project:
            self.project.memory["context"] = {k: self.context[k] for k in self.PERSISTED if k in self.context}
            self.project.save("contexto de trabajo")

    def stop(self) -> None:
        self.stop_event.set()
        self.oplog.record("detener", "done", project=self.project_name)
