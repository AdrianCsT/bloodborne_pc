// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: checks on a pipeline cache entry that need neither Vulkan nor a game (see
// tests/test_cache_consistency.cpp).
#pragma once

#include <array>
#include <cstddef>
#include <cstring>
#include <span>
#include "common/types.h"
#include "shader_recompiler/backend/bindings.h"

namespace Vulkan::CacheCheck {

/// What a graphics or compute pipeline in the shader cache says about one of its stages.
struct StageBindings {
    bool present{};
    /// At least one buffer, image or sampler of the stage has a valid sharp in the stored
    /// permutation (StageSpecialization::bitset): only then is `start` part of what the
    /// permutation is, and a lookup at another start does not find it.
    bool has_resources{};
    /// Where the stored SPIR-V numbers its first descriptor (and user data register).
    Shader::Backend::Bindings start{};
    /// What the stage adds for the stages after it (Info::AddBindings).
    Shader::Backend::Bindings size{};
};

/// Stages are numbered one after the other in stage order (fragment first), and the descriptor set
/// layout of the pipeline is built the same way from the stages' resource lists. A pipeline whose
/// stored stage starts anywhere else was assembled from shaders that were compiled for different
/// neighbours, so its layout and its SPIR-V disagree about which descriptor is which. Returns the
/// index of the first such stage, or -1. A stage without a valid resource is skipped: its start is
/// not part of its identity (StageSpecialization::operator== ignores it), so a running game
/// legitimately pairs such a permutation with fragment shaders of any size, and a pipeline key
/// written that way is sound.
inline int FirstMisplacedStage(std::span<const StageBindings> stages) {
    Shader::Backend::Bindings expected{};
    for (size_t i = 0; i < stages.size(); ++i) {
        const auto& stage = stages[i];
        if (!stage.present) {
            continue;
        }
        if (stage.has_resources && stage.start != expected) {
            return static_cast<int>(i);
        }
        expected.unified += stage.size.unified;
        expected.buffer += stage.size.buffer;
        expected.user_data += stage.size.user_data;
    }
    return -1;
}

/// The "profile" blob of a cache is {versions..., Shader::Profile}. Whoever changes what a cache
/// entry means bumps a version; a blob without this header (an older build) or with other numbers
/// is not compatible and the whole cache is rebuilt.
inline bool HeaderMatches(std::span<const u8> blob, std::span<const u32> versions,
                          size_t profile_size) {
    const size_t header = versions.size_bytes();
    return blob.size() == header + profile_size &&
           std::memcmp(blob.data(), versions.data(), header) == 0;
}

} // namespace Vulkan::CacheCheck
