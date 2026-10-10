"""Rutas y configuración persistente de la aplicación."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_home() -> Path:
    """Carpeta de datos de la aplicación (%APPDATA%\\AIEngineer en Windows)."""
    env = os.environ.get("AIENGINEER_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "AIEngineer"
    return Path.home() / ".aiengineer"


@dataclass
class Settings:
    autonomy_mode: str = "assisted"  # assisted | semi | autonomous
    # Permisos por categoría: "allow" | "ask" | "deny"
    permissions: dict = field(default_factory=dict)
    extra_vst_paths: list = field(default_factory=list)
    cubase_appdata: str = ""  # vacío = autodetectar
    exchange_dir: str = ""  # carpeta de intercambio con Cubase (vacío = <home>/exchange)
    keystroke_control_enabled: bool = False  # control por teclado (experimental) desactivado por defecto


class Config:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.home / "settings.json"
        self.settings = self._load()

    @property
    def projects_dir(self) -> Path:
        p = self.home / "projects"
        p.mkdir(exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.home / "logs"
        p.mkdir(exist_ok=True)
        return p

    @property
    def exchange_dir(self) -> Path:
        p = Path(self.settings.exchange_dir) if self.settings.exchange_dir else self.home / "exchange"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _load(self) -> Settings:
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
                return Settings(**known)
            except (json.JSONDecodeError, TypeError):
                # Configuración corrupta: se conserva una copia y se usan valores por defecto.
                self.settings_path.replace(self.settings_path.with_suffix(".corrupt.json"))
        return Settings()

    def save(self) -> None:
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.settings), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.settings_path)
