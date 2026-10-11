// AIE Clip — clipper con sobremuestreo para Cubase 5 (VST 2.4, 32 y 64 bits).
//
// Implementa la interfaz binaria VST 2.x directamente (estructura AEffect y códigos de operación
// estándar), sin depender del SDK de Steinberg. Sin interfaz gráfica propia: Cubase muestra sus
// deslizadores genéricos para los parámetros.
//
// Parámetros: Input, Ceiling, Softness, Oversampling (1x-16x), Output, Mix, Safety.

#include <cmath>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <algorithm>
#include <vector>

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
    effMainsChanged = 12, effEditGetRect = 13, effGetProgramNameIndexed = 29, effGetPlugCategory = 35,
    effGetEffectName = 45, effGetVendorString = 47, effGetProductString = 48, effGetVendorVersion = 49,
    effCanDo = 51, effGetTailSize = 52, effGetParameterProperties = 56, effGetVstVersion = 58
};
enum { audioMasterVersion = 1 };
enum { effFlagsCanReplacing = 1 << 4, effFlagsCanDoubleReplacing = 1 << 12 };
static const int32_t kEffectMagic = 0x56737450;  // 'VstP'
static const int32_t kUniqueId = 0x41494563;     // 'AIEc'

enum Param { kInput = 0, kCeiling, kSoftness, kOversampling, kOutput, kMix, kSafety, kNumParams };
static const int kOsFactors[5] = {1, 2, 4, 8, 16};
static const int kTapsPerPhase = 16;  // longitud del filtro por fase (calidad del sobremuestreo)
// Latencia fija en todos los factores: (L-1)/2 de subida + (L-1)/2 de bajada - (N-1) por tomar la
// salida en la última fase = L-N = N*(kTapsPerPhase-1) muestras altas = kTapsPerPhase-1 muestras.
static const int kLatency = kTapsPerPhase - 1;

static void copyStr(void* dst, const char* src, size_t max) {
    char* d = static_cast<char*>(dst);
    std::strncpy(d, src, max - 1);
    d[max - 1] = 0;
}

// Curva de recorte: lineal hasta T*(1-k) y transición tanh hasta el techo T (derivada continua).
static inline double clipSample(double x, double T, double k) {
    double a = std::fabs(x);
    if (k <= 1e-6) return x > T ? T : (x < -T ? -T : x);
    double knee = T * (1.0 - k);
    if (a <= knee) return x;
    double y = knee + T * k * std::tanh((a - knee) / (T * k));
    return x < 0 ? -y : y;
}

// Filtro paso bajo enventanado (Kaiser) para sobremuestreo x N con L coeficientes.
static std::vector<double> designLowpass(int N, int L) {
    std::vector<double> h(L);
    const double beta = 9.0, pi = 3.14159265358979323846;
    double cutoff = 0.45 / N;  // en ciclos por muestra a la frecuencia alta (deja margen bajo Nyquist)
    auto besselI0 = [](double x) {
        double sum = 1.0, term = 1.0;
        for (int k = 1; k < 30; ++k) { term *= (x / (2.0 * k)) * (x / (2.0 * k)); sum += term; }
        return sum;
    };
    double i0b = besselI0(beta), sum = 0;
    for (int i = 0; i < L; ++i) {
        double m = i - (L - 1) / 2.0;
        double s = (m == 0) ? 2 * cutoff : std::sin(2 * pi * cutoff * m) / (pi * m);
        double r = 2.0 * i / (L - 1) - 1.0;
        double w = besselI0(beta * std::sqrt(std::fmax(0.0, 1 - r * r))) / i0b;
        h[i] = s * w;
        sum += h[i];
    }
    for (auto& v : h) v /= sum;  // ganancia unitaria en continua
    return h;
}

struct Channel {
    std::vector<double> upHist, downHist, dryDelay, wetDelay;
    int upPos = 0, downPos = 0, dryPos = 0, wetPos = 0;
};

class AIEClip {
public:
    AEffect fx{};
    audioMasterCallback host;
    float params[kNumParams];
    double sampleRate = 44100;
    int osIndex = -1, N = 1, latency = 0;
    // Filtros de todos los factores calculados al crear el plugin: cambiar el sobremuestreo durante la
    // reproducción no reserva memoria ni cambia la latencia (no hace falta avisar a Cubase).
    std::vector<double> filters[5];
    const double* h = nullptr;  // filtro activo (N*kTapsPerPhase coeficientes)
    Channel ch[2];

    explicit AIEClip(audioMasterCallback cb) : host(cb) {
        params[kInput] = paramFromInputDb(0.0);
        params[kCeiling] = paramFromCeilingDb(-0.3);
        params[kSoftness] = 0.3f;
        params[kOversampling] = 0.5f;  // 4x
        params[kOutput] = paramFromOutputDb(0.0);
        params[kMix] = 1.0f;
        params[kSafety] = 1.0f;
        fx.magic = kEffectMagic;
        fx.numPrograms = 1;
        fx.numParams = kNumParams;
        fx.numInputs = 2;
        fx.numOutputs = 2;
        fx.flags = effFlagsCanReplacing | effFlagsCanDoubleReplacing;
        fx.object = this;
        fx.uniqueID = kUniqueId;
        fx.version = 1000;
        fx.ioRatio = 1.0f;
        for (int i = 1; i < 5; ++i) filters[i] = designLowpass(kOsFactors[i], kOsFactors[i] * kTapsPerPhase);
        latency = kLatency;
        fx.initialDelay = kLatency;
        for (auto& c : ch) {
            c.upHist.assign(kTapsPerPhase, 0.0);
            c.downHist.assign(16 * kTapsPerPhase, 0.0);
            c.dryDelay.assign(kLatency + 1, 0.0);
            c.wetDelay.assign(kLatency + 1, 0.0);
        }
        configure();
    }

    // conversiones de parámetros normalizados (0..1) a unidades reales
    static float paramFromInputDb(double db) { return float((db + 12.0) / 36.0); }      // -12..+24 dB
    static float paramFromCeilingDb(double db) { return float((db + 24.0) / 24.0); }   // -24..0 dB
    static float paramFromOutputDb(double db) { return float((db + 24.0) / 36.0); }    // -24..+12 dB
    double inputDb() const { return -12.0 + 36.0 * params[kInput]; }
    double ceilingDb() const { return -24.0 + 24.0 * params[kCeiling]; }
    double outputDb() const { return -24.0 + 36.0 * params[kOutput]; }
    int osIdx() const { int i = int(params[kOversampling] * 4.0f + 0.5f); return i < 0 ? 0 : (i > 4 ? 4 : i); }

    // Se llama desde el hilo de audio: solo cambia punteros y pone a cero historiales (sin reservar memoria).
    void configure() {
        int idx = osIdx();
        if (idx == osIndex) return;
        osIndex = idx;
        N = kOsFactors[idx];
        h = (N > 1) ? filters[idx].data() : nullptr;
        for (auto& c : ch) {
            std::fill(c.upHist.begin(), c.upHist.end(), 0.0);
            std::fill(c.downHist.begin(), c.downHist.end(), 0.0);
            std::fill(c.wetDelay.begin(), c.wetDelay.end(), 0.0);
            c.upPos = c.downPos = c.wetPos = 0;
        }
    }

    void reset() {
        for (auto& c : ch) {
            std::fill(c.dryDelay.begin(), c.dryDelay.end(), 0.0);
            c.dryPos = 0;
        }
        osIndex = -1;
        configure();
    }

    // Devuelve la señal procesada; en 'dry' deja la entrada retrasada la misma latencia.
    double processOne(Channel& c, double x, double gin, double T, double k, double& dry) {
        int D = int(c.dryDelay.size());
        c.dryDelay[c.dryPos] = x;
        dry = c.dryDelay[(c.dryPos + D - latency) % D];
        c.dryPos = (c.dryPos + 1) % D;
        x *= gin;
        double y;
        if (N == 1) {
            // sin sobremuestreo: se retrasa igual que con filtros para que la latencia sea siempre la misma
            int W = int(c.wetDelay.size());
            c.wetDelay[c.wetPos] = clipSample(x, T, k);
            y = c.wetDelay[(c.wetPos + W - kLatency) % W];
            c.wetPos = (c.wetPos + 1) % W;
        } else {
            // historial de entrada para el filtro polifásico de subida
            c.upPos = (c.upPos + kTapsPerPhase - 1) % kTapsPerPhase;
            c.upHist[c.upPos] = x;
            int L = N * kTapsPerPhase;
            for (int p = 0; p < N; ++p) {
                double acc = 0;
                for (int t = 0; t < kTapsPerPhase; ++t) acc += h[p + t * N] * c.upHist[(c.upPos + t) % kTapsPerPhase];
                double clipped = clipSample(acc * N, T, k);
                c.downPos = (c.downPos + L - 1) % L;
                c.downHist[c.downPos] = clipped;
            }
            double acc = 0;  // filtro de bajada y diezmado (una salida cada N muestras)
            for (int i = 0; i < L; ++i) acc += h[i] * c.downHist[(c.downPos + i) % L];
            y = acc;
        }
        return y;
    }

    template <typename T_>
    void run(T_** in, T_** out, int32_t frames) {
        configure();
        double gin = std::pow(10.0, inputDb() / 20.0);
        double gout = std::pow(10.0, outputDb() / 20.0);
        double T = std::pow(10.0, ceilingDb() / 20.0);
        double k = params[kSoftness];
        double mix = params[kMix];
        bool safety = params[kSafety] >= 0.5f;
        for (int c = 0; c < 2; ++c) {
            for (int32_t i = 0; i < frames; ++i) {
                double x = in[c][i];
                Channel& chan = ch[c];
                double dry;
                double wet = processOne(chan, x, gin, T, k, dry);
                if (safety) wet = wet > T ? T : (wet < -T ? -T : wet);
                double y = (mix * wet + (1.0 - mix) * dry) * gout;
                out[c][i] = T_(y);
            }
        }
    }

    void paramName(int i, char* s) {
        static const char* n[kNumParams] = {"Input", "Ceiling", "Soft", "OverSmp", "Output", "Mix", "Safety"};
        copyStr(s, i < kNumParams ? n[i] : "", 9);
    }
    void paramLabel(int i, char* s) {
        static const char* l[kNumParams] = {"dB", "dB", "%", "", "dB", "%", ""};
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
            default: b[0] = 0;
        }
        copyStr(s, b, 9);
    }
};

static AIEClip* self(AEffect* e) { return static_cast<AIEClip*>(e->object); }

static VstIntPtr VSTCALL dispatcher(AEffect* e, int32_t op, int32_t idx, VstIntPtr val, void* ptr, float opt) {
    AIEClip* p = self(e);
    switch (op) {
        case effOpen: return 0;
        case effClose: delete p; return 0;
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
        case effEditGetRect: return 0;  // sin editor propio: Cubase usa su interfaz genérica
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
