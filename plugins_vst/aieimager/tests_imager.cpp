// Pruebas de AIE Imager sin ventana. Con un argumento guarda una vista previa (PPM).
// g++ -O2 tests_imager.cpp -o tests_imager && ./tests_imager [vista.ppm]
#include "aieimager.cpp"

static VstIntPtr VSTCALL hostCb(AEffect*, int32_t op, int32_t, VstIntPtr, void*, float) { return op == 1 ? 2400 : 0; }
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { std::printf("FALLO: %s\n", msg); fails++; } else std::printf("ok: %s\n", msg); } while (0)
static const double PI = 3.14159265358979323846;

// estéreo de prueba: tonos con distinta imagen en cada banda + ruido ancho
static void signal(std::vector<float>& L, std::vector<float>& R, int n, double fs, unsigned seed = 1) {
    L.resize(n); R.resize(n);
    for (int i = 0; i < n; ++i) {
        double t = i / fs;
        seed = seed * 1664525u + 1013904223u;
        double nz = (seed >> 8) / 16777216.0 - 0.5;
        seed = seed * 1664525u + 1013904223u;
        double nz2 = (seed >> 8) / 16777216.0 - 0.5;
        double bass = 0.3 * std::sin(2 * PI * 60 * t);                       // mono
        double mid = 0.2 * std::sin(2 * PI * 500 * t);
        double hi = 0.1 * std::sin(2 * PI * 9000 * t);
        L[i] = float(bass + mid + 0.8 * hi + 0.1 * nz);
        R[i] = float(bass + 0.6 * mid - 0.5 * hi + 0.1 * nz2);
    }
}
static void process(AIEImager& p, std::vector<float>& L, std::vector<float>& R, std::vector<float>& oL, std::vector<float>& oR) {
    oL.resize(L.size()); oR.resize(R.size());
    for (size_t s = 0; s < L.size(); s += 512) {
        int len = int(std::min<size_t>(512, L.size() - s));
        float* in[2] = {L.data() + s, R.data() + s};
        float* out[2] = {oL.data() + s, oR.data() + s};
        p.run(in, out, len);
    }
}
// energía de una frecuencia (DFT de un solo término) entre i0 e i1
static double tone(const std::vector<float>& x, double f, double fs, int i0, int i1) {
    double re = 0, im = 0;
    for (int i = i0; i < i1; ++i) { re += x[i] * std::cos(2 * PI * f * i / fs); im += x[i] * std::sin(2 * PI * f * i / fs); }
    return std::sqrt(re * re + im * im) * 2 / (i1 - i0);
}

int main(int argc, char** argv) {
    const double fs = 44100;
    const int n = 44100;
    std::vector<float> L, R, oL, oR;
    signal(L, R, n, fs);

    {   // 1) todo al 100 %: mismo color (magnitud plana) y misma imagen estéreo en cada tono
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        process(p, L, R, oL, oR);
        double worst = 0;
        for (double f : {60.0, 500.0, 9000.0}) {
            for (int c = 0; c < 2; ++c) {
                const std::vector<float>& x = c ? R : L;
                const std::vector<float>& y = c ? oR : oL;
                double a = tone(x, f, fs, 8000, n), b = tone(y, f, fs, 8000, n);
                if (a > 0.01) worst = std::max(worst, std::fabs(20 * std::log10(b / a)));
            }
        }
        char m[96];
        std::snprintf(m, sizeof m, "todo al 100 %%: cada tono sale al mismo nivel en L y R (desviación %.3f dB)", worst);
        CHECK(worst < 0.05, m);
        // sin latencia: el pico de la respuesta a un impulso queda en las primeras muestras
        AIEImager q(hostCb);
        q.setSampleRate(fs);
        std::vector<float> iL(2048, 0.0f), iR(2048, 0.0f), rL, rR;
        iL[0] = iR[0] = 1.0f;
        process(q, iL, iR, rL, rR);
        int pk = int(std::max_element(rL.begin(), rL.end(), [](float a, float b) { return std::fabs(a) < std::fabs(b); }) - rL.begin());
        std::snprintf(m, sizeof m, "sin latencia (pico de la respuesta al impulso en la muestra %d)", pk);
        CHECK(pk < 8, m);
    }
    {   // 2) la suma mono es la misma con cualquier ajuste (sin Solo) que con todo al 100 %
        AIEImager ref(hostCb), p(hostCb);
        ref.setSampleRate(fs);
        p.setSampleRate(fs);
        std::vector<float> rL, rR;
        process(ref, L, R, rL, rR);
        float ws[4] = {0.0f, 1.0f, 0.2f, 0.85f}, sts[4] = {1.0f, 0.4f, 0.0f, 0.7f};
        for (int b = 0; b < kBands; ++b) { p.params[kW1 + b] = ws[b]; p.params[kS1 + b] = sts[b]; }
        process(p, L, R, oL, oR);
        double err = 0;
        for (int i = 0; i < n; ++i) err = std::max(err, std::fabs(double(oL[i] + oR[i]) - double(rL[i] + rR[i])));
        char m[96];
        std::snprintf(m, sizeof m, "suma mono intacta con anchuras y stereoize extremos (error %.1e)", err);
        CHECK(err < 1e-5, m);
    }
    {   // 3) Width 0 % en la banda de agudos: el tono de 9 kHz queda centrado (L = R)
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        p.params[kW1 + 3] = 0.0f;
        p.params[kX3] = float(normFromFreq(3000));  // corte lejos de 9 kHz (un cruce siempre solapa un poco)
        process(p, L, R, oL, oR);
        double l = tone(oL, 9000, fs, 8000, n), r = tone(oR, 9000, fs, 8000, n);
        std::vector<float> side(n);
        for (int i = 0; i < n; ++i) side[i] = 0.5f * (oL[i] - oR[i]);
        double s9 = tone(side, 9000, fs, 8000, n), s500 = tone(side, 500, fs, 8000, n);
        char m[160];
        std::snprintf(m, sizeof m, "Width 0 %% en agudos: 9 kHz en mono (L %.3f R %.3f, lateral %.4f) y 500 Hz sigue abierto (%.3f)", l, r, s9, s500);
        CHECK(s9 < 0.003 && s500 > 0.03, m);
    }
    {   // 4) Width 200 % en medios: el lateral de 500 Hz se duplica
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        process(p, L, R, oL, oR);
        std::vector<float> side(n);
        for (int i = 0; i < n; ++i) side[i] = 0.5f * (oL[i] - oR[i]);
        double before = tone(side, 500, fs, 8000, n);
        p.params[kW1 + 1] = 1.0f;
        p.reset();
        process(p, L, R, oL, oR);
        for (int i = 0; i < n; ++i) side[i] = 0.5f * (oL[i] - oR[i]);
        double after = tone(side, 500, fs, 8000, n);
        char m[128];
        std::snprintf(m, sizeof m, "Width 200 %% en medios: lateral de 500 Hz %.4f -> %.4f (x%.2f)", before, after, after / before);
        CHECK(std::fabs(after / before - 2.0) < 0.1, m);
    }
    {   // 5) Stereoize abre una banda mono sin cambiar la suma mono
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        std::vector<float> mL(n), mR(n);
        for (int i = 0; i < n; ++i) mL[i] = mR[i] = 0.3f * float(std::sin(2 * PI * 500 * i / fs) + 0.5 * std::sin(2 * PI * 730 * i / fs));
        AIEImager ref(hostCb);
        ref.setSampleRate(fs);
        std::vector<float> rL, rR;
        process(ref, mL, mR, rL, rR);
        p.params[kS1 + 1] = 1.0f;
        process(p, mL, mR, oL, oR);
        double se = 0, me = 0, err = 0;
        for (int i = 4000; i < n; ++i) {
            double s = 0.5 * (oL[i] - oR[i]), m = 0.5 * (oL[i] + oR[i]);
            se += s * s; me += m * m;
            err = std::max(err, std::fabs(double(oL[i] + oR[i]) - double(rL[i] + rR[i])));
        }
        double ratio = std::sqrt(se / me);
        char m[128];
        std::snprintf(m, sizeof m, "Stereoize 100 %% en medios: señal mono pasa a lateral/central %.2f, suma mono intacta (%.1e)", ratio, err);
        CHECK(ratio > 0.4 && err < 1e-5, m);
    }
    {   // 6) Solo en graves: solo queda el tono de 60 Hz
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        p.params[kSolo1] = 1.0f;
        process(p, L, R, oL, oR);
        double b60 = tone(oL, 60, fs, 8000, n), b500 = tone(oL, 500, fs, 8000, n), b9k = tone(oL, 9000, fs, 8000, n);
        char m[128];
        std::snprintf(m, sizeof m, "Solo banda 1: 60 Hz %.3f, 500 Hz %.4f, 9 kHz %.5f", b60, b500, b9k);
        CHECK(b60 > 0.25 && b500 < 0.01 && b9k < 0.002, m);
    }
    {   // 7) Mono: L = R
        AIEImager p(hostCb);
        p.setSampleRate(fs);
        p.params[kMono] = 1.0f;
        process(p, L, R, oL, oR);
        double err = 0;
        for (int i = 0; i < n; ++i) err = std::max(err, double(std::fabs(oL[i] - oR[i])));
        CHECK(err < 1e-7, "botón Mono: izquierda = derecha");
    }
    {   // 8) cortes siempre ordenados aunque se crucen
        AIEImager p(hostCb);
        p.params[kX1] = 0.9f; p.params[kX2] = 0.1f; p.params[kX3] = 0.5f;
        double f[3];
        p.crossovers(f);
        CHECK(f[0] < f[1] && f[1] < f[2], "cortes cruzados se ordenan solos");
    }
    // 9) interfaz: medidores, ratón y velocidad
    AIEImager p(hostCb);
    p.setSampleRate(fs);
    gui::ImagerEditor ed(&p);
    for (size_t s = 0; s + 512 <= L.size(); s += 512) {
        float* in[2] = {L.data() + s, R.data() + s};
        float* out[2] = {oL.data() + s, oR.data() + s};
        p.run(in, out, 512);
        ed.tick();
    }
    CHECK(ed.bandCorr[0] > 0.95f && ed.bandCorr[3] < 0.5f, "correlación por banda: graves mono (~+1), agudos abiertos");
    gui::Rect wf = ed.widthFader(2);
    ed.mouseDown(wf.x + 9, wf.y + wf.h / 2, false, false, 0, 0);
    ed.mouseMove(wf.x + 9, wf.y, false);
    ed.mouseUp();
    CHECK(p.widthOf(2) > 1.9, "arrastrar Width de la banda 3 hasta arriba = 200 %");
    ed.mouseDown(wf.x + 9, wf.y + 20, true, false, 0, 0);
    CHECK(std::fabs(p.widthOf(2) - 1.0) < 1e-6, "doble clic en Width = 100 %");
    double f0[3];
    p.crossovers(f0);
    gui::Rect h = ed.crossHandle(1);
    ed.mouseDown(h.x + 8, h.y + 10, false, false, 0, 0);
    ed.mouseMove(h.x + 8 + 60, h.y + 10, false);
    ed.mouseUp();
    double f1[3];
    p.crossovers(f1);
    char m[96];
    std::snprintf(m, sizeof m, "arrastrar el corte 2 a la derecha: %.0f Hz -> %.0f Hz", f0[1], f1[1]);
    CHECK(f1[1] > f0[1] * 1.5, m);
    gui::Rect so = ed.soloBtn(0);
    ed.mouseDown(so.x + 5, so.y + 5, false, false, 0, 0);
    CHECK(p.params[kSolo1] >= 0.5f, "botón Solo de la banda 1");
    ed.mouseDown(ed.btnReset.x + 5, ed.btnReset.y + 5, false, false, 0, 0);
    CHECK(p.params[kSolo1] < 0.5f && std::fabs(p.widthOf(2) - 1.0) < 1e-6, "Todo a 100 % deja anchuras y Solo por defecto");
    ed.mouseDown(ed.tglMono.x + 5, ed.tglMono.y + 5, false, false, 0, 0);
    CHECK(p.params[kMono] >= 0.5f, "botón Mono");
    p.params[kMono] = 0;
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 20; ++i) ed.draw();
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 20;
    std::snprintf(m, sizeof m, "dibujo de un fotograma: %.1f ms", ms);
    CHECK(ms < 25, m);
    if (argc > 1) {  // vista previa con ajustes de ejemplo
        p.params[kW1] = 0.0f; p.params[kW1 + 2] = 0.7f; p.params[kW1 + 3] = 0.8f; p.params[kS1 + 1] = 0.35f;
        for (size_t s = 0; s + 512 <= L.size(); s += 512) {
            float* in[2] = {L.data() + s, R.data() + s};
            float* out[2] = {oL.data() + s, oR.data() + s};
            p.run(in, out, 512);
            ed.tick();
        }
        ed.draw();
        FILE* f = std::fopen(argv[1], "wb");
        std::fprintf(f, "P6 %d %d 255\n", ed.cv.w, ed.cv.h);
        for (uint32_t px : ed.cv.px) { unsigned char c[3] = {(unsigned char)(px >> 16), (unsigned char)(px >> 8), (unsigned char)px}; std::fwrite(c, 1, 3, f); }
        std::fclose(f);
    }
    std::printf(fails ? "\n%d pruebas fallidas\n" : "\nTodas las pruebas de AIE Imager superadas\n", fails);
    return fails ? 1 : 0;
}
