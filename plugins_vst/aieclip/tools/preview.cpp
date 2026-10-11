// Renderiza la interfaz de AIE Clip a un archivo PPM con audio de prueba (para revisarla sin Windows).
// g++ -O2 -I.. preview.cpp -o preview && ./preview salida.ppm [modo 0-3] [oversampling 0-5]
#include "../aieclip.cpp"

static VstIntPtr VSTCALL fakeHost(AEffect*, int32_t op, int32_t, VstIntPtr, void*, float) { return op == 1 ? 2400 : 0; }

int main(int argc, char** argv) {
    AIEClip p(fakeHost);
    p.sampleRate = 44100;
    if (argc > 2) p.params[kMode] = float(atoi(argv[2])) / (kNumModes - 1);
    if (argc > 3) p.params[kOversampling] = float(atoi(argv[3])) / (kNumOs - 1);
    p.params[kInput] = AIEClip::paramFromInputDb(4.0);
    p.params[kSoftness] = 0.35f;
    PluginEditorHost h(&p);
    gui::Editor ed(&h);
    // "batería" sintética: bombo cada 0,5 s + caja + ruido suave
    const int block = 512, fs = 44100;
    std::vector<float> L(block), R(block), oL(block), oR(block);
    float* in[2] = {L.data(), R.data()};
    float* out[2] = {oL.data(), oR.data()};
    unsigned seed = 1;
    long n = 0;
    for (int frame = 0; frame < 400; ++frame) {
        for (int b = 0; b < 3; ++b) {  // ~3 bloques por fotograma de 33 ms
            for (int i = 0; i < block; ++i, ++n) {
                double t = double(n % (fs / 2)) / fs, t2 = double((n + fs / 4) % (fs / 2)) / fs;
                double kick = 0.9 * std::exp(-t * 18) * std::sin(2 * M_PI * (50 + 120 * std::exp(-t * 40)) * t);
                seed = seed * 1664525u + 1013904223u;
                double noise = ((seed >> 8) / 16777216.0 - 0.5);
                double snare = (n / (fs / 2)) % 2 ? 0.6 * std::exp(-t2 * 25) * noise : 0.0;
                double pad = 0.12 * std::sin(2 * M_PI * 220 * n / fs) + 0.08 * noise;
                L[i] = float(kick + snare + pad);
                R[i] = float(kick + snare * 0.8 + pad * 0.9);
            }
            p.run(in, out, block);
        }
        ed.tickMeters();
    }
    ed.draw();
    FILE* f = std::fopen(argc > 1 ? argv[1] : "preview.ppm", "wb");
    std::fprintf(f, "P6 %d %d 255\n", ed.cv.w, ed.cv.h);
    for (uint32_t v : ed.cv.px) { unsigned char c[3] = {(unsigned char)(v >> 16), (unsigned char)(v >> 8), (unsigned char)v}; std::fwrite(c, 1, 3, f); }
    std::fclose(f);
    return 0;
}
