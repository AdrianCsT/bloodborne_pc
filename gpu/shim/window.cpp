// bbport: SDL3 window for the Vulkan swapchain (X11 or Wayland).
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <SDL3/SDL.h>
#include "common/assert.h"
#include "common/logging/log.h"
#include "sdl_window.h"
#include "bbport_overlay.h"
#include "bbport_settings.h"

#ifdef _WIN32
extern "C" char* compat_strcasestr(const char* haystack, const char* needle); // src/compat_win.c
#define strcasestr compat_strcasestr
#endif

namespace Frontend {

namespace {

// Issue #69: the monitor the window (and fullscreen) goes to. BB_DISPLAY: its number in SDL's
// order (1, 2, ...; bb-gpu-capabilities --displays lists them) or a part of its name; without it
// SDL's primary display. The monitors are logged so a report says which one was taken.
SDL_DisplayID ChooseDisplay() {
    const SDL_DisplayID primary = SDL_GetPrimaryDisplay();
    std::string wanted;
    if (const char* value = std::getenv("BB_DISPLAY")) {
        wanted = value;
        while (!wanted.empty() && std::isspace(static_cast<unsigned char>(wanted.back()))) {
            wanted.pop_back();
        }
        size_t first = 0;
        while (first < wanted.size() && std::isspace(static_cast<unsigned char>(wanted[first]))) {
            ++first;
        }
        wanted.erase(0, first);
    }
    int count = 0;
    SDL_DisplayID* ids = SDL_GetDisplays(&count);
    SDL_DisplayID chosen = 0;
    if (!wanted.empty()) {
        char* end = nullptr;
        const long number = std::strtol(wanted.c_str(), &end, 10);
        if (end && *end == '\0') {
            if (number >= 1 && number <= count) {
                chosen = ids[number - 1];
            }
        } else {
            for (int i = 0; i < count && !chosen; ++i) {
                const char* name = SDL_GetDisplayName(ids[i]);
                if (name && strcasestr(name, wanted.c_str())) {
                    chosen = ids[i];
                }
            }
        }
    }
    const SDL_DisplayID display = chosen ? chosen : primary;
    for (int i = 0; i < count; ++i) {
        const char* name = SDL_GetDisplayName(ids[i]);
        const SDL_DisplayMode* mode = SDL_GetDesktopDisplayMode(ids[i]);
        std::printf("Display %d: %s %dx%d%s%s\n", i + 1, name ? name : "?", mode ? mode->w : 0,
                    mode ? mode->h : 0, ids[i] == primary ? " (primary)" : "",
                    ids[i] == display ? " <- the game's (BB_DISPLAY)" : "");
    }
    if (!wanted.empty() && !chosen) {
        std::printf("Display: BB_DISPLAY=%s matches none, the primary one is used\n",
                    wanted.c_str());
    }
    SDL_free(ids);
    return display;
}

} // namespace

WindowSDL::WindowSDL(s32 width_, s32 height_, const char* title) : width{width_}, height{height_} {
    // Gamepads are sampled by runtime_pad.c; their events are pumped here with the window's.
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        UNREACHABLE_MSG("Failed to initialize SDL video: {}", SDL_GetError());
    }
    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetStringProperty(props, SDL_PROP_WINDOW_CREATE_TITLE_STRING, title);
    const SDL_DisplayID display = ChooseDisplay();
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_X_NUMBER, SDL_WINDOWPOS_CENTERED_DISPLAY(display));
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_Y_NUMBER, SDL_WINDOWPOS_CENTERED_DISPLAY(display));
    const char* fullscreen = std::getenv("BB_FULLSCREEN");
    const bool full = fullscreen && fullscreen[0] == '1';
    // bbport: in a window, an output above the game's 1920x1080 (bbport.ini output_res) used to be
    // rendered at that size and then shrunk into a 1920x1080 window. The window takes the output
    // size instead, or fills the display's usable area (maximized) when that size does not fit.
    s32 want_w = width_, want_h = height_;
    bool maximize = false;
    const int output = BbSettings::Get().output_res.load();
    if (!full && output >= 0 && output < int(std::size(BbSettings::OutputWidths))) {
        const s32 ow = BbSettings::OutputWidths[output], oh = BbSettings::OutputHeights[output];
        if (ow > want_w && oh > want_h) {
            want_w = ow;
            want_h = oh;
            SDL_Rect usable{};
            // When the usable area cannot be read, maximize: the output size could be larger than the screen.
            const bool known = SDL_GetDisplayUsableBounds(display, &usable);
            maximize = !known || ow > usable.w || oh > usable.h;
            std::printf("Window: sized for the %dx%d output%s\n", ow, oh,
                        !known ? " (maximized: the size of the screen could not be read)"
                        : maximize ? " (maximized: that size does not fit the screen)" : "");
        }
    }
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_WIDTH_NUMBER, want_w);
    SDL_SetNumberProperty(props, SDL_PROP_WINDOW_CREATE_HEIGHT_NUMBER, want_h);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_MAXIMIZED_BOOLEAN, maximize);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_RESIZABLE_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_VULKAN_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_WINDOW_CREATE_FULLSCREEN_BOOLEAN, full);
    base_title = title;
    window = SDL_CreateWindowWithProperties(props);
    SDL_DestroyProperties(props);
    ASSERT_MSG(window, "Failed to create window: {}", SDL_GetError());

    const char* driver = SDL_GetCurrentVideoDriver();
    const SDL_PropertiesID wp = SDL_GetWindowProperties(window);
#ifdef _WIN32
    if (driver && !std::strcmp(driver, "windows")) {
        window_info.type = WindowSystemType::Windows;
        window_info.render_surface = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WIN32_HWND_POINTER, nullptr);
    } else
#endif
    if (driver && !std::strcmp(driver, "x11")) {
        window_info.type = WindowSystemType::X11;
        window_info.display_connection = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_X11_DISPLAY_POINTER, nullptr);
        window_info.render_surface = reinterpret_cast<void*>(SDL_GetNumberProperty(wp, SDL_PROP_WINDOW_X11_WINDOW_NUMBER, 0));
    } else if (driver && !std::strcmp(driver, "wayland")) {
        window_info.type = WindowSystemType::Wayland;
        window_info.display_connection = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER, nullptr);
        window_info.render_surface = SDL_GetPointerProperty(wp, SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER, nullptr);
    } else {
        UNREACHABLE_MSG("Unsupported SDL video driver {}", driver ? driver : "(none)");
    }
    int w = 0, h = 0;
    SDL_GetWindowSizeInPixels(window, &w, &h);
    width = w;
    height = h;
    LOG_INFO(Frontend, "Window {}x{} on {}", w, h, driver);
}

WindowSDL::~WindowSDL() {
    SDL_DestroyWindow(window);
}

void WindowSDL::BeginTextInput(const std::string& initial, const std::string& prompt) {
    std::scoped_lock lock{text_mutex};
    text = initial;
    text_prompt = prompt;
    text_state = 0;
    text_requested = true;
}

int WindowSDL::PollTextInput(std::string& out) {
    std::scoped_lock lock{text_mutex};
    out = text;
    return text_state;
}

void WindowSDL::UpdateTextTitle() {
    const std::string title = text_active ? base_title + " \u2014 " + text_prompt + ": " + text + "_  (Enter = OK, Esc = cancel)"
                                          : base_title;
    SDL_SetWindowTitle(window, title.c_str());
    BbOverlay::SetTextPrompt(text_active, text_prompt, text);
}

bool WindowSDL::TakeMouse(float& dx, float& dy, float& wheel, u32& buttons) {
    std::scoped_lock lock{mouse_mutex};
    dx = mouse_dx;
    dy = mouse_dy;
    wheel = mouse_wheel;
    buttons = mouse_held | mouse_clicked; // a click shorter than a game frame still counts
    mouse_dx = mouse_dy = mouse_wheel = 0.0f;
    mouse_clicked = 0;
    return mouse_captured.load(std::memory_order_relaxed);
}

// The mouse belongs to the game while it is enabled (runtime_pad.c), the window has focus and
// neither the settings menu nor the text entry is shown; relative mode hides the cursor and
// keeps it inside. Any change drops what was collected: the click that focused the window and
// motion from before the capture are not the game's, nor is a turn the camera has not taken
// when the mouse is let go.
void WindowSDL::UpdateMouseCapture() {
    const bool focused = (SDL_GetWindowFlags(window) & SDL_WINDOW_INPUT_FOCUS) != 0;
    const bool want = mouse_enabled.load(std::memory_order_relaxed) && focused && !text_active &&
                      !BbOverlay::CapturesInput();
    if (want != SDL_GetWindowRelativeMouseMode(window)) {
        SDL_SetWindowRelativeMouseMode(window, want);
    }
    if (want != mouse_captured.load(std::memory_order_relaxed)) {
        {
            std::scoped_lock lock{mouse_mutex};
            mouse_dx = mouse_dy = mouse_wheel = 0.0f;
            mouse_held = mouse_clicked = 0;
            mouse_captured.store(want, std::memory_order_relaxed);
        }
        if (const auto drop = mouse_drop.load(std::memory_order_acquire)) {
            drop();
        }
    }
}

bool WindowSDL::PollEvents() {
    {
        std::scoped_lock lock{text_mutex};
        if (text_requested) { // SDL text input must be toggled from the window thread
            text_requested = false;
            text_active = true;
            SDL_StartTextInput(window);
            UpdateTextTitle();
        }
    }
    if (!text_active) {
        BbOverlay::UpdateTextInput(window);
    }
    UpdateMouseCapture();
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_EVENT_MOUSE_MOTION) {
            last_mouse_motion_ms = SDL_GetTicks();
        }
        if (text_active && (event.type == SDL_EVENT_TEXT_INPUT || event.type == SDL_EVENT_KEY_DOWN)) {
            std::scoped_lock lock{text_mutex};
            if (event.type == SDL_EVENT_TEXT_INPUT) {
                text += event.text.text;
            } else if (event.key.key == SDLK_BACKSPACE && !text.empty()) {
                size_t cut = text.size() - 1; // drop one UTF-8 code point
                while (cut > 0 && (static_cast<unsigned char>(text[cut]) & 0xC0) == 0x80) --cut;
                text.erase(cut);
            } else if (event.key.key == SDLK_RETURN || event.key.key == SDLK_KP_ENTER || event.key.key == SDLK_ESCAPE) {
                text_state = event.key.key == SDLK_ESCAPE ? 2 : 1;
                text_active = false;
                SDL_StopTextInput(window);
            }
            UpdateTextTitle();
            continue;
        }
        // The controller finishes the text dialog too: Cross (A) accepts, Circle (B) cancels.
        if (text_active && event.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN &&
            (event.gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ||
             event.gbutton.button == SDL_GAMEPAD_BUTTON_EAST)) {
            std::scoped_lock lock{text_mutex};
            text_state = event.gbutton.button == SDL_GAMEPAD_BUTTON_SOUTH ? 1 : 2;
            text_active = false;
            SDL_StopTextInput(window);
            UpdateTextTitle();
            continue;
        }
        if (BbOverlay::HandleEvent(event)) {
            continue;
        }
        switch (event.type) {
        case SDL_EVENT_MOUSE_MOTION:
            if (mouse_captured.load(std::memory_order_relaxed)) {
                if (const auto turn = mouse_turn.load(std::memory_order_acquire)) {
                    turn(event.motion.xrel, event.motion.yrel);
                    break;
                }
                std::scoped_lock lock{mouse_mutex};
                mouse_dx += event.motion.xrel;
                mouse_dy += event.motion.yrel;
            }
            break;
        case SDL_EVENT_MOUSE_BUTTON_DOWN:
        case SDL_EVENT_MOUSE_BUTTON_UP:
            if (mouse_captured.load(std::memory_order_relaxed)) {
                std::scoped_lock lock{mouse_mutex};
                const u32 mask = SDL_BUTTON_MASK(event.button.button);
                if (event.type == SDL_EVENT_MOUSE_BUTTON_DOWN) {
                    mouse_held |= mask;
                    mouse_clicked |= mask;
                } else {
                    mouse_held &= ~mask;
                }
            }
            break;
        case SDL_EVENT_MOUSE_WHEEL:
            if (mouse_captured.load(std::memory_order_relaxed)) {
                std::scoped_lock lock{mouse_mutex};
                mouse_wheel += event.wheel.direction == SDL_MOUSEWHEEL_FLIPPED ? -event.wheel.y : event.wheel.y;
            }
            break;
        case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        case SDL_EVENT_WINDOW_RESIZED: {
            int w = 0, h = 0;
            SDL_GetWindowSizeInPixels(window, &w, &h);
            width = w;
            height = h;
            break;
        }
        case SDL_EVENT_QUIT:
        case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
            is_open = false;
            break;
        default:
            break;
        }
    }
    UpdateCursor();
    return is_open;
}

// Issue #3: the OS cursor over the game. Hidden in fullscreen, and in a window after 3 s without
// moving the mouse; always shown while the settings menu is open. Hidden too while the game holds
// the mouse (relative mode).
void WindowSDL::UpdateCursor() {
    const bool fullscreen = (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0;
    const bool hide = !BbOverlay::MenuOpen() &&
                      (fullscreen || mouse_captured.load(std::memory_order_relaxed) ||
                       SDL_GetTicks() - last_mouse_motion_ms > 3000);
    if (hide != cursor_hidden) {
        cursor_hidden = hide;
        hide ? SDL_HideCursor() : SDL_ShowCursor();
    }
}

} // namespace Frontend
