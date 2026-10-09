/* Check whether native-size depth/stencil images can be blitted to reduced
 * renderer targets. The renderer needs both directions for live presets.
 * --gamepads: the connected gamepads, "GUID<tab>name" per line (the launcher's controller list,
 * BB_GAMEPAD). --displays: the monitors in the numbering BB_DISPLAY uses (below; issue #69).
 * --read-input: one key or button for the launcher's controls (below).
 * --upscalers: one "UPSCALER <name> supported|unsupported: <reason>" line per upscaler (below). */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#include <SDL3/SDL.h>

static VkDeviceSize largest_local_heap(VkPhysicalDevice device) {
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(device, &memory);
    VkDeviceSize largest = 0;
    for (uint32_t i = 0; i < memory.memoryHeapCount; ++i) {
        if ((memory.memoryHeaps[i].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) &&
            memory.memoryHeaps[i].size > largest) {
            largest = memory.memoryHeaps[i].size;
        }
    }
    return largest;
}

static int better_device(VkPhysicalDevice candidate, VkPhysicalDevice current) {
    VkPhysicalDeviceProperties next, old;
    vkGetPhysicalDeviceProperties(candidate, &next);
    vkGetPhysicalDeviceProperties(current, &old);
    const int next_api = next.apiVersion >= VK_API_VERSION_1_3;
    const int old_api = old.apiVersion >= VK_API_VERSION_1_3;
    if (next_api != old_api) return next_api;
    const int next_discrete = next.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
    const int old_discrete = old.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
    if (next_discrete != old_discrete) return next_discrete;
    const int next_cpu = next.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU;
    const int old_cpu = old.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU;
    if (next_cpu != old_cpu) return !next_cpu;
    return largest_local_heap(candidate) > largest_local_heap(current);
}

/* --live-resolution: prints 1 when live resolution changes suit the GPU (run.sh, setting
 * live_resolution=auto), else 0. Live scaling keeps the game's post-processing at 1080p and
 * copies scene targets every frame: fine on a strong discrete GPU, 7-8 FPS on the Steam Deck
 * and a GTX 1060. Rule: discrete, at least 8 GB of device memory, and not an NVIDIA GPU older
 * than Turing (no fragment shader barycentrics; its depth/stencil copies take nine draws). */
static int has_extension(VkPhysicalDevice device, const char *name) {
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(device, NULL, &count, NULL) != VK_SUCCESS) return 0;
    VkExtensionProperties *list = calloc(count ? count : 1, sizeof(*list));
    int found = 0;
    if (list && vkEnumerateDeviceExtensionProperties(device, NULL, &count, list) == VK_SUCCESS)
        for (uint32_t i = 0; i < count && !found; ++i) found = !strcmp(list[i].extensionName, name);
    free(list);
    return found;
}

static int live_resolution_suits(VkPhysicalDevice device) {
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(device, &props);
    const int discrete = props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
    const VkDeviceSize memory = largest_local_heap(device);
    const int old_nvidia = props.vendorID == 0x10de &&
                           !has_extension(device, "VK_KHR_fragment_shader_barycentric");
    const int suits = discrete && memory >= (VkDeviceSize)7680 << 20 && !old_nvidia;
    fprintf(stderr, "GPU: %s, %s, %llu MiB: live resolution changes %s\n", props.deviceName,
            discrete ? "discrete" : "integrated or other", (unsigned long long)(memory >> 20),
            suits ? "on" : "off (startup resolution patch)");
    return suits;
}

static int list_gamepads(void) {
    if (!SDL_Init(SDL_INIT_GAMEPAD)) {
        fprintf(stderr, "gamepads: %s\n", SDL_GetError());
        return 1;
    }
    // Some devices (HIDAPI) show up only after events are pumped.
    for (int i = 0; i < 5; ++i) {
        SDL_PumpEvents();
        SDL_Delay(40);
    }
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    for (int i = 0; ids && i < count; ++i) {
        char guid[33];
        SDL_GUIDToString(SDL_GetGamepadGUIDForID(ids[i]), guid, sizeof guid);
        const char *name = SDL_GetGamepadNameForID(ids[i]);
        printf("%s\t%s\n", guid, name ? name : "?");
    }
    SDL_free(ids);
    SDL_Quit();
    return 0;
}

/* --displays: one line per monitor, in SDL's order, which is the number BB_DISPLAY takes
 * (gpu/shim/window.cpp picks the game's window and fullscreen display the same way):
 *   <number><TAB><name><TAB><width>x<height><TAB><refresh Hz><TAB><primary>
 * number: 1, 2, ... (the line's position); name: SDL's, tabs and line breaks replaced by spaces;
 * width x height: the desktop mode in pixels (0x0 when unknown); refresh Hz: whole Hz, rounded,
 * 0 when unknown; primary: 1 for SDL's primary display, else 0. No monitor: no output, exit 0;
 * SDL cannot start: a message on stderr, exit 1. */
static int list_displays(void) {
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        fprintf(stderr, "displays: %s\n", SDL_GetError());
        return 1;
    }
    const SDL_DisplayID primary = SDL_GetPrimaryDisplay();
    int count = 0;
    SDL_DisplayID *ids = SDL_GetDisplays(&count);
    for (int i = 0; ids && i < count; ++i) {
        char name[256];
        const char *sdl_name = SDL_GetDisplayName(ids[i]);
        snprintf(name, sizeof name, "%s", sdl_name && *sdl_name ? sdl_name : "?");
        for (char *p = name; *p; ++p) {
            if (*p == '\t' || *p == '\n' || *p == '\r') {
                *p = ' ';
            }
        }
        const SDL_DisplayMode *mode = SDL_GetDesktopDisplayMode(ids[i]);
        printf("%d\t%s\t%dx%d\t%d\t%d\n", i + 1, name, mode ? mode->w : 0, mode ? mode->h : 0,
               mode ? (int)(mode->refresh_rate + 0.5f) : 0, ids[i] == primary);
    }
    SDL_free(ids);
    SDL_Quit();
    return 0;
}

/* --read-input key|pad: a small window; prints "key <SDL key name>" or "pad <SDL button name>"
 * (lefttrigger/righttrigger for the triggers) for the first key or gamepad button pressed, the
 * names bbport.ini's key.* and pad.* lines take. Escape, closing it or 15 s: nothing. */
static int read_input(const char *kind) {
    const int want_key = strcmp(kind, "pad") != 0, want_pad = strcmp(kind, "key") != 0;
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        fprintf(stderr, "read-input: %s\n", SDL_GetError());
        return 1;
    }
    SDL_Window *window = NULL;
    SDL_Renderer *renderer = NULL;
    const char *prompt = want_key && want_pad ? "Press a key or a gamepad button"
                         : want_key           ? "Press a key"
                                              : "Press a gamepad button";
    if (!SDL_CreateWindowAndRenderer("bbport", 520, 90, 0, &window, &renderer)) {
        fprintf(stderr, "read-input: %s\n", SDL_GetError());
        SDL_Quit();
        return 1;
    }
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    for (int i = 0; ids && i < count; ++i) SDL_OpenGamepad(ids[i]);
    SDL_free(ids);
    const Uint64 end = SDL_GetTicks() + 15000;
    int done = 0;
    while (!done && SDL_GetTicks() < end) {
        SDL_SetRenderDrawColor(renderer, 24, 24, 28, 255);
        SDL_RenderClear(renderer);
        SDL_SetRenderDrawColor(renderer, 230, 230, 230, 255);
        SDL_RenderDebugText(renderer, 16, 30, prompt);
        SDL_RenderDebugText(renderer, 16, 50, "Escape: cancel");
        SDL_RenderPresent(renderer);
        SDL_Event e;
        while (!done && SDL_WaitEventTimeout(&e, 50)) {
            switch (e.type) {
            case SDL_EVENT_QUIT:
            case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
                done = 1;
                break;
            case SDL_EVENT_GAMEPAD_ADDED:
                SDL_OpenGamepad(e.gdevice.which);
                break;
            case SDL_EVENT_KEY_DOWN:
                if (e.key.scancode == SDL_SCANCODE_ESCAPE) {
                    done = 1;
                } else if (want_key) {
                    printf("key %s\n", SDL_GetScancodeName(e.key.scancode));
                    done = 1;
                }
                break;
            case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
                if (want_pad) {
                    printf("pad %s\n", SDL_GetGamepadStringForButton((SDL_GamepadButton)e.gbutton.button));
                    done = 1;
                }
                break;
            case SDL_EVENT_GAMEPAD_AXIS_MOTION:
                if (want_pad && e.gaxis.value > 16000 &&
                    (e.gaxis.axis == SDL_GAMEPAD_AXIS_LEFT_TRIGGER || e.gaxis.axis == SDL_GAMEPAD_AXIS_RIGHT_TRIGGER)) {
                    printf("pad %s\n", e.gaxis.axis == SDL_GAMEPAD_AXIS_LEFT_TRIGGER ? "lefttrigger" : "righttrigger");
                    done = 1;
                }
                break;
            default:
                break;
            }
        }
    }
    fflush(stdout);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}

/* The renderer's default ranking, or BB_GPU_ID (an index into the device list). NULL with a message
 * in *error when there is no usable device; the list is freed here. */
static VkPhysicalDevice select_device(VkInstance instance, const char **error) {
    uint32_t count = 0;
    if (vkEnumeratePhysicalDevices(instance, &count, NULL) != VK_SUCCESS || !count) {
        *error = "no Vulkan device";
        return VK_NULL_HANDLE;
    }
    VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
    if (!devices || vkEnumeratePhysicalDevices(instance, &count, devices) != VK_SUCCESS) {
        free(devices);
        *error = "cannot enumerate Vulkan devices";
        return VK_NULL_HANDLE;
    }
    VkPhysicalDevice selected = devices[0];
    const char *gpu_id = getenv("BB_GPU_ID");
    if (gpu_id && atoi(gpu_id) >= 0) {
        const unsigned long index = strtoul(gpu_id, NULL, 10);
        if (index >= count) {
            free(devices);
            *error = "BB_GPU_ID is outside the device list";
            return VK_NULL_HANDLE;
        }
        selected = devices[index];
    } else {
        for (uint32_t i = 1; i < count; ++i)
            if (better_device(devices[i], selected)) selected = devices[i];
    }
    free(devices);
    return selected;
}

/* --upscalers: whether each upscaler can run on the selected GPU, with the checks the renderer
 * makes (vk_instance.h IsFsr4Int8Supported / IsFsr411Supported, Dlss::Problem, Xess::Problem),
 * one line each on stdout:
 *   UPSCALER <name> supported[: <note>]       the note is a warning (FSR 4 on RDNA2 or older)
 *   UPSCALER <name> unsupported: <reason>
 * FSR 4's downloadable assets are not checked here (the launcher knows where they are). DLSS and
 * XeSS load their DLLs from the folder of this executable, as the renderer does from bb-probe.exe's. */
#define MAX_INSTANCE_EXTENSIONS 16

typedef struct {
    int ok;
    char reason[200];
} UpscalerVerdict;

/* Marks an upscaler unsupported, with the reason shown to the player. */
static void fail(UpscalerVerdict *v, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void fail(UpscalerVerdict *v, const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(v->reason, sizeof v->reason, fmt, args);
    va_end(args);
    v->ok = 0;
}

static void pass(UpscalerVerdict *v) {
    v->ok = 1;
    v->reason[0] = 0;
}

#ifdef _WIN32
#include <windows.h>
#include "../gpu/dlss_bridge/bbport_dlss_bridge.h"

static void add_instance_extension(const char **list, uint32_t *count, const char *name) {
    for (uint32_t i = 0; i < *count; ++i)
        if (!strcmp(list[i], name)) return;
    if (*count < MAX_INSTANCE_EXTENSIONS) list[(*count)++] = name;
}

/* The ABI of libxess.dll's Vulkan entry points (inc/xess/xess_vk.h, MIT); xess_result_t is an int. */
typedef int (*XessInstanceExtensionsFn)(uint32_t *count, const char *const **names, uint32_t *min_api);
typedef int (*XessDeviceExtensionsFn)(VkInstance, VkPhysicalDevice, uint32_t *count,
                                      const char *const **names);

static HMODULE load_beside_exe(const wchar_t *name) {
    wchar_t path[MAX_PATH + 32];
    DWORD length = GetModuleFileNameW(NULL, path, MAX_PATH);
    if (!length || length >= MAX_PATH) return NULL;
    while (length && path[length - 1] != L'\\') --length;
    path[length] = 0;
    wcscat(path, name);
    if (GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES) return NULL;
    return LoadLibraryW(path);
}

static void bridge_log(int warning, const char *message) {
    (void)warning;
    (void)message;
}

typedef struct {
    HMODULE xess;
    XessInstanceExtensionsFn xess_instance_extensions;
    XessDeviceExtensionsFn xess_device_extensions;
    const BbDlssApi *dlss;
    int xess_result;
    int dlss_configured;
} Dlls;

/* Loads the DLLs and adds their instance extensions to `list`. */
static void load_upscaler_dlls(Dlls *dlls, const char **list, uint32_t *count) {
    memset(dlls, 0, sizeof *dlls);
    dlls->xess = load_beside_exe(L"libxess.dll");
    if (dlls->xess) {
        dlls->xess_instance_extensions =
            (XessInstanceExtensionsFn)(void *)GetProcAddress(dlls->xess, "xessVKGetRequiredInstanceExtensions");
        dlls->xess_device_extensions =
            (XessDeviceExtensionsFn)(void *)GetProcAddress(dlls->xess, "xessVKGetRequiredDeviceExtensions");
        const char *const *names = NULL;
        uint32_t n = 0, min_api = 0;
        if (dlls->xess_instance_extensions && dlls->xess_device_extensions) {
            dlls->xess_result = dlls->xess_instance_extensions(&n, &names, &min_api);
            if (dlls->xess_result >= 0)
                for (uint32_t i = 0; i < n; ++i) add_instance_extension(list, count, names[i]);
        } else {
            dlls->xess_result = -1000;
        }
    }
    HMODULE bridge = load_beside_exe(L"bbport_dlss.dll");
    HMODULE ngx = load_beside_exe(L"nvngx_dlss.dll");
    BbDlssGetApiFn get_api =
        bridge && ngx ? (BbDlssGetApiFn)(void *)GetProcAddress(bridge, "BbDlssGetApi") : NULL;
    const BbDlssApi *api = get_api ? get_api() : NULL;
    if (!api || api->abi != BBPORT_DLSS_BRIDGE_ABI) return;
    wchar_t folder[MAX_PATH + 32], data[MAX_PATH + 32];
    DWORD length = GetModuleFileNameW(NULL, folder, MAX_PATH);
    while (length && folder[length - 1] != L'\\') --length;
    folder[length] = 0;
    const DWORD temp = GetTempPathW(MAX_PATH, data);
    if (!temp || temp > MAX_PATH) return;
    wcscpy(data + temp, L"bbport-dlss-probe");
    CreateDirectoryW(data, NULL);
    dlls->dlss = api;
    uint32_t n = 0;
    const VkExtensionProperties *required = NULL;
    if (api->Configure(folder, data, bridge_log) && api->InstanceExtensions(&n, &required)) {
        dlls->dlss_configured = 1;
        for (uint32_t i = 0; i < n; ++i) add_instance_extension(list, count, required[i].extensionName);
    }
}

static void verdict_xess(const Dlls *dlls, VkInstance instance, VkPhysicalDevice device, int instance_ok,
                         UpscalerVerdict *v) {
    if (!dlls->xess) return fail(v, "libxess.dll is not installed");
    if (dlls->xess_result < 0 || !instance_ok) return fail(v, "this Vulkan driver lacks what XeSS needs");
    uint32_t n = 0;
    const char *const *names = NULL;
    const int result = dlls->xess_device_extensions(instance, device, &n, &names);
    if (result < 0) return fail(v, "this GPU or driver does not support XeSS (DP4a needed)");
    for (uint32_t i = 0; i < n; ++i)
        if (!has_extension(device, names[i])) return fail(v, "needs the Vulkan extension %s", names[i]);
    pass(v);
}

static void verdict_dlss(const Dlls *dlls, const VkPhysicalDeviceProperties *props, VkInstance instance,
                         VkPhysicalDevice device, int instance_ok, UpscalerVerdict *v) {
    if (props->vendorID != 0x10de) return fail(v, "not an NVIDIA RTX GPU");
    if (!dlls->dlss) return fail(v, "bbport_dlss.dll and nvngx_dlss.dll are not installed");
    if (!dlls->dlss_configured || !instance_ok) return fail(v, "the NVIDIA driver lacks what DLSS needs (update it)");
    uint32_t n = 0;
    const VkExtensionProperties *required = NULL;
    if (!dlls->dlss->DeviceExtensions(instance, device, &n, &required))
        return fail(v, "this GPU or driver does not support DLSS (GeForce RTX needed)");
    for (uint32_t i = 0; i < n; ++i)
        if (!has_extension(device, required[i].extensionName))
            return fail(v, "needs the Vulkan extension %s", required[i].extensionName);
    pass(v);
}
#endif

static int upscalers_mode(void) {
    const char *extensions[MAX_INSTANCE_EXTENSIONS];
    uint32_t extension_count = 0;
#ifdef _WIN32
    Dlls dlls;
    load_upscaler_dlls(&dlls, extensions, &extension_count);
#endif
    const VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "bbport upscaler probe",
        .apiVersion = VK_API_VERSION_1_3,
    };
    VkInstanceCreateInfo create = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
        .enabledExtensionCount = extension_count,
        .ppEnabledExtensionNames = extensions,
    };
    int instance_ok = 1;
    VkInstance instance = VK_NULL_HANDLE;
    if (vkCreateInstance(&create, NULL, &instance) != VK_SUCCESS) {
        /* An extension of DLSS or XeSS is missing: the other upscalers still get their answer. */
        instance_ok = 0;
        create.enabledExtensionCount = 0;
        if (vkCreateInstance(&create, NULL, &instance) != VK_SUCCESS) {
            fputs("GPU upscalers: cannot create Vulkan instance\n", stderr);
            return 1;
        }
    }
    const char *error = NULL;
    const VkPhysicalDevice device = select_device(instance, &error);
    if (!device) {
        fprintf(stderr, "GPU upscalers: %s\n", error);
        vkDestroyInstance(instance, NULL);
        return 1;
    }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(device, &props);

    VkPhysicalDeviceShaderMixedFloatDotProductFeaturesVALVE mixed = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_MIXED_FLOAT_DOT_PRODUCT_FEATURES_VALVE};
    VkPhysicalDeviceCooperativeMatrixFeaturesKHR matrix = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COOPERATIVE_MATRIX_FEATURES_KHR, .pNext = &mixed};
    VkPhysicalDeviceComputeShaderDerivativesFeaturesKHR derivatives = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COMPUTE_SHADER_DERIVATIVES_FEATURES_KHR, .pNext = &matrix};
    VkPhysicalDeviceVulkan13Features vk13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &derivatives};
    VkPhysicalDeviceVulkan12Features vk12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &vk13};
    VkPhysicalDeviceMutableDescriptorTypeFeaturesEXT mutable_type = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MUTABLE_DESCRIPTOR_TYPE_FEATURES_EXT, .pNext = &vk12};
    VkPhysicalDeviceFeatures2 features = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
                                          .pNext = &mutable_type};
    vkGetPhysicalDeviceFeatures2(device, &features);
    const int derivatives_ok = has_extension(device, "VK_KHR_compute_shader_derivatives") &&
                               derivatives.computeDerivativeGroupLinear;
    const int matrix_ok = has_extension(device, "VK_KHR_cooperative_matrix") && matrix.cooperativeMatrix;
    const int mixed_ok = has_extension(device, "VK_VALVE_shader_mixed_float_dot_product") &&
                         mixed.shaderMixedFloatDotProductFloat16AccFloat32;

    UpscalerVerdict fsr3, fsr4, fsr411, dlss, xess;
    pass(&fsr3);
    if (!features.features.shaderStorageImageWriteWithoutFormat)
        fail(&fsr3, "the GPU lacks shaderStorageImageWriteWithoutFormat");
    /* vk_instance.h: IsFsr4Int8Supported. */
    pass(&fsr4);
    if (!(vk12.shaderFloat16 && vk12.shaderInt8 && features.features.shaderInt16 &&
          vk13.shaderIntegerDotProduct && derivatives_ok &&
          features.features.shaderStorageImageExtendedFormats))
        fail(&fsr4, "the GPU or driver lacks the INT8 features FSR 4 needs");
    if (fsr4.ok) {
        /* RDNA3 and newer expose cooperative matrices (WMMA); older AMD GPUs run the INT8 model in
         * plain shaders. The name also catches RDNA1/2 (RX 5000/6000) and Vega if a driver exposes
         * matrices anyway. */
        const int amd = props.vendorID == 0x1002;
        const int old_name = strstr(props.deviceName, "RX 6") || strstr(props.deviceName, "RX 5") ||
                             strstr(props.deviceName, "Vega");
        if (amd && (!matrix_ok || old_name))
            snprintf(fsr4.reason, sizeof fsr4.reason, "may be slow on this GPU");
    }
    /* IsFsr411Supported: FSR 4 INT8 plus VK_VALVE_shader_mixed_float_dot_product, which only Mesa
     * exposes today. The reason stays plain: the launcher shows it, and no Windows driver reports it. */
    pass(&fsr411);
    if (!fsr4.ok) fail(&fsr411, "%s", fsr4.reason);
    else if (!mixed_ok) fail(&fsr411, "needs a Vulkan extension that no Windows driver is known to support yet");
#ifdef _WIN32
    verdict_dlss(&dlls, &props, instance, device, instance_ok, &dlss);
    verdict_xess(&dlls, instance, device, instance_ok, &xess);
    if (xess.ok && !mutable_type.mutableDescriptorType)
        fail(&xess, "needs the Vulkan feature mutableDescriptorType");
    if (xess.ok && !features.features.shaderStorageImageWriteWithoutFormat)
        fail(&xess, "needs the Vulkan feature shaderStorageImageWriteWithoutFormat");
#else
    (void)instance_ok;
    (void)mutable_type;
    fail(&dlss, "DLSS is built for Windows only");
    fail(&xess, "XeSS is built for Windows only");
#endif
    const struct { const char *name; const UpscalerVerdict *verdict; } rows[] = {
        {"fsr4", &fsr4}, {"fsr411", &fsr411}, {"fsr3", &fsr3}, {"taa", &fsr3},
        {"dlss", &dlss}, {"xess", &xess},
    };
    for (size_t i = 0; i < sizeof rows / sizeof rows[0]; ++i) {
        const UpscalerVerdict *v = rows[i].verdict;
        if (v->ok && !v->reason[0]) printf("UPSCALER %s supported\n", rows[i].name);
        else printf("UPSCALER %s %s: %s\n", rows[i].name, v->ok ? "supported" : "unsupported", v->reason);
    }
    fflush(stdout);
    vkDestroyInstance(instance, NULL);
    return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "--upscalers")) {
        return upscalers_mode();
    }
    if (argc > 1 && !strcmp(argv[1], "--read-input")) {
        return read_input(argc > 2 ? argv[2] : "any");
    }
    if (argc > 1 && !strcmp(argv[1], "--gamepads")) {
        return list_gamepads();
    }
    if (argc > 1 && !strcmp(argv[1], "--displays")) {
        return list_displays();
    }
    const int live_mode = argc > 1 && !strcmp(argv[1], "--live-resolution");
    const VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "bbport scene scaling probe",
        .apiVersion = VK_API_VERSION_1_3,
    };
    const VkInstanceCreateInfo create = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance instance = VK_NULL_HANDLE;
    if (vkCreateInstance(&create, NULL, &instance) != VK_SUCCESS) {
        fputs("GPU scene scaling: cannot create Vulkan instance\n", stderr);
        return 1;
    }
    const char *error = NULL;
    const VkPhysicalDevice selected = select_device(instance, &error);
    if (!selected) {
        fprintf(stderr, "GPU scene scaling: %s\n", error);
        vkDestroyInstance(instance, NULL);
        return 1;
    }
    if (live_mode) {
        printf("%d\n", live_resolution_suits(selected));
        vkDestroyInstance(instance, NULL);
        return 0;
    }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(selected, &props);
    const struct { VkFormat format; const char *name; } formats[] = {
        {VK_FORMAT_R8G8B8A8_UNORM, "RGBA8"},
        {VK_FORMAT_R8G8B8A8_SRGB, "RGBA8 sRGB"},
        {VK_FORMAT_B10G11R11_UFLOAT_PACK32, "B10G11R11"},
        {VK_FORMAT_R16G16B16A16_SFLOAT, "RGBA16F"},
        {VK_FORMAT_D32_SFLOAT_S8_UINT, "D32S8"},
    };
    int supported = 1;
    for (size_t i = 0; i < sizeof(formats) / sizeof(formats[0]); ++i) {
        VkFormatProperties features;
        vkGetPhysicalDeviceFormatProperties(selected, formats[i].format, &features);
        const VkFormatFeatureFlags required = VK_FORMAT_FEATURE_BLIT_SRC_BIT |
                                              VK_FORMAT_FEATURE_BLIT_DST_BIT;
        if ((features.optimalTilingFeatures & required) != required) {
            fprintf(stderr, "GPU scene scaling: %s lacks blit support for %s\n",
                    props.deviceName, formats[i].name);
            supported = 0;
        }
    }
    if (supported) fprintf(stderr, "GPU scene scaling: %s supports live presets\n", props.deviceName);
    vkDestroyInstance(instance, NULL);
    return supported ? 0 : 1;
}
