// Ventana de editor para Windows reutilizable: crea una ventana hija de la que da el anfitrión, copia el
// lienzo (gui::Canvas) a pantalla ~30 veces por segundo y pasa el ratón al editor.
//
// El editor (clase Ed) debe tener: gui::Canvas cv; static const int W, H, kFrameMs; void tick(); void draw();
// bool mouseDown(int x, int y, bool dbl, bool fine, int sx, int sy); bool mouseMove(int x, int y, bool fine);
// bool mouseUp(); bool wheel(int x, int y, int steps, bool fine); bool dragging() const;
// y un miembro 'int (*menu)(void* ctx, int x, int y, const std::vector<std::string>&, int)' con 'void* menuCtx'
// para mostrar menús emergentes.
#pragma once
#if defined(_WIN32)

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <cstring>
#include <string>
#include <vector>

namespace win {

inline HINSTANCE dllInstance() {
    HMODULE m = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&dllInstance), &m);
    return m;
}

template <class Ed>
class EditorWindow {
public:
    HWND hwnd = nullptr;
    Ed* ed;
    explicit EditorWindow(Ed* e) : ed(e) {
        ed->menu = &EditorWindow::showMenu;
        ed->menuCtx = this;
    }

    // className debe ser único por plugin
    static bool open(EditorWindow*& slot, Ed* e, HWND parent, const char* className, int& counter) {
        if (slot) return true;
        HINSTANCE inst = dllInstance();
        if (counter == 0) {
            WNDCLASSA wc;
            std::memset(&wc, 0, sizeof wc);
            wc.style = CS_DBLCLKS;
            wc.lpfnWndProc = &EditorWindow::proc;
            wc.hInstance = inst;
            wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
            wc.lpszClassName = className;
            RegisterClassA(&wc);
        }
        EditorWindow* w = new EditorWindow(e);
        w->hwnd = CreateWindowExA(0, className, "", WS_CHILD | WS_VISIBLE, 0, 0, Ed::W, Ed::H, parent, nullptr, inst, w);
        if (!w->hwnd) {
            delete w;
            if (counter == 0) UnregisterClassA(className, inst);
            return false;
        }
        ++counter;
        slot = w;
        return true;
    }

    // se borra la clase al cerrar la última ventana: si el anfitrión descarga la DLL no queda una clase
    // registrada apuntando a código que ya no existe
    static void close(EditorWindow*& slot, const char* className, int& counter) {
        if (!slot) return;
        DestroyWindow(slot->hwnd);
        delete slot;
        slot = nullptr;
        if (--counter == 0) UnregisterClassA(className, dllInstance());
    }

private:
    static int showMenu(void* ctx, int x, int y, const std::vector<std::string>& items, int checked) {
        EditorWindow* w = static_cast<EditorWindow*>(ctx);
        HMENU m = CreatePopupMenu();
        for (size_t i = 0; i < items.size(); ++i)
            AppendMenuA(m, MF_STRING | (int(i) == checked ? MF_CHECKED : 0), i + 1, items[i].c_str());
        int r = TrackPopupMenu(m, TPM_RETURNCMD | TPM_NONOTIFY | TPM_LEFTALIGN | TPM_TOPALIGN, x, y, 0, w->hwnd, nullptr);
        DestroyMenu(m);
        return r > 0 ? r - 1 : -1;
    }

    void paintTo(HDC dc) {
        ed->draw();
        BITMAPINFO bi;
        std::memset(&bi, 0, sizeof bi);
        bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bi.bmiHeader.biWidth = Ed::W;
        bi.bmiHeader.biHeight = -Ed::H;  // de arriba abajo
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        SetDIBitsToDevice(dc, 0, 0, Ed::W, Ed::H, 0, 0, 0, Ed::H, ed->cv.px.data(), &bi, DIB_RGB_COLORS);
    }

    static LRESULT CALLBACK proc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
        EditorWindow* w = reinterpret_cast<EditorWindow*>(GetWindowLongPtrA(h, GWLP_USERDATA));
        switch (msg) {
            case WM_CREATE: {
                CREATESTRUCTA* cs = reinterpret_cast<CREATESTRUCTA*>(lp);
                SetWindowLongPtrA(h, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(cs->lpCreateParams));
                SetTimer(h, 1, Ed::kFrameMs, nullptr);
                return 0;
            }
            case WM_TIMER:
                if (w) { w->ed->tick(); InvalidateRect(h, nullptr, FALSE); }
                return 0;
            case WM_ERASEBKGND: return 1;
            case WM_PAINT: {
                PAINTSTRUCT ps;
                HDC dc = BeginPaint(h, &ps);
                if (w) w->paintTo(dc);
                EndPaint(h, &ps);
                return 0;
            }
            case WM_PRINTCLIENT:
                if (w) w->paintTo(reinterpret_cast<HDC>(wp));
                return 0;
            case WM_LBUTTONDOWN:
            case WM_LBUTTONDBLCLK: {
                if (!w) break;
                POINT pt = {(short)LOWORD(lp), (short)HIWORD(lp)};
                POINT sp = pt;
                ClientToScreen(h, &sp);
                bool fine = (wp & (MK_SHIFT | MK_CONTROL)) != 0;
                bool redraw = w->ed->mouseDown(pt.x, pt.y, msg == WM_LBUTTONDBLCLK, fine, sp.x, sp.y);
                if (w->ed->dragging()) SetCapture(h);
                if (redraw) InvalidateRect(h, nullptr, FALSE);
                return 0;
            }
            case WM_MOUSEMOVE:
                if (w && w->ed->mouseMove((short)LOWORD(lp), (short)HIWORD(lp), (wp & (MK_SHIFT | MK_CONTROL)) != 0))
                    InvalidateRect(h, nullptr, FALSE);
                return 0;
            case WM_LBUTTONUP:
                if (w && w->ed->dragging()) ReleaseCapture();
                if (w && w->ed->mouseUp()) InvalidateRect(h, nullptr, FALSE);
                return 0;
            case WM_CAPTURECHANGED:
                if (w) w->ed->mouseUp();
                return 0;
            case WM_MOUSEWHEEL: {
                if (!w) break;
                POINT pt = {(short)LOWORD(lp), (short)HIWORD(lp)};
                ScreenToClient(h, &pt);
                int steps = GET_WHEEL_DELTA_WPARAM(wp) / WHEEL_DELTA;
                if (w->ed->wheel(pt.x, pt.y, steps, (GET_KEYSTATE_WPARAM(wp) & (MK_SHIFT | MK_CONTROL)) != 0))
                    InvalidateRect(h, nullptr, FALSE);
                return 0;
            }
            case WM_DESTROY:
                KillTimer(h, 1);
                return 0;
        }
        return DefWindowProcA(h, msg, wp, lp);
    }
};

}  // namespace win
#endif
