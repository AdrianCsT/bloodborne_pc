// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/renderer_vulkan/vk_object_motion.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <vk_mem_alloc.h>
#include "bbport_settings.h"
#include "bbport_toggles.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan {

namespace {

bool CreateBuffer(const Instance& instance, vk::DeviceSize size, bool host, vk::Buffer& buffer,
                  VmaAllocation& allocation, void** mapped, u64& address,
                  VkBufferUsageFlags extra_usage = 0) {
    const VkBufferCreateInfo buffer_ci{
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT |
                 extra_usage,
    };
    VmaAllocationCreateInfo alloc_ci{
        .usage = VMA_MEMORY_USAGE_AUTO,
    };
    if (host) {
        alloc_ci.requiredFlags = VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        alloc_ci.flags = VMA_ALLOCATION_CREATE_MAPPED_BIT |
                         VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT;
    } else {
        alloc_ci.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE;
    }
    VkBuffer raw{};
    VmaAllocationInfo info{};
    if (vmaCreateBuffer(instance.GetAllocator(), &buffer_ci, &alloc_ci, &raw, &allocation, &info) !=
        VK_SUCCESS) {
        return false;
    }
    buffer = raw;
    if (mapped) {
        *mapped = info.pMappedData;
    }
    address = instance.GetDevice().getBufferAddress({.buffer = buffer});
    return address != 0;
}

} // namespace

ObjectMotion::ObjectMotion(const Instance& instance_, Scheduler& scheduler_)
    : instance{instance_}, scheduler{scheduler_} {
    // Pipeline selection limits the extra attachment and vertex stores to likely
    // animated draws. A setting or environment override can still disable the path.
    if (!BbSettings::Get().object_motion ||
        BbSettings::Get().upscaler == BbSettings::UpscalerOff) {
        std::puts("Object motion: off (enable in menu or BB_OBJECT_MOTION=1)");
        return;
    }
#ifdef _WIN32
    // bbport: AMD's Windows driver loses the device on the per-vertex motion stores 35-55 s in
    // (deadinside28/bloodborne_pc#39: five of five runs; stable with the stores off). Off there
    // unless BB_OBJECT_MOTION_AMD=1; the upscaler still runs with camera motion only.
    if (instance.GetVendorID() == 0x1002) {
        const char* env = std::getenv("BB_OBJECT_MOTION_AMD");
        if (!env || std::strcmp(env, "1") != 0) {
            std::puts("Object motion: off on AMD under Windows (driver hang, see #39; "
                      "BB_OBJECT_MOTION_AMD=1 enables it)");
            return;
        }
    }
#endif
    const auto& features = instance.GetPhysicalDevice().getFeatures();
    if (!features.vertexPipelineStoresAndAtomics) {
        std::printf("Object motion: vertexPipelineStoresAndAtomics unsupported, off\n");
        return;
    }
    // Stress tests of the parameter ring (see the guards in Shader::MotionVectors).
    if (const char* env = std::getenv("BB_OM_SLOTS")) {
        frame_slots = std::clamp<u32>(u32(std::strtoul(env, nullptr, 10)), 1, MaxFrameSlots);
    }
    if (const char* env = std::getenv("BB_OM_NOWAIT")) {
        no_param_wait = std::strcmp(env, "1") == 0;
    }
    if (const char* env = std::getenv("BB_OM_GUARD")) {
        Shader::MotionVectors::guards = std::strcmp(env, "0") != 0;
    }
    if (const char* env = std::getenv("BB_OM_STORE")) {
        Shader::MotionVectors::plain_store = std::strcmp(env, "plain") == 0;
    }
    void* mapped = nullptr;
    void* read_mapped = nullptr;
    u64 params_address = 0, positions_address = 0, diag_address = 0, read_address = 0;
    if (!CreateBuffer(instance, vk::DeviceSize(1 + MaxFrameSlots * ParamsPerFrame) * 32, true,
                      params_buffer, params_allocation, &mapped, params_address) ||
        !CreateBuffer(instance, vk::DeviceSize(1 + 2 * PositionsPerFrame) * 16, false,
                      positions_buffer, positions_allocation, nullptr, positions_address) ||
        !CreateBuffer(instance, DiagWords * sizeof(u32), false, diag_buffer, diag_allocation,
                      nullptr, diag_address,
                      VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT) ||
        !CreateBuffer(instance, MaxFrameSlots * DiagWords * sizeof(u32), true, diag_read_buffer,
                      diag_read_allocation, &read_mapped, read_address,
                      VK_BUFFER_USAGE_TRANSFER_DST_BIT)) {
        std::printf("Object motion: buffer creation failed, off\n");
        return;
    }
    static_assert(DiagWords == Shader::MotionVectors::DiagWords);
    params_mapped = static_cast<u32*>(mapped);
    std::memset(params_mapped, 0, 32);
    diag_read_mapped = static_cast<const u32*>(read_mapped);
    std::memset(read_mapped, 0, MaxFrameSlots * DiagWords * sizeof(u32));
    Shader::MotionVectors::params_address = params_address;
    Shader::MotionVectors::positions_address = positions_address;
    Shader::MotionVectors::param_entries = 1 + frame_slots * ParamsPerFrame;
    Shader::MotionVectors::position_elements = 1 + 2 * PositionsPerFrame;
    Shader::MotionVectors::diag_address = diag_address;
    enabled = true;
    std::printf("Object motion: on (%u vertices per frame, %u parameter slots%s, %s stores%s)\n",
                PositionsPerFrame, frame_slots, no_param_wait ? ", no slot wait" : "",
                Shader::MotionVectors::plain_store ? "plain" : "atomic",
                Shader::MotionVectors::guards ? "" : ", GUARDS OFF");
}

ObjectMotion::~ObjectMotion() {
    scheduler.Finish();
    const auto allocator = instance.GetAllocator();
    if (params_buffer) {
        vmaDestroyBuffer(allocator, params_buffer, params_allocation);
    }
    if (positions_buffer) {
        vmaDestroyBuffer(allocator, positions_buffer, positions_allocation);
    }
    if (diag_buffer) {
        vmaDestroyBuffer(allocator, diag_buffer, diag_allocation);
    }
    if (diag_read_buffer) {
        vmaDestroyBuffer(allocator, diag_read_buffer, diag_read_allocation);
    }
}

void ObjectMotion::ReadDiagnostics() {
    // The copy of a slot is recorded at the start of a frame; read every copy the GPU is past.
    using MV = Shader::MotionVectors;
    bool changed = false;
    for (u32 slot = 0; slot < frame_slots; ++slot) {
        if (!diag_ticks[slot] || !scheduler.IsFree(diag_ticks[slot])) {
            continue;
        }
        u32 words[DiagWords];
        std::memcpy(words, diag_read_mapped + slot * DiagWords, sizeof(words));
        diag_ticks[slot] = 0;
        for (u32 i = 0; i < DiagWords; ++i) {
            if (i <= MV::DiagBadIndex ? words[i] > diag_seen[i] : words[i] != diag_seen[i]) {
                diag_seen[i] = words[i];
                changed = changed || i <= MV::DiagBadIndex;
            }
        }
    }
    if (changed && !diag_reported) {
        diag_reported = true;
        std::printf("Object motion: blocked %u store OOB, %u load OOB, %u torn params, %u bad index "
                    "(frame %llu; last bad index %08x, last torn %08x)\n",
                    diag_seen[MV::DiagStoreOob], diag_seen[MV::DiagLoadOob],
                    diag_seen[MV::DiagTornParams], diag_seen[MV::DiagBadIndex],
                    (unsigned long long)frame, diag_seen[MV::DiagLastBadIndex],
                    diag_seen[MV::DiagLastTorn]);
    }
}

void ObjectMotion::RecordDiagnostics(u32 slot) {
    const bool clear = !diag_cleared;
    diag_cleared = true;
    const vk::Buffer source = diag_buffer;
    const vk::Buffer target = diag_read_buffer;
    const vk::DeviceSize offset = vk::DeviceSize(slot) * DiagWords * sizeof(u32);
    scheduler.Record([=](vk::CommandBuffer cmd) {
        if (clear) {
            cmd.fillBuffer(source, 0, VK_WHOLE_SIZE, 0);
            const vk::MemoryBarrier2 filled{
                .srcStageMask = vk::PipelineStageFlagBits2::eTransfer,
                .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
                .dstStageMask = vk::PipelineStageFlagBits2::eVertexShader |
                                vk::PipelineStageFlagBits2::eTransfer,
                .dstAccessMask = vk::AccessFlagBits2::eShaderRead |
                                 vk::AccessFlagBits2::eShaderWrite |
                                 vk::AccessFlagBits2::eTransferRead,
            };
            cmd.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &filled});
        }
        const vk::MemoryBarrier2 written{
            .srcStageMask = vk::PipelineStageFlagBits2::eVertexShader,
            .srcAccessMask = vk::AccessFlagBits2::eShaderWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &written});
        const vk::BufferCopy region{0, offset, DiagWords * sizeof(u32)};
        cmd.copyBuffer(source, target, region);
        const vk::MemoryBarrier2 readable{
            .srcStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &readable});
    });
    diag_ticks[slot] = scheduler.CurrentTick();
}

void ObjectMotion::OnFrameStart() {
    if (!enabled) {
        return;
    }
    if (BbStats::enabled && (frame % 600) == 0 && history.stats.draws) {
        const auto& s = history.stats;
        std::printf("Object motion: %llu draws, %llu stored, %llu with history, %llu unmatched, "
                    "%llu capacity skips, %llu invalid, %llu still (600 frames); last frame %u/%u vertices; "
                    "index ranges %llu reused, %llu scanned (%llu found changed), %zu cached\n",
                    (unsigned long long)s.draws, (unsigned long long)s.stored,
                    (unsigned long long)s.loaded, (unsigned long long)s.unmatched,
                    (unsigned long long)s.exhausted, (unsigned long long)s.invalid,
                    (unsigned long long)s.still,
                    history.Used(), PositionsPerFrame, (unsigned long long)index_ranges.stats.hits,
                    (unsigned long long)index_ranges.stats.scans,
                    (unsigned long long)index_ranges.stats.stale, index_ranges.Size());
        index_ranges.stats = {};
        history.stats = {};
        using MV = Shader::MotionVectors;
        std::printf("Object motion: guards %u store OOB, %u load OOB, %u torn params, %u bad index "
                    "(totals, frame %llu)\n",
                    diag_seen[MV::DiagStoreOob], diag_seen[MV::DiagLoadOob],
                    diag_seen[MV::DiagTornParams], diag_seen[MV::DiagBadIndex],
                    (unsigned long long)frame);
    }
    history.NextFrame();
    ++frame;
    if (frame % Motion::IndexRangeCache::Unused == 0) {
        index_ranges.Trim(frame);
    }
    const u32 slot = u32(frame % frame_slots);
    // Do not overwrite host parameters still referenced by queued GPU work.
    if (params_ticks[slot] && !no_param_wait) {
        scheduler.Wait(params_ticks[slot]);
        params_ticks[slot] = 0;
    }
    ReadDiagnostics();
    params_used = 0;
    for (auto& target : targets) {
        target.written = false;
    }
    scheduler.EndRendering();
    // Last frame's stores must be visible to this frame's loads. Also finish old reads
    // before the ping-pong half they reference is reused for writes.
    scheduler.Record([](vk::CommandBuffer cmd) {
        const vk::MemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eVertexShader,
            .srcAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eVertexShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &barrier});
    });
    RecordDiagnostics(slot);
}

u32 ObjectMotion::PrepareDraw(const DrawInfo& draw) {
    if (!enabled || BbToggle::Disabled(1u << 29) || params_used >= ParamsPerFrame) {
        return 0;
    }
    const auto allocation = history.Prepare(draw);
    u32 flags = (allocation.store ? Shader::MotionVectors::FlagStore : 0) |
                (allocation.load ? Shader::MotionVectors::FlagLoad : 0);
    if (!flags) {
        return 0;
    }
    const u32 slot = u32(frame % frame_slots);
    const u32 index = 1 + slot * ParamsPerFrame + params_used++;
    const u32 frame16 = u32(frame) & 0xFFFF;
    u32* param = params_mapped + index * 8;
    param[0] = allocation.store;
    param[1] = allocation.load;
    param[2] = allocation.vertices;
    param[3] = flags;
    param[4] = allocation.first_vertex;
    param[5] = allocation.first_instance;
    param[6] = allocation.instances;
    param[7] = Shader::MotionVectors::Tag(param, frame16);
    params_ticks[slot] = scheduler.CurrentTick();
    return index | frame16 << 16;
}

ObjectMotion::Target& ObjectMotion::GetTarget(u32 width, u32 height) {
    for (auto& target : targets) {
        if (target.image && target.width == width && target.height == height) {
            target.used_frame = frame;
            return target;
        }
    }
    // A free slot, else the image used longest ago (not the one being read).
    Target* slot = nullptr;
    for (auto& target : targets) {
        if (!target.image) {
            slot = &target;
            break;
        }
        if (&target != read_target && (!slot || target.used_frame < slot->used_frame)) {
            slot = &target;
        }
    }
    if (slot->image) {
        // Destroyed once the GPU is past the work recorded so far (it may still use it).
        scheduler.DeferOperation([image = std::move(slot->image),
                                  view = std::move(slot->view)]() mutable {
            view.reset();
            image.Destroy();
        });
    }
    static u32 reports = 0;
    if (BbStats::enabled && reports < 16) {
        ++reports;
        std::printf("Object motion: motion image %ux%u%s\n", width, height,
                    slot->width ? " (replacing one)" : "");
    }
    const auto device = instance.GetDevice();
    slot->image = VideoCore::UniqueImage(device, instance.GetAllocator());
    slot->image.Create(vk::ImageCreateInfo{
        .imageType = vk::ImageType::e2D,
        .format = vk::Format::eR32G32B32A32Sfloat,
        .extent = {width, height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = vk::SampleCountFlagBits::e1,
        .tiling = vk::ImageTiling::eOptimal,
        .usage = vk::ImageUsageFlagBits::eColorAttachment | vk::ImageUsageFlagBits::eSampled |
                 vk::ImageUsageFlagBits::eTransferSrc,
        .initialLayout = vk::ImageLayout::eUndefined,
    });
    slot->view = Check(device.createImageViewUnique({
        .image = vk::Image(slot->image),
        .viewType = vk::ImageViewType::e2D,
        .format = vk::Format::eR32G32B32A32Sfloat,
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    }));
    slot->width = width;
    slot->height = height;
    slot->written = false;
    slot->layout = vk::ImageLayout::eUndefined;
    slot->used_frame = frame;
    return *slot;
}

void ObjectMotion::Attach(RenderState& state, u32 width, u32 height) {
    constexpr u32 slot = Shader::MotionVectors::Output;
    Target& target = GetTarget(width, height);
    auto& attachment = state.color_attachments[slot];
    attachment = {};
    attachment.image_view = *target.view;
    attachment.image_layout = vk::ImageLayout::eColorAttachmentOptimal;
    if (!target.written || target.layout != vk::ImageLayout::eColorAttachmentOptimal) {
        // Clear once per frame; later reads must not erase the validity of this history.
        scheduler.EndRendering();
        const vk::ImageMemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eColorAttachmentOutput,
            .dstAccessMask = vk::AccessFlagBits2::eColorAttachmentWrite |
                             vk::AccessFlagBits2::eColorAttachmentRead,
            .oldLayout = target.layout,
            .newLayout = vk::ImageLayout::eColorAttachmentOptimal,
            .image = vk::Image(target.image),
            .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
        };
        // Recorded in order on the recording thread: CommandBuffer() here switched the rest of
        // the submission (most of the G-buffer) to direct recording on the GPU thread.
        scheduler.Record([barrier](vk::CommandBuffer cmd) {
            cmd.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
        });
        target.layout = vk::ImageLayout::eColorAttachmentOptimal;
        attachment.is_clear = !target.written;
        target.written = true;
    }
    state.num_color_attachments = std::max<u16>(state.num_color_attachments, slot + 1);
}

vk::ImageView ObjectMotion::PrepareRead(u32 width, u32 height, bool& valid) {
    // The image of this size; otherwise any image (the pass binds one, its vectors unused).
    Target* target = const_cast<Target*>(Find(width, height));
    if (!target) {
        for (auto& candidate : targets) {
            if (candidate.image && (!target || candidate.used_frame > target->used_frame)) {
                target = &candidate;
            }
        }
    }
    if (!target) {
        target = &GetTarget(1, 1);
    }
    valid = target->written && width == target->width && height == target->height;
    const vk::ImageMemoryBarrier2 barrier{
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .dstAccessMask = vk::AccessFlagBits2::eShaderRead,
        .oldLayout = target->layout,
        .newLayout = vk::ImageLayout::eGeneral,
        .image = vk::Image(target->image),
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    };
    scheduler.Record([barrier](vk::CommandBuffer cmdbuf) {
        cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
    });
    target->layout = vk::ImageLayout::eGeneral;
    read_target = target;
    return *target->view;
}

} // namespace Vulkan
