"""Detección de Cubase 5: instalación, arquitectura, proceso en ejecución y proyecto activo.

Cubase 5 no ofrece una API de control externa. Lo que sí es verificable desde fuera:
- El ejecutable instalado y su arquitectura (cabecera PE).
- Si el proceso está en ejecución y el título de su ventana principal (contiene el nombre del proyecto).
- La carpeta de preferencias (%APPDATA%\\Steinberg\\Cubase 5*) con Key Commands.xml.
Todo lo que dependa del título de ventana es heurístico y se marca como tal.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..plugins.scanner import pe_architecture

EXE_NAMES = ("Cubase5.exe", "Cubase 5.exe", "Cubase.exe")


@dataclass
class CubaseStatus:
    platform_supported: bool
    installed: bool = False
    exe_path: str = ""
    arch: str | None = None
    running: bool = False
    pid: int | None = None
    window_title: str = ""
    hwnd: int | None = None
    project_name: str | None = None
    appdata_dirs: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.platform_supported:
            return "Control de Cubase no disponible en este sistema (requiere Windows)."
        parts = []
        parts.append(f"Cubase instalado: {'sí' if self.installed else 'no encontrado'}"
                     + (f" ({self.arch}, {self.exe_path})" if self.installed else ""))
        parts.append(f"En ejecución: {'sí' if self.running else 'no'}")
        if self.running:
            parts.append(f"Ventana: {self.window_title!r}")
            parts.append(f"Proyecto activo (deducido del título): {self.project_name or 'desconocido'}")
        return " | ".join(parts)


def parse_project_from_title(title: str) -> str | None:
    """Extrae el nombre del proyecto del título de la ventana principal de Cubase.

    Formatos tolerados (varían según versión e idioma): 'Cubase 5 Project - Mi tema',
    'Cubase 5 - [Mi tema.cpr]', 'Cubase 5 Proyecto - Mi tema', 'Mi tema - Cubase 5'.
    """
    if not title or "cubase" not in title.lower():
        return None
    m = re.search(r"\[([^\]]+?)(?:\.cpr)?\]", title, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:project|proyecto|projekt|projet)\s*[-–:]\s*(.+)$", title, re.IGNORECASE)
    if m:
        return re.sub(r"\.cpr$", "", m.group(1).strip(), flags=re.IGNORECASE) or None
    m = re.match(r"^(.+?)\s*[-–]\s*cubase", title, re.IGNORECASE)
    if m:
        return re.sub(r"\.cpr$", "", m.group(1).strip(), flags=re.IGNORECASE) or None
    return None


def find_install() -> tuple[str, str | None]:
    cands = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            for sub in ("Steinberg/Cubase 5", "Steinberg/Cubase5"):
                for exe in EXE_NAMES:
                    cands.append(Path(base) / sub / exe)
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore
            for sub in (r"SOFTWARE\Steinberg\Cubase 5", r"SOFTWARE\WOW6432Node\Steinberg\Cubase 5"):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                        val, _ = winreg.QueryValueEx(k, "Path")
                        for exe in EXE_NAMES:
                            cands.append(Path(val) / exe)
                except OSError:
                    pass
        except ImportError:
            pass
    for c in cands:
        if c.exists():
            return str(c), pe_architecture(c)
    return "", None


def appdata_dirs() -> list[Path]:
    base = os.environ.get("APPDATA")
    if not base:
        return []
    root = Path(base) / "Steinberg"
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower().startswith("cubase 5"))


def _win_windows() -> list[tuple[int, int, str]]:
    """(hwnd, pid, título) de las ventanas visibles de nivel superior (Win32 vía ctypes)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    res = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        res.append((int(hwnd), int(pid.value), buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return res


def _process_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def detect() -> CubaseStatus:
    if sys.platform != "win32":
        st = CubaseStatus(platform_supported=False)
        st.notes.append("Este sistema no es Windows: se pueden usar análisis, composición y masterización, "
                        "pero no la detección ni el control de Cubase.")
        return st
    st = CubaseStatus(platform_supported=True)
    st.exe_path, st.arch = find_install()
    st.installed = bool(st.exe_path)
    st.appdata_dirs = [str(p) for p in appdata_dirs()]
    try:
        for hwnd, pid, title in _win_windows():
            exe = _process_name(pid)
            if Path(exe).name.lower() in {e.lower() for e in EXE_NAMES} or (not exe and "cubase" in title.lower()):
                # la ventana principal es la que lleva el nombre del programa en el título
                if "cubase" in title.lower() and (not st.window_title or len(title) > len(st.window_title)):
                    st.running, st.pid, st.hwnd, st.window_title = True, pid, hwnd, title
                    if not st.installed and exe:
                        st.exe_path, st.arch, st.installed = exe, pe_architecture(Path(exe)), True
    except Exception as e:  # noqa: BLE001 - informar sin romper la aplicación
        st.notes.append(f"No se pudieron enumerar las ventanas: {e}")
    st.project_name = parse_project_from_title(st.window_title) if st.running else None
    if st.running and not st.project_name:
        st.notes.append("Cubase está abierto pero no se pudo deducir el proyecto del título de la ventana.")
    return st
