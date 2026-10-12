// Sobremuestreo x1/x2/x4/x8 con etapas x2 de media banda (half-band) de fase lineal, el mismo diseño medido en
// AIE Clip (banda de paso plana hasta 20 kHz a 44,1 kHz, ~90 dB de rechazo). La latencia es fija para todos los
// factores (se rellena con retardo), así cambiar el factor no obliga al anfitrión a reiniciar el plugin.
//
// Uso: Oversampler os; os.setStages(n);  y = os.process(x, f);  donde f es una función x -> f(x) que se aplica a
// la frecuencia alta (por ejemplo, una saturación). kLatency muestras de retardo en todos los factores.
#pragma once

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace ovs {

static const int kMaxStages = 3;                     // hasta x8
static const int kStageLen[kMaxStages] = {123, 25, 17};
static const double kKaiserBeta = 9.0;
static const int kHist = 64;
static const int kPadBuf = 128;

// retardo de 'stages' etapas en muestras (entero gracias a las longitudes elegidas)
inline int stageLatency(int stages) {
    int num = 0;
    for (int s = 0; s < stages; ++s) num += ((kStageLen[s] - 1) / 2) << (kMaxStages - 1 - s);
    return num >> (kMaxStages - 1);
}
static const int kLatency = 69;  // = stageLatency(kMaxStages)

struct HalfBand {
    int L = 0, c = 0, pc = 0;
    double center2 = 0, center = 0;
    int centerDelay = 0;
    std::vector<double> dense, dense2;
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
        for (auto& v : h) v /= sum;
        center = h[c]; center2 = 2.0 * h[c];
        centerDelay = (c - pc) / 2;
        dense.clear(); dense2.clear();
        for (int i = 1 - pc; i < L; i += 2) { dense.push_back(h[i]); dense2.push_back(2.0 * h[i]); }
    }
};

// filtros compartidos (se calculan una vez)
inline const HalfBand* filters() {
    static HalfBand hb[kMaxStages];
    static bool ready = false;
    if (!ready) { for (int s = 0; s < kMaxStages; ++s) hb[s].design(kStageLen[s]); ready = true; }
    return hb;
}

struct Hist {
    double buf[2 * kHist];
    int pos;
    void clear() { std::memset(buf, 0, sizeof buf); pos = 0; }
    inline void push(double v) { pos = (pos - 1) & (kHist - 1); buf[pos] = buf[pos + kHist] = v; }
    inline const double* at() const { return buf + pos; }
};

inline double dot(const double* a, const double* b, int n) {
    double s0 = 0, s1 = 0;
    int i = 0;
    for (; i + 1 < n; i += 2) { s0 += a[i] * b[i]; s1 += a[i + 1] * b[i + 1]; }
    if (i < n) s0 += a[i] * b[i];
    return s0 + s1;
}

class Oversampler {
public:
    Oversampler() { filters(); clear(); }
    void setStages(int s) {
        s = std::max(0, std::min(kMaxStages, s));
        if (s == stages) return;
        stages = s;
        pad = kLatency - stageLatency(stages);
        clear();
    }
    int factor() const { return 1 << stages; }
    void clear() {
        for (int s = 0; s < kMaxStages; ++s) { up[s].clear(); d0[s].clear(); d1[s].clear(); }
        std::memset(padBuf, 0, sizeof padBuf);
        padPos = 0;
    }
    template <typename F>
    inline double process(double x, F& f) {
        double y = stage(0, x, f);
        padPos = (padPos + 1) & (kPadBuf - 1);
        padBuf[padPos] = y;
        return padBuf[(padPos - pad) & (kPadBuf - 1)];
    }

private:
    int stages = -1, pad = kLatency, padPos = 0;
    Hist up[kMaxStages], d0[kMaxStages], d1[kMaxStages];
    double padBuf[kPadBuf];

    template <typename F>
    double stage(int s, double x, F& f) {
        if (s >= stages) return f(x);
        const HalfBand& h = filters()[s];
        const int nd = int(h.dense.size());
        Hist& uh = up[s];
        uh.push(x);
        double uc = h.center2 * uh.at()[h.centerDelay];
        double ud = dot(h.dense2.data(), uh.at(), nd);
        double u0 = h.pc == 0 ? uc : ud, u1 = h.pc == 0 ? ud : uc;
        double v0 = stage(s + 1, u0, f);
        double v1 = stage(s + 1, u1, f);
        d0[s].push(v0);
        double z;
        if (h.pc == 0) z = h.center * d0[s].at()[h.centerDelay] + dot(h.dense.data(), d1[s].at(), nd);
        else z = dot(h.dense.data(), d0[s].at(), nd) + h.center * d1[s].at()[h.centerDelay];
        d1[s].push(v1);
        return z;
    }
};

// Retardo entero fijo (para alinear la señal seca con la procesada)
class Delay {
public:
    void clear() { std::memset(buf, 0, sizeof buf); pos = 0; }
    Delay() { clear(); }
    inline double process(double x, int d) {
        pos = (pos + 1) & (kPadBuf - 1);
        buf[pos] = x;
        return buf[(pos - d) & (kPadBuf - 1)];
    }

private:
    double buf[kPadBuf];
    int pos = 0;
};

}  // namespace ovs
