// SPDX-License-Identifier: GPL-2.0-or-later
// bbport (Windows): <dlfcn.h> for the renderer's diagnostics, which name host code addresses.
// dladdr finds nothing here: the callers fall back to printing the address. On the include path
// of the GPU library on Windows only (CMakeLists.txt).
#pragma once
#ifdef _WIN32
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    const char* dli_fname;
    void* dli_fbase;
    const char* dli_sname;
    void* dli_saddr;
} Dl_info;
static inline int dladdr(const void* address, Dl_info* info) {
    (void)address;
    info->dli_fname = 0;
    info->dli_fbase = 0;
    info->dli_sname = 0;
    info->dli_saddr = 0;
    return 0;
}
#ifdef __cplusplus
}
#endif
#endif
