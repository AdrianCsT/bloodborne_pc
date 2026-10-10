// bbport: the game window. Created by the VideoOut driver on first open; the
// event pump runs on the port's window thread (see window.cpp).
#pragma once
#include <atomic>
#include <mutex>
#include <string>
#include "common/types.h"

struct SDL_Window;

namespace Frontend {

enum class WindowSystemType : u8 { Headless, Windows, X11, Wayland, Metal };

struct WindowSystemInfo {
    void* display_connection = nullptr;
    void* render_surface = nullptr;
    float render_surface_scale = 1.0f;
    WindowSystemType type = WindowSystemType::Headless;
};

class WindowSDL {
public:
    WindowSDL(s32 width, s32 height, const char* title);
    ~WindowSDL();
    s32 GetWidth() const { return width.load(std::memory_order_relaxed); }
    s32 GetHeight() const { return height.load(std::memory_order_relaxed); }
    SDL_Window* GetSDLWindow() const { return window; }
    WindowSystemInfo GetWindowInfo() const { return window_info; }
    bool IsOpen() const { return is_open.load(std::memory_order_relaxed); }
    /// Processes pending window events. Returns false once the user closed the window.
    bool PollEvents();
    /// Keyboard text entry for the system IME dialog; typed text shows in the title bar.
    void BeginTextInput(const std::string& initial, const std::string& prompt);
    /// 0 while typing, 1 confirmed (Enter), 2 cancelled (Escape); text is UTF-8.
    int PollTextInput(std::string& text);
    /// Mouse for the game (runtime_pad.c). While enabled, the window holds the mouse in relative
    /// mode whenever it has focus and neither the settings menu nor the text entry is shown.
    void SetMouseEnabled(bool enabled) { mouse_enabled.store(enabled, std::memory_order_relaxed); }
    /// Motion (counts) and wheel steps since the last call, and the buttons held or clicked
    /// meanwhile (SDL_BUTTON_MASK); true while the mouse is held.
    bool TakeMouse(float& dx, float& dy, float& wheel, u32& buttons);
    /// The native mouse camera: motion goes to `turn` at once, from the window thread, instead of
    /// TakeMouse, and `drop` runs whenever the mouse is let go or taken. Null turn: off.
    static inline std::atomic<void (*)(float dx, float dy)> mouse_turn{nullptr};
    static inline std::atomic<void (*)()> mouse_drop{nullptr};

private:
    std::atomic<bool> mouse_enabled{false}, mouse_captured{false};
    std::mutex mouse_mutex;
    float mouse_dx{}, mouse_dy{}, mouse_wheel{};
    u32 mouse_held{}, mouse_clicked{}; ///< clicked: pressed since the last TakeMouse
    void UpdateMouseCapture();
    std::atomic<s32> width, height;
    std::atomic<bool> is_open{true};
    std::mutex text_mutex;
    bool text_requested{}, text_active{};
    int text_state{};
    std::string text, text_prompt, base_title;
    void UpdateTextTitle();
    void UpdateCursor();
    u64 last_mouse_motion_ms{}; ///< SDL_GetTicks of the last mouse motion (UpdateCursor)
    bool cursor_hidden{};
    SDL_Window* window{};
    WindowSystemInfo window_info{};
};

} // namespace Frontend
