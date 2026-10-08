// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "shader_recompiler/backend/spirv/emit_spirv_instructions.h"
#include "shader_recompiler/backend/spirv/spirv_emit_context.h"
#include "shader_recompiler/ir/debug_print.h"
#include "shader_recompiler/ir/microinstruction.h"

namespace Shader::Backend::SPIRV {

void EmitPrologue(EmitContext& ctx) {
    if (ctx.hw_stage == HwStage::Fragment) {
        ctx.DefineAmdPerVertexAttribs();
    }
    if (ctx.info.loads.Get(IR::Attribute::WorkgroupIndex)) {
        ctx.DefineWorkgroupIndex();
    }
    ctx.DefineBufferProperties();
}

void ConvertDepthMode(EmitContext& ctx) {
    const Id type{ctx.F32[1]};
    const Id position{ctx.OpLoad(ctx.F32[4], ctx.output_position)};
    const Id z{ctx.OpCompositeExtract(type, position, 2u)};
    const Id w{ctx.OpCompositeExtract(type, position, 3u)};
    const Id screen_depth{ctx.OpFMul(type, ctx.OpFAdd(type, z, w), ctx.Constant(type, 0.5f))};
    const Id vector{ctx.OpCompositeInsert(ctx.F32[4], screen_depth, position, 2u)};
    ctx.OpStore(ctx.output_position, vector);
}

void ConvertPositionToClipSpace(EmitContext& ctx) {
    ASSERT_MSG(!ctx.info.stores.GetAny(IR::Attribute::ViewportIndex),
               "Multi-viewport with shader clip space conversion not yet implemented.");

    const Id type{ctx.F32[1]};
    Id position{ctx.OpLoad(ctx.F32[4], ctx.output_position)};
    const Id x{ctx.OpCompositeExtract(type, position, 0u)};
    const Id y{ctx.OpCompositeExtract(type, position, 1u)};
    const Id z{ctx.OpCompositeExtract(type, position, 2u)};
    const Id w{ctx.OpCompositeExtract(type, position, 3u)};
    const Id xoffset_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                           ctx.push_data_block,
                                           ctx.ConstU32(PushData::XOffsetIndex))};
    const Id xoffset{ctx.OpLoad(type, xoffset_ptr)};
    const Id yoffset_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                           ctx.push_data_block,
                                           ctx.ConstU32(PushData::YOffsetIndex))};
    const Id yoffset{ctx.OpLoad(type, yoffset_ptr)};
    const Id xscale_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                          ctx.push_data_block,
                                          ctx.ConstU32(PushData::XScaleIndex))};
    const Id xscale{ctx.OpLoad(type, xscale_ptr)};
    const Id yscale_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                          ctx.push_data_block,
                                          ctx.ConstU32(PushData::YScaleIndex))};
    const Id yscale{ctx.OpLoad(type, yscale_ptr)};
    const Id vport_w =
        ctx.Constant(type, float(std::min<u32>(ctx.profile.max_viewport_width / 2, 8_KB)));
    const Id wnd_x = ctx.OpFAdd(type, ctx.OpFMul(type, x, xscale), xoffset);
    const Id ndc_x = ctx.OpFSub(type, ctx.OpFDiv(type, wnd_x, vport_w), ctx.Constant(type, 1.f));
    const Id vport_h =
        ctx.Constant(type, float(std::min<u32>(ctx.profile.max_viewport_height / 2, 8_KB)));
    const Id wnd_y = ctx.OpFAdd(type, ctx.OpFMul(type, y, yscale), yoffset);
    const Id ndc_y = ctx.OpFSub(type, ctx.OpFDiv(type, wnd_y, vport_h), ctx.Constant(type, 1.f));
    const Id vector{ctx.OpCompositeConstruct(ctx.F32[4], std::array<Id, 4>({ndc_x, ndc_y, z, w}))};
    ctx.OpStore(ctx.output_position, vector);
}

// bbport: object motion vectors (runtime_info.h, MotionVectors). The vertex shader stores its
// clip position for this frame and loads the one of the previous frame (same draw, same
// vertex), both by buffer device address. Disabled accesses are branched around: a shared
// scratch element would race between all the inactive vertex invocations.
//
// Every access is guarded: the params index must be inside the ring and the entry must carry
// the tag of its words and draw (else it was overwritten under the GPU), and the positions
// element of a store or load must be inside the buffer. A failed guard skips the access and
// counts in the diagnostics array, so no params the CPU wrote wrongly can reach other memory.
static void EmitVertexMotion(EmitContext& ctx) {
    const Id u32_type = ctx.U32[1];
    const Id bool_type = ctx.U1[1];
    const Id position = ctx.OpLoad(ctx.F32[4], ctx.output_position);
    const Id param_ptr = ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, u32_type),
                                           ctx.push_data_block,
                                           ctx.ConstU32(PushData::MotionParamIndex));
    const Id motion_param = ctx.OpLoad(u32_type, param_ptr);
    const Id param_index = ctx.OpBitwiseAnd(u32_type, motion_param, ctx.ConstU32(0xFFFFu));
    const Id frame16 = ctx.OpShiftRightLogical(u32_type, motion_param, ctx.ConstU32(16u));
    const Id active = ctx.OpINotEqual(bool_type, param_index, ctx.u32_zero_value);
    const Id index_ok = ctx.OpLogicalAnd(
        bool_type, active,
        ctx.OpULessThan(bool_type, param_index, ctx.ConstU32(MotionVectors::param_entries)));
    // An index outside the ring reads element 0 (always zero) instead.
    const Id safe_index = ctx.OpSelect(u32_type, index_ok, param_index, ctx.u32_zero_value);
    const auto address = [&](u64 base, Id index, u32 stride) {
        return ctx.OpIAdd(ctx.U64, ctx.Constant(ctx.U64, base),
                          ctx.OpIMul(ctx.U64, ctx.OpUConvert(ctx.U64, index),
                                     ctx.Constant(ctx.U64, u64(stride))));
    };
    const Id u32x4_ptr = ctx.TypePointer(spv::StorageClass::PhysicalStorageBuffer, ctx.U32[4]);
    const Id f32x4_ptr = ctx.TypePointer(spv::StorageClass::PhysicalStorageBuffer, ctx.F32[4]);
    const Id scalar_ptr = ctx.TypePointer(spv::StorageClass::PhysicalStorageBuffer, u32_type);
    const Id scope = ctx.ConstU32(static_cast<u32>(spv::Scope::Device));
    const Id params = ctx.OpLoad(
        ctx.U32[4],
        ctx.OpConvertUToPtr(u32x4_ptr, address(MotionVectors::params_address, safe_index, 32)),
        spv::MemoryAccessMask::Aligned, 16u);
    const Id store_base = ctx.OpCompositeExtract(u32_type, params, 0u);
    const Id load_base = ctx.OpCompositeExtract(u32_type, params, 1u);
    const Id vertices = ctx.OpCompositeExtract(u32_type, params, 2u);
    const Id flags = ctx.OpCompositeExtract(u32_type, params, 3u);
    const Id offsets = ctx.OpLoad(
        ctx.U32[4], ctx.OpConvertUToPtr(u32x4_ptr,
            address(MotionVectors::params_address + 16, safe_index, 32)),
        spv::MemoryAccessMask::Aligned, 16u);
    const Id first_vertex = ctx.OpCompositeExtract(u32_type, offsets, 0u);
    const Id first_instance = ctx.OpCompositeExtract(u32_type, offsets, 1u);
    const Id instances = ctx.OpCompositeExtract(u32_type, offsets, 2u);
    const Id entry_tag = ctx.OpCompositeExtract(u32_type, offsets, 3u);
    // The same mix as MotionVectors::Tag on the CPU.
    Id tag = ctx.OpIAdd(u32_type, ctx.ConstU32(MotionVectors::TagSeed), frame16);
    for (const Id word : {store_base, load_base, vertices, flags, first_vertex, first_instance,
                          instances}) {
        tag = ctx.OpIMul(u32_type, ctx.OpBitwiseXor(u32_type, tag, word),
                         ctx.ConstU32(MotionVectors::TagPrime));
    }
    tag = ctx.OpBitwiseXor(u32_type, tag,
                           ctx.OpShiftRightLogical(u32_type, tag, ctx.ConstU32(15u)));
    const Id tag_ok = MotionVectors::guards ? ctx.OpIEqual(bool_type, tag, entry_tag)
                                            : ctx.ConstantTrue(bool_type);
    const Id entry_valid = ctx.OpLogicalAnd(bool_type, index_ok, tag_ok);

    const auto diag_ptr = [&](u32 word) {
        return ctx.OpConvertUToPtr(scalar_ptr, ctx.Constant(ctx.U64, MotionVectors::diag_address +
                                                                         u64(word) * 4));
    };
    // Counts a violation (and notes the push constant of the last one) when `condition` holds.
    const auto count = [&](Id condition, u32 counter, u32 note_word) {
        if (!MotionVectors::diag_address) {
            return;
        }
        const Id label = ctx.OpLabel();
        const Id merge = ctx.OpLabel();
        ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
        ctx.OpBranchConditional(condition, label, merge);
        ctx.AddLabel(label);
        ctx.OpAtomicIAdd(u32_type, diag_ptr(counter), scope, ctx.u32_zero_value, ctx.ConstU32(1u));
        if (note_word) {
            ctx.OpStore(diag_ptr(note_word), motion_param, spv::MemoryAccessMask::Aligned, 4u);
        }
        ctx.OpBranch(merge);
        ctx.AddLabel(merge);
    };
    count(ctx.OpLogicalAnd(bool_type, active, ctx.OpLogicalNot(bool_type, index_ok)),
          MotionVectors::DiagBadIndex, MotionVectors::DiagLastBadIndex);
    count(ctx.OpLogicalAnd(bool_type, index_ok, ctx.OpLogicalNot(bool_type, tag_ok)),
          MotionVectors::DiagTornParams, MotionVectors::DiagLastTorn);

    const Id vertex = ctx.OpISub(u32_type, ctx.OpLoad(u32_type, ctx.vertex_index), first_vertex);
    const Id instance = ctx.OpISub(u32_type, ctx.OpLoad(u32_type, ctx.instance_id), first_instance);
    const Id in_range = ctx.OpLogicalAnd(bool_type, ctx.OpULessThan(bool_type, vertex, vertices),
                                         ctx.OpULessThan(bool_type, instance, instances));
    // The element offset in 64 bits: nothing wraps, whatever the params hold.
    const Id slot = ctx.OpIAdd(ctx.U64, ctx.OpUConvert(ctx.U64, vertex),
                               ctx.OpIMul(ctx.U64, ctx.OpUConvert(ctx.U64, instance),
                                          ctx.OpUConvert(ctx.U64, vertices)));
    const auto wanted = [&](u32 bit) {
        return ctx.OpLogicalAnd(
            bool_type, ctx.OpLogicalAnd(bool_type, entry_valid, in_range),
            ctx.OpINotEqual(bool_type, ctx.OpBitwiseAnd(u32_type, flags, ctx.ConstU32(bit)),
                            ctx.u32_zero_value));
    };
    const auto element_ok = [&](Id element) {
        if (!MotionVectors::guards) {
            return ctx.ConstantTrue(bool_type);
        }
        return ctx.OpLogicalAnd(
            bool_type,
            ctx.OpUGreaterThanEqual(bool_type, element, ctx.Constant(ctx.U64, u64(1))),
            ctx.OpULessThan(bool_type, element,
                            ctx.Constant(ctx.U64, u64(MotionVectors::position_elements))));
    };
    const auto element_address = [&](Id element) {
        return ctx.OpIAdd(ctx.U64, ctx.Constant(ctx.U64, MotionVectors::positions_address),
                          ctx.OpIMul(ctx.U64, element, ctx.Constant(ctx.U64, u64(16))));
    };
    const Id want_store = wanted(MotionVectors::FlagStore);
    const Id want_load = wanted(MotionVectors::FlagLoad);
    const Id store_element = ctx.OpIAdd(ctx.U64, ctx.OpUConvert(ctx.U64, store_base), slot);
    const Id load_element = ctx.OpIAdd(ctx.U64, ctx.OpUConvert(ctx.U64, load_base), slot);
    const Id store_ok = element_ok(store_element);
    const Id load_ok = element_ok(load_element);
    count(ctx.OpLogicalAnd(bool_type, want_store, ctx.OpLogicalNot(bool_type, store_ok)),
          MotionVectors::DiagStoreOob, 0);
    count(ctx.OpLogicalAnd(bool_type, want_load, ctx.OpLogicalNot(bool_type, load_ok)),
          MotionVectors::DiagLoadOob, 0);
    const Id do_store = ctx.OpLogicalAnd(bool_type, want_store, store_ok);
    const Id do_load = ctx.OpLogicalAnd(bool_type, want_load, load_ok);
    const Id store_label = ctx.OpLabel();
    const Id store_merge = ctx.OpLabel();
    ctx.OpSelectionMerge(store_merge, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(do_store, store_label, store_merge);
    ctx.AddLabel(store_label);
    const Id store_address = element_address(store_element);
    if (MotionVectors::plain_store) {
        // Indexed draws may invoke the same vertex more than once, all with the same value.
        ctx.OpStore(ctx.OpConvertUToPtr(f32x4_ptr, store_address), position,
                    spv::MemoryAccessMask::Aligned, 16u);
    } else {
        // Atomic component stores avoid write/write races; all these invocations produce the
        // same clip position.
        for (u32 i = 0; i < 4; ++i) {
            const Id ptr = ctx.OpConvertUToPtr(scalar_ptr,
                ctx.OpIAdd(ctx.U64, store_address, ctx.Constant(ctx.U64, u64(i * 4))));
            const Id bits = ctx.OpBitcast(u32_type, ctx.OpCompositeExtract(ctx.F32[1], position, i));
            ctx.OpAtomicExchange(u32_type, ptr, scope, ctx.u32_zero_value, bits);
        }
    }
    ctx.OpBranch(store_merge);
    ctx.AddLabel(store_merge);

    const Id load_label = ctx.OpLabel();
    const Id load_merge = ctx.OpLabel();
    ctx.OpSelectionMerge(load_merge, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(do_load, load_label, load_merge);
    ctx.AddLabel(load_label);
    const Id loaded = ctx.OpLoad(
        ctx.F32[4], ctx.OpConvertUToPtr(f32x4_ptr, element_address(load_element)),
        spv::MemoryAccessMask::Aligned, 16u);
    ctx.OpBranch(load_merge);
    ctx.AddLabel(load_merge);
    Id previous = ctx.OpPhi(ctx.F32[4], position, store_merge, loaded, load_label);
    const Id valid = ctx.OpSelect(ctx.F32[1], do_load, ctx.Constant(ctx.F32[1], 1.0f),
                                  ctx.Constant(ctx.F32[1], 0.0f));
    previous = ctx.OpCompositeInsert(ctx.F32[4], valid, previous, 2u);
    ctx.OpStore(ctx.motion_out_cur, position);
    ctx.OpStore(ctx.motion_out_prev, previous);
}

// Screen-space motion (previous minus current, pixels) through the viewport scale; z = valid.
static void EmitFragmentMotion(EmitContext& ctx) {
    const Id f32_type = ctx.F32[1];
    const Id current = ctx.OpLoad(ctx.F32[4], ctx.motion_in_cur);
    const Id previous = ctx.OpLoad(ctx.F32[4], ctx.motion_in_prev);
    const auto push = [&](u32 index) {
        return ctx.OpLoad(f32_type, ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant,
                                                                 f32_type),
                                                 ctx.push_data_block, ctx.ConstU32(index)));
    };
    const Id xscale = push(PushData::XScaleIndex);
    const Id yscale = push(PushData::YScaleIndex);
    const auto ndc = [&](Id clip, u32 component) {
        return ctx.OpFDiv(f32_type, ctx.OpCompositeExtract(f32_type, clip, component),
                          ctx.OpCompositeExtract(f32_type, clip, 3u));
    };
    const Id dx = ctx.OpFMul(f32_type, ctx.OpFSub(f32_type, ndc(previous, 0u), ndc(current, 0u)), xscale);
    const Id dy = ctx.OpFMul(f32_type, ctx.OpFSub(f32_type, ndc(previous, 1u), ndc(current, 1u)), yscale);
    const Id zero = ctx.Constant(f32_type, 0.0f);
    const Id epsilon = ctx.Constant(f32_type, 1e-5f);
    const Id front = ctx.OpLogicalAnd(ctx.U1[1],
        ctx.OpFOrdGreaterThan(ctx.U1[1], ctx.OpCompositeExtract(f32_type, previous, 3u), epsilon),
        ctx.OpFOrdGreaterThan(ctx.U1[1], ctx.OpCompositeExtract(f32_type, current, 3u), epsilon));
    const Id valid = ctx.OpSelect(f32_type, front,
                                  ctx.OpCompositeExtract(f32_type, previous, 2u), zero);
    // Uninstrumented draws can overwrite the final scene depth after this pixel was
    // written. Preserve depth so the compose pass can reject an occluded vector.
    const Id depth = ctx.OpCompositeExtract(f32_type,
                                            ctx.OpLoad(ctx.F32[4], ctx.frag_coord), 2u);
    ctx.OpStore(ctx.motion_frag_out,
                ctx.OpCompositeConstruct(ctx.F32[4], std::array<Id, 4>{
                    ctx.OpSelect(f32_type, front, dx, zero),
                    ctx.OpSelect(f32_type, front, dy, zero), valid, depth}));
}

void EmitEpilogue(EmitContext& ctx) {
    if (Sirit::ValidId(ctx.motion_out_cur)) {
        EmitVertexMotion(ctx);
    }
    if (Sirit::ValidId(ctx.motion_frag_out)) {
        EmitFragmentMotion(ctx);
    }
    if (ctx.hw_stage == HwStage::Vertex &&
        ctx.runtime_info.hw.vs.emulate_depth_negative_one_to_one) {
        ConvertDepthMode(ctx);
    }
    if (ctx.hw_stage == HwStage::Vertex && ctx.runtime_info.hw.vs.clip_disable) {
        ConvertPositionToClipSpace(ctx);
    }
}

void EmitDiscard(EmitContext& ctx) {
    ctx.OpDemoteToHelperInvocationEXT();
}

void EmitDiscardCond(EmitContext& ctx, Id condition) {
    const Id kill_label{ctx.OpLabel()};
    const Id merge_label{ctx.OpLabel()};
    ctx.OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(condition, kill_label, merge_label);
    ctx.AddLabel(kill_label);
    ctx.OpDemoteToHelperInvocationEXT();
    ctx.OpBranch(merge_label);
    ctx.AddLabel(merge_label);
}

void EmitEmitVertex(EmitContext& ctx) {
    ctx.OpEmitVertex();
}

void EmitEmitPrimitive(EmitContext& ctx) {
    ctx.OpEndPrimitive();
}

void EmitEmitVertex(EmitContext& ctx, const IR::Value& stream) {
    UNREACHABLE_MSG("Geometry streams");
}

void EmitEndPrimitive(EmitContext& ctx, const IR::Value& stream) {
    UNREACHABLE_MSG("Geometry streams");
}

void EmitDebugPrint(EmitContext& ctx, IR::Inst* inst, Id fmt, Id arg0, Id arg1, Id arg2, Id arg3) {
    IR::DebugPrintFlags flags = inst->Flags<IR::DebugPrintFlags>();
    std::array<Id, IR::DEBUGPRINT_NUM_FORMAT_ARGS> fmt_args = {arg0, arg1, arg2, arg3};
    auto fmt_args_span = std::span<Id>(fmt_args.begin(), fmt_args.begin() + flags.num_args);
    ctx.OpDebugPrintf(fmt, fmt_args_span);
}

Id EmitMemtime(EmitContext& ctx) {
    if (ctx.profile.supports_shader_subgroup_clock) {
        return ctx.OpReadClockKHR(ctx.U64, ctx.ConstU32(std::to_underlying(spv::Scope::Subgroup)));
    } else {
        return ctx.Constant(ctx.U64, 1U);
    }
}

} // namespace Shader::Backend::SPIRV
