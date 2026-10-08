// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: FSR 3.1 frame generation on top of the temporal upscaler (docs: README-Windows.txt).
//
// One interpolated frame is shown between every two game frames. The port keeps its own
// swapchain and presenter; this class drives the frame interpolation and optical flow compute
// passes of the FSR-Vulkan portable library (ffx_vk_portable.h) and keeps what they need:
//
//   * at the upscale (UpscalerFsr3/4/DLSS/XeSS/TAA, GPU command thread): the scene depth and the
//     camera motion vectors of the frame, which the game overwrites before the frame is
//     presented (CaptureScene), and a copy of the upscaled scene right before the first UI
//     draw (CaptureHudless);
//   * in the presenter (PrepareFrame, same thread): Plan() decides whether this frame gets an
//     interpolated frame, Record() puts the optical flow and interpolation passes after the
//     frame's host passes;
//   * on the swap thread (Present): the smoothed interval of the game frames, which places
//     the interpolated frame half way, and the statistics.
//
// SDR output only. With the switch off none of this runs.

#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <deque>
#include <string>
#include <string_view>
#include <vector>

#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/texture_cache/image.h"

struct FfxVkPortableFrameGenerationContext;

namespace Vulkan {

class Instance;
class Scheduler;

class FrameGeneration {
public:
    FrameGeneration(const Instance& instance, Scheduler& scheduler);
    ~FrameGeneration();

    /// The switch is on (bbport.ini frame_generation, BB_FRAME_GEN) and a temporal upscaler is
    /// selected. Thread safe.
    [[nodiscard]] static bool Requested();

    // ---- Upscaler side: GPU command thread, in command order ---------------------------------

    struct Scene {
        vk::Image depth;
        vk::ImageLayout depth_layout; ///< layout `depth` is in when the copy runs
        vk::Format depth_format;
        vk::Image motion; ///< RG16F camera + object motion, pixels, previous minus current
        u32 width, height; ///< render size
        std::array<float, 2> jitter;
        float frame_ms;
        float camera_near, camera_fov;
        bool reset; ///< the upscaler restarted its history with this frame
    };
    /// The upscaler ran for this frame: copies the depth, notes the rest for PrepareFrame.
    void CaptureScene(const Scene& scene);
    /// A scene was captured and its HUD-less copy is not taken yet (the first UI draw).
    [[nodiscard]] bool WantsHudless() const noexcept {
        return pending.valid && !pending.hudless;
    }
    /// The finished scene before any UI pixel: R8G8B8A8 (unorm or sRGB) of `width` x `height`.
    void CaptureHudless(vk::Image image, vk::ImageLayout layout, vk::Format format, u32 width,
                        u32 height);

    // ---- Presenter side: GPU command thread ---------------------------------------------------

    struct Plan {
        bool generate = false; ///< Record() will be called for this frame
        bool present = false;  ///< the interpolated frame can be shown (not a history restart)
        bool hudless = false;  ///< a HUD-less image of the size of the display buffer exists
    };
    /// Called for every game frame at PrepareFrame (also with the switch off, cheaply):
    /// `width` x `height` is the size of the presented frame image, `format` its format,
    /// `display` the size of the image the host passes read (the display buffer).
    Plan PlanFrame(u32 width, u32 height, vk::Format format, bool hdr, vk::Extent2D display);
    /// Format of the interpolated image, valid after a Plan with generate set.
    [[nodiscard]] vk::Format OutputFormat() const noexcept {
        return output_format;
    }
    /// The HUD-less copy as an image the host passes can sample, in `view_format`.
    struct Hudless {
        vk::ImageView view;
        vk::Extent2D size;
    };
    Hudless HudlessView(vk::Format view_format);
    [[nodiscard]] vk::Image HudlessImage() const noexcept {
        return vk::Image(hudless_raw);
    }

    struct Targets {
        vk::Image current;  ///< the presented frame, with the UI (General, shader readable)
        vk::Image hudless;  ///< the same without the UI, or null
        vk::Image output;   ///< the interpolated frame, to be written (any layout)
        vk::Format format;
        u32 width, height;
    };
    /// Records the optical flow, the interpolation preparation and the interpolation into
    /// `cmdbuf` (direct recording, after the frame's host passes). The image `output` is in
    /// General afterwards. False when the library refused (generation is off until the
    /// switch is turned off and on again).
    bool Record(vk::CommandBuffer cmdbuf, const Targets& targets);
    /// After the frame's submission: its tick, which the library's view recycling waits for.
    void EndFrame(u64 submission_tick);

    // ---- Swap thread --------------------------------------------------------------------------

    /// A game frame (not a redraw) is presented now. `generated`: an interpolated frame goes
    /// first. Returns the delay of the game frame after the interpolated one, in ms (0 when
    /// there is no interpolated frame).
    float OnPresent(bool generated);
    /// The display's refresh rate when known (0 otherwise).
    void SetRefreshRate(float hz) noexcept {
        refresh_hz.store(hz, std::memory_order_relaxed);
    }
    /// Swap thread: one more image went out (an interpolated one or a game frame).
    void OnImagePresented(bool generated);
    /// Swap thread: a game frame goes out with generation switched off.
    void OnSwitchedOff();
    /// Smoothed interval between game frames in ms, 0 until known.
    [[nodiscard]] float BaseIntervalMs() const noexcept {
        return base_interval_ms.load(std::memory_order_relaxed);
    }

private:
    struct Pending {
        bool valid = false;
        vk::Image motion;
        u32 width = 0, height = 0;
        vk::Format depth_format{};
        std::array<float, 2> jitter{};
        float frame_ms = 16.6f, camera_near = 0.05f, camera_fov = 1.0f;
        bool reset = false;
        bool hudless = false;
        u32 hudless_width = 0, hudless_height = 0;
    };

    void EnsureDepthCopy(vk::Format format, u32 width, u32 height);
    void EnsureHudless(vk::Format format, u32 width, u32 height);
    bool CreateContext(u32 width, u32 height, vk::Format format);
    void DestroyContext();
    /// Logs a change of state once ("off (HDR output)").
    void Report(std::string_view state);
    /// Whether the base frame rate leaves room for a doubled one on this display.
    bool Idle();
    void PrintStats(std::chrono::steady_clock::time_point now);

    const Instance& instance;
    Scheduler& scheduler;

    // Capture (command thread).
    Pending pending;
    Pending current; ///< the capture PrepareFrame is using
    VideoCore::UniqueImage depth_copy;
    vk::Format depth_copy_format{};
    u32 depth_copy_width = 0, depth_copy_height = 0;
    VideoCore::UniqueImage hudless_raw;
    vk::Format hudless_format{};
    u32 hudless_width = 0, hudless_height = 0;
    struct View {
        vk::Format format;
        vk::ImageView view;
    };
    std::vector<View> hudless_views;

    // Context (command thread).
    FfxVkPortableFrameGenerationContext* context = nullptr;
    u32 context_width = 0, context_height = 0;
    vk::Format context_format{};
    vk::Format output_format{};
    bool failed = false;          ///< the library refused; until the switch is cycled
    bool was_requested = false;
    bool history_broken = true;   ///< the next frame restarts the interpolation history
    bool idle = false;
    u64 frame_id = 0;
    std::deque<u64> ticks;        ///< submissions of the last frames the library recorded
    std::string reported;
    bool reset_frame = false;     ///< this frame restarts the interpolation history

    // Pacing and statistics (swap thread writes; the command thread reads the atomics).
    std::atomic<float> base_interval_ms{0.0f};
    std::atomic<float> refresh_hz{0.0f};
    std::chrono::steady_clock::time_point last_present{};
    std::chrono::steady_clock::time_point stats_start{}, overlay_start{};
    u32 stats_base = 0, stats_images = 0, stats_generated = 0;
    u32 overlay_base = 0, overlay_images = 0;
    std::atomic<u32> skipped{0};
};

} // namespace Vulkan
