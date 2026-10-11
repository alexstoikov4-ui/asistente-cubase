// Pruebas de AIE Scope sin ventana: el sonido no cambia, cada banda detecta lo suyo, el búfer avanza al
// ritmo correcto, el dibujo es rápido. Con un argumento, guarda una vista previa en formato PPM.
// g++ -O2 tests_scope.cpp -o tests_scope && ./tests_scope [vista.ppm]
#include <chrono>
#include "aiescope.cpp"

static VstIntPtr VSTCALL hostCb(AEffect*, int32_t op, int32_t, VstIntPtr, void*, float) { return op == 1 ? 2400 : 0; }
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { std::printf("FALLO: %s\n", msg); fails++; } else std::printf("ok: %s\n", msg); } while (0)

// Ritmo de prueba a 126 BPM: bombo (graves), bajo, acorde (medios) y charles/palmas (agudos)
static void beat(std::vector<float>& L, std::vector<float>& R, double fs, double seconds) {
    const double pi = 3.14159265358979323846;
    size_t n = size_t(fs * seconds);
    L.assign(n, 0); R.assign(n, 0);
    unsigned seed = 7;
    double beatLen = 60.0 / 126;
    for (size_t i = 0; i < n; ++i) {
        double t = i / fs, tb = std::fmod(t, beatLen), bar = std::fmod(t, beatLen * 4);
        int beatNo = int(t / beatLen);
        double kick = 0.8 * std::exp(-tb * 14) * std::sin(2 * pi * (48 + 110 * std::exp(-tb * 35)) * tb);
        double bass = (tb > beatLen * 0.5 ? 0.28 : 0.0) * std::sin(2 * pi * 55 * t) * std::exp(-(tb - beatLen * 0.5) * 6);
        double chordEnv = (bar > beatLen * 2 && bar < beatLen * 3.6) ? 0.22 : 0.05;
        double chord = chordEnv * (std::sin(2 * pi * 440 * t) + std::sin(2 * pi * 554 * t) + std::sin(2 * pi * 659 * t)) / 3;
        seed = seed * 1664525u + 1013904223u;
        double noise = (seed >> 8) / 16777216.0 - 0.5;
        double th = std::fmod(t + beatLen / 2, beatLen);
        double hat = 0.5 * std::exp(-th * 60) * noise * (beatNo % 8 >= 4 ? 1.0 : 0.4);
        double clap = (beatNo % 2 == 1) ? 0.5 * std::exp(-tb * 30) * noise : 0.0;
        // el charles de verdad es agudo: diferencia de ruido (filtro paso alto sencillo)
        static double prev = 0;
        double hp = noise - prev;
        prev = noise;
        hat = 0.45 * std::exp(-th * 60) * hp * (beatNo % 8 >= 4 ? 1.0 : 0.5);
        L[i] = float(kick + bass + chord + hat + clap * 0.6);
        R[i] = float(kick + bass + chord * 0.9 + hat * 0.8 + clap * 0.6);
    }
}

int main(int argc, char** argv) {
    AIEScope p(hostCb);
    p.setSampleRate(44100);
    const int block = 512;
    std::vector<float> L(block), R(block), oL(block), oR(block);
    float* in[2] = {L.data(), R.data()};
    float* out[2] = {oL.data(), oR.data()};

    // 1) el sonido sale igual que entra (también en el mismo búfer)
    bool same = true;
    unsigned s = 3;
    for (int b = 0; b < 50; ++b) {
        for (int i = 0; i < block; ++i) { s = s * 1103515245u + 12345u; L[i] = float((s >> 9) / 4194304.0 - 1); R[i] = -L[i] * 0.5f; }
        p.run(in, out, block);
        for (int i = 0; i < block; ++i) same = same && oL[i] == L[i] && oR[i] == R[i];
        p.run(in, in, block);
    }
    CHECK(same, "la salida es idéntica a la entrada (no toca el sonido)");

    // 2) cada banda detecta su frecuencia
    struct { double f; int band; const char* name; } cases[3] = {{80, 0, "80 Hz -> graves"}, {900, 1, "900 Hz -> medios"}, {7000, 2, "7 kHz -> agudos"}};
    for (auto& c : cases) {
        p.reset();
        uint32_t w0 = p.written.load();
        for (int b = 0; b < 40; ++b) {
            for (int i = 0; i < block; ++i) L[i] = R[i] = float(0.5 * std::sin(2 * 3.14159265358979 * c.f * (b * block + i) / 44100.0));
            p.run(in, out, block);
        }
        uint32_t w1 = p.written.load();
        float bands[3] = {0, 0, 0};
        for (uint32_t k = w0 + (w1 - w0) / 2; k < w1; ++k) {
            const Column& col = p.ring[k & (kRing - 1)];
            bands[0] = std::max(bands[0], col.low); bands[1] = std::max(bands[1], col.mid); bands[2] = std::max(bands[2], col.high);
        }
        int best = int(std::max_element(bands, bands + 3) - bands);
        char msg[160];
        std::snprintf(msg, sizeof msg, "%s (graves %.2f, medios %.2f, agudos %.2f)", c.name, bands[0], bands[1], bands[2]);
        CHECK(best == c.band && bands[c.band] > 0.4f, msg);
    }

    // 3) ritmo del búfer: ~345 columnas por segundo a 44,1 kHz y 48 kHz
    for (double fs : {44100.0, 48000.0}) {
        p.setSampleRate(fs);
        uint32_t w0 = p.written.load();
        int blocks = int(fs / block);
        for (int b = 0; b < blocks; ++b) p.run(in, out, block);
        double cps = (p.written.load() - w0) / (blocks * double(block) / fs);
        char msg[96];
        std::snprintf(msg, sizeof msg, "a %.0f Hz guarda %.0f columnas por segundo", fs, cps);
        CHECK(std::fabs(cps - 1.0 / kColumnSeconds) < 6, msg);
    }

    // 4) dibujo con el ritmo de prueba
    p.setSampleRate(44100);
    std::vector<float> bl, br;
    beat(bl, br, 44100, 20.0);
    gui::ScopeEditor ed(&p);
    for (size_t pos = 0; pos + block <= bl.size(); pos += block) {
        float* bi[2] = {bl.data() + pos, br.data() + pos};
        p.run(bi, out, block);
        if ((pos / block) % 3 == 0) ed.tick();
    }
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 20; ++i) ed.draw();
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 20;
    char msg[96];
    std::snprintf(msg, sizeof msg, "dibujo de un fotograma: %.1f ms", ms);
    CHECK(ms < 25, msg);

    if (argc > 1) {  // (antes de la prueba de ratón, que mete ruido)  // vistas previas: modo Color (4 s) y 3 bandas (8 s)
        for (int v = 0; v < 2; ++v) {
            p.params[kMode] = float(v);
            p.params[kZoom] = v ? 2.0f / 3 : 1.0f / 3;
            ed.draw();
            std::string name = std::string(argv[1]) + (v ? "_3bandas.ppm" : "_color.ppm");
            FILE* f = std::fopen(name.c_str(), "wb");
            std::fprintf(f, "P6 %d %d 255\n", ed.cv.w, ed.cv.h);
            for (uint32_t px : ed.cv.px) { unsigned char c[3] = {(unsigned char)(px >> 16), (unsigned char)(px >> 8), (unsigned char)px}; std::fwrite(c, 1, 3, f); }
            std::fclose(f);
        }
    }
    p.params[kMode] = 0; p.params[kZoom] = 1.0f / 3;
    // 5) interacción: zoom, vista, freeze, gain
    ed.mouseDown(ed.btnZoom[3].x + 5, ed.btnZoom[3].y + 5, false, false, 0, 0);
    CHECK(p.zoomIdx() == 3, "botón 16 s");
    ed.wheel(ed.wave.x + 50, ed.wave.y + 50, 2, false);
    CHECK(p.zoomIdx() == 1, "rueda sobre la onda acerca (16 s -> 4 s)");
    ed.mouseDown(ed.btnMode[1].x + 5, ed.btnMode[1].y + 5, false, false, 0, 0);
    CHECK(p.params[kMode] >= 0.5f, "botón 3 bandas");
    uint32_t before = ed.head();
    ed.mouseDown(ed.tglFreeze.x + 5, ed.tglFreeze.y + 5, false, false, 0, 0);
    for (int b = 0; b < 20; ++b) p.run(in, out, block);
    ed.tick();
    CHECK(p.params[kFreeze] >= 0.5f && ed.head() == before, "Freeze congela la onda aunque siga sonando");
    ed.mouseDown(ed.tglFreeze.x + 5, ed.tglFreeze.y + 5, false, false, 0, 0);
    ed.tick();
    CHECK(ed.head() != before, "quitar Freeze vuelve al tiempo real");
    ed.mouseDown(ed.gainTrack.x + ed.gainTrack.w / 2, ed.gainTrack.y + 10, false, false, 0, 0);
    ed.mouseUp();
    CHECK(std::fabs(p.gainDb() - 12.0) < 0.5, "clic en mitad de Gain = +12 dB");
    ed.mouseDown(ed.gainTrack.x + 5, ed.gainTrack.y + 10, true, false, 0, 0);
    CHECK(p.gainDb() < 0.01, "doble clic en Gain vuelve a 0 dB");

    std::printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas de AIE Scope superadas\n", fails);
    return fails ? 1 : 0;
}
