// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csetjmp>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <mutex>
#include <ranges>
#include <string>
#include <unordered_set>
#include <xxhash.h>

#include "common/hash.h"
#include "common/io_file.h"
#include "common/path_util.h"
#include "common/thread.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/motion_history.h"
#include "video_core/renderer_vulkan/vk_draw_prep.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_pipeline_serialization.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"
#include "bbport_spec_drop.h"
#include "bbport_toggles.h"
#ifdef _WIN32
#include <windows.h>
#endif

// bbport: common/assert.cpp calls it before stopping on a failed check (SpeculativeAssertExit).
extern void (*g_assert_speculative_exit)();
#ifdef _WIN32
extern "C" [[noreturn]] void bb_longjmp(sigjmp_buf buffer, int value);
#endif
extern "C" int runtime_memory_vma_info(uintptr_t address, int* prot, int* type, uintptr_t* end);
extern "C" const uint64_t* runtime_memory_generation(void);

namespace {
// bbport: work done ahead of the GPU thread reads game memory the game may still be writing. A
// C++ exception from that data (a container over its capacity, an empty optional) ends the
// attempt like a fault does, and the GPU thread translates the draw when it gets there; it must
// not end the game. bb_longjmp restores registers without unwinding, so a fault jump out of this
// try block is unaffected.
template <typename F>
bool SpeculativeCall(F&& f) {
    static std::atomic<int> logged{0};
    try {
        f();
        return true;
    } catch (const std::exception& e) {
        if (logged.fetch_add(1, std::memory_order_relaxed) < 8) {
            LOG_WARNING(Render_Vulkan, "Shader work ahead of the GPU thread stopped: {}",
                        e.what());
        }
    } catch (...) {
        if (logged.fetch_add(1, std::memory_order_relaxed) < 8) {
            LOG_WARNING(Render_Vulkan,
                        "Shader work ahead of the GPU thread stopped (unknown exception)");
        }
    }
    return false;
}

// bbport: a stage's program address points at readable memory. A malformed command buffer (a
// register packet whose values are the next packets) set a shader address of 0x4c0012d0000 and
// reading the program there ended the game; such a draw or dispatch is skipped instead.
template <typename Program>
bool ProgramReadable(const Program& pgm) {
    const auto address = reinterpret_cast<uintptr_t>(pgm.template Address<u32*>());
    if (!address) {
        return false;
    }
    // Checked for every stage of every draw: the last readable mapping found is remembered for
    // as long as the mapping table keeps its generation (an odd one is a change in progress).
    thread_local uintptr_t known_start = 0, known_end = 0;
    thread_local uint64_t known_generation = 1;
    const uint64_t generation = *runtime_memory_generation();
    if (generation == known_generation && address >= known_start && address < known_end) {
        return true;
    }
    int prot = 0, type = -1;
    uintptr_t end = 0;
    if (runtime_memory_vma_info(address, &prot, &type, &end)) {
        if ((prot & 1) != 0 && (generation & 1) == 0) {
            known_start = address; // the mapping's start is not known: from here on
            known_end = end;
            known_generation = generation;
        }
        return (prot & 1) != 0;
    }
#ifdef _WIN32
    // Not a guest mapping (the executable's image): readable committed memory.
    MEMORY_BASIC_INFORMATION info;
    const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ |
                           PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    const bool ok = VirtualQuery(reinterpret_cast<void*>(address), &info, sizeof(info)) &&
                    info.State == MEM_COMMIT && (info.Protect & readable) != 0 &&
                    (info.Protect & PAGE_GUARD) == 0;
#else
    const bool ok = true;
#endif
    if (!ok) {
        static int reports = 0;
        if (reports++ < 8) {
            std::fprintf(stderr, "Pipeline: shader program address %#llx is not readable; the draw "
                                 "is skipped\n", (unsigned long long)address);
        }
    }
    return ok;
}
} // namespace

namespace Vulkan {

using Shader::HwStage;
using Shader::Output;
using Shader::SwStage;

constexpr static auto SpirvVersion1_6 = 0x00010600U;

constexpr static std::array DescriptorHeapSizes = {
    vk::DescriptorPoolSize{vk::DescriptorType::eUniformBuffer, 512},
    vk::DescriptorPoolSize{vk::DescriptorType::eStorageBuffer, 8192},
    vk::DescriptorPoolSize{vk::DescriptorType::eSampledImage, 8192},
    vk::DescriptorPoolSize{vk::DescriptorType::eStorageImage, 1024},
    vk::DescriptorPoolSize{vk::DescriptorType::eSampler, 1024},
};

static u32 MapOutputs(std::span<Shader::OutputMap, 3> outputs, const AmdGpu::VsOutputControl& ctl) {
    u32 num_outputs = 0;

    if (ctl.vs_out_misc_enable) {
        auto& misc_vec = outputs[num_outputs++];
        misc_vec[0] = ctl.use_vtx_point_size ? Output::PointSize : Output::None;
        misc_vec[1] = ctl.use_vtx_edge_flag
                          ? Output::EdgeFlag
                          : (ctl.use_vtx_gs_cut_flag ? Output::GsCutFlag : Output::None);
        misc_vec[2] =
            ctl.use_vtx_kill_flag
                ? Output::KillFlag
                : (ctl.use_vtx_render_target_idx ? Output::RenderTargetIndex : Output::None);
        misc_vec[3] = ctl.use_vtx_viewport_idx ? Output::ViewportIndex : Output::None;
    }

    if (ctl.vs_out_ccdist0_enable) {
        auto& ccdist0 = outputs[num_outputs++];
        ccdist0[0] = ctl.IsClipDistEnabled(0)
                         ? Output::ClipDist0
                         : (ctl.IsCullDistEnabled(0) ? Output::CullDist0 : Output::None);
        ccdist0[1] = ctl.IsClipDistEnabled(1)
                         ? Output::ClipDist1
                         : (ctl.IsCullDistEnabled(1) ? Output::CullDist1 : Output::None);
        ccdist0[2] = ctl.IsClipDistEnabled(2)
                         ? Output::ClipDist2
                         : (ctl.IsCullDistEnabled(2) ? Output::CullDist2 : Output::None);
        ccdist0[3] = ctl.IsClipDistEnabled(3)
                         ? Output::ClipDist3
                         : (ctl.IsCullDistEnabled(3) ? Output::CullDist3 : Output::None);
    }

    if (ctl.vs_out_ccdist1_enable) {
        auto& ccdist1 = outputs[num_outputs++];
        ccdist1[0] = ctl.IsClipDistEnabled(4)
                         ? Output::ClipDist4
                         : (ctl.IsCullDistEnabled(4) ? Output::CullDist4 : Output::None);
        ccdist1[1] = ctl.IsClipDistEnabled(5)
                         ? Output::ClipDist5
                         : (ctl.IsCullDistEnabled(5) ? Output::CullDist5 : Output::None);
        ccdist1[2] = ctl.IsClipDistEnabled(6)
                         ? Output::ClipDist6
                         : (ctl.IsCullDistEnabled(6) ? Output::CullDist6 : Output::None);
        ccdist1[3] = ctl.IsClipDistEnabled(7)
                         ? Output::ClipDist7
                         : (ctl.IsCullDistEnabled(7) ? Output::CullDist7 : Output::None);
    }

    return num_outputs;
}

const Shader::RuntimeInfo& PipelineCache::BuildRuntimeInfo(PipelineSelection& sel, HwStage stage,
                                                             SwStage l_stage) {
    auto& info = sel.runtime_infos[u32(l_stage)];
    const auto& regs = (*sel.regs);
    const auto BuildCommon = [&](const auto& program) {
        info.props.num_user_data = program.settings.num_user_regs;
        info.props.num_input_vgprs = program.settings.vgpr_comp_cnt;
        info.props.num_allocated_vgprs = program.NumVgprs();
        info.props.fp_denorm_mode32 = program.settings.fp_denorm_mode32;
        info.props.fp_denorm_mode16_64 = program.settings.fp_denorm_mode64;
        info.props.fp_round_mode32 = program.settings.fp_round_mode32;
        info.props.fp_round_mode16_64 = program.settings.fp_round_mode64;
    };
    info.Initialize(stage, l_stage);
    switch (stage) {
    case HwStage::Local: {
        BuildCommon(regs.ls_program);
        Shader::TessellationDataConstantBuffer tess_constants{};
        const auto* hull_info = sel.infos[u32(SwStage::TessellationControl)];
        hull_info->ReadTessConstantBuffer(tess_constants);
        info.hw.ls.ls_stride = tess_constants.ls_stride;
        break;
    }
    case HwStage::Hull:
        BuildCommon(regs.hs_program);
        break;
    case HwStage::Export:
        BuildCommon(regs.es_program);
        info.hw.es.vertex_data_size = regs.vgt_esgs_ring_itemsize;
        break;
    case HwStage::Geometry: {
        BuildCommon(regs.gs_program);
        info.hw.gs.num_outputs = MapOutputs(info.hw.gs.outputs, regs.vs_output_control);
        info.hw.gs.output_vertices = regs.vgt_gs_max_vert_out;
        info.hw.gs.num_invocations =
            regs.vgt_gs_instance_cnt.IsEnabled() ? regs.vgt_gs_instance_cnt.count : 1;
        if (regs.stage_enable.raw == AmdGpu::ShaderStageEnable::LsHsEsGs) {
            info.hw.gs.in_primitive = [&]() {
                switch (regs.tess_config.topology) {
                case AmdGpu::TessellationTopology::Point:
                    return AmdGpu::PrimitiveType::PointList;
                case AmdGpu::TessellationTopology::Line:
                    return AmdGpu::PrimitiveType::LineList;
                case AmdGpu::TessellationTopology::TriangleCw:
                case AmdGpu::TessellationTopology::TriangleCcw:
                    return AmdGpu::PrimitiveType::TriangleList;
                default:
                    UNREACHABLE();
                }
            }();
        } else {
            info.hw.gs.in_primitive = regs.primitive_type;
        }
        for (u32 stream_id = 0; stream_id < Shader::GsMaxOutputStreams; ++stream_id) {
            info.hw.gs.out_primitive[stream_id] =
                regs.vgt_gs_out_prim_type.GetPrimitiveType(stream_id);
        }
        info.hw.gs.in_vertex_data_size = regs.vgt_esgs_ring_itemsize;
        info.hw.gs.out_vertex_data_size = regs.vgt_gs_vert_itemsize[0];
        info.hw.gs.mode = regs.vgt_gs_mode.mode;
        const auto params_vc = AmdGpu::GetParams(regs.vs_program);
        info.hw.gs.vs_copy = params_vc.code;
        info.hw.gs.vs_copy_hash = params_vc.hash;
        DumpShader(info.hw.gs.vs_copy, info.hw.gs.vs_copy_hash, Shader::HwStage::Vertex, 0,
                   "copy.bin");
        break;
    }
    case HwStage::Vertex: {
        BuildCommon(regs.vs_program);
        info.hw.vs.user_clip_plane_mask = regs.clipper_control.user_clip_plane_enable;
        info.hw.vs.num_outputs = MapOutputs(info.hw.vs.outputs, regs.vs_output_control);
        info.hw.vs.emulate_depth_negative_one_to_one =
            !instance.IsDepthClipControlSupported() &&
            regs.clipper_control.clip_space == AmdGpu::ClipSpace::MinusWToW;
        info.hw.vs.clip_disable = regs.IsClipDisabled();
        info.hw.vs.motion_vectors = sel.motion;
        break;
    }
    case HwStage::Fragment: {
        BuildCommon(regs.ps_program);
        info.hw.fs.en_flags = regs.ps_input_ena;
        info.hw.fs.addr_flags = regs.ps_input_addr;
        info.hw.fs.num_inputs = regs.num_interp;
        info.hw.fs.front_face_all_bits = regs.barycentric_control.front_face_all_bits;
        info.hw.fs.num_samples =
            regs.ps_input_addr.sample_coverage_ena && regs.ps_input_ena.sample_coverage_ena
                ? regs.aa_config.NumSamples()
                : 1;
        info.hw.fs.z_export_format = regs.z_export_format;
        // BB_OM_PART nofs / novary: the fragment shader is the normal one.
        info.hw.fs.motion_vectors = sel.motion && Shader::MotionVectors::WritesFragmentOutput();
        u8 stencil_ref_export_enable = regs.depth_shader_control.stencil_op_val_export_enable |
                                       regs.depth_shader_control.stencil_test_val_export_enable;
        info.hw.fs.mrtz_mask = regs.depth_shader_control.z_export_enable |
                               (stencil_ref_export_enable << 1) |
                               (regs.depth_shader_control.mask_export_enable << 2) |
                               (regs.depth_shader_control.coverage_to_mask_enable << 3);
        const auto& cb0_blend = regs.blend_control[0];
        if (cb0_blend.enable) {
            info.hw.fs.dual_source_blending =
                LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.color_dst_factor) ||
                LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.color_src_factor);
            if (cb0_blend.separate_alpha_blend) {
                info.hw.fs.dual_source_blending |=
                    LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.alpha_dst_factor) ||
                    LiverpoolToVK::IsDualSourceBlendFactor(cb0_blend.alpha_src_factor);
            }
        } else {
            info.hw.fs.dual_source_blending = false;
        }
        const auto& ps_inputs = regs.ps_inputs;
        for (u32 i = 0; i < regs.num_interp; i++) {
            info.hw.fs.inputs[i] = {
                .param_index = u8(ps_inputs[i].input_offset),
                .is_default = bool(ps_inputs[i].use_default),
                .is_flat = bool(ps_inputs[i].flat_shade),
                .default_value = u8(ps_inputs[i].default_value),
            };
        }
        for (u32 i = 0; i < Shader::MaxColorBuffers; i++) {
            info.hw.fs.color_buffers[i] = sel.graphics_key.color_buffers[i];
        }
        // Lowered user clip planes ride the same emulation path as guest-exported distances, so
        // the fragment side arms whenever the hardware vertex stage lowers them, keeping its input
        // locations in sync with the shifted vertex outputs.
        const bool lowers_user_clip_planes =
            regs.clipper_control.user_clip_plane_enable &&
            !regs.stage_enable.IsStageEnabled(static_cast<u32>(HwStage::Geometry));
        info.hw.fs.clip_distance_emulation =
            ((regs.vs_output_control.clip_distance_enable &&
              !regs.stage_enable.IsStageEnabled(static_cast<u32>(HwStage::Local))) ||
             lowers_user_clip_planes) &&
            profile.needs_clip_distance_emulation;
        break;
    }
    case HwStage::Compute: {
        const auto& cs_pgm = liverpool->GetCsRegs();
        info.props.num_user_data = cs_pgm.settings.num_user_regs;
        info.props.num_allocated_vgprs = cs_pgm.settings.num_vgprs * 4;
        info.props.fp_denorm_mode32 = cs_pgm.settings.fp_denorm_mode32;
        info.props.fp_denorm_mode16_64 = cs_pgm.settings.fp_denorm_mode64;
        info.props.fp_round_mode32 = cs_pgm.settings.fp_round_mode32;
        info.props.fp_round_mode16_64 = cs_pgm.settings.fp_round_mode64;
        info.hw.cs.workgroup_size = {cs_pgm.num_thread_x.full, cs_pgm.num_thread_y.full,
                                     cs_pgm.num_thread_z.full};
        info.hw.cs.tgid_enable = {cs_pgm.IsTgidEnabled(0), cs_pgm.IsTgidEnabled(1),
                                  cs_pgm.IsTgidEnabled(2)};
        info.hw.cs.shared_memory_size = cs_pgm.SharedMemSize();
        break;
    }
    default:
        break;
    }
    switch (l_stage) {
    case SwStage::Vertex:
        info.sw.vs.step_rate_0 = regs.vgt_instance_step_rate_0;
        info.sw.vs.step_rate_1 = regs.vgt_instance_step_rate_1;
        info.sw.vs.vertex_sgpr_offset = sel.draw_indirect_params.vertex_sgpr_offset;
        info.sw.vs.instance_sgpr_offset = sel.draw_indirect_params.instance_sgpr_offset;
        info.sw.vs.tess_emulated_primitive =
            regs.primitive_type == AmdGpu::PrimitiveType::RectList ||
            regs.primitive_type == AmdGpu::PrimitiveType::QuadList;
        break;
    case SwStage::TessellationControl: {
        info.sw.tcs.num_input_control_points = regs.ls_hs_config.hs_input_control_points;
        info.sw.tcs.num_threads = regs.ls_hs_config.hs_output_control_points;
        info.sw.tcs.tess_type = regs.tess_config.type;
        info.sw.tcs.offchip_lds_enable = regs.hs_program.settings.oc_lds_en;
        break;
    }
    case SwStage::TessellationEval: {
        info.sw.tes.tess_type = regs.tess_config.type;
        info.sw.tes.tess_topology = regs.tess_config.topology;
        info.sw.tes.tess_partitioning = regs.tess_config.partitioning;
        break;
    }
    default:
        break;
    }
    return info;
}

namespace {
// bbport: BB_DRIVER_PIPELINE_CACHE=0 keeps the driver's pipeline cache from being loaded or saved.
bool DriverCacheEnabled() {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_DRIVER_PIPELINE_CACHE");
        return !env || env[0] != '0';
    }();
    return enabled;
}
} // namespace

namespace {
// bbport: BB_ASYNC_COMPILE=1 lets the draw-preparation workers compile the shaders and pipelines
// the draws they prepare need, ahead of the GPU thread; 0 keeps every compile on the GPU thread.
bool AsyncCompileEnabled() {
    constexpr bool Default = true;
    const char* env = std::getenv("BB_ASYNC_COMPILE");
    return env ? env[0] != '0' : Default;
}

// bbport: a check that fails inside a speculative translation (draw preparation ahead of the
// GPU thread: CompileNewProgram, CompilePermutation, GetProgramSpeculative) saw a descriptor the
// game was still rewriting ("Thread ID buffer addressing is not supported outside of compute",
// "MapNumberConversion: data_fmt = 6"). The attempt ends at its recovery point like a fault does
// and the GPU thread translates the draw in order. Anywhere else the assertion still stops.
void SpeculativeAssertExit() {
    sigjmp_buf* const recover = runtime_fault_recover;
    if (!recover) {
        return;
    }
    runtime_fault_recover = nullptr;
    std::fprintf(stderr,
                 "GPU: speculative shader translation abandoned after the check above (%s); "
                 "the GPU thread translates this draw in order\n",
                 Common::GetCurrentThreadName().c_str());
#ifdef _WIN32
    bb_longjmp(*recover, 1);
#else
    siglongjmp(*recover, 1);
#endif
}
} // namespace

PipelineCache::PipelineCache(const Instance& instance_, Scheduler& scheduler_,
                             AmdGpu::Liverpool* liverpool_, u32 sparse_page_shift)
    : instance{instance_}, scheduler{scheduler_}, liverpool{liverpool_},
      desc_heap{instance, scheduler.GetWorkSemaphore(), DescriptorHeapSizes} {
    sel.regs = &liverpool->regs;
    const auto& vk12_props = instance.GetVk12Properties();
    profile = Shader::Profile{
        .max_viewport_width = instance.GetMaxViewportWidth(),
        .max_viewport_height = instance.GetMaxViewportHeight(),
        .max_shared_memory_size = instance.MaxComputeSharedMemorySize(),
        .supported_spirv = SpirvVersion1_6,
        .subgroup_size = instance.SubgroupSize(),
        .sparse_page_shift = sparse_page_shift,
        .support_int8 = instance.IsShaderInt8Supported(),
        .support_int16 = instance.IsShaderInt16Supported(),
        .support_int64 = instance.IsShaderInt64Supported(),
        .support_float16 = instance.IsShaderFloat16Supported(),
        .support_float64 = instance.IsShaderFloat64Supported(),
        .supports_denorm_behavior_independence =
            vk12_props.denormBehaviorIndependence != vk::ShaderFloatControlsIndependence::eNone,
        .supports_rounding_mode_independence =
            vk12_props.roundingModeIndependence != vk::ShaderFloatControlsIndependence::eNone,
        .support_fp16_denorm_preserve = bool(vk12_props.shaderDenormPreserveFloat16),
        .support_fp16_denorm_flush = bool(vk12_props.shaderDenormFlushToZeroFloat16),
        .support_fp16_round_to_zero = bool(vk12_props.shaderRoundingModeRTZFloat16),
        .support_fp32_denorm_preserve = bool(vk12_props.shaderDenormPreserveFloat32),
        .support_fp32_denorm_flush = bool(vk12_props.shaderDenormFlushToZeroFloat32),
        .support_fp32_round_to_zero = bool(vk12_props.shaderRoundingModeRTZFloat32),
        .support_fp64_denorm_preserve = bool(vk12_props.shaderDenormPreserveFloat64),
        .support_fp64_denorm_flush = bool(vk12_props.shaderDenormFlushToZeroFloat64),
        .support_fp64_round_to_zero = bool(vk12_props.shaderRoundingModeRTZFloat64),
        .support_fp16_signed_zero_inf_nan_preserve =
            bool(vk12_props.shaderSignedZeroInfNanPreserveFloat16),
        .support_fp32_signed_zero_inf_nan_preserve =
            bool(vk12_props.shaderSignedZeroInfNanPreserveFloat32),
        .support_fp64_signed_zero_inf_nan_preserve =
            bool(vk12_props.shaderSignedZeroInfNanPreserveFloat64),
        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),
        .supports_native_cube_calc = instance_.IsAmdGcnShaderSupported(),
        .supports_trinary_minmax = instance_.IsAmdShaderTrinaryMinMaxSupported(),
        .supports_buffer_fp32_atomic_min_max =
            instance_.IsShaderAtomicFloatBuffer32MinMaxSupported(),
        .supports_image_fp32_atomic_min_max = instance_.IsShaderAtomicFloatImage32MinMaxSupported(),
        .supports_buffer_int64_atomics = instance_.IsBufferInt64AtomicsSupported(),
        .supports_shared_int64_atomics = instance_.IsSharedInt64AtomicsSupported(),
        .supports_workgroup_explicit_memory_layout =
            instance_.IsWorkgroupMemoryExplicitLayoutSupported(),
        .supports_amd_shader_explicit_vertex_parameter =
            instance_.IsAmdShaderExplicitVertexParameterSupported(),
        .supports_fragment_shader_barycentric = instance_.IsFragmentShaderBarycentricSupported(),
        .supports_shader_subgroup_clock = instance_.IsShaderSubgroupClockSupported(),
        .needs_manual_interpolation = instance.IsFragmentShaderBarycentricSupported() &&
                                      instance.GetDriverID() == vk::DriverId::eNvidiaProprietary,
        // bbport: older NVIDIA (Pascal) has no barycentrics; BB_INTERP_INT_FIX=0/1 overrides.
        .needs_integer_interpolation_fix = [&] {
            if (const char* env = std::getenv("BB_INTERP_INT_FIX")) {
                return env[0] == '1';
            }
            return !instance.IsFragmentShaderBarycentricSupported() &&
                   instance.GetDriverID() == vk::DriverId::eNvidiaProprietary;
        }(),
        .needs_lds_barriers = instance.GetDriverID() == vk::DriverId::eNvidiaProprietary ||
                              instance.GetDriverID() == vk::DriverId::eMesaKosmickrisp,
        .needs_buffer_offsets = instance.StorageMinAlignment() > 4,
        .needs_unorm_fixup = instance.GetDriverID() == vk::DriverId::eMesaKosmickrisp,
        .needs_clip_distance_emulation = instance.GetDriverID() == vk::DriverId::eNvidiaProprietary,
        .supports_shader_stencil_export = instance_.IsShaderStencilExportSupported(),
    };
    async_compile = AsyncCompileEnabled() && !EmulatorSettings.IsShaderCollect();
    g_assert_speculative_exit = SpeculativeAssertExit;
    CreateDriverCache();
    WarmUp();
    if (Storage::DataBase::Instance().IsOpened() && DriverCacheEnabled()) {
        driver_cache_saver = std::jthread([this](std::stop_token stop) { DriverCacheSaverLoop(stop); });
        shutdown_hook = Storage::AddShutdownHook([this] {
            if (num_new_pipelines != saved_pipelines) {
                SaveDriverCache();
            }
        });
    }
}

PipelineCache::~PipelineCache() {
    if (shutdown_hook) {
        Storage::RemoveShutdownHook(shutdown_hook);
    }
    if (driver_cache_saver.joinable()) {
        driver_cache_saver.request_stop();
        driver_cache_saver.join();
        if (num_new_pipelines != saved_pipelines) {
            SaveDriverCache();
        }
    }
}

namespace {
// bbport: file of the saved driver pipeline cache: this header, then the blob
// vkGetPipelineCacheData returned (its own VkPipelineCacheHeaderVersionOne first).
constexpr u32 DriverCacheMagic = 0x4B434242; // "BBCK"
constexpr u32 DriverCacheVersion = 1;
struct DriverCacheHeader {
    u32 magic;
    u32 version;
    u64 payload_size;
    u64 payload_hash; // XXH3 of the blob: a cut-short or damaged file is not given to the driver
};
static_assert(sizeof(DriverCacheHeader) == 24);

/// The blob of a saved driver cache file, or the reason it cannot be used.
const char* ExtractDriverCache(const Instance& instance, const std::vector<u8>& file,
                               std::vector<u8>& blob) {
    DriverCacheHeader header;
    if (file.size() < sizeof(header) + sizeof(VkPipelineCacheHeaderVersionOne)) {
        return "file too small";
    }
    std::memcpy(&header, file.data(), sizeof(header));
    if (header.magic != DriverCacheMagic || header.version != DriverCacheVersion) {
        return "not a driver cache file of this version";
    }
    if (header.payload_size != file.size() - sizeof(header)) {
        return "size does not match";
    }
    const u8* payload = file.data() + sizeof(header);
    if (XXH3_64bits(payload, header.payload_size) != header.payload_hash) {
        return "checksum mismatch";
    }
    VkPipelineCacheHeaderVersionOne driver;
    std::memcpy(&driver, payload, sizeof(driver));
    if (driver.headerSize < sizeof(driver) || driver.headerSize > header.payload_size ||
        driver.headerVersion != VK_PIPELINE_CACHE_HEADER_VERSION_ONE) {
        return "unknown driver header";
    }
    if (driver.vendorID != instance.GetVendorID() || driver.deviceID != instance.GetDeviceID()) {
        return "saved for another GPU";
    }
    const auto uuid = instance.GetPipelineCacheUUID();
    if (std::memcmp(driver.pipelineCacheUUID, uuid.data(), VK_UUID_SIZE) != 0) {
        return "saved by another driver version";
    }
    blob.assign(payload, payload + header.payload_size);
    return nullptr;
}
} // namespace

void PipelineCache::CreateDriverCache() {
    auto& database = Storage::DataBase::Instance();
    if (EmulatorSettings.IsPipelineCacheEnabled()) {
        database.Open(); // WarmUp opens it too (no-op then): the path is needed before it
    }
    std::vector<u8> blob;
    const char* state = DriverCacheEnabled() ? "none saved" : "off (BB_DRIVER_PIPELINE_CACHE=0)";
    if (std::vector<u8> file; DriverCacheEnabled() && database.LoadDriverCache(file)) {
        if (const char* why = ExtractDriverCache(instance, file, blob)) {
            std::printf("GPU: driver pipeline cache ignored (%s), deleted\n", why);
            database.DeleteDriverCache();
            blob.clear();
        } else {
            state = "loaded";
        }
    }
    const auto create = [&](std::span<const u8> initial) {
        return instance.GetDevice().createPipelineCacheUnique(
            {.initialDataSize = initial.size(), .pInitialData = initial.data()});
    };
    auto created = create(blob);
    if (created.result != vk::Result::eSuccess && !blob.empty()) {
        std::printf("GPU: driver rejected the saved pipeline cache (%s), deleted\n",
                    vk::to_string(created.result).c_str());
        database.DeleteDriverCache();
        blob.clear();
        state = "none saved";
        created = create({});
    }
    ASSERT_MSG(created.result == vk::Result::eSuccess, "Failed to create pipeline cache: {}",
               vk::to_string(created.result));
    pipeline_cache = std::move(created.value);
    std::printf("GPU: driver pipeline cache: %s (%zu KiB)\n", state, blob.size() / 1024);
}

void PipelineCache::SaveDriverCache() {
    std::scoped_lock lock{driver_cache_mutex};
    const auto start = std::chrono::steady_clock::now();
    const u32 marker = num_new_pipelines;
    auto [result, data] = instance.GetDevice().getPipelineCacheData(*pipeline_cache);
    if (result != vk::Result::eSuccess || data.size() < sizeof(VkPipelineCacheHeaderVersionOne)) {
        return;
    }
    std::vector<u8> file(sizeof(DriverCacheHeader) + data.size());
    const DriverCacheHeader header{.magic = DriverCacheMagic,
                                   .version = DriverCacheVersion,
                                   .payload_size = data.size(),
                                   .payload_hash = XXH3_64bits(data.data(), data.size())};
    std::memcpy(file.data(), &header, sizeof(header));
    std::memcpy(file.data() + sizeof(header), data.data(), data.size());
    if (Storage::DataBase::Instance().SaveDriverCache(file)) {
        saved_pipelines = marker;
        std::printf("GPU: driver pipeline cache saved: %zu KiB in %lld ms\n", data.size() / 1024,
                    static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                               std::chrono::steady_clock::now() - start)
                                               .count()));
    }
}

void PipelineCache::DriverCacheSaverLoop(std::stop_token stop) {
    using namespace std::chrono;
    Common::SetCurrentThreadName("bb:VkCacheSave");
    constexpr u32 SaveEveryPipelines = 64;
    constexpr auto MinInterval = seconds(10);
    constexpr auto MaxInterval = seconds(60);
    auto last_save = steady_clock::now();
    std::mutex wake_mutex;
    std::condition_variable_any wake;
    while (!stop.stop_requested()) {
        {
            std::unique_lock lock{wake_mutex};
            wake.wait_for(lock, stop, seconds(2), [] { return false; });
        }
        if (stop.stop_requested()) {
            return;
        }
        const u32 added = num_new_pipelines - saved_pipelines;
        const auto since = steady_clock::now() - last_save;
        if (added && ((added >= SaveEveryPipelines && since >= MinInterval) || since >= MaxInterval)) {
            SaveDriverCache();
            last_save = steady_clock::now();
        }
    }
}

// bbport: shader/pipeline compile time on the GPU thread, reported by BB_FRAME_STATS.
std::atomic<u64> g_bb_compile_ns;
std::atomic<u32> g_bb_compiles;
// Compiles by the draw-preparation workers (BB_ASYNC_COMPILE), and the time the GPU thread spent
// waiting for one of them to finish instead of compiling the same thing itself.
std::atomic<u64> g_bb_worker_compile_ns;
std::atomic<u32> g_bb_worker_compiles;
std::atomic<u64> g_bb_wait_ns;
std::atomic<u32> g_bb_waits;
std::atomic<u32> g_bb_wait_timeouts;
std::atomic<u32> g_bb_spec_discards;
namespace {
/// Set while a draw-preparation worker selects a pipeline: what it compiles counts as its own.
thread_local bool t_speculative = false;

struct CompileTimer {
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    ~CompileTimer() {
        const u64 ns = u64(std::chrono::duration_cast<std::chrono::nanoseconds>(
                               std::chrono::steady_clock::now() - start)
                               .count());
        if (t_speculative) {
            g_bb_worker_compile_ns += ns;
            ++g_bb_worker_compiles;
        } else {
            g_bb_compile_ns += ns;
            ++g_bb_compiles;
        }
    }
};

/// How long the GPU thread waits for a compile a worker has under way before it compiles the
/// same thing itself. The workers run at idle priority and may be starved on a busy CPU.
std::chrono::milliseconds MaxCompileWait() {
    static const auto wait = [] {
        const char* env = std::getenv("BB_ASYNC_WAIT_MS");
        return std::chrono::milliseconds(env ? std::clamp(std::atoi(env), 0, 5000) : 50);
    }();
    return wait;
}

/// Content hash of a program's code as it is in guest memory now.
u64 CodeHash(std::span<const u32> code) {
    return XXH3_64bits(code.data(), code.size_bytes());
}

/// The pipeline layout and the descriptor updates of a permutation come from the program's
/// Info, its bindings from its own translation: both must list the same resources.
bool SameResources(const Shader::Info& a, const Shader::Info& b) {
    return a.buffers.size() == b.buffers.size() && a.images.size() == b.images.size() &&
           a.samplers.size() == b.samplers.size() && a.fmasks.size() == b.fmasks.size();
}

/// A worker's translation that is thrown away: the GPU thread translates it in order.
void NoteDiscarded(u64 hash, size_t perm_idx, const char* why) {
    ++g_bb_spec_discards;
    static std::atomic<int> logged{0};
    if (logged.fetch_add(1, std::memory_order_relaxed) < 8) {
        LOG_WARNING(Render_Vulkan, "Shader {:#x} permutation {} translated ahead of the GPU "
                                   "thread was dropped: {}",
                    hash, perm_idx, why);
    }
}

/// Test hook for the check in CompilePermutation, BB_TEST_TORN_SHADER_CODE=N: every Nth worker
/// permutation translates its code with the last 40% replaced by s_endpgm, as a worker reading
/// memory the game has reused would. Each one must be dropped ("Async compile: ... N dropped")
/// and the validation layer must stay quiet.
bool TestTornShaderCode() {
    static const int every = [] {
        const char* env = std::getenv("BB_TEST_TORN_SHADER_CODE");
        return env ? std::atoi(env) : 0;
    }();
    static std::atomic<int> count{0};
    return every > 0 && count.fetch_add(1, std::memory_order_relaxed) % every == 0;
}

/// Waits (bounded) until `done()` holds; false on timeout.
template <typename Lock, typename Done>
bool WaitForCompile(std::condition_variable_any& cv, Lock& lock, Done&& done) {
    const auto start = std::chrono::steady_clock::now();
    const bool finished = cv.wait_for(lock, MaxCompileWait(), done);
    g_bb_wait_ns += u64(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - start)
                            .count());
    ++g_bb_waits;
    if (!finished) {
        ++g_bb_wait_timeouts;
    }
    return finished;
}
} // namespace

bool PipelineCache::PrepareGraphicsPipeline(PipelineSelection& worker_sel) {
    // Tessellation stages read constant buffers from memory at selection time: not prepared.
    if (worker_sel.regs->stage_enable.hs_en) {
        return false;
    }
    t_speculative = true;
    struct Reset {
        ~Reset() {
            t_speculative = false;
        }
    } reset;
    if (!RefreshGraphicsKey(worker_sel) || worker_sel.worker->failed) {
        return false;
    }
    return !async_compile || EnsureGraphicsPipeline(worker_sel);
}

const GraphicsPipeline* PipelineCache::TryPreparedPipeline(const PreparedDraw& prepared) {
    if (prepared.state.load(std::memory_order_acquire) != PreparedDraw::Ready ||
        prepared.reg_checksum != liverpool->gfx_reg_checksum) {
        return nullptr;
    }
    // Same registers; the stage programs and their flattened user data (which also covers
    // everything read from guest memory for the specialization) must match as well. This is
    // the per-stage work GetProgram does on the regular path.
    const auto& regs = liverpool->regs;
    for (u32 i = 0; i < prepared.num_stages; ++i) {
        const auto& stage = prepared.stages[i];
        const auto* pgm = regs.ProgramForStage(static_cast<u32>(stage.hw_stage));
        if (!pgm || !ProgramReadable(*pgm)) {
            return nullptr;
        }
        const auto params = AmdGpu::GetParams(*pgm);
        if (params.hash != stage.hash) {
            return nullptr;
        }
        auto& info = const_cast<Program*>(stage.program)->info;
        info.pgm_base = params.Base();
        info.user_data = params.user_data;
        info.RefreshFlatBuf();
        if (info.pgm_base != stage.pgm_base || info.flattened_ud_buf.size() != stage.flat_size ||
            std::memcmp(info.flattened_ud_buf.data(), stage.flat,
                        stage.flat_size * sizeof(u32)) != 0) {
            return nullptr;
        }
    }
    // Not there yet when a worker is still compiling it: the regular path waits for that.
    std::shared_lock lock{pipelines_mutex};
    const auto it = graphics_pipelines.find(prepared.key);
    return it != graphics_pipelines.end() ? it->second.get() : nullptr;
}

const GraphicsPipeline* PipelineCache::PublishGraphicsPipeline(
    const GraphicsPipelineKey& key, u64 hash, std::unique_ptr<GraphicsPipeline> pipeline,
    GraphicsPipeline::SerializationSupport& sdata, bool claimed) {
    const GraphicsPipeline* result;
    bool inserted;
    {
        std::unique_lock lock{pipelines_mutex};
        // Another thread may have published the same pipeline while this one compiled a copy
        // (after a timed-out wait): the first one stays, this one is dropped.
        const auto [it, is_new] = graphics_pipelines.try_emplace(key, std::move(pipeline));
        inserted = is_new;
        result = it->second.get();
        if (claimed) {
            pipelines_compiling.erase(key);
        }
    }
    pipelines_cv.notify_all();
    if (inserted) {
        RegisterPipelineData(key, hash, sdata);
        ++num_new_pipelines;
    }
    return result;
}

const GraphicsPipeline* PipelineCache::GetGraphicsPipeline(const DrawIndirectParams params,
                                                           const PreparedDraw* prepared) {
    used_prepared = nullptr;
    if (prepared) {
        if (const auto* pipeline = TryPreparedPipeline(*prepared)) {
            used_prepared = prepared;
            return pipeline;
        }
    }
    sel.draw_indirect_params = params;
    if (!RefreshGraphicsKey(sel)) {
        return nullptr;
    }
    const auto& key = sel.graphics_key;
    {
        std::shared_lock lock{pipelines_mutex};
        if (const auto it = graphics_pipelines.find(key); it != graphics_pipelines.end()) {
            return it->second.get();
        }
    }
    bool claimed = false;
    {
        std::unique_lock lock{pipelines_mutex};
        for (;;) {
            if (const auto it = graphics_pipelines.find(key); it != graphics_pipelines.end()) {
                return it->second.get();
            }
            if (!pipelines_compiling.contains(key)) {
                pipelines_compiling.insert(key);
                claimed = true;
                break;
            }
            // A worker is compiling it: wait for that. Past the bound, build a copy.
            if (!WaitForCompile(pipelines_cv, lock,
                                [&] { return !pipelines_compiling.contains(key); })) {
                break;
            }
        }
    }
    const auto pipeline_hash = std::hash<GraphicsPipelineKey>{}(key);
    LOG_INFO(Render_Vulkan, "Compiling graphics pipeline {:#x}", pipeline_hash);
    CompileTimer timer;

    GraphicsPipeline::SerializationSupport sdata{};
    auto pipeline = std::make_unique<GraphicsPipeline>(
        instance, scheduler, desc_heap, profile, key, *pipeline_cache, sel.infos,
        sel.runtime_infos, sel.fetch_shader, sel.modules, sdata, false);
    const auto* result = PublishGraphicsPipeline(key, pipeline_hash, std::move(pipeline), sdata,
                                                 claimed);

    if (EmulatorSettings.IsShaderCollect()) {
        for (auto stage = 0; stage < MaxShaderStages; ++stage) {
            if (sel.infos[stage]) {
                auto& m = sel.modules[stage];
                module_related_pipelines[m].emplace_back(key);
            }
        }
    }
    sel.fetch_shader.reset();
    return result;
}

bool PipelineCache::EnsureGraphicsPipeline(PipelineSelection& worker_sel) {
    // Without dynamic vertex input the pipeline is built from the vertex buffer descriptions of
    // the V#s as they are in guest memory now, and the format conversion asserts on garbage the
    // guest may still be writing: the GPU thread compiles such pipelines itself.
    if (!instance.IsVertexInputDynamicState()) {
        return true;
    }
    const auto& key = worker_sel.graphics_key;
    {
        std::unique_lock lock{pipelines_mutex};
        if (graphics_pipelines.contains(key) || !pipelines_compiling.insert(key).second) {
            return true; // there, or another thread is on it
        }
    }
    const auto pipeline_hash = std::hash<GraphicsPipelineKey>{}(key);
    CompileTimer timer;

    GraphicsPipeline::SerializationSupport sdata{};
    auto pipeline = std::make_unique<GraphicsPipeline>(
        instance, scheduler, desc_heap, profile, key, *pipeline_cache, worker_sel.infos,
        worker_sel.runtime_infos, worker_sel.fetch_shader, worker_sel.modules, sdata, false);
    // It was described with the worker's copy of each stage's Info (this draw's user data); the
    // GPU thread binds resources through the Info of the program.
    const auto& worker = *worker_sel.worker;
    std::array<const Shader::Info*, MaxShaderStages> stable{};
    for (u32 i = 0; i < MaxShaderStages; ++i) {
        for (const auto& stage : worker.stages) {
            if (worker_sel.infos[i] && worker_sel.infos[i] == &worker.infos.at(stage.program)) {
                stable[i] = &stage.program->info;
                break;
            }
        }
    }
    pipeline->SetStages(stable);
    PublishGraphicsPipeline(key, pipeline_hash, std::move(pipeline), sdata, true);
    return true;
}

const ComputePipeline* PipelineCache::GetComputePipeline() {
    if (!RefreshComputeKey()) {
        return nullptr;
    }
    const auto [it, is_new] = compute_pipelines.try_emplace(compute_key);
    if (is_new) {
        const auto pipeline_hash = std::hash<ComputePipelineKey>{}(compute_key);
        LOG_INFO(Render_Vulkan, "Compiling compute pipeline {:#x}", pipeline_hash);
        CompileTimer timer;

        ComputePipeline::SerializationSupport sdata{};
        it.value() = std::make_unique<ComputePipeline>(instance, scheduler, desc_heap, profile,
                                                       *pipeline_cache, compute_key, *sel.infos[0],
                                                       sel.modules[0], sdata, false);
        RegisterPipelineData(compute_key, sdata);
        ++num_new_pipelines;

        if (EmulatorSettings.IsShaderCollect()) {
            auto& m = sel.modules[0];
            module_related_pipelines[m].emplace_back(compute_key);
        }
    }
    return it->second.get();
}

bool PipelineCache::RefreshGraphicsKey(PipelineSelection& sel) {
    std::memset(&sel.graphics_key, 0, sizeof(GraphicsPipelineKey));
    const auto& regs = (*sel.regs);
    auto& key = sel.graphics_key;

    const bool db_enabled = regs.depth_buffer.DepthValid() || regs.depth_buffer.StencilValid();

    key.z_format = regs.depth_buffer.DepthValid() ? regs.depth_buffer.z_info.format
                                                  : AmdGpu::DepthBuffer::ZFormat::Invalid;
    key.stencil_format = regs.depth_buffer.StencilValid()
                             ? regs.depth_buffer.stencil_info.format
                             : AmdGpu::DepthBuffer::StencilFormat::Invalid;
    key.depth_clamp_enable = !regs.depth_render_override.disable_viewport_clamp;
    key.depth_clip_enable = regs.clipper_control.ZclipEnable();
    key.clip_space = regs.clipper_control.clip_space;
    key.provoking_vtx_last = regs.polygon_control.provoking_vtx_last;
    key.prim_type = regs.primitive_type;
    key.polygon_mode = regs.polygon_control.PolyMode();
    key.patch_control_points =
        regs.stage_enable.hs_en ? regs.ls_hs_config.hs_input_control_points : 0;
    key.logic_op = regs.color_control.rop3;
    key.depth_samples = db_enabled ? regs.depth_buffer.NumSamples() : 1;
    key.num_samples = key.depth_samples;
    key.cb_shader_mask = regs.color_shader_mask;

    const bool skip_cb_binding =
        regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Disable;

    // Only potentially animated G-buffer draws may use the extra motion attachment. The
    // ordinary stage variant is needed first to inspect the vertex shader's resources.
    bool motion_possible = false;
    {
        u32 bound = 0;
        for (s32 cb = 0; cb < AmdGpu::NUM_COLOR_BUFFERS && !skip_cb_binding; ++cb) {
            bound += (regs.color_buffers[cb] && regs.color_target_mask.GetMask(cb)) ? 1 : 0;
        }
        motion_possible = Shader::MotionVectors::buffers_ready && bound >= 5 &&
                          !regs.color_buffers[Shader::MotionVectors::Output] &&
                          regs.depth_buffer.DepthValid() &&
                          regs.depth_buffer.NumSamples() == 1 && !regs.IsClipDisabled() &&
                          regs.stage_enable.raw == AmdGpu::ShaderStageEnable::VgtStages::Vs;
    }
    sel.motion = false;

    // First pass to fill render target information needed by shader recompiler
    for (s32 cb = 0; cb < AmdGpu::NUM_COLOR_BUFFERS && !skip_cb_binding; ++cb) {
        const auto& col_buf = regs.color_buffers[cb];
        if (!col_buf || !regs.color_target_mask.GetMask(cb)) {
            // No attachment bound or writing to it is disabled.
            continue;
        }

        // Fill color target information
        auto& color_buffer = key.color_buffers[cb];
        color_buffer.data_format = col_buf.GetDataFmt();
        color_buffer.num_format = col_buf.GetNumberFmt();
        color_buffer.num_conversion = col_buf.GetNumberConversion();
        color_buffer.export_format = regs.color_export_format.GetFormat(cb);
        color_buffer.swizzle = col_buf.Swizzle();

        const auto& bc = regs.blend_control[cb];
        color_buffer.blend_self_scale =
            bc.enable && !col_buf.info.blend_bypass &&
            (bc.color_func == AmdGpu::BlendControl::BlendFunc::Min ||
             bc.color_func == AmdGpu::BlendControl::BlendFunc::Max) &&
            bc.color_src_factor == AmdGpu::BlendControl::BlendFactor::SrcColor &&
            bc.color_dst_factor == AmdGpu::BlendControl::BlendFactor::DstColor;
    }

    // Compile and bind shader stages
    if (!RefreshGraphicsStages(sel)) {
        return false;
    }
    if (motion_possible) {
        const auto* vs = sel.infos[static_cast<u32>(Shader::SwStage::Vertex)];
        if (vs) {
            // Shaders with a bone palette (motion_history.h). Small skeletons (weapons,
            // props) also include static world pieces: the rasterizer gives those
            // history only while their constants change.
            for (const auto& resource : vs->buffers) {
                if (resource.IsSpecial()) continue;
                const auto buffer = resource.GetSharp(*vs);
                if (buffer.Valid() && buffer.GetStride() == 16 &&
                    Motion::ClassifyBuffer(buffer.GetSize()) != Motion::BufferRole::Other) {
                    sel.motion = true;
                    break;
                }
            }
        }
        // BB_MOTION_SELECT_LOG=1: each G-buffer vertex shader once, with its buffer sizes
        // and the selection, to find animated models that the size rule leaves out.
        static const bool select_log = [] {
            const char* value = std::getenv("BB_MOTION_SELECT_LOG");
            return value && value[0] == '1';
        }();
        if (select_log && vs) {
            std::string sizes;
            for (const auto& resource : vs->buffers) {
                if (resource.IsSpecial()) continue;
                const auto buffer = resource.GetSharp(*vs);
                sizes += fmt::format(" {}/{}", buffer.GetSize(), buffer.GetStride());
            }
            static std::mutex log_mutex;
            static std::unordered_set<u64> logged;
            std::scoped_lock lock{log_mutex};
            if (logged.insert(vs->pgm_hash ^ std::hash<std::string>{}(sizes)).second) {
                std::printf("Motion select: vs %016llx %s, buffers (size/stride):%s\n",
                            static_cast<unsigned long long>(vs->pgm_hash),
                            sel.motion ? "ON " : "off", sizes.c_str());
            }
        }
        // Keep the old broad path available for visual A/B tests.
        static const bool all_motion = [] {
            const char* value = std::getenv("BB_OBJECT_MOTION_ALL");
            return value && value[0] == '1';
        }();
        if (all_motion) {
            sel.motion = true;
        }
        // The motion varyings take two fixed locations (MotionVectors::CurrentLocation). A
        // vertex shader exporting a parameter there or later keeps no motion vectors. Clip
        // distance emulation (NVIDIA) moves every parameter one location up.
        if (sel.motion && vs) {
            for (u32 param = Shader::MotionVectors::CurrentLocation - 1;
                 param < Shader::IR::NumParams; ++param) {
                if (vs->stores.GetAny(Shader::IR::Attribute::Param0 + param)) {
                    sel.motion = false;
                    break;
                }
            }
        }
        if (sel.motion && !RefreshGraphicsStages(sel)) {
            return false;
        }
    }

    // Second pass to mask out render targets not written by shader and fill remaining info
    u8 color_samples = 0;
    bool all_color_samples_same = true;
    for (s32 cb = 0; cb < key.num_color_attachments && !skip_cb_binding; ++cb) {
        const auto& col_buf = regs.color_buffers[cb];
        const u32 target_mask = regs.color_target_mask.GetMask(cb);
        if (!col_buf || !target_mask) {
            continue;
        }
        if ((key.mrt_mask & (1u << cb)) == 0) {
            std::memset(&key.color_buffers[cb], 0, sizeof(Shader::PsColorBuffer));
            continue;
        }

        // Fill color blending information
        if (regs.blend_control[cb].enable && !col_buf.info.blend_bypass) {
            key.blend_controls[cb] = regs.blend_control[cb];
        }

        // Apply swizzle to target mask
        key.write_masks[cb] =
            vk::ColorComponentFlags{key.color_buffers[cb].swizzle.ApplyMask(target_mask)};

        // Fill color samples
        const u8 prev_color_samples = std::exchange(color_samples, col_buf.NumSamples());
        all_color_samples_same &= color_samples == prev_color_samples || prev_color_samples == 0;
        key.color_samples[cb] = color_samples;
        key.num_samples = std::max(key.num_samples, color_samples);
    }

    // BB_OM_PART nofs / novary: the vertex side stays (key.motion_vectors drives the params)
    // but there is no attachment 7, as if the fragment shader had motion_vectors false.
    if (sel.motion) {
        key.motion_vectors = 1;
    }
    if (sel.motion && Shader::MotionVectors::WritesFragmentOutput()) {
        constexpr u32 mv = Shader::MotionVectors::Output;
        // The slots between the shader's last target and the motion attachment have no image in
        // the render pass. The first pass above filled them from every bound color buffer, also
        // ones this shader never writes; left in, the pipeline declares a format there (e.g.
        // R16G16B16A16 at 6) where the rendering has none (VUID-vkCmdDrawIndexed-
        // dynamicRenderingUnusedAttachments-08912).
        for (u32 cb = key.num_color_attachments; cb < mv; ++cb) {
            std::memset(&key.color_buffers[cb], 0, sizeof(Shader::PsColorBuffer));
            key.write_masks[cb] = {};
            key.color_samples[cb] = 0;
        }
        key.mrt_mask |= 1u << mv;
        key.num_color_attachments = mv + 1;
        auto& color_buffer = key.color_buffers[mv];
        color_buffer.data_format = AmdGpu::DataFormat::Format32_32_32_32;
        color_buffer.num_format = AmdGpu::NumberFormat::Float;
        color_buffer.swizzle = AmdGpu::IdentityMapping;
        key.write_masks[mv] = vk::ColorComponentFlagBits::eR | vk::ColorComponentFlagBits::eG |
                              vk::ColorComponentFlagBits::eB | vk::ColorComponentFlagBits::eA;
        key.color_samples[mv] = 1;
    }

    // Force all color samples to match depth samples to avoid unsupported MSAA configuration
    if (color_samples != 0) {
        const bool depth_mismatch = db_enabled && color_samples != key.depth_samples;
        if (!all_color_samples_same && !instance.IsMixedAnySamplesSupported() ||
            all_color_samples_same && depth_mismatch && !instance.IsMixedDepthSamplesSupported()) {
            key.color_samples.fill(key.depth_samples);
            key.num_samples = key.depth_samples;
        }
    }

    return true;
}

bool PipelineCache::RefreshGraphicsStages(PipelineSelection& sel) {
    const auto& regs = (*sel.regs);
    auto& key = sel.graphics_key;
    sel.fetch_shader = std::nullopt;

    Shader::Backend::Bindings binding{};
    const auto bind_stage = [&](HwStage stage_in, SwStage stage_out) -> bool {
        const auto stage_in_idx = static_cast<u32>(stage_in);
        const auto stage_out_idx = static_cast<u32>(stage_out);
        if (!regs.stage_enable.IsStageEnabled(stage_in_idx)) {
            key.stage_hashes[stage_out_idx] = 0;
            sel.infos[stage_out_idx] = nullptr;
            return false;
        }

        const auto* pgm = regs.ProgramForStage(stage_in_idx);
        if (!pgm || !ProgramReadable(*pgm)) {
            key.stage_hashes[stage_out_idx] = 0;
            sel.infos[stage_out_idx] = nullptr;
            return false;
        }

        const auto params = AmdGpu::GetParams(*pgm);
        std::optional<Shader::Gcn::FetchShaderData> fetch_shader_;
        std::tie(sel.infos[stage_out_idx], sel.modules[stage_out_idx], fetch_shader_,
                 key.stage_hashes[stage_out_idx]) =
            GetProgram(sel, stage_in, stage_out, params, binding);
        if (fetch_shader_) {
            sel.fetch_shader = fetch_shader_;
        }
        return true;
    };

    sel.infos.fill(nullptr);
    sel.modules.fill(nullptr);

    bind_stage(HwStage::Fragment, SwStage::Fragment);

    const auto* fs_info = sel.infos[static_cast<u32>(SwStage::Fragment)];
    key.mrt_mask = fs_info ? fs_info->mrt_mask : 0u;
    key.num_color_attachments = std::bit_width(key.mrt_mask);

    switch (regs.stage_enable.raw) {
    case AmdGpu::ShaderStageEnable::VgtStages::EsGs:
        if (!instance.IsGeometryStageSupported()) {
            LOG_WARNING(Render_Vulkan, "Geometry shader stage unsupported, skipping");
            return false;
        }
        if (regs.vgt_gs_mode.onchip || regs.vgt_strmout_config.raw) {
            LOG_WARNING(Render_Vulkan, "Geometry shader features unsupported, skipping");
            return false;
        }
        if (!bind_stage(HwStage::Export, SwStage::Vertex)) {
            return false;
        }
        if (!bind_stage(HwStage::Geometry, SwStage::Geometry)) {
            return false;
        }
        break;
    case AmdGpu::ShaderStageEnable::VgtStages::LsHs:
        if (!instance.IsTessellationSupported()) {
            return false;
        }
        if (!bind_stage(HwStage::Hull, SwStage::TessellationControl)) {
            return false;
        }
        if (!bind_stage(HwStage::Vertex, SwStage::TessellationEval)) {
            return false;
        }
        if (!bind_stage(HwStage::Local, SwStage::Vertex)) {
            return false;
        }
        break;
    case AmdGpu::ShaderStageEnable::VgtStages::LsHsEsGs:
        if (!instance.IsTessellationSupported()) {
            return false;
        }
        if (!instance.IsGeometryStageSupported()) {
            LOG_WARNING(Render_Vulkan, "Geometry shader stage unsupported, skipping");
            return false;
        }
        if (regs.vgt_gs_mode.onchip || regs.vgt_strmout_config.raw) {
            LOG_WARNING(Render_Vulkan, "Geometry shader features unsupported, skipping");
            return false;
        }
        if (!bind_stage(HwStage::Hull, SwStage::TessellationControl)) {
            return false;
        }
        if (!bind_stage(HwStage::Export, SwStage::TessellationEval)) {
            return false;
        }
        if (!bind_stage(HwStage::Local, SwStage::Vertex)) {
            return false;
        }
        if (!bind_stage(HwStage::Geometry, SwStage::Geometry)) {
            return false;
        }
        break;
    case AmdGpu::ShaderStageEnable::VgtStages::Vs:
        bind_stage(HwStage::Vertex, SwStage::Vertex);
        break;
    default:
        LOG_WARNING(Render_Vulkan, "unimplemented shader stage {}", (u32)regs.stage_enable.raw);
        return false;
    }

    const auto* vs_info = sel.infos[static_cast<u32>(SwStage::Vertex)];
    if (vs_info && sel.fetch_shader && !instance.IsVertexInputDynamicState()) {
        // Without vertex input dynamic state, the pipeline needs to specialize on format.
        // Stride will still be handled outside the pipeline using dynamic state.
        u32 vertex_binding = 0;
        for (const auto& attrib : sel.fetch_shader->attributes) {
            const auto& buffer = attrib.GetSharp(*vs_info);
            ASSERT_MSG(vertex_binding < MaxVertexBufferCount,
                       "Vertex attribute binding count exceeded limit: {} >= {}", vertex_binding,
                       MaxVertexBufferCount);
            key.vertex_buffer_formats[vertex_binding++] =
                Vulkan::LiverpoolToVK::SurfaceFormat(buffer.GetDataFmt(), buffer.GetNumberFmt());
        }
    }

    return true;
}

bool PipelineCache::RefreshComputeKey() {
    Shader::Backend::Bindings binding{};
    const auto& cs_pgm = liverpool->GetCsRegs();
    if (!ProgramReadable(cs_pgm)) {
        return false;
    }
    const auto cs_params = AmdGpu::GetParams(cs_pgm);
    std::tie(sel.infos[0], sel.modules[0], sel.fetch_shader, compute_key.value) =
        GetProgram(sel, HwStage::Compute, SwStage::Compute, cs_params, binding);
    return true;
}

vk::ShaderModule PipelineCache::CompileModule(Shader::Info& info, Shader::RuntimeInfo& runtime_info,
                                              const std::span<const u32>& code, size_t perm_idx,
                                              Shader::Backend::Bindings& binding,
                                              Shader::Pools& translator_pools, bool persist,
                                              std::vector<u32>* spv_out) {
    LOG_INFO(Render_Vulkan, "Compiling {} shader {:#x} {}", info.hw_stage, info.pgm_hash,
             perm_idx != 0 ? "(permutation)" : "");
    DumpShader(code, info.pgm_hash, info.hw_stage, perm_idx, "bin");
    CompileTimer timer;

    const auto ir_program =
        Shader::TranslateProgram(code, translator_pools, info, runtime_info, profile);
    auto spv = Shader::Backend::SPIRV::EmitSPIRV(profile, runtime_info, ir_program, binding);
    DumpShader(spv, info.pgm_hash, info.hw_stage, perm_idx, "spv");

    vk::ShaderModule module;

    auto patch = GetShaderPatch(info.pgm_hash, info.hw_stage, perm_idx, "spv");
    const bool is_patched = patch && EmulatorSettings.IsPatchShaders();
    if (is_patched) {
        LOG_INFO(Loader, "Loaded patch for {} shader {:#x}", info.hw_stage, info.pgm_hash);
        module = CompileSPV(*patch, instance.GetDevice());
    } else {
        module = CompileSPV(spv, instance.GetDevice());
    }

    if (persist) {
        RegisterShaderBinary(std::move(spv), info.pgm_hash, perm_idx);
    } else if (spv_out) {
        *spv_out = std::move(spv);
    }

    const auto name = GetShaderName(info.hw_stage, info.pgm_hash, perm_idx);
    Vulkan::SetObjectName(instance.GetDevice(), module, name);
    if (EmulatorSettings.IsShaderCollect()) {
        DebugState.CollectShader(name, info.sw_stage, module, spv, code,
                                 patch ? *patch : std::span<const u32>{}, is_patched);
    }
    return module;
}

Program* PipelineCache::FindProgram(u64 hash) {
    std::shared_lock lock{programs_mutex};
    const auto it = program_cache.find(hash);
    return it != program_cache.end() ? it.value().get() : nullptr;
}

std::optional<size_t> PipelineCache::FindReadyPermutation(const Program& program,
                                                          const Shader::StageSpecialization& spec) {
    // bbport: consecutive draws of a program almost always use the same permutation.
    const auto& modules = program.modules;
    const size_t last = program.last_used.load(std::memory_order_relaxed);
    if (last < modules.size() && !modules[last].compiling && modules[last].spec == spec) {
        return last;
    }
    const auto it = std::ranges::find_if(modules, [&](const Program::Module& module) {
        return !module.compiling && module.spec == spec;
    });
    if (it == modules.end()) {
        return std::nullopt;
    }
    return static_cast<size_t>(it - modules.begin());
}

PipelineCache::Result PipelineCache::GetProgram(PipelineSelection& sel, HwStage hw_stage,
                                                SwStage sw_stage,
                                                const Shader::ShaderParams& params,
                                                Shader::Backend::Bindings& binding) {
    auto runtime_info = BuildRuntimeInfo(sel, hw_stage, sw_stage);
    if (sel.worker) {
        return GetProgramSpeculative(sel, hw_stage, sw_stage, params, binding, runtime_info);
    }
    for (;;) {
        if (auto* program = FindProgram(params.hash)) {
            return GetPermutation(*program, hw_stage, sw_stage, params, binding, runtime_info);
        }
        if (auto result =
                CompileNewProgram(hw_stage, sw_stage, params, binding, runtime_info, pools, false)) {
            return *result;
        }
        // Another thread translated it first: found at the top of the loop.
    }
}

std::optional<PipelineCache::Result> PipelineCache::CompileNewProgram(
    HwStage hw_stage, SwStage sw_stage, const Shader::ShaderParams& params,
    Shader::Backend::Bindings& binding, Shader::RuntimeInfo& runtime_info,
    Shader::Pools& translator_pools, bool speculative) {
    bool claimed = false;
    {
        std::unique_lock lock{programs_mutex};
        for (;;) {
            if (program_cache.contains(params.hash)) {
                return std::nullopt;
            }
            if (programs_compiling.insert(params.hash).second) {
                claimed = true;
                break;
            }
            if (speculative) {
                return std::nullopt;
            }
            // A worker is translating it: wait for that. Past the bound, translate a copy.
            if (!WaitForCompile(programs_cv, lock,
                                [&] { return !programs_compiling.contains(params.hash); })) {
                break;
            }
        }
    }
    // A copy of a translation another thread has under way is not written to the shader cache.
    const bool persist = claimed;
    auto new_program = std::make_unique<Program>(hw_stage, sw_stage, params);
    const auto start = binding;
    vk::ShaderModule module;
    std::optional<Shader::StageSpecialization> spec;
    // A worker's SPIR-V goes to the shader cache only once the translation is known good.
    std::vector<u32> spv;
    u64 code_before{};
    bool unchanged = true;
    const auto compile = [&] {
        code_before = CodeHash(params.code);
        module = CompileModule(new_program->info, runtime_info, params.code, 0, binding,
                               translator_pools, persist && !speculative,
                               speculative && persist ? &spv : nullptr);
        spec.emplace(new_program->info, runtime_info, profile, start);
        if (speculative) {
            // The bytes it translated must still be there, under the footer that names it.
            unchanged = CodeHash(params.code) == code_before &&
                        AmdGpu::SearchBinaryInfo(params.code.data()).shader_hash == params.hash;
        }
    };
    bool faulted = false;
    if (speculative) {
        // The code, user data and resource tables are read from guest memory, which a draw
        // prepared ahead of the GPU thread may have seen reused: a fault ends the attempt (the
        // GPU thread translates it when it gets there). The jump leaks the partial work.
        sigjmp_buf recover;
        if (sigsetjmp(recover, 0)) {
            faulted = true;
        } else {
            runtime_fault_recover = &recover;
            faulted = !SpeculativeCall(compile);
            runtime_fault_recover = nullptr;
        }
        runtime_fault_recover = nullptr;
    } else {
        compile();
    }
    if (!faulted && !unchanged) {
        instance.GetDevice().destroyShaderModule(module);
        NoteDiscarded(params.hash, 0, "its code changed while it was translated");
        faulted = true;
    }
    if (faulted) {
        if (claimed) {
            {
                std::unique_lock lock{programs_mutex};
                programs_compiling.erase(params.hash);
            }
            programs_cv.notify_all();
        }
        return std::nullopt;
    }
    if (speculative && persist) {
        RegisterShaderBinary(std::move(spv), params.hash, 0);
    }
    new_program->code_hash = code_before;
    const u64 perm_hash = HashCombine(params.hash, 0);
    if (persist) {
        RegisterShaderMeta(new_program->info, spec->fetch_shader_data, *spec, perm_hash, 0);
    }
    auto fetch_shader_data = spec->fetch_shader_data;
    new_program->AddPermut(module, std::move(*spec));
    new_program->info_template = std::make_unique<Shader::Info>(new_program->info);
    Program* program = new_program.get();
    bool inserted;
    {
        std::unique_lock lock{programs_mutex};
        inserted = program_cache.try_emplace(params.hash, std::move(new_program)).second;
        if (claimed) {
            programs_compiling.erase(params.hash);
        }
    }
    programs_cv.notify_all();
    if (!inserted) {
        instance.GetDevice().destroyShaderModule(module);
        return std::nullopt;
    }
    return std::make_tuple(&program->info, module, std::move(fetch_shader_data), perm_hash);
}

PipelineCache::Result PipelineCache::GetPermutation(Program& program, HwStage hw_stage,
                                                    SwStage sw_stage,
                                                    const Shader::ShaderParams& params,
                                                    Shader::Backend::Bindings& binding,
                                                    Shader::RuntimeInfo& runtime_info) {
    if (!program.info_template) {
        // Programs loaded by the pipeline cache warm-up get their template on first use.
        std::unique_lock lock{programs_mutex};
        program.info_template = std::make_unique<Shader::Info>(program.info);
        program.code_hash = CodeHash(params.code);
    }
    auto& info = program.info;
    info.pgm_base = params.Base(); // Needs to be actualized for inline cbuffer address fixup
    info.user_data = params.user_data;
    info.RefreshFlatBuf();
    const Shader::StageSpecialization spec(info, runtime_info, profile, binding);

    {
        std::shared_lock lock{programs_mutex};
        if (const auto idx = FindReadyPermutation(program, spec)) {
            program.last_used.store(*idx, std::memory_order_relaxed);
            info.AddBindings(binding);
            return std::make_tuple(&info, program.modules[*idx].module,
                                   program.modules[*idx].spec.fetch_shader_data,
                                   HashCombine(params.hash, *idx));
        }
    }
    // Not compiled: the GPU thread compiles it (or waits for a worker that is).
    return *CompilePermutation(program, info, spec, hw_stage, sw_stage, params, binding,
                               runtime_info, pools, false);
}

std::optional<PipelineCache::Result> PipelineCache::CompilePermutation(
    Program& program, Shader::Info& info, const Shader::StageSpecialization& spec,
    HwStage hw_stage, SwStage sw_stage, const Shader::ShaderParams& params,
    Shader::Backend::Bindings& binding, Shader::RuntimeInfo& runtime_info,
    Shader::Pools& translator_pools, bool speculative) {
    size_t perm_idx;
    {
        std::unique_lock lock{programs_mutex};
        for (;;) {
            if (const auto idx = FindReadyPermutation(program, spec)) {
                // Compiled by another thread since the caller looked.
                info.AddBindings(binding);
                return std::make_tuple(&info, program.modules[*idx].module,
                                       program.modules[*idx].spec.fetch_shader_data,
                                       HashCombine(params.hash, *idx));
            }
            const auto compiling = std::ranges::find_if(
                program.modules, [&](const Program::Module& module) {
                    return module.compiling && module.spec == spec;
                });
            if (compiling == program.modules.end()) {
                break;
            }
            if (speculative) {
                return std::nullopt;
            }
            // A worker is compiling it: wait for that. Past the bound, compile another copy.
            const size_t slot = static_cast<size_t>(compiling - program.modules.begin());
            if (!WaitForCompile(programs_cv, lock,
                                [&] { return !program.modules[slot].compiling; })) {
                break;
            }
        }
        // Reserved before compiling: the index names the shader cache files of this permutation.
        perm_idx = program.modules.size();
        program.modules.emplace_back(vk::ShaderModule{}, spec);
        program.modules.back().compiling = true;
        program.modules.back().spec.info = &program.info;
    }
    const u64 perm_hash = HashCombine(params.hash, perm_idx);
    vk::ShaderModule module;
    // A worker's SPIR-V goes to the shader cache only once the translation is known good.
    std::vector<u32> spv;
    u64 code_before{};
    u64 code_after{};
    bool same_resources = true;
    const auto compile = [&] {
        std::span<const u32> code = params.code;
        std::vector<u32> torn;
        if (speculative && TestTornShaderCode()) {
            torn.assign(code.begin(), code.end());
            std::fill(torn.begin() + torn.size() * 6 / 10, torn.end(), 0xBF810000u); // s_endpgm
            code = torn;
        }
        code_before = CodeHash(code);
        auto new_info = Shader::Info(hw_stage, sw_stage, params);
        module = CompileModule(new_info, runtime_info, code, perm_idx, binding, translator_pools,
                               !speculative, speculative ? &spv : nullptr);
        code_after = CodeHash(code);
        same_resources = SameResources(new_info, info);
    };
    bool faulted = false;
    if (speculative) {
        // As in CompileNewProgram: a fault in guest memory ends the attempt.
        sigjmp_buf recover;
        if (sigsetjmp(recover, 0)) {
            faulted = true;
        } else {
            runtime_fault_recover = &recover;
            faulted = !SpeculativeCall(compile);
            runtime_fault_recover = nullptr;
        }
        runtime_fault_recover = nullptr;
    } else {
        compile();
    }
    // bbport: a worker runs at idle priority and can fall far behind the GPU thread on a busy
    // CPU, until the game has reused the memory of the draw it is preparing. Its translation
    // then comes from other bytes than the program's, and lists other resources than the
    // program's Info, which the pipeline layout is built from: the layout and the module
    // disagree (VUID-VkGraphicsPipelineCreateInfo-layout-07988/07990). Such a translation is
    // thrown away; the GPU thread translates the draw in order.
    const char* dropped = faulted ? nullptr
                                  : BbSpec::DropReason(speculative, code_before, code_after,
                                                       program.code_hash, same_resources);
    if (!faulted && !speculative && (!same_resources || code_before != program.code_hash)) {
        // In order, the game itself put other code under this hash: nothing to fall back to,
        // but the log names it.
        static std::atomic<int> warned{0};
        if (warned.fetch_add(1, std::memory_order_relaxed) < 8) {
            LOG_WARNING(Render_Vulkan,
                        "Shader {:#x} permutation {}: code {:#x} (program {:#x}), resources {}",
                        params.hash, perm_idx, code_before, program.code_hash,
                        same_resources ? "the program's" : "not the program's");
        }
    }
    if (dropped) {
        instance.GetDevice().destroyShaderModule(module);
        NoteDiscarded(params.hash, perm_idx, dropped);
        faulted = true;
    }
    if (faulted) {
        {
            // The slot stays: an invalid specialization never matches.
            std::unique_lock lock{programs_mutex};
            auto& slot = program.modules[perm_idx];
            slot.spec.info = nullptr;
            slot.compiling = false;
        }
        programs_cv.notify_all();
        return std::nullopt;
    }
    if (speculative) {
        RegisterShaderBinary(std::move(spv), params.hash, perm_idx);
    }
    RegisterShaderMeta(info, spec.fetch_shader_data, spec, perm_hash, perm_idx);
    {
        std::unique_lock lock{programs_mutex};
        auto& slot = program.modules[perm_idx];
        slot.module = module;
        slot.compiling = false;
    }
    programs_cv.notify_all();
    return std::make_tuple(&info, module, spec.fetch_shader_data, perm_hash);
}

PipelineCache::Result PipelineCache::GetProgramSpeculative(PipelineSelection& sel,
                                                           HwStage hw_stage, SwStage sw_stage,
                                                           const Shader::ShaderParams& params,
                                                           Shader::Backend::Bindings& binding,
                                                           Shader::RuntimeInfo& runtime_info) {
    // bbport: draw-preparation worker: works on the worker's own Info copy. With
    // BB_ASYNC_COMPILE it also compiles what the caches lack; what it makes is not bound to
    // this draw, the GPU thread finds it in the caches.
    auto& worker = *sel.worker;
    const auto lookup = [&]() -> Program* {
        std::shared_lock lock{programs_mutex};
        const auto it = program_cache.find(params.hash);
        // Programs loaded by the warm-up get their template on the GPU thread's first use.
        return it != program_cache.end() && it.value()->info_template ? it.value().get() : nullptr;
    };
    Program* program = lookup();
    if (!program && async_compile) {
        if (!worker.pools) {
            worker.pools = std::make_unique<Shader::Pools>();
        }
        auto start = binding;
        CompileNewProgram(hw_stage, sw_stage, params, start, runtime_info, *worker.pools, true);
        program = lookup();
    }
    if (!program) {
        worker.failed = true;
        return {};
    }
    auto [it_info, new_info] = worker.infos.try_emplace(program, *program->info_template);
    auto& info = it_info->second;
    info.pgm_base = params.Base();
    info.user_data = params.user_data;
    // The walk of resource tables and the fetch shader parse read guest memory through
    // pointers in the registers; ahead of the GPU thread that memory may already be
    // reused. A fault returns here (runtime_fault_recover) and the draw is left to the GPU
    // thread. A jump out of the specialization leaks its partial allocations (rare).
    sigjmp_buf recover;
    if (sigsetjmp(recover, 0)) {
        worker.failed = true;
        return {};
    }
    runtime_fault_recover = &recover;
    std::optional<Shader::StageSpecialization> spec;
    const bool specialized = SpeculativeCall([&] {
        info.RefreshFlatBuf();
        spec.emplace(info, runtime_info, profile, binding);
    });
    runtime_fault_recover = nullptr;
    if (!specialized) {
        worker.failed = true;
        return {};
    }

    std::optional<Result> result;
    {
        std::shared_lock lock{programs_mutex};
        if (const auto idx = FindReadyPermutation(*program, *spec)) {
            result = std::make_tuple(&info, program->modules[*idx].module,
                                     program->modules[*idx].spec.fetch_shader_data,
                                     HashCombine(params.hash, *idx));
        }
    }
    if (result) {
        info.AddBindings(binding);
    } else if (async_compile) {
        if (!worker.pools) {
            worker.pools = std::make_unique<Shader::Pools>();
        }
        result = CompilePermutation(*program, info, *spec, hw_stage, sw_stage, params, binding,
                                    runtime_info, *worker.pools, true);
    }
    if (!result) {
        worker.failed = true;
        return {};
    }
    worker.stages.push_back(
        {program, params.hash, hw_stage, info.pgm_base, &info.flattened_ud_buf});
    return *result;
}

std::optional<vk::ShaderModule> PipelineCache::ReplaceShader(vk::ShaderModule module,
                                                             std::span<const u32> spv_code) {
    std::optional<vk::ShaderModule> new_module{};
    std::unique_lock programs_lock{programs_mutex};
    std::unique_lock pipelines_lock{pipelines_mutex};
    for (const auto& [_, program] : program_cache) {
        for (auto& m : program->modules) {
            if (m.module == module) {
                const auto& d = instance.GetDevice();
                d.destroyShaderModule(m.module);
                m.module = CompileSPV(spv_code, d);
                new_module = m.module;
            }
        }
    }
    if (module_related_pipelines.contains(module)) {
        auto& pipeline_keys = module_related_pipelines[module];
        for (auto& key : pipeline_keys) {
            if (std::holds_alternative<GraphicsPipelineKey>(key)) {
                auto& graphics_key = std::get<GraphicsPipelineKey>(key);
                graphics_pipelines.erase(graphics_key);
            } else if (std::holds_alternative<ComputePipelineKey>(key)) {
                auto& compute_key = std::get<ComputePipelineKey>(key);
                compute_pipelines.erase(compute_key);
            }
        }
    }
    return new_module;
}

std::string PipelineCache::GetShaderName(Shader::HwStage stage, u64 hash,
                                         std::optional<size_t> perm) {
    if (perm) {
        return fmt::format("{}_{:#018x}_{}", stage, hash, *perm);
    }
    return fmt::format("{}_{:#018x}", stage, hash);
}

void PipelineCache::DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage,
                               size_t perm_idx, std::string_view ext) {
    if (!EmulatorSettings.IsDumpShaders()) {
        return;
    }

    using namespace Common::FS;
    const auto dump_dir = GetUserPath(PathType::ShaderDir) / "dumps";
    if (!std::filesystem::exists(dump_dir)) {
        std::filesystem::create_directories(dump_dir);
    }
    const auto filename = fmt::format("{}.{}", GetShaderName(stage, hash, perm_idx), ext);
    const auto file = IOFile{dump_dir / filename, FileAccessMode::Create};
    file.WriteSpan(code);
}

std::optional<std::vector<u32>> PipelineCache::GetShaderPatch(u64 hash, Shader::HwStage stage,
                                                              size_t perm_idx,
                                                              std::string_view ext) {

    using namespace Common::FS;
    const auto patch_dir = GetUserPath(PathType::ShaderDir) / "patch";
    if (!std::filesystem::exists(patch_dir)) {
        std::filesystem::create_directories(patch_dir);
    }
    const auto filename = fmt::format("{}.{}", GetShaderName(stage, hash, perm_idx), ext);
    const auto filepath = patch_dir / filename;
    if (!std::filesystem::exists(filepath)) {
        return {};
    }
    const auto file = IOFile{patch_dir / filename, FileAccessMode::Read};
    std::vector<u32> code(file.GetSize() / sizeof(u32));
    file.Read(code);
    return code;
}
} // namespace Vulkan
