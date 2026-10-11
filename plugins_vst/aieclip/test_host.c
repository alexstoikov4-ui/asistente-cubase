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
    CHECK(fx->numParams == 9 && fx->numInputs == 2 && fx->numOutputs == 2, "9 parámetros, estéreo");
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
    for (int os = 0; os <= 5; os++) {
        fx->setParameter(fx, 3, os / 5.0f);       /* sobremuestreo */
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
    for (int os = 0; os <= 5; os++) {
        for (int m = 0; m < 2; m++) {
            fx->setParameter(fx, 3, os / 5.0f);
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
                fx->setParameter(fx, 3, (float)((steps * 7) % 51) / 50.0f); /* valores intermedios incluidos */
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
    /* calidad: respuesta plana hasta 20 kHz y aliasing bajo (DFT con número entero de ciclos) */
    {
        const double fs = 48000.0;
        fx->setParameter(fx, 5, 1.0f);
        fx->setParameter(fx, 6, 0.0f);  /* Safety Off: se mide el clipper sobremuestreado */
        fx->setParameter(fx, 0, 12.0f / 36); /* Input 0 dB */
        for (int os = 1; os <= 5; os++) {
            fx->setParameter(fx, 3, os / 5.0f);
            fx->setParameter(fx, 1, 1.0f); /* techo 0 dB: la señal de prueba no llega */
            double worst = 0;
            const double fr[3] = {10000, 18000, 20000};
            for (int q = 0; q < 3; q++) {
                for (int i = 0; i < n; i++) inL[i] = inR[i] = 0.25f * sinf((float)(2 * 3.14159265358979 * fr[q] * i / fs));
                fx->processReplacing(fx, ins, outs, n);
                double si = 0, so = 0;
                for (int i = 8000; i < n; i++) { si += (double)inL[i] * inL[i]; so += (double)outL[i] * outL[i]; }
                double db = 10 * log10(so / si);
                if (fabs(db) > fabs(worst)) worst = db;
            }
            char msg[160];
            snprintf(msg, sizeof msg, "OS %dx: respuesta 10-20 kHz %+.3f dB (sin pérdida de agudos)", 1 << os, worst);
            CHECK(fabs(worst) < 0.05, msg);

            /* aliasing: seno de ~6,3 kHz recortado 6 dB con rodilla dura y con Soft 30 % */
            const int Nw = 8192, k = 1075; /* 1075*48000/8192 = 6299 Hz, impar: los alias no caen en armónicos */
            const double lim_hard[6] = {0, -33, -45, -57, -69, -81};
            const double lim_soft[6] = {0, -38, -60, -85, -105, -110};
            for (int sft = 0; sft < 2; sft++) {
                fx->setParameter(fx, 1, 18.0f / 24); /* techo -6 dB */
                fx->setParameter(fx, 2, sft ? 0.3f : 0.0f);
                for (int i = 0; i < n; i++) inL[i] = inR[i] = (float)sin(2 * 3.14159265358979 * k * i / Nw);
                fx->processReplacing(fx, ins, outs, n);
                double fund = 0, ali = 0;
                const int st = 20000;
                for (int b = 1; b * fs / Nw < 20000; b++) {
                    double re = 0, im = 0;
                    for (int i = 0; i < Nw; i++) {
                        double ph = 2 * 3.14159265358979 * (double)((long)b * i % Nw) / Nw;
                        re += outL[st + i] * cos(ph); im -= outL[st + i] * sin(ph);
                    }
                    double p = re * re + im * im;
                    if (b == k) fund = p; else if (b % k != 0) ali += p;
                }
                double db = 10 * log10(ali / fund);
                snprintf(msg, sizeof msg, "OS %dx: aliasing %s %.1f dB (límite %.0f)", 1 << os, sft ? "suave" : "duro",
                         db, sft ? lim_soft[os] : lim_hard[os]);
                CHECK(db < (sft ? lim_soft[os] : lim_hard[os]), msg);
            }
        }
        /* Delta: por debajo del techo el clipper no quita nada -> silencio */
        fx->setParameter(fx, 3, 2 / 5.0f);
        fx->setParameter(fx, 1, 1.0f);
        fx->setParameter(fx, 7, 1.0f);
        for (int i = 0; i < n; i++) inL[i] = inR[i] = 0.25f * sinf(2 * 3.14159265f * 1000 * i / 48000.0f);
        fx->processReplacing(fx, ins, outs, n);
        double mx = 0;
        for (int i = 4000; i < n; i++) if (fabs(outL[i]) > mx) mx = fabs(outL[i]);
        char msg[96];
        snprintf(msg, sizeof msg, "Delta sin recorte: residuo %.6f (silencio)", mx);
        CHECK(mx < 1e-3, msg);
        fx->setParameter(fx, 7, 0.0f);
    }
    /* modos de recorte: con Ceiling activo ningún pico pasa del techo y todos recortan */
    {
        const char* names[4] = {"Classic", "Pro", "Hard", "Ratio 2:1"};
        fx->setParameter(fx, 3, 2 / 5.0f);
        fx->setParameter(fx, 1, (-6.0f + 24) / 24);
        fx->setParameter(fx, 2, 0.5f);
        fx->setParameter(fx, 6, 1.0f);
        fx->setParameter(fx, 7, 0.0f);
        for (int i = 0; i < n; i++) inL[i] = inR[i] = 1.2f * sinf(2 * 3.14159265f * 220 * i / 48000.0f);
        for (int m = 0; m < 4; m++) {
            fx->setParameter(fx, 8, m / 3.0f);
            fx->processReplacing(fx, ins, outs, n);
            double pk = 0, ceil = pow(10, -6 / 20.0);
            for (int i = 4000; i < n; i++) if (fabs(outL[i]) > pk) pk = fabs(outL[i]);
            char disp[16];
            fx->dispatcher(fx, 7, 8, 0, disp, 0);
            char msg[128];
            snprintf(msg, sizeof msg, "modo %s (%s): pico %.4f <= techo %.4f", names[m], disp, pk, ceil);
            CHECK(pk <= ceil + 1e-6 && pk > ceil * 0.9, msg);
        }
        fx->setParameter(fx, 8, 0.0f);
    }
#if defined(_WIN32)
    /* ventana del editor como la abre Cubase: tamaño, abrir, clic, captura, cerrar (dos veces) */
    {
        typedef struct { short top, left, bottom, right; } ERect;
        ERect* r = NULL;
        VstIntPtr ok = fx->dispatcher(fx, 13, 0, 0, &r, 0);
        char msg[160];
        CHECK((fx->flags & 1) && ok && r && r->right - r->left == 800 && r->bottom - r->top == 470,
              "tiene editor propio de 800x470");
        HWND parent = CreateWindowExA(0, "STATIC", "anfitrion", WS_OVERLAPPEDWINDOW, 0, 0, 900, 600, NULL, NULL,
                                      GetModuleHandleA(NULL), NULL);
        for (int rep = 0; rep < 2; rep++) {
            VstIntPtr o = fx->dispatcher(fx, 14, 0, 0, parent, 0);
            HWND child = GetWindow(parent, GW_CHILD);
            CHECK(o == 1 && child != NULL, "abre la ventana del editor");
            if (!child) break;
            float d0 = fx->getParameter(fx, 7);
            SendMessageA(child, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(500, 384)); /* interruptor Delta */
            SendMessageA(child, WM_LBUTTONUP, 0, MAKELPARAM(500, 384));
            CHECK((fx->getParameter(fx, 7) >= 0.5f) != (d0 >= 0.5f), "clic en Delta desde la ventana");
            SendMessageA(child, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(500, 384));
            SendMessageA(child, WM_LBUTTONUP, 0, MAKELPARAM(500, 384));
            /* arrastrar el mando Gain */
            float g0 = fx->getParameter(fx, 0);
            int ky = 76 + 320 - (int)(g0 * 320);
            SendMessageA(child, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(118, ky));
            SendMessageA(child, WM_MOUSEMOVE, MK_LBUTTON, MAKELPARAM(118, ky - 40));
            SendMessageA(child, WM_LBUTTONUP, 0, MAKELPARAM(118, ky - 40));
            CHECK(fx->getParameter(fx, 0) > g0 + 0.1f, "arrastrar Gain desde la ventana");
            fx->setParameter(fx, 0, g0);
            /* audio y temporizador de la interfaz durante un momento */
            for (int k = 0; k < 20; k++) {
                fx->processReplacing(fx, ins, outs, 512);
                MSG m;
                while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageA(&m); }
                Sleep(10);
            }
            if (rep == 0) {
                HDC sdc = GetDC(NULL), mdc = CreateCompatibleDC(sdc);
                HBITMAP bmp = CreateCompatibleBitmap(sdc, 800, 470);
                HGDIOBJ old = SelectObject(mdc, bmp);
                /* la ventana de prueba está oculta: se le pide que se pinte en nuestro contexto */
                SendMessageA(child, WM_PRINTCLIENT, (WPARAM)mdc, PRF_CLIENT);
                /* contar colores distintos en una rejilla: la interfaz dibujada tiene muchos */
                int distinct = 0;
                COLORREF seen[512];
                for (int y = 5; y < 470 && distinct < 512; y += 9)
                    for (int x = 5; x < 800 && distinct < 512; x += 9) {
                        COLORREF c = GetPixel(mdc, x, y);
                        int found = 0;
                        for (int q = 0; q < distinct; q++) if (seen[q] == c) { found = 1; break; }
                        if (!found) seen[distinct++] = c;
                    }
                snprintf(msg, sizeof msg, "la interfaz se dibuja en Windows (%d colores distintos en la captura)", distinct);
                CHECK(distinct > 40, msg);
                /* guardar la captura (BMP) para el artefacto de la compilación */
                BITMAPINFO bi; memset(&bi, 0, sizeof bi);
                bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER); bi.bmiHeader.biWidth = 800; bi.bmiHeader.biHeight = 470;
                bi.bmiHeader.biPlanes = 1; bi.bmiHeader.biBitCount = 24; bi.bmiHeader.biCompression = BI_RGB;
                int stride = (800 * 3 + 3) & ~3;
                unsigned char* bits = (unsigned char*)malloc(stride * 470);
                GetDIBits(mdc, bmp, 0, 470, bits, &bi, DIB_RGB_COLORS);
                FILE* f = fopen("editor.bmp", "wb");
                if (f) {
                    BITMAPFILEHEADER fh; memset(&fh, 0, sizeof fh);
                    fh.bfType = 0x4D42; fh.bfOffBits = sizeof fh + sizeof(BITMAPINFOHEADER); fh.bfSize = fh.bfOffBits + stride * 470;
                    fwrite(&fh, sizeof fh, 1, f); fwrite(&bi.bmiHeader, sizeof(BITMAPINFOHEADER), 1, f); fwrite(bits, stride * 470, 1, f);
                    fclose(f);
                }
                free(bits);
                SelectObject(mdc, old); DeleteObject(bmp); DeleteDC(mdc); ReleaseDC(NULL, sdc);
            }
            fx->dispatcher(fx, 15, 0, 0, NULL, 0);
            CHECK(GetWindow(parent, GW_CHILD) == NULL, "cierra la ventana del editor");
        }
        DestroyWindow(parent);
    }
#endif
    fx->dispatcher(fx, 1, 0, 0, NULL, 0);
    printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas superadas\n", fails);
    return fails ? 1 : 0;
}
