// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone CPU checks of the shader cache's consistency rules: no game, Vulkan device or window.
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include "video_core/renderer_vulkan/vk_cache_consistency.h"

using namespace Vulkan::CacheCheck;
using Shader::Backend::Bindings;

namespace {

// Stage numbers as in Shader::SwStage.
constexpr size_t Fragment = 0, TessControl = 1, TessEval = 2, Vertex = 3;

StageBindings Stage(Bindings start, Bindings size, bool has_resources = true) {
    return {.present = true, .has_resources = has_resources, .start = start, .size = size};
}

} // namespace

int main() {
    std::array<StageBindings, 6> stages{};

    // A fragment shader of 19 descriptors (7 buffers) and the vertex shader compiled behind it.
    stages[Fragment] = Stage({0, 0, 0}, {19, 7, 2});
    stages[Vertex] = Stage({19, 7, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // The same vertex shader read back from a cache in which a later session wrote another
    // permutation under its name: it was compiled behind a fragment shader of 17 descriptors. The
    // layout built from the fragment shader gives binding 17 to a fragment sampler (the logged
    // VUID-VkGraphicsPipelineCreateInfo-layout-07988 of the shipped build).
    stages[Vertex] = Stage({17, 7, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // Each of the three counters is checked.
    stages[Vertex] = Stage({19, 6, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));
    stages[Vertex] = Stage({19, 7, 1}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // A vertex-only pipeline whose vertex shader was compiled behind a fragment shader.
    stages = {};
    stages[Vertex] = Stage({3, 1, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));
    stages[Vertex] = Stage({0, 0, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // A stage without resources keeps whatever start it was compiled with: it names no descriptor.
    stages = {};
    stages[Fragment] = Stage({0, 0, 0}, {4, 2, 1});
    stages[Vertex] = Stage({9, 9, 9}, {0, 0, 0}, false);
    assert(FirstMisplacedStage(stages) == -1);
    // ... but it still adds its own size for the stages after it.
    stages = {};
    stages[Fragment] = Stage({0, 0, 0}, {4, 2, 1});
    stages[TessControl] = Stage({9, 9, 9}, {2, 0, 3}, false);
    stages[TessEval] = Stage({6, 2, 4}, {1, 1, 0});
    stages[Vertex] = Stage({7, 3, 4}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);
    stages[Vertex] = Stage({6, 3, 4}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // Absent stages add nothing, and no stage at all is consistent.
    stages = {};
    assert(FirstMisplacedStage(stages) == -1);
    stages[Fragment] = Stage({0, 0, 0}, {2, 1, 0});
    stages[Vertex] = Stage({2, 1, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // A compute pipeline is one stage that starts at zero.
    const std::array<StageBindings, 1> compute{Stage({0, 0, 0}, {8, 4, 1})};
    assert(FirstMisplacedStage(compute) == -1);
    const std::array<StageBindings, 1> late_compute{Stage({1, 0, 0}, {8, 4, 1})};
    assert(FirstMisplacedStage(late_compute) == 0);

    // The profile blob: versions first, then the profile, and nothing else.
    constexpr size_t profile_size = 24;
    const std::array<u32, 4> versions{0x42425043u, 10, 7, 6};
    std::vector<u8> blob(sizeof(versions) + profile_size, 0xAB);
    std::memcpy(blob.data(), versions.data(), sizeof(versions));
    assert(HeaderMatches(blob, versions, profile_size));
    const std::array<u32, 4> bumped{0x42425043u, 11, 7, 6};
    assert(!HeaderMatches(blob, bumped, profile_size));
    // A blob of an older build holds the profile alone.
    assert(!HeaderMatches(std::vector<u8>(profile_size, 0xAB), versions, profile_size));
    blob.push_back(0);
    assert(!HeaderMatches(blob, versions, profile_size));
    assert(!HeaderMatches(std::vector<u8>{}, versions, profile_size));

    std::puts("PASS");
    return 0;
}
