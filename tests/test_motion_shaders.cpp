// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the actual SPIR-V backend without opening a window or loading the game.
// Output is validated by spirv-val (see docs/upscaler.md).
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <setjmp.h>
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/ir/ir_emitter.h"


int main(int argc, char** argv) {
    using namespace Shader;
    const std::filesystem::path dir = argc > 1 ? argv[1] : ".";
    std::filesystem::create_directories(dir);
    MotionVectors::params_address = 0x10000;
    MotionVectors::positions_address = 0x20000;
    MotionVectors::diag_address = 0x30000;
    MotionVectors::param_entries = 1 + 4 * 8192;
    MotionVectors::position_elements = 1 + 2 * (4u << 20);
    // Every BB_OM_PART variant, and both ways of storing the position (BB_OM_STORE=plain),
    // must validate. Full keeps the original file names.
    const std::pair<MotionVectors::Part, const char*> parts[] = {
        {MotionVectors::Part::Full, ""},
        {MotionVectors::Part::NoBda, "-nobda"},
        {MotionVectors::Part::NoFs, "-nofs"},
        {MotionVectors::Part::NoVary, "-novary"},
    };
    for (const auto& [part, part_suffix] : parts) {
    MotionVectors::part = part;
    for (bool plain_store : {false, true}) {
    MotionVectors::plain_store = plain_store;
    for (bool vertex : {true, false}) {
        for (bool motion : {false, true}) {
            Info info{};
            info.hw_stage = vertex ? HwStage::Vertex : HwStage::Fragment;
            info.sw_stage = vertex ? SwStage::Vertex : SwStage::Fragment;
            RuntimeInfo runtime{};
            runtime.Initialize(info.hw_stage, info.sw_stage);
            if (vertex) {
                runtime.hw.vs.motion_vectors = motion;
            } else {
                // As the pipeline cache sets it.
                runtime.hw.fs.motion_vectors = motion && MotionVectors::WritesFragmentOutput();
                runtime.hw.fs.color_buffers[0].num_format = AmdGpu::NumberFormat::Float;
            }
            Common::ObjectPool<IR::Inst> pool;
            IR::Block block(pool);
            IR::IREmitter ir(block);
            const auto output = vertex ? IR::Attribute::Position0 : IR::Attribute::RenderTarget0;
            for (u32 component = 0; component < 4; ++component) {
                info.stores.Set(output, component);
                if (!vertex && component < 2) {
                    info.loads.Set(IR::Attribute::FragCoord, component);
                    ir.SetAttribute(output, ir.GetAttribute(IR::Attribute::FragCoord, component), component);
                } else {
                    ir.SetAttribute(output, ir.Imm32(component == 3 ? 1.0f : 0.0f), component);
                }
            }
            ir.Epilogue();
            IR::Program program(info);
            program.blocks.push_back(&block);
            program.syntax_list.push_back({.data = {.block = &block},
                                           .type = IR::AbstractSyntaxNode::Type::Block});
            program.syntax_list.push_back({.type = IR::AbstractSyntaxNode::Type::Return});
            Profile profile{};
            profile.supported_spirv = 0x00010600;
            profile.support_int64 = true;
            Backend::Bindings bindings{};
            const auto code = Backend::SPIRV::EmitSPIRV(profile, runtime, program, bindings);
            const auto path = dir / (std::string(vertex ? "vertex" : "fragment") +
                                      (motion ? "-motion" : "-plain") + part_suffix +
                                      (plain_store ? "-plainstore.spv" : ".spv"));
            std::ofstream out(path, std::ios::binary);
            out.write(reinterpret_cast<const char*>(code.data()), code.size() * sizeof(u32));
            if (!out) { return 1; }
        }
    }
    }
    }
}
