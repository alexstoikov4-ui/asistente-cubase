"""Control de Cubase 5 por teclado (EXPERIMENTAL) con verificación de resultados.

Mecanismo: se trae la ventana de Cubase al frente y se envían pulsaciones con SendInput.
Solo se consideran 'verificadas' las acciones cuyo efecto se puede comprobar desde fuera
(p.ej. Guardar: cambia la fecha de modificación del .cpr). Las demás se informan como
'no verificadas', nunca como hechas.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .detect import CubaseStatus, detect
from .keycommands import KeyMap

VK = {
    "CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "ENTER": 0x0D, "RETURN": 0x0D, "SPACE": 0x20, "ESC": 0x1B,
    "ESCAPE": 0x1B, "TAB": 0x09, "BACKSPACE": 0x08, "DELETE": 0x2E, "DEL": 0x2E, "HOME": 0x24, "END": 0x23,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "INSERT": 0x2D,
    "NUM 0": 0x60, "NUM 1": 0x61, "NUM 2": 0x62, "NUM 3": 0x63, "NUM 4": 0x64, "NUM 5": 0x65, "NUM 6": 0x66,
    "NUM 7": 0x67, "NUM 8": 0x68, "NUM 9": 0x69, "NUM *": 0x6A, "NUM +": 0x6B, "NUM -": 0x6D, "NUM /": 0x6F,
    "NUM ENTER": 0x0D, "PAD 0": 0x60,
}
for i in range(1, 13):
    VK[f"F{i}"] = 0x6F + i


def parse_shortcut(text: str) -> list[int]:
    """'Ctrl+Shift+E' -> [VK_CONTROL, VK_SHIFT, ord('E')]. Lanza ValueError si no se reconoce."""
    if not text:
        raise ValueError("atajo vacío")
    parts = [p.strip() for p in text.replace("Control", "Ctrl").split("+")]
    if text.endswith("++"):  # 'Num ++' o 'Ctrl++'
        parts = parts[:-2] + ["+"] if parts[-2:] == ["", ""] else parts
    codes = []
    for p in parts:
        u = p.upper()
        if u in VK:
            codes.append(VK[u])
        elif len(p) == 1 and (p.isalnum()):
            codes.append(ord(p.upper()))
        elif u in ("NUM+", "NUM +"):
            codes.append(0x6B)
        else:
            raise ValueError(f"tecla no reconocida: {p!r} en {text!r}")
    return codes


@dataclass
class ActionResult:
    action: str
    status: str  # verified | unverified | failed | skipped
    detail: str = ""


class CubaseController:
    def __init__(self, keymap: KeyMap | None = None, enabled: bool = False, dry_run: bool = False):
        self.enabled = enabled
        self.dry_run = dry_run
        self.keymap = keymap or KeyMap({})
        self.sent: list[list[int]] = []  # registro (útil en pruebas y en modo simulación)

    def status(self) -> CubaseStatus:
        return detect()

    # ---------------------------------------------------------------- bajo nivel
    def _focus(self, hwnd: int) -> bool:
        import ctypes
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return user32.GetForegroundWindow() == hwnd

    def _send(self, codes: list[int]) -> None:
        self.sent.append(codes)
        if self.dry_run:
            return
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        seq = []
        for c in codes:
            seq.append(INPUT(type=1, ki=KEYBDINPUT(wVk=c)))
        for c in reversed(codes):
            seq.append(INPUT(type=1, ki=KEYBDINPUT(wVk=c, dwFlags=0x0002)))
        arr = (INPUT * len(seq))(*seq)
        ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))

    # ---------------------------------------------------------------- acciones
    def run_command(self, category: str, command: str, verify=None, timeout: float = 5.0) -> ActionResult:
        """Ejecuta un comando de Cubase por su atajo. verify: función sin argumentos -> (bool, detalle)."""
        name = f"{category} > {command}"
        if not self.enabled:
            return ActionResult(name, "skipped", "El control por teclado está desactivado en la configuración.")
        if sys.platform != "win32" and not self.dry_run:
            return ActionResult(name, "skipped", "Requiere Windows.")
        key, origin = self.keymap.shortcut(category, command)
        if not key:
            return ActionResult(name, "failed", "No hay atajo asignado a este comando en Cubase. Asígnalo en "
                                                "Archivo > Comandos de teclado y vuelve a intentarlo.")
        try:
            codes = parse_shortcut(key)
        except ValueError as e:
            return ActionResult(name, "failed", str(e))
        if not self.dry_run:
            st = self.status()
            if not st.running or st.hwnd is None:
                return ActionResult(name, "failed", "Cubase no está abierto.")
            if not self._focus(st.hwnd):
                return ActionResult(name, "failed", "No se pudo traer la ventana de Cubase al frente.")
        self._send(codes)
        if verify is None:
            return ActionResult(name, "unverified", f"Atajo {key} ({origin}) enviado; el efecto no se puede comprobar "
                                                    "desde fuera de Cubase.")
        deadline = time.time() + timeout
        while True:
            ok, detail = verify()
            if ok:
                return ActionResult(name, "verified", detail)
            if time.time() >= deadline:
                return ActionResult(name, "failed", f"Atajo {key} enviado pero no se observó el efecto: {detail}")
            time.sleep(0.25)

    def save_project(self, cpr_path: str | None) -> ActionResult:
        """Guardar es verificable si conocemos la ruta del .cpr: su fecha de modificación debe cambiar."""
        if not cpr_path or not Path(cpr_path).exists():
            return self.run_command("File", "Save")
        before = Path(cpr_path).stat().st_mtime

        def verify():
            now = Path(cpr_path).stat().st_mtime
            return now > before, f"fecha de modificación {'actualizada' if now > before else 'sin cambios'}"

        return self.run_command("File", "Save", verify=verify)
