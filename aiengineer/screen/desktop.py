"""Acceso al escritorio de Windows: capturas de pantalla, ratón, teclado y ventana activa (ctypes).

Hay dos implementaciones con la misma interfaz:
- WindowsDesktop: la real (solo Windows; funciona también con Python ARM64 controlando Cubase x86).
- FakeDesktop: una pantalla simulada para pruebas automáticas.
"""
from __future__ import annotations

import sys
import time

import numpy as np

SPECIAL_KEYS = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
    "win": 0x5B,
}
for _i in range(1, 13):
    SPECIAL_KEYS[f"f{_i}"] = 0x6F + _i


def key_codes(combo: str) -> list[int]:
    """'ctrl+shift+e' -> códigos de tecla virtual."""
    codes = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part in SPECIAL_KEYS:
            codes.append(SPECIAL_KEYS[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f"tecla no reconocida: {part!r}")
    return codes


class DesktopUnavailable(RuntimeError):
    pass


class WindowsDesktop:
    def __init__(self):
        if sys.platform != "win32":
            raise DesktopUnavailable("El control de pantalla requiere Windows.")
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        try:  # coordenadas en píxeles reales aunque Windows tenga escalado (125%, 150%...)
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            self.user32.SetProcessDPIAware()
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.GetDC.argtypes = [ctypes.c_void_p]
        self.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        self.gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.gdi32.SelectObject.restype = ctypes.c_void_p
        self.gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] + [ctypes.c_int] * 2 + [wintypes.DWORD]
        self.gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        self.gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self.user32.GetForegroundWindow.restype = ctypes.c_void_p
        self._build_types()

    # ------------------------------------------------------------ pantalla
    def screen_rect(self) -> tuple[int, int, int, int]:
        g = self.user32.GetSystemMetrics
        return g(76), g(77), g(78), g(79)  # x, y, ancho, alto del escritorio virtual

    def screenshot(self) -> np.ndarray:
        ct, wt = self.ct, self.wt
        x, y, w, h = self.screen_rect()
        hdc = self.user32.GetDC(None)
        mdc = self.gdi32.CreateCompatibleDC(hdc)
        bmp = self.gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = self.gdi32.SelectObject(mdc, bmp)
        try:
            self.gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT

            class BMIH(ct.Structure):
                _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                            ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                            ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                            ("biClrImportant", wt.DWORD)]
            bi = BMIH(ct.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ct.create_string_buffer(w * h * 4)
            self.gdi32.GetDIBits(mdc, bmp, 0, h, buf, ct.byref(bi), 0)
            arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)
            return arr[:, :, 2::-1].copy()  # BGRA -> RGB
        finally:
            self.gdi32.SelectObject(mdc, old)
            self.gdi32.DeleteObject(bmp)
            self.gdi32.DeleteDC(mdc)
            self.user32.ReleaseDC(None, hdc)

    def cursor(self) -> tuple[int, int]:
        p = self.wt.POINT()
        self.user32.GetCursorPos(self.ct.byref(p))
        x0, y0, _, _ = self.screen_rect()
        return p.x - x0, p.y - y0

    def foreground_title(self) -> str:
        hwnd = self.user32.GetForegroundWindow()
        n = self.user32.GetWindowTextLengthW(self.ct.c_void_p(hwnd))
        buf = self.ct.create_unicode_buffer(n + 1)
        self.user32.GetWindowTextW(self.ct.c_void_p(hwnd), buf, n + 1)
        return buf.value

    def foreground_process(self) -> str:
        """Ruta del ejecutable de la ventana activa (las ventanas de plugins pertenecen a Cubase)."""
        ct, wt = self.ct, self.wt
        hwnd = self.user32.GetForegroundWindow()
        pid = wt.DWORD()
        self.user32.GetWindowThreadProcessId(ct.c_void_p(hwnd), ct.byref(pid))
        k32 = ct.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ""
        try:
            buf = ct.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ct.byref(size)) else ""
        finally:
            k32.CloseHandle(h)

    # ------------------------------------------------------------ entrada
    def _mouse(self, flags: int, data: int = 0):
        MI = self._U._fields_[0][1]
        inp = self._INPUT(0, self._U(mi=MI(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))
        self.user32.SendInput(1, self.ct.byref(inp), self.ct.sizeof(self._INPUT))

    def move(self, x: int, y: int):
        x0, y0, _, _ = self.screen_rect()
        self.user32.SetCursorPos(int(x + x0), int(y + y0))
        time.sleep(0.05)

    def click(self, x: int, y: int, button: str = "left", double: bool = False):
        self.move(x, y)
        down, up = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}[button]
        for _ in range(2 if double else 1):
            self._mouse(down)
            self._mouse(up)
            time.sleep(0.06)

    def drag(self, x1: int, y1: int, x2: int, y2: int):
        self.move(x1, y1)
        self._mouse(0x2)
        for i in range(1, 11):  # movimiento progresivo: los mandos de los plugins necesitan ver el arrastre
            self.move(x1 + (x2 - x1) * i // 10, y1 + (y2 - y1) * i // 10)
        self._mouse(0x4)

    def scroll(self, x: int, y: int, clicks: int):
        self.move(x, y)
        self._mouse(0x0800, 120 * clicks)

    def _key(self, vk: int = 0, scan: int = 0, flags: int = 0):
        ct = self.ct
        inp = self._INPUT(1, self._U(ki=self._KI(vk, scan, flags, 0, 0)))
        self.user32.SendInput(1, ct.byref(inp), ct.sizeof(self._INPUT))

    def _build_types(self):
        ct, wt = self.ct, self.wt

        class MI(ct.Structure):
            _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ct.c_size_t)]

        class KI(ct.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ct.c_size_t)]

        class U(ct.Union):
            _fields_ = [("mi", MI), ("ki", KI)]

        class INPUT(ct.Structure):
            _fields_ = [("type", wt.DWORD), ("u", U)]
        self._INPUT, self._KI, self._U = INPUT, KI, U

    def key(self, combo: str):
        codes = key_codes(combo)
        for c in codes:
            self._key(vk=c)
        for c in reversed(codes):
            self._key(vk=c, flags=0x2)
        time.sleep(0.05)

    def type_text(self, text: str):
        for ch in text:  # KEYEVENTF_UNICODE: escribe cualquier carácter sin depender del teclado
            self._key(scan=ord(ch), flags=0x4)
            self._key(scan=ord(ch), flags=0x4 | 0x2)
        time.sleep(0.05)


class FakeDesktop:
    """Escritorio simulado: guarda las acciones y deja que la prueba cambie la 'pantalla'."""

    def __init__(self, image: np.ndarray, title: str = "Cubase 5 Project - Prueba",
                 process: str = r"C:\Program Files (x86)\Steinberg\Cubase 5\Cubase5.exe"):
        self.image = image
        self.title = title
        self.process = process
        self.actions: list[tuple] = []
        self.on_action = None  # callback(acción, self) para simular la respuesta de la interfaz
        self._cursor = (100, 100)

    def screenshot(self):
        return self.image.copy()

    def cursor(self):
        return self._cursor

    def foreground_title(self):
        return self.title

    def foreground_process(self):
        return self.process

    def _do(self, *a):
        self.actions.append(a)
        if self.on_action:
            self.on_action(a, self)

    def click(self, x, y, button="left", double=False):
        self._cursor = (x, y)
        self._do("click", x, y, button, double)

    def drag(self, x1, y1, x2, y2):
        self._do("drag", x1, y1, x2, y2)

    def scroll(self, x, y, clicks):
        self._do("scroll", x, y, clicks)

    def key(self, combo):
        key_codes(combo)
        self._do("key", combo)

    def type_text(self, text):
        self._do("type", text)

    def move(self, x, y):
        self._cursor = (x, y)
