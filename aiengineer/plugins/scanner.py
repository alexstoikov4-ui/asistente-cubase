"""Escáner de plugins VST: encuentra lo que realmente está instalado y su arquitectura (32/64 bits).

En Windows lee las rutas VST del registro y las carpetas habituales; en cualquier sistema puede
escanear carpetas indicadas por el usuario. La arquitectura se obtiene de la cabecera PE del DLL,
no se supone.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

MACHINE = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}


def pe_architecture(path: Path) -> str | None:
    """'x86' (32 bits), 'x64' (64 bits), 'arm64' o None si no es un ejecutable PE."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return None
            off = struct.unpack("<I", head[0x3C:0x40])[0]
            f.seek(off)
            sig = f.read(6)
            if len(sig) < 6 or sig[:4] != b"PE\0\0":
                return None
            return MACHINE.get(struct.unpack("<H", sig[4:6])[0], "unknown")
    except OSError:
        return None


@dataclass
class PluginInfo:
    name: str
    path: str
    format: str  # VST2 | VST3 | Waves bundle
    arch: str | None
    vendor: str = ""
    roles: list = field(default_factory=list)
    compatible: bool | None = None
    note: str = ""


# Catálogo de plugins conocidos: patrón -> (fabricante, nombre canónico, papeles)
KNOWN = [
    (r"pro-?q\s*3", "FabFilter", "FabFilter Pro-Q 3", ["eq", "dynamic_eq", "linear_phase_eq", "analyzer"]),
    (r"saturn\s*2", "FabFilter", "FabFilter Saturn 2", ["saturation", "multiband"]),
    (r"limiter\s*(no\.?\s*)?6", "vladg/sound", "Limiter 6", ["limiter", "compressor", "clipper"]),
    (r"waveshell", "Waves", "WaveShell (contenedor de plugins Waves)", ["shell"]),
    (r"^c1\b|c1 comp", "Waves", "C1", ["compressor", "gate", "sidechain"]),
    (r"^c4\b", "Waves", "C4", ["multiband"]),
    (r"^l1\b", "Waves", "L1", ["limiter"]),
    (r"^l2\b", "Waves", "L2", ["limiter"]),
    (r"^l3\b", "Waves", "L3", ["limiter", "multiband"]),
    (r"^q10\b", "Waves", "Q10", ["eq"]),
    (r"linmb|linear phase multiband", "Waves", "LinMB", ["multiband"]),
    (r"lineq", "Waves", "LinEQ", ["eq", "linear_phase_eq"]),
    (r"renaissance comp|rcomp|r-comp", "Waves", "Renaissance Compressor", ["compressor"]),
    (r"renaissance eq|req", "Waves", "Renaissance Equalizer", ["eq"]),
    (r"renaissance bass|rbass", "Waves", "Renaissance Bass", ["bass_enhancer"]),
    (r"renaissance vox|rvox", "Waves", "Renaissance Vox", ["compressor"]),
    (r"renaissance reverb|rverb", "Waves", "Renaissance Reverb", ["reverb"]),
    (r"trueverb", "Waves", "TrueVerb", ["reverb"]),
    (r"supertap", "Waves", "SuperTap", ["delay"]),
    (r"h-?delay", "Waves", "H-Delay", ["delay"]),
    (r"h-?comp", "Waves", "H-Comp", ["compressor"]),
    (r"maxxbass", "Waves", "MaxxBass", ["bass_enhancer"]),
    (r"^s1\b|s1 imager|^imager", "Waves", "S1 Imager", ["stereo"]),
    (r"cla-?76|cla-?2a|cla-?3a", "Waves", "CLA", ["compressor"]),
    (r"^ssl", "Waves", "SSL", ["compressor", "eq"]),
    (r"^api\b|^api[- ]?\d", "Waves", "API", ["compressor", "eq"]),
    (r"kramer", "Waves", "Kramer Master Tape", ["saturation"]),
    (r"deesser", "Waves", "DeEsser", ["deesser"]),
    (r"trans-?x", "Waves", "TransX", ["transient"]),
    (r"paz", "Waves", "PAZ Analyzer", ["analyzer"]),
    (r"mv2", "Waves", "MV2", ["compressor"]),
    (r"vitamin", "Waves", "Vitamin", ["multiband", "stereo"]),
    # Plugins incluidos con Cubase (se confirman al encontrarlos en la carpeta VST3 de Cubase)
    (r"studioeq", "Steinberg", "StudioEQ", ["eq"]),
    (r"vintagecompressor", "Steinberg", "VintageCompressor", ["compressor"]),
    (r"tubecompressor", "Steinberg", "TubeCompressor", ["compressor", "saturation"]),
    (r"multibandcompressor", "Steinberg", "MultibandCompressor", ["multiband"]),
    (r"^compressor", "Steinberg", "Compressor", ["compressor", "sidechain"]),
    (r"maximizer", "Steinberg", "Maximizer", ["limiter"]),
    (r"^limiter$", "Steinberg", "Limiter", ["limiter"]),
    (r"envelopeshaper", "Steinberg", "EnvelopeShaper", ["transient"]),
    (r"roomworks", "Steinberg", "RoomWorks", ["reverb"]),
    (r"reverence", "Steinberg", "REVerence", ["reverb"]),
    (r"stereodelay|monodelay|pingpong", "Steinberg", "Delay", ["delay"]),
    (r"magneto", "Steinberg", "Magneto", ["saturation"]),
    (r"datube", "Steinberg", "DaTube", ["saturation"]),
    (r"stereoenhancer", "Steinberg", "StereoEnhancer", ["stereo"]),
    (r"softclipper", "Steinberg", "SoftClipper", ["saturation", "clipper"]),
    (r"deesser", "Steinberg", "DeEsser", ["deesser"]),
]


def classify(name: str) -> tuple[str, str, list]:
    n = name.lower().strip()
    n = re.sub(r"\.(dll|vst3|bundle)$", "", n)
    n = re.sub(r"[_\s]*(x64|x86|64|32|\(x64\)|\(x86\))$", "", n).strip()
    for pat, vendor, canon, roles in KNOWN:
        if re.search(pat, n):
            return vendor, canon, roles
    return "", name, []


def default_vst_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore
            for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VST"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VST"),
                              (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VST")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        val, _ = winreg.QueryValueEx(k, "VSTPluginsPath")
                        dirs.append(Path(val))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "CommonProgramFiles(x86)"):
            base = os.environ.get(env)
            if not base:
                continue
            b = Path(base)
            dirs += [b / "VST3", b / "Steinberg" / "VSTPlugins", b / "VSTPlugins", b / "Steinberg" / "Cubase 5" / "VSTPlugins",
                     b / "Steinberg" / "Cubase 5" / "VST3", b / "Waves" / "Plug-Ins", b / "Waves" / "WaveShells",
                     b / "FabFilter"]
    seen, out = set(), []
    for d in dirs:
        k = str(d).lower()
        if k not in seen and d.exists():
            seen.add(k)
            out.append(d)
    return out


def scan(dirs: list[Path], host_arch: str | None = None, max_depth: int = 6) -> list[PluginInfo]:
    """Escanea carpetas. host_arch: arquitectura de Cubase ('x86' o 'x64') para valorar compatibilidad."""
    found: list[PluginInfo] = []
    seen = set()
    for root in dirs:
        root = Path(root)
        if not root.exists():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
            here = Path(dirpath)
            for d in list(dirnames):
                if d.lower().endswith(".bundle"):  # bundles de Waves (Plug-Ins)
                    vendor, canon, roles = classify(d)
                    found.append(PluginInfo(canon if vendor else d[:-7], str(here / d), "Waves bundle", None,
                                            vendor or "Waves", roles, note="Se carga a través de WaveShell."))
                    dirnames.remove(d)
                elif d.lower().endswith(".vst3"):  # paquete VST3 (carpeta)
                    inner = list((here / d).rglob("*.vst3"))
                    files = [p for p in inner if p.is_file()]
                    arch = next((pe_architecture(p) for p in files if pe_architecture(p)), None)
                    vendor, canon, roles = classify(d)
                    found.append(PluginInfo(canon if vendor else d[:-5], str(here / d), "VST3", arch, vendor, roles))
                    dirnames.remove(d)
            for fn in filenames:
                low = fn.lower()
                if not (low.endswith(".dll") or low.endswith(".vst3")):
                    continue
                p = here / fn
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                arch = pe_architecture(p)
                if arch is None:
                    continue
                vendor, canon, roles = classify(fn)
                found.append(PluginInfo(canon if vendor else Path(fn).stem, str(p),
                                        "VST3" if low.endswith(".vst3") else "VST2", arch, vendor, roles))
    for pl in found:
        pl.compatible, note = compatibility(pl, host_arch)
        if note:
            pl.note = (pl.note + " " + note).strip()
    return found


def compatibility(pl: PluginInfo, host_arch: str | None) -> tuple[bool | None, str]:
    if host_arch is None or pl.arch is None:
        return None, ""
    if pl.format == "VST3" and pl.arch != host_arch:
        return False, "VST3 de otra arquitectura: Cubase 5 no puede puentearlo."
    if pl.arch == host_arch:
        return True, ""
    if host_arch == "x64" and pl.arch == "x86":
        return True, "32 bits en Cubase 64 bits: requiere VST Bridge (puede ser inestable)."
    return False, "Plugin de 64 bits: no se puede cargar en Cubase 5 de 32 bits."


def roles_index(plugins: list[PluginInfo], only_compatible: bool = True) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for p in plugins:
        if only_compatible and p.compatible is False:
            continue
        for r in p.roles:
            if p.name not in idx.setdefault(r, []):
                idx[r].append(p.name)
    return idx


def save_inventory(plugins: list[PluginInfo], path: Path) -> None:
    Path(path).write_text(json.dumps([asdict(p) for p in plugins], indent=2, ensure_ascii=False), encoding="utf-8")


def load_inventory(path: Path) -> list[PluginInfo]:
    p = Path(path)
    if not p.exists():
        return []
    return [PluginInfo(**d) for d in json.loads(p.read_text(encoding="utf-8"))]
