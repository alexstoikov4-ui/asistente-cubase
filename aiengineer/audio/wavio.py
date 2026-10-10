"""Lectura/escritura WAV propia (PCM 8/16/24/32 bits, float 32/64, WAVE_FORMAT_EXTENSIBLE).

No depende de bibliotecas externas salvo numpy. Las muestras se devuelven como float64 en [-1, 1),
con forma (n_muestras, n_canales).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PCM = 1
IEEE_FLOAT = 3
EXTENSIBLE = 0xFFFE


class WavError(ValueError):
    pass


@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    bits: int
    format_tag: int
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def _parse_chunks(data: bytes):
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise WavError("no es un archivo RIFF/WAVE")
    pos = 12
    fmt = None
    pcm = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            if size == 0xFFFFFFFF or pos + 8 + size > len(data):
                body = data[pos + 8:]  # tamaño truncado/desconocido: se lee hasta el final
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise WavError("faltan los bloques fmt o data")
    return fmt, pcm


def read_info(path) -> WavInfo:
    _, info = read_wav(path, info_only=True)
    return info


def read_wav(path, info_only: bool = False):
    data = Path(path).read_bytes()
    fmt, pcm = _parse_chunks(data)
    if len(fmt) < 16:
        raise WavError("bloque fmt demasiado corto")
    tag, ch, sr, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == EXTENSIBLE and len(fmt) >= 40:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag not in (PCM, IEEE_FLOAT):
        raise WavError(f"formato no soportado (tag {tag})")
    if ch < 1 or sr < 1:
        raise WavError("cabecera inválida")
    bps = bits // 8
    if block != ch * bps:
        block = ch * bps
    frames = len(pcm) // block
    info = WavInfo(sr, ch, bits, tag, frames)
    if info_only:
        return None, info
    raw = pcm[:frames * block]
    if tag == IEEE_FLOAT:
        if bits == 32:
            x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
        elif bits == 64:
            x = np.frombuffer(raw, dtype="<f8").copy()
        else:
            raise WavError(f"float de {bits} bits no soportado")
    elif bits == 8:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif bits == 16:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float64) / float(1 << 23)
    elif bits == 32:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise WavError(f"PCM de {bits} bits no soportado")
    return x.reshape(-1, ch), info


def write_wav(path, samples: np.ndarray, sample_rate: int, bits: int = 24, float_format: bool = False) -> Path:
    """Escribe un WAV. En PCM se recorta a [-1, 1) y se aplica redondeo (sin dither: lo decide el llamador)."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    frames, ch = x.shape
    if float_format:
        if bits != 32:
            raise WavError("solo float de 32 bits")
        raw = x.astype("<f4").tobytes()
        tag = IEEE_FLOAT
    else:
        tag = PCM
        if bits == 16:
            raw = np.clip(np.round(x * 32768.0), -32768, 32767).astype("<i2").tobytes()
        elif bits == 24:
            v = np.clip(np.round(x * (1 << 23)), -(1 << 23), (1 << 23) - 1).astype(np.int32)
            v = np.where(v < 0, v + (1 << 24), v).astype(np.uint32)
            b = np.stack([(v & 0xFF), (v >> 8) & 0xFF, (v >> 16) & 0xFF], axis=-1).astype(np.uint8)
            raw = b.tobytes()
        elif bits == 32:
            raw = np.clip(np.round(x * 2147483648.0), -2147483648, 2147483647).astype("<i4").tobytes()
        else:
            raise WavError(f"profundidad no soportada: {bits}")
    block = ch * bits // 8
    fmt = struct.pack("<HHIIHH", tag, ch, sample_rate, sample_rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    if len(raw) & 1:
        body += b"\x00"
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    tmp.replace(path)
    return path
