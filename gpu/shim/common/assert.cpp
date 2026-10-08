// bbport: GPU-side assertion failures stop the port with exit code 23.
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include "common/assert.h"
#include "common/logging/log.h"
#include <unistd.h>

// bb-probe (probe.c): set while the port restarts itself through run.sh. The device fd is closed
// before exec; Vulkan calls failing then are not errors: this thread waits for the exec instead.
extern "C" __attribute__((weak)) volatile int runtime_restarting; // absent in the tests

// bbport: set by the pipeline cache. A speculative shader translation (draw preparation ahead
// of the GPU thread) reads descriptors the game may still be rewriting; a check failing on such
// a torn descriptor ends that attempt like a fault (the hook does not return) instead of
// stopping the port. Returns when no speculative recovery point is armed on this thread.
void (*g_assert_speculative_exit)() = nullptr;

void assert_fail_impl() {
    if (&runtime_restarting && runtime_restarting) {
        for (;;) {
            pause();
        }
    }
    if (g_assert_speculative_exit) {
        g_assert_speculative_exit();
    }
    std::fflush(stdout);
    std::fputs("STOP: GPU library assertion failed (see GPU log above)\n", stderr);
    std::_Exit(23);
}

[[noreturn]] void unreachable_impl() {
    assert_fail_impl();
    throw std::runtime_error("Unreachable code");
}

void assert_fail_debug_msg(const char* msg) {
    LOG_CRITICAL(Debug, "Assertion Failed!\n{}", msg);
    assert_fail_impl();
}
