/* Anfitrión mínimo para probar AIE Exciter como lo carga Cubase (DLL en Windows, .so en Linux). */
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
static VstIntPtr VSTCALL host(AEffect* e, int32_t op, int32_t i, VstIntPtr v, void* p, float o) {
    (void)e; (void)i; (void)v; (void)p; (void)o;
    return op == 1 ? 2400 : 0;
}
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { printf("FALLO: %s\n", msg); fails++; } else printf("ok: %s\n", msg); } while (0)

int main(int argc, char** argv) {
    const char* path = argc > 1 ? argv[1] : "./AIEExciter.dll";
#if defined(_WIN32)
    HMODULE lib = LoadLibraryA(path);
    if (!lib) { printf("No se pudo cargar %s\n", path); return 2; }
    AEffect* (VSTCALL *entry)(Callback) = (AEffect* (VSTCALL *)(Callback))GetProcAddress(lib, "VSTPluginMain");
    CHECK(GetProcAddress(lib, "main") != NULL, "exporta también 'main' (anfitriones antiguos)");
#else
    void* lib = dlopen(path, RTLD_NOW);
    if (!lib) { printf("No se pudo cargar %s: %s\n", path, dlerror()); return 2; }
    AEffect* (*entry)(Callback) = (AEffect* (*)(Callback))dlsym(lib, "VSTPluginMain");
#endif
    CHECK(entry != NULL, "exporta VSTPluginMain");
    if (!entry) return 1;
    AEffect* fx = entry(host);
    CHECK(fx && fx->magic == 0x56737450, "AEffect válido");
    char name[64] = {0};
    fx->dispatcher(fx, 45, 0, 0, name, 0);
    CHECK(strcmp(name, "AIE Exciter") == 0 && fx->numParams == 30 && fx->initialDelay == 69, "AIE Exciter: 30 parámetros, latencia 69");
    CHECK(fx->dispatcher(fx, 35, 0, 0, NULL, 0) == 1, "categoría de efecto");
    fx->dispatcher(fx, 0, 0, 0, NULL, 0);
    fx->dispatcher(fx, 10, 0, 0, NULL, 48000.0f);
    fx->dispatcher(fx, 12, 0, 1, NULL, 0);
    const int n = 4800;
    float *inL = calloc(n, 4), *inR = calloc(n, 4), *outL = calloc(n, 4), *outR = calloc(n, 4);
    for (int i = 0; i < n; i++) { inL[i] = 0.7f * sinf(i * 0.05f) + 0.2f * sinf(i * 1.3f); inR[i] = -0.5f * inL[i]; }
    float* ins[2] = {inL, inR};
    float* outs[2] = {outL, outR};
    fx->processReplacing(fx, ins, outs, n);
    /* con Amount 0 el nivel no cambia; con Amount en todas las bandas el nivel sigue estable y sin valores raros */
    double ei = 0, eo = 0;
    for (int i = 2000; i < n; i++) { ei += inL[i] * inL[i] + inR[i] * inR[i]; eo += outL[i] * outL[i] + outR[i] * outR[i]; }
    char msg0[96];
    snprintf(msg0, sizeof msg0, "Amount 0: nivel de salida / entrada %.3f", sqrt(eo / ei));
    CHECK(fabs(sqrt(eo / ei) - 1.0) < 0.02, msg0);
    for (int b = 0; b < 4; b++) fx->setParameter(fx, 3 + b, 0.8f);
    for (int r = 0; r < 3; r++) fx->processReplacing(fx, ins, outs, n);
    eo = 0;
    int finite = 1;
    for (int i = 2000; i < n; i++) { eo += outL[i] * outL[i] + outR[i] * outR[i]; finite = finite && outL[i] == outL[i]; }
    snprintf(msg0, sizeof msg0, "Amount 80 %% en las 4 bandas: nivel %.2f dB, sin valores raros", 10 * log10(eo / ei));
    CHECK(finite && fabs(10 * log10(eo / ei)) < 2.0, msg0);
    for (int b = 0; b < 4; b++) fx->setParameter(fx, 3 + b, 0.0f);
#if defined(_WIN32)
    {
        typedef struct { short top, left, bottom, right; } ERect;
        ERect* r = NULL;
        VstIntPtr ok = fx->dispatcher(fx, 13, 0, 0, &r, 0);
        CHECK((fx->flags & 1) && ok && r && r->right == 960 && r->bottom == 570, "editor propio de 960x570");
        HWND parent = CreateWindowExA(0, "STATIC", "anfitrion", WS_OVERLAPPEDWINDOW, 0, 0, 1060, 680, NULL, NULL,
                                      GetModuleHandleA(NULL), NULL);
        for (int rep = 0; rep < 2; rep++) {
            VstIntPtr o = fx->dispatcher(fx, 14, 0, 0, parent, 0);
            HWND child = GetWindow(parent, GW_CHILD);
            CHECK(o == 1 && child != NULL, "abre la ventana");
            if (!child) break;
            for (int k = 0; k < 30; k++) {
                fx->processReplacing(fx, ins, outs, n);
                MSG m;
                while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageA(&m); }
                Sleep(10);
            }
            SendMessageA(child, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(700, 387)); /* botón Delta */
            SendMessageA(child, WM_LBUTTONUP, 0, MAKELPARAM(700, 387));
            CHECK(fx->getParameter(fx, 28) > 0.5f, "clic en el botón Delta");
            fx->setParameter(fx, 28, 0.0f);
            HDC sdc = GetDC(NULL), mdc = CreateCompatibleDC(sdc);
            HBITMAP bmp = CreateCompatibleBitmap(sdc, 960, 570);
            HGDIOBJ old = SelectObject(mdc, bmp);
            SendMessageA(child, WM_PRINTCLIENT, (WPARAM)mdc, PRF_CLIENT);
            int distinct = 0;
            COLORREF seen[512];
            for (int y = 5; y < 570 && distinct < 512; y += 7)
                for (int x = 5; x < 960 && distinct < 512; x += 7) {
                    COLORREF c = GetPixel(mdc, x, y);
                    int found = 0;
                    for (int q = 0; q < distinct; q++) if (seen[q] == c) { found = 1; break; }
                    if (!found) seen[distinct++] = c;
                }
            char msg[128];
            snprintf(msg, sizeof msg, "la interfaz se dibuja en Windows (%d colores distintos)", distinct);
            CHECK(distinct > 40, msg);
            SelectObject(mdc, old); DeleteObject(bmp); DeleteDC(mdc); ReleaseDC(NULL, sdc);
            fx->dispatcher(fx, 15, 0, 0, NULL, 0);
            CHECK(GetWindow(parent, GW_CHILD) == NULL, "cierra la ventana");
        }
        DestroyWindow(parent);
    }
#endif
    fx->dispatcher(fx, 1, 0, 0, NULL, 0);
    printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas superadas\n", fails);
    return fails ? 1 : 0;
}
