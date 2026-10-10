// bbport: when a shader translated ahead of the GPU thread is thrown away (see
// PipelineCache::CompilePermutation and tests/test_spec_drop.cpp). Pure: no Vulkan, no game.
#pragma once

#include <cstdint>

namespace BbSpec {

/// Why a permutation translated by a draw-preparation worker (`speculative`) must not be used, or
/// nullptr to keep it. A worker runs at idle priority and can fall behind the GPU thread until the
/// game has reused the memory of the draw it is preparing; its translation then comes from other
/// bytes than the program's, or lists other resources than the program's Info, which the pipeline
/// layout is built from. The GPU thread translates such a draw in order, and what it translates is
/// never dropped: nothing is left to fall back to.
///  code_before / code_after: hash of the worker's code before and after it translated.
///  program_code_hash: Program::code_hash, the bytes the program's Info stands for.
///  same_resources: the translation lists the resources of the program's Info.
/// The first rule that applies names the reason.
inline const char* DropReason(bool speculative, uint64_t code_before, uint64_t code_after,
                              uint64_t program_code_hash, bool same_resources) {
    if (!speculative) {
        return nullptr;
    }
    if (code_before != program_code_hash) {
        return "the code at its address is not the program's";
    }
    if (code_after != code_before) {
        return "its code changed while it was translated";
    }
    if (!same_resources) {
        return "its resources differ from the program's";
    }
    return nullptr;
}

} // namespace BbSpec
