// SPDX-License-Identifier: GPL-2.0-or-later
//
// bbport: Intel XeSS Super Resolution (DP4a path, runs on AMD, Intel and NVIDIA GPUs) through
// Intel's libxess.dll, loaded at run time from next to bb-probe.exe. The port contains no Intel
// code and does not link the library: without the DLL, with BB_XESS=0 or on an unsupported
// GPU nothing changes. The Vulkan entry points are declared in vk_xess.cpp (from the MIT-licensed
// xess.h and xess_vk.h of github.com/intel/xess, SDK 3.0.2).

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/renderer_vulkan/vk_dlss.h"

namespace Vulkan {

class Xess {
public:
    /// The process-wide instance, or null when libxess.dll is absent or BB_XESS=0.
    static Xess* Get();
    ~Xess();

    /// Device creation: the extensions XeSS needs (XeSS is disabled when it cannot be had).
    void AppendDeviceExtensions(vk::Instance instance, vk::PhysicalDevice physical,
                                std::vector<const char*>& enabled);
    /// `chain` is the pNext chain of the VkDeviceCreateInfo (its first structure is a
    /// VkPhysicalDeviceFeatures2); returns the chain to create the device with, XeSS's features set.
    [[nodiscard]] const void* PatchDeviceFeatures(vk::Instance instance,
                                                  vk::PhysicalDevice physical, const void* chain);
    /// After the device exists.
    void Initialize(vk::Instance instance, vk::PhysicalDevice physical, vk::Device device);
    void Shutdown();

    [[nodiscard]] bool Available() const;
    /// Why XeSS cannot run on this system (empty when it can, or before device creation).
    [[nodiscard]] std::string Problem() const;

    using Resource = Dlss::Resource;
    struct FeatureDesc {
        u32 input_width, input_height, output_width, output_height;
        bool hdr; ///< linear scene color with automatic exposure; false: the game's tonemapped frame
        bool operator==(const FeatureDesc&) const = default;
    };
    struct Frame {
        Resource color, depth, motion, output;
        float jitter_x, jitter_y;
        bool reset;
    };

    [[nodiscard]] bool HasFeature(const FeatureDesc& desc) const;
    /// (Re)initializes XeSS for these sizes. Blocking (kernel compilation the first time); the GPU
    /// must be done with the previous feature.
    bool CreateFeature(const FeatureDesc& desc);
    /// Records the upscale (output in General). The inputs are General; they are moved to
    /// shader-read-only for XeSS and back.
    bool Evaluate(vk::CommandBuffer command, const Frame& frame);
    void ReleaseFeature();

private:
    Xess();
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Vulkan
