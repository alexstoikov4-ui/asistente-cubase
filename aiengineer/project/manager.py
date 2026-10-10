"""Proyectos con memoria persistente (JSON atómico) y versiones."""
from __future__ import annotations

import copy
import json
import re
import shutil
import time
from pathlib import Path

from .backup import BackupManager

MEMORY_TEMPLATE = {
    "name": "",
    "created": 0.0,
    "source_folder": "",
    "cubase_project": "",
    "tempo": None,
    "key": None,
    "time_signature": "4/4",
    "style": None,
    "structure": [],
    "artistic_goals": [],
    "references": [],
    "tracks": [],
    "instruments": [],
    "plugins": [],
    "processing_applied": [],
    "problems_detected": [],
    "decisions": [],  # {"ts", "decision", "approved", "effective": True/False/None}
    "user_preferences": [],
    "feedback": [],
    "ineffective_actions": [],
    "generated_files": [],
    "context": {},  # archivos de trabajo y últimos resultados, para retomar la sesión
}


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return s or "proyecto"


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.memory_path = self.root / "memory.json"
        self.versions_dir = self.root / "versions"
        self.output_dir = self.root / "output"
        self.backups = BackupManager(self.root / "backups")
        for d in (self.versions_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.memory = self._load()

    @property
    def name(self) -> str:
        return self.memory["name"]

    def _load(self) -> dict:
        data = json.loads(self.memory_path.read_text(encoding="utf-8"))
        mem = copy.deepcopy(MEMORY_TEMPLATE)
        mem.update(data)
        return mem

    def save(self, note: str = "") -> None:
        """Guarda la memoria de forma atómica y archiva la versión anterior."""
        if self.memory_path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            n = len(list(self.versions_dir.glob("*.json"))) + 1
            shutil.copy2(self.memory_path, self.versions_dir / f"{n:04d}_{stamp}.json")
        tmp = self.memory_path.with_suffix(".tmp")
        self.memory["last_saved"] = time.time()
        self.memory["last_note"] = note
        tmp.write_text(json.dumps(self.memory, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.memory_path)

    def remember(self, key: str, value, note: str = "") -> None:
        if key not in MEMORY_TEMPLATE:
            raise KeyError(f"campo de memoria desconocido: {key}")
        if isinstance(MEMORY_TEMPLATE[key], list) and not isinstance(value, list):
            self.memory[key].append(value)
        else:
            self.memory[key] = value
        self.save(note or f"actualizado {key}")

    def record_decision(self, decision: str, approved: bool, effective=None) -> None:
        self.memory["decisions"].append({"ts": time.time(), "decision": decision, "approved": approved,
                                         "effective": effective})
        if effective is False:
            self.memory["ineffective_actions"].append(decision)
        self.save("decisión registrada")

    def was_ineffective(self, decision: str) -> bool:
        return decision in self.memory["ineffective_actions"]

    def versions(self) -> list[Path]:
        return sorted(self.versions_dir.glob("*.json"))

    def restore_version(self, version: Path) -> None:
        data = json.loads(Path(version).read_text(encoding="utf-8"))
        self.memory = copy.deepcopy(MEMORY_TEMPLATE)
        self.memory.update(data)
        self.save(f"restaurada versión {Path(version).name}")

    @staticmethod
    def diff(a: dict, b: dict) -> dict:
        keys = set(a) | set(b)
        return {k: (a.get(k), b.get(k)) for k in sorted(keys)
                if a.get(k) != b.get(k) and k not in ("last_saved", "last_note")}


class ProjectManager:
    def __init__(self, projects_dir: Path):
        self.dir = Path(projects_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def create(self, name: str, source_folder: str = "", cubase_project: str = "") -> Project:
        root = self.dir / slugify(name)
        if root.exists():
            raise FileExistsError(f"ya existe un proyecto llamado {name!r}")
        root.mkdir(parents=True)
        mem = copy.deepcopy(MEMORY_TEMPLATE)
        mem.update(name=name, created=time.time(), source_folder=source_folder, cubase_project=cubase_project)
        (root / "memory.json").write_text(json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8")
        return Project(root)

    def open(self, name: str) -> Project:
        root = self.dir / slugify(name)
        if not (root / "memory.json").exists():
            raise FileNotFoundError(f"no existe el proyecto {name!r}")
        return Project(root)

    def list(self) -> list[str]:
        out = []
        for d in sorted(self.dir.iterdir()):
            m = d / "memory.json"
            if m.exists():
                try:
                    out.append(json.loads(m.read_text(encoding="utf-8"))["name"])
                except (json.JSONDecodeError, KeyError):
                    continue
        return out
