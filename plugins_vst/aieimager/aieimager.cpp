// AIE Imager — imagen estéreo multibanda (4 bandas) estilo Ozone Imager (VST 2.4, 32 y 64 bits).
//
// Trabaja en Mid/Side. La señal central (Mid) NO se modifica, así que la suma mono (L+R) queda igual sea cual
// sea el ajuste: compatible con mono por diseño. En cada banda se cambia solo la parte lateral (Side):
//  - Width (escala de Ozone): -100 = banda en mono, 0 = sin cambios, +100 = el doble de anchura.
//  - Stereoize: crea anchura en bandas mono añadiendo al Side una versión del Mid de la banda (tampoco cambia la
//    suma mono). Modo I: copia retrasada (7-13 ms, efecto Haas). Modo II: copia decorrelada con filtros
//    pasa-todo (más suave, sin eco ni filtro peine).
// Las bandas se separan con un árbol de cruces Linkwitz-Riley de 4.º orden con compensación de fase (pasa-todo):
// la suma de las bandas es plana en magnitud, con el mismo pequeño desfase en L y R, y no hay latencia.
//
// Parámetros (los 16 primeros son los de la versión 1.0, en el mismo orden): 3 cortes, Width x4,
// Stereoize x4, Solo x4, Mono, modo de Stereoize, Link, Bypass x4, modo del vectorscopio.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
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
    kW1, kW2, kW3, kW4,                      // anchura por banda (normalizado 0..1 = 0..200 %)
    kS1 = kW1 + kBands, kS2, kS3, kS4,       // stereoize por banda (0..100 %)
    kSolo1 = kS1 + kBands, kSolo2, kSolo3, kSolo4,
    kMono = kSolo1 + kBands,                 // 15: escucha en mono
    kStereoMode,                             // 16: Stereoize I / II
    kLink,                                   // 17: mover todas las anchuras a la vez
    kByp1, kByp2, kByp3, kByp4,              // 18-21: bypass por banda
    kScopeMode = kByp1 + kBands,             // 22: Polar Sample / Polar Level / Lissajous
    kNumParams
};
static const double kFreqMin = 20.0, kFreqMax = 20000.0;
inline double freqFromNorm(double v) { return kFreqMin * std::pow(kFreqMax / kFreqMin, v); }
inline double normFromFreq(double f) { return std::log(f / kFreqMin) / std::log(kFreqMax / kFreqMin); }
static const double kStereoizeMs[kBands] = {13.0, 11.0, 9.0, 7.0};  // modo I: retardo del Mid por banda
static const double kDiffuseMs[4] = {1.7, 3.1, 5.3, 7.9};          // modo II: cadena de pasa-todo
static const int kDelayLen = 8192;
static const int kScopeLen = 8192;  // muestras (L,R) a frecuencia completa para vectorscopio y espectro

// Límite de correlación "seguro" según la frecuencia de la banda: los graves deben quedar casi mono, los
// agudos admiten más anchura. Verde por encima del límite, amarillo hasta max(0, límite - 0,5), rojo debajo.
inline double safeCorrelation(double centerHz) {
    double x = std::log10(std::max(30.0, std::min(12000.0, centerHz)));  // 1,48 .. 4,08
    double t = (x - std::log10(80.0)) / (std::log10(8000.0) - std::log10(80.0));
    t = std::min(1.0, std::max(0.0, t));
    return 0.9 - t * 0.8;  // 80 Hz -> 0,9 ; 8 kHz -> 0,1
}

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

// Pasa-todo de Schroeder (decorrelador del modo II)
struct Diffuser {
    std::vector<float> buf[4];
    int pos[4] = {0, 0, 0, 0}, len[4] = {1, 1, 1, 1};
    void setup(double fs) {
        for (int i = 0; i < 4; ++i) {
            len[i] = std::max(1, int(std::lround(kDiffuseMs[i] * 0.001 * fs)));
            buf[i].assign(len[i], 0.0f);
            pos[i] = 0;
        }
    }
    inline double run(double x) {
        const double g = 0.6;
        for (int i = 0; i < 4; ++i) {
            double d = buf[i][pos[i]];
            double y = -g * x + d;
            buf[i][pos[i]] = float(x + g * y);
            pos[i] = (pos[i] + 1) % len[i];
            x = y;
        }
        return x;
    }
    void clear() { for (int i = 0; i < 4; ++i) std::fill(buf[i].begin(), buf[i].end(), 0.0f); }
};

struct BandMeter { std::atomic<float> mm, ss; };  // medias de M² y S² del último bloque (salida)

class AIEImager {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    double designed[kBands - 1] = {-1, -1, -1};
    Splitter splitM, splitS;
    std::vector<float> delay[kBands];
    Diffuser diffuse[kBands];
    int delayPos = 0;
    // medidores, vectorscopio y espectro para la interfaz
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
            params[kW1 + b] = 0.5f;  // sin cambios
            params[kS1 + b] = 0.0f;
            params[kSolo1 + b] = 0.0f;
            params[kByp1 + b] = 0.0f;
            delay[b].assign(kDelayLen, 0.0f);
            meters[b].mm = 0; meters[b].ss = 0;
        }
        params[kMono] = 0.0f;
        params[kStereoMode] = 1.0f;  // modo II por defecto (más suave)
        params[kLink] = 0.0f;
        params[kScopeMode] = 0.0f;
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
        fx.version = 1100;
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
    double bandCenter(int b) const {
        double f[kBands - 1];
        crossovers(f);
        double lo = b == 0 ? 30.0 : f[b - 1], hi = b == kBands - 1 ? 16000.0 : f[b];
        return std::sqrt(lo * hi);
    }
    void setSampleRate(double fs) {
        sampleRate = fs > 0 ? fs : 44100;
        designed[0] = -1;
        for (auto& d : diffuse) d.setup(sampleRate);
        reset();
    }
    void reset() {
        splitM.clear();
        splitS.clear();
        for (auto& d : delay) std::fill(d.begin(), d.end(), 0.0f);
        for (auto& d : diffuse) d.clear();
        delayPos = 0;
    }
    double widthOf(int b) const { return 2.0 * params[kW1 + b]; }        // 0..2 (1 = sin cambios)
    double ozoneWidth(int b) const { return (widthOf(b) - 1.0) * 100.0; }  // -100..+100
    bool bypassed(int b) const { return params[kByp1 + b] >= 0.5f; }

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
        bool modeII = params[kStereoMode] >= 0.5f;
        for (int b = 0; b < kBands; ++b) {
            bool byp = bypassed(b);
            w[b] = byp ? 1.0 : widthOf(b);
            st[b] = byp ? 0.0 : params[kS1 + b] * (modeII ? 0.9 : 0.7);
            dly[b] = std::min(kDelayLen - 1, int(std::lround(kStereoizeMs[b] * 0.001 * sampleRate)));
            solo[b] = params[kSolo1 + b] >= 0.5f;
            anySolo = anySolo || solo[b];
        }
        bool mono = params[kMono] >= 0.5f;
        double mm[kBands] = {0, 0, 0, 0}, ss[kBands] = {0, 0, 0, 0};
        uint32_t sw = scopeWritten.load(std::memory_order_relaxed);
        for (int32_t i = 0; i < n; ++i) {
            double L = in[0][i], R = in[1][i];
            double M = 0.5 * (L + R), S = 0.5 * (L - R);
            double mb[kBands], sb[kBands];
            splitM.run(M, mb);
            splitS.run(S, sb);
            double mOut = 0, sOut = 0;
            for (int b = 0; b < kBands; ++b) {
                double extra = 0;
                if (modeII) {
                    double dfz = diffuse[b].run(mb[b]);
                    extra = st[b] * dfz;
                } else {
                    std::vector<float>& d = delay[b];
                    d[delayPos] = float(mb[b]);
                    extra = st[b] * d[(delayPos - dly[b] + kDelayLen) % kDelayLen];
                }
                double sBand = w[b] * sb[b] + extra;
                if (!anySolo || solo[b]) {
                    sOut += sBand;
                    mOut += mb[b];  // el Mid pasa por los mismos filtros (mismo desfase que el Side)
                }
                mm[b] += mb[b] * mb[b];
                ss[b] += sBand * sBand;
            }
            delayPos = (delayPos + 1) % kDelayLen;
            if (mono) sOut = 0;
            double oL = mOut + sOut, oR = mOut - sOut;
            out[0][i] = T(oL);
            out[1][i] = T(oR);
            scopeL[sw & (kScopeLen - 1)] = float(oL);
            scopeR[sw & (kScopeLen - 1)] = float(oR);
            ++sw;
        }
        scopeWritten.store(sw, std::memory_order_release);
        for (int b = 0; b < kBands; ++b) {
            meters[b].mm.store(float(mm[b] / std::max(1, n)), std::memory_order_relaxed);
            meters[b].ss.store(float(ss[b] / std::max(1, n)), std::memory_order_relaxed);
        }
        meterSeq.fetch_add(1, std::memory_order_release);
    }

    void paramName(int i, char* s) {
        char b[16];
        if (i <= kX3) std::snprintf(b, sizeof b, "Cross %d", (i - kX1 + 1) % 10);
        else if (i < kS1) std::snprintf(b, sizeof b, "Width %d", (i - kW1 + 1) % 10);
        else if (i < kSolo1) std::snprintf(b, sizeof b, "Stereo %d", (i - kS1 + 1) % 10);
        else if (i < kMono) std::snprintf(b, sizeof b, "Solo %d", (i - kSolo1 + 1) % 10);
        else if (i == kMono) std::snprintf(b, sizeof b, "Mono");
        else if (i == kStereoMode) std::snprintf(b, sizeof b, "StMode");
        else if (i == kLink) std::snprintf(b, sizeof b, "Link");
        else if (i < kScopeMode) std::snprintf(b, sizeof b, "Bypass %d", (i - kByp1 + 1) % 10);
        else std::snprintf(b, sizeof b, "Scope");
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
        } else if (i < kS1) {
            double v = ozoneWidth(i - kW1);
            if (std::fabs(v) < 0.5) std::snprintf(b, sizeof b, "0");
            else std::snprintf(b, sizeof b, "%+.0f", v);
        } else if (i < kSolo1) std::snprintf(b, sizeof b, "%.0f %%", params[i] * 100.0);
        else if (i == kStereoMode) std::snprintf(b, sizeof b, "%s", params[i] >= 0.5f ? "II" : "I");
        else if (i == kScopeMode) {
            static const char* m[3] = {"Polar S", "Polar L", "Lissaj"};
            std::snprintf(b, sizeof b, "%s", m[scopeMode()]);
        } else std::snprintf(b, sizeof b, "%s", params[i] >= 0.5f ? "On" : "Off");
        return b;
    }
    int scopeMode() const { int m = int(params[kScopeMode] * 2 + 0.5f); return std::min(2, std::max(0, m)); }
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
static const Color kYellow = rgb(250, 204, 21);

// FFT compleja radix-2 en el sitio (para el espectro de la interfaz)
inline void fft(std::vector<std::complex<double>>& a) {
    size_t n = a.size();
    for (size_t i = 1, j = 0; i < n; ++i) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (size_t len = 2; len <= n; len <<= 1) {
        double ang = -2 * 3.14159265358979323846 / double(len);
        std::complex<double> wl(std::cos(ang), std::sin(ang));
        for (size_t i = 0; i < n; i += len) {
            std::complex<double> w(1, 0);
            for (size_t k = 0; k < len / 2; ++k) {
                std::complex<double> u = a[i + k], v = a[i + k + len / 2] * w;
                a[i + k] = u + v;
                a[i + k + len / 2] = u - v;
                w *= wl;
            }
        }
    }
}

class ImagerEditor {
public:
    static const int W = 960, H = 570;
    static const int kFrameMs = 33;
    static const int kFftN = 4096;
    Canvas cv;
    AIEImager* p;
    int (*menu)(void*, int, int, const std::vector<std::string>&, int) = nullptr;
    void* menuCtx = nullptr;

    // zonas
    Rect spec = {16, 86, 640, 190};       // espectro Mid/Side con las bandas y los cortes
    Rect strip = {16, 300, 640, 250};     // columnas fijas con los controles de cada banda
    Rect scope = {680, 92, 264, 180};     // vectorscopio
    Rect btnScope[3];
    Rect corr = {680, 330, 264, 22};
    Rect tglMono = {680, 372, 128, 30}, tglLink = {816, 372, 128, 30};
    Rect btnModeI = {680, 430, 62, 28}, btnModeII = {746, 430, 62, 28}, btnReset = {816, 430, 128, 28};

    // medidas suavizadas
    float bandCorr[kBands] = {1, 1, 1, 1}, bandWidth[kBands] = {0, 0, 0, 0}, totalCorr = 1;
    uint32_t lastSeq = 0;
    std::vector<float> specMid, specSide;  // dB suavizados por bin
    std::vector<std::complex<double>> bufM, bufS;
    std::vector<double> window;
    std::vector<uint32_t> background;
    // arrastre
    int dragKind = 0, dragIndex = -1, dragY = 0, dragX = 0;  // 1 = corte, 2 = width, 3 = stereoize
    float dragV = 0, dragOthers[kBands] = {0, 0, 0, 0};

    explicit ImagerEditor(AIEImager* plug) : p(plug) {
        cv.resize(W, H);
        specMid.assign(kFftN / 2, -120.0f);
        specSide.assign(kFftN / 2, -120.0f);
        bufM.resize(kFftN);
        bufS.resize(kFftN);
        window.resize(kFftN);
        for (int i = 0; i < kFftN; ++i) window[i] = 0.5 - 0.5 * std::cos(2 * 3.14159265358979323846 * i / (kFftN - 1));
        for (int i = 0; i < 3; ++i) btnScope[i] = {680 + i * 90, scope.b() + 10, 84, 22};
    }
    bool dragging() const { return dragKind != 0; }

    // ---- geometría ----
    int xOfFreq(double f) const { return spec.x + int(normFromFreq(f) * spec.w + 0.5); }
    void bandSpan(int b, int& x0, int& x1) const {
        double f[kBands - 1];
        p->crossovers(f);
        x0 = b == 0 ? spec.x : xOfFreq(f[b - 1]);
        x1 = b == kBands - 1 ? spec.r() : xOfFreq(f[b]);
    }
    Rect column(int b) const { int cw = strip.w / kBands; return {strip.x + b * cw + 4, strip.y, cw - 8, strip.h}; }
    Rect widthFader(int b) const { Rect c = column(b); return {c.x + 46, c.y + 64, 18, 100}; }
    Rect corrMeter(int b) const { Rect c = column(b); return {c.x + c.w - 34, c.y + 64, 10, 100}; }
    Rect stereoSlider(int b) const { Rect c = column(b); return {c.x + 12, c.y + 202, c.w - 24, 14}; }
    Rect soloBtn(int b) const { Rect c = column(b); return {c.x + 12, c.y + 222, (c.w - 30) / 2, 20}; }
    Rect bypBtn(int b) const { Rect c = column(b); int w = (c.w - 30) / 2; return {c.x + 18 + w, c.y + 222, w, 20}; }
    Rect crossHandle(int i) const {
        double f[kBands - 1];
        p->crossovers(f);
        int x = xOfFreq(f[i]);
        return {x - 8, spec.y + 4, 16, 24};
    }

    // ---- medidas ----
    void tick() {
        uint32_t seq = p->meterSeq.load(std::memory_order_acquire);
        if (seq != lastSeq) {
            lastSeq = seq;
            double tm = 0, ts = 0;
            for (int b = 0; b < kBands; ++b) {
                double mm = p->meters[b].mm.load(), ss = p->meters[b].ss.load();
                tm += mm; ts += ss;
                double c = (mm + ss) > 1e-12 ? (mm - ss) / (mm + ss) : 1.0;  // correlación L/R de la banda
                double wd = (mm + ss) > 1e-12 ? std::sqrt(ss / (mm + ss)) : 0.0;
                bandCorr[b] += float((c - bandCorr[b]) * 0.25);
                bandWidth[b] += float((wd - bandWidth[b]) * 0.25);
            }
            double c = (tm + ts) > 1e-12 ? (tm - ts) / (tm + ts) : 1.0;
            totalCorr += float((c - totalCorr) * 0.2);
        }
        // espectro de Mid y Side de las últimas kFftN muestras
        uint32_t end = p->scopeWritten.load(std::memory_order_acquire);
        if (end < uint32_t(kFftN)) return;
        for (int i = 0; i < kFftN; ++i) {
            uint32_t k = (end - kFftN + i) & (kScopeLen - 1);
            double l = p->scopeL[k], r = p->scopeR[k];
            bufM[i] = std::complex<double>(0.5 * (l + r) * window[i], 0);
            bufS[i] = std::complex<double>(0.5 * (l - r) * window[i], 0);
        }
        fft(bufM);
        fft(bufS);
        for (int i = 1; i < kFftN / 2; ++i) {
            double m = 20 * std::log10(std::abs(bufM[i]) / (kFftN / 4.0) + 1e-9);
            double s = 20 * std::log10(std::abs(bufS[i]) / (kFftN / 4.0) + 1e-9);
            // sube rápido y baja despacio
            specMid[i] = float(m > specMid[i] ? m : specMid[i] + (m - specMid[i]) * 0.15);
            specSide[i] = float(s > specSide[i] ? s : specSide[i] + (s - specSide[i]) * 0.15);
        }
    }

    Color corrColor(double c, double safe) const {
        if (c >= safe) return kGreen;
        if (c >= std::max(0.0, safe - 0.5)) return kYellow;
        return kRed;
    }

    // ---- dibujo ----
    void drawBackground() {
        cv.vgradient({0, 0, W, H}, rgb(20, 22, 30), rgb(11, 12, 17));
        cv.vgradient({0, 0, W, 50}, rgb(34, 37, 48), rgb(23, 25, 33));
        cv.fill({0, 50, W, 1}, rgb(52, 56, 70));
        cv.circle(26, 25, 9, rgb(80, 160, 255));
        cv.circle(34, 25, 9, withAlpha(rgb(255, 92, 92), 200));
        cv.text(kFontBig, 50, 13, "AIE Imager", kText);
        cv.text(kFontSmall, 50 + Canvas::textWidth(kFontBig, "AIE Imager") + 12, 22,
                "AI Audio Engineer  ·  imagen estéreo multibanda", kDim);
        cv.text(kFontSmall, W - 18, 22, "Mono compatible: el centro no se modifica", kFaint, 2);
        cv.rounded({spec.x - 6, spec.y - 26, spec.w + 12, spec.h + 48}, 8, rgb(26, 29, 39));
        cv.text(kFontNormal, spec.x, spec.y - 22, "Espectro estéreo", kDim);
        cv.rounded({spec.x + 140, spec.y - 18, 10, 10}, 2, rgb(120, 126, 140));
        cv.text(kFontSmall, spec.x + 154, spec.y - 20, "Centro (Mid)", kDim);
        cv.rounded({spec.x + 240, spec.y - 18, 10, 10}, 2, rgb(150, 210, 255));
        cv.text(kFontSmall, spec.x + 254, spec.y - 20, "Lados (Side)", kDim);
        cv.rounded({strip.x - 6, strip.y - 4, strip.w + 12, strip.h + 10}, 8, rgb(26, 29, 39));
        cv.rounded({scope.x - 8, 60, scope.w + 16, 496}, 8, rgb(26, 29, 39));
        cv.text(kFontNormal, scope.x, 66, "Vectorscopio", kDim);
        cv.text(kFontNormal, corr.x, corr.y - 20, "Correlación", kDim);
        cv.text(kFontNormal, btnModeI.x, btnModeI.y - 20, "Stereoize", kDim);
        // eje de frecuencias
        const double marks[] = {50, 100, 200, 500, 1000, 2000, 5000, 10000};
        const char* lab[] = {"50", "100", "200", "500", "1k", "2k", "5k", "10k"};
        for (int i = 0; i < 8; ++i) {
            int x = xOfFreq(marks[i]);
            cv.text(kFontSmall, x, spec.b() + 4, lab[i], kFaint, 1);
        }
        // escala del vectorscopio
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
        cv.text(kFontSmall, int(cx - rad * 0.707) - 12, int(cy - rad * 0.707) - 14, "L", kFaint);
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
    void drawButton(const Rect& r, const char* label, bool on) {
        cv.rounded(r, 6, on ? rgb(64, 196, 255) : rgb(40, 44, 56));
        cv.text(kFontNormal, r.x + r.w / 2, r.y + r.h / 2 - 8, label, on ? rgb(10, 14, 20) : kDim, 1);
    }

    void drawSpectrum(bool anySolo) {
        cv.fill(spec, rgb(12, 13, 18));
        // bandas: fondo tenue del color de la banda
        for (int b = 0; b < kBands; ++b) {
            int x0, x1;
            bandSpan(b, x0, x1);
            bool active = (!anySolo || p->params[kSolo1 + b] >= 0.5f) && !p->bypassed(b);
            cv.fill({x0, spec.y, x1 - x0, spec.h}, withAlpha(kBandColors[b], active ? 18 : 6));
            cv.fill({x0, spec.b() - 3, x1 - x0, 3}, withAlpha(kBandColors[b], active ? 210 : 70));
        }
        // rejilla
        const double marks[] = {50, 100, 200, 500, 1000, 2000, 5000, 10000};
        for (double m : marks) cv.fill({xOfFreq(m), spec.y, 1, spec.h}, rgb(30, 33, 44));
        for (int d = -24; d >= -72; d -= 24) {
            int y = spec.y + int((-d) / 84.0 * spec.h);
            cv.fill({spec.x, y, spec.w, 1}, rgb(30, 33, 44));
        }
        // espectros: Mid relleno gris, Side línea del color de la banda
        double fs = p->sampleRate;
        auto yOf = [&](double db) { return spec.y + std::min(1.0, std::max(0.0, (-db + 0.0) / 84.0)) * spec.h; };
        std::vector<double> sx, sy;
        double f[kBands - 1];
        p->crossovers(f);
        for (int x = 0; x < spec.w; ++x) {
            double fl = freqFromNorm(double(x) / spec.w), fh = freqFromNorm(double(x + 1) / spec.w);
            int k0 = std::max(1, int(fl * kFftN / fs)), k1 = std::max(k0 + 1, int(fh * kFftN / fs));
            k1 = std::min(k1, kFftN / 2);
            if (k0 >= kFftN / 2) break;
            float m = -120, s = -120;
            for (int k = k0; k < k1; ++k) { m = std::max(m, specMid[k]); s = std::max(s, specSide[k]); }
            int ym = int(yOf(m + 6));
            if (ym < spec.b()) cv.fill({spec.x + x, ym, 1, spec.b() - ym}, rgb(70, 74, 88));
            sx.push_back(spec.x + x + 0.5);
            sy.push_back(yOf(s + 6));
        }
        // línea del Side por tramos del color de cada banda
        size_t start = 0;
        for (int b = 0; b < kBands && start < sx.size(); ++b) {
            int x0, x1;
            bandSpan(b, x0, x1);
            std::vector<double> xs, ys;
            for (size_t i = start; i < sx.size() && sx[i] <= x1 + 1; ++i) { xs.push_back(sx[i]); ys.push_back(sy[i]); start = i; }
            cv.polyline(xs, ys, 1.6, mix(kBandColors[b], rgb(255, 255, 255), 0.25));
        }
        // cortes
        for (int i = 0; i < kBands - 1; ++i) {
            int x = xOfFreq(f[i]);
            cv.fill({x, spec.y, 1, spec.h}, rgb(170, 178, 200));
            Rect h = crossHandle(i);
            cv.rounded(h, 4, dragKind == 1 && dragIndex == i ? kAccent : rgb(70, 78, 96));
            cv.fill({x - 2, h.y + 6, 1, 12}, rgb(210, 216, 230));
            cv.fill({x + 1, h.y + 6, 1, 12}, rgb(210, 216, 230));
            std::string t = p->paramText(kX1 + i);
            int tw = Canvas::textWidth(kFontSmall, t);
            int tx = std::min(spec.r() - tw - 4, std::max(spec.x + 4, x + 12));
            cv.rounded({tx - 4, h.y + 4, tw + 8, 16}, 4, withAlpha(rgb(12, 13, 18), 220));
            cv.text(kFontSmall, tx, h.y + 5, t, kText);
        }
    }

    void drawBandColumn(int b, bool anySolo) {
        Rect c = column(b);
        bool byp = p->bypassed(b), so = p->params[kSolo1 + b] >= 0.5f;
        bool active = (!anySolo || so) && !byp;
        Color bc = kBandColors[b];
        cv.rounded(c, 6, rgb(31, 34, 45));
        cv.fill({c.x, c.y, c.w, 3}, withAlpha(bc, active ? 230 : 80));
        char t[32];
        std::snprintf(t, sizeof t, "Banda %d", b + 1);
        cv.text(kFontNormal, c.x + 12, c.y + 10, t, active ? kText : kFaint);
        // rango de la banda
        double f[kBands - 1];
        p->crossovers(f);
        auto fmt = [](double v) { char s[16]; if (v >= 1000) std::snprintf(s, sizeof s, "%.1fk", v / 1000); else std::snprintf(s, sizeof s, "%.0f", v); return std::string(s); };
        std::string range = (b == 0 ? std::string("20") : fmt(f[b - 1])) + "-" + (b == kBands - 1 ? std::string("20k") : fmt(f[b])) + " Hz";
        cv.text(kFontSmall, c.r() - 10, c.y + 12, range, kFaint, 2);
        // Width (escala de Ozone: -100 mono, 0 sin cambios, +100 doble)
        Rect wf = widthFader(b);
        cv.text(kFontSmall, wf.x + wf.w / 2, wf.y - 20, "Width", kFaint, 1);
        cv.rounded({wf.x + 6, wf.y, 6, wf.h}, 3, rgb(12, 13, 18));
        int yZero = wf.y + wf.h / 2;
        cv.fill({wf.x - 3, yZero, wf.w + 6, 1}, rgb(80, 86, 104));
        cv.text(kFontSmall, wf.x - 6, wf.y - 6, "+100", kFaint, 2);
        cv.text(kFontSmall, wf.x - 6, yZero - 6, "0", kFaint, 2);
        cv.text(kFontSmall, wf.x - 6, wf.b() - 6, "-100", kFaint, 2);
        double v = p->params[kW1 + b];
        int ky = wf.b() - int(v * wf.h + 0.5);
        cv.rounded({wf.x + 6, std::min(ky, yZero), 6, std::abs(yZero - ky) + 1}, 3, withAlpha(bc, active ? 230 : 80));
        cv.rounded({wf.x - 4, ky - 7, wf.w + 8, 14}, 4, active ? rgb(222, 228, 240) : rgb(110, 116, 130));
        std::string wt = p->ozoneWidth(b) <= -99.5 ? std::string("Mono") : p->paramText(kW1 + b);
        cv.text(kFontNormal, wf.x + wf.w / 2, wf.b() + 6, wt, active ? kText : kDim, 1);
        // medidor de correlación vertical con límites según la frecuencia
        Rect cm = corrMeter(b);
        double safe = safeCorrelation(p->bandCenter(b));
        cv.text(kFontSmall, cm.x + cm.w / 2, cm.y - 20, "Corr", kFaint, 1);
        cv.rounded(cm, 3, rgb(12, 13, 18));
        auto yC = [&](double cval) { return cm.b() - int((cval + 1) / 2 * cm.h + 0.5); };
        int ySafe = yC(safe), yWarn = yC(std::max(0.0, safe - 0.5)), y0 = yC(0);
        cv.fill({cm.x - 3, ySafe, cm.w + 6, 1}, withAlpha(kGreen, 160));
        cv.fill({cm.x - 3, yWarn, cm.w + 6, 1}, withAlpha(kYellow, 120));
        int yv = yC(bandCorr[b]);
        Color cc = corrColor(bandCorr[b], safe);
        cv.fill({cm.x + 1, std::min(yv, y0), cm.w - 2, std::abs(y0 - yv) + 1}, withAlpha(cc, active ? 230 : 90));
        char cs[12];
        std::snprintf(cs, sizeof cs, "%+.2f", bandCorr[b]);
        cv.text(kFontSmall, cm.x + cm.w / 2, cm.b() + 8, cs, active ? cc : kFaint, 1);
        // anchura medida (barra horizontal bajo el título)
        Rect wm = {c.x + 12, c.y + 32, c.w - 24, 4};
        cv.rounded(wm, 2, rgb(12, 13, 18));
        cv.rounded({wm.x, wm.y, int(std::min(1.0, bandWidth[b] / 0.72) * wm.w), wm.h}, 2, withAlpha(bc, active ? 200 : 70));
        // Stereoize
        Rect ss = stereoSlider(b);
        cv.text(kFontSmall, ss.x, ss.y - 15, "Stereoize", kFaint);
        cv.text(kFontSmall, ss.r(), ss.y - 15, p->paramText(kS1 + b), kDim, 2);
        cv.rounded({ss.x, ss.y + 5, ss.w, 4}, 2, rgb(12, 13, 18));
        int sx = ss.x + int(p->params[kS1 + b] * ss.w + 0.5);
        cv.rounded({ss.x, ss.y + 5, sx - ss.x, 4}, 2, withAlpha(bc, active ? 190 : 70));
        cv.circle(sx, ss.y + 7, 6, active ? rgb(222, 228, 240) : rgb(110, 116, 130));
        // Solo / Bypass
        Rect sb = soloBtn(b), bb = bypBtn(b);
        cv.rounded(sb, 5, so ? withAlpha(kYellow, 90) : rgb(42, 46, 58));
        if (so) cv.roundedOutline(sb, 5, kYellow);
        cv.text(kFontSmall, sb.x + sb.w / 2, sb.y + 4, "Solo", so ? kText : kDim, 1);
        cv.rounded(bb, 5, byp ? withAlpha(kRed, 80) : rgb(42, 46, 58));
        if (byp) cv.roundedOutline(bb, 5, kRed);
        cv.text(kFontSmall, bb.x + bb.w / 2, bb.y + 4, "Bypass", byp ? kText : kDim, 1);
    }

    void drawScope() {
        double cx = scope.x + scope.w / 2.0, cy = scope.b() - 4, rad = std::min(scope.h - 12.0, scope.w / 2.0 - 10);
        uint32_t end = p->scopeWritten.load(std::memory_order_acquire);
        int np = std::min<uint32_t>(end, 3072);
        float peak = 1e-6f;
        for (int i = 1; i <= np; ++i) {
            uint32_t k = (end - i) & (kScopeLen - 1);
            peak = std::max(peak, std::max(std::fabs(p->scopeL[k]), std::fabs(p->scopeR[k])));
        }
        double sc = rad / std::max(0.25, double(peak) * 1.15);
        int mode = p->scopeMode();
        if (mode == 2) {  // Lissajous: puntos sin plegar, círculo completo
            double lcx = cx, lcy = scope.y + scope.h / 2.0, lr = scope.h / 2.0 - 6;
            cv.fill({scope.x, scope.y - 10, scope.w, scope.h + 12}, rgb(26, 29, 39));
            std::vector<double> xs, ys;
            for (int i = 0; i <= 120; ++i) {
                double a = 2 * 3.14159265358979 * i / 120;
                xs.push_back(lcx + lr * std::cos(a)); ys.push_back(lcy + lr * std::sin(a));
            }
            cv.polyline(xs, ys, 1.0, rgb(52, 56, 70));
            cv.line(lcx, lcy - lr, lcx, lcy + lr, 1.0, rgb(52, 56, 70));
            cv.line(lcx - lr, lcy, lcx + lr, lcy, 1.0, rgb(44, 48, 60));
            double s2 = lr / std::max(0.25, double(peak) * 1.15);
            for (int i = 1; i <= np; i += 2) {
                uint32_t k = (end - i) & (kScopeLen - 1);
                double l = p->scopeL[k], r = p->scopeR[k];
                int px = int(lcx + (l - r) * 0.7071 * s2), py = int(lcy - (l + r) * 0.7071 * s2);
                if (std::hypot(px - lcx, py - lcy) <= lr) cv.blend(px, py, rgb(120, 200, 255), 0.6 * (1.0 - double(i) / np) + 0.15);
            }
            return;
        }
        if (mode == 1) {  // Polar Level: rayos con el nivel máximo en cada dirección
            const int nb = 72;
            double lvl[nb] = {0};
            for (int i = 1; i <= np; ++i) {
                uint32_t k = (end - i) & (kScopeLen - 1);
                double l = p->scopeL[k], r = p->scopeR[k];
                double sx = (l - r) * 0.7071, my = (l + r) * 0.7071;
                if (my < 0) { my = -my; sx = -sx; }
                double a = std::atan2(my, sx);  // 0..pi
                int bi = std::min(nb - 1, std::max(0, int(a / 3.14159265358979 * nb)));
                lvl[bi] = std::max(lvl[bi], std::hypot(sx, my));
            }
            for (int bi = 0; bi < nb; ++bi) {
                if (lvl[bi] <= 0) continue;
                double a = (bi + 0.5) / nb * 3.14159265358979, len = std::min(rad, lvl[bi] * sc);
                cv.line(cx, cy, cx + len * std::cos(a), cy - len * std::sin(a), 2.0, rgb(120, 200, 255));
            }
            return;
        }
        // Polar Sample: puntos plegados en el semicírculo
        for (int i = 1; i <= np; i += 2) {
            uint32_t k = (end - i) & (kScopeLen - 1);
            double l = p->scopeL[k], r = p->scopeR[k];
            double sx = (l - r) * 0.7071, my = (l + r) * 0.7071;
            if (my < 0) { my = -my; sx = -sx; }
            int px = int(cx + sx * sc), py = int(cy - my * sc);
            if (std::hypot(px - cx, cy - py) <= rad) cv.blend(px, py, rgb(120, 200, 255), 0.55 * (1.0 - double(i) / np) + 0.15);
        }
    }

    void draw() {
        if (background.size() != cv.px.size()) drawBackground();
        else std::copy(background.begin(), background.end(), cv.px.begin());
        bool anySolo = false;
        for (int b = 0; b < kBands; ++b) anySolo = anySolo || p->params[kSolo1 + b] >= 0.5f;
        drawSpectrum(anySolo);
        for (int b = 0; b < kBands; ++b) drawBandColumn(b, anySolo);
        drawScope();
        const char* sm[3] = {"Polar Sample", "Polar Level", "Lissajous"};
        for (int i = 0; i < 3; ++i) {
            bool on = p->scopeMode() == i;
            cv.rounded(btnScope[i], 5, on ? rgb(64, 196, 255) : rgb(40, 44, 56));
            cv.text(kFontSmall, btnScope[i].x + btnScope[i].w / 2, btnScope[i].y + 5, sm[i], on ? rgb(10, 14, 20) : kDim, 1);
        }
        // correlación total
        cv.rounded(corr, 4, rgb(12, 13, 18));
        int cmid = corr.x + corr.w / 2, cpos = corr.x + int((totalCorr + 1) / 2 * corr.w);
        Color ccol = corrColor(totalCorr, 0.3);
        cv.fill({std::min(cmid, cpos), corr.y + 3, std::abs(cpos - cmid) + 1, corr.h - 6}, ccol);
        cv.fill({cmid, corr.y, 1, corr.h}, rgb(90, 96, 112));
        cv.text(kFontSmall, corr.x + 2, corr.b() + 3, "-1", kFaint);
        cv.text(kFontSmall, cmid, corr.b() + 3, "0", kFaint, 1);
        cv.text(kFontSmall, corr.r() - 2, corr.b() + 3, "+1", kFaint, 2);
        char ct[16];
        std::snprintf(ct, sizeof ct, "%+.2f", totalCorr);
        cv.text(kFontNormal, corr.r(), corr.y - 20, ct, ccol, 2);
        drawToggle(tglMono, "Mono", p->params[kMono] >= 0.5f, kAccent2);
        drawToggle(tglLink, "Link", p->params[kLink] >= 0.5f, kAccent);
        bool m2 = p->params[kStereoMode] >= 0.5f;
        drawButton(btnModeI, "I", !m2);
        drawButton(btnModeII, "II", m2);
        cv.rounded(btnReset, 6, rgb(40, 44, 56));
        cv.text(kFontNormal, btnReset.x + btnReset.w / 2, btnReset.y + 6, "Reiniciar", kDim, 1);
        cv.text(kFontSmall, scope.x, 476, m2 ? "II: decorrelado (suave, sin eco)" : "I: retardo corto (efecto Haas)", kFaint);
        cv.text(kFontSmall, scope.x, 496, "Correlación por banda: verde = seguro", kFaint);
        cv.text(kFontSmall, scope.x, 512, "para esa frecuencia, amarillo = al límite.", kFaint);
    }

    // ---- ratón ----
    bool mouseDown(int x, int y, bool dbl, bool, int, int) {
        for (int i = 0; i < kBands - 1; ++i) {
            Rect h = crossHandle(i);
            Rect hit = {h.x - 4, h.y - 4, h.w + 8, spec.h};
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
            if (soloBtn(b).contains(x, y)) { toggle(kSolo1 + b); return true; }
            if (bypBtn(b).contains(x, y)) { toggle(kByp1 + b); return true; }
        }
        for (int i = 0; i < 3; ++i)
            if (btnScope[i].contains(x, y)) { p->setParamFromEditor(kScopeMode, i / 2.0f); return true; }
        if (tglMono.contains(x, y)) { toggle(kMono); return true; }
        if (tglLink.contains(x, y)) { toggle(kLink); return true; }
        if (btnModeI.contains(x, y)) { p->setParamFromEditor(kStereoMode, 0.0f); return true; }
        if (btnModeII.contains(x, y)) { p->setParamFromEditor(kStereoMode, 1.0f); return true; }
        if (btnReset.contains(x, y)) {
            for (int b = 0; b < kBands; ++b) {
                p->setParamFromEditor(kW1 + b, 0.5f);
                p->setParamFromEditor(kS1 + b, 0.0f);
                p->setParamFromEditor(kSolo1 + b, 0.0f);
                p->setParamFromEditor(kByp1 + b, 0.0f);
            }
            return true;
        }
        return false;
    }
    void toggle(int param) { p->setParamFromEditor(param, p->params[param] >= 0.5f ? 0.0f : 1.0f); }
    void startDrag(int kind, int idx, int x, int y, float v) {
        dragKind = kind; dragIndex = idx; dragX = x; dragY = y; dragV = v;
        for (int b = 0; b < kBands; ++b) dragOthers[b] = p->params[kW1 + b];
        int param = kind == 1 ? kX1 + idx : kW1 + idx;
        if (p->host) p->host(&p->fx, vst::audioMasterBeginEdit, param, 0, nullptr, 0.0f);
    }
    int dragParam() const { return dragKind == 1 ? kX1 + dragIndex : (dragKind == 2 ? kW1 + dragIndex : kS1 + dragIndex); }
    bool mouseMove(int x, int y, bool fine) {
        if (!dragKind) return false;
        float k = fine ? 0.1f : 1.0f, v;
        if (dragKind == 1) v = dragV + float(x - dragX) / spec.w * k;
        else if (dragKind == 2) v = dragV + float(dragY - y) / widthFader(dragIndex).h * k;
        else v = dragV + float(x - dragX) / stereoSlider(dragIndex).w * k;
        p->setParamFromEditor(dragParam(), v, false, false);
        if (dragKind == 2 && p->params[kLink] >= 0.5f) {  // Link: el resto de bandas se mueve lo mismo
            float delta = p->params[kW1 + dragIndex] - dragV;
            for (int b = 0; b < kBands; ++b)
                if (b != dragIndex) p->setParamFromEditor(kW1 + b, dragOthers[b] + delta);
        }
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
        case effGetVendorVersion: return 1100;
        case effGetPlugCategory: return kPlugCategEffect;
        case effGetVstVersion: return 2400;
        case effGetTailSize: return int(0.03 * p->sampleRate);
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
