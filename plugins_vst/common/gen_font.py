"""Genera gui_font.h: fuentes Inter rasterizadas (alfa de 8 bits) para la interfaz de AIE Clip.

Uso: python plugins_vst/common/gen_font.py  (necesita Pillow y la fuente Inter instalada; Inter: SIL Open Font License 1.1)
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gui_font.h")
CANDIDATES = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/inter", "C:/Windows/Fonts"]
FONTS = [  # (nombre en C, archivo, tamaño en píxeles)
    ("kFontSmall", "Inter-Medium.otf", 10),
    ("kFontNormal", "Inter-SemiBold.otf", 12),
    ("kFontBig", "Inter-Bold.otf", 21),
]
CHARS = [chr(c) for c in range(32, 127)] + list("áéíóúñ°±·")


def find(name):
    for d in CANDIDATES:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    sys.exit(f"no encuentro {name}")


def main():
    out = ["// Generado por tools/gen_font.py a partir de Inter (SIL Open Font License 1.1). No editar a mano.",
           "#pragma once", "#include <cstdint>", "",
           "struct Glyph { uint16_t cp; int8_t w, h, ox, oy, adv; uint32_t off; };",
           "struct Font { const Glyph* glyphs; int count; const uint8_t* bits; int ascent, height; };", ""]
    for cname, fname, size in FONTS:
        font = ImageFont.truetype(find(fname), size)
        ascent, descent = font.getmetrics()
        glyphs, bits = [], []
        for ch in CHARS:
            adv = int(round(font.getlength(ch)))
            bbox = font.getbbox(ch)  # relativo al origen con la línea superior en y=0
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                glyphs.append((ord(ch), 0, 0, 0, 0, adv, len(bits)))
                continue
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            img = Image.new("L", (w, h), 0)
            ImageDraw.Draw(img).text((-bbox[0], -bbox[1]), ch, font=font, fill=255)
            off = len(bits)
            bits.extend(img.tobytes())
            glyphs.append((ord(ch), w, h, bbox[0], bbox[1], adv, off))
        out.append(f"static const uint8_t {cname}Bits[] = {{")
        for i in range(0, len(bits), 32):
            out.append("    " + ",".join(str(b) for b in bits[i:i + 32]) + ",")
        out.append("};")
        out.append(f"static const Glyph {cname}Glyphs[] = {{")
        for g in glyphs:
            out.append("    {%d,%d,%d,%d,%d,%d,%d}," % g)
        out.append("};")
        out.append(f"static const Font {cname} = {{{cname}Glyphs, {len(glyphs)}, {cname}Bits, {ascent}, {ascent + descent}}};")
        out.append("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("escrito", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
