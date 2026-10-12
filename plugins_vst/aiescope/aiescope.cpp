// AIE Scope — forma de onda en color por bandas, al estilo de los programas de DJ (VST 2.4, 32 y 64 bits).
//
// No modifica el sonido (la salida es la entrada, sin latencia). Divide la señal en tres bandas con
// filtros Linkwitz-Riley de 4.º orden: graves (< 200 Hz, rojo), medios (200 Hz - 2,5 kHz, verde) y agudos
// (> 2,5 kHz, azul). Cada ~3 ms guarda el pico de la señal y de cada banda en un búfer circular que lee la
// interfaz: la altura de la onda es el pico y el color, la mezcla de las tres bandas.
//
// Parámetros: Zoom (2/4/8/16 s), Gain (escala vertical), Modo (Color / 3 bandas), Freeze (congelar).

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "../common/vst2.h"
#include "../common/gui_canvas.h"
#if defined(_WIN32)
#include "../common/vst_window_win32.h"
#endif

static const int32_t kUniqueId = 0x41494573;  // 'AIEs'
enum Param { kZoom = 0, kGain, kMode, kFreeze, kNumParams };
static const int kNumZoom = 4;
static const double kZoomSeconds[kNumZoom] = {2, 4, 8, 16};
static const double kLowCut = 200.0, kHighCut = 2500.0;
static const double kColumnSeconds = 0.0029;  // resolución temporal guardada (~3 ms)
static const int kRing = 1 << 15;             // columnas guardadas (~95 s)
static const double kOverviewSeconds = 64.0;  // franja de resumen inferior

// Biquad Butterworth (Q = 0,7071); dos en cascada = Linkwitz-Riley de 4.º orden.
struct Biquad {
    double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1 = 0, z2 = 0;
    void design(bool highpass, double f, double fs) {
        const double pi = 3.14159265358979323846;
        double w = 2 * pi * f / fs, c = std::cos(w), s = std::sin(w), alpha = s / (2 * 0.70710678118654752);
        double a0 = 1 + alpha;
        if (highpass) { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = (1 + c) / 2; }
        else { b0 = (1 - c) / 2; b1 = 1 - c; b2 = (1 - c) / 2; }
        b0 /= a0; b1 /= a0; b2 /= a0;
        a1 = -2 * c / a0; a2 = (1 - alpha) / a0;
    }
    inline double run(double x) {  // forma transpuesta directa II
        double y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        return y;
    }
    void clear() { z1 = z2 = 0; }
};
struct LR4 {
    Biquad a, b;
    void design(bool hp, double f, double fs) { a.design(hp, f, fs); b.design(hp, f, fs); }
    inline double run(double x) { return b.run(a.run(x)); }
    void clear() { a.clear(); b.clear(); }
};

struct Column { float peak, rms, low, mid, high; };

class AIEScope {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    int colLen = 128;  // muestras por columna (según la frecuencia de muestreo)
    // análisis
    LR4 lowLp, midHp, midLp, highHp;
    int colCount = 0;
    float accPeak = 0, accLow = 0, accMid = 0, accHigh = 0;
    double accSq = 0;
    // envolventes: suben al instante y bajan en ~12 ms -> contorno suave y estable (sin "peine")
    double envPeak = 0, envLow = 0, envMid = 0, envHigh = 0, envSq = 0, rel = 0.998, rmsCoef = 0.002;
    // búfer circular: el audio escribe y avanza 'written'; la interfaz lee hasta 'written'
    std::vector<Column> ring;
    std::atomic<uint32_t> written;
    void* window = nullptr;

    explicit AIEScope(audioMasterCallback cb) : host(cb), ring(kRing, Column{0, 0, 0, 0, 0}) {
        written = 0;
        params[kZoom] = 1.0f / (kNumZoom - 1);  // 4 s
        params[kGain] = 0.0f;                   // 0 dB
        params[kMode] = 0.0f;                   // Color
        params[kFreeze] = 0.0f;
        fx.magic = vst::kEffectMagic;
        fx.numPrograms = 1;
        fx.numParams = kNumParams;
        fx.numInputs = 2;
        fx.numOutputs = 2;
        fx.flags = vst::effFlagsCanReplacing | vst::effFlagsCanDoubleReplacing;
#if defined(_WIN32)
        fx.flags |= vst::effFlagsHasEditor;
#endif
        fx.object = this;
        fx.uniqueID = kUniqueId;
        fx.version = 1000;
        fx.ioRatio = 1.0f;
        fx.initialDelay = 0;
        setSampleRate(44100);
    }

    void setSampleRate(double fs) {
        sampleRate = fs > 0 ? fs : 44100;
        colLen = std::max(16, int(std::lround(sampleRate * kColumnSeconds)));
        rel = std::exp(-1.0 / (0.012 * sampleRate));
        rmsCoef = 1.0 - std::exp(-1.0 / (0.010 * sampleRate));
        lowLp.design(false, kLowCut, sampleRate);
        midHp.design(true, kLowCut, sampleRate);
        midLp.design(false, kHighCut, sampleRate);
        highHp.design(true, kHighCut, sampleRate);
        reset();
    }
    void reset() {
        lowLp.clear(); midHp.clear(); midLp.clear(); highHp.clear();
        colCount = 0;
        accPeak = accLow = accMid = accHigh = 0;
        accSq = 0;
        envPeak = envLow = envMid = envHigh = envSq = 0;
    }
    double columnsPerSecond() const { return sampleRate / colLen; }
    int zoomIdx() const { int z = int(params[kZoom] * (kNumZoom - 1) + 0.5f); return std::min(kNumZoom - 1, std::max(0, z)); }
    double gainDb() const { return 24.0 * params[kGain]; }

    template <typename T>
    void run(T** in, T** out, int32_t n) {
        for (int32_t i = 0; i < n; ++i) {
            double l = in[0][i], r = in[1][i];
            out[0][i] = T(l);  // sin cambios (sirve también si entrada y salida son el mismo búfer)
            out[1][i] = T(r);
            double x = 0.5 * (l + r);
            double lo = lowLp.run(x), mi = midLp.run(midHp.run(x)), hi = highHp.run(x);
            envPeak = std::max(std::max(std::fabs(l), std::fabs(r)), envPeak * rel);
            // bandas: nivel medio (RMS ~10 ms), como los programas de DJ; con el pico, un master
            // comprimido llena toda la altura y se pierde la forma
            envLow += rmsCoef * (lo * lo - envLow);
            envMid += rmsCoef * (mi * mi - envMid);
            envHigh += rmsCoef * (hi * hi - envHigh);
            envSq += rmsCoef * (x * x - envSq);
            accPeak = std::max(accPeak, float(envPeak));
            accSq += envSq;
            accLow = std::max(accLow, float(std::sqrt(envLow)));
            accMid = std::max(accMid, float(std::sqrt(envMid)));
            accHigh = std::max(accHigh, float(std::sqrt(envHigh)));
            if (++colCount >= colLen) {
                uint32_t w = written.load(std::memory_order_relaxed);
                ring[w & (kRing - 1)] = Column{accPeak, float(std::sqrt(accSq / colLen)), accLow, accMid, accHigh};
                written.store(w + 1, std::memory_order_release);
                colCount = 0;
                accPeak = accLow = accMid = accHigh = 0;
                accSq = 0;
            }
        }
    }

    void paramName(int i, char* s) {
        static const char* n[kNumParams] = {"Zoom", "Gain", "Mode", "Freeze"};
        vst::copyStr(s, i < kNumParams ? n[i] : "", 9);
    }
    std::string paramText(int i) {
        char b[32];
        switch (i) {
            case kZoom: std::snprintf(b, sizeof b, "%.0f s", kZoomSeconds[zoomIdx()]); break;
            case kGain: std::snprintf(b, sizeof b, "+%.1f dB", gainDb()); break;
            case kMode: std::snprintf(b, sizeof b, "%s", params[kMode] >= 0.5f ? "3 bandas" : "Color"); break;
            case kFreeze: std::snprintf(b, sizeof b, "%s", params[kFreeze] >= 0.5f ? "On" : "Off"); break;
            default: b[0] = 0;
        }
        return b;
    }
    void setParamFromEditor(int i, float v) {
        params[i] = v < 0 ? 0 : (v > 1 ? 1 : v);
        if (host) {
            host(&fx, vst::audioMasterBeginEdit, i, 0, nullptr, 0.0f);
            host(&fx, vst::audioMasterAutomate, i, 0, nullptr, params[i]);
            host(&fx, vst::audioMasterEndEdit, i, 0, nullptr, 0.0f);
        }
    }
};

// ---------------------------------------------------------------------------------------------
// Interfaz
namespace gui {

static const Color kLowColor = rgb(255, 40, 60), kMidColor = rgb(60, 235, 90), kHighColor = rgb(50, 120, 255);

class ScopeEditor {
public:
    static const int W = 900, H = 372;
    static const int kFrameMs = 20;  // 50 imágenes por segundo: desplazamiento fluido
    Canvas cv;
    AIEScope* p;
    int (*menu)(void*, int, int, const std::vector<std::string>&, int) = nullptr;
    void* menuCtx = nullptr;

    Rect wave = {12, 100, 800, 184}, meters = {824, 100, 64, 184}, overview = {12, 300, 876, 56};
    Rect btnZoom[kNumZoom], btnMode[2], gainTrack = {560, 62, 140, 24}, tglFreeze = {796, 60, 92, 28};
    double frozenAt = 0;
    // desplazamiento suave: Cubase entrega el audio a trozos (según el búfer de la tarjeta); la vista avanza
    // con el reloj a velocidad constante, un poco por detrás de lo recibido, y se corrige con suavidad
    double smoothHead = 0, lastNow = -1, burst = 0;
    uint32_t lastWritten = 0;
    // escala automática: el nivel medio más alto de los últimos segundos ocupa ~85 % de la altura
    double autoRef = 0, autoRefPk = 0;
    float meterLow = 0, meterMid = 0, meterHigh = 0;
    std::vector<uint32_t> background;
    bool dragGain = false;
    int dragX = 0;
    float dragV = 0;

    explicit ScopeEditor(AIEScope* plug) : p(plug) {
        cv.resize(W, H);
        for (int i = 0; i < kNumZoom; ++i) btnZoom[i] = {78 + i * 52, 60, 48, 28};
        btnMode[0] = {340, 60, 70, 28};
        btnMode[1] = {414, 60, 82, 28};
    }

    bool dragging() const { return dragGain; }

    // Color de una columna como en los programas de DJ: mezcla aditiva natural de rojo (graves), verde
    // (medios) y azul (agudos) según cuánto suena cada banda. Cuando suena todo junto sale una mezcla
    // (rosa, morado, naranja...), nunca blanco: se conserva la saturación del color.
    static Color bandColor(float lo, float mi, float hi) {
        double r = lo * 1.0, g = mi * 1.15, b = hi * 2.4;  // compensa que los graves tienen más energía
        double m = std::max(r, std::max(g, b));
        if (m <= 1e-9) return rgb(90, 90, 110);
        r /= m; g /= m; b /= m;
        double mn = std::min(r, std::min(g, b));
        // quita gran parte del componente común (lo que haría gris/blanco) y vuelve a normalizar
        r -= 0.85 * mn; g -= 0.85 * mn; b -= 0.85 * mn;
        double mx = std::max(r, std::max(g, b));
        if (mx <= 1e-6) return rgb(230, 80, 200);  // las tres bandas exactamente iguales: rosa-morado
        r /= mx; g /= mx; b /= mx;
        return rgb(int(20 + 235 * r), int(20 + 235 * g), int(30 + 225 * b));
    }

    // posición de la vista en columnas (con decimales, para que el paso en píxeles sea uniforme)
    double headF() const {
        if (p->params[kFreeze] >= 0.5f) return frozenAt;
        return lastNow < 0 ? double(p->written.load(std::memory_order_acquire)) : std::max(0.0, smoothHead);
    }
    uint32_t head() const { return uint32_t(headF()); }

    static double nowSeconds() {
        return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    void tick() { tick(nowSeconds()); }

    void tick(double now) {
        uint32_t w = p->written.load(std::memory_order_acquire);
        double cps = p->columnsPerSecond();
        if (lastNow < 0 || now - lastNow > 1.0) {  // primera vez o tras una pausa larga
            smoothHead = w;
            burst = 0;
        } else {
            double dt = now - lastNow;
            burst = std::max(burst * 0.995, double(w - lastWritten));  // tamaño de los trozos que llegan
            double target = double(w) - burst - cps * 0.01;
            smoothHead += dt * cps;
            double err = target - smoothHead;
            if (std::fabs(err) > cps * 0.5) smoothHead = target;  // salto grande (parada, cambio de posición)
            else smoothHead += err * 0.015;  // corrección lenta: los trozos de audio no producen tirones
            if (smoothHead > w) smoothHead = w;
        }
        // escala automática con el nivel medio de las columnas nuevas
        float newMax = 0, newPk = 0;
        uint32_t nNew = std::min<uint32_t>(w - lastWritten, kRing - 1);
        for (uint32_t i = 1; i <= nNew; ++i) {
            const Column& c = p->ring[(w - i) & (kRing - 1)];
            newMax = std::max(newMax, c.rms);
            newPk = std::max(newPk, c.peak);
        }
        double decay = std::pow(0.5, (lastNow < 0 ? 0.0 : now - lastNow) / 6.0);  // se reduce a la mitad en 6 s
        autoRef = std::max(double(newMax), std::max(autoRef * decay, 1e-4));
        autoRefPk = std::max(double(newPk), std::max(autoRefPk * decay, 1e-4));
        lastWritten = w;
        lastNow = now;
        if (p->params[kFreeze] < 0.5f) frozenAt = headF();
        // medidores de banda: máximo de los últimos ~100 ms con caída suave
        float lo = 0, mi = 0, hi = 0;
        int n = std::min<uint32_t>(w, uint32_t(p->columnsPerSecond() * 0.1));
        for (int i = 1; i <= n; ++i) {
            const Column& c = p->ring[(w - i) & (kRing - 1)];
            lo = std::max(lo, c.low); mi = std::max(mi, c.mid); hi = std::max(hi, c.high);
        }
        meterLow = std::max(lo, meterLow * 0.85f);
        meterMid = std::max(mi, meterMid * 0.85f);
        meterHigh = std::max(hi, meterHigh * 0.85f);
    }

    void drawBackground() {
        cv.vgradient({0, 0, W, H}, rgb(18, 20, 26), rgb(10, 11, 15));
        cv.vgradient({0, 0, W, 48}, rgb(32, 35, 44), rgb(22, 24, 31));
        cv.fill({0, 48, W, 1}, rgb(50, 54, 66));
        // logotipo: tres barras de color
        cv.rounded({18, 14, 6, 20}, 2, kLowColor);
        cv.rounded({27, 18, 6, 16}, 2, kMidColor);
        cv.rounded({36, 22, 6, 12}, 2, kHighColor);
        cv.text(kFontBig, 50, 12, "AIE Scope", kText);
        cv.text(kFontSmall, 50 + Canvas::textWidth(kFontBig, "AIE Scope") + 12, 21, "AI Audio Engineer  ·  onda por bandas", kDim);
        int lx = 560;
        const char* names[3] = {"Graves < 200 Hz", "Medios", "Agudos > 2,5 kHz"};
        Color cols[3] = {kLowColor, kMidColor, kHighColor};
        for (int i = 0; i < 3; ++i) {
            cv.rounded({lx, 19, 10, 10}, 3, cols[i]);
            cv.text(kFontSmall, lx + 15, 18, names[i], kDim);
            lx += 15 + Canvas::textWidth(kFontSmall, names[i]) + 16;
        }
        cv.text(kFontNormal, 18, 66, "Zoom", kDim);
        cv.text(kFontNormal, 290, 66, "Vista", kDim);
        cv.text(kFontNormal, 516, 66, "Gain", kDim);
        cv.rounded({wave.x - 2, wave.y - 2, wave.w + 4, wave.h + 4}, 6, rgb(6, 7, 10));
        cv.rounded({overview.x - 2, overview.y - 2, overview.w + 4, overview.h + 4}, 6, rgb(6, 7, 10));
        cv.rounded({meters.x - 2, meters.y - 2, meters.w + 4, meters.h + 4}, 6, rgb(6, 7, 10));
        background = cv.px;
    }

    // Columna sólida simétrica alrededor del centro (sin transparencias: bordes nítidos como en los
    // programas de DJ). 'edge' oscurece un poco hacia el borde para dar volumen.
    void column(const Rect& r, int x, double amp, Color c, double edge = 0.35) {
        int half = r.h / 2, cy = r.y + half;
        int hh = int(std::min(1.0, amp) * (half - 1) + 0.5);
        if (hh <= 0) return;
        uint32_t* px = cv.px.data();
        for (int dy = -hh; dy <= hh; ++dy) {
            int y = cy + dy;
            if (y < r.y || y >= r.b()) continue;
            double t = double(std::abs(dy)) / hh;
            Color cc = mix(c, rgb(0, 0, 0), edge * t * t);
            px[size_t(y) * cv.w + x] = cc & 0x00FFFFFF;
        }
    }

    // Línea vertical con bordes suaves (brillo difuminado) y centro más claro, como en Traktor.
    void glowColumn(const Rect& r, int x, double amp, Color c, double bright) {
        int half = r.h / 2, cy = r.y + half;
        int hh = int(std::min(1.0, amp) * (half - 1) + 0.5);
        if (hh <= 0) return;
        Color base = mix(rgb(0, 0, 0), c, std::min(1.0, bright));
        for (int dy = -hh; dy <= hh; ++dy) {
            int y = cy + dy;
            if (y < r.y || y >= r.b()) continue;
            double t = double(std::abs(dy)) / hh;
            Color cc = mix(base, rgb(255, 255, 255), 0.06 * (1 - t) * (1 - t) * bright);  // apenas más claro
            double a = t < 0.72 ? 1.0 : std::max(0.0, (1.0 - t) / 0.28);  // borde difuminado
            cv.blend(x, y, cc, a * a * (3 - 2 * a));
        }
    }

    // Une las columnas de audio en barras de ancho fijo ANCLADAS AL TIEMPO ABSOLUTO: la barra k reúne
    // siempre las mismas columnas, así que al avanzar la onda solo se desplaza (no "tiembla").
    struct Bin { float pk, rms, lo, mi, hi; bool valid; };
    Bin binAt(uint32_t end, double perPx, long k) {
        Bin b = {0, 0, 0, 0, 0, false};
        if (k < 0) return b;
        uint32_t from = uint32_t(std::ceil(k * perPx)), to = uint32_t(std::ceil((k + 1) * perPx));
        if (to <= from) to = from + 1;
        uint32_t oldest = end > kRing - 1 ? end - (kRing - 1) : 0;
        if (from < oldest) return b;
        to = std::min(to, end);
        double sq = 0;
        for (uint32_t c = from; c < to; ++c) {
            const Column& col = p->ring[c & (kRing - 1)];
            b.pk = std::max(b.pk, col.peak); b.lo = std::max(b.lo, col.low);
            b.mi = std::max(b.mi, col.mid); b.hi = std::max(b.hi, col.high);
            sq += double(col.rms) * col.rms;
            b.valid = true;
        }
        if (b.valid) b.rms = float(std::sqrt(sq / (to - from)));
        return b;
    }

    void drawWave(const Rect& r, double seconds, bool threeBands, double gain, double gainPk) {
        double hf = headF();
        uint32_t end = uint32_t(hf);
        if (end == 0) return;
        double perPx = seconds * p->columnsPerSecond() / r.w;
        long last = long(std::floor((hf - 1) / perPx));  // barra de la posición actual
        for (int x = 0; x < r.w; ++x) {
            Bin b = binAt(end, perPx, last - (r.w - 1 - x));
            if (!b.valid) continue;
            int X = r.x + x;
            if (!threeBands) {
                // altura según el pico (escala automática); el brillo de cada línea según la relación
                // nivel medio / pico: da la textura de líneas finas de los programas de DJ
                Color c = bandColor(b.lo, b.mi, b.hi);
                double crest = b.pk > 1e-9 ? std::min(1.0, b.rms * 1.6 / b.pk) : 0.0;
                glowColumn(r, X, b.pk * gainPk, c, 0.55 + 0.45 * crest);
            } else {  // tres ondas superpuestas: graves al fondo, agudos delante
                column(r, X, b.lo * gain, kLowColor, 0.35);
                column(r, X, b.mi * gain * 1.4, kMidColor, 0.35);
                column(r, X, b.hi * gain * 2.2, kHighColor, 0.25);
            }
        }
    }

    void draw() {
        if (background.size() != cv.px.size()) drawBackground();
        else std::copy(background.begin(), background.end(), cv.px.begin());
        // escala: automática (nivel medio máximo reciente al 85 %) más el Gain del usuario
        double user = std::pow(10.0, p->gainDb() / 20.0);
        double gain = 0.85 / autoRef * user, gainPk = 0.95 / autoRefPk * user;
        bool three = p->params[kMode] >= 0.5f;
        double secs = kZoomSeconds[p->zoomIdx()];

        // controles
        for (int i = 0; i < kNumZoom; ++i) {
            bool on = p->zoomIdx() == i;
            cv.rounded(btnZoom[i], 6, on ? rgb(64, 196, 255) : rgb(38, 42, 54));
            char b[8];
            std::snprintf(b, sizeof b, "%.0f s", kZoomSeconds[i]);
            cv.text(kFontNormal, btnZoom[i].x + btnZoom[i].w / 2, btnZoom[i].y + 6, b, on ? rgb(10, 14, 20) : kText, 1);
        }
        const char* modes[2] = {"Color", "3 bandas"};
        for (int i = 0; i < 2; ++i) {
            bool on = (i == 1) == three;
            cv.rounded(btnMode[i], 6, on ? rgb(64, 196, 255) : rgb(38, 42, 54));
            cv.text(kFontNormal, btnMode[i].x + btnMode[i].w / 2, btnMode[i].y + 6, modes[i], on ? rgb(10, 14, 20) : kText, 1);
        }
        Rect tr = {gainTrack.x, gainTrack.y + gainTrack.h / 2 - 3, gainTrack.w, 6};
        cv.rounded(tr, 3, rgb(6, 7, 10));
        int kx = tr.x + int(p->params[kGain] * tr.w + 0.5);
        cv.rounded({tr.x, tr.y, kx - tr.x, tr.h}, 3, withAlpha(kAccent, 160));
        cv.circle(kx, tr.y + 3, 8, rgb(214, 222, 236));
        cv.text(kFontNormal, gainTrack.r() + 14, 66, p->paramText(kGain), kText);
        bool fr = p->params[kFreeze] >= 0.5f;
        cv.rounded(tglFreeze, 6, fr ? withAlpha(kAccent2, 70) : rgb(38, 42, 54));
        if (fr) cv.roundedOutline(tglFreeze, 6, kAccent2);
        cv.circle(tglFreeze.x + 15, tglFreeze.y + 14, 4.5, fr ? kAccent2 : kFaint);
        cv.text(kFontNormal, tglFreeze.x + 28, tglFreeze.y + 6, "Freeze", fr ? kText : kDim);

        // rejilla de segundos y línea central
        cv.fill({wave.x, wave.y, wave.w, wave.h}, rgb(8, 9, 13));
        double step = secs <= 4 ? 0.5 : (secs <= 8 ? 1 : 2);
        for (double t = step; t < secs; t += step) {
            int x = wave.r() - int(t / secs * wave.w);
            cv.fill({x, wave.y, 1, wave.h}, rgb(30, 33, 42));
            char b[12];
            std::snprintf(b, sizeof b, step < 1 ? "-%.1f s" : "-%.0f s", t);
            cv.text(kFontSmall, x + 3, wave.y + 3, b, rgb(80, 86, 104));
        }
        cv.fill({wave.x, wave.y + wave.h / 2, wave.w, 1}, rgb(36, 40, 52));
        drawWave(wave, secs, three, gain, gainPk);
        cv.fill({wave.r() - 2, wave.y, 2, wave.h}, fr ? kAccent2 : rgb(255, 60, 40));  // "ahora" (rojo, como Traktor)

        // resumen inferior (últimos 64 s)
        cv.fill(overview, rgb(8, 9, 13));
        drawWave(overview, kOverviewSeconds, false, gain, gainPk);
        int vx = overview.r() - int(secs / kOverviewSeconds * overview.w);
        cv.fill({vx, overview.y, overview.r() - vx, overview.h}, withAlpha(rgb(255, 255, 255), 22));
        cv.fill({vx, overview.y, 1, overview.h}, withAlpha(rgb(255, 255, 255), 120));
        cv.text(kFontSmall, overview.x + 6, overview.y + 3, "últimos 64 s", rgb(90, 96, 114));

        // medidores de banda
        cv.fill(meters, rgb(8, 9, 13));
        float vals[3] = {meterLow, meterMid * 1.8f, meterHigh * 3.2f};
        Color cols[3] = {kLowColor, kMidColor, kHighColor};
        const char* lab[3] = {"G", "M", "A"};
        for (int i = 0; i < 3; ++i) {
            Rect m = {meters.x + 6 + i * 20, meters.y + 8, 14, meters.h - 30};
            double db = dbOf(vals[i] * gain);
            double f = std::min(1.0, std::max(0.0, (db + 48.0) / 48.0));
            int hgt = int(f * m.h);
            cv.rounded(m, 3, rgb(20, 22, 30));
            cv.vgradient({m.x, m.b() - hgt, m.w, hgt}, mix(cols[i], rgb(255, 255, 255), 0.25), mix(cols[i], rgb(0, 0, 0), 0.35));
            cv.text(kFontSmall, m.x + m.w / 2, meters.b() - 18, lab[i], kDim, 1);
        }
    }

    // ---- ratón ----
    bool mouseDown(int x, int y, bool dbl, bool fine, int sx, int sy) {
        (void)fine; (void)sx; (void)sy;
        for (int i = 0; i < kNumZoom; ++i)
            if (btnZoom[i].contains(x, y)) { p->setParamFromEditor(kZoom, float(i) / (kNumZoom - 1)); return true; }
        for (int i = 0; i < 2; ++i)
            if (btnMode[i].contains(x, y)) { p->setParamFromEditor(kMode, float(i)); return true; }
        if (tglFreeze.contains(x, y)) { toggleFreeze(); return true; }
        Rect hit = {gainTrack.x - 10, gainTrack.y, gainTrack.w + 20, gainTrack.h};
        if (hit.contains(x, y)) {
            if (dbl) { p->setParamFromEditor(kGain, 0.0f); return true; }
            dragGain = true;
            dragX = x;
            float v = std::min(1.0f, std::max(0.0f, float(x - gainTrack.x) / gainTrack.w));
            p->setParamFromEditor(kGain, v);
            dragV = v;
            return true;
        }
        if (wave.contains(x, y) || overview.contains(x, y)) { toggleFreeze(); return true; }  // clic en la onda
        return false;
    }
    void toggleFreeze() {
        bool on = p->params[kFreeze] < 0.5f;
        if (on) frozenAt = headF();  // se congela tal como se ve en ese instante
        p->setParamFromEditor(kFreeze, on ? 1.0f : 0.0f);
    }
    bool mouseMove(int x, int, bool fine) {
        if (!dragGain) return false;
        float v = dragV + float(x - dragX) / gainTrack.w * (fine ? 0.1f : 1.0f);
        p->setParamFromEditor(kGain, v);
        return true;
    }
    bool mouseUp() { bool was = dragGain; dragGain = false; return was; }
    bool wheel(int x, int y, int steps, bool fine) {
        if (wave.contains(x, y) || overview.contains(x, y)) {  // rueda sobre la onda: zoom
            int z = std::min(kNumZoom - 1, std::max(0, p->zoomIdx() - steps));
            p->setParamFromEditor(kZoom, float(z) / (kNumZoom - 1));
            return true;
        }
        Rect hit = {gainTrack.x - 10, gainTrack.y, gainTrack.w + 20, gainTrack.h};
        if (hit.contains(x, y)) {
            p->setParamFromEditor(kGain, p->params[kGain] + steps * (fine ? 0.005f : 0.03f));
            return true;
        }
        return false;
    }
};

}  // namespace gui

// ---------------------------------------------------------------------------------------------
// Puntos de entrada VST
#if defined(_WIN32)
typedef win::EditorWindow<gui::ScopeEditor> ScopeWindow;
static const char* const kWndClass = "AIEScopeEditorWindow";
static int gWindows = 0;
struct ScopeUi {
    gui::ScopeEditor ed;
    ScopeWindow* win = nullptr;
    explicit ScopeUi(AIEScope* p) : ed(p) {}
};
static void closeUi(AIEScope* p) {
    ScopeUi* ui = static_cast<ScopeUi*>(p->window);
    if (!ui) return;
    ScopeWindow::close(ui->win, kWndClass, gWindows);
    delete ui;
    p->window = nullptr;
}
#endif

static AIEScope* self(AEffect* e) { return static_cast<AIEScope*>(e->object); }

static VstIntPtr VSTCALL dispatcher(AEffect* e, int32_t op, int32_t idx, VstIntPtr val, void* ptr, float opt) {
    AIEScope* p = self(e);
    using namespace vst;
    switch (op) {
        case effOpen: return 0;
        case effClose:
#if defined(_WIN32)
            closeUi(p);
#endif
            delete p;
            return 0;
        case effSetSampleRate: p->setSampleRate(opt); return 0;
        case effSetBlockSize: return 0;
        case effMainsChanged: if (val) p->reset(); return 0;
        case effGetProgram: case effSetProgram: return 0;
        case effGetProgramName: case effGetProgramNameIndexed: copyStr(ptr, "Default", 25); return 1;
        case effGetParamName: p->paramName(idx, static_cast<char*>(ptr)); return 0;
        case effGetParamLabel: copyStr(ptr, "", 9); return 0;
        case effGetParamDisplay: copyStr(ptr, idx < kNumParams ? p->paramText(idx).c_str() : "", 9); return 0;
        case effGetEffectName: copyStr(ptr, "AIE Scope", 32); return 1;
        case effGetProductString: copyStr(ptr, "AIE Scope", 64); return 1;
        case effGetVendorString: copyStr(ptr, "AI Audio Engineer", 64); return 1;
        case effGetVendorVersion: return 1000;
        case effGetPlugCategory: return kPlugCategAnalysis;
        case effGetVstVersion: return 2400;
        case effGetTailSize: return 1;
        case effCanDo: return 0;
#if defined(_WIN32)
        case effEditGetRect: {
            static ERect r = {0, 0, int16_t(gui::ScopeEditor::H), int16_t(gui::ScopeEditor::W)};
            if (ptr) *static_cast<ERect**>(ptr) = &r;
            return 1;
        }
        case effEditOpen: {
            ScopeUi* ui = static_cast<ScopeUi*>(p->window);
            if (!ui) { ui = new ScopeUi(p); p->window = ui; }
            return ScopeWindow::open(ui->win, &ui->ed, static_cast<HWND>(ptr), kWndClass, gWindows) ? 1 : 0;
        }
        case effEditClose: closeUi(p); return 0;
        case effEditIdle: return 0;
#endif
        default: return 0;
    }
}

static void VSTCALL setParameter(AEffect* e, int32_t i, float v) {
    if (i >= 0 && i < kNumParams) self(e)->params[i] = v < 0 ? 0 : (v > 1 ? 1 : v);
}
static float VSTCALL getParameter(AEffect* e, int32_t i) { return (i >= 0 && i < kNumParams) ? self(e)->params[i] : 0.0f; }
static void VSTCALL processReplacing(AEffect* e, float** in, float** out, int32_t n) { self(e)->run(in, out, n); }
static void VSTCALL processDouble(AEffect* e, double** in, double** out, int32_t n) { self(e)->run(in, out, n); }
static void VSTCALL processAccumulating(AEffect* e, float** in, float** out, int32_t n) {
    float a[256], b[256];  // por bloques: sin reservar memoria en el hilo de audio
    for (int32_t s = 0; s < n; s += 256) {
        int32_t len = n - s < 256 ? n - s : 256;
        float* src[2] = {in[0] + s, in[1] + s};
        float* tmp[2] = {a, b};
        self(e)->run(src, tmp, len);
        for (int32_t i = 0; i < len; ++i) { out[0][s + i] += a[i]; out[1][s + i] += b[i]; }
    }
}

VSTEXPORT AEffect* VSTPluginMain(audioMasterCallback host) {
    if (host && host(nullptr, vst::audioMasterVersion, 0, 0, nullptr, 0.0f) == 0) return nullptr;
    AIEScope* p = new AIEScope(host);
    p->fx.dispatcher = dispatcher;
    p->fx.setParameter = setParameter;
    p->fx.getParameter = getParameter;
    p->fx.processReplacing = processReplacing;
    p->fx.processDoubleReplacing = processDouble;
    p->fx.process = processAccumulating;
    return &p->fx;
}
