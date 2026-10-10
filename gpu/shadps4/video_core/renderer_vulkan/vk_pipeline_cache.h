// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <shared_mutex>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <variant>
#include <boost/container/static_vector.hpp>
#include <tsl/robin_map.h>
#include "shader_recompiler/profile.h"
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/specialization.h"
#include "video_core/renderer_vulkan/vk_cache_consistency.h"
#include "video_core/renderer_vulkan/vk_compute_pipeline.h"
#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"
#include "video_core/renderer_vulkan/vk_resource_pool.h"

template <>
struct std::hash<vk::ShaderModule> {
    std::size_t operator()(const vk::ShaderModule& module) const noexcept {
        return std::hash<size_t>{}(reinterpret_cast<size_t>((VkShaderModule)module));
    }
};

namespace AmdGpu {
class Liverpool;
}

namespace Serialization {
struct Archive;
}

namespace Shader {
struct Info;
}

namespace Vulkan {

class Instance;
class Scheduler;
class ShaderCache;

struct Program {
    struct Module {
        vk::ShaderModule module;
        Shader::StageSpecialization spec;
        /// bbport: slot reserved by a thread that is still compiling it (guarded by
        /// PipelineCache::programs_mutex; the module handle is null until it is done).
        bool compiling = false;
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    Shader::Info info;
    ModuleList modules{};
    /// bbport: permutation of the previous lookup, compared first (the GPU thread writes it
    /// while workers read it).
    std::atomic<size_t> last_used{0};
    /// bbport: `info` as translated, for draw-preparation workers (they must not read `info`,
    /// whose user data the GPU thread rewrites every draw). Guarded by programs_mutex.
    std::unique_ptr<Shader::Info> info_template;
    /// bbport: XXH3 of the code bytes `info` stands for, set with info_template. A program made by
    /// CompileNewProgram holds the hash of the bytes it was translated from. One loaded by the
    /// pipeline cache warm-up was not translated here: its hash is of the bytes at its address
    /// when the GPU thread first used it. A permutation a worker translated from other bytes than
    /// these does not belong here (CompilePermutation drops it; see bbport_spec_drop.h).
    u64 code_hash{};

    Program() = default;
    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)
        : info{stage, l_stage, params} {}

    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec) {
        modules.emplace_back(module, std::move(spec));
    }

    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                      size_t perm_idx) {
        modules.resize(std::max(modules.size(), perm_idx + 1)); // <-- beware of realloc
        modules[perm_idx] = {module, std::move(spec)};
    }
};

struct DrawIndirectParams {
    u16 vertex_sgpr_offset;
    u32 instance_sgpr_offset;
};

} // namespace Vulkan

namespace AmdGpu {
union Regs;
}

namespace Vulkan {

/// bbport: state of one graphics/compute pipeline selection. The GPU thread owns one
/// (PipelineCache::sel); draw-preparation workers use their own with their register copies.
struct PipelineSelection {
    const AmdGpu::Regs* regs{};
    std::array<Shader::RuntimeInfo, MaxShaderStages> runtime_infos{};
    std::array<const Shader::Info*, MaxShaderStages> infos{};
    std::array<vk::ShaderModule, MaxShaderStages> modules{};
    std::optional<Shader::Gcn::FetchShaderData> fetch_shader{};
    GraphicsPipelineKey graphics_key{};
    bool motion = false;
    DrawIndirectParams draw_indirect_params{};
    struct PrepWorker* worker{}; ///< set: read-only selection for a draw-preparation worker
};

/// bbport: a draw-preparation worker's own program state (see vk_draw_prep.h).
struct PrepWorker {
    struct Stage {
        const Program* program;
        u64 hash;
        Shader::HwStage hw_stage;
        VAddr pgm_base;
        const std::vector<u32>* flat;
    };
    // Node-based: stages keep pointers to these Infos while later stages are inserted.
    std::unordered_map<const Program*, Shader::Info> infos;
    boost::container::static_vector<Stage, MaxShaderStages> stages;
    bool failed = false;
    /// Translator memory for the shaders this worker compiles (BB_ASYNC_COMPILE).
    std::unique_ptr<Shader::Pools> pools;
};

/// bbport: compile time of the draw-preparation workers, and what the GPU thread waited for
/// them (BB_FRAME_STATS prints these; g_bb_compile_* in the .cpp is the GPU thread's own).
extern std::atomic<u64> g_bb_worker_compile_ns;
extern std::atomic<u32> g_bb_worker_compiles;
extern std::atomic<u64> g_bb_wait_ns;
extern std::atomic<u32> g_bb_waits;
extern std::atomic<u32> g_bb_wait_timeouts;
/// bbport: worker translations thrown away (NoteDiscarded, which CompileNewProgram and
/// CompilePermutation call): the guest code they read was not the program's, it changed while
/// they were translated, or (CompilePermutation) their resources differ from the program's Info.
extern std::atomic<u32> g_bb_spec_discards;

struct PreparedDraw;

class PipelineCache {
public:
    explicit PipelineCache(const Instance& instance, Scheduler& scheduler,
                           AmdGpu::Liverpool* liverpool, u32 sparse_page_shift);
    ~PipelineCache();

    void WarmUp();
    void Sync();

    bool LoadComputePipeline(Serialization::Archive& ar);
    bool LoadGraphicsPipeline(Serialization::Archive& ar);
    bool LoadPipelineStage(Serialization::Archive& ar, size_t stage,
                           CacheCheck::StageBindings& facts);
    /// bbport: forgets the stages a cache entry that is not used left in `sel`.
    void DropSelection();

    const GraphicsPipeline* GetGraphicsPipeline(const DrawIndirectParams params = {},
                                                const PreparedDraw* prepared = nullptr);

    /// bbport: worker side of draw preparation: selects the pipeline key for `sel.regs`. With
    /// BB_ASYNC_COMPILE it also compiles the programs, permutations and the pipeline that do
    /// not exist yet (so the GPU thread finds them); without it, false when one is missing.
    bool PrepareGraphicsPipeline(PipelineSelection& sel);

    /// bbport: GPU-thread side: the pipeline for a prepared draw after checking that registers
    /// and flattened user data match; null to take the regular path.
    const GraphicsPipeline* TryPreparedPipeline(const PreparedDraw& prepared);

    /// bbport: the prepared draw the last GetGraphicsPipeline used, or null (regular path).
    [[nodiscard]] const PreparedDraw* UsedPrepared() const noexcept {
        return used_prepared;
    }

    const ComputePipeline* GetComputePipeline();

    using Result = std::tuple<const Shader::Info*, vk::ShaderModule,
                              std::optional<Shader::Gcn::FetchShaderData>, u64>;
    Result GetProgram(PipelineSelection& sel, Shader::HwStage stage, Shader::SwStage l_stage,
                      const Shader::ShaderParams& params, Shader::Backend::Bindings& binding);

    std::optional<vk::ShaderModule> ReplaceShader(vk::ShaderModule module,
                                                  std::span<const u32> spv_code);

    static std::string GetShaderName(Shader::HwStage stage, u64 hash,
                                     std::optional<size_t> perm = {});

    auto& GetProfile() const {
        return profile;
    }

private:
    bool RefreshGraphicsKey(PipelineSelection& sel);
    bool RefreshGraphicsStages(PipelineSelection& sel);
    bool RefreshComputeKey();

    // bbport: compiles can run on the draw-preparation workers (BB_ASYNC_COMPILE), so the
    // program, permutation and pipeline caches are shared between threads. A thread that needs
    // something another one is compiling waits for it (a bounded time) instead of compiling it
    // twice; a permutation is reserved in its program before it is compiled, so its index (it
    // names its files in the shader cache) never depends on who finishes first.
    Program* FindProgram(u64 hash);
    /// The compiled permutation of `program` that `spec` selects, if any.
    static std::optional<size_t> FindReadyPermutation(const Program& program,
                                                      const Shader::StageSpecialization& spec);
    Result GetPermutation(Program& program, Shader::HwStage hw_stage, Shader::SwStage sw_stage,
                          const Shader::ShaderParams& params, Shader::Backend::Bindings& binding,
                          Shader::RuntimeInfo& runtime_info);
    /// Translates the program for `params`. Null when it exists already or (speculative) another
    /// thread is on it: the caller looks it up again.
    std::optional<Result> CompileNewProgram(Shader::HwStage hw_stage, Shader::SwStage sw_stage,
                                            const Shader::ShaderParams& params,
                                            Shader::Backend::Bindings& binding,
                                            Shader::RuntimeInfo& runtime_info,
                                            Shader::Pools& translator_pools, bool speculative);
    /// Compiles the permutation `spec` of `program`; `info` is the Info the result refers to.
    /// Null (speculative only): another thread has it under way, or reading guest memory failed.
    std::optional<Result> CompilePermutation(Program& program, Shader::Info& info,
                                             const Shader::StageSpecialization& spec,
                                             Shader::HwStage hw_stage, Shader::SwStage sw_stage,
                                             const Shader::ShaderParams& params,
                                             Shader::Backend::Bindings& binding,
                                             Shader::RuntimeInfo& runtime_info,
                                             Shader::Pools& translator_pools, bool speculative);
    Result GetProgramSpeculative(PipelineSelection& sel, Shader::HwStage hw_stage,
                                 Shader::SwStage sw_stage, const Shader::ShaderParams& params,
                                 Shader::Backend::Bindings& binding,
                                 Shader::RuntimeInfo& runtime_info);
    /// Worker side: makes sure the pipeline of `sel.graphics_key` exists or is being compiled.
    bool EnsureGraphicsPipeline(PipelineSelection& sel);
    const GraphicsPipeline* PublishGraphicsPipeline(const GraphicsPipelineKey& key, u64 hash,
                                                    std::unique_ptr<GraphicsPipeline> pipeline,
                                                    GraphicsPipeline::SerializationSupport& sdata,
                                                    bool claimed);

    void DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage, size_t perm_idx,
                    std::string_view ext);
    std::optional<std::vector<u32>> GetShaderPatch(u64 hash, Shader::HwStage stage, size_t perm_idx,
                                                   std::string_view ext);
    /// `persist`: also write the SPIR-V to the shader cache (not for a duplicate compile of
    /// something another thread has under way). `spv_out`, when not persisting: receives the
    /// SPIR-V, for a caller that writes it only after checking the translation.
    vk::ShaderModule CompileModule(Shader::Info& info, Shader::RuntimeInfo& runtime_info,
                                   const std::span<const u32>& code, size_t perm_idx,
                                   Shader::Backend::Bindings& binding,
                                   Shader::Pools& translator_pools, bool persist,
                                   std::vector<u32>* spv_out = nullptr);
    const Shader::RuntimeInfo& BuildRuntimeInfo(PipelineSelection& sel, Shader::HwStage stage,
                                                Shader::SwStage l_stage);

    [[nodiscard]] bool IsPipelineCacheDirty() const {
        return num_new_pipelines > 0;
    }

    /// bbport: the driver's pipeline cache is kept in the game's cache directory between runs:
    /// created from the saved blob, written again every few dozen new pipelines or minute, and at
    /// shutdown.
    void CreateDriverCache();
    void SaveDriverCache();
    void DriverCacheSaverLoop(std::stop_token stop);

private:
    const Instance& instance;
    Scheduler& scheduler;
    AmdGpu::Liverpool* liverpool;
    DescriptorHeap desc_heap;
    vk::UniquePipelineCache pipeline_cache;
    vk::UniquePipelineLayout pipeline_layout;
    Shader::Profile profile{};
    Shader::Pools pools;
    tsl::robin_map<size_t, std::unique_ptr<Program>> program_cache;
    /// bbport: guards program_cache, Program::modules and Program::info_template: exclusive for
    /// insertions, shared for lookups from any thread.
    std::shared_mutex programs_mutex;
    std::condition_variable_any programs_cv;    ///< a program or permutation compile finished
    std::unordered_set<u64> programs_compiling; ///< hashes of programs being translated
    /// bbport: guards graphics_pipelines and pipelines_compiling.
    std::shared_mutex pipelines_mutex;
    std::condition_variable_any pipelines_cv; ///< a pipeline compile finished
    std::unordered_set<GraphicsPipelineKey> pipelines_compiling;
    bool async_compile{}; ///< BB_ASYNC_COMPILE, fixed at construction
    u64 prepared_hits = 0, prepared_misses = 0;
    const PreparedDraw* used_prepared = nullptr;
    tsl::robin_map<ComputePipelineKey, std::unique_ptr<ComputePipeline>> compute_pipelines;
    tsl::robin_map<GraphicsPipelineKey, std::unique_ptr<GraphicsPipeline>> graphics_pipelines;
    PipelineSelection sel{}; ///< GPU thread selection state
    ComputePipelineKey compute_key{};
    std::atomic<u32> num_new_pipelines{}; // new pipelines added to the cache since the game start
    std::atomic<u32> saved_pipelines{};   // value of num_new_pipelines at the last driver cache save
    std::mutex driver_cache_mutex;        // one driver cache save at a time
    u32 shutdown_hook{};                  // Storage::AddShutdownHook id, 0: none

    // Only if Config::collectShadersForDebug()
    tsl::robin_map<vk::ShaderModule,
                   std::vector<std::variant<GraphicsPipelineKey, ComputePipelineKey>>>
        module_related_pipelines;

    std::jthread driver_cache_saver; // last: stops before anything it uses is destroyed
};

} // namespace Vulkan
