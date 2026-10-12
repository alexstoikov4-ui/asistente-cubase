// AIE Imager — imagen estéreo multibanda (4 bandas) estilo Ozone Imager (VST 2.4, 32 y 64 bits).
//
// Trabaja en Mid/Side. La señal central (Mid) NO se toca nunca, así que la suma mono (L+R) queda igual sea
// cual sea el ajuste: compatible con mono por diseño. En cada banda se cambia solo la parte lateral (Side):
//  - Width 0 % = banda en mono, 100 % = sin cambios, 200 % = el doble de anchura.
//  - Stereoize: crea anchura en bandas que son mono añadiendo al Side una copia retrasada del Mid de esa banda
//    (tampoco cambia la suma mono).
// Las bandas se separan con un árbol de cruces Linkwitz-Riley de 4.º orden con compensación de fase (pasa-todo):
// la suma de las bandas tiene respuesta plana en magnitud y el mismo pequeño desfase en L y R, así que con todo
// al 100 % el sonido no cambia de color ni de imagen, y no hay latencia. Mid y Side pasan por filtros idénticos.
//
// Parámetros: 3 cortes, Width x4, Stereoize x4, Solo x4, Mono (escucha de comprobación).

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

static const int32_t kUniqueId = 0x41494569;  // 'AIEi'
static const int kBands = 4;
enum Param {
    kX1 = 0, kX2, kX3,                       // cortes entre bandas
    kW1, kW2, kW3, kW4,                      // anchura por banda (0..200 %)
    kS1 = kW1 + kBands, kS2, kS3, kS4,       // stereoize por banda (0..100 %)
    kSolo1 = kS1 + kBands, kSolo2, kSolo3, kSolo4,
    kMono = kSolo1 + kBands,
    kNumParams
};
static const double kFreqMin = 20.0, kFreqMax = 20000.0;
inline double freqFromNorm(double v) { return kFreqMin * std::pow(kFreqMax / kFreqMin, v); }
inline double normFromFreq(double f) { return std::log(f / kFreqMin) / std::log(kFreqMax / kFreqMin); }
static const double kStereoizeMs[kBands] = {13.0, 11.0, 9.0, 7.0};  // retardo del Mid que se añade al Side
static const int kDelayLen = 8192;                                  // > 13 ms a 192 kHz... con margen
static const int kScopeLen = 4096;

struct Biquad {
    double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1 = 0, z2 = 0;
    void design(bool highpass, double f, double fs) {
        const double pi = 3.14159265358979323846;
        double w = 2 * pi * f / fs, c = std::cos(w), s = std::sin(w), alpha = s / (2 * 0.70710678118654752);
        double a0 = 1 + alpha;
        if (highpass) { b0 = (1 + c) / 2 / a0; b1 = -(1 + c) / a0; b2 = (1 + c) / 2 / a0; }
        else { b0 = (1 - c) / 2 / a0; b1 = (1 - c) / a0; b2 = (1 - c) / 2 / a0; }
        a1 = -2 * c / a0; a2 = (1 - alpha) / a0;
    }
    inline double run(double x) {
        double y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        return y;
    }
    void clear() { z1 = z2 = 0; }
};
struct LR4 {  // Linkwitz-Riley de 4.º orden = dos Butterworth de 2.º orden en serie
    Biquad a, b;
    void design(bool hp, double f, double fs) { a.design(hp, f, fs); b.design(hp, f, fs); }
    inline double run(double x) { return b.run(a.run(x)); }
    void clear() { a.clear(); b.clear(); }
};
struct AllPass {  // pasa-todo de un cruce LR4: paso bajo + paso alto en paralelo (magnitud plana)
    LR4 lp, hp;
    void design(double f, double fs) { lp.design(false, f, fs); hp.design(true, f, fs); }
    inline double run(double x) { return lp.run(x) + hp.run(x); }
    void clear() { lp.clear(); hp.clear(); }
};

// Árbol de cruces de 4 bandas con compensación de fase:
//   graves+medios-graves = LP(f2) -> pasa-todo(f3) -> LP/HP(f1)
//   medios-agudos+agudos = HP(f2) -> pasa-todo(f1) -> LP/HP(f3)
// Así las dos ramas tienen el mismo desfase y la suma de las 4 bandas es plana.
struct Splitter {
    LR4 lo2, hi2, lo1, hi1, lo3, hi3;
    AllPass ap3, ap1;
    void design(const double* f, double fs) {
        lo2.design(false, f[1], fs); hi2.design(true, f[1], fs);
        lo1.design(false, f[0], fs); hi1.design(true, f[0], fs);
        lo3.design(false, f[2], fs); hi3.design(true, f[2], fs);
        ap3.design(f[2], fs); ap1.design(f[0], fs);
    }
    inline void run(double x, double* out) {
        double low = ap3.run(lo2.run(x)), high = ap1.run(hi2.run(x));
        out[0] = lo1.run(low);
        out[1] = hi1.run(low);
        out[2] = lo3.run(high);
        out[3] = hi3.run(high);
    }
    void clear() { lo2.clear(); hi2.clear(); lo1.clear(); hi1.clear(); lo3.clear(); hi3.clear(); ap3.clear(); ap1.clear(); }
};

struct BandMeter { std::atomic<float> mm, ss, ms; };  // sumas de M², S², M·S del último bloque (salida)

class AIEImager {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    double designed[kBands - 1] = {-1, -1, -1};
    Splitter splitM, splitS;
    std::vector<float> delay[kBands];
    int delayPos = 0;
    // medidores y vectorscopio para la interfaz
    BandMeter meters[kBands];
    std::atomic<uint32_t> meterSeq;
    std::vector<float> scopeL, scopeR;
    std::atomic<uint32_t> scopeWritten;
    void* window = nullptr;

    explicit AIEImager(audioMasterCallback cb) : host(cb), scopeL(kScopeLen, 0.0f), scopeR(kScopeLen, 0.0f) {
        params[kX1] = float(normFromFreq(120));
        params[kX2] = float(normFromFreq(1000));
        params[kX3] = float(normFromFreq(6000));
        for (int b = 0; b < kBands; ++b) {
            params[kW1 + b] = 0.5f;  // 100 %
            params[kS1 + b] = 0.0f;
            params[kSolo1 + b] = 0.0f;
            delay[b].assign(kDelayLen, 0.0f);
            meters[b].mm = 0; meters[b].ss = 0; meters[b].ms = 0;
        }
        params[kMono] = 0.0f;
        meterSeq = 0;
        scopeWritten = 0;
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

    // cortes ordenados y separados al menos 1/3 de octava
    void crossovers(double* f) const {
        for (int i = 0; i < kBands - 1; ++i) f[i] = freqFromNorm(params[kX1 + i]);
        std::sort(f, f + kBands - 1);
        for (int i = 1; i < kBands - 1; ++i) f[i] = std::max(f[i], f[i - 1] * 1.26);
        double nyq = sampleRate * 0.45;
        for (int i = 0; i < kBands - 1; ++i) f[i] = std::min(f[i], nyq);
    }
    void setSampleRate(double fs) {
        sampleRate = fs > 0 ? fs : 44100;
        designed[0] = -1;
        reset();
    }
    void reset() {
        splitM.clear();
        splitS.clear();
        for (auto& d : delay) std::fill(d.begin(), d.end(), 0.0f);
        delayPos = 0;
    }
    double widthOf(int b) const { return 2.0 * params[kW1 + b]; }

    template <typename T>
    void run(T** in, T** out, int32_t n) {
        double f[kBands - 1];
        crossovers(f);
        if (f[0] != designed[0] || f[1] != designed[1] || f[2] != designed[2]) {
            splitM.design(f, sampleRate);
            splitS.design(f, sampleRate);
            for (int i = 0; i < kBands - 1; ++i) designed[i] = f[i];
        }
        double w[kBands], st[kBands];
        int dly[kBands];
        bool anySolo = false, solo[kBands];
        for (int b = 0; b < kBands; ++b) {
            w[b] = widthOf(b);
            st[b] = params[kS1 + b] * 0.7;  // 100 % de stereoize = Side de ~-3 dB respecto al Mid de la banda
            dly[b] = std::min(kDelayLen - 1, int(std::lround(kStereoizeMs[b] * 0.001 * sampleRate)));
            solo[b] = params[kSolo1 + b] >= 0.5f;
            anySolo = anySolo || solo[b];
        }
        bool mono = params[kMono] >= 0.5f;
        double mm[kBands] = {0, 0, 0, 0}, ss[kBands] = {0, 0, 0, 0}, ms[kBands] = {0, 0, 0, 0};
        uint32_t sw = scopeWritten.load(std::memory_order_relaxed);
        for (int32_t i = 0; i < n; ++i) {
            double L = in[0][i], R = in[1][i];
            double M = 0.5 * (L + R), S = 0.5 * (L - R);
            double mb[kBands], sb[kBands];
            splitM.run(M, mb);
            splitS.run(S, sb);
            double mOut = 0, sOut = 0;
            for (int b = 0; b < kBands; ++b) {
                std::vector<float>& d = delay[b];
                d[delayPos] = float(mb[b]);
                double sBand = w[b] * sb[b] + st[b] * d[(delayPos - dly[b] + kDelayLen) % kDelayLen];
                if (!anySolo || solo[b]) {
                    sOut += sBand;
                    mOut += mb[b];  // el Mid pasa por los mismos filtros (mismo desfase que el Side)
                }
                mm[b] += mb[b] * mb[b];
                ss[b] += sBand * sBand;
                ms[b] += mb[b] * sBand;
            }
            delayPos = (delayPos + 1) % kDelayLen;
            if (mono) sOut = 0;
            double oL = mOut + sOut, oR = mOut - sOut;
            out[0][i] = T(oL);
            out[1][i] = T(oR);
            if ((i & 1) == 0) {  // vectorscopio: una de cada dos muestras
                scopeL[sw & (kScopeLen - 1)] = float(oL);
                scopeR[sw & (kScopeLen - 1)] = float(oR);
                ++sw;
            }
        }
        scopeWritten.store(sw, std::memory_order_release);
        for (int b = 0; b < kBands; ++b) {
            meters[b].mm.store(float(mm[b] / std::max(1, n)), std::memory_order_relaxed);
            meters[b].ss.store(float(ss[b] / std::max(1, n)), std::memory_order_relaxed);
            meters[b].ms.store(float(ms[b] / std::max(1, n)), std::memory_order_relaxed);
        }
        meterSeq.fetch_add(1, std::memory_order_release);
    }

    void paramName(int i, char* s) {
        char b[16];
        if (i <= kX3) std::snprintf(b, sizeof b, "Cross %d", (i - kX1 + 1) % 10);
        else if (i < kS1) std::snprintf(b, sizeof b, "Width %d", i - kW1 + 1);
        else if (i < kSolo1) std::snprintf(b, sizeof b, "Stereo %d", i - kS1 + 1);
        else if (i < kMono) std::snprintf(b, sizeof b, "Solo %d", i - kSolo1 + 1);
        else std::snprintf(b, sizeof b, "Mono");
        vst::copyStr(s, b, 9);
    }
    std::string paramText(int i) {
        char b[24];
        if (i <= kX3) {
            double f[kBands - 1];
            crossovers(f);
            double v = f[i - kX1];
            if (v >= 1000) std::snprintf(b, sizeof b, "%.1f kHz", v / 1000);
            else std::snprintf(b, sizeof b, "%.0f Hz", v);
        } else if (i < kS1) std::snprintf(b, sizeof b, "%.0f %%", widthOf(i - kW1) * 100);
        else if (i < kSolo1) std::snprintf(b, sizeof b, "%.0f %%", params[i] * 100.0);
        else std::snprintf(b, sizeof b, "%s", params[i] >= 0.5f ? "On" : "Off");
        return b;
    }
    void setParamFromEditor(int i, float v, bool gestureBegin = true, bool gestureEnd = true) {
        params[i] = v < 0 ? 0 : (v > 1 ? 1 : v);
        if (!host) return;
        if (gestureBegin) host(&fx, vst::audioMasterBeginEdit, i, 0, nullptr, 0.0f);
        host(&fx, vst::audioMasterAutomate, i, 0, nullptr, params[i]);
        if (gestureEnd) host(&fx, vst::audioMasterEndEdit, i, 0, nullptr, 0.0f);
    }
};

// ---------------------------------------------------------------------------------------------
// Interfaz
namespace gui {

static const Color kBandColors[kBands] = {rgb(255, 92, 92), rgb(255, 176, 64), rgb(90, 220, 140), rgb(80, 160, 255)};

class ImagerEditor {
public:
    static const int W = 900, H = 440;
    static const int kFrameMs = 33;
    Canvas cv;
    AIEImager* p;
    int (*menu)(void*, int, int, const std::vector<std::string>&, int) = nullptr;
    void* menuCtx = nullptr;

    Rect area = {12, 96, 616, 264};      // bandas sobre el eje de frecuencia
    Rect scope = {644, 96, 244, 170};    // vectorscopio (semicírculo)
    Rect corr = {644, 290, 244, 22};     // correlación total
    Rect tglMono = {644, 330, 116, 30};
    Rect btnReset = {772, 330, 116, 30};
    // medidas suavizadas por banda
    float bandCorr[kBands] = {1, 1, 1, 1}, bandWidth[kBands] = {0, 0, 0, 0}, totalCorr = 1;
    uint32_t lastSeq = 0;
    std::vector<uint32_t> background;
    // arrastre
    int dragKind = 0, dragIndex = -1, dragY = 0, dragX = 0;  // 1 = corte, 2 = width, 3 = stereoize
    float dragV = 0;

    explicit ImagerEditor(AIEImager* plug) : p(plug) { cv.resize(W, H); }
    bool dragging() const { return dragKind != 0; }

    // eje de frecuencia logarítmico en el área de bandas
    int xOfFreq(double f) const { return area.x + int(normFromFreq(f) * area.w + 0.5); }
    double freqOfX(int x) const { return freqFromNorm(std::min(1.0, std::max(0.0, double(x - area.x) / area.w))); }
    void bandSpan(int b, int& x0, int& x1) const {
        double f[kBands - 1];
        p->crossovers(f);
        x0 = b == 0 ? area.x : xOfFreq(f[b - 1]);
        x1 = b == kBands - 1 ? area.r() : xOfFreq(f[b]);
    }
    Rect widthFader(int b) const {  // pista vertical del Width de la banda
        int x0, x1;
        bandSpan(b, x0, x1);
        int cx = (x0 + x1) / 2;
        return {cx - 9, area.y + 54, 18, 120};
    }
    Rect stereoSlider(int b) const {
        int x0, x1;
        bandSpan(b, x0, x1);
        int cx = (x0 + x1) / 2, w = std::max(30, std::min(110, x1 - x0 - 16));
        return {cx - w / 2, area.y + 214, w, 14};
    }
    Rect soloBtn(int b) const {
        int x0, x1;
        bandSpan(b, x0, x1);
        int cx = (x0 + x1) / 2;
        return {cx - 22, area.y + 236, 44, 20};
    }
    Rect crossHandle(int i) const {
        double f[kBands - 1];
        p->crossovers(f);
        int x = xOfFreq(f[i]);
        return {x - 8, area.y - 30, 16, 26};
    }

    void tick() {
        uint32_t seq = p->meterSeq.load(std::memory_order_acquire);
        if (seq == lastSeq) return;
        lastSeq = seq;
        double tm = 0, ts = 0, tms = 0;
        for (int b = 0; b < kBands; ++b) {
            double mm = p->meters[b].mm.load(), ss = p->meters[b].ss.load(), ms = p->meters[b].ms.load();
            tm += mm; ts += ss; tms += ms;
            // correlación de L y R de la banda: (M²-S²)/(M²+S²)
            double c = (mm + ss) > 1e-12 ? (mm - ss) / (mm + ss) : 1.0;
            double wdt = (mm + ss) > 1e-12 ? std::sqrt(ss / (mm + ss)) : 0.0;  // 0 = mono, ~0,7 = muy ancho
            bandCorr[b] += float((c - bandCorr[b]) * 0.25);
            bandWidth[b] += float((wdt - bandWidth[b]) * 0.25);
        }
        double c = (tm + ts) > 1e-12 ? (tm - ts) / (tm + ts) : 1.0;
        totalCorr += float((c - totalCorr) * 0.2);
    }

    void drawBackground() {
        cv.vgradient({0, 0, W, H}, rgb(20, 22, 30), rgb(11, 12, 17));
        cv.vgradient({0, 0, W, 48}, rgb(34, 37, 48), rgb(23, 25, 33));
        cv.fill({0, 48, W, 1}, rgb(52, 56, 70));
        // logotipo: dos semicírculos
        cv.circle(26, 24, 9, rgb(80, 160, 255));
        cv.circle(34, 24, 9, withAlpha(rgb(255, 92, 92), 200));
        cv.text(kFontBig, 50, 12, "AIE Imager", kText);
        cv.text(kFontSmall, 50 + Canvas::textWidth(kFontBig, "AIE Imager") + 12, 21,
                "AI Audio Engineer  ·  imagen estéreo multibanda", kDim);
        cv.text(kFontSmall, W - 18, 21, "Mono compatible: el centro no se toca", kFaint, 2);
        cv.rounded({area.x - 4, area.y - 36, area.w + 8, area.h + 40}, 8, rgb(26, 29, 39));
        cv.rounded({scope.x - 4, scope.y - 36, scope.w + 8, 280}, 8, rgb(26, 29, 39));
        cv.text(kFontNormal, scope.x + 4, scope.y - 28, "Vectorscopio", kDim);
        cv.text(kFontNormal, corr.x + 4, corr.y - 18, "Correlación", kDim);
        // eje de frecuencias
        const double marks[] = {50, 100, 200, 500, 1000, 2000, 5000, 10000};
        const char* lab[] = {"50", "100", "200", "500", "1k", "2k", "5k", "10k"};
        for (int i = 0; i < 8; ++i) {
            int x = xOfFreq(marks[i]);
            cv.fill({x, area.y, 1, area.h}, rgb(34, 37, 48));
            cv.text(kFontSmall, x, area.b() + 6, lab[i], kFaint, 1);
        }
        cv.text(kFontSmall, area.x, area.b() + 6, "Hz", kFaint);
        // escala del vectorscopio: semicírculo y líneas L / R / M
        double cx = scope.x + scope.w / 2.0, cy = scope.b() - 4, rad = std::min(scope.h - 12.0, scope.w / 2.0 - 10);
        std::vector<double> xs, ys;
        for (int i = 0; i <= 90; ++i) {
            double a = 3.14159265358979 * i / 90;
            xs.push_back(cx + rad * std::cos(a));
            ys.push_back(cy - rad * std::sin(a));
        }
        cv.polyline(xs, ys, 1.2, rgb(52, 56, 70));
        cv.line(cx - rad, cy, cx + rad, cy, 1.0, rgb(52, 56, 70));
        cv.line(cx, cy, cx, cy - rad, 1.0, rgb(52, 56, 70));
        cv.line(cx, cy, cx - rad * 0.707, cy - rad * 0.707, 1.0, rgb(44, 48, 60));
        cv.line(cx, cy, cx + rad * 0.707, cy - rad * 0.707, 1.0, rgb(44, 48, 60));
        cv.text(kFontSmall, int(cx - rad * 0.707) - 10, int(cy - rad * 0.707) - 14, "L", kFaint);
        cv.text(kFontSmall, int(cx + rad * 0.707) + 4, int(cy - rad * 0.707) - 14, "R", kFaint);
        cv.text(kFontSmall, int(cx) - 4, int(cy - rad) - 14, "M", kFaint);
        background = cv.px;
    }

    void drawToggle(const Rect& r, const char* label, bool on, Color c) {
        cv.rounded(r, 6, on ? withAlpha(c, 70) : rgb(40, 44, 56));
        if (on) cv.roundedOutline(r, 6, c);
        cv.circle(r.x + 14, r.y + r.h / 2, 4.5, on ? c : kFaint);
        cv.text(kFontNormal, r.x + 26, r.y + r.h / 2 - 8, label, on ? kText : kDim);
    }

    void draw() {
        if (background.size() != cv.px.size()) drawBackground();
        else std::copy(background.begin(), background.end(), cv.px.begin());
        bool anySolo = false;
        for (int b = 0; b < kBands; ++b) anySolo = anySolo || p->params[kSolo1 + b] >= 0.5f;

        // bandas
        for (int b = 0; b < kBands; ++b) {
            int x0, x1;
            bandSpan(b, x0, x1);
            bool active = !anySolo || p->params[kSolo1 + b] >= 0.5f;
            Color bc = kBandColors[b];
            // anchura medida: bloque translúcido que crece desde el centro de la banda
            int cy = area.y + 114, maxH = 74;
            int hh = int(std::min(1.0, bandWidth[b] / 0.72) * maxH);
            cv.fill({x0 + 2, cy - hh, x1 - x0 - 4, 2 * hh}, withAlpha(bc, active ? 46 : 16));
            cv.fill({x0 + 2, area.b() - 4, x1 - x0 - 4, 4}, withAlpha(bc, active ? 200 : 60));
            char t[32];
            std::snprintf(t, sizeof t, "Banda %d", b + 1);
            cv.text(kFontSmall, (x0 + x1) / 2, area.y + 8, t, active ? kDim : kFaint, 1);
            // Width
            Rect wf = widthFader(b);
            cv.rounded({wf.x + 6, wf.y, 6, wf.h}, 3, rgb(12, 13, 18));
            int yUnity = wf.y + wf.h / 2;
            cv.fill({wf.x - 2, yUnity, wf.w + 4, 1}, rgb(70, 76, 92));  // 100 %
            double v = p->params[kW1 + b];
            int ky = wf.b() - int(v * wf.h + 0.5);
            cv.rounded({wf.x + 6, std::min(ky, yUnity), 6, std::abs(yUnity - ky) + 1}, 3, withAlpha(bc, active ? 220 : 90));
            cv.rounded({wf.x - 4, ky - 7, wf.w + 8, 14}, 4, active ? rgb(220, 226, 238) : rgb(120, 126, 140));
            cv.text(kFontNormal, wf.x + wf.w / 2, wf.b() + 6, p->paramText(kW1 + b), active ? kText : kDim, 1);
            cv.text(kFontSmall, wf.x + wf.w / 2, wf.y - 19, v < 0.01 ? "Mono" : "Width", kFaint, 1);
            // Stereoize
            Rect ss = stereoSlider(b);
            cv.rounded({ss.x, ss.y + 5, ss.w, 4}, 2, rgb(12, 13, 18));
            int sx = ss.x + int(p->params[kS1 + b] * ss.w + 0.5);
            cv.rounded({ss.x, ss.y + 5, sx - ss.x, 4}, 2, withAlpha(bc, 180));
            cv.circle(sx, ss.y + 7, 6, rgb(220, 226, 238));
            if (ss.w >= 60) cv.text(kFontSmall, ss.x, ss.y - 14, "Stereoize", kFaint);
            // Solo
            Rect sb = soloBtn(b);
            bool so = p->params[kSolo1 + b] >= 0.5f;
            cv.rounded(sb, 5, so ? withAlpha(rgb(250, 204, 21), 90) : rgb(40, 44, 56));
            if (so) cv.roundedOutline(sb, 5, rgb(250, 204, 21));
            cv.text(kFontSmall, sb.x + sb.w / 2, sb.y + 4, "Solo", so ? kText : kDim, 1);
            // correlación de la banda: barra de -1 a +1
            Rect cr = {x0 + 8, area.y + 24, std::max(10, x1 - x0 - 16), 6};
            cv.rounded(cr, 3, rgb(12, 13, 18));
            int mid = cr.x + cr.w / 2, cx = cr.x + int((bandCorr[b] + 1) / 2 * cr.w);
            Color cc = bandCorr[b] < 0 ? kRed : (bandCorr[b] < 0.3 ? rgb(250, 204, 21) : kGreen);
            cv.fill({std::min(mid, cx), cr.y, std::abs(cx - mid) + 1, cr.h}, cc);
        }
        // cortes
        double f[kBands - 1];
        p->crossovers(f);
        for (int i = 0; i < kBands - 1; ++i) {
            int x = xOfFreq(f[i]);
            cv.fill({x, area.y, 1, area.h}, rgb(150, 160, 185));
            Rect h = crossHandle(i);
            cv.rounded(h, 4, dragKind == 1 && dragIndex == i ? kAccent : rgb(70, 78, 96));
            cv.fill({x - 2, h.y + 7, 1, 12}, rgb(200, 206, 220));
            cv.fill({x + 1, h.y + 7, 1, 12}, rgb(200, 206, 220));
            std::string t = p->paramText(kX1 + i);
            int tw = Canvas::textWidth(kFontSmall, t);
            int tx = std::min(area.r() - tw / 2 - 2, std::max(area.x + tw / 2 + 2, x));
            cv.text(kFontSmall, tx, area.y - 46, t, kText, 1);
        }

        // vectorscopio: últimos puntos (L,R) girados 45°: mono = línea vertical, ancho = abierto
        double cx = scope.x + scope.w / 2.0, cy = scope.b() - 4, rad = std::min(scope.h - 12.0, scope.w / 2.0 - 10);
        uint32_t end = p->scopeWritten.load(std::memory_order_acquire);
        int np = std::min<uint32_t>(end, 1536);
        float peak = 1e-6f;
        for (int i = 1; i <= np; ++i) {
            uint32_t k = (end - i) & (kScopeLen - 1);
            peak = std::max(peak, std::max(std::fabs(p->scopeL[k]), std::fabs(p->scopeR[k])));
        }
        double sc = rad / std::max(0.25, double(peak) * 1.15);
        for (int i = 1; i <= np; ++i) {
            uint32_t k = (end - i) & (kScopeLen - 1);
            double l = p->scopeL[k], r = p->scopeR[k];
            double sx = (l - r) * 0.7071, my = (l + r) * 0.7071;
            if (my < 0) { my = -my; sx = -sx; }  // se pliega la mitad inferior (como el "polar sample")
            int px = int(cx + sx * sc), py = int(cy - my * sc);
            if (std::hypot(px - cx, cy - py) <= rad) cv.blend(px, py, rgb(120, 200, 255), 0.55 * (1.0 - double(i) / np) + 0.15);
        }
        // correlación total
        cv.rounded(corr, 4, rgb(12, 13, 18));
        int cmid = corr.x + corr.w / 2, cpos = corr.x + int((totalCorr + 1) / 2 * corr.w);
        Color ccol = totalCorr < 0 ? kRed : (totalCorr < 0.3 ? rgb(250, 204, 21) : kGreen);
        cv.fill({std::min(cmid, cpos), corr.y + 3, std::abs(cpos - cmid) + 1, corr.h - 6}, ccol);
        cv.fill({cmid, corr.y, 1, corr.h}, rgb(90, 96, 112));
        cv.text(kFontSmall, corr.x + 4, corr.b() + 4, "-1", kFaint);
        cv.text(kFontSmall, cmid, corr.b() + 4, "0", kFaint, 1);
        cv.text(kFontSmall, corr.r() - 4, corr.b() + 4, "+1", kFaint, 2);
        char ct[16];
        std::snprintf(ct, sizeof ct, "%+.2f", totalCorr);
        cv.text(kFontNormal, corr.r(), corr.y - 18, ct, kText, 2);
        drawToggle(tglMono, "Mono", p->params[kMono] >= 0.5f, kAccent2);
        cv.rounded(btnReset, 6, rgb(40, 44, 56));
        cv.text(kFontNormal, btnReset.x + btnReset.w / 2, btnReset.y + 7, "Todo a 100 %", kDim, 1);
        cv.text(kFontSmall, area.x, H - 22,
                "Arrastra: cortes (arriba), Width (vertical), Stereoize (horizontal). Doble clic = valor por defecto. "
                "Mayúsculas = ajuste fino.", kFaint);
    }

    // ---- ratón ----
    bool mouseDown(int x, int y, bool dbl, bool fine, int, int) {
        (void)fine;
        for (int i = 0; i < kBands - 1; ++i) {
            Rect h = crossHandle(i);
            Rect hit = {h.x - 4, h.y - 4, h.w + 8, h.h + 8};
            if (hit.contains(x, y)) {
                if (dbl) {
                    static const double def[3] = {120, 1000, 6000};
                    p->setParamFromEditor(kX1 + i, float(normFromFreq(def[i])));
                    return true;
                }
                startDrag(1, i, x, y, p->params[kX1 + i]);
                return true;
            }
        }
        for (int b = 0; b < kBands; ++b) {
            Rect wf = widthFader(b);
            Rect hit = {wf.x - 12, wf.y - 10, wf.w + 24, wf.h + 20};
            if (hit.contains(x, y)) {
                if (dbl) { p->setParamFromEditor(kW1 + b, 0.5f); return true; }
                startDrag(2, b, x, y, p->params[kW1 + b]);
                return true;
            }
            Rect ss = stereoSlider(b);
            Rect sh = {ss.x - 8, ss.y - 4, ss.w + 16, ss.h + 8};
            if (sh.contains(x, y)) {
                if (dbl) { p->setParamFromEditor(kS1 + b, 0.0f); return true; }
                float v = std::min(1.0f, std::max(0.0f, float(x - ss.x) / ss.w));
                p->setParamFromEditor(kS1 + b, v, true, false);
                dragKind = 3; dragIndex = b; dragX = x; dragY = y; dragV = v;
                return true;
            }
            if (soloBtn(b).contains(x, y)) {
                p->setParamFromEditor(kSolo1 + b, p->params[kSolo1 + b] >= 0.5f ? 0.0f : 1.0f);
                return true;
            }
        }
        if (tglMono.contains(x, y)) { p->setParamFromEditor(kMono, p->params[kMono] >= 0.5f ? 0.0f : 1.0f); return true; }
        if (btnReset.contains(x, y)) {
            for (int b = 0; b < kBands; ++b) {
                p->setParamFromEditor(kW1 + b, 0.5f);
                p->setParamFromEditor(kS1 + b, 0.0f);
                p->setParamFromEditor(kSolo1 + b, 0.0f);
            }
            return true;
        }
        return false;
    }
    void startDrag(int kind, int idx, int x, int y, float v) {
        dragKind = kind; dragIndex = idx; dragX = x; dragY = y; dragV = v;
        int param = kind == 1 ? kX1 + idx : kW1 + idx;
        if (p->host) p->host(&p->fx, vst::audioMasterBeginEdit, param, 0, nullptr, 0.0f);
    }
    int dragParam() const { return dragKind == 1 ? kX1 + dragIndex : (dragKind == 2 ? kW1 + dragIndex : kS1 + dragIndex); }
    bool mouseMove(int x, int y, bool fine) {
        if (!dragKind) return false;
        float k = fine ? 0.1f : 1.0f, v;
        if (dragKind == 1) v = dragV + float(x - dragX) / area.w * k;
        else if (dragKind == 2) v = dragV + float(dragY - y) / widthFader(dragIndex).h * k;
        else v = dragV + float(x - dragX) / stereoSlider(dragIndex).w * k;
        p->setParamFromEditor(dragParam(), v, false, false);
        return true;
    }
    bool mouseUp() {
        if (!dragKind) return false;
        if (p->host) p->host(&p->fx, vst::audioMasterEndEdit, dragParam(), 0, nullptr, 0.0f);
        dragKind = 0;
        dragIndex = -1;
        return true;
    }
    bool wheel(int x, int y, int steps, bool fine) {
        for (int b = 0; b < kBands; ++b) {
            Rect wf = widthFader(b);
            if (Rect{wf.x - 12, wf.y - 10, wf.w + 24, wf.h + 20}.contains(x, y)) {
                p->setParamFromEditor(kW1 + b, p->params[kW1 + b] + steps * (fine ? 0.005f : 0.025f));
                return true;
            }
        }
        return false;
    }
};

}  // namespace gui

// ---------------------------------------------------------------------------------------------
// Puntos de entrada VST
#if defined(_WIN32)
typedef win::EditorWindow<gui::ImagerEditor> ImagerWindow;
static const char* const kWndClass = "AIEImagerEditorWindow";
static int gWindows = 0;
struct ImagerUi {
    gui::ImagerEditor ed;
    ImagerWindow* win = nullptr;
    explicit ImagerUi(AIEImager* p) : ed(p) {}
};
static void closeUi(AIEImager* p) {
    ImagerUi* ui = static_cast<ImagerUi*>(p->window);
    if (!ui) return;
    ImagerWindow::close(ui->win, kWndClass, gWindows);
    delete ui;
    p->window = nullptr;
}
#endif

static AIEImager* self(AEffect* e) { return static_cast<AIEImager*>(e->object); }

static VstIntPtr VSTCALL dispatcher(AEffect* e, int32_t op, int32_t idx, VstIntPtr val, void* ptr, float opt) {
    AIEImager* p = self(e);
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
        case effGetEffectName: copyStr(ptr, "AIE Imager", 32); return 1;
        case effGetProductString: copyStr(ptr, "AIE Imager", 64); return 1;
        case effGetVendorString: copyStr(ptr, "AI Audio Engineer", 64); return 1;
        case effGetVendorVersion: return 1000;
        case effGetPlugCategory: return kPlugCategEffect;
        case effGetVstVersion: return 2400;
        case effGetTailSize: return int(0.02 * p->sampleRate);
        case effCanDo: return 0;
#if defined(_WIN32)
        case effEditGetRect: {
            static ERect r = {0, 0, int16_t(gui::ImagerEditor::H), int16_t(gui::ImagerEditor::W)};
            if (ptr) *static_cast<ERect**>(ptr) = &r;
            return 1;
        }
        case effEditOpen: {
            ImagerUi* ui = static_cast<ImagerUi*>(p->window);
            if (!ui) { ui = new ImagerUi(p); p->window = ui; }
            return ImagerWindow::open(ui->win, &ui->ed, static_cast<HWND>(ptr), kWndClass, gWindows) ? 1 : 0;
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
    AIEImager* p = new AIEImager(host);
    p->fx.dispatcher = dispatcher;
    p->fx.setParameter = setParameter;
    p->fx.getParameter = getParameter;
    p->fx.processReplacing = processReplacing;
    p->fx.processDoubleReplacing = processDouble;
    p->fx.process = processAccumulating;
    return &p->fx;
}
