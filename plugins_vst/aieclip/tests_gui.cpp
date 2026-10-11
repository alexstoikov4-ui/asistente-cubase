// Pruebas de la interfaz sin ventana: ratón, rueda, menús, dibujo con valores extremos y velocidad.
// g++ -O2 tests_gui.cpp -o tests_gui && ./tests_gui   (o con cl en Windows)
#include <chrono>
#include "aieclip.cpp"

static VstIntPtr VSTCALL hostCb(AEffect*, int32_t op, int32_t, VstIntPtr, void*, float) { return op == 1 ? 2400 : 0; }
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { std::printf("FALLO: %s\n", msg); fails++; } else std::printf("ok: %s\n", msg); } while (0)

struct TestHost : PluginEditorHost {
    int nextMenu = -1, menus = 0, automates = 0;
    explicit TestHost(AIEClip* p) : PluginEditorHost(p) {}
    int popupMenu(int, int, const std::vector<std::string>&, int) override { ++menus; return nextMenu; }
    void setParam(int i, float v) override { ++automates; PluginEditorHost::setParam(i, v); }
};

int main() {
    AIEClip p(hostCb);
    TestHost h(&p);
    gui::Editor ed(&h);
    const auto& gain = ed.sliders[0];
    // arrastrar Gain hacia arriba media pista
    float v0 = p.params[kInput];
    int kx = gain.r.x + gain.r.w / 2, ky = gain.r.b() - int(v0 * gain.r.h);
    ed.mouseDown(kx, ky, false, false, 0, 0);
    ed.mouseMove(kx, ky - gain.r.h / 4, false);
    ed.mouseUp();
    CHECK(std::fabs(p.params[kInput] - (v0 + 0.25f)) < 0.02f, "arrastrar Gain sube el valor un cuarto");
    // ajuste fino con mayúsculas
    float v1 = p.params[kInput];
    ed.mouseDown(kx, ky - gain.r.h / 4, false, true, 0, 0);
    ed.mouseMove(kx, ky - gain.r.h / 4 - 100, true);
    ed.mouseUp();
    CHECK(p.params[kInput] > v1 && p.params[kInput] - v1 < 100.0f / gain.r.h * 0.11f, "ajuste fino 10 veces más lento");
    // doble clic: valor por defecto (0 dB)
    ed.mouseDown(kx, gain.r.y + 10, true, false, 0, 0);
    CHECK(std::fabs(p.inputDb()) < 0.05, "doble clic devuelve Gain a 0 dB");
    // rueda
    float v2 = p.params[kMix];
    ed.wheel(ed.sliders[3].r.x + 20, ed.sliders[3].r.y + 5, -5, false);
    CHECK(p.params[kMix] < v2 && p.params[kMix] > v2 - 0.06f, "rueda baja el Mix");
    // menús
    h.nextMenu = 2;
    ed.mouseDown(ed.btnMode.x + 5, ed.btnMode.y + 5, false, false, 0, 0);
    CHECK(p.modeIdx() == kHard && h.menus == 1, "menú Modo elige Hard Clip");
    h.nextMenu = 4;
    ed.mouseDown(ed.btnOs.x + 5, ed.btnOs.y + 5, false, false, 0, 0);
    CHECK(kOsFactors[p.osIdx()] == 16, "menú Oversampling elige x16");
    h.nextMenu = -1;
    ed.mouseDown(ed.btnOs.x + 5, ed.btnOs.y + 5, false, false, 0, 0);
    CHECK(kOsFactors[p.osIdx()] == 16, "cerrar el menú sin elegir no cambia nada");
    // interruptores
    bool c0 = p.params[kSafety] >= 0.5f;
    ed.mouseDown(ed.tglCeil.x + 5, ed.tglCeil.y + 5, false, false, 0, 0);
    CHECK((p.params[kSafety] >= 0.5f) != c0, "clic en Ceiling lo cambia");
    ed.mouseDown(ed.tglDelta.x + 5, ed.tglDelta.y + 5, false, false, 0, 0);
    CHECK(p.params[kDelta] >= 0.5f, "clic en Delta lo activa");
    CHECK(h.automates > 0, "los cambios se comunican al anfitrión");
    // dibujo con valores extremos en todos los modos y factores (no debe salirse del lienzo ni colgarse)
    bool ok = true;
    for (int m = 0; m < kNumModes; ++m)
        for (float v : {0.0f, 0.5f, 1.0f}) {
            for (int i = 0; i < kNumParams; ++i) p.params[i] = v;
            p.params[kMode] = float(m) / (kNumModes - 1);
            ed.dispIn[0] = ed.dispOut[1] = 4.0f; ed.dispGr = 40; ed.holdIn = 100;
            ed.draw();
            ok = ok && ed.cv.px.size() == size_t(gui::Editor::W) * gui::Editor::H;
        }
    CHECK(ok, "dibujo con valores extremos");
    // velocidad: a 30 fotogramas por segundo el dibujo debe tardar mucho menos de 33 ms
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 20; ++i) { ed.tickMeters(); ed.draw(); }
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 20;
    char msg[96];
    std::snprintf(msg, sizeof msg, "dibujo de un fotograma: %.1f ms", ms);
    CHECK(ms < 25, msg);
    std::printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas de la interfaz superadas\n", fails);
    return fails ? 1 : 0;
}
