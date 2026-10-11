/* Host mínimo de pruebas: carga AIE Clip como lo haría Cubase y comprueba su comportamiento. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <windows.h>
#define VSTCALL __cdecl
#else
#include <dlfcn.h>
#define VSTCALL
#endif

typedef intptr_t VstIntPtr;
typedef struct AEffect AEffect;
typedef VstIntPtr (VSTCALL *Callback)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
struct AEffect {
    int32_t magic;
    VstIntPtr (VSTCALL *dispatcher)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
    void (VSTCALL *process)(AEffect*, float**, float**, int32_t);
    void (VSTCALL *setParameter)(AEffect*, int32_t, float);
    float (VSTCALL *getParameter)(AEffect*, int32_t);
    int32_t numPrograms, numParams, numInputs, numOutputs, flags;
    VstIntPtr resvd1, resvd2;
    int32_t initialDelay, realQualities, offQualities;
    float ioRatio;
    void *object, *user;
    int32_t uniqueID, version;
    void (VSTCALL *processReplacing)(AEffect*, float**, float**, int32_t);
    void (VSTCALL *processDoubleReplacing)(AEffect*, double**, double**, int32_t);
    char future[56];
};
/* Como Cubase: si el plugin avisa de un cambio de E/S o de latencia (audioMasterIOChanged), el
   anfitrión lo reinicia en el acto (apagar y encender). Ningún plugin debería pedirlo mientras procesa. */
static int io_changed = 0;
static VstIntPtr VSTCALL host(AEffect* e, int32_t op, int32_t i, VstIntPtr v, void* p, float o) {
    (void)i; (void)v; (void)p; (void)o;
    if (op == 13 && e) {
        if (++io_changed > 50) { printf("FALLO: el plugin pide reiniciarse sin parar (bucle)\n"); exit(3); }
        e->dispatcher(e, 12, 0, 0, NULL, 0);
        e->dispatcher(e, 12, 0, 1, NULL, 0);
        return 1;
    }
    return op == 1 ? 2400 : 0; /* audioMasterVersion */
}
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { printf("FALLO: %s\n", msg); fails++; } else printf("ok: %s\n", msg); } while (0)

int main(int argc, char** argv) {
    const char* path = argc > 1 ? argv[1] : "./AIEClip.so";
#if defined(_WIN32)
    HMODULE lib = LoadLibraryA(path);
    if (!lib) { printf("No se pudo cargar %s\n", path); return 2; }
    AEffect* (VSTCALL *entry)(Callback) = (AEffect* (VSTCALL *)(Callback))GetProcAddress(lib, "VSTPluginMain");
    void* legacy = (void*)GetProcAddress(lib, "main");
    CHECK(legacy != NULL, "exporta 'main' (hosts antiguos como Cubase 5)");
#else
    void* lib = dlopen(path, RTLD_NOW);
    if (!lib) { printf("No se pudo cargar %s: %s\n", path, dlerror()); return 2; }
    AEffect* (*entry)(Callback) = (AEffect* (*)(Callback))dlsym(lib, "VSTPluginMain");
#endif
    CHECK(entry != NULL, "exporta VSTPluginMain");
    if (!entry) return 1;
    AEffect* fx = entry(host);
    CHECK(fx && fx->magic == 0x56737450, "AEffect válido ('VstP')");
    CHECK(fx->numParams == 7 && fx->numInputs == 2 && fx->numOutputs == 2, "7 parámetros, estéreo");
    char name[64] = {0};
    fx->dispatcher(fx, 45, 0, 0, name, 0);
    CHECK(strcmp(name, "AIE Clip") == 0, "nombre del efecto");
    fx->dispatcher(fx, 0, 0, 0, NULL, 0);
    fx->dispatcher(fx, 10, 0, 0, NULL, 48000.0f);
    fx->dispatcher(fx, 11, 0, 512, NULL, 0);
    fx->dispatcher(fx, 12, 0, 1, NULL, 0);

    const int n = 48000;
    float *inL = calloc(n, 4), *inR = calloc(n, 4), *outL = calloc(n, 4), *outR = calloc(n, 4);
    for (int i = 0; i < n; i++) { inL[i] = 2.0f * sinf(2 * 3.14159265f * 100 * i / 48000.0f); inR[i] = inL[i] * 0.5f; }
    float* ins[2] = {inL, inR};
    float* outs[2] = {outL, outR};
    for (int os = 0; os <= 4; os++) {
        fx->setParameter(fx, 3, os / 4.0f);       /* sobremuestreo */
        fx->setParameter(fx, 1, (-3.0f + 24) / 24); /* techo -3 dB */
        for (int s = 0; s < n; s += 512) {
            float* a[2] = {inL + s, inR + s};
            float* b[2] = {outL + s, outR + s};
            int len = n - s < 512 ? n - s : 512;
            fx->processReplacing(fx, a, b, len);
        }
        double ceil = pow(10, -3 / 20.0), peak = 0;
        for (int i = 2000; i < n; i++) { double v = fabs(outL[i]); if (v > peak) peak = v; }
        char msg[128];
        snprintf(msg, sizeof msg, "OS %dx: pico %.4f <= techo %.4f (latencia %d)", 1 << os, peak, ceil, fx->initialDelay);
        CHECK(peak <= ceil + 1e-6 && peak > ceil * 0.9, msg);
        char disp[16];
        fx->dispatcher(fx, 7, 3, 0, disp, 0);
        printf("   pantalla del parámetro OverSmp: %s\n", disp);
    }
    /* señal por debajo del umbral: debe pasar casi intacta (sin coloración) y alineada
       exactamente con la latencia declarada; con Mix al 50 % no debe haber filtro peine */
    fx->setParameter(fx, 2, 0.0f);
    for (int i = 0; i < n; i++) inL[i] = inR[i] = 0.25f * sinf(2 * 3.14159265f * 1000 * i / 48000.0f);
    for (int os = 0; os <= 4; os++) {
        for (int m = 0; m < 2; m++) {
            fx->setParameter(fx, 3, os / 4.0f);
            fx->setParameter(fx, 5, m ? 0.5f : 1.0f);
            fx->processReplacing(fx, ins, outs, n);
            int lat = fx->initialDelay;
            double err = 0;
            for (int i = 4000; i < n - 100; i++) { double d = fabs(outL[i] - inL[i - lat]); if (d > err) err = d; }
            char msg[128];
            snprintf(msg, sizeof msg, "señal limpia OS %dx mix %d%%: error máx %.5f (latencia %d)", 1 << os, m ? 50 : 100, err, lat);
            CHECK(err < 0.001, msg);
        }
    }
    fx->setParameter(fx, 5, 1.0f);

    /* mover OverSmp mientras suena (como al tocar el deslizador en Cubase): sin cuelgues, sin pedir
       reinicios al anfitrión, latencia constante y ningún pico por encima del techo */
    {
        int lat0 = fx->initialDelay, bad = 0, steps = 0;
        double peak = 0, ceil = pow(10, -3 / 20.0);
        fx->setParameter(fx, 1, (-3.0f + 24) / 24);
        fx->setParameter(fx, 2, 0.3f);
        for (int i = 0; i < n; i++) { inL[i] = 2.0f * sinf(2 * 3.14159265f * 100 * i / 48000.0f); inR[i] = -inL[i]; }
        io_changed = 0;
        for (int rep = 0; rep < 4; rep++) {
            for (int s = 0; s < n; s += 256, steps++) {
                fx->setParameter(fx, 3, (float)((steps * 7) % 41) / 40.0f); /* valores intermedios incluidos */
                if (steps % 37 == 0) { fx->dispatcher(fx, 12, 0, 0, NULL, 0); fx->dispatcher(fx, 12, 0, 1, NULL, 0); }
                float* a[2] = {inL + s, inR + s};
                float* b[2] = {outL + s, outR + s};
                int len = n - s < 256 ? n - s : 256;
                fx->processReplacing(fx, a, b, len);
                for (int j = 0; j < len; j++) {
                    if (!(outL[s + j] == outL[s + j]) || !(outR[s + j] == outR[s + j])) bad++;
                    if (fabs(outL[s + j]) > peak) peak = fabs(outL[s + j]);
                    if (fabs(outR[s + j]) > peak) peak = fabs(outR[s + j]);
                }
                if (fx->initialDelay != lat0) bad++;
            }
        }
        char msg[160];
        snprintf(msg, sizeof msg, "mover OverSmp en marcha (%d cambios): avisos de reinicio %d, errores %d, pico %.4f",
                 steps, io_changed, bad, peak);
        CHECK(io_changed == 0 && bad == 0 && peak <= ceil + 1e-6, msg);
    }
    fx->dispatcher(fx, 1, 0, 0, NULL, 0);
    printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas superadas\n", fails);
    return fails ? 1 : 0;
}
