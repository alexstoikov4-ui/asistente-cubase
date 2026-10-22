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
    std::setvbuf(stdout, nullptr, _IONBF, 0);  // que se vea cada línea aunque algo falle
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
    {   // 2) comparación con Ozone 7 Exciter: ganancia, H2 y H3 de un seno de 1 kHz (banda 2 entre 200 Hz y 5 kHz)
        struct Ref { int prof; int amount; double level, gain, h2, h3; };
        static const Ref refs[] = {  // medidas de los renders de Ozone 7 (herramientas/medicion_ozone)
            {kTube, 5, -24, 1.14, -42.5, -33.6},
            {kTube, 5, -12, 0.08, -31.6, -38.4},
            {kTube, 5, -6, -0.17, -28.1, -32.3},
            {kTube, 15, -24, 6.88, -48.2, -9.8},
            {kTube, 15, -12, -5.03, -64.5, -9.6},
            {kTube, 15, -6, -11.01, -64.8, -9.6},
            {kTape, 5, -24, 2.97, -156.3, -53.3},
            {kTape, 5, -12, 1.18, -167.9, -28.3},
            {kTape, 5, -6, -3.58, -150.3, -17.9},
            {kTape, 15, -24, 8.62, -147.0, -15.8},
            {kTape, 15, -12, -3.19, -149.8, -14.2},
            {kTape, 15, -6, -9.14, -148.9, -13.9},
            {kRetro, 5, -24, 2.73, -158.7, -26.1},
            {kRetro, 5, -12, -1.71, -165.7, -17.6},
            {kRetro, 5, -6, -5.37, -161.5, -14.7},
            {kRetro, 15, -24, 7.29, -157.7, -18.8},
            {kRetro, 15, -12, -0.28, -170.1, -13.3},
            {kRetro, 15, -6, -5.21, -167.5, -11.7},
            {kWarm, 5, -24, 0.02, -33.4, -157.2},
            {kWarm, 5, -12, 0.02, -26.2, -168.8},
            {kWarm, 5, -6, 0.02, -22.6, -168.9},
            {kWarm, 15, -24, -0.96, -19.4, -163.0},
            {kWarm, 15, -12, -0.96, -12.2, -167.1},
            {kWarm, 15, -6, -0.96, -8.6, -177.3},
            {kTriode, 5, -24, 2.09, -33.0, -42.6},
            {kTriode, 5, -12, 2.23, -30.6, -75.8},
            {kTriode, 5, -6, 1.98, -23.6, -43.2},
            {kTriode, 15, -24, 8.58, -24.8, -44.6},
            {kTriode, 15, -12, 2.47, -10.2, -23.0},
            {kTriode, 15, -6, -3.63, -6.1, -28.1},
            {kDualTriode, 5, -24, 0.36, -65.0, -128.9},
            {kDualTriode, 5, -12, 0.54, -37.0, -38.4},
            {kDualTriode, 5, -6, 0.42, -21.7, -38.3},
            {kDualTriode, 15, -24, 8.69, -13.2, -23.2},
            {kDualTriode, 15, -12, -1.69, -11.5, -13.2},
            {kDualTriode, 15, -6, -7.65, -12.0, -12.3},
        };
        const int i0 = 8000, i1 = 8000 + 441 * 80;  // número entero de ciclos de 1 kHz
        double worstG = 0, worstH = 0;
        int cases = 0, bad = 0;
        for (const Ref& r : refs) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            p.params[kX1] = float(normFromFreq(200)); p.params[kX2] = float(normFromFreq(5000)); p.params[kX3] = float(normFromFreq(12000));
            p.params[kAmt1 + 1] = float(r.amount / 15.0);
            p.params[kProf1 + 1] = float(r.prof) / (kNumProfiles - 1);
            p.params[kOversampling] = 1.0f;
            double a = std::pow(10.0, r.level / 20);
            std::vector<float> s = sine(1000, a, i1);
            process(p, s, s, oL, oR);
            double h1 = tone(oL, 1000, i0, i1), g = 20 * std::log10(h1 / a);
            double h2 = 20 * std::log10(tone(oL, 2000, i0, i1) / h1 + 1e-12), h3 = 20 * std::log10(tone(oL, 3000, i0, i1) / h1 + 1e-12);
            // un armónico que en Ozone no existe (< -50 dB) solo cuenta si aquí pasa de -45 dB
            double eg = std::fabs(g - r.gain);
            double e2 = r.h2 > -50 ? std::fabs(h2 - r.h2) : std::max(0.0, h2 + 45);
            double e3 = r.h3 > -50 ? std::fabs(h3 - r.h3) : std::max(0.0, h3 + 45);
            bool ok = eg < 2.5 && e2 < 4 && e3 < 4;
            worstG = std::max(worstG, eg);
            worstH = std::max(worstH, std::max(e2, e3));
            bad += !ok;
            ++cases;
            std::printf("   %-11s %2d %5.0f dBFS  ganancia %+6.2f/%+6.2f  H2 %6.1f/%6.1f  H3 %6.1f/%6.1f%s\n", kProfileNames[r.prof], r.amount,
                        r.level, g, r.gain, h2, r.h2, h3, r.h3, ok ? "" : "  <-");
        }
        std::snprintf(m, sizeof m, "parecido a Ozone (mío/Ozone) en %d de %d casos; peor: ganancia %.1f dB, armónicos %.1f dB", cases - bad, cases, worstG, worstH);
        CHECK(bad <= 4, m);
    }
    {   // 3) más Amount = más armónicos (Retro, respecto al fundamental); Mix 0 = sin cambios
        double prev = -200;
        bool mono = true;
        for (float a : {0.2f, 0.5f, 0.9f}) {
            AIEExciter p(hostCb);
            p.setSampleRate(FS);
            p.params[kAmt1 + 1] = a;
            p.params[kProf1 + 1] = float(kRetro) / (kNumProfiles - 1);
            std::vector<float> s = sine(300, 0.1, n);
            process(p, s, s, oL, oR);
            double h3 = 20 * std::log10(tone(oL, 900, 8000, n) / tone(oL, 300, 8000, n) + 1e-12);
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
        double hm = tone(oL, 600, 8000, 8000 + 147 * 240);  // banda 2 = Warm: armónico 2 (ciclos enteros)
        process(ps, s, s, oL, oR);   // solo centro
        double hs = tone(oL, 600, 8000, 8000 + 147 * 240);
        AIEExciter pm2(hostCb);
        pm2.setSampleRate(FS);
        pm2.params[kAmt1 + 1] = 0.9f; pm2.params[kMode1 + 1] = float(kMid) / (kNumModes - 1);
        process(pm2, s, s, oL, oR);  // centro con modo Mid: sí añade
        double hm2 = tone(oL, 600, 8000, 8000 + 147 * 240);
        std::snprintf(m, sizeof m, "modo Mid ignora los lados (H2 %.1e) y modo Side ignora el centro (H2 %.1e); Mid en el centro sí (%.1e)", hm, hs, hm2);
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
    {   // 8) sin valores raros con Amount 15 en los 7 perfiles, también con picos por encima de 0 dBFS
        bool finite = true;
        double peak = 0;
        for (int pr = 0; pr < kNumProfiles; ++pr)
            for (double a : {0.0158, 0.5, 1.4}) {
                AIEExciter p(hostCb);
                p.setSampleRate(FS);
                for (int b = 0; b < kBands; ++b) { p.params[kAmt1 + b] = 1.0f; p.params[kProf1 + b] = float(pr) / (kNumProfiles - 1); }
                std::vector<float> s(n);
                for (int i = 0; i < n; ++i) s[i] = float(a * (0.6 * std::sin(2 * PI * 80 * i / FS) + 0.4 * std::sin(2 * PI * 3100 * i / FS)));
                process(p, s, s, oL, oR);
                for (float v : oL) { finite = finite && std::isfinite(v); peak = std::max(peak, double(std::fabs(v))); }
            }
        std::snprintf(m, sizeof m, "Amount 15 en los 7 perfiles (-36 a +3 dBFS): sin valores raros, pico de salida %.2f", peak);
        CHECK(finite && peak < 8, m);
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
