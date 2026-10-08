// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: one rule for stepping over an invalid PM4 packet header.

#pragma once

#include <cstddef>
#include <cstdint>
#include <span>

namespace AmdGpu {

/// A dword that can start a type 3 packet: type 3, reserved bits clear, and the packet within
/// the `remaining` dwords (same bits as PM4Type3Header).
inline bool PlausibleType3(std::uint32_t dword, std::size_t remaining) {
    const std::uint32_t num_words = (((dword >> 16) & 0x3fff) + 1) & 0x3fff;
    return (dword >> 30) == 3 && (dword & 0xfc) == 0 && num_words < remaining;
}

/// Dwords from the invalid header at the front of `remaining` to the next dword that can start
/// a type 3 packet. Liverpool::ProcessGraphics and the draw preparation scanner both skip by
/// this: when the scanner stopped at the header instead, its register state fell out of step
/// with the GPU thread's for the rest of the session and no prepared draw was used again.
inline std::size_t ResyncSkip(std::span<const std::uint32_t> remaining) {
    std::size_t skip = 1;
    while (skip < remaining.size() && !PlausibleType3(remaining[skip], remaining.size() - skip)) {
        ++skip;
    }
    return skip;
}

} // namespace AmdGpu
