// Interfaz binaria VST 2.4 mínima (estructura AEffect y códigos de operación estándar), sin el SDK de
// Steinberg. Compartida por los plugins nuevos (AIE Scope); AIE Clip lleva su propia copia equivalente.
#pragma once

#include <cstdint>
#include <cstring>

#if defined(_WIN32)
#define VSTCALL __cdecl
#define VSTEXPORT extern "C" __declspec(dllexport)
#else
#define VSTCALL
#define VSTEXPORT extern "C" __attribute__((visibility("default")))
#endif

typedef intptr_t VstIntPtr;
struct AEffect;
typedef VstIntPtr(VSTCALL* audioMasterCallback)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
typedef VstIntPtr(VSTCALL* AEffectDispatcherProc)(AEffect*, int32_t, int32_t, VstIntPtr, void*, float);
typedef void(VSTCALL* AEffectProcessProc)(AEffect*, float**, float**, int32_t);
typedef void(VSTCALL* AEffectProcessDoubleProc)(AEffect*, double**, double**, int32_t);
typedef void(VSTCALL* AEffectSetParameterProc)(AEffect*, int32_t, float);
typedef float(VSTCALL* AEffectGetParameterProc)(AEffect*, int32_t);

struct AEffect {
    int32_t magic;
    AEffectDispatcherProc dispatcher;
    AEffectProcessProc process;
    AEffectSetParameterProc setParameter;
    AEffectGetParameterProc getParameter;
    int32_t numPrograms;
    int32_t numParams;
    int32_t numInputs;
    int32_t numOutputs;
    int32_t flags;
    VstIntPtr resvd1;
    VstIntPtr resvd2;
    int32_t initialDelay;
    int32_t realQualities;
    int32_t offQualities;
    float ioRatio;
    void* object;
    void* user;
    int32_t uniqueID;
    int32_t version;
    AEffectProcessProc processReplacing;
    AEffectProcessDoubleProc processDoubleReplacing;
    char future[56];
};

namespace vst {
enum {
    effOpen = 0, effClose = 1, effSetProgram = 2, effGetProgram = 3, effGetProgramName = 5,
    effGetParamLabel = 6, effGetParamDisplay = 7, effGetParamName = 8, effSetSampleRate = 10, effSetBlockSize = 11,
    effMainsChanged = 12, effEditGetRect = 13, effEditOpen = 14, effEditClose = 15, effEditIdle = 19,
    effGetProgramNameIndexed = 29, effGetPlugCategory = 35, effGetEffectName = 45, effGetVendorString = 47,
    effGetProductString = 48, effGetVendorVersion = 49, effCanDo = 51, effGetTailSize = 52, effGetVstVersion = 58
};
enum { audioMasterAutomate = 0, audioMasterVersion = 1, audioMasterBeginEdit = 43, audioMasterEndEdit = 44 };
enum { effFlagsHasEditor = 1 << 0, effFlagsCanReplacing = 1 << 4, effFlagsCanDoubleReplacing = 1 << 12 };
enum { kPlugCategEffect = 1, kPlugCategAnalysis = 3 };
static const int32_t kEffectMagic = 0x56737450;  // 'VstP'
struct ERect { int16_t top, left, bottom, right; };

inline void copyStr(void* dst, const char* src, size_t max) {
    char* d = static_cast<char*>(dst);
    std::strncpy(d, src, max - 1);
    d[max - 1] = 0;
}
}  // namespace vst
