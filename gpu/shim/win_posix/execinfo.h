// SPDX-License-Identifier: GPL-2.0-or-later
// bbport (Windows): <execinfo.h> for the renderer's diagnostics (call stacks of waits and of
// concurrent recording). No stack walk here: backtrace finds no frames. On the include path of
// the GPU library on Windows only (CMakeLists.txt).
#pragma once
#ifdef _WIN32
#ifdef __cplusplus
extern "C" {
#endif
static inline int backtrace(void** frames, int size) {
    (void)frames;
    (void)size;
    return 0;
}
static inline char** backtrace_symbols(void* const* frames, int size) {
    (void)frames;
    (void)size;
    return 0;
}
static inline void backtrace_symbols_fd(void* const* frames, int size, int fd) {
    (void)frames;
    (void)size;
    (void)fd;
}
#ifdef __cplusplus
}
#endif
#endif
