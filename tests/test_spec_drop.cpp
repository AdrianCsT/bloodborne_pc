// Regression: the rule that throws away a shader translated ahead of the GPU thread. No game, Vulkan
// device or window.
//   ninja -C out/gpu spec-drop-test && out/gpu/spec-drop-test.exe
#include <cassert>
#include <cstdio>
#include <cstring>
#include "bbport_spec_drop.h"

using BbSpec::DropReason;

int main() {
    constexpr uint64_t program = 0x1234;
    // A worker that read the program's bytes, saw them unchanged and listed its resources is kept.
    assert(DropReason(true, program, program, program, true) == nullptr);

    // Bytes at the address that are not the program's: dropped, whatever else is true.
    const char* other = DropReason(true, 0x9999, 0x9999, program, true);
    assert(other && std::strstr(other, "not the program's"));
    // Code that changed while it translated.
    const char* torn = DropReason(true, program, 0x9999, program, true);
    assert(torn && std::strstr(torn, "changed while"));
    // Other resources than the program's Info.
    const char* resources = DropReason(true, program, program, program, false);
    assert(resources && std::strstr(resources, "resources"));

    // The first rule names the reason when several apply.
    assert(DropReason(true, 0x9999, 0x8888, program, false) == other);
    assert(DropReason(true, program, 0x8888, program, false) == torn);

    // The GPU thread's own translation is never dropped, however it differs.
    assert(DropReason(false, 0x9999, 0x8888, program, false) == nullptr);
    assert(DropReason(false, program, program, program, true) == nullptr);

    std::puts("Speculative shader drop: PASS (kept when it matches, each reason, first reason wins, in-order never dropped)");
    return 0;
}
