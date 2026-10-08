// SPDX-License-Identifier: GPL-2.0-or-later
// The type 0 header Bloodborne left in a command buffer on an RX 6700 XT (log of 2026-10-08):
// the GPU thread stepped over it and the draw preparation scanner stopped, so no prepared draw
// was used for the rest of the session. Both now skip by ResyncSkip.
#include <cassert>
#include <cstdint>
#include "gpu/shadps4/video_core/amdgpu/pm4_resync.h"

int main() {
    // From the log: [000001e4] 00000000 c0016900 000001e5 00000000 c0016900 000001e6 00000000
    const std::uint32_t bad[] = {0x000001e4, 0x00000000, 0xc0016900, 0x000001e5,
                                 0x00000000, 0xc0016900, 0x000001e6, 0x00000000};
    assert(AmdGpu::ResyncSkip(bad) == 2);

    // SET_CONTEXT_REG with 2 body dwords fits in 3 remaining dwords, not in 2.
    assert(AmdGpu::PlausibleType3(0xc0016900, 3));
    assert(!AmdGpu::PlausibleType3(0xc0016900, 2));
    // Reserved bits set, or not type 3: no header.
    assert(!AmdGpu::PlausibleType3(0xc0016904, 8));
    assert(!AmdGpu::PlausibleType3(0x000001e4, 8));

    // Nothing that can start a packet: skip to the end of the buffer.
    const std::uint32_t junk[] = {0x000001e4, 0x00000000, 0x00000000};
    assert(AmdGpu::ResyncSkip(junk) == 3);
    return 0;
}
