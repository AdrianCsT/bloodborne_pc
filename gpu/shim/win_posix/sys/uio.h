// SPDX-License-Identifier: GPL-2.0-or-later
// bbport (Windows): <sys/uio.h> for the renderer's diagnostics and safe reads of guest memory.
// process_vm_readv reads the current process's own memory (ReadProcessMemory on the
// pseudo-handle): a bad address yields a short read instead of a fault, as on Linux. Only the
// calling process is supported. On the include path of the GPU library on Windows only
// (CMakeLists.txt); kept free of <windows.h>.
#pragma once
#ifdef _WIN32
#include <stddef.h>
#include <string.h>
#include <process.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif
struct iovec {
    void* iov_base;
    size_t iov_len;
};
__declspec(dllimport) int __stdcall ReadProcessMemory(void* process, const void* base, void* buffer,
                                                      size_t size, size_t* read);

static inline ssize_t process_vm_readv(int pid, const struct iovec* local, unsigned long local_count,
                                       const struct iovec* remote, unsigned long remote_count,
                                       unsigned long flags) {
    (void)pid;
    (void)flags;
    ssize_t total = 0;
    unsigned long li = 0, ri = 0;
    size_t lo = 0, ro = 0;
    while (li < local_count && ri < remote_count) {
        const size_t room = local[li].iov_len - lo, left = remote[ri].iov_len - ro;
        const size_t n = room < left ? room : left;
        if (n) {
            size_t got = 0;
            const int ok = ReadProcessMemory((void*)(ptrdiff_t)-1,
                                             (const char*)remote[ri].iov_base + ro,
                                             (char*)local[li].iov_base + lo, n, &got);
            total += (ssize_t)got;
            if (!ok || got != n) {
                return total ? total : -1;
            }
        }
        lo += n;
        ro += n;
        if (lo == local[li].iov_len) {
            ++li;
            lo = 0;
        }
        if (ro == remote[ri].iov_len) {
            ++ri;
            ro = 0;
        }
    }
    return total;
}
#ifdef __cplusplus
}
#endif
#endif
