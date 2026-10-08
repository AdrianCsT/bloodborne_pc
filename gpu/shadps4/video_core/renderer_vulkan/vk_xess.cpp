// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <optional>
#include <string_view>
#include <type_traits>

#include "video_core/renderer_vulkan/vk_xess.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace Vulkan {

#ifdef _WIN32

namespace {

// The ABI of libxess.dll (inc/xess/xess.h and xess_vk.h, SDK 3.0.2, MIT, structures packed to 8).
using XessResult = int;
using XessContext = struct XessContextTag*;
constexpr XessResult XessSuccess = 0;
constexpr u32 XessQualityUltraPerformance = 100;
constexpr u32 XessQualityAa = 106;
constexpr u32 XessInitLdrInputColor = 1u << 6;
constexpr u32 XessInitAutoExposure = 1u << 8;

struct XessVersion {
    u16 major, minor, patch, reserved;
};
struct Xess2d {
    u32 x, y;
};
struct XessImage {
    VkImageView image_view;
    VkImage image;
    VkImageSubresourceRange range;
    VkFormat format;
    u32 width, height;
};
struct XessExecuteParams {
    XessImage color, velocity, depth, exposure, responsive_mask, output;
    float jitter_x, jitter_y, exposure_scale;
    u32 reset_history, input_width, input_height;
    Xess2d color_base, velocity_base, depth_base, responsive_mask_base, reserved, output_base;
};
struct XessInitParams {
    Xess2d output_resolution;
    u32 quality, init_flags, creation_node_mask, visible_node_mask;
    VkDeviceMemory temp_buffer_heap;
    u64 buffer_heap_offset;
    VkDeviceMemory temp_texture_heap;
    u64 texture_heap_offset;
    VkPipelineCache pipeline_cache;
};
static_assert(sizeof(XessImage) == 48 && sizeof(XessExecuteParams) == 360 &&
              sizeof(XessInitParams) == 64);

using LogCallback = void (*)(const char* message, int level);

struct Api {
    XessResult (*GetVersion)(XessVersion*);
    XessResult (*VKGetRequiredInstanceExtensions)(u32*, const char* const**, u32*);
    XessResult (*VKGetRequiredDeviceExtensions)(VkInstance, VkPhysicalDevice, u32*,
                                                const char* const**);
    XessResult (*VKGetRequiredDeviceFeatures)(VkInstance, VkPhysicalDevice, void**);
    XessResult (*VKCreateContext)(VkInstance, VkPhysicalDevice, VkDevice, XessContext*);
    XessResult (*VKInit)(XessContext, const XessInitParams*);
    XessResult (*VKExecute)(XessContext, VkCommandBuffer, const XessExecuteParams*);
    XessResult (*DestroyContext)(XessContext);
    XessResult (*GetOptimalInputResolution)(XessContext, const Xess2d*, u32, Xess2d*, Xess2d*, Xess2d*);
    XessResult (*SetLoggingCallback)(XessContext, int, LogCallback);
    XessResult (*IsOptimalDriver)(XessContext);
};

constexpr wchar_t LibraryName[] = L"libxess.dll";

void XessLog(const char* message, int level) {
    static constexpr const char* names[] = {"debug", "info", "warning", "error"};
    std::printf("XeSS: %s: %s\n", names[std::clamp(level, 0, 3)], message);
}

/// The XeSS quality preset whose render scale is nearest to `scale` (output / render size).
u32 QualityFor(float scale) {
    static constexpr std::array<float, 7> ratios = {3.0f, 2.3f, 2.0f, 1.7f, 1.5f, 1.3f, 1.0f};
    u32 best = XessQualityUltraPerformance;
    float distance = 1e9f;
    for (u32 i = 0; i < ratios.size(); ++i) {
        const float d = std::fabs(ratios[i] - scale);
        if (d < distance) {
            distance = d;
            best = XessQualityUltraPerformance + i;
        }
    }
    return best;
}

const char* QualityName(u32 quality) {
    static constexpr std::array<const char*, 7> names = {"Ultra Performance", "Performance", "Balanced",
                                                         "Quality", "Ultra Quality", "Ultra Quality Plus",
                                                         "Native AA"};
    return names[std::min<u32>(quality - XessQualityUltraPerformance, 6)];
}

std::filesystem::path ExecutableDirectory() {
    std::wstring path(MAX_PATH, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), DWORD(path.size()));
    path.resize(length);
    return std::filesystem::path{path}.parent_path();
}
} // namespace

struct Xess::Impl {
    HMODULE module{};
    Api api{};
    XessContext context{};
    XessVersion version{};
    std::vector<std::string> device_extension_names;
    std::optional<FeatureDesc> feature;
    std::string problem;
    bool eligible{true}, device_ready{}, available{};

    void Disable(std::string_view reason) {
        eligible = available = false;
        problem = reason;
        std::printf("XeSS: unavailable (%.*s)\n", int(reason.size()), reason.data());
    }
};

Xess* Xess::Get() {
    static Xess* const xess = []() -> Xess* {
        const char* setting = std::getenv("BB_XESS");
        if (setting && setting[0] == '0') {
            return nullptr;
        }
        if (!std::filesystem::is_regular_file(ExecutableDirectory() / LibraryName)) {
            return nullptr;
        }
        auto* created = new Xess;
        std::atexit([] {
            if (Xess* live = Get()) {
                live->Shutdown();
            }
        });
        return created;
    }();
    return xess;
}

Xess::Xess() : impl{std::make_unique<Impl>()} {
    impl->module = LoadLibraryW((ExecutableDirectory() / LibraryName).c_str());
    if (!impl->module) {
        impl->Disable("libxess.dll cannot be loaded");
        return;
    }
    bool complete = true;
    const auto load = [&](auto& function, const char* name) {
        function = reinterpret_cast<std::remove_reference_t<decltype(function)>>(
            GetProcAddress(impl->module, name));
        complete = complete && function;
    };
    Api& api = impl->api;
    load(api.GetVersion, "xessGetVersion");
    load(api.VKGetRequiredInstanceExtensions, "xessVKGetRequiredInstanceExtensions");
    load(api.VKGetRequiredDeviceExtensions, "xessVKGetRequiredDeviceExtensions");
    load(api.VKGetRequiredDeviceFeatures, "xessVKGetRequiredDeviceFeatures");
    load(api.VKCreateContext, "xessVKCreateContext");
    load(api.VKInit, "xessVKInit");
    load(api.VKExecute, "xessVKExecute");
    load(api.DestroyContext, "xessDestroyContext");
    load(api.GetOptimalInputResolution, "xessGetOptimalInputResolution");
    load(api.SetLoggingCallback, "xessSetLoggingCallback");
    load(api.IsOptimalDriver, "xessIsOptimalDriver");
    if (!complete) {
        impl->Disable("libxess.dll is from another version");
        return;
    }
    api.GetVersion(&impl->version);
}

Xess::~Xess() {
    Shutdown();
}

void Xess::AppendDeviceExtensions(vk::Instance instance, vk::PhysicalDevice physical,
                                  std::vector<const char*>& enabled) {
    if (!impl->eligible) {
        return;
    }
    // XeSS 3.0.2 asks for no instance extension and Vulkan 1.3, which the renderer's instance
    // already is; a newer library asking for more would need vk_platform.cpp to enable them.
    u32 instance_count{}, api_version{};
    const char* const* instance_names{};
    if (impl->api.VKGetRequiredInstanceExtensions(&instance_count, &instance_names, &api_version) < 0 ||
        instance_count != 0 || api_version > VK_API_VERSION_1_3) {
        return impl->Disable("this libxess.dll needs Vulkan instance features this build does not enable");
    }
    u32 count{};
    const char* const* names{};
    const XessResult result = impl->api.VKGetRequiredDeviceExtensions(
        static_cast<VkInstance>(instance), static_cast<VkPhysicalDevice>(physical), &count, &names);
    if (result < 0) {
        return impl->Disable(result == -1 || result == -2
                                 ? "this GPU or driver does not support XeSS (DP4a needed)"
                                 : "XeSS device query failed");
    }
    const auto [listed, available] = physical.enumerateDeviceExtensionProperties();
    for (u32 i = 0; i < count; ++i) {
        if (listed != vk::Result::eSuccess ||
            std::ranges::none_of(available, [&](const auto& e) {
                return std::strcmp(e.extensionName.data(), names[i]) == 0;
            })) {
            return impl->Disable("a Vulkan extension XeSS needs is missing");
        }
    }
    impl->device_extension_names.assign(names, names + count);
    for (const auto& name : impl->device_extension_names) {
        if (std::ranges::none_of(enabled, [&](const char* e) { return name == e; })) {
            enabled.push_back(name.c_str());
        }
    }
    impl->device_ready = true;
}

const void* Xess::PatchDeviceFeatures(vk::Instance instance, vk::PhysicalDevice physical,
                                      const void* chain) {
    if (!impl->eligible || !impl->device_ready) {
        return chain;
    }
    void* patched = const_cast<void*>(chain);
    if (impl->api.VKGetRequiredDeviceFeatures(static_cast<VkInstance>(instance),
                                              static_cast<VkPhysicalDevice>(physical),
                                              &patched) < 0) {
        impl->Disable("the GPU lacks Vulkan features XeSS needs");
        return chain;
    }
    return patched;
}

void Xess::Initialize(vk::Instance instance, vk::PhysicalDevice physical, vk::Device device) {
    if (!impl->eligible || !impl->device_ready || impl->context) {
        return;
    }
    const XessResult result =
        impl->api.VKCreateContext(static_cast<VkInstance>(instance),
                                  static_cast<VkPhysicalDevice>(physical),
                                  static_cast<VkDevice>(device), &impl->context);
    if (result < 0 || !impl->context) {
        impl->context = nullptr;
        return impl->Disable(result == -2 ? "XeSS does not support this GPU driver (update it)"
                                          : "XeSS context creation failed");
    }
    impl->api.SetLoggingCallback(impl->context, 1, XessLog);
    impl->available = true;
    std::printf("XeSS: ready, libxess %u.%u.%u%s\n", impl->version.major, impl->version.minor,
                impl->version.patch,
                impl->api.IsOptimalDriver(impl->context) == XessSuccess ? ""
                                                                        : " (the GPU driver is not the newest XeSS knows)");
}

bool Xess::Available() const {
    return impl->available;
}

std::string Xess::Problem() const {
    return impl->problem;
}

bool Xess::HasFeature(const FeatureDesc& desc) const {
    return impl->feature && *impl->feature == desc;
}

bool Xess::CreateFeature(const FeatureDesc& desc) {
    if (!impl->available) {
        return false;
    }
    impl->feature.reset();
    const Xess2d output{desc.output_width, desc.output_height};
    const u32 quality = QualityFor(float(desc.output_width) / float(desc.input_width));
    Xess2d optimal{}, minimum{}, maximum{};
    if (impl->api.GetOptimalInputResolution(impl->context, &output, quality, &optimal, &minimum,
                                            &maximum) == XessSuccess &&
        (desc.input_width < minimum.x || desc.input_width > maximum.x ||
         desc.input_height < minimum.y || desc.input_height > maximum.y)) {
        std::printf("XeSS: warning: render size %ux%u is outside the %ux%u to %ux%u range of quality %u\n",
                    desc.input_width, desc.input_height, minimum.x, minimum.y, maximum.x, maximum.y,
                    quality);
    }
    XessInitParams params{};
    params.output_resolution = output;
    params.quality = quality;
    // Linear HDR scene color: XeSS tonemaps and exposes it itself. The game's finished frame is LDR.
    params.init_flags = desc.hdr ? XessInitAutoExposure : XessInitLdrInputColor;
    const auto start = std::chrono::steady_clock::now();
    const XessResult result = impl->api.VKInit(impl->context, &params);
    if (result < 0) {
        std::printf("XeSS: initialization failed (code %d)\n", result);
        return false;
    }
    std::printf("XeSS: initialized %ux%u -> %ux%u, quality %s (scale %.2f), %s input, %.0f ms\n",
                desc.input_width, desc.input_height, desc.output_width, desc.output_height,
                QualityName(quality),
                float(desc.output_width) / float(desc.input_width), desc.hdr ? "HDR" : "LDR",
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
    impl->feature = desc;
    return true;
}

bool Xess::Evaluate(vk::CommandBuffer command, const Frame& frame) {
    if (!impl->available || !impl->feature) {
        return false;
    }
    // XeSS reads its inputs as shader-read-only images; the port keeps them in General.
    const std::array<const Resource*, 3> inputs{&frame.color, &frame.depth, &frame.motion};
    const auto transition = [&](bool to_read_only) {
        std::array<vk::ImageMemoryBarrier2, 3> barriers;
        for (size_t i = 0; i < inputs.size(); ++i) {
            barriers[i] = vk::ImageMemoryBarrier2{
                .srcStageMask = to_read_only ? vk::PipelineStageFlagBits2::eAllCommands
                                             : vk::PipelineStageFlagBits2::eComputeShader,
                .srcAccessMask = to_read_only ? vk::AccessFlagBits2::eMemoryWrite
                                              : vk::AccessFlagBits2::eShaderRead,
                .dstStageMask = to_read_only ? vk::PipelineStageFlagBits2::eComputeShader
                                             : vk::PipelineStageFlagBits2::eAllCommands,
                .dstAccessMask = to_read_only ? vk::AccessFlagBits2::eShaderRead
                                              : vk::AccessFlagBits2::eMemoryRead |
                                                    vk::AccessFlagBits2::eMemoryWrite,
                .oldLayout = to_read_only ? vk::ImageLayout::eGeneral
                                          : vk::ImageLayout::eShaderReadOnlyOptimal,
                .newLayout = to_read_only ? vk::ImageLayout::eShaderReadOnlyOptimal
                                          : vk::ImageLayout::eGeneral,
                .image = inputs[i]->image,
                .subresourceRange = {inputs[i]->aspect, 0, 1, 0, 1},
            };
        }
        command.pipelineBarrier2({.imageMemoryBarrierCount = u32(barriers.size()),
                                  .pImageMemoryBarriers = barriers.data()});
    };
    const auto image = [](const Resource& r) {
        return XessImage{r.view,
                         r.image,
                         {static_cast<VkImageAspectFlags>(r.aspect), 0, 1, 0, 1},
                         static_cast<VkFormat>(r.format),
                         r.width,
                         r.height};
    };
    XessExecuteParams params{};
    params.color = image(frame.color);
    params.velocity = image(frame.motion);
    params.depth = image(frame.depth);
    params.output = image(frame.output);
    params.jitter_x = frame.jitter_x;
    params.jitter_y = frame.jitter_y;
    params.exposure_scale = 1.0f;
    params.reset_history = frame.reset ? 1 : 0;
    params.input_width = impl->feature->input_width;
    params.input_height = impl->feature->input_height;
    transition(true);
    const XessResult result =
        impl->api.VKExecute(impl->context, static_cast<VkCommandBuffer>(command), &params);
    transition(false);
    if (result < 0) {
        std::printf("XeSS: execute failed (code %d)\n", result);
    }
    return result >= 0;
}

void Xess::ReleaseFeature() {
    // The context stays: xessVKInit reconfigures it for the next sizes.
    impl->feature.reset();
}

void Xess::Shutdown() {
    impl->available = false;
    impl->feature.reset();
    if (impl->context) {
        impl->api.DestroyContext(impl->context);
        impl->context = nullptr;
    }
}

#else

struct Xess::Impl {};
Xess::Xess() = default;
Xess::~Xess() = default;
Xess* Xess::Get() {
    return nullptr;
}
void Xess::AppendDeviceExtensions(vk::Instance, vk::PhysicalDevice, std::vector<const char*>&) {}
const void* Xess::PatchDeviceFeatures(vk::Instance, vk::PhysicalDevice, const void* chain) {
    return chain;
}
void Xess::Initialize(vk::Instance, vk::PhysicalDevice, vk::Device) {}
void Xess::Shutdown() {}
bool Xess::Available() const {
    return false;
}
std::string Xess::Problem() const {
    return {};
}
bool Xess::HasFeature(const FeatureDesc&) const {
    return false;
}
bool Xess::CreateFeature(const FeatureDesc&) {
    return false;
}
bool Xess::Evaluate(vk::CommandBuffer, const Frame&) {
    return false;
}
void Xess::ReleaseFeature() {}

#endif

} // namespace Vulkan
