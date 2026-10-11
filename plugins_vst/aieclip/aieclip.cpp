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

enum Param { kInput = 0, kCeiling, kSoftness, kOversampling, kOutput, kMix, kSafety, kDelta, kNumParams };
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

// Curva de recorte: lineal hasta T*(1-k) y transición tanh hasta el techo T (derivada continua).
static inline double clipSample(double x, double T, double k) {
    double a = std::fabs(x);
    if (k <= 1e-6) return x > T ? T : (x < -T ? -T : x);
    double knee = T * (1.0 - k);
    if (a <= knee) return x;
    // tanh(z) = 1 - 2/(e^(2z)+1): mismo resultado que std::tanh para z >= 0 y bastante más rápido
    double y = knee + T * k * (1.0 - 2.0 / (std::exp(2.0 * (a - knee) / (T * k)) + 1.0));
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
    int osIndex = -1, stages = 0, pad = kLatency, latency = kLatency;
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
        fx.magic = kEffectMagic;
        fx.numPrograms = 1;
        fx.numParams = kNumParams;
        fx.numInputs = 2;
        fx.numOutputs = 2;
        fx.flags = effFlagsCanReplacing | effFlagsCanDoubleReplacing;
        fx.object = this;
        fx.uniqueID = kUniqueId;
        fx.version = 1100;
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
        if (s == stages) return clipSample(x, T, k);
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
        double gin = std::pow(10.0, inputDb() / 20.0);
        double gout = std::pow(10.0, outputDb() / 20.0);
        double T = std::pow(10.0, ceilingDb() / 20.0);
        double k = params[kSoftness];
        double mix = params[kMix];
        bool safety = params[kSafety] >= 0.5f;
        bool delta = params[kDelta] >= 0.5f;
        for (int c = 0; c < 2; ++c) {
            for (int32_t i = 0; i < frames; ++i) {
                double x = in[c][i];
                Channel& chan = ch[c];
                double dry;
                double wet = processOne(chan, x, gin, T, k, dry);
                if (safety) wet = wet > T ? T : (wet < -T ? -T : wet);
                // Delta: escuchar solo lo que el clipper quita (para ajustar Input de oído)
                double y = delta ? (dry * gin - wet) * gout : (mix * wet + (1.0 - mix) * dry) * gout;
                out[c][i] = T_(y);
            }
        }
    }

    void paramName(int i, char* s) {
        static const char* n[kNumParams] = {"Input", "Ceiling", "Soft", "OverSmp", "Output", "Mix", "Safety", "Delta"};
        copyStr(s, i < kNumParams ? n[i] : "", 9);
    }
    void paramLabel(int i, char* s) {
        static const char* l[kNumParams] = {"dB", "dB", "%", "", "dB", "%", "", ""};
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
