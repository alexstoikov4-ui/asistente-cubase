// AIE Clip — clipper con sobremuestreo para Cubase 5 (VST 2.4, 32 y 64 bits).
//
// Implementa la interfaz binaria VST 2.x directamente (estructura AEffect y códigos de operación
// estándar), sin depender del SDK de Steinberg. Interfaz gráfica propia (gui.h) dibujada por software;
// en Windows se muestra en una ventana hija de la que da Cubase.
//
// Parámetros: Input (Gain), Clip (umbral), Saturación, Oversampling (1x-32x), Output, Mix,
// Ceiling (recorte final de seguridad), Delta, Modo.

#include <cmath>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <algorithm>
#include <atomic>
#include <string>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#include "gui.h"

#if defined(_WIN32)
#define VSTCALL __cdecl
#define VSTEXPORT extern "C" __declspec(dllexport)
#else
#define VSTCALL
#define VSTEXPORT extern "C" __attribute__((visibility("default")))
#endif

typedef intptr_t VstIntPtr;
struct AEffect;
typedef VstIntPtr(VSTCALL* audioMasterCallback)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
typedef VstIntPtr(VSTCALL* AEffectDispatcherProc)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
typedef void(VSTCALL* AEffectProcessProc)(AEffect*, float**, float**, int32_t);
typedef void(VSTCALL* AEffectProcessDoubleProc)(AEffect*, double**, double**, int32_t);
typedef void(VSTCALL* AEffectSetParameterProc)(AEffect*, int32_t, float);
typedef float(VSTCALL* AEffectGetParameterProc)(AEffect*, int32_t);

struct AEffect {
    int32_t magic;
    AEffectDispatcherProc dispatcher;
    AEffectProcessProc process;
    AEffectSetParameterProc setParameter;
    AEffectGetParameterProc getParameter;
    int32_t numPrograms;
    int32_t numParams;
    int32_t numInputs;
    int32_t numOutputs;
    int32_t flags;
    VstIntPtr resvd1;
    VstIntPtr resvd2;
    int32_t initialDelay;
    int32_t realQualities;
    int32_t offQualities;
    float ioRatio;
    void* object;
    void* user;
    int32_t uniqueID;
    int32_t version;
    AEffectProcessProc processReplacing;
    AEffectProcessDoubleProc processDoubleReplacing;
    char future[56];
};

enum {
    effOpen = 0, effClose = 1, effSetProgram = 2, effGetProgram = 3, effSetProgramName = 4, effGetProgramName = 5,
    effGetParamLabel = 6, effGetParamDisplay = 7, effGetParamName = 8, effSetSampleRate = 10, effSetBlockSize = 11,
    effMainsChanged = 12, effEditGetRect = 13, effEditOpen = 14, effEditClose = 15, effEditIdle = 19,
    effGetProgramNameIndexed = 29, effGetPlugCategory = 35,
    effGetEffectName = 45, effGetVendorString = 47, effGetProductString = 48, effGetVendorVersion = 49,
    effCanDo = 51, effGetTailSize = 52, effGetParameterProperties = 56, effGetVstVersion = 58
};
enum { audioMasterAutomate = 0, audioMasterVersion = 1, audioMasterBeginEdit = 43, audioMasterEndEdit = 44 };
enum { effFlagsHasEditor = 1 << 0, effFlagsCanReplacing = 1 << 4, effFlagsCanDoubleReplacing = 1 << 12 };
struct ERect { int16_t top, left, bottom, right; };
static const int32_t kEffectMagic = 0x56737450;  // 'VstP'
static const int32_t kUniqueId = 0x41494563;     // 'AIEc'

// (kCeiling es el umbral "Clip"; kSafety es el interruptor "Ceiling": se conservan los índices de la 1.0)
enum Param { kInput = 0, kCeiling, kSoftness, kOversampling, kOutput, kMix, kSafety, kDelta, kMode, kNumParams };
enum Mode { kClassic = 0, kPro, kHard, kRatio, kNumModes };
static const char* const kModeNames[kNumModes] = {"Soft Clip Classic", "Soft Clip Pro", "Hard Clip", "Ratio 2:1"};
static const int kNumOs = 6;
static const int kOsFactors[kNumOs] = {1, 2, 4, 8, 16, 32};

// Sobremuestreo en cascada de etapas x2 con filtros de media banda (half-band) de fase lineal.
// La primera etapa es la exigente (banda de paso hasta 20 kHz a 44,1 kHz, ~90 dB de rechazo); las
// siguientes trabajan a más frecuencia y les basta con filtros cortos. Las longitudes cumplen
// (L-1)/2 divisible por 2^etapa, así el retardo de cada factor es un número entero de muestras.
static const int kStages = 5;
static const int kStageLen[kStages] = {123, 25, 17, 17, 33};
static const double kKaiserBeta = 9.0;
static const int kBuf = 128;  // potencia de 2 mayor que cualquier longitud de filtro
static const int kMask = kBuf - 1;
// latencia de cada factor = suma de (L-1)/2 / 2^etapa; la del 32x es la mayor y se usa para todos
static int stageLatency(int stages) {
    int num = 0;  // en unidades de 1/2^(kStages-1) muestras
    for (int s = 0; s < stages; ++s) num += ((kStageLen[s] - 1) / 2) << (kStages - 1 - s);
    return num >> (kStages - 1);
}
static const int kLatency = 71;  // = stageLatency(kStages); se comprueba al crear el plugin

static void copyStr(void* dst, const char* src, size_t max) {
    char* d = static_cast<char*>(dst);
    std::strncpy(d, src, max - 1);
    d[max - 1] = 0;
}

// Curvas de recorte (umbral T, saturación k de 0 a 1). Todas son lineales por debajo de la rodilla.
//  Classic: rodilla tanh desde T*(1-k), se acerca al techo de forma asintótica.
//  Pro:     rodilla cuadrática (derivada continua) que llega exactamente al techo: más transparente.
//  Hard:    recorte duro en T.
//  Ratio:   por encima de la rodilla, pendiente 1/2 hasta llegar a T (compresión estática 2:1 + techo).
static inline double clipSample(double x, double T, double k, int mode) {
    double a = std::fabs(x), y;
    if (mode == kHard || (k <= 1e-6 && mode != kRatio)) return x > T ? T : (x < -T ? -T : x);
    if (mode == kRatio) {
        double knee = T * (1.0 - std::max(k, 0.02) * 0.75);  // con k=1 la rodilla queda 12 dB por debajo
        if (a <= knee) return x;
        y = std::min(T, knee + (a - knee) * 0.5);
    } else if (mode == kPro) {
        double knee = T * (1.0 - k), w = T - knee;  // tramo cuadrático de knee a knee + 2w
        if (a <= knee) return x;
        y = a >= knee + 2 * w ? T : a - (a - knee) * (a - knee) / (4 * w);
    } else {
        double knee = T * (1.0 - k);
        if (a <= knee) return x;
        // tanh(z) = 1 - 2/(e^(2z)+1): mismo resultado que std::tanh para z >= 0 y bastante más rápido
        y = knee + T * k * (1.0 - 2.0 / (std::exp(2.0 * (a - knee) / (T * k)) + 1.0));
    }
    return x < 0 ? -y : y;
}

// Filtro de media banda (corte en 1/4 de la frecuencia alta) enventanado con Kaiser. En un filtro de
// media banda la mitad de los coeficientes son cero: una fase es solo el coeficiente central (un retardo)
// y la otra es densa. Se guarda así para hacer productos escalares contiguos (vectorizables).
static const int kHist = 64;  // historial por fase (potencia de 2 >= mitad del filtro más largo)
struct HalfBand {
    int L = 0, c = 0, pc = 0;    // longitud, índice central y su fase (paridad)
    double center2 = 0;          // 2*h[c] (subida)
    double center = 0;           // h[c] (bajada)
    int centerDelay = 0;         // retardo en muestras bajas del coeficiente central
    std::vector<double> dense;   // h[q + 2t] con q = 1 - pc
    std::vector<double> dense2;  // 2*h[q + 2t] (subida)

    void design(int len) {
        L = len; c = (L - 1) / 2; pc = c % 2;
        std::vector<double> h(L, 0.0);
        const double pi = 3.14159265358979323846;
        auto besselI0 = [](double x) {
            double sum = 1.0, term = 1.0;
            for (int k = 1; k < 40; ++k) { term *= (x / (2.0 * k)) * (x / (2.0 * k)); sum += term; }
            return sum;
        };
        double i0b = besselI0(kKaiserBeta), sum = 0;
        for (int i = 0; i < L; ++i) {
            int m = i - c;
            double s = (m == 0) ? 0.5 : ((m % 2 == 0) ? 0.0 : std::sin(pi * m / 2.0) / (pi * m));
            double r = 2.0 * i / (L - 1) - 1.0;
            h[i] = s * besselI0(kKaiserBeta * std::sqrt(std::fmax(0.0, 1 - r * r))) / i0b;
            sum += h[i];
        }
        for (auto& v : h) v /= sum;  // ganancia unitaria en continua
        center = h[c]; center2 = 2.0 * h[c];
        centerDelay = (c - pc) / 2;
        dense.clear(); dense2.clear();
        for (int i = 1 - pc; i < L; i += 2) { dense.push_back(h[i]); dense2.push_back(2.0 * h[i]); }
    }
};

// Historial con copia espejo: buf[pos + t] es la muestra de hace t pasos, siempre contigua.
struct Hist {
    double buf[2 * kHist];
    int pos;
    void clear() { std::memset(buf, 0, sizeof buf); pos = 0; }
    inline void push(double v) { pos = (pos - 1) & (kHist - 1); buf[pos] = buf[pos + kHist] = v; }
    inline const double* at() const { return buf + pos; }
};

static inline double dot(const double* a, const double* b, int n) {
    double s0 = 0, s1 = 0;
    int i = 0;
    for (; i + 1 < n; i += 2) { s0 += a[i] * b[i]; s1 += a[i + 1] * b[i + 1]; }
    if (i < n) s0 += a[i] * b[i];
    return s0 + s1;
}

struct Channel {
    Hist up[kStages], down0[kStages], down1[kStages];
    double dry[kBuf], wet[kBuf];
    int dryPos, wetPos;
    void clearFilters() {
        for (int s = 0; s < kStages; ++s) { up[s].clear(); down0[s].clear(); down1[s].clear(); }
        std::memset(wet, 0, sizeof wet);
        wetPos = 0;
    }
    void clearAll() { clearFilters(); std::memset(dry, 0, sizeof dry); dryPos = 0; }
};

class AIEClip {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    int osIndex = -1, stages = 0, pad = kLatency, latency = kLatency, mode = kClassic;
    // medidores para la interfaz: el audio guarda el máximo y la interfaz lo recoge y pone a cero
    std::atomic<float> mInPeak[2], mOutPeak[2], mGr;
    void* editor = nullptr;  // ventana abierta (solo Windows)
    // Filtros calculados al crear el plugin: cambiar el sobremuestreo durante la reproducción no
    // reserva memoria ni cambia la latencia (no hace falta avisar a Cubase).
    HalfBand hb[kStages];
    Channel ch[2];

    explicit AIEClip(audioMasterCallback cb) : host(cb) {
        params[kInput] = paramFromInputDb(0.0);
        params[kCeiling] = paramFromCeilingDb(-0.3);
        params[kSoftness] = 0.3f;
        params[kOversampling] = 2.0f / (kNumOs - 1);  // 4x
        params[kOutput] = paramFromOutputDb(0.0);
        params[kMix] = 1.0f;
        params[kSafety] = 1.0f;
        params[kDelta] = 0.0f;
        params[kMode] = 0.0f;
        for (int c = 0; c < 2; ++c) { mInPeak[c] = 0; mOutPeak[c] = 0; }
        mGr = 0;
        fx.magic = kEffectMagic;
        fx.numPrograms = 1;
        fx.numParams = kNumParams;
        fx.numInputs = 2;
        fx.numOutputs = 2;
        fx.flags = effFlagsCanReplacing | effFlagsCanDoubleReplacing;
#if defined(_WIN32)
        fx.flags |= effFlagsHasEditor;
#endif
        fx.object = this;
        fx.uniqueID = kUniqueId;
        fx.version = 1200;
        fx.ioRatio = 1.0f;
        for (int s = 0; s < kStages; ++s) hb[s].design(kStageLen[s]);
        latency = stageLatency(kStages);  // 71: igual a kLatency
        fx.initialDelay = latency;
        for (auto& c : ch) c.clearAll();
        configure();
    }

    // conversiones de parámetros normalizados (0..1) a unidades reales
    static float paramFromInputDb(double db) { return float((db + 12.0) / 36.0); }      // -12..+24 dB
    static float paramFromCeilingDb(double db) { return float((db + 24.0) / 24.0); }   // -24..0 dB
    static float paramFromOutputDb(double db) { return float((db + 24.0) / 36.0); }    // -24..+12 dB
    double inputDb() const { return -12.0 + 36.0 * params[kInput]; }
    double ceilingDb() const { return -24.0 + 24.0 * params[kCeiling]; }
    double outputDb() const { return -24.0 + 36.0 * params[kOutput]; }
    int modeIdx() const { int m = int(params[kMode] * (kNumModes - 1) + 0.5f); return m < 0 ? 0 : (m >= kNumModes ? kNumModes - 1 : m); }
    int osIdx() const {
        int i = int(params[kOversampling] * (kNumOs - 1) + 0.5f);
        return i < 0 ? 0 : (i > kNumOs - 1 ? kNumOs - 1 : i);
    }

    // Se llama desde el hilo de audio: sin reservar memoria ni llamar al anfitrión.
    void configure() {
        int idx = osIdx();
        if (idx == osIndex) return;
        osIndex = idx;
        stages = idx;  // 2^stages = factor
        pad = latency - stageLatency(stages);
        for (auto& c : ch) c.clearFilters();
    }

    void reset() {
        for (auto& c : ch) c.clearAll();
        osIndex = -1;
        configure();
    }

    // Etapa s: sube x2, procesa las etapas interiores (o recorta en la última) y baja x2.
    double stage(Channel& c, int s, double x, double T, double k) {
        if (s == stages) return clipSample(x, T, k, mode);
        const HalfBand& f = hb[s];
        const int nd = int(f.dense.size());
        Hist& uh = c.up[s];
        uh.push(x);
        // subida: y[2n+p] = 2 * sum_t h[p+2t] x[n-t]
        double uc = f.center2 * uh.at()[f.centerDelay];
        double ud = dot(f.dense2.data(), uh.at(), nd);
        double u0 = f.pc == 0 ? uc : ud, u1 = f.pc == 0 ? ud : uc;
        double v0 = stage(c, s + 1, u0, T, k);
        double v1 = stage(c, s + 1, u1, T, k);
        // bajada con la muestra par v0[n] como la más reciente: z = sum_i h[i] u[2n-i];
        // i par -> v0[n - i/2], i impar -> v1[n - 1 - (i-1)/2] (v1[n] aún no ha entrado)
        Hist& d0 = c.down0[s];
        Hist& d1 = c.down1[s];
        d0.push(v0);
        double z;
        if (f.pc == 0) z = f.center * d0.at()[f.centerDelay] + dot(f.dense.data(), d1.at(), nd);
        else z = dot(f.dense.data(), d0.at(), nd) + f.center * d1.at()[f.centerDelay];
        d1.push(v1);
        return z;
    }

    // Devuelve la señal procesada; en 'dry' deja la entrada retrasada la misma latencia.
    double processOne(Channel& c, double x, double gin, double T, double k, double& dry) {
        c.dryPos = (c.dryPos + 1) & kMask;
        c.dry[c.dryPos] = x;
        dry = c.dry[(c.dryPos - latency) & kMask];
        double y = stage(c, 0, x * gin, T, k);
        // los factores más bajos tienen menos retardo: se completa hasta la latencia fija
        c.wetPos = (c.wetPos + 1) & kMask;
        c.wet[c.wetPos] = y;
        return c.wet[(c.wetPos - pad) & kMask];
    }

    template <typename T_>
    void run(T_** in, T_** out, int32_t frames) {
        configure();
        mode = modeIdx();
        double gin = std::pow(10.0, inputDb() / 20.0);
        double gout = std::pow(10.0, outputDb() / 20.0);
        double T = std::pow(10.0, ceilingDb() / 20.0);
        double k = params[kSoftness];
        double mix = params[kMix];
        bool safety = params[kSafety] >= 0.5f;
        bool delta = params[kDelta] >= 0.5f;
        double grDb = 0;
        for (int c = 0; c < 2; ++c) {
            double inPk = 0, dryPk = 0, wetPk = 0, outPk = 0;
            for (int32_t i = 0; i < frames; ++i) {
                double x = in[c][i];
                Channel& chan = ch[c];
                double dry;
                double wet = processOne(chan, x, gin, T, k, dry);
                if (safety) wet = wet > T ? T : (wet < -T ? -T : wet);
                // Delta: escuchar solo lo que el clipper quita (para ajustar Input de oído)
                double y = delta ? (dry * gin - wet) * gout : (mix * wet + (1.0 - mix) * dry) * gout;
                out[c][i] = T_(y);
                inPk = std::max(inPk, std::fabs(x * gin));
                dryPk = std::max(dryPk, std::fabs(dry * gin));  // entrada alineada con la salida (misma latencia)
                wetPk = std::max(wetPk, std::fabs(wet));
                outPk = std::max(outPk, std::fabs(y));
            }
            if (dryPk > 1e-9 && wetPk > 1e-12) grDb = std::max(grDb, 20.0 * std::log10(dryPk / wetPk));
            if (float(inPk) > mInPeak[c].load(std::memory_order_relaxed)) mInPeak[c].store(float(inPk), std::memory_order_relaxed);
            if (float(outPk) > mOutPeak[c].load(std::memory_order_relaxed)) mOutPeak[c].store(float(outPk), std::memory_order_relaxed);
        }
        if (float(grDb) > mGr.load(std::memory_order_relaxed)) mGr.store(float(grDb), std::memory_order_relaxed);
    }

    gui::Meters takeMeters() {
        gui::Meters m;
        for (int c = 0; c < 2; ++c) {
            m.inPeak[c] = mInPeak[c].exchange(0.0f);
            m.outPeak[c] = mOutPeak[c].exchange(0.0f);
        }
        m.gr = mGr.exchange(0.0f);
        return m;
    }
    double curveAt(double x) { return clipSample(x, std::pow(10.0, ceilingDb() / 20.0), params[kSoftness], modeIdx()); }

    void paramName(int i, char* s) {
        static const char* n[kNumParams] = {"Gain", "Clip", "Satur", "OverSmp", "Output", "Mix", "Ceiling", "Delta", "Mode"};
        copyStr(s, i < kNumParams ? n[i] : "", 9);
    }
    void paramLabel(int i, char* s) {
        static const char* l[kNumParams] = {"dB", "dB", "%", "", "dB", "%", "", "", ""};
        copyStr(s, i < kNumParams ? l[i] : "", 9);
    }
    void paramDisplay(int i, char* s) {
        char b[32];
        switch (i) {
            case kInput: std::snprintf(b, sizeof b, "%+.1f", inputDb()); break;
            case kCeiling: std::snprintf(b, sizeof b, "%.1f", ceilingDb()); break;
            case kSoftness: std::snprintf(b, sizeof b, "%.0f", params[kSoftness] * 100.0); break;
            case kOversampling: std::snprintf(b, sizeof b, "%dx", kOsFactors[osIdx()]); break;
            case kOutput: std::snprintf(b, sizeof b, "%+.1f", outputDb()); break;
            case kMix: std::snprintf(b, sizeof b, "%.0f", params[kMix] * 100.0); break;
            case kSafety: std::snprintf(b, sizeof b, "%s", params[kSafety] >= 0.5f ? "On" : "Off"); break;
            case kDelta: std::snprintf(b, sizeof b, "%s", params[kDelta] >= 0.5f ? "On" : "Off"); break;
            case kMode: { static const char* sh[kNumModes] = {"Classic", "Pro", "Hard", "2:1"}; std::snprintf(b, sizeof b, "%s", sh[modeIdx()]); break; }
            default: b[0] = 0;
        }
        copyStr(s, b, 9);
    }
    // texto largo para la interfaz propia
    std::string paramTextLong(int i) {
        char b[48];
        switch (i) {
            case kInput: std::snprintf(b, sizeof b, "%+.1f dB", inputDb()); break;
            case kCeiling: std::snprintf(b, sizeof b, "%.1f dB", ceilingDb()); break;
            case kOutput: std::snprintf(b, sizeof b, "%+.1f dB", outputDb()); break;
            case kSoftness: case kMix: std::snprintf(b, sizeof b, "%.0f %%", params[i] * 100.0); break;
            case kOversampling: std::snprintf(b, sizeof b, "x%d", kOsFactors[osIdx()]); break;
            case kMode: return kModeNames[modeIdx()];
            default: { char t[16]; paramDisplay(i, t); return t; }
        }
        return b;
    }
};

// Puente entre la interfaz (gui.h) y el plugin. Los cambios desde la interfaz se comunican al anfitrión
// (automatización); las lecturas son directas.
struct PluginEditorHost : gui::EditorHost {
    AIEClip* p;
    explicit PluginEditorHost(AIEClip* plug) : p(plug) {}
    float getParam(int i) override { return (i >= 0 && i < kNumParams) ? p->params[i] : 0.0f; }
    void beginEdit(int i) override { if (p->host) p->host(&p->fx, audioMasterBeginEdit, i, 0, nullptr, 0.0f); }
    void setParam(int i, float v) override {
        if (i < 0 || i >= kNumParams) return;
        v = v < 0 ? 0 : (v > 1 ? 1 : v);
        p->params[i] = v;
        if (p->host) p->host(&p->fx, audioMasterAutomate, i, 0, nullptr, v);
    }
    void endEdit(int i) override { if (p->host) p->host(&p->fx, audioMasterEndEdit, i, 0, nullptr, 0.0f); }
    std::string paramText(int i) override { return p->paramTextLong(i); }
    gui::Meters takeMeters() override { return p->takeMeters(); }
    int popupMenu(int, int, const std::vector<std::string>&, int) override { return -1; }
    double curve(double x) override { return p->curveAt(x); }
    double ceilingLinear() override { return std::pow(10.0, p->ceilingDb() / 20.0); }
    int latency() override { return p->latency; }
};

#if defined(_WIN32)
// ---- ventana de Windows: copia el lienzo a pantalla y pasa ratón y temporizador a la interfaz ----
static const char* const kWndClass = "AIEClipEditorWindow";
static int gWindows = 0;

static HINSTANCE dllInstance() {
    HMODULE m = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&dllInstance), &m);
    return m;
}

struct WinEditor : PluginEditorHost {
    HWND hwnd = nullptr;
    gui::Editor ed;
    explicit WinEditor(AIEClip* plug) : PluginEditorHost(plug), ed(this) {}
    int popupMenu(int x, int y, const std::vector<std::string>& items, int checked) override {
        HMENU m = CreatePopupMenu();
        for (size_t i = 0; i < items.size(); ++i)
            AppendMenuA(m, MF_STRING | (int(i) == checked ? MF_CHECKED : 0), i + 1, items[i].c_str());
        int r = TrackPopupMenu(m, TPM_RETURNCMD | TPM_NONOTIFY | TPM_LEFTALIGN | TPM_TOPALIGN, x, y, 0, hwnd, nullptr);
        DestroyMenu(m);
        return r > 0 ? r - 1 : -1;
    }
};

static void paintTo(WinEditor* w, HDC dc) {
    w->ed.draw();
    BITMAPINFO bi;
    std::memset(&bi, 0, sizeof bi);
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = gui::Editor::W;
    bi.bmiHeader.biHeight = -gui::Editor::H;  // de arriba abajo
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    SetDIBitsToDevice(dc, 0, 0, gui::Editor::W, gui::Editor::H, 0, 0, 0, gui::Editor::H, w->ed.cv.px.data(), &bi,
                      DIB_RGB_COLORS);
}

static LRESULT CALLBACK editorProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    WinEditor* w = reinterpret_cast<WinEditor*>(GetWindowLongPtrA(h, GWLP_USERDATA));
    switch (msg) {
        case WM_CREATE: {
            CREATESTRUCTA* cs = reinterpret_cast<CREATESTRUCTA*>(lp);
            SetWindowLongPtrA(h, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(cs->lpCreateParams));
            SetTimer(h, 1, 33, nullptr);
            return 0;
        }
        case WM_TIMER:
            if (w) { w->ed.tickMeters(); InvalidateRect(h, nullptr, FALSE); }
            return 0;
        case WM_ERASEBKGND: return 1;
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            if (w) paintTo(w, dc);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_PRINTCLIENT:  // capturas (PrintWindow)
            if (w) paintTo(w, reinterpret_cast<HDC>(wp));
            return 0;
        case WM_LBUTTONDOWN:
        case WM_LBUTTONDBLCLK: {
            if (!w) break;
            POINT pt = {(short)LOWORD(lp), (short)HIWORD(lp)};
            POINT sp = pt;
            ClientToScreen(h, &sp);
            bool fine = (wp & (MK_SHIFT | MK_CONTROL)) != 0;
            bool redraw = w->ed.mouseDown(pt.x, pt.y, msg == WM_LBUTTONDBLCLK, fine, sp.x, sp.y);
            if (w->ed.dragSlider >= 0) SetCapture(h);
            if (redraw) InvalidateRect(h, nullptr, FALSE);
            return 0;
        }
        case WM_MOUSEMOVE:
            if (w && w->ed.mouseMove((short)LOWORD(lp), (short)HIWORD(lp), (wp & (MK_SHIFT | MK_CONTROL)) != 0))
                InvalidateRect(h, nullptr, FALSE);
            return 0;
        case WM_LBUTTONUP:
            if (w && w->ed.dragSlider >= 0) { ReleaseCapture(); }
            if (w && w->ed.mouseUp()) InvalidateRect(h, nullptr, FALSE);
            return 0;
        case WM_CAPTURECHANGED:
            if (w) w->ed.mouseUp();
            return 0;
        case WM_MOUSEWHEEL: {
            if (!w) break;
            POINT pt = {(short)LOWORD(lp), (short)HIWORD(lp)};
            ScreenToClient(h, &pt);
            int steps = GET_WHEEL_DELTA_WPARAM(wp) / WHEEL_DELTA;
            if (w->ed.wheel(pt.x, pt.y, steps, (GET_KEYSTATE_WPARAM(wp) & (MK_SHIFT | MK_CONTROL)) != 0))
                InvalidateRect(h, nullptr, FALSE);
            return 0;
        }
        case WM_DESTROY:
            KillTimer(h, 1);
            return 0;
    }
    return DefWindowProcA(h, msg, wp, lp);
}

static bool openEditor(AIEClip* p, HWND parent) {
    if (p->editor) return true;
    HINSTANCE inst = dllInstance();
    if (gWindows == 0) {
        WNDCLASSA wc;
        std::memset(&wc, 0, sizeof wc);
        wc.style = CS_DBLCLKS;
        wc.lpfnWndProc = editorProc;
        wc.hInstance = inst;
        wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wc.lpszClassName = kWndClass;
        RegisterClassA(&wc);
    }
    WinEditor* w = new WinEditor(p);
    w->hwnd = CreateWindowExA(0, kWndClass, "AIE Clip", WS_CHILD | WS_VISIBLE, 0, 0, gui::Editor::W, gui::Editor::H,
                              parent, nullptr, inst, w);
    if (!w->hwnd) {
        delete w;
        if (gWindows == 0) UnregisterClassA(kWndClass, inst);
        return false;
    }
    ++gWindows;
    p->editor = w;
    return true;
}

static void closeEditor(AIEClip* p) {
    WinEditor* w = static_cast<WinEditor*>(p->editor);
    if (!w) return;
    DestroyWindow(w->hwnd);
    delete w;
    p->editor = nullptr;
    // se borra la clase al cerrar la última ventana: si Cubase descarga la DLL no queda una clase
    // registrada apuntando a código que ya no existe
    if (--gWindows == 0) UnregisterClassA(kWndClass, dllInstance());
}
#endif

static AIEClip* self(AEffect* e) { return static_cast<AIEClip*>(e->object); }

static VstIntPtr VSTCALL dispatcher(AEffect* e, int32_t op, int32_t idx, VstIntPtr val, void* ptr, float opt) {
    AIEClip* p = self(e);
    switch (op) {
        case effOpen: return 0;
        case effClose:
#if defined(_WIN32)
            closeEditor(p);
#endif
            delete p;
            return 0;
        case effSetSampleRate: p->sampleRate = opt; return 0;
        case effSetBlockSize: return 0;
        case effMainsChanged: if (val) p->reset(); return 0;
        case effGetProgram: return 0;
        case effSetProgram: return 0;
        case effGetProgramName: case effGetProgramNameIndexed: copyStr(ptr, "Default", 25); return 1;
        case effGetParamName: p->paramName(idx, static_cast<char*>(ptr)); return 0;
        case effGetParamLabel: p->paramLabel(idx, static_cast<char*>(ptr)); return 0;
        case effGetParamDisplay: p->paramDisplay(idx, static_cast<char*>(ptr)); return 0;
        case effGetEffectName: copyStr(ptr, "AIE Clip", 32); return 1;
        case effGetProductString: copyStr(ptr, "AIE Clip", 64); return 1;
        case effGetVendorString: copyStr(ptr, "AI Audio Engineer", 64); return 1;
        case effGetVendorVersion: return 1000;
        case effGetPlugCategory: return 1;  // kPlugCategEffect
        case effGetVstVersion: return 2400;
        case effGetTailSize: return p->latency + 1;
        case effCanDo: return 0;
#if defined(_WIN32)
        case effEditGetRect: {
            static ERect r = {0, 0, int16_t(gui::Editor::H), int16_t(gui::Editor::W)};
            if (ptr) *static_cast<ERect**>(ptr) = &r;
            return 1;
        }
        case effEditOpen: return openEditor(p, static_cast<HWND>(ptr)) ? 1 : 0;
        case effEditClose: closeEditor(p); return 0;
        case effEditIdle: return 0;
#endif
        default: return 0;
    }
}

static void VSTCALL setParameter(AEffect* e, int32_t i, float v) {
    if (i >= 0 && i < kNumParams) self(e)->params[i] = v < 0 ? 0 : (v > 1 ? 1 : v);
}
static float VSTCALL getParameter(AEffect* e, int32_t i) {
    return (i >= 0 && i < kNumParams) ? self(e)->params[i] : 0.0f;
}
static void VSTCALL processReplacing(AEffect* e, float** in, float** out, int32_t n) { self(e)->run(in, out, n); }
static void VSTCALL processDouble(AEffect* e, double** in, double** out, int32_t n) { self(e)->run(in, out, n); }
static void VSTCALL processAccumulating(AEffect* e, float** in, float** out, int32_t n) {
    // proceso antiguo (acumulativo), por compatibilidad con hosts muy viejos
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
    if (host && host(nullptr, audioMasterVersion, 0, 0, nullptr, 0.0f) == 0) return nullptr;
    AIEClip* p = new AIEClip(host);
    p->fx.dispatcher = dispatcher;
    p->fx.setParameter = setParameter;
    p->fx.getParameter = getParameter;
    p->fx.processReplacing = processReplacing;
    p->fx.processDoubleReplacing = processDouble;
    p->fx.process = processAccumulating;
    return &p->fx;
}
