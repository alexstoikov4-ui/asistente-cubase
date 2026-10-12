// AIE Exciter — excitador armónico multibanda (4 bandas) estilo Ozone Exciter (VST 2.4, 32 y 64 bits).
//
// Cada banda satura su parte de la señal con uno de 7 perfiles (Tube, Tape, Retro, Warm, Triode, Dual Triode,
// Warm Tape; curvas propias que buscan ese carácter, no copias de ningún producto) y la mezcla con la señal
// limpia. Modos por banda: Estéreo (L y R), Mid o Side (solo el centro o solo los lados), Transient o Sustain
// (solo los ataques o solo el cuerpo, con un detector de envolventes rápida/lenta).
// La saturación se hace con sobremuestreo x1/x2/x4/x8 (etapas de media banda de fase lineal) y la señal limpia
// se retrasa lo mismo: latencia fija de 69 muestras con cualquier factor.
// Generador de armónicos independiente del nivel: antes de saturar, cada componente se divide por su envolvente
// (queda entre -1 y 1), se satura siempre con la misma intensidad y se vuelve a multiplicar por la envolvente.
// Así un pasaje flojo y uno fuerte reciben la misma proporción de armónicos y el volumen no cambia.
//
// Por banda: Amount (cantidad de armónicos; 0 = sin efecto), Mix (seco/húmedo), Perfil, Modo, Solo, Bypass.
// Global: 3 cortes, Oversampling, Delta (escuchar solo lo añadido), Output.

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
#include "../common/oversampler.h"
#if defined(_WIN32)
#include "../common/vst_window_win32.h"
#endif

static const int32_t kUniqueId = 0x41494578;  // 'AIEx'
static const int kBands = 4;
enum Param {
    kX1 = 0, kX2, kX3,
    kAmt1 = 3,
    kMix1 = kAmt1 + kBands,
    kProf1 = kMix1 + kBands,
    kMode1 = kProf1 + kBands,
    kSolo1 = kMode1 + kBands,
    kByp1 = kSolo1 + kBands,
    kOversampling = kByp1 + kBands,  // 27
    kDelta,                          // 28
    kOutput,                         // 29
    kNumParams
};
enum Profile { kTube = 0, kTape, kRetro, kWarm, kTriode, kDualTriode, kWarmTape, kNumProfiles };
static const char* const kProfileNames[kNumProfiles] = {"Tube", "Tape", "Retro", "Warm", "Triode", "Dual Triode", "Warm Tape"};
enum Mode { kStereo = 0, kMid, kSide, kTransient, kSustain, kNumModes };
static const char* const kModeNames[kNumModes] = {"Estéreo", "Mid", "Side", "Transient", "Sustain"};
static const int kNumOs = 4;  // x1 x2 x4 x8
static const double kFreqMin = 20.0, kFreqMax = 20000.0;
inline double freqFromNorm(double v) { return kFreqMin * std::pow(kFreqMax / kFreqMin, v); }
inline double normFromFreq(double f) { return std::log(f / kFreqMin) / std::log(kFreqMax / kFreqMin); }
static const int kScopeLen = 8192;
static const double kMaxDriveDb = 24.0;

// ---- curvas de saturación (pendiente 1 en el origen) ----
inline double triode(double u) { return u >= 0 ? std::tanh(u) : std::tanh(1.6 * u) / 1.6; }  // asimétrica: pares
inline double shape(int prof, double u) {
    switch (prof) {
        case kTube: {  // polarizada: armónicos pares, cálida
            const double b = 0.25, tb = std::tanh(b), s = 1 - tb * tb;
            return (std::tanh(u + b) - tb) / s;
        }
        case kTape: return u / std::sqrt(1 + u * u);                                      // suave y simétrica
        case kRetro: return u >= 0 ? 1 - std::exp(-u) : -(1 - std::exp(u));              // transistor: rodilla dura
        case kWarm: { double t = std::tanh(u); return t + 0.15 * t * t; }                 // 2.º y 3.er armónico
        case kTriode: return triode(u);
        case kDualTriode: return -triode(-triode(u));                                     // dos etapas: más denso
        case kWarmTape: { double t = std::tanh(u); double w = t + 0.12 * t * t; return w / std::sqrt(1 + w * w); }
        default: return u;
    }
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
struct LR4 {
    Biquad a, b;
    void design(bool hp, double f, double fs) { a.design(hp, f, fs); b.design(hp, f, fs); }
    inline double run(double x) { return b.run(a.run(x)); }
    void clear() { a.clear(); b.clear(); }
};
struct AllPass {
    LR4 lp, hp;
    void design(double f, double fs) { lp.design(false, f, fs); hp.design(true, f, fs); }
    inline double run(double x) { return lp.run(x) + hp.run(x); }
    void clear() { lp.clear(); hp.clear(); }
};
// Árbol de cruces LR4 con compensación de fase: la suma de las 4 bandas es plana.
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
        out[0] = lo1.run(low); out[1] = hi1.run(low); out[2] = lo3.run(high); out[3] = hi3.run(high);
    }
    void clear() { lo2.clear(); hi2.clear(); lo1.clear(); hi1.clear(); lo3.clear(); hi3.clear(); ap3.clear(); ap1.clear(); }
};

// Saturación de una componente normalizada (entre -1 y 1): f(g·u) escalada para que un seno de amplitud 1
// conserve su energía (RMS) aunque la saturación lo acerque a una onda cuadrada.
struct Saturator {
    int prof = kWarm;
    double g = -1, k = 1;
    void set(int profile, double drive) {
        if (profile == prof && drive == g) return;
        prof = profile;
        g = drive;
        const double xr = 1.0, pi = 3.14159265358979323846;
        double mean = 0, sq = 0;
        const int N = 64;
        double v[N];
        for (int i = 0; i < N; ++i) { v[i] = shape(prof, g * xr * std::sin(2 * pi * (i + 0.5) / N)); mean += v[i]; }
        mean /= N;
        for (int i = 0; i < N; ++i) sq += (v[i] - mean) * (v[i] - mean);
        double r = std::sqrt(sq / N);
        k = r > 1e-12 ? (xr / std::sqrt(2.0)) / r : 1.0;
    }
    inline double operator()(double x) const { return k * shape(prof, g * x); }
};

struct DcBlock {  // paso alto de 1 polo a ~10 Hz (las curvas asimétricas generan continua)
    double x1 = 0, y1 = 0, r = 0.9986;
    void set(double fs) { r = std::exp(-2 * 3.14159265358979323846 * 10.0 / fs); }
    inline double run(double x) { double y = x - x1 + r * y1; x1 = x; y1 = y; return y; }
    void clear() { x1 = y1 = 0; }
};

struct BandState {
    ovs::Oversampler os[2];
    ovs::Delay dry[2];
    DcBlock dc[2];
    Saturator sat;
    double envFast = 0, envSlow = 0, tw = 0;  // detector de transitorios
    double fade = 0;      // entrada suave del efecto al activar la banda (sin chasquidos)
    double env[2] = {0, 0};
    ovs::Delay envDelay[2];
    bool running = false;
    void clear() {
        for (int i = 0; i < 2; ++i) { os[i].clear(); dry[i].clear(); dc[i].clear(); }
        envFast = envSlow = tw = 0;
        fade = 0;
        for (int i = 0; i < 2; ++i) { env[i] = 0; envDelay[i].clear(); }
        running = false;
    }
};

class AIEExciter {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    double designed[kBands - 1] = {-1, -1, -1};
    Splitter splitL, splitR;
    BandState band[kBands];
    double atkF = 0, relF = 0, atkS = 0, relS = 0, fadeCoef = 0.001, envRel = 0.999;
    // medidores y espectro para la interfaz
    std::atomic<float> mBand[kBands], mAdded[kBands];
    std::atomic<uint32_t> meterSeq;
    std::vector<float> scopeOut, scopeAdd;
    std::atomic<uint32_t> scopeWritten;
    void* window = nullptr;

    explicit AIEExciter(audioMasterCallback cb) : host(cb), scopeOut(kScopeLen, 0.0f), scopeAdd(kScopeLen, 0.0f) {
        params[kX1] = float(normFromFreq(120));
        params[kX2] = float(normFromFreq(1000));
        params[kX3] = float(normFromFreq(6000));
        static const int defProf[kBands] = {kTape, kWarm, kTube, kTube};
        for (int b = 0; b < kBands; ++b) {
            params[kAmt1 + b] = 0.0f;
            params[kMix1 + b] = 1.0f;
            params[kProf1 + b] = float(defProf[b]) / (kNumProfiles - 1);
            params[kMode1 + b] = 0.0f;
            params[kSolo1 + b] = 0.0f;
            params[kByp1 + b] = 0.0f;
            mBand[b] = 0; mAdded[b] = 0;
        }
        params[kOversampling] = 1.0f / (kNumOs - 1);  // x2
        params[kDelta] = 0.0f;
        params[kOutput] = 0.5f;  // 0 dB
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
        fx.initialDelay = ovs::kLatency;
        setSampleRate(44100);
    }

    void crossovers(double* f) const {
        for (int i = 0; i < kBands - 1; ++i) f[i] = freqFromNorm(params[kX1 + i]);
        std::sort(f, f + kBands - 1);
        for (int i = 1; i < kBands - 1; ++i) f[i] = std::max(f[i], f[i - 1] * 1.26);
        for (int i = 0; i < kBands - 1; ++i) f[i] = std::min(f[i], sampleRate * 0.45);
    }
    void setSampleRate(double fs) {
        sampleRate = fs > 0 ? fs : 44100;
        designed[0] = -1;
        atkF = 1 - std::exp(-1 / (0.0005 * sampleRate)); relF = 1 - std::exp(-1 / (0.030 * sampleRate));
        atkS = 1 - std::exp(-1 / (0.030 * sampleRate)); relS = 1 - std::exp(-1 / (0.250 * sampleRate));
        fadeCoef = 1 - std::exp(-1 / (0.020 * sampleRate));
        envRel = std::exp(-1 / (0.040 * sampleRate));
        for (auto& b : band) for (auto& d : b.dc) d.set(sampleRate);
        reset();
    }
    void reset() {
        splitL.clear();
        splitR.clear();
        for (auto& b : band) b.clear();
    }
    int profileOf(int b) const { int v = int(params[kProf1 + b] * (kNumProfiles - 1) + 0.5f); return std::min(kNumProfiles - 1, std::max(0, v)); }
    int modeOf(int b) const { int v = int(params[kMode1 + b] * (kNumModes - 1) + 0.5f); return std::min(kNumModes - 1, std::max(0, v)); }
    int osIdx() const { int v = int(params[kOversampling] * (kNumOs - 1) + 0.5f); return std::min(kNumOs - 1, std::max(0, v)); }
    double driveOf(int b) const { return std::pow(10.0, params[kAmt1 + b] * kMaxDriveDb / 20.0); }
    double outputDb() const { return -12.0 + 24.0 * params[kOutput]; }
    double curve(int b, double x) const { Saturator s; s.set(profileOf(b), driveOf(b)); return s(x); }

    template <typename T>
    void run(T** in, T** out, int32_t n) {
        double f[kBands - 1];
        crossovers(f);
        if (f[0] != designed[0] || f[1] != designed[1] || f[2] != designed[2]) {
            splitL.design(f, sampleRate);
            splitR.design(f, sampleRate);
            for (int i = 0; i < kBands - 1; ++i) designed[i] = f[i];
        }
        int stages = osIdx();
        bool anySolo = false;
        double amtScale[kBands], mix[kBands];
        int mode[kBands];
        bool active[kBands], byp[kBands];
        for (int b = 0; b < kBands; ++b) {
            for (auto& o : band[b].os) o.setStages(stages);
            band[b].sat.set(profileOf(b), driveOf(b));
            amtScale[b] = params[kAmt1 + b];  // la intensidad sube con Amount (y la saturación también); 0 = limpia exacta
            mix[b] = params[kMix1 + b];
            mode[b] = modeOf(b);
            byp[b] = params[kByp1 + b] >= 0.5f;
            anySolo = anySolo || params[kSolo1 + b] >= 0.5f;
        }
        for (int b = 0; b < kBands; ++b) {
            active[b] = !anySolo || params[kSolo1 + b] >= 0.5f;
            bool on = !byp[b] && amtScale[b] > 0;
            if (on && !band[b].running) {  // se activa: filtros vacíos y entrada con fundido
                for (int c = 0; c < 2; ++c) { band[b].os[c].clear(); band[b].dc[c].clear(); }
                band[b].fade = 0;
            }
            band[b].running = on;
        }
        bool delta = params[kDelta] >= 0.5f;
        double gout = std::pow(10.0, outputDb() / 20.0);
        double sumBand[kBands] = {0, 0, 0, 0}, sumAdd[kBands] = {0, 0, 0, 0};
        uint32_t sw = scopeWritten.load(std::memory_order_relaxed);
        for (int32_t i = 0; i < n; ++i) {
            double lb[kBands], rb[kBands];
            splitL.run(in[0][i], lb);
            splitR.run(in[1][i], rb);
            double oL = 0, oR = 0, aL = 0, aR = 0;
            for (int b = 0; b < kBands; ++b) {
                BandState& st = band[b];
                // componentes: L/R o M/S según el modo
                bool ms = mode[b] == kMid || mode[b] == kSide;
                double c0 = ms ? 0.5 * (lb[b] + rb[b]) : lb[b];
                double c1 = ms ? 0.5 * (lb[b] - rb[b]) : rb[b];
                // detector de transitorios con el nivel de la banda
                double lvl = std::fabs(0.5 * (lb[b] + rb[b])) + std::fabs(0.5 * (lb[b] - rb[b]));
                st.envFast += (lvl > st.envFast ? atkF : relF) * (lvl - st.envFast);
                st.envSlow += (lvl > st.envSlow ? atkS : relS) * (lvl - st.envSlow);
                double t = st.envFast > 1e-9 ? std::min(1.0, std::max(0.0, (st.envFast - st.envSlow) / st.envFast * 2.0)) : 0.0;
                st.tw += 0.02 * (t - st.tw);
                double weight = mode[b] == kTransient ? st.tw : (mode[b] == kSustain ? 1.0 - st.tw : 1.0);
                double d0 = st.dry[0].process(c0, ovs::kLatency), d1 = st.dry[1].process(c1, ovs::kLatency);
                double y0 = d0, y1 = d1, e0 = 0, e1 = 0;
                if (st.running) {
                    st.fade += fadeCoef * (1.0 - st.fade);
                    double gain = amtScale[b] * mix[b] * weight * st.fade;
                    bool p0 = mode[b] != kSide, p1 = mode[b] != kMid;
                    double cc[2] = {c0, c1}, dd[2] = {d0, d1}, ee[2] = {0, 0};
                    for (int c = 0; c < 2; ++c) {
                        if (c == 0 ? !p0 : !p1) continue;
                        // envolvente de pico (ataque inmediato, caída ~40 ms) para normalizar
                        st.env[c] = std::max(std::fabs(cc[c]), st.env[c] * envRel);
                        double en = std::max(st.env[c], 1e-6);
                        double wn = st.os[c].process(cc[c] / en, st.sat);
                        double w = st.dc[c].run(wn * st.envDelay[c].process(en, ovs::kLatency));
                        ee[c] = (w - dd[c]) * gain;
                    }
                    e0 = ee[0]; e1 = ee[1];
                    y0 += e0; y1 += e1;
                }
                double bl = ms ? y0 + y1 : y0, br = ms ? y0 - y1 : y1;
                double el = ms ? e0 + e1 : e0, er = ms ? e0 - e1 : e1;
                if (active[b]) { oL += bl; oR += br; aL += el; aR += er; }
                sumBand[b] += 0.25 * (bl + br) * (bl + br);
                sumAdd[b] += 0.25 * (el + er) * (el + er);
            }
            double yL = (delta ? aL : oL) * gout, yR = (delta ? aR : oR) * gout;
            out[0][i] = T(yL);
            out[1][i] = T(yR);
            scopeOut[sw & (kScopeLen - 1)] = float(0.5 * (oL + oR));
            scopeAdd[sw & (kScopeLen - 1)] = float(0.5 * (aL + aR));
            ++sw;
        }
        scopeWritten.store(sw, std::memory_order_release);
        for (int b = 0; b < kBands; ++b) {
            mBand[b].store(float(sumBand[b] / std::max(1, n)), std::memory_order_relaxed);
            mAdded[b].store(float(sumAdd[b] / std::max(1, n)), std::memory_order_relaxed);
        }
        meterSeq.fetch_add(1, std::memory_order_release);
    }

    void paramName(int i, char* s) {
        char b[16];
        if (i <= kX3) std::snprintf(b, sizeof b, "Cross %d", (i - kX1 + 1) % 10);
        else if (i < kMix1) std::snprintf(b, sizeof b, "Amount %d", (i - kAmt1 + 1) % 10);
        else if (i < kProf1) std::snprintf(b, sizeof b, "Mix %d", (i - kMix1 + 1) % 10);
        else if (i < kMode1) std::snprintf(b, sizeof b, "Type %d", (i - kProf1 + 1) % 10);
        else if (i < kSolo1) std::snprintf(b, sizeof b, "Mode %d", (i - kMode1 + 1) % 10);
        else if (i < kByp1) std::snprintf(b, sizeof b, "Solo %d", (i - kSolo1 + 1) % 10);
        else if (i < kOversampling) std::snprintf(b, sizeof b, "Bypass %d", (i - kByp1 + 1) % 10);
        else if (i == kOversampling) std::snprintf(b, sizeof b, "OverSmp");
        else if (i == kDelta) std::snprintf(b, sizeof b, "Delta");
        else std::snprintf(b, sizeof b, "Output");
        vst::copyStr(s, b, 9);
    }
    std::string paramText(int i) {
        char b[32];
        if (i <= kX3) {
            double f[kBands - 1];
            crossovers(f);
            double v = f[i - kX1];
            if (v >= 1000) std::snprintf(b, sizeof b, "%.1f kHz", v / 1000);
            else std::snprintf(b, sizeof b, "%.0f Hz", v);
        } else if (i < kProf1) std::snprintf(b, sizeof b, "%.0f %%", params[i] * 100.0);
        else if (i < kMode1) return kProfileNames[profileOf(i - kProf1)];
        else if (i < kSolo1) return kModeNames[modeOf(i - kMode1)];
        else if (i == kOversampling) std::snprintf(b, sizeof b, "x%d", 1 << osIdx());
        else if (i == kOutput) std::snprintf(b, sizeof b, "%+.1f dB", outputDb());
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
static const Color kYellow = rgb(250, 204, 21);

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

class ExciterEditor {
public:
    static const int W = 960, H = 570;
    static const int kFrameMs = 33;
    static const int kFftN = 4096;
    Canvas cv;
    AIEExciter* p;
    int (*menu)(void*, int, int, const std::vector<std::string>&, int) = nullptr;
    void* menuCtx = nullptr;

    Rect spec = {16, 86, 640, 190};
    Rect strip = {16, 300, 640, 250};
    Rect graph = {680, 92, 264, 200};
    Rect btnOs[kNumOs];
    Rect tglDelta = {680, 372, 128, 30};
    Rect outTrack = {680, 440, 200, 22};
    int selected = 1;  // banda que se ve en la gráfica de la curva

    float bandLvl[kBands] = {0, 0, 0, 0}, addLvl[kBands] = {0, 0, 0, 0};
    uint32_t lastSeq = 0;
    std::vector<float> specOut, specAdd;
    std::vector<std::complex<double>> bufO, bufA;
    std::vector<double> window;
    std::vector<uint32_t> background;
    int dragKind = 0, dragIndex = -1, dragY = 0, dragX = 0;  // 1 corte, 2 amount, 3 mix, 4 output
    float dragV = 0;

    explicit ExciterEditor(AIEExciter* plug) : p(plug) {
        cv.resize(W, H);
        specOut.assign(kFftN / 2, -120.0f);
        specAdd.assign(kFftN / 2, -120.0f);
        bufO.resize(kFftN);
        bufA.resize(kFftN);
        window.resize(kFftN);
        for (int i = 0; i < kFftN; ++i) window[i] = 0.5 - 0.5 * std::cos(2 * 3.14159265358979323846 * i / (kFftN - 1));
        for (int i = 0; i < kNumOs; ++i) btnOs[i] = {680 + i * 67, 322, 62, 26};
    }
    bool dragging() const { return dragKind != 0; }

    int xOfFreq(double f) const { return spec.x + int(normFromFreq(f) * spec.w + 0.5); }
    void bandSpan(int b, int& x0, int& x1) const {
        double f[kBands - 1];
        p->crossovers(f);
        x0 = b == 0 ? spec.x : xOfFreq(f[b - 1]);
        x1 = b == kBands - 1 ? spec.r() : xOfFreq(f[b]);
    }
    Rect column(int b) const { int cw = strip.w / kBands; return {strip.x + b * cw + 4, strip.y, cw - 8, strip.h}; }
    Rect titleArea(int b) const { Rect c = column(b); return {c.x, c.y, c.w, 28}; }
    Rect profBtn(int b) const { Rect c = column(b); return {c.x + 10, c.y + 34, c.w - 20, 22}; }
    Rect modeBtn(int b) const { Rect c = column(b); return {c.x + 10, c.y + 60, c.w - 20, 22}; }
    Rect amtFader(int b) const { Rect c = column(b); return {c.x + 18, c.y + 108, 16, 84}; }
    Rect mixFader(int b) const { Rect c = column(b); return {c.x + 64, c.y + 108, 16, 84}; }
    Rect harmMeter(int b) const { Rect c = column(b); return {c.x + c.w - 28, c.y + 108, 10, 84}; }
    Rect soloBtn(int b) const { Rect c = column(b); return {c.x + 10, c.y + 222, (c.w - 26) / 2, 20}; }
    Rect bypBtn(int b) const { Rect c = column(b); int w = (c.w - 26) / 2; return {c.x + 16 + w, c.y + 222, w, 20}; }
    Rect crossHandle(int i) const {
        double f[kBands - 1];
        p->crossovers(f);
        int x = xOfFreq(f[i]);
        return {x - 8, spec.y + 4, 16, 24};
    }

    void tick() {
        uint32_t seq = p->meterSeq.load(std::memory_order_acquire);
        if (seq != lastSeq) {
            lastSeq = seq;
            for (int b = 0; b < kBands; ++b) {
                bandLvl[b] += (p->mBand[b].load() - bandLvl[b]) * 0.3f;
                addLvl[b] += (p->mAdded[b].load() - addLvl[b]) * 0.3f;
            }
        }
        uint32_t end = p->scopeWritten.load(std::memory_order_acquire);
        if (end < uint32_t(kFftN)) return;
        for (int i = 0; i < kFftN; ++i) {
            uint32_t k = (end - kFftN + i) & (kScopeLen - 1);
            bufO[i] = std::complex<double>(p->scopeOut[k] * window[i], 0);
            bufA[i] = std::complex<double>(p->scopeAdd[k] * window[i], 0);
        }
        fft(bufO);
        fft(bufA);
        for (int i = 1; i < kFftN / 2; ++i) {
            double o = 20 * std::log10(std::abs(bufO[i]) / (kFftN / 4.0) + 1e-9);
            double a = 20 * std::log10(std::abs(bufA[i]) / (kFftN / 4.0) + 1e-9);
            specOut[i] = float(o > specOut[i] ? o : specOut[i] + (o - specOut[i]) * 0.15);
            specAdd[i] = float(a > specAdd[i] ? a : specAdd[i] + (a - specAdd[i]) * 0.15);
        }
    }

    void drawBackground() {
        cv.vgradient({0, 0, W, H}, rgb(22, 20, 26), rgb(12, 11, 15));
        cv.vgradient({0, 0, W, 50}, rgb(40, 34, 44), rgb(26, 23, 31));
        cv.fill({0, 50, W, 1}, rgb(60, 52, 66));
        // logotipo: llama de tres colores
        cv.circle(28, 27, 9, rgb(255, 120, 60));
        cv.circle(28, 21, 6, rgb(255, 190, 80));
        cv.circle(28, 17, 3, rgb(255, 240, 170));
        cv.text(kFontBig, 46, 13, "AIE Exciter", kText);
        cv.text(kFontSmall, 46 + Canvas::textWidth(kFontBig, "AIE Exciter") + 12, 22,
                "AI Audio Engineer  ·  excitador armónico multibanda", kDim);
        char lat[64];
        std::snprintf(lat, sizeof lat, "Latencia %d muestras", ovs::kLatency);
        cv.text(kFontSmall, W - 18, 22, lat, kFaint, 2);
        cv.rounded({spec.x - 6, spec.y - 26, spec.w + 12, spec.h + 48}, 8, rgb(30, 27, 36));
        cv.text(kFontNormal, spec.x, spec.y - 22, "Espectro", kDim);
        cv.rounded({spec.x + 90, spec.y - 18, 10, 10}, 2, rgb(110, 104, 120));
        cv.text(kFontSmall, spec.x + 104, spec.y - 20, "Salida", kDim);
        cv.rounded({spec.x + 160, spec.y - 18, 10, 10}, 2, rgb(255, 170, 90));
        cv.text(kFontSmall, spec.x + 174, spec.y - 20, "Armónicos añadidos", kDim);
        cv.rounded({strip.x - 6, strip.y - 4, strip.w + 12, strip.h + 10}, 8, rgb(30, 27, 36));
        cv.rounded({graph.x - 8, 60, graph.w + 16, 496}, 8, rgb(30, 27, 36));
        cv.text(kFontNormal, btnOs[0].x, btnOs[0].y - 20, "Oversampling", kDim);
        cv.text(kFontNormal, outTrack.x, outTrack.y - 20, "Output", kDim);
        const double marks[] = {50, 100, 200, 500, 1000, 2000, 5000, 10000};
        const char* lab[] = {"50", "100", "200", "500", "1k", "2k", "5k", "10k"};
        for (int i = 0; i < 8; ++i) cv.text(kFontSmall, xOfFreq(marks[i]), spec.b() + 4, lab[i], kFaint, 1);
        background = cv.px;
    }

    void drawBtn(const Rect& r, const std::string& label, bool on, bool arrow = false) {
        cv.rounded(r, 5, on ? rgb(64, 196, 255) : rgb(46, 42, 54));
        if (arrow) {
            cv.text(kFontSmall, r.x + 8, r.y + 4, label, kText);
            int ax = r.r() - 12, ay = r.y + r.h / 2 - 2;
            cv.polyline({ax - 3.0, double(ax), ax + 3.0}, {double(ay), ay + 3.0, double(ay)}, 1.4, kDim);
        } else {
            cv.text(kFontSmall, r.x + r.w / 2, r.y + r.h / 2 - 7, label, on ? rgb(10, 14, 20) : kDim, 1);
        }
    }
    void drawFader(const Rect& f, float v, Color c, bool active, const char* label, const std::string& value) {
        cv.text(kFontSmall, f.x + f.w / 2, f.y - 18, label, kFaint, 1);
        cv.rounded({f.x + 5, f.y, 6, f.h}, 3, rgb(14, 12, 18));
        int ky = f.b() - int(v * f.h + 0.5);
        cv.rounded({f.x + 5, ky, 6, f.b() - ky}, 3, withAlpha(c, active ? 220 : 80));
        cv.rounded({f.x - 4, ky - 6, f.w + 8, 12}, 4, active ? rgb(226, 222, 236) : rgb(110, 104, 120));
        cv.text(kFontSmall, f.x + f.w / 2, f.b() + 7, value, active ? kText : kDim, 1);
    }

    void drawSpectrum(bool anySolo) {
        cv.fill(spec, rgb(13, 12, 17));
        for (int b = 0; b < kBands; ++b) {
            int x0, x1;
            bandSpan(b, x0, x1);
            bool active = (!anySolo || p->params[kSolo1 + b] >= 0.5f) && p->params[kByp1 + b] < 0.5f;
            cv.fill({x0, spec.y, x1 - x0, spec.h}, withAlpha(kBandColors[b], active ? 16 : 5));
            cv.fill({x0, spec.b() - 3, x1 - x0, 3}, withAlpha(kBandColors[b], active ? 210 : 70));
        }
        const double marks[] = {50, 100, 200, 500, 1000, 2000, 5000, 10000};
        for (double m : marks) cv.fill({xOfFreq(m), spec.y, 1, spec.h}, rgb(32, 30, 40));
        double fs = p->sampleRate;
        auto yOf = [&](double db) { return spec.y + std::min(1.0, std::max(0.0, -db / 90.0)) * spec.h; };
        std::vector<double> ax, ay;
        for (int x = 0; x < spec.w; ++x) {
            double fl = freqFromNorm(double(x) / spec.w), fh = freqFromNorm(double(x + 1) / spec.w);
            int k0 = std::max(1, int(fl * kFftN / fs)), k1 = std::max(k0 + 1, int(fh * kFftN / fs));
            k1 = std::min(k1, kFftN / 2);
            if (k0 >= kFftN / 2) break;
            float o = -120, a = -120;
            for (int k = k0; k < k1; ++k) { o = std::max(o, specOut[k]); a = std::max(a, specAdd[k]); }
            int yo = int(yOf(o + 6));
            if (yo < spec.b()) cv.fill({spec.x + x, yo, 1, spec.b() - yo}, rgb(66, 62, 76));
            ax.push_back(spec.x + x + 0.5);
            ay.push_back(yOf(a + 6));
        }
        size_t start = 0;
        for (int b = 0; b < kBands && start < ax.size(); ++b) {
            int x0, x1;
            bandSpan(b, x0, x1);
            std::vector<double> xs, ys;
            for (size_t i = start; i < ax.size() && ax[i] <= x1 + 1; ++i) { xs.push_back(ax[i]); ys.push_back(ay[i]); start = i; }
            cv.polyline(xs, ys, 1.6, mix(kBandColors[b], rgb(255, 240, 200), 0.3));
        }
        double f[kBands - 1];
        p->crossovers(f);
        for (int i = 0; i < kBands - 1; ++i) {
            int x = xOfFreq(f[i]);
            cv.fill({x, spec.y, 1, spec.h}, rgb(180, 172, 196));
            Rect h = crossHandle(i);
            cv.rounded(h, 4, dragKind == 1 && dragIndex == i ? kAccent : rgb(78, 70, 94));
            cv.fill({x - 2, h.y + 6, 1, 12}, rgb(220, 214, 232));
            cv.fill({x + 1, h.y + 6, 1, 12}, rgb(220, 214, 232));
            std::string t = p->paramText(kX1 + i);
            int tw = Canvas::textWidth(kFontSmall, t);
            int tx = std::min(spec.r() - tw - 4, std::max(spec.x + 4, x + 12));
            cv.rounded({tx - 4, h.y + 4, tw + 8, 16}, 4, withAlpha(rgb(13, 12, 17), 220));
            cv.text(kFontSmall, tx, h.y + 5, t, kText);
        }
    }

    void drawColumn(int b, bool anySolo) {
        Rect c = column(b);
        bool byp = p->params[kByp1 + b] >= 0.5f, so = p->params[kSolo1 + b] >= 0.5f;
        bool active = (!anySolo || so) && !byp;
        Color bc = kBandColors[b];
        cv.rounded(c, 6, b == selected ? rgb(44, 40, 54) : rgb(35, 32, 42));
        if (b == selected) cv.roundedOutline(c, 6, withAlpha(bc, 160));
        cv.fill({c.x, c.y, c.w, 3}, withAlpha(bc, active ? 230 : 80));
        char t[32];
        std::snprintf(t, sizeof t, "Banda %d", b + 1);
        cv.text(kFontNormal, c.x + 10, c.y + 8, t, active ? kText : kFaint);
        drawBtn(profBtn(b), p->paramText(kProf1 + b), false, true);
        drawBtn(modeBtn(b), p->paramText(kMode1 + b), false, true);
        drawFader(amtFader(b), p->params[kAmt1 + b], bc, active, "Amount", p->paramText(kAmt1 + b));
        drawFader(mixFader(b), p->params[kMix1 + b], bc, active, "Mix", p->paramText(kMix1 + b));
        // medidor de armónicos añadidos respecto a la banda (dB)
        Rect hm = harmMeter(b);
        cv.text(kFontSmall, hm.x + hm.w / 2, hm.y - 18, "Arm.", kFaint, 1);
        cv.rounded(hm, 3, rgb(14, 12, 18));
        double rel = bandLvl[b] > 1e-12 ? 10 * std::log10(std::max(1e-12f, addLvl[b]) / bandLvl[b]) : -60;
        double frac = std::min(1.0, std::max(0.0, (rel + 48) / 48));
        int hh = int(frac * hm.h);
        Color mc = rel > -12 ? kRed : (rel > -24 ? kYellow : rgb(255, 160, 80));
        cv.fill({hm.x + 1, hm.b() - hh, hm.w - 2, hh}, withAlpha(mc, active ? 230 : 80));
        char rs[16];
        if (rel < -59) std::snprintf(rs, sizeof rs, "-");
        else std::snprintf(rs, sizeof rs, "%.0f", rel);
        cv.text(kFontSmall, hm.x + hm.w / 2, hm.b() + 7, rs, active ? kText : kDim, 1);
        Rect sb = soloBtn(b), bb = bypBtn(b);
        cv.rounded(sb, 5, so ? withAlpha(kYellow, 90) : rgb(46, 42, 54));
        if (so) cv.roundedOutline(sb, 5, kYellow);
        cv.text(kFontSmall, sb.x + sb.w / 2, sb.y + 4, "Solo", so ? kText : kDim, 1);
        cv.rounded(bb, 5, byp ? withAlpha(kRed, 80) : rgb(46, 42, 54));
        if (byp) cv.roundedOutline(bb, 5, kRed);
        cv.text(kFontSmall, bb.x + bb.w / 2, bb.y + 4, "Bypass", byp ? kText : kDim, 1);
    }

    void drawGraph() {
        const Rect& g = graph;
        cv.rounded(g, 6, rgb(13, 12, 17));
        auto X = [&](double v) { return g.x + 10 + (v + 1) / 2 * (g.w - 20); };
        auto Y = [&](double v) { return g.b() - 10 - (v + 1) / 2 * (g.h - 20); };
        for (double v = -1; v <= 1.001; v += 0.5) {
            cv.fill({int(X(v)), g.y + 8, 1, g.h - 16}, rgb(34, 32, 42));
            cv.fill({g.x + 8, int(Y(v)), g.w - 16, 1}, rgb(34, 32, 42));
        }
        cv.line(X(-1), Y(-1), X(1), Y(1), 1.0, rgb(70, 66, 82));
        std::vector<double> xs, ys;
        double amt = p->params[kAmt1 + selected];
        for (int i = 0; i <= 160; ++i) {
            double x = -1 + 2.0 * i / 160;
            double y = x + (p->curve(selected, x) - x) * amt;
            xs.push_back(X(x));
            ys.push_back(Y(std::max(-1.2, std::min(1.2, y))));
        }
        cv.polyline(xs, ys, 2.2, kBandColors[selected]);
        char t[64];
        std::snprintf(t, sizeof t, "Banda %d: %s", selected + 1, p->paramText(kProf1 + selected).c_str());
        cv.text(kFontSmall, g.x + 10, g.y + 8, t, kDim);
    }

    void draw() {
        if (background.size() != cv.px.size()) drawBackground();
        else std::copy(background.begin(), background.end(), cv.px.begin());
        bool anySolo = false;
        for (int b = 0; b < kBands; ++b) anySolo = anySolo || p->params[kSolo1 + b] >= 0.5f;
        drawSpectrum(anySolo);
        for (int b = 0; b < kBands; ++b) drawColumn(b, anySolo);
        drawGraph();
        for (int i = 0; i < kNumOs; ++i) {
            char b[8];
            std::snprintf(b, sizeof b, "x%d", 1 << i);
            drawBtn(btnOs[i], b, p->osIdx() == i);
        }
        bool d = p->params[kDelta] >= 0.5f;
        cv.rounded(tglDelta, 6, d ? withAlpha(kRed, 70) : rgb(46, 42, 54));
        if (d) cv.roundedOutline(tglDelta, 6, kRed);
        cv.circle(tglDelta.x + 14, tglDelta.y + 15, 4.5, d ? kRed : kFaint);
        cv.text(kFontNormal, tglDelta.x + 26, tglDelta.y + 7, "Delta", d ? kText : kDim);
        cv.text(kFontSmall, tglDelta.r() + 10, tglDelta.y + 8, "solo lo añadido", kFaint);
        Rect tr = {outTrack.x, outTrack.y + 8, outTrack.w, 6};
        cv.rounded(tr, 3, rgb(14, 12, 18));
        int kx = tr.x + int(p->params[kOutput] * tr.w + 0.5);
        cv.fill({tr.x + tr.w / 2, tr.y - 3, 1, 12}, rgb(90, 86, 104));
        cv.circle(kx, tr.y + 3, 7, rgb(226, 222, 236));
        cv.text(kFontNormal, outTrack.r() + 12, outTrack.y + 3, p->paramText(kOutput), kText);
        cv.text(kFontSmall, graph.x, 484, "Clic en el nombre de una banda para", kFaint);
        cv.text(kFontSmall, graph.x, 500, "ver su curva. Amount 0 = sin efecto.", kFaint);
        cv.text(kFontSmall, graph.x, 520, "Arm. = armónicos añadidos (dB).", kFaint);
    }

    // ---- ratón ----
    int popup(const std::vector<std::string>& items, int checked, int sx, int sy) {
        return menu ? menu(menuCtx, sx, sy, items, checked) : -1;
    }
    bool mouseDown(int x, int y, bool dbl, bool, int sx, int sy) {
        for (int i = 0; i < kBands - 1; ++i) {
            Rect h = crossHandle(i);
            if (Rect{h.x - 4, h.y - 4, h.w + 8, spec.h}.contains(x, y)) {
                if (dbl) { static const double def[3] = {120, 1000, 6000}; p->setParamFromEditor(kX1 + i, float(normFromFreq(def[i]))); return true; }
                startDrag(1, i, x, y, p->params[kX1 + i], kX1 + i);
                return true;
            }
        }
        for (int b = 0; b < kBands; ++b) {
            if (titleArea(b).contains(x, y)) { selected = b; return true; }
            if (profBtn(b).contains(x, y)) {
                selected = b;
                std::vector<std::string> items(kProfileNames, kProfileNames + kNumProfiles);
                int s = popup(items, p->profileOf(b), sx, sy);
                if (s >= 0) p->setParamFromEditor(kProf1 + b, float(s) / (kNumProfiles - 1));
                return true;
            }
            if (modeBtn(b).contains(x, y)) {
                std::vector<std::string> items(kModeNames, kModeNames + kNumModes);
                int s = popup(items, p->modeOf(b), sx, sy);
                if (s >= 0) p->setParamFromEditor(kMode1 + b, float(s) / (kNumModes - 1));
                return true;
            }
            Rect fa = amtFader(b), fm = mixFader(b);
            if (Rect{fa.x - 10, fa.y - 8, fa.w + 20, fa.h + 16}.contains(x, y)) {
                selected = b;
                if (dbl) { p->setParamFromEditor(kAmt1 + b, 0.0f); return true; }
                startDrag(2, b, x, y, p->params[kAmt1 + b], kAmt1 + b);
                return true;
            }
            if (Rect{fm.x - 10, fm.y - 8, fm.w + 20, fm.h + 16}.contains(x, y)) {
                if (dbl) { p->setParamFromEditor(kMix1 + b, 1.0f); return true; }
                startDrag(3, b, x, y, p->params[kMix1 + b], kMix1 + b);
                return true;
            }
            if (soloBtn(b).contains(x, y)) { toggle(kSolo1 + b); return true; }
            if (bypBtn(b).contains(x, y)) { toggle(kByp1 + b); return true; }
        }
        for (int i = 0; i < kNumOs; ++i)
            if (btnOs[i].contains(x, y)) { p->setParamFromEditor(kOversampling, float(i) / (kNumOs - 1)); return true; }
        if (tglDelta.contains(x, y)) { toggle(kDelta); return true; }
        if (Rect{outTrack.x - 10, outTrack.y, outTrack.w + 20, outTrack.h}.contains(x, y)) {
            if (dbl) { p->setParamFromEditor(kOutput, 0.5f); return true; }
            startDrag(4, 0, x, y, p->params[kOutput], kOutput);
            return true;
        }
        return false;
    }
    void toggle(int param) { p->setParamFromEditor(param, p->params[param] >= 0.5f ? 0.0f : 1.0f); }
    int dragParamId = -1;
    void startDrag(int kind, int idx, int x, int y, float v, int param) {
        dragKind = kind; dragIndex = idx; dragX = x; dragY = y; dragV = v; dragParamId = param;
        if (p->host) p->host(&p->fx, vst::audioMasterBeginEdit, param, 0, nullptr, 0.0f);
    }
    bool mouseMove(int x, int y, bool fine) {
        if (!dragKind) return false;
        float k = fine ? 0.1f : 1.0f, v;
        if (dragKind == 1) v = dragV + float(x - dragX) / spec.w * k;
        else if (dragKind == 4) v = dragV + float(x - dragX) / outTrack.w * k;
        else v = dragV + float(dragY - y) / amtFader(dragIndex).h * k;
        p->setParamFromEditor(dragParamId, v, false, false);
        return true;
    }
    bool mouseUp() {
        if (!dragKind) return false;
        if (p->host) p->host(&p->fx, vst::audioMasterEndEdit, dragParamId, 0, nullptr, 0.0f);
        dragKind = 0;
        return true;
    }
    bool wheel(int x, int y, int steps, bool fine) {
        for (int b = 0; b < kBands; ++b) {
            Rect fa = amtFader(b);
            if (Rect{fa.x - 10, fa.y - 8, fa.w + 20, fa.h + 16}.contains(x, y)) {
                p->setParamFromEditor(kAmt1 + b, p->params[kAmt1 + b] + steps * (fine ? 0.005f : 0.02f));
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
typedef win::EditorWindow<gui::ExciterEditor> ExciterWindow;
static const char* const kWndClass = "AIEExciterEditorWindow";
static int gWindows = 0;
struct ExciterUi {
    gui::ExciterEditor ed;
    ExciterWindow* win = nullptr;
    explicit ExciterUi(AIEExciter* p) : ed(p) {}
};
static void closeUi(AIEExciter* p) {
    ExciterUi* ui = static_cast<ExciterUi*>(p->window);
    if (!ui) return;
    ExciterWindow::close(ui->win, kWndClass, gWindows);
    delete ui;
    p->window = nullptr;
}
#endif

static AIEExciter* self(AEffect* e) { return static_cast<AIEExciter*>(e->object); }

static VstIntPtr VSTCALL dispatcher(AEffect* e, int32_t op, int32_t idx, VstIntPtr val, void* ptr, float opt) {
    AIEExciter* p = self(e);
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
        case effGetEffectName: copyStr(ptr, "AIE Exciter", 32); return 1;
        case effGetProductString: copyStr(ptr, "AIE Exciter", 64); return 1;
        case effGetVendorString: copyStr(ptr, "AI Audio Engineer", 64); return 1;
        case effGetVendorVersion: return 1000;
        case effGetPlugCategory: return kPlugCategEffect;
        case effGetVstVersion: return 2400;
        case effGetTailSize: return ovs::kLatency + 1;
        case effCanDo: return 0;
#if defined(_WIN32)
        case effEditGetRect: {
            static ERect r = {0, 0, int16_t(gui::ExciterEditor::H), int16_t(gui::ExciterEditor::W)};
            if (ptr) *static_cast<ERect**>(ptr) = &r;
            return 1;
        }
        case effEditOpen: {
            ExciterUi* ui = static_cast<ExciterUi*>(p->window);
            if (!ui) { ui = new ExciterUi(p); p->window = ui; }
            return ExciterWindow::open(ui->win, &ui->ed, static_cast<HWND>(ptr), kWndClass, gWindows) ? 1 : 0;
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
    float a[256], b[256];
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
    AIEExciter* p = new AIEExciter(host);
    p->fx.dispatcher = dispatcher;
    p->fx.setParameter = setParameter;
    p->fx.getParameter = getParameter;
    p->fx.processReplacing = processReplacing;
    p->fx.processDoubleReplacing = processDouble;
    p->fx.process = processAccumulating;
    return &p->fx;
}
