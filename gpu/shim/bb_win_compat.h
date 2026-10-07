// SPDX-License-Identifier: GPL-2.0-or-later
// bbport (Windows): small POSIX helpers the renderer uses for diagnostics and thread
// identity. Force-included into the GPU library on Windows only (CMakeLists.txt); kept free
// of <windows.h> so its macros do not reach every translation unit.
#pragma once
#ifdef _WIN32
#ifdef __cplusplus
extern "C" {
#endif
__declspec(dllimport) unsigned long __stdcall GetCurrentThreadId(void);
static inline int gettid(void) { return (int)GetCurrentThreadId(); }
// POSIX pause(): the caller waits for a signal that never comes here (assert.cpp, restarting).
__declspec(dllimport) void __stdcall Sleep(unsigned long milliseconds);
static inline int pause(void) {
    for (;;) {
        Sleep(0xffffffffUL);
    }
}
#ifdef __cplusplus
}
#endif
#endif
