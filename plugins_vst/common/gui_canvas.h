// Lienzo de dibujo por software compartido por los plugins (AIE Clip, AIE Scope): píxeles 0x00RRGGBB,
// rectángulos redondeados, líneas suavizadas y texto con letras Inter rasterizadas (gui_font.h).
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <string>
#include <vector>

#include "gui_font.h"

namespace gui {

typedef uint32_t Color;  // 0xAARRGGBB
inline Color rgb(int r, int g, int b, int a = 255) { return (Color(a) << 24) | (Color(r) << 16) | (Color(g) << 8) | Color(b); }
inline Color withAlpha(Color c, int a) { return (c & 0x00FFFFFF) | (Color(a) << 24); }
inline Color mix(Color a, Color b, double t) {
    auto ch = [&](int s) { return int(((a >> s) & 255) * (1 - t) + ((b >> s) & 255) * t + 0.5); };
    return rgb(ch(16), ch(8), ch(0), ch(24));
}

struct Rect {
    int x = 0, y = 0, w = 0, h = 0;
    bool contains(int px, int py) const { return px >= x && py >= y && px < x + w && py < y + h; }
    int r() const { return x + w; }
    int b() const { return y + h; }
};

// ---------------------------------------------------------------------------------------------
// Lienzo: píxeles 0x00RRGGBB (el mismo formato que un DIB de 32 bits de Windows)
class Canvas {
public:
    int w = 0, h = 0;
    std::vector<uint32_t> px;
    void resize(int W, int H) { w = W; h = H; px.assign(size_t(W) * H, 0); }

    inline void blend(int x, int y, Color c, double cover = 1.0) {
        if (x < 0 || y < 0 || x >= w || y >= h) return;
        double a = ((c >> 24) & 255) / 255.0 * cover;
        if (a <= 0) return;
        uint32_t& d = px[size_t(y) * w + x];
        if (a >= 1) { d = c & 0x00FFFFFF; return; }
        int r = int(((c >> 16) & 255) * a + ((d >> 16) & 255) * (1 - a) + 0.5);
        int g = int(((c >> 8) & 255) * a + ((d >> 8) & 255) * (1 - a) + 0.5);
        int b = int((c & 255) * a + (d & 255) * (1 - a) + 0.5);
        d = (uint32_t(r) << 16) | (uint32_t(g) << 8) | uint32_t(b);
    }
    void fill(Rect r, Color c) {
        int x0 = std::max(0, r.x), y0 = std::max(0, r.y), x1 = std::min(w, r.r()), y1 = std::min(h, r.b());
        if ((c >> 24) == 255) {
            for (int y = y0; y < y1; ++y) std::fill(px.begin() + size_t(y) * w + x0, px.begin() + size_t(y) * w + x1, c & 0x00FFFFFF);
            return;
        }
        for (int y = y0; y < y1; ++y)
            for (int x = x0; x < x1; ++x) blend(x, y, c);
    }
    void vgradient(Rect r, Color top, Color bottom) {
        for (int y = 0; y < r.h; ++y) fill({r.x, r.y + y, r.w, 1}, mix(top, bottom, r.h > 1 ? double(y) / (r.h - 1) : 0));
    }
    // rectángulo redondeado con bordes suavizados
    void rounded(Rect r, double rad, Color c) {
        int ir = int(std::ceil(rad));
        for (int y = r.y; y < r.b(); ++y) {
            bool rowCorner = y < r.y + ir || y >= r.b() - ir;
            if (!rowCorner) {  // filas centrales: sin esquinas, relleno directo
                fill({r.x, y, r.w, 1}, c);
                continue;
            }
            for (int x = r.x; x < r.r(); ++x) {
                double cx = std::min(std::max(x + 0.5, r.x + rad), r.r() - rad);
                double cy = std::min(std::max(y + 0.5, r.y + rad), r.b() - rad);
                double d = std::hypot(x + 0.5 - cx, y + 0.5 - cy);
                double cover = std::min(1.0, std::max(0.0, rad - d + 0.5));
                if (rad <= 0) cover = 1;
                blend(x, y, c, cover);
            }
        }
    }
    static double roundCover(Rect r, double rad, int x, int y) {
        if (r.w <= 0 || r.h <= 0) return 0;
        double cx = std::min(std::max(x + 0.5, r.x + rad), r.r() - rad);
        double cy = std::min(std::max(y + 0.5, r.y + rad), r.b() - rad);
        double d = std::hypot(x + 0.5 - cx, y + 0.5 - cy);
        bool inside = x >= r.x && y >= r.y && x < r.r() && y < r.b();
        if (!inside) return 0;
        return rad <= 0 ? 1.0 : std::min(1.0, std::max(0.0, rad - d + 0.5));
    }
    void roundedOutline(Rect r, double rad, Color c) {
        Rect in = {r.x + 1, r.y + 1, r.w - 2, r.h - 2};
        for (int y = r.y; y < r.b(); ++y)
            for (int x = r.x; x < r.r(); ++x) {
                double cover = roundCover(r, rad, x, y) - roundCover(in, std::max(0.0, rad - 1), x, y);
                if (cover > 0) blend(x, y, c, cover);
            }
    }
    // polilínea suavizada de grosor 'width' (cobertura máxima, sin oscurecer las uniones)
    void polyline(const std::vector<double>& xs, const std::vector<double>& ys, double width, Color c) {
        if (xs.size() < 2) return;
        double minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
        for (size_t i = 0; i < xs.size(); ++i) {
            minx = std::min(minx, xs[i]); maxx = std::max(maxx, xs[i]);
            miny = std::min(miny, ys[i]); maxy = std::max(maxy, ys[i]);
        }
        int x0 = std::max(0, int(minx - width - 1)), x1 = std::min(w - 1, int(maxx + width + 1));
        int y0 = std::max(0, int(miny - width - 1)), y1 = std::min(h - 1, int(maxy + width + 1));
        int mw = x1 - x0 + 1, mh = y1 - y0 + 1;
        if (mw <= 0 || mh <= 0) return;
        std::vector<float> mask(size_t(mw) * mh, 0.0f);
        double hw = width / 2.0;
        for (size_t i = 0; i + 1 < xs.size(); ++i) {
            double ax = xs[i], ay = ys[i], bx = xs[i + 1], by = ys[i + 1];
            int sx0 = std::max(x0, int(std::min(ax, bx) - hw - 1)), sx1 = std::min(x1, int(std::max(ax, bx) + hw + 1));
            int sy0 = std::max(y0, int(std::min(ay, by) - hw - 1)), sy1 = std::min(y1, int(std::max(ay, by) + hw + 1));
            double dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
            for (int y = sy0; y <= sy1; ++y) {
                for (int x = sx0; x <= sx1; ++x) {
                    double px_ = x + 0.5, py = y + 0.5;
                    double t = len2 > 0 ? ((px_ - ax) * dx + (py - ay) * dy) / len2 : 0;
                    t = std::min(1.0, std::max(0.0, t));
                    double d = std::hypot(px_ - (ax + t * dx), py - (ay + t * dy));
                    float cover = float(std::min(1.0, std::max(0.0, hw - d + 0.5)));
                    float& m = mask[size_t(y - y0) * mw + (x - x0)];
                    if (cover > m) m = cover;
                }
            }
        }
        for (int y = 0; y < mh; ++y)
            for (int x = 0; x < mw; ++x) {
                float m = mask[size_t(y) * mw + x];
                if (m > 0) blend(x0 + x, y0 + y, c, m);
            }
    }
    void line(double ax, double ay, double bx, double by, double width, Color c) {
        polyline({ax, bx}, {ay, by}, width, c);
    }
    void circle(double cx, double cy, double rad, Color c) {
        for (int y = int(cy - rad - 1); y <= int(cy + rad + 1); ++y)
            for (int x = int(cx - rad - 1); x <= int(cx + rad + 1); ++x) {
                double d = std::hypot(x + 0.5 - cx, y + 0.5 - cy);
                blend(x, y, c, std::min(1.0, std::max(0.0, rad - d + 0.5)));
            }
    }

    // ---- texto (UTF-8) ----
    static uint32_t nextCp(const char*& s) {
        unsigned char c = (unsigned char)*s++;
        if (c < 0x80) return c;
        if ((c & 0xE0) == 0xC0 && *s) return ((c & 0x1F) << 6) | ((unsigned char)*s++ & 0x3F);
        if ((c & 0xF0) == 0xE0 && s[0] && s[1]) { uint32_t v = ((c & 0x0F) << 12) | ((s[0] & 0x3F) << 6) | (s[1] & 0x3F); s += 2; return v; }
        return '?';
    }
    static const Glyph* glyph(const Font& f, uint32_t cp) {
        for (int i = 0; i < f.count; ++i) if (f.glyphs[i].cp == cp) return &f.glyphs[i];
        return nullptr;
    }
    static int textWidth(const Font& f, const std::string& s) {
        int wd = 0;
        for (const char* p = s.c_str(); *p;) { const Glyph* g = glyph(f, nextCp(p)); if (g) wd += g->adv; }
        return wd;
    }
    // align: 0 izquierda, 1 centro, 2 derecha; y es la parte superior de la línea
    void text(const Font& f, int x, int y, const std::string& s, Color c, int align = 0) {
        if (align == 1) x -= textWidth(f, s) / 2;
        else if (align == 2) x -= textWidth(f, s);
        for (const char* p = s.c_str(); *p;) {
            const Glyph* g = glyph(f, nextCp(p));
            if (!g) continue;
            const uint8_t* bits = f.bits + g->off;
            for (int gy = 0; gy < g->h; ++gy)
                for (int gx = 0; gx < g->w; ++gx) {
                    uint8_t a = bits[gy * g->w + gx];
                    if (a) blend(x + g->ox + gx, y + g->oy + gy, c, a / 255.0);
                }
            x += g->adv;
        }
    }
};


// paleta común
static const Color kBg0 = rgb(16, 20, 28), kBg1 = rgb(24, 30, 41), kPanel = rgb(30, 37, 50), kPanelHi = rgb(40, 49, 66);
static const Color kLine = rgb(55, 66, 86), kText = rgb(226, 232, 242), kDim = rgb(140, 152, 172), kFaint = rgb(92, 104, 124);
static const Color kAccent = rgb(64, 196, 255), kAccent2 = rgb(255, 154, 60), kRed = rgb(255, 82, 82), kGreen = rgb(74, 222, 128);
inline double dbOf(double lin) { return lin > 1e-9 ? 20.0 * std::log10(lin) : -200.0; }

}  // namespace gui
