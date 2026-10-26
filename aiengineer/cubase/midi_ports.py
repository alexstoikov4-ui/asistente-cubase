"""Puertos MIDI: implementación Windows (winmm vía ctypes, sin dependencias) y puertos simulados.

Para hablar con Cubase hace falta un cable MIDI virtual. En Windows se usa loopMIDI (gratis) con dos
puertos: uno de la aplicación hacia Cubase y otro de Cubase hacia la aplicación.
"""
from __future__ import annotations

import queue
import sys
import threading
import time


class MidiUnavailable(RuntimeError):
    pass


def list_ports() -> dict:
    """{'out': [nombres], 'in': [nombres]} de los puertos MIDI del sistema (vacío fuera de Windows)."""
    if sys.platform != "win32":
        return {"out": [], "in": []}
    import ctypes
    from ctypes import wintypes
    winmm = ctypes.windll.winmm

    class MIDIOUTCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                    ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]

    class MIDIINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwSupport", wintypes.DWORD)]
    outs, ins = [], []
    for i in range(winmm.midiOutGetNumDevs()):
        c = MIDIOUTCAPSW()
        if winmm.midiOutGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
            outs.append(c.szPname)
    for i in range(winmm.midiInGetNumDevs()):
        c = MIDIINCAPSW()
        if winmm.midiInGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
            ins.append(c.szPname)
    return {"out": outs, "in": ins}


class WinMidiOut:
    def __init__(self, name_part: str):
        ports = list_ports()["out"]
        idx = next((i for i, n in enumerate(ports) if name_part.lower() in n.lower()), None)
        if idx is None:
            raise MidiUnavailable(f"No hay ningún puerto MIDI de salida que contenga {name_part!r}. Puertos: {ports}")
        import ctypes
        self.ct = ctypes
        self.winmm = ctypes.windll.winmm
        self.handle = ctypes.c_void_p()
        if self.winmm.midiOutOpen(ctypes.byref(self.handle), idx, None, None, 0) != 0:
            raise MidiUnavailable(f"No se pudo abrir el puerto {ports[idx]!r} (¿lo está usando otro programa?)")
        self.name = ports[idx]

    def send(self, data: bytes) -> None:
        if not data:
            return
        if data[0] == 0xF0:
            self._sysex(data)
            return
        msg = data[0] | ((data[1] if len(data) > 1 else 0) << 8) | ((data[2] if len(data) > 2 else 0) << 16)
        self.winmm.midiOutShortMsg(self.handle, msg)

    def _sysex(self, data: bytes) -> None:
        ct = self.ct

        class MIDIHDR(ct.Structure):
            _fields_ = [("lpData", ct.c_char_p), ("dwBufferLength", ct.c_uint32), ("dwBytesRecorded", ct.c_uint32),
                        ("dwUser", ct.c_size_t), ("dwFlags", ct.c_uint32), ("lpNext", ct.c_void_p),
                        ("reserved", ct.c_size_t), ("dwOffset", ct.c_uint32), ("dwReserved", ct.c_size_t * 8)]
        buf = ct.create_string_buffer(bytes(data), len(data))
        hdr = MIDIHDR(ct.cast(buf, ct.c_char_p), len(data), len(data), 0, 0, None, 0, 0)
        self.winmm.midiOutPrepareHeader(self.handle, ct.byref(hdr), ct.sizeof(hdr))
        self.winmm.midiOutLongMsg(self.handle, ct.byref(hdr), ct.sizeof(hdr))
        for _ in range(200):  # esperar a que el controlador termine (MHDR_DONE)
            if hdr.dwFlags & 1:
                break
            time.sleep(0.002)
        self.winmm.midiOutUnprepareHeader(self.handle, ct.byref(hdr), ct.sizeof(hdr))

    def close(self) -> None:
        self.winmm.midiOutClose(self.handle)


class WinMidiIn:
    """Entrada MIDI con cola de mensajes (incluye SysEx, necesarios para leer la pantalla Mackie)."""

    def __init__(self, name_part: str, nbuf: int = 8, bufsize: int = 1024):
        ports = list_ports()["in"]
        idx = next((i for i, n in enumerate(ports) if name_part.lower() in n.lower()), None)
        if idx is None:
            raise MidiUnavailable(f"No hay ningún puerto MIDI de entrada que contenga {name_part!r}. Puertos: {ports}")
        import ctypes
        self.ct = ctypes
        self.winmm = ctypes.windll.winmm
        self.q: queue.Queue = queue.Queue()
        self._done: queue.Queue = queue.Queue()
        ct = ctypes

        class MIDIHDR(ct.Structure):
            _fields_ = [("lpData", ct.c_void_p), ("dwBufferLength", ct.c_uint32), ("dwBytesRecorded", ct.c_uint32),
                        ("dwUser", ct.c_size_t), ("dwFlags", ct.c_uint32), ("lpNext", ct.c_void_p),
                        ("reserved", ct.c_size_t), ("dwOffset", ct.c_uint32), ("dwReserved", ct.c_size_t * 8)]
        self.MIDIHDR = MIDIHDR
        CB = ct.WINFUNCTYPE(None, ct.c_void_p, ct.c_uint, ct.c_size_t, ct.c_size_t, ct.c_size_t)

        def callback(h, msg, inst, p1, p2):
            if msg == 0x3C3:  # MIM_DATA
                st = p1 & 0xFF
                n = 1 if st in (0xC0, 0xD0) or (st & 0xF0) in (0xC0, 0xD0) else 2
                self.q.put(bytes([st, (p1 >> 8) & 0x7F, (p1 >> 16) & 0x7F][:n + 1]))
            elif msg == 0x3C4:  # MIM_LONGDATA: SysEx completo
                self._done.put(p1)
        self._cb = CB(callback)
        self.handle = ct.c_void_p()
        if self.winmm.midiInOpen(ct.byref(self.handle), idx, self._cb, None, 0x30000) != 0:
            raise MidiUnavailable(f"No se pudo abrir el puerto de entrada {ports[idx]!r}")
        self.name = ports[idx]
        self.bufs, self.hdrs = [], {}
        for _ in range(nbuf):
            b = ct.create_string_buffer(bufsize)
            h = MIDIHDR(ct.cast(b, ct.c_void_p), bufsize, 0, 0, 0, None, 0, 0)
            self.winmm.midiInPrepareHeader(self.handle, ct.byref(h), ct.sizeof(h))
            self.winmm.midiInAddBuffer(self.handle, ct.byref(h), ct.sizeof(h))
            self.bufs.append(b)
            self.hdrs[ct.addressof(h)] = (h, b)
        self._running = True
        threading.Thread(target=self._sysex_worker, daemon=True).start()
        self.winmm.midiInStart(self.handle)

    def _sysex_worker(self):
        ct = self.ct
        while self._running:
            try:
                addr = self._done.get(timeout=0.2)
            except queue.Empty:
                continue
            entry = self.hdrs.get(addr)
            if not entry:
                continue
            h, b = entry
            if h.dwBytesRecorded:
                self.q.put(b.raw[:h.dwBytesRecorded])
            if self._running:  # devolver el búfer al controlador (fuera del callback, como exige winmm)
                h.dwBytesRecorded = 0
                self.winmm.midiInAddBuffer(self.handle, ct.byref(h), ct.sizeof(h))

    def receive(self, timeout: float = 0.0) -> bytes | None:
        try:
            return self.q.get(timeout=timeout) if timeout else self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._running = False
        self.winmm.midiInStop(self.handle)
        self.winmm.midiInReset(self.handle)
        for h, _ in self.hdrs.values():
            self.winmm.midiInUnprepareHeader(self.handle, self.ct.byref(h), self.ct.sizeof(h))
        self.winmm.midiInClose(self.handle)


class LoopbackPair:
    """Dos puertos simulados conectados (para pruebas): lo que envía uno lo recibe el otro."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        self.q.put(bytes(data))

    def receive(self, timeout: float = 0.0) -> bytes | None:
        try:
            return self.q.get(timeout=timeout) if timeout else self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        pass


def diagnose(out_name: str, in_name: str, listen_seconds: float = 6.0, say=None) -> list[str]:
    """Separa los problemas: (1) ¿la aplicación recibe MIDI por el puerto de entrada? (prueba en bucle
    sobre el propio puerto) y (2) ¿Cubase envía algo por ese puerto mientras el usuario toca Cubase?"""
    say = say or (lambda s: None)
    lines = []
    ports = list_ports()
    lines.append("Salidas: " + ", ".join(ports["out"]))
    lines.append("Entradas: " + ", ".join(ports["in"]))
    try:
        inp = WinMidiIn(in_name)
    except MidiUnavailable as e:
        return lines + [f"✖ No se puede abrir la entrada: {e}"]
    try:
        # 1) bucle: escribir en la salida del MISMO puerto de entrada y comprobar que llega
        try:
            loop_out = WinMidiOut(in_name)
            # solo SysEx de uso no comercial (0x7D): no suena aunque Cubase escuche ese puerto
            probe = bytes([0xF0, 0x7D, 0x41, 0x49, 0x45, 0xF7])
            loop_out.send(probe)
            got, sysex = 0, False
            end = time.time() + 1.5
            while time.time() < end:
                m = inp.receive(timeout=0.05)
                if m:
                    got += 1  # aquí también cuentan los medidores que envíe Cubase a la vez
                    sysex = sysex or m == probe
            loop_out.close()
            if sysex:
                lines.append(f"✔ Prueba de bucle: la aplicación recibe SysEx por '{inp.name}'.")
            elif got:
                lines.append(f"✖ Prueba de bucle: llegan mensajes cortos por '{inp.name}' pero el SysEx de prueba no "
                             "vuelve. El controlador MIDI de Windows no deja pasar SysEx, y Cubase envía por SysEx "
                             "los nombres de pista y los valores en dB.")
            else:
                lines.append(f"✖ Prueba de bucle: lo enviado a '{in_name}' no vuelve por su entrada. El puerto "
                             "virtual no funciona como bucle para esta aplicación.")
        except MidiUnavailable as e:
            lines.append(f"• No se pudo hacer la prueba de bucle: {e}")
        # 2) escuchar a Cubase
        say(f"Escuchando a Cubase durante {listen_seconds:.0f} s: dale al PLAY o mueve un fader en Cubase…")
        count, kinds = 0, {}
        end = time.time() + listen_seconds
        while time.time() < end:
            m = inp.receive(timeout=0.05)
            if m:
                count += 1
                k = "SysEx (pantalla)" if m[0] == 0xF0 else {0x90: "LED", 0xE0: "fader", 0xB0: "CC (V-pot/reloj)",
                                                          0xD0: "medidores"}.get(m[0] & 0xF0, hex(m[0]))
                kinds[k] = kinds.get(k, 0) + 1
        if count:
            lines.append(f"✔ Cubase envía datos: {count} mensajes (" +
                         ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items())) + ").")
            if "fader" not in kinds:
                lines.append("  • No llegó ninguna posición de fader: si moviste un fader en Cubase durante la escucha, "
                             "Cubase no lo está enviando a la superficie.")
            if "SysEx (pantalla)" not in kinds:
                lines.append("  • No llegó texto de pantalla (SysEx). Prueba en Cubase: Dispositivos > Configuración "
                             "de dispositivos > Mackie Control > Reinicializar, y repite el diagnóstico.")
        else:
            lines.append("✖ Cubase no ha enviado nada por ese puerto. Revisa en Cubase que Mackie Control tenga "
                         f"Salida MIDI = '{in_name}', reinicia Cubase y abre un proyecto.")
        # 3) qué responde Cubase a cada estímulo de la superficie
        try:
            out = WinMidiOut(out_name)
        except MidiUnavailable as e:
            lines.append(f"✖ Salida: {e}")
        else:
            lines.append(f"✔ La salida '{out_name}' se puede abrir. Respuesta de Cubase a cada botón:")
            try:
                lines += stimulus_test(inp, out)
            finally:
                out.close()
    finally:
        inp.close()
    return lines


def _listen(inp, seconds: float) -> tuple[dict, list[str]]:
    kinds: dict = {}
    sysex: list[str] = []
    end = time.time() + seconds
    while time.time() < end:
        m = inp.receive(timeout=0.02)
        if not m:
            continue
        if m[0] == 0xF0:
            k = "SysEx"
            if len(sysex) < 2:
                sysex.append(m[:20].hex(" "))
        else:
            k = {0x90: "LED", 0xE0: "fader", 0xB0: "CC", 0xD0: "medidores"}.get(m[0] & 0xF0, hex(m[0] & 0xF0))
        kinds[k] = kinds.get(k, 0) + 1
    return kinds, sysex


def stimulus_test(inp, out) -> list[str]:
    """Pulsa botones de la superficie (los que vuelven al estado inicial) y anota qué devuelve Cubase a cada uno:
    sirve para saber si Cubase envía alguna vez texto de pantalla (SysEx) y en qué momento."""
    from .mackie import ASSIGN, HEADER, NAV, SERIAL

    def press(n):
        out.send(bytes([0x90, n, 0x7F]))
        time.sleep(0.03)
        out.send(bytes([0x90, n, 0x00]))

    steps = [
        ("saludo de superficie", lambda: (out.send(HEADER + bytes([0x01]) + SERIAL + bytes([0, 0, 0, 0, 0xF7])),
                                          out.send(HEADER + bytes([0x03]) + SERIAL + bytes([0xF7])))),
        ("petición de versión", lambda: out.send(HEADER + bytes([0x13, 0x00, 0xF7]))),
        ("botón PAN", lambda: press(ASSIGN["pan"])),
        ("NAME/VALUE x2", lambda: (press(NAV["name_value"]), time.sleep(0.3), press(NAV["name_value"]))),
        ("FLIP x2", lambda: (press(NAV["flip"]), time.sleep(0.3), press(NAV["flip"]))),
        ("BANCO > y <", lambda: (press(NAV["bank_right"]), time.sleep(0.3), press(NAV["bank_left"]))),
        ("CANAL > y <", lambda: (press(NAV["channel_right"]), time.sleep(0.3), press(NAV["channel_left"]))),
        ("tocar fader 1", lambda: (out.send(bytes([0x90, 0x68, 0x7F])), time.sleep(0.3), out.send(bytes([0x90, 0x68, 0x00])))),
    ]
    lines = []
    _listen(inp, 0.3)  # descarta lo pendiente
    for name, act in steps:
        act()
        kinds, sysex = _listen(inp, 0.9)
        kinds.pop("medidores", None)  # llegan siempre; no dicen nada del estímulo
        desc = ", ".join(f"{k}: {v}" for k, v in sorted(kinds.items())) or "nada"
        lines.append(f"   {name}: {desc}" + (f"  [{sysex[0]}]" if sysex else ""))
    return lines
