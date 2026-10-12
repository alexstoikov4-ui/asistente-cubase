// Pruebas de AIE Exciter sin ventana. Con un argumento guarda una vista previa (PPM).
// g++ -O2 tests_exciter.cpp -o tests_exciter && ./tests_exciter [vista.ppm]
#include "aieexciter.cpp"

static VstIntPtr VSTCALL hostCb(AEffect*, int32_t op, int32_t, VstIntPtr, void*, float) { return op == 1 ? 2400 : 0; }
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { std::printf("FALLO: %s\n", msg); fails++; } else std::printf("ok: %s\n", msg); } while (0)
static const double PI = 3.14159265358979323846;
static const double FS = 44100;

static void process(AIEExciter& p, const std::vector<float>& L, const std::vector<float>& R, std::vector<float>& oL, std::vector<float>& oR) {
    std::vector<float> l = L, r = R;
    oL.resize(L.size()); oR.resize(R.size());
    for (size_t s = 0; s < L.size(); s += 512) {
        int len = int(std::min<size_t>(512, L.size() - s));
        float* in[2] = {l.data() + s, r.data() + s};
        float* out[2] = {oL.data() + s, oR.data() + s};
        p.run(in, out, len);
    }
}
static double tone(const std::vector<float>& x, double f, int i0, int i1) {
    double re = 0, im = 0;
    for (int i = i0; i < i1; ++i) { re += x[i] * std::cos(2 * PI * f * i / FS); im += x[i] * std::sin(2 * PI * f * i / FS); }
    return std::sqrt(re * re + im * im) * 2 / (i1 - i0);
}
static std::vector<float> sine(double f, double a, int n) {
    std::vector<float> v(n);
    for (int i = 0; i < n; ++i) v[i] = float(a * std::sin(2 * PI * f * i / FS));
    return v;
}
static double rms(const std::vector<float>& x, int i0, int i1) { double s = 0; for (int i = i0; i < i1; ++i) s += double(x[i]) * x[i]; return std::sqrt(s / (i1 - i0)); }

int main(int argc, char** argv) {
    const int n = 44100;
    char m[200];
    std::vector<float> oL, oR;
    {   // 1) Amount 0: nivel plano y latencia de 69 muestras
        AIEExciter p(hostCb);
        p.setSampleRate(FS);
        double worst = 0;
        for (double f : {60.0, 400.0, 3000.0, 12000.0}) {
            std::vector<float> s = sine(f, 0.3, n);
            process(p, s, s, oL, oR);
            worst = std::max(worst, std::fabs(20 * std::log10(tone(oL, f, 8000, n) / 0.3)));
        }
        std::vector<float> imp(2048, 0.0f);
        imp[0] = 1;
        AIEExciter q(hostCb);
        q.setSampleRate(FS);
        process(q, imp, imp, oL, oR);
        int pk = int(std::max_element(oL.begin(), oL.end(), [](float a, float b) { return std::fabs(a) < std::fabs(b); }) - oL.begin());
        std::snprintf(m, sizeof m, "Amount 0: nivel plano (desviación %.3f dB) y latencia declarada %d (pico en %d)", worst, p.fx.initialDelay, pk);
        CHECK(worst < 0.05 && p.fx.initialDelay == 69 && pk >= 69 && pk < 75, m);
    }
    {   // 2) perfiles: carácter de armónicos (pares / impares) en un seno de 300 Hz (banda 2)
        const char* names[kNumProfiles] = {"Tube", "Tape", "Retro", "Warm", "Triode", "Dual Triode", "Warm Tape"};
        double h2r[kNumProfiles], h3r[kNumProfiles];
        for (int pr = 0; pr < kNumProfiles; ++pr) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            p.params[kAmt1 + 1] = 0.6f;
            p.params[kProf1 + 1] = float(pr) / (kNumProfiles - 1);
            std::vector<float> s = sine(300, 0.3, n);
            process(p, s, s, oL, oR);
            double h1 = tone(oL, 300, 8000, n), h2 = tone(oL, 600, 8000, n), h3 = tone(oL, 900, 8000, n);
            h2r[pr] = 20 * std::log10(h2 / h1 + 1e-12);
            h3r[pr] = 20 * std::log10(h3 / h1 + 1e-12);
            std::printf("   %-11s  H2 %6.1f dB  H3 %6.1f dB\n", names[pr], h2r[pr], h3r[pr]);
        }
        CHECK(h2r[kTube] > -40 && h2r[kTriode] > -40 && h2r[kWarm] > -40, "Tube, Triode y Warm generan armónicos pares (H2 > -40 dB; los simétricos quedan en -62)");
        // (el residuo de H2 a ~-60 dB es la fuga de la propia medida, ventana rectangular)
        CHECK(h2r[kTape] < -55 && h2r[kRetro] < -55 && h3r[kTape] > -45 && h3r[kRetro] > -45, "Tape y Retro: simétricos (H2 en el ruido de la medida), con H3");
        CHECK(h3r[kDualTriode] > h3r[kTriode], "Dual Triode más denso en impares que Triode");
    }
    {   // 3) más Amount = más armónicos; Mix 0 = sin cambios
        double prev = -200;
        bool mono = true;
        for (float a : {0.2f, 0.5f, 0.9f}) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            p.params[kAmt1 + 1] = a;
            std::vector<float> s = sine(300, 0.3, n);
            process(p, s, s, oL, oR);
            double h3 = 20 * std::log10(tone(oL, 900, 8000, n) + 1e-12);
            mono = mono && h3 > prev;
            prev = h3;
        }
        CHECK(mono, "más Amount genera más armónicos");
        AIEExciter p(hostCb), ref(hostCb);
        p.setSampleRate(FS); ref.setSampleRate(FS);
        p.params[kAmt1 + 1] = 0.9f;
        p.params[kMix1 + 1] = 0.0f;
        std::vector<float> s = sine(300, 0.3, n), rL, rR;
        process(p, s, s, oL, oR);
        process(ref, s, s, rL, rR);
        double err = 0;
        for (int i = 0; i < n; ++i) err = std::max(err, double(std::fabs(oL[i] - rL[i])));
        std::snprintf(m, sizeof m, "Mix 0 = señal limpia (diferencia %.1e)", err);
        CHECK(err < 1e-6, m);
    }
    {   // 4) Delta: salida = solo lo añadido; normal = limpia + delta
        AIEExciter p(hostCb), d(hostCb), ref(hostCb);
        p.setSampleRate(FS); d.setSampleRate(FS); ref.setSampleRate(FS);
        for (AIEExciter* e : {&p, &d}) { e->params[kAmt1 + 2] = 0.7f; e->params[kAmt1 + 0] = 0.5f; }
        d.params[kDelta] = 1.0f;
        std::vector<float> s(n), r(n), pL, pR, dL, dR, rL, rR;
        for (int i = 0; i < n; ++i) { s[i] = float(0.3 * std::sin(2 * PI * 80 * i / FS) + 0.2 * std::sin(2 * PI * 2500 * i / FS)); r[i] = s[i] * 0.8f; }
        process(p, s, r, pL, pR);
        process(d, s, r, dL, dR);
        process(ref, s, r, rL, rR);
        double err = 0;
        for (int i = 0; i < n; ++i) err = std::max(err, std::fabs(double(pL[i]) - (double(rL[i]) + dL[i])));
        std::snprintf(m, sizeof m, "Delta: salida normal = limpia + lo añadido (error %.1e), delta RMS %.4f", err, rms(dL, 8000, n));
        CHECK(err < 1e-5 && rms(dL, 8000, n) > 1e-3, m);
    }
    {   // 5) Mid: no toca una señal puramente lateral; Side: no toca una señal mono
        AIEExciter pm(hostCb), ps(hostCb), ref(hostCb);
        pm.setSampleRate(FS); ps.setSampleRate(FS); ref.setSampleRate(FS);
        pm.params[kAmt1 + 1] = 0.9f; pm.params[kMode1 + 1] = float(kMid) / (kNumModes - 1);
        ps.params[kAmt1 + 1] = 0.9f; ps.params[kMode1 + 1] = float(kSide) / (kNumModes - 1);
        std::vector<float> s = sine(300, 0.3, n), ns(n), rL, rR;
        for (int i = 0; i < n; ++i) ns[i] = -s[i];
        process(pm, s, ns, oL, oR);  // solo lados
        double hm = tone(oL, 900, 8000, n);
        process(ps, s, s, oL, oR);   // solo centro
        double hs = tone(oL, 900, 8000, n);
        AIEExciter pm2(hostCb);
        pm2.setSampleRate(FS);
        pm2.params[kAmt1 + 1] = 0.9f; pm2.params[kMode1 + 1] = float(kMid) / (kNumModes - 1);
        process(pm2, s, s, oL, oR);  // centro con modo Mid: sí añade
        double hm2 = tone(oL, 900, 8000, n);
        std::snprintf(m, sizeof m, "modo Mid ignora los lados (H3 %.1e) y modo Side ignora el centro (H3 %.1e); Mid en el centro sí (%.1e)", hm, hs, hm2);
        CHECK(hm < 1e-4 && hs < 1e-4 && hm2 > 1e-3, m);  // 1e-4 = -70 dB respecto al seno
    }
    {   // 6) Transient / Sustain: ráfagas cortas vs seno continuo
        std::vector<float> burst(n), cont = sine(300, 0.3, n);
        for (int i = 0; i < n; ++i) { double t = std::fmod(i / FS, 0.25); burst[i] = float(0.3 * std::exp(-t * 60) * std::sin(2 * PI * 300 * i / FS)); }
        double res[2][2];
        for (int md = 0; md < 2; ++md) {
            for (int sig = 0; sig < 2; ++sig) {
                AIEExciter p(hostCb), d(hostCb);
                p.setSampleRate(FS);
                p.params[kAmt1 + 1] = 0.9f;
                p.params[kMode1 + 1] = float(md == 0 ? kTransient : kSustain) / (kNumModes - 1);
                p.params[kDelta] = 1.0f;
                const std::vector<float>& x = sig == 0 ? burst : cont;
                process(p, x, x, oL, oR);
                res[md][sig] = rms(oL, 8000, n) / rms(x, 8000, n);
            }
        }
        std::snprintf(m, sizeof m, "Transient añade más en ráfagas (%.3f) que en continuo (%.3f); Sustain al revés (%.3f / %.3f)", res[0][0], res[0][1], res[1][0], res[1][1]);
        CHECK(res[0][0] > 5 * res[0][1] && res[1][1] > res[1][0], m);
    }
    {   // 7) sobremuestreo: menos aliasing (Retro fuerte en 7 kHz, banda 4)
        double ali[2];
        for (int k = 0; k < 2; ++k) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            p.params[kAmt1 + 3] = 1.0f;
            p.params[kProf1 + 3] = float(kRetro) / (kNumProfiles - 1);
            p.params[kOversampling] = k ? 1.0f : 0.0f;
            p.params[kDelta] = 1.0f;
            std::vector<float> s = sine(7123, 0.5, n);
            process(p, s, s, oL, oR);
            // energía total menos la de los armónicos verdaderos (7123, 21369 -> solo 7123 cabe por debajo de 22 kHz)
            double tot = 0, n0 = 0;
            for (int i = 8000; i < n; ++i) { tot += double(oL[i]) * oL[i]; n0++; }
            double h = tone(oL, 7123, 8000, n), h3 = tone(oL, 21369, 8000, n);
            ali[k] = 10 * std::log10(std::max(1e-20, tot / n0 - 0.5 * h * h - 0.5 * h3 * h3));
        }
        std::snprintf(m, sizeof m, "sobremuestreo x8 reduce el aliasing: x1 %.1f dB -> x8 %.1f dB", ali[0], ali[1]);
        CHECK(ali[1] < ali[0] - 15, m);
    }
    {   // 8) nivel estable a -12 dBFS con Amount 100 % y sin valores raros
        bool finite = true;
        double worst = 0;
        for (int pr = 0; pr < kNumProfiles; ++pr) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            for (int b = 0; b < kBands; ++b) { p.params[kAmt1 + b] = 1.0f; p.params[kProf1 + b] = float(pr) / (kNumProfiles - 1); }
            std::vector<float> s = sine(300, 0.25, n);
            process(p, s, s, oL, oR);
            for (float v : oL) finite = finite && std::isfinite(v);
            worst = std::max(worst, std::fabs(20 * std::log10(rms(oL, 8000, n) / rms(s, 8000, n))));
        }
        std::snprintf(m, sizeof m, "Amount 100 %% a -12 dBFS en los 7 perfiles: nivel ±%.1f dB, sin valores raros", worst);
        CHECK(finite && worst < 1.5, m);
        // el volumen no depende del nivel de entrada: igual a -36, -12 y -3 dBFS
        double w2 = 0;
        for (int pr = 0; pr < kNumProfiles; ++pr)
            for (double a : {0.0158, 0.25, 0.7}) {
                AIEExciter q(hostCb);
                q.setSampleRate(FS);
                q.params[kAmt1 + 1] = 1.0f;
                q.params[kProf1 + 1] = float(pr) / (kNumProfiles - 1);
                std::vector<float> s = sine(300, a, n);
                process(q, s, s, oL, oR);
                w2 = std::max(w2, std::fabs(20 * std::log10(rms(oL, 8000, n) / rms(s, 8000, n))));
            }
        std::snprintf(m, sizeof m, "Amount 100 %%: el volumen no cambia ni en pasajes flojos ni fuertes (-36/-12/-3 dBFS, ±%.1f dB)", w2);
        CHECK(w2 < 1.5, m);
        // misma proporción de armónicos a -36 y a -6 dBFS
        double hr[2];
        for (int k = 0; k < 2; ++k) {
            AIEExciter q(hostCb);
            q.setSampleRate(FS);
            q.params[kAmt1 + 1] = 0.6f;
            std::vector<float> s = sine(300, k ? 0.5 : 0.0158, n);
            process(q, s, s, oL, oR);
            hr[k] = 20 * std::log10(tone(oL, 900, 8000, n) / tone(oL, 300, 8000, n));
        }
        std::snprintf(m, sizeof m, "armónicos independientes del nivel: H3 %.1f dB a -36 dBFS y %.1f dB a -6 dBFS", hr[0], hr[1]);
        CHECK(std::fabs(hr[0] - hr[1]) < 2, m);
    }
    // 9) interfaz
    AIEExciter p(hostCb);
    p.setSampleRate(FS);
    gui::ExciterEditor ed(&p);
    static int nextChoice = -1;
    ed.menu = [](void*, int, int, const std::vector<std::string>&, int) { return nextChoice; };
    nextChoice = kRetro;
    ed.mouseDown(ed.profBtn(2).x + 5, ed.profBtn(2).y + 5, false, false, 0, 0);
    CHECK(p.profileOf(2) == kRetro && ed.selected == 2, "menú de perfil (Retro) y la banda queda seleccionada");
    nextChoice = kSide;
    ed.mouseDown(ed.modeBtn(3).x + 5, ed.modeBtn(3).y + 5, false, false, 0, 0);
    CHECK(p.modeOf(3) == kSide, "menú de modo (Side)");
    gui::Rect fa = ed.amtFader(1);
    ed.mouseDown(fa.x + 8, fa.b(), false, false, 0, 0);
    ed.mouseMove(fa.x + 8, fa.b() - fa.h / 2, false);
    ed.mouseUp();
    std::snprintf(m, sizeof m, "arrastrar Amount de la banda 2 a la mitad (%.0f %%)", p.params[kAmt1 + 1] * 100);
    CHECK(std::fabs(p.params[kAmt1 + 1] - 0.5f) < 0.03f, m);
    ed.mouseDown(ed.btnOs[3].x + 5, ed.btnOs[3].y + 5, false, false, 0, 0);
    CHECK(p.osIdx() == 3, "botón x8");
    ed.mouseDown(ed.tglDelta.x + 5, ed.tglDelta.y + 5, false, false, 0, 0);
    CHECK(p.params[kDelta] >= 0.5f, "botón Delta");
    p.params[kDelta] = 0;
    ed.mouseDown(ed.bypBtn(0).x + 5, ed.bypBtn(0).y + 5, false, false, 0, 0);
    CHECK(p.params[kByp1] >= 0.5f, "botón Bypass");
    p.params[kByp1] = 0;
    // audio de ejemplo para la vista previa: ritmo con bajo, acordes y platos
    std::vector<float> L(n * 3), R(n * 3), out1, out2;
    unsigned seed = 3;
    for (int i = 0; i < n * 3; ++i) {
        double t = i / FS, tb = std::fmod(t, 0.5);
        seed = seed * 1664525u + 1013904223u;
        double nz = (seed >> 8) / 16777216.0 - 0.5;
        double v = 0.5 * std::exp(-tb * 12) * std::sin(2 * PI * 55 * t) + 0.12 * std::sin(2 * PI * 330 * t) + 0.08 * std::sin(2 * PI * 2200 * t) + 0.05 * nz * std::exp(-std::fmod(t + 0.25, 0.5) * 30);
        L[i] = float(v); R[i] = float(v * 0.9 + 0.02 * nz);
    }
    p.params[kAmt1 + 0] = 0.35f; p.params[kAmt1 + 1] = 0.5f; p.params[kAmt1 + 2] = 0.3f; p.params[kAmt1 + 3] = 0.45f;
    p.params[kMode1 + 3] = float(kSide) / (kNumModes - 1);
    p.params[kOversampling] = 1.0f / 3;
    for (size_t s = 0; s + 512 <= L.size(); s += 512) {
        float* in[2] = {L.data() + s, R.data() + s};
        std::vector<float> a(512), b(512);
        float* out[2] = {a.data(), b.data()};
        p.run(in, out, 512);
        if ((s / 512) % 3 == 0) ed.tick();
    }
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 20; ++i) ed.draw();
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 20;
    std::snprintf(m, sizeof m, "dibujo de un fotograma: %.1f ms", ms);
    CHECK(ms < 25, m);
    // CPU del audio a x2 y x8 con las 4 bandas activas
    for (int os : {1, 3}) {
        p.params[kOversampling] = float(os) / 3;
        auto c0 = std::chrono::steady_clock::now();
        for (size_t s = 0; s + 512 <= L.size(); s += 512) {
            float* in[2] = {L.data() + s, R.data() + s};
            std::vector<float> a(512), b(512);
            float* out[2] = {a.data(), b.data()};
            p.run(in, out, 512);
        }
        double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - c0).count();
        std::printf("   CPU x%d: %.1f %% de un núcleo\n", 1 << os, sec / 3.0 * 100);
    }
    p.params[kOversampling] = 1.0f / 3;
    if (argc > 1) {
        ed.selected = 1;
        ed.draw();
        FILE* f = std::fopen(argv[1], "wb");
        std::fprintf(f, "P6 %d %d 255\n", ed.cv.w, ed.cv.h);
        for (uint32_t px : ed.cv.px) { unsigned char c[3] = {(unsigned char)(px >> 16), (unsigned char)(px >> 8), (unsigned char)px}; std::fwrite(c, 1, 3, f); }
        std::fclose(f);
    }
    std::printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas de AIE Exciter superadas\n", fails);
    return fails ? 1 : 0;
}
