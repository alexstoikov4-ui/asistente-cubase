// Interfaz gráfica de AIE Clip, independiente del sistema: se dibuja píxel a píxel en un lienzo propio
// (con letras Inter rasterizadas) y la ventana de Windows solo copia el lienzo a pantalla y pasa el ratón.
// Así la misma interfaz se puede renderizar a PNG en Linux para revisarla y probarla.
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

// ---------------------------------------------------------------------------------------------
// Lo que la interfaz necesita del plugin
struct Meters {
    float inPeak[2] = {0, 0}, outPeak[2] = {0, 0}, inRms[2] = {0, 0}, outRms[2] = {0, 0};
    float gr = 0;  // reducción máxima del bloque, en dB (positiva)
};

struct EditorHost {
    virtual ~EditorHost() {}
    virtual float getParam(int i) = 0;
    virtual void beginEdit(int i) = 0;
    virtual void setParam(int i, float v) = 0;  // también avisa al anfitrión (automatización)
    virtual void endEdit(int i) = 0;
    virtual std::string paramText(int i) = 0;
    virtual Meters takeMeters() = 0;  // valores desde la última lectura
    // muestra un menú y devuelve el elemento elegido o -1
    virtual int popupMenu(int x, int y, const std::vector<std::string>& items, int checked) = 0;
    virtual double curve(double xLinear) = 0;  // curva de recorte actual (para la gráfica)
    virtual double ceilingLinear() = 0;
    virtual int latency() = 0;
};

// índices de parámetros (deben coincidir con aieclip.cpp)
enum { pInput = 0, pClip, pSoft, pOversampling, pOutput, pMix, pCeiling, pDelta, pMode, pCount };

static const Color kBg0 = rgb(16, 20, 28), kBg1 = rgb(24, 30, 41), kPanel = rgb(30, 37, 50), kPanelHi = rgb(40, 49, 66);
static const Color kLine = rgb(55, 66, 86), kText = rgb(226, 232, 242), kDim = rgb(140, 152, 172), kFaint = rgb(92, 104, 124);
static const Color kAccent = rgb(64, 196, 255), kAccent2 = rgb(255, 154, 60), kRed = rgb(255, 82, 82), kGreen = rgb(74, 222, 128);

inline double dbOf(double lin) { return lin > 1e-9 ? 20.0 * std::log10(lin) : -200.0; }

// ---------------------------------------------------------------------------------------------
class Editor {
public:
    static const int W = 800, H = 470;
    EditorHost* host;
    Canvas cv;

    explicit Editor(EditorHost* h) : host(h) {
        cv.resize(W, H);
        std::fill(std::begin(histOut), std::end(histOut), 0.0f);
        std::fill(std::begin(histGr), std::end(histGr), 0.0f);
        layout();
    }

    // ---- disposición ----
    struct Slider { int param; Rect r; bool vertical; const char* label; float def; };
    Slider sliders[5];
    Rect btnMode, btnOs, tglCeil, tglDelta, graph, hist, inMeter[2], outMeter[2], grMeter, inReadout, outReadout, grReadout;

    void layout() {
        int top = 76, bot = 396;
        inMeter[0] = {24, top, 14, bot - top};
        inMeter[1] = {42, top, 14, bot - top};
        sliders[0] = {pInput, {100, top, 36, bot - top}, true, "Gain", 1.0f / 3};
        sliders[1] = {pClip, {160, top, 36, bot - top}, true, "Clip", float(23.7 / 24)};
        graph = {236, 64, 328, 178};
        btnMode = {316, 256, 248, 28};
        sliders[2] = {pSoft, {316, 296, 248, 24}, false, "Saturación", 0.3f};
        btnOs = {316, 332, 112, 28};
        tglCeil = {444, 332, 120, 28};
        sliders[3] = {pMix, {316, 372, 160, 24}, false, "Mix", 1.0f};
        tglDelta = {490, 370, 74, 28};
        hist = {236, 410, 328, 40};
        grMeter = {596, top, 14, bot - top};
        sliders[4] = {pOutput, {650, top, 36, bot - top}, true, "Output", float(24.0 / 36)};
        outMeter[0] = {724, top, 14, bot - top};
        outMeter[1] = {742, top, 14, bot - top};
        inReadout = {16, 420, 48, 20};
        grReadout = {586, 420, 34, 20};
        outReadout = {716, 420, 48, 20};
    }

    // ---- estado de medidores ----
    float dispIn[2] = {0, 0}, dispOut[2] = {0, 0}, dispGr = 0, holdIn = 0, holdOut = 0, holdGr = 0;
    float histOut[328], histGr[328];
    int histPos = 0;

    void tickMeters() {
        Meters m = host->takeMeters();
        const float fall = 0.82f;  // caída de las barras por fotograma (~30 fps)
        for (int c = 0; c < 2; ++c) {
            dispIn[c] = std::max(m.inPeak[c], dispIn[c] * fall);
            dispOut[c] = std::max(m.outPeak[c], dispOut[c] * fall);
            holdIn = std::max(holdIn, m.inPeak[c]);
            holdOut = std::max(holdOut, m.outPeak[c]);
        }
        dispGr = std::max(m.gr, dispGr * fall);
        holdGr = std::max(holdGr, m.gr);
        histOut[histPos] = std::max(m.outPeak[0], m.outPeak[1]);
        histGr[histPos] = m.gr;
        histPos = (histPos + 1) % 328;
    }

    // escala de los medidores: +6 .. -60 dB
    static double meterFrac(double db) { return std::min(1.0, std::max(0.0, (db + 60.0) / 66.0)); }
    static int meterY(const Rect& r, double db) { return r.b() - int(meterFrac(db) * r.h + 0.5); }

    // ---- dibujo ----
    // Lo que no cambia (fondo, paneles, escalas, etiquetas) se dibuja una vez y se copia en cada fotograma.
    std::vector<uint32_t> background;
    void drawBackground() {
        cv.vgradient({0, 0, W, H}, kBg1, kBg0);
        // cabecera
        cv.vgradient({0, 0, W, 48}, rgb(34, 42, 58), rgb(24, 30, 41));
        cv.fill({0, 48, W, 1}, kLine);
        cv.rounded({18, 14, 20, 20}, 4, kAccent);
        cv.rounded({22, 18, 12, 12}, 2, rgb(24, 30, 41));
        cv.fill({22, 23, 12, 2}, kAccent2);
        cv.text(kFontBig, 46, 12, "AIE Clip", kText);
        cv.text(kFontSmall, 142, 21, "AI Audio Engineer  ·  clipper con sobremuestreo", kDim);
        char lat[64];
        std::snprintf(lat, sizeof lat, "Latencia %d muestras", host->latency());
        cv.text(kFontSmall, W - 18, 21, lat, kFaint, 2);

        // paneles
        cv.rounded({10, 58, 206, 400}, 8, kPanel);
        cv.rounded({226, 58, 348, 400}, 8, kPanel);
        cv.rounded({584, 58, 206, 400}, 8, kPanel);
        drawMeterScale(62, inMeter[0]);
        drawMeterScale(764, outMeter[0]);
        cv.text(kFontSmall, 40, 400, "IN", kDim, 1);
        cv.text(kFontSmall, grMeter.x + 7, 400, "GR", kDim, 1);
        cv.text(kFontSmall, 740, 400, "OUT", kDim, 1);
        for (int d = 0; d <= 24; d += 6) {
            int y = grMeter.y + int(d / 24.0 * grMeter.h);
            cv.fill({grMeter.r() + 3, y, 4, 1}, kFaint);
            char b[8];
            std::snprintf(b, sizeof b, "%d", d);
            cv.text(kFontSmall, grMeter.r() + 9, y - 6, b, kFaint);
        }
        drawLabel(236, btnMode, "Modo");
        drawLabel(236, sliders[2].r, "Saturación");
        drawLabel(236, btnOs, "Oversmp.");
        drawLabel(236, sliders[3].r, "Mix");
        for (auto& s : sliders)
            if (s.vertical) cv.text(kFontNormal, s.r.x + s.r.w / 2, s.r.y - 22, s.label, kDim, 1);
        background = cv.px;
    }

    void draw() {
        if (background.size() != cv.px.size()) drawBackground();
        else std::copy(background.begin(), background.end(), cv.px.begin());
        for (int c = 0; c < 2; ++c) drawMeter(inMeter[c], dispIn[c]);
        drawReadout(inReadout, holdIn, false);

        for (auto& s : sliders) drawSlider(s);

        drawGraph();
        drawButton(btnMode, host->paramText(pMode));
        drawButton(btnOs, host->paramText(pOversampling));
        drawToggle(tglCeil, "Ceiling", host->getParam(pCeiling) >= 0.5f, kAccent2);
        drawToggle(tglDelta, "Delta", host->getParam(pDelta) >= 0.5f, kRed);
        drawHistory();

        // reducción
        cv.rounded({grMeter.x - 1, grMeter.y - 1, grMeter.w + 2, grMeter.h + 2}, 3, rgb(12, 15, 21));
        int gh = int(std::min(1.0, dispGr / 24.0) * grMeter.h + 0.5);
        cv.vgradient({grMeter.x, grMeter.y, grMeter.w, gh}, kRed, rgb(200, 50, 60));
        drawReadout(grReadout, holdGr, true);

        for (int c = 0; c < 2; ++c) drawMeter(outMeter[c], dispOut[c]);
        // línea del techo en el medidor de salida
        int ty = meterY(outMeter[0], dbOf(host->ceilingLinear()));
        cv.fill({outMeter[0].x - 3, ty, outMeter[1].r() - outMeter[0].x + 6, 1}, withAlpha(kAccent2, 220));
        drawReadout(outReadout, holdOut, false);
    }

    void drawLabel(int x, const Rect& r, const char* s) { cv.text(kFontNormal, x, r.y + r.h / 2 - 8, s, kDim); }

    void drawMeterScale(int x, const Rect& r) {
        static const int marks[] = {6, 0, -6, -12, -18, -24, -36, -48, -60};
        bool left = x < r.x;
        for (int m : marks) {
            int y = meterY(r, m);
            char b[8];
            std::snprintf(b, sizeof b, "%d", m);
            if (left) cv.text(kFontSmall, x - 46, y - 6, b, kFaint, 2);
            else cv.text(kFontSmall, x + 2, y - 6, b, kFaint, 0);
        }
    }

    void drawMeter(const Rect& r, float peak) {
        cv.rounded({r.x - 1, r.y - 1, r.w + 2, r.h + 2}, 3, rgb(12, 15, 21));
        double db = dbOf(peak);
        int y = meterY(r, db);
        int y0 = meterY(r, -6), yc = meterY(r, 0);
        for (int yy = y; yy < r.b(); ++yy) {
            Color c = yy < yc ? kRed : (yy < y0 ? rgb(250, 204, 21) : kGreen);
            double t = double(r.b() - yy) / r.h;
            cv.fill({r.x, yy, r.w, 1}, mix(withAlpha(c, 255), rgb(30, 60, 50), 0.15 * (1 - t)));
        }
        for (int m = 0; m >= -60; m -= 6) cv.fill({r.x, meterY(r, m), r.w, 1}, withAlpha(rgb(0, 0, 0), 90));
    }

    void drawReadout(const Rect& r, float v, bool isGr) {
        cv.rounded(r, 4, rgb(14, 18, 25));
        char b[16];
        if (isGr) std::snprintf(b, sizeof b, "%.1f", v);
        else if (v <= 1e-6f) std::snprintf(b, sizeof b, "-inf");
        else std::snprintf(b, sizeof b, "%.1f", dbOf(v));
        bool over = !isGr && v > 1.0f;
        cv.text(kFontNormal, r.x + r.w / 2, r.y + 3, b, over ? kRed : kText, 1);
    }

    void drawSlider(const Slider& s) {
        float v = host->getParam(s.param);
        std::string txt = host->paramText(s.param);
        if (s.vertical) {
            Rect tr = {s.r.x + s.r.w / 2 - 3, s.r.y, 6, s.r.h};
            cv.rounded(tr, 3, rgb(12, 15, 21));
            int ky = s.r.b() - int(v * s.r.h + 0.5);
            Color acc = s.param == pOutput ? kAccent2 : kAccent;
            cv.rounded({tr.x, ky, tr.w, s.r.b() - ky}, 3, withAlpha(acc, 150));
            Rect knob = {s.r.x + 2, ky - 9, s.r.w - 4, 18};
            cv.rounded({knob.x, knob.y + 2, knob.w, knob.h}, 5, withAlpha(rgb(0, 0, 0), 120));
            cv.rounded(knob, 5, rgb(214, 222, 236));
            for (int i = -1; i <= 1; ++i) cv.fill({knob.x + 7, knob.y + 9 + i * 3, knob.w - 14, 1}, rgb(120, 132, 152));
            cv.rounded({s.r.x - 8, s.r.b() + 22, s.r.w + 16, 20}, 4, rgb(14, 18, 25));
            cv.text(kFontNormal, s.r.x + s.r.w / 2, s.r.b() + 25, txt, kText, 1);
        } else {
            Rect tr = {s.r.x, s.r.y + s.r.h / 2 - 3, s.r.w - 60, 6};
            cv.rounded(tr, 3, rgb(12, 15, 21));
            int kx = tr.x + int(v * tr.w + 0.5);
            cv.rounded({tr.x, tr.y, kx - tr.x, tr.h}, 3, withAlpha(kAccent, 150));
            cv.circle(kx, tr.y + 3.5, 8.5, withAlpha(rgb(0, 0, 0), 110));
            cv.circle(kx, tr.y + 2.5, 8, rgb(214, 222, 236));
            cv.rounded({s.r.r() - 52, s.r.y, 52, s.r.h}, 4, rgb(14, 18, 25));
            cv.text(kFontNormal, s.r.r() - 26, s.r.y + s.r.h / 2 - 8, txt, kText, 1);
        }
    }

    void drawButton(const Rect& r, const std::string& s) {
        cv.rounded(r, 6, kPanelHi);
        cv.text(kFontNormal, r.x + 12, r.y + r.h / 2 - 8, s, kText);
        int ax = r.r() - 16, ay = r.y + r.h / 2 - 2;
        cv.polyline({ax - 4.0, double(ax), ax + 4.0}, {double(ay), ay + 4.0, double(ay)}, 1.6, kDim);
    }

    void drawToggle(const Rect& r, const char* label, bool on, Color c) {
        cv.rounded(r, 6, on ? withAlpha(c, 60) : kPanelHi);
        if (on) cv.roundedOutline(r, 6, withAlpha(c, 200));
        cv.circle(r.x + 14, r.y + r.h / 2, 4.5, on ? c : kFaint);
        cv.text(kFontNormal, r.x + 26, r.y + r.h / 2 - 8, label, on ? kText : kDim);
    }

    void drawGraph() {
        const Rect& g = graph;
        cv.rounded(g, 6, rgb(14, 18, 25));
        const double lo = -30, hi = 6;  // dB en ambos ejes (nivel a la entrada del clipper)
        auto X = [&](double db) { return g.x + 8 + (db - lo) / (hi - lo) * (g.w - 16); };
        auto Y = [&](double db) { return g.b() - 8 - (db - lo) / (hi - lo) * (g.h - 16); };
        for (int d = -24; d <= 6; d += 6) {
            Color c = d == 0 ? withAlpha(kLine, 255) : withAlpha(kLine, 140);
            cv.fill({int(X(d)), g.y + 6, 1, g.h - 12}, c);
            cv.fill({g.x + 6, int(Y(d)), g.w - 12, 1}, c);
            char b[8];
            std::snprintf(b, sizeof b, "%d", d);
            if (d < 6) cv.text(kFontSmall, int(X(d)) + 3, g.b() - 20, b, kFaint);
        }
        // techo
        double cdb = dbOf(host->ceilingLinear());
        cv.fill({g.x + 6, int(Y(cdb)), g.w - 12, 1}, withAlpha(kAccent2, 140));
        // diagonal de referencia
        cv.line(X(lo), Y(lo), X(hi), Y(hi), 1.0, withAlpha(kFaint, 120));
        std::vector<double> xs, ys;
        for (int i = 0; i <= 160; ++i) {
            double din = lo + (hi - lo) * i / 160.0;
            double out = host->curve(std::pow(10.0, din / 20.0));
            double dout = std::max(lo, std::min(hi, dbOf(out)));
            xs.push_back(X(din));
            ys.push_back(Y(dout));
        }
        cv.polyline(xs, ys, 2.4, kAccent);
        // punto con el nivel actual
        double lvl = dbOf(std::max(dispIn[0], dispIn[1]) * std::pow(10.0, 0.0));
        if (lvl > lo) {
            double out = dbOf(host->curve(std::pow(10.0, lvl / 20.0)));
            cv.circle(X(std::min(hi, lvl)), Y(std::max(lo, std::min(hi, out))), 4.5, kAccent2);
        }
        cv.text(kFontSmall, g.x + 10, g.y + 8, "Curva de recorte", kDim);
    }

    void drawHistory() {
        const Rect& r = hist;
        cv.rounded(r, 6, rgb(14, 18, 25));
        int n = 328;
        for (int i = 0; i < n; ++i) {
            int idx = (histPos + i) % n;
            int x = r.x + i;
            // salida: barra desde abajo (escala dB de -36 a 0)
            double o = std::min(1.0, std::max(0.0, (dbOf(histOut[idx]) + 36.0) / 36.0));
            int oh = int(o * (r.h - 4));
            cv.fill({x, r.b() - 2 - oh, 1, oh}, withAlpha(rgb(120, 150, 190), 110));
            // reducción: desde arriba (0..12 dB)
            double gq = std::min(1.0, histGr[idx] / 12.0);
            int gh = int(gq * (r.h - 4));
            if (gh > 0) cv.fill({x, r.y + 2, 1, gh}, withAlpha(kRed, 200));
        }
        int lw = Canvas::textWidth(kFontSmall, "Salida + reducción");
        cv.rounded({r.r() - lw - 14, r.b() - 18, lw + 10, 15}, 4, withAlpha(rgb(14, 18, 25), 210));
        cv.text(kFontSmall, r.r() - lw - 9, r.b() - 17, "Salida + reducción", kDim);
    }

    // ---- ratón ----
    int dragSlider = -1;
    int dragStartX = 0, dragStartY = 0;
    float dragStartV = 0;

    int sliderAt(int x, int y) {
        for (int i = 0; i < 5; ++i) {
            Rect r = sliders[i].r;
            Rect hit = sliders[i].vertical ? Rect{r.x - 6, r.y - 12, r.w + 12, r.h + 24} : Rect{r.x - 10, r.y - 4, r.w - 50, r.h + 8};
            if (hit.contains(x, y)) return i;
        }
        return -1;
    }

    // devuelve true si hay que repintar
    bool mouseDown(int x, int y, bool doubleClick, bool fine, int screenX, int screenY) {
        int s = sliderAt(x, y);
        if (s >= 0) {
            const Slider& sl = sliders[s];
            if (doubleClick) {
                host->beginEdit(sl.param);
                host->setParam(sl.param, sl.def);
                host->endEdit(sl.param);
                return true;
            }
            dragSlider = s;
            dragStartX = x; dragStartY = y;
            dragStartV = host->getParam(sl.param);
            host->beginEdit(sl.param);
            // clic directo en la pista (no en el mando): salta a esa posición
            if (!fine) {
                float v = posToValue(sl, x, y);
                float cur = dragStartV;
                if (std::fabs(v - cur) > 0.06f) { host->setParam(sl.param, v); dragStartV = v; }
            }
            return true;
        }
        if (btnMode.contains(x, y)) {
            int cur = int(host->getParam(pMode) * 3 + 0.5f);
            int sel = host->popupMenu(screenX, screenY, {"Soft Clip Classic", "Soft Clip Pro", "Hard Clip", "Ratio 2:1"}, cur);
            if (sel >= 0) { host->beginEdit(pMode); host->setParam(pMode, sel / 3.0f); host->endEdit(pMode); }
            return true;
        }
        if (btnOs.contains(x, y)) {
            int cur = int(host->getParam(pOversampling) * 5 + 0.5f);
            int sel = host->popupMenu(screenX, screenY, {"x1 (sin sobremuestreo)", "x2", "x4", "x8", "x16", "x32 (mucha CPU)"}, cur);
            if (sel >= 0) { host->beginEdit(pOversampling); host->setParam(pOversampling, sel / 5.0f); host->endEdit(pOversampling); }
            return true;
        }
        if (tglCeil.contains(x, y)) { toggle(pCeiling); return true; }
        if (tglDelta.contains(x, y)) { toggle(pDelta); return true; }
        if (inReadout.contains(x, y) || outReadout.contains(x, y) || grReadout.contains(x, y)) {
            holdIn = holdOut = holdGr = 0;
            return true;
        }
        return false;
    }

    float posToValue(const Slider& sl, int x, int y) {
        float v = sl.vertical ? float(sl.r.b() - y) / sl.r.h : float(x - sl.r.x) / (sl.r.w - 60);
        return std::min(1.0f, std::max(0.0f, v));
    }

    bool mouseMove(int x, int y, bool fine) {
        if (dragSlider < 0) return false;
        const Slider& sl = sliders[dragSlider];
        float range = sl.vertical ? float(sl.r.h) : float(sl.r.w - 60);
        float d = sl.vertical ? float(dragStartY - y) : float(x - dragStartX);
        float v = dragStartV + d / range * (fine ? 0.1f : 1.0f);
        host->setParam(sl.param, std::min(1.0f, std::max(0.0f, v)));
        return true;
    }

    bool mouseUp() {
        if (dragSlider < 0) return false;
        host->endEdit(sliders[dragSlider].param);
        dragSlider = -1;
        return true;
    }

    bool wheel(int x, int y, int steps, bool fine) {
        int s = sliderAt(x, y);
        if (s < 0) return false;
        int p = sliders[s].param;
        float v = host->getParam(p) + steps * (fine ? 0.002f : 0.01f);
        host->beginEdit(p);
        host->setParam(p, std::min(1.0f, std::max(0.0f, v)));
        host->endEdit(p);
        return true;
    }

    void toggle(int p) {
        host->beginEdit(p);
        host->setParam(p, host->getParam(p) >= 0.5f ? 0.0f : 1.0f);
        host->endEdit(p);
    }
};

}  // namespace gui
