"""Lectura y escritura de Standard MIDI Files (formato 0 y 1) sin dependencias externas.

Cubase 5 importa SMF tipo 0 y 1; se escribe tipo 1 con una pista de tempo/compás/marcadores
y una pista por instrumento, que es lo que Cubase convierte en pistas MIDI independientes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


class MidiError(ValueError):
    pass


@dataclass
class Note:
    start: int  # ticks
    duration: int  # ticks
    pitch: int
    velocity: int = 100

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class Track:
    name: str = ""
    channel: int = 0
    program: int | None = None
    notes: list[Note] = field(default_factory=list)
    controllers: list[tuple[int, int, int]] = field(default_factory=list)  # (tick, cc, valor)
    role: str = ""  # papel musical (melodía, bajo...), no se escribe en el archivo

    def sorted_notes(self) -> list[Note]:
        return sorted(self.notes, key=lambda n: (n.start, n.pitch))


@dataclass
class Song:
    ppq: int = 480
    tempos: list[tuple[int, float]] = field(default_factory=lambda: [(0, 120.0)])  # (tick, bpm)
    time_signatures: list[tuple[int, int, int]] = field(default_factory=lambda: [(0, 4, 4)])
    markers: list[tuple[int, str]] = field(default_factory=list)
    key_signature: tuple[int, int] | None = None  # (sostenidos(+)/bemoles(-), 0 mayor / 1 menor)
    tracks: list[Track] = field(default_factory=list)

    @property
    def bpm(self) -> float:
        return self.tempos[0][1] if self.tempos else 120.0

    def ticks_per_bar(self) -> int:
        _, num, den = self.time_signatures[0] if self.time_signatures else (0, 4, 4)
        return int(self.ppq * num * 4 / den)

    def length_ticks(self) -> int:
        ends = [n.end for t in self.tracks for n in t.notes]
        return max(ends) if ends else 0

    def track(self, name: str) -> Track | None:
        for t in self.tracks:
            if t.name == name:
                return t
        return None


def _vlq(n: int) -> bytes:
    if n < 0:
        raise MidiError("delta negativo")
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _chunk(cid: bytes, body: bytes) -> bytes:
    return cid + struct.pack(">I", len(body)) + body


def _encode_events(events: list[tuple[int, int, bytes]]) -> bytes:
    # events: (tick, orden, bytes). En el mismo tick, note-off (orden 0) antes que note-on (orden 2).
    events.sort(key=lambda e: (e[0], e[1]))
    out = bytearray()
    last = 0
    for tick, _, data in events:
        out += _vlq(tick - last) + data
        last = tick
    out += _vlq(0) + b"\xff\x2f\x00"
    return bytes(out)


def _text_meta(kind: int, text: str) -> bytes:
    raw = text.encode("latin-1", errors="replace")
    return bytes([0xFF, kind]) + _vlq(len(raw)) + raw


def write_midi(song: Song, path) -> Path:
    if not 0 < song.ppq < 0x8000:
        raise MidiError("PPQ fuera de rango")
    conductor: list[tuple[int, int, bytes]] = [(0, 1, _text_meta(0x03, "Tempo"))]
    for tick, bpm in song.tempos:
        mpq = int(round(60_000_000 / bpm))
        conductor.append((tick, 1, b"\xff\x51\x03" + mpq.to_bytes(3, "big")))
    for tick, num, den in song.time_signatures:
        dd = den.bit_length() - 1
        if 1 << dd != den:
            raise MidiError(f"denominador de compás no válido: {den}")
        conductor.append((tick, 1, bytes([0xFF, 0x58, 4, num, dd, 24, 8])))
    if song.key_signature is not None:
        sf, mi = song.key_signature
        conductor.append((0, 1, bytes([0xFF, 0x59, 2, sf & 0xFF, mi])))
    for tick, text in song.markers:
        conductor.append((tick, 1, _text_meta(0x06, text)))
    chunks = [_chunk(b"MThd", struct.pack(">HHH", 1, len(song.tracks) + 1, song.ppq)),
              _chunk(b"MTrk", _encode_events(conductor))]
    for tr in song.tracks:
        ch = tr.channel & 0x0F
        ev: list[tuple[int, int, bytes]] = [(0, 1, _text_meta(0x03, tr.name or "Track"))]
        if tr.program is not None:
            ev.append((0, 1, bytes([0xC0 | ch, tr.program & 0x7F])))
        for tick, cc, val in tr.controllers:
            ev.append((tick, 1, bytes([0xB0 | ch, cc & 0x7F, val & 0x7F])))
        for n in tr.notes:
            if not (0 <= n.pitch <= 127):
                raise MidiError(f"nota fuera de rango: {n.pitch}")
            if n.duration <= 0:
                raise MidiError("duración de nota no positiva")
            vel = max(1, min(127, int(n.velocity)))
            ev.append((n.start, 2, bytes([0x90 | ch, n.pitch, vel])))
            ev.append((n.end, 0, bytes([0x80 | ch, n.pitch, 0])))
        chunks.append(_chunk(b"MTrk", _encode_events(ev)))
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(b"".join(chunks))
    tmp.replace(path)
    return path


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    n = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("VLQ truncado")
        b = data[pos]
        pos += 1
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, pos
    raise MidiError("VLQ demasiado largo")


def read_midi(path) -> Song:
    data = Path(path).read_bytes()
    if data[:4] != b"MThd":
        raise MidiError("no es un archivo MIDI estándar")
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise MidiError("división SMPTE no soportada")
    song = Song(ppq=division, tempos=[], time_signatures=[], markers=[], tracks=[])
    pos = 8 + hlen
    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            raise MidiError("bloque de pista esperado")
        size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        pos += 8 + size
        tr = Track()
        open_notes: dict[tuple[int, int], list[tuple[int, int]]] = {}
        channels = set()
        p = 0
        tick = 0
        status = 0
        while p < len(body):
            delta, p = _read_vlq(body, p)
            tick += delta
            b = body[p]
            if b & 0x80:
                status = b
                p += 1
            elif status == 0:
                raise MidiError("running status sin estado previo")
            if status == 0xFF:
                mtype = body[p]
                ln, p = _read_vlq(body, p + 1)
                payload = body[p:p + ln]
                p += ln
                if mtype == 0x2F:
                    break
                if mtype == 0x51 and ln == 3:
                    song.tempos.append((tick, round(60_000_000 / int.from_bytes(payload, "big"), 3)))
                elif mtype == 0x58 and ln >= 2:
                    song.time_signatures.append((tick, payload[0], 1 << payload[1]))
                elif mtype == 0x59 and ln == 2:
                    sf = payload[0] - 256 if payload[0] > 127 else payload[0]
                    song.key_signature = (sf, payload[1])
                elif mtype == 0x06:
                    song.markers.append((tick, payload.decode("latin-1")))
                elif mtype == 0x03 and not tr.name:
                    tr.name = payload.decode("latin-1")
                status = 0  # los meta-eventos cancelan el running status
                continue
            if status in (0xF0, 0xF7):
                ln, p = _read_vlq(body, p)
                p += ln
                status = 0
                continue
            kind, ch = status & 0xF0, status & 0x0F
            nbytes = 1 if kind in (0xC0, 0xD0) else 2
            d = body[p:p + nbytes]
            p += nbytes
            channels.add(ch)
            if kind == 0x90 and d[1] > 0:
                open_notes.setdefault((ch, d[0]), []).append((tick, d[1]))
            elif kind == 0x80 or (kind == 0x90 and d[1] == 0):
                stack = open_notes.get((ch, d[0]))
                if stack:
                    st, vel = stack.pop(0)
                    if tick > st:
                        tr.notes.append(Note(st, tick - st, d[0], vel))
            elif kind == 0xC0:
                tr.program = d[0]
            elif kind == 0xB0:
                tr.controllers.append((tick, d[0], d[1]))
        tr.channel = min(channels) if channels else 0
        if tr.notes or tr.controllers or tr.program is not None:
            song.tracks.append(tr)
    if not song.tempos:
        song.tempos = [(0, 120.0)]
    if not song.time_signatures:
        song.time_signatures = [(0, 4, 4)]
    return song


def validate_roundtrip(song: Song, path) -> dict:
    """Escribe, vuelve a leer y compara. Garantiza que el archivo es legible y fiel."""
    write_midi(song, path)
    back = read_midi(path)
    issues = []
    if len(back.tracks) != len([t for t in song.tracks if t.notes or t.controllers or t.program is not None]):
        issues.append(f"número de pistas distinto: {len(back.tracks)}")
    for a in song.tracks:
        b = back.track(a.name or "Track")
        if b is None:
            issues.append(f"falta la pista {a.name}")
            continue
        na = sorted((n.start, n.duration, n.pitch, max(1, min(127, n.velocity))) for n in a.notes)
        nb = sorted((n.start, n.duration, n.pitch, n.velocity) for n in b.notes)
        if na != nb:
            issues.append(f"notas distintas en {a.name}: {len(na)} vs {len(nb)}")
    if abs(back.bpm - song.bpm) > 0.01:
        issues.append(f"tempo distinto: {back.bpm}")
    return {"ok": not issues, "issues": issues, "tracks": len(back.tracks),
            "notes": sum(len(t.notes) for t in back.tracks)}
