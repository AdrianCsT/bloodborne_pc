// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/renderer_vulkan/vk_frame_generation.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string_view>
#include <utility>

#include "bbport_settings.h"
#include "core/emulator_settings.h"
#include "ffx_vk_portable.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan {

namespace {

constexpr float kCameraFar = 3000.0f;
/// The library's effect calls recycle their image views after 8 calls and the interpolation
/// makes two a frame: the GPU must have finished the frame three back before the next is recorded.
constexpr size_t kFramesInFlight = 3;

FfxVkPortableImage Describe(vk::Image image, vk::Format format, u32 width, u32 height,
                            vk::ImageUsageFlags usage, vk::ImageAspectFlags aspect,
                            FfxVkPortableResourceState state) {
    FfxVkPortableImage out{};
    out.structSize = sizeof(out);
    out.image = image;
    out.format = static_cast<VkFormat>(format);
    out.extent = {width, height};
    out.mipCount = 1;
    out.arrayLayers = 1;
    out.usage = static_cast<VkImageUsageFlags>(usage);
    out.aspect = static_cast<VkImageAspectFlags>(aspect);
    out.state = state;
    return out;
}

/// An optional image that is not there: described but null.
FfxVkPortableImage Absent() {
    FfxVkPortableImage out{};
    out.structSize = sizeof(out);
    return out;
}

void PrintIssues(const char* what, u64 issues) {
    std::printf("Frame generation: %s refused:", what);
    for (u64 bit = 1; bit != 0 && bit <= issues; bit <<= 1) {
        if (issues & bit) {
            std::printf(" %s;", ffxVkPortableValidationIssueName(bit));
        }
    }
    std::printf("\n");
}

vk::ImageAspectFlags DepthAspects(vk::Format format) {
    const bool stencil = format == vk::Format::eD32SfloatS8Uint ||
                         format == vk::Format::eD24UnormS8Uint ||
                         format == vk::Format::eD16UnormS8Uint;
    return vk::ImageAspectFlagBits::eDepth |
           (stencil ? vk::ImageAspectFlagBits::eStencil : vk::ImageAspectFlags{});
}

FfxVkPortableFrameGenerationCreateInfo MakeCreateInfo(u32 width, u32 height, vk::Format source,
                                                      vk::Format output) {
    FfxVkPortableFrameGenerationCreateInfo info{};
    info.structSize = sizeof(info);
    // LDR, jitter-free motion vectors in render resolution pixels, depth 0..1 (near 0).
    info.flags = 0;
    info.maxRenderSize = {width, height};
    info.displaySize = {width, height};
    info.interpolationSourceFormat = static_cast<VkFormat>(source);
    info.outputFormat = static_cast<VkFormat>(output);
    return info;
}

} // namespace

FrameGeneration::FrameGeneration(const Instance& instance_, Scheduler& scheduler_)
    : instance{instance_}, scheduler{scheduler_} {}

FrameGeneration::~FrameGeneration() {
    if (context) {
        ffxVkPortableFrameGenerationContextDestroy(context);
        context = nullptr;
    }
    for (const auto& entry : hudless_views) {
        instance.GetDevice().destroyImageView(entry.view);
    }
}

bool FrameGeneration::Requested() {
    const auto& settings = BbSettings::Get();
    return settings.frame_generation.load(std::memory_order_relaxed) &&
           settings.upscaler.load(std::memory_order_relaxed) != BbSettings::UpscalerOff;
}

// ---- Capture --------------------------------------------------------------------------------

void FrameGeneration::EnsureDepthCopy(vk::Format format, u32 width, u32 height) {
    if (depth_copy && depth_copy_format == format && depth_copy_width >= width &&
        depth_copy_height >= height) {
        return;
    }
    // Earlier frames may still read the old one.
    scheduler.DeferOperation([old = std::move(depth_copy)] {});
    const auto device = instance.GetDevice();
    depth_copy = VideoCore::UniqueImage(device, instance.GetAllocator());
    depth_copy.Create(vk::ImageCreateInfo{
        .imageType = vk::ImageType::e2D,
        .format = format,
        .extent = {width, height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = vk::SampleCountFlagBits::e1,
        .tiling = vk::ImageTiling::eOptimal,
        .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferDst,
        .initialLayout = vk::ImageLayout::eUndefined,
    });
    depth_copy_format = format;
    depth_copy_width = width;
    depth_copy_height = height;
    // The interpolation context holds no reference to the image between frames.
}

void FrameGeneration::EnsureHudless(vk::Format format, u32 width, u32 height) {
    if (hudless_raw && hudless_format == format && hudless_width == width &&
        hudless_height == height) {
        return;
    }
    const auto device = instance.GetDevice();
    for (const auto& entry : hudless_views) {
        scheduler.DeferOperation([device, view = entry.view] { device.destroyImageView(view); });
    }
    hudless_views.clear();
    scheduler.DeferOperation([old = std::move(hudless_raw)] {});
    hudless_raw = VideoCore::UniqueImage(device, instance.GetAllocator());
    hudless_raw.Create(vk::ImageCreateInfo{
        // The host passes read it through the display buffer's view format (sRGB or not).
        .flags = vk::ImageCreateFlagBits::eMutableFormat,
        .imageType = vk::ImageType::e2D,
        .format = format,
        .extent = {width, height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = vk::SampleCountFlagBits::e1,
        .tiling = vk::ImageTiling::eOptimal,
        .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferDst,
        .initialLayout = vk::ImageLayout::eUndefined,
    });
    hudless_format = format;
    hudless_width = width;
    hudless_height = height;
}

void FrameGeneration::CaptureScene(const Scene& scene) {
    pending = {};
    if (!Requested() || !scene.depth || !scene.motion) {
        return;
    }
    EnsureDepthCopy(scene.depth_format, scene.width, scene.height);
    const vk::ImageAspectFlags all_aspects = DepthAspects(scene.depth_format);
    scheduler.Record([src = scene.depth, layout = scene.depth_layout, dst = vk::Image(depth_copy),
                      w = scene.width, h = scene.height,
                      all_aspects](vk::CommandBuffer cmd) {
        const vk::MemoryBarrier2 source_ready{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
        };
        // Whatever read the previous frame's copy (the interpolation) is done before it is
        // overwritten.
        vk::ImageMemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .oldLayout = vk::ImageLayout::eUndefined,
            .newLayout = vk::ImageLayout::eTransferDstOptimal,
            .image = dst,
            .subresourceRange = {all_aspects, 0, 1, 0, 1},
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1,
                              .pMemoryBarriers = &source_ready,
                              .imageMemoryBarrierCount = 1,
                              .pImageMemoryBarriers = &barrier});
        const vk::ImageCopy region{
            .srcSubresource = {vk::ImageAspectFlagBits::eDepth, 0, 0, 1},
            .dstSubresource = {vk::ImageAspectFlagBits::eDepth, 0, 0, 1},
            .extent = {w, h, 1},
        };
        cmd.copyImage(src, layout, dst, vk::ImageLayout::eTransferDstOptimal, region);
        barrier.srcStageMask = vk::PipelineStageFlagBits2::eTransfer;
        barrier.srcAccessMask = vk::AccessFlagBits2::eTransferWrite;
        barrier.dstStageMask = vk::PipelineStageFlagBits2::eAllCommands;
        barrier.dstAccessMask = vk::AccessFlagBits2::eMemoryRead;
        barrier.oldLayout = vk::ImageLayout::eTransferDstOptimal;
        barrier.newLayout = vk::ImageLayout::eGeneral;
        cmd.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
    });
    pending.valid = true;
    pending.motion = scene.motion;
    pending.width = scene.width;
    pending.height = scene.height;
    pending.depth_format = scene.depth_format;
    pending.jitter = scene.jitter;
    pending.frame_ms = scene.frame_ms;
    pending.camera_near = scene.camera_near;
    pending.camera_fov = scene.camera_fov;
    pending.reset = scene.reset;
}

void FrameGeneration::CaptureHudless(vk::Image image, vk::ImageLayout layout, vk::Format format,
                                     u32 width, u32 height) {
    if (!WantsHudless()) {
        return;
    }
    EnsureHudless(format, width, height);
    scheduler.Record([src = image, layout, dst = vk::Image(hudless_raw), width,
                      height](vk::CommandBuffer cmd) {
        constexpr vk::ImageSubresourceRange color{vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1};
        const vk::MemoryBarrier2 source_ready{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
        };
        vk::ImageMemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eTransfer,
            .dstAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .oldLayout = vk::ImageLayout::eUndefined,
            .newLayout = vk::ImageLayout::eTransferDstOptimal,
            .image = dst,
            .subresourceRange = color,
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1,
                              .pMemoryBarriers = &source_ready,
                              .imageMemoryBarrierCount = 1,
                              .pImageMemoryBarriers = &barrier});
        const vk::ImageCopy region{
            .srcSubresource = {vk::ImageAspectFlagBits::eColor, 0, 0, 1},
            .dstSubresource = {vk::ImageAspectFlagBits::eColor, 0, 0, 1},
            .extent = {width, height, 1},
        };
        cmd.copyImage(src, layout, dst, vk::ImageLayout::eTransferDstOptimal, region);
        // The host passes sample it next.
        barrier.srcStageMask = vk::PipelineStageFlagBits2::eTransfer;
        barrier.srcAccessMask = vk::AccessFlagBits2::eTransferWrite;
        barrier.dstStageMask = vk::PipelineStageFlagBits2::eAllCommands;
        barrier.dstAccessMask = vk::AccessFlagBits2::eMemoryRead;
        barrier.oldLayout = vk::ImageLayout::eTransferDstOptimal;
        barrier.newLayout = vk::ImageLayout::eShaderReadOnlyOptimal;
        cmd.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
    });
    pending.hudless = true;
    pending.hudless_width = width;
    pending.hudless_height = height;
}

// ---- Context --------------------------------------------------------------------------------

void FrameGeneration::Report(std::string_view state) {
    if (state == reported) {
        return;
    }
    reported = state;
    std::printf("Frame generation: %.*s\n", int(state.size()), state.data());
}

bool FrameGeneration::CreateContext(u32 width, u32 height, vk::Format format) {
    const auto physical = instance.GetPhysicalDevice();
    FfxVkPortableDeviceCapabilities capabilities{};
    capabilities.structSize = sizeof(capabilities);
    if (ffxVkPortableQueryDeviceCapabilities(physical, &capabilities) != FFX_VK_PORTABLE_OK ||
        !capabilities.fsr3FrameGenerationPrerequisites) {
        Report("off (the GPU lacks what the FSR 3.1 interpolation needs)");
        return false;
    }
    // The interpolated frame is written by a compute shader: storage support in the frame's
    // format, else RGBA8 (the swapchain blit converts).
    const auto storage = [&](vk::Format candidate) {
        const auto features = physical.getFormatProperties(candidate).optimalTilingFeatures;
        return (features & vk::FormatFeatureFlagBits::eStorageImage) &&
               (features & vk::FormatFeatureFlagBits::eSampledImage) &&
               (features & vk::FormatFeatureFlagBits::eTransferSrc);
    };
    output_format = storage(format) ? format : vk::Format::eR8G8B8A8Unorm;
    if (!storage(output_format)) {
        Report("off (no storage image format for the interpolated frame)");
        return false;
    }

    FfxVkPortableDeviceInfo device_info{};
    device_info.structSize = sizeof(device_info);
    device_info.instance = instance.GetInstance();
    device_info.physicalDevice = physical;
    device_info.device = instance.GetDevice();
    device_info.getDeviceProcAddr = VULKAN_HPP_DEFAULT_DISPATCHER.vkGetDeviceProcAddr;
    device_info.queue = instance.GetGraphicsQueue();
    device_info.queueFamilyIndex = instance.GetGraphicsQueueFamilyIndex();
    device_info.shaderFloat16Enabled = instance.IsShaderFloat16Enabled();
    device_info.subgroupSizeControlEnabled = instance.IsSubgroupSizeControlEnabled();
    device_info.synchronization2Enabled = VK_TRUE;
    device_info.shaderStorageImageWriteWithoutFormatEnabled = VK_TRUE;

    const auto create_info = MakeCreateInfo(width, height, format, output_format);
    if (const u64 issues = ffxVkPortableValidateFrameGenerationCreateInfo(&create_info)) {
        PrintIssues("create info", issues);
        return false;
    }
    const auto result = ffxVkPortableFrameGenerationContextCreate(&device_info, &create_info,
                                                                 &context);
    if (result != FFX_VK_PORTABLE_OK) {
        context = nullptr;
        std::printf("Frame generation: off (the FSR 3.1 interpolation context could not be "
                    "created, error %d)\n", int(result));
        reported = "failed";
        return false;
    }
    context_width = width;
    context_height = height;
    context_format = format;
    std::printf("Frame generation: on (FSR 3.1 interpolation, %ux%u, interpolated frame %s, "
                "optical flow)\n", width, height, vk::to_string(output_format).c_str());
    reported = "on";
    return true;
}

void FrameGeneration::DestroyContext() {
    if (!context) {
        return;
    }
    // Submitted work may still use the context's images.
    scheduler.Finish();
    ffxVkPortableFrameGenerationContextDestroy(context);
    context = nullptr;
    ticks.clear();
}

bool FrameGeneration::Idle() {
    const float refresh = refresh_hz.load(std::memory_order_relaxed);
    const float interval = base_interval_ms.load(std::memory_order_relaxed);
    if (refresh <= 0.0f || interval <= 0.0f) {
        return false; // refresh rate unknown: no rule
    }
    const float base_fps = 1000.0f / interval;
    const float half = refresh * 0.5f;
    // A frame cap at or below half the refresh rate leaves a refresh slot for every generated
    // frame, so there is nothing to pause for (the cap itself bounds the base rate).
    const u32 cap = EmulatorSettings.GetFrameLimit();
    if (cap > 0 && float(cap) <= half * 1.02f) {
        idle = false;
        if (!cap_reported) {
            cap_reported = true;
            std::printf("Frame generation: frame cap %u FPS fits half the refresh rate (%.0f Hz): "
                        "always on\n", cap, refresh);
        }
        return false;
    }
    if (!idle && base_fps > 0.9f * half) {
        idle = true;
        std::printf("Frame generation: idle, base FPS above half the refresh rate (%.0f FPS, "
                    "%.0f Hz)\n", base_fps, refresh);
    } else if (idle && base_fps < 0.8f * half) {
        idle = false;
        std::printf("Frame generation: active again (%.0f FPS, %.0f Hz)\n", base_fps, refresh);
    }
    return idle;
}

FrameGeneration::Plan FrameGeneration::PlanFrame(u32 width, u32 height, vk::Format format,
                                                 bool hdr, vk::Extent2D display) {
    Plan plan;
    current = std::exchange(pending, Pending{});
    const bool switched_on = BbSettings::Get().frame_generation.load(std::memory_order_relaxed);
    if (!switched_on || !Requested()) {
        if (was_requested) {
            was_requested = false;
            DestroyContext();
            failed = false;
            idle = false;
            history_broken = true;
        }
        if (switched_on) {
            Report("off (no temporal upscaler selected)");
        } else {
            reported.clear();
        }
        return plan;
    }
    if (!was_requested) {
        was_requested = true;
        failed = false;
        history_broken = true;
    }
    if (failed) {
        skipped.fetch_add(1, std::memory_order_relaxed);
        return plan;
    }
    if (hdr) {
        Report("off (HDR output)");
        history_broken = true;
        return plan;
    }
    if (!current.valid) {
        // Menus, loading screens, movies: no scene to interpolate.
        history_broken = true;
        skipped.fetch_add(1, std::memory_order_relaxed);
        return plan;
    }
    if (current.width > width || current.height > height) {
        Report("off (the scene is rendered larger than the window)");
        history_broken = true;
        skipped.fetch_add(1, std::memory_order_relaxed);
        return plan;
    }
    if (!context || context_width != width || context_height != height ||
        context_format != format) {
        if (context) {
            DestroyContext();
        }
        if (!CreateContext(width, height, format)) {
            failed = true;
            return plan;
        }
        history_broken = true;
    } else if (reported != "on") {
        Report("on");
    }
    if (Idle()) {
        history_broken = true;
        skipped.fetch_add(1, std::memory_order_relaxed);
        return plan;
    }
    while (ticks.size() >= kFramesInFlight) {
        scheduler.Wait(ticks.front());
        ticks.pop_front();
    }
    plan.generate = true;
    reset_frame = current.reset || history_broken;
    history_broken = false;
    plan.present = !reset_frame;
    if (!plan.present) {
        skipped.fetch_add(1, std::memory_order_relaxed);
    }
    plan.hudless = current.hudless && hudless_raw && current.hudless_width == display.width &&
                   current.hudless_height == display.height;
    return plan;
}

FrameGeneration::Hudless FrameGeneration::HudlessView(vk::Format view_format) {
    for (const auto& entry : hudless_views) {
        if (entry.format == view_format) {
            return {entry.view, {hudless_width, hudless_height}};
        }
    }
    const vk::ImageView view = Check(instance.GetDevice().createImageView({
        .image = vk::Image(hudless_raw),
        .viewType = vk::ImageViewType::e2D,
        .format = view_format,
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    }));
    hudless_views.push_back({view_format, view});
    return {view, {hudless_width, hudless_height}};
}

bool FrameGeneration::Record(vk::CommandBuffer cmdbuf, const Targets& targets) {
    const auto all = vk::PipelineStageFlagBits2::eAllCommands;
    const bool hudless = bool(targets.hudless);
    // The host passes of the frame (and the last interpolation's reads) come first.
    const vk::MemoryBarrier2 before{
        .srcStageMask = all,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite | vk::AccessFlagBits2::eMemoryRead,
        .dstStageMask = all,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryWrite | vk::AccessFlagBits2::eMemoryRead,
    };
    const vk::ImageMemoryBarrier2 output_to_general{
        .srcStageMask = all,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = all,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .oldLayout = vk::ImageLayout::eUndefined,
        .newLayout = vk::ImageLayout::eGeneral,
        .image = targets.output,
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    };
    cmdbuf.pipelineBarrier2({.memoryBarrierCount = 1,
                             .pMemoryBarriers = &before,
                             .imageMemoryBarrierCount = 1,
                             .pImageMemoryBarriers = &output_to_general});

    const auto sampled = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferSrc;
    const auto create_info = MakeCreateInfo(context_width, context_height, context_format,
                                            output_format);
    const FfxVkPortableImage current_color =
        Describe(targets.current, targets.format, targets.width, targets.height, sampled,
                 vk::ImageAspectFlagBits::eColor, FFX_VK_PORTABLE_RESOURCE_STATE_GENERIC_READ);
    const FfxVkPortableImage hudless_color =
        hudless ? Describe(targets.hudless, targets.format, targets.width, targets.height,
                           sampled, vk::ImageAspectFlagBits::eColor,
                           FFX_VK_PORTABLE_RESOURCE_STATE_GENERIC_READ)
                : Absent();
    const FfxVkPortableImage& source = hudless ? hudless_color : current_color;

    FfxVkPortableFrameGenerationPrepareInfo prepare{};
    prepare.structSize = sizeof(prepare);
    prepare.commandBuffer = cmdbuf;
    prepare.depth = Describe(vk::Image(depth_copy), depth_copy_format, depth_copy_width,
                             depth_copy_height,
                             vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferDst,
                             vk::ImageAspectFlagBits::eDepth,
                             FFX_VK_PORTABLE_RESOURCE_STATE_GENERIC_READ);
    prepare.motionVectors = Describe(current.motion, vk::Format::eR16G16Sfloat, current.width,
                                     current.height,
                                     vk::ImageUsageFlagBits::eStorage |
                                         vk::ImageUsageFlagBits::eSampled,
                                     vk::ImageAspectFlagBits::eColor,
                                     FFX_VK_PORTABLE_RESOURCE_STATE_GENERIC_READ);
    prepare.renderSize = {current.width, current.height};
    prepare.jitterOffset = {current.jitter[0], current.jitter[1]};
    prepare.motionVectorScale = {1.0f, 1.0f};
    prepare.frameTimeMilliseconds = current.frame_ms;
    prepare.cameraNear = current.camera_near;
    prepare.cameraFar = kCameraFar;
    prepare.cameraVerticalFovRadians = current.camera_fov;
    prepare.viewSpaceToMeters = 1.0f;
    prepare.minLuminance = 0.0f;
    prepare.maxLuminance = 1.0f;
    prepare.transferFunction = FFX_VK_PORTABLE_TRANSFER_FUNCTION_SRGB;
    // The library (1.1.4) only checks that the camera is given; the port tracks no basis.
    prepare.cameraUp = {0.0f, 1.0f, 0.0f};
    prepare.cameraRight = {1.0f, 0.0f, 0.0f};
    prepare.cameraForward = {0.0f, 0.0f, 1.0f};
    prepare.reset = reset_frame ? VK_TRUE : VK_FALSE;
    prepare.frameId = frame_id;

    FfxVkPortableFrameGenerationDispatchInfo dispatch{};
    dispatch.structSize = sizeof(dispatch);
    dispatch.commandBuffer = cmdbuf;
    dispatch.currentColor = current_color;
    dispatch.hudlessColor = hudless_color;
    dispatch.distortionField = Absent();
    dispatch.output = Describe(targets.output, output_format, targets.width, targets.height,
                               vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eSampled |
                                   vk::ImageUsageFlagBits::eTransferSrc,
                               vk::ImageAspectFlagBits::eColor,
                               FFX_VK_PORTABLE_RESOURCE_STATE_UNORDERED_ACCESS);
    dispatch.displaySize = {targets.width, targets.height};
    dispatch.interpolationRect = {0, 0, targets.width, targets.height};
    dispatch.frameTimeMilliseconds = current.frame_ms;
    dispatch.cameraNear = current.camera_near;
    dispatch.cameraFar = kCameraFar;
    dispatch.cameraVerticalFovRadians = current.camera_fov;
    dispatch.viewSpaceToMeters = 1.0f;
    dispatch.minLuminance = 0.0f;
    dispatch.maxLuminance = 1.0f;
    dispatch.transferFunction = FFX_VK_PORTABLE_TRANSFER_FUNCTION_SRGB;
    dispatch.reset = reset_frame ? VK_TRUE : VK_FALSE;
    dispatch.frameId = frame_id;

    auto refused = [&](const char* what, u64 issues) {
        PrintIssues(what, issues);
        failed = true;
        return false;
    };
    // The command buffer is real here: the checks see the same structs the library gets.
    if (const u64 issues = ffxVkPortableValidateFrameGenerationPrepareInfo(&create_info, &prepare)) {
        return refused("prepare", issues);
    }
    if (const u64 issues = ffxVkPortableValidateFrameGenerationDispatchInfo(&create_info, &dispatch)) {
        return refused("dispatch", issues);
    }
    if (const auto result = ffxVkPortableFrameGenerationContextPrepare(context, &prepare, &source);
        result != FFX_VK_PORTABLE_OK) {
        std::printf("Frame generation: off (optical flow failed, error %d)\n", int(result));
        failed = true;
        return false;
    }
    if (const auto result = ffxVkPortableFrameGenerationContextRecordDispatch(context, &dispatch);
        result != FFX_VK_PORTABLE_OK) {
        std::printf("Frame generation: off (interpolation failed, error %d)\n", int(result));
        failed = true;
        return false;
    }
    ++frame_id;
    const vk::MemoryBarrier2 after{
        .srcStageMask = all,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = all,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
    };
    cmdbuf.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &after});
    return true;
}

void FrameGeneration::EndFrame(u64 submission_tick) {
    ticks.push_back(submission_tick);
}

// ---- Pacing and statistics (swap thread) ----------------------------------------------------

float FrameGeneration::OnPresent(bool generated) {
    const auto now = std::chrono::steady_clock::now();
    if (last_present.time_since_epoch().count() != 0) {
        const float ms = std::chrono::duration<float, std::milli>(now - last_present).count();
        // A frame of a menu or a hitch is not the rate the interpolated frame is placed by.
        const float clamped = std::clamp(ms, 1.0f, 33.0f);
        const float previous = base_interval_ms.load(std::memory_order_relaxed);
        base_interval_ms.store(previous > 0.0f ? previous * 0.9f + clamped * 0.1f : clamped,
                               std::memory_order_relaxed);
    }
    last_present = now;
    ++stats_base;
    ++overlay_base;
    return generated ? std::clamp(base_interval_ms.load(std::memory_order_relaxed), 1.0f, 33.0f) *
                           0.5f
                     : 0.0f;
}

void FrameGeneration::PrintStats(std::chrono::steady_clock::time_point now) {
    static const bool stats = EmulatorSettingsImpl::Flag("BB_FRAME_STATS", false);
    const double seconds = std::chrono::duration<double>(now - stats_start).count();
    if (!stats || seconds < 5.0) {
        return;
    }
    if (BbSettings::Get().frame_generation.load(std::memory_order_relaxed)) {
        std::printf("Frame generation: %.1f generated/s, %.1f presented/s, base %.1f FPS, %u "
                    "skipped\n",
                    stats_generated / seconds, stats_images / seconds, stats_base / seconds,
                    skipped.exchange(0, std::memory_order_relaxed));
    }
    stats_start = now;
    stats_base = stats_images = stats_generated = 0;
}

void FrameGeneration::OnSwitchedOff() {
    auto& settings = BbSettings::Get();
    if (settings.fg_active.load(std::memory_order_relaxed)) {
        settings.fg_active.store(false, std::memory_order_relaxed);
        overlay_base = overlay_images = 0;
        overlay_start = {};
    }
}

void FrameGeneration::OnImagePresented(bool generated) {
    const auto now = std::chrono::steady_clock::now();
    if (stats_start.time_since_epoch().count() == 0) {
        stats_start = overlay_start = now;
    }
    ++stats_images;
    ++overlay_images;
    stats_generated += generated;
    PrintStats(now);
    // The overlay's "60 -> 120 FPS FG", twice a second.
    const double seconds = std::chrono::duration<double>(now - overlay_start).count();
    if (seconds >= 0.5) {
        auto& settings = BbSettings::Get();
        const bool on = settings.frame_generation.load(std::memory_order_relaxed) &&
                        overlay_images > overlay_base + overlay_base / 4;
        settings.fg_active.store(on, std::memory_order_relaxed);
        settings.fg_base_fps.store(float(overlay_base / seconds), std::memory_order_relaxed);
        settings.fg_presented_fps.store(float(overlay_images / seconds), std::memory_order_relaxed);
        overlay_start = now;
        overlay_base = overlay_images = 0;
    }
}

} // namespace Vulkan
