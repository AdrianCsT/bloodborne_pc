/* libScePad on SDL3 gamepads, the keyboard and the mouse. SDL events are pumped by the window
 * thread (gpu/shim/window.cpp); here state is only sampled.
 *
 * Keyboard layout (also with a gamepad connected: both drive the game):
 *   WASD left stick, arrow keys right stick, Space Cross, LShift Circle,
 *   E Square, Q Triangle, 1 L1, 3 R1, R L2, F R2, Z L3, C R3,
 *   Enter Options, Tab left touchpad, Backspace right touchpad,
 *   IJKL d-pad (I up, K down, J left, L right).
 *
 * Mouse (issue #5; the camera hook and the stick fallback follow Ryansousa10/bloodborne_windows_mouse_and_keyboard,
 * commit c650c2e, GPL-2.0-or-later): while the game window has focus and the settings menu and the text
 * dialog are closed, the window holds the mouse in relative mode (gpu/shim/window.cpp). Its motion turns the
 * camera through a hook in the game's own camera code (runtime_camhook.c), by exact angles as PC games do;
 * without the hook (another game version, not Windows) it acts as the right stick. Its buttons and wheel are
 * inputs of key.<input>= lines, next to keys (see parse_input).
 *
 * Stick neutral: SDL exposes no way to read a pad's calibration and some clones report a
 * biased neutral (a Switch-style pad was seen returning both sticks at a constant ~ +/-16380).
 * The neutral of each axis is taken from a quiet window after the pad opens (SDL returns zero
 * until the first report arrives, so those samples are skipped) and subtracted when all four axes
 * are biased; otherwise (a genuine pad, perhaps opened with a stick held) the neutral is 0. Until
 * it is decided the sticks read centred (a biased clone would read as deflected). A pad opened
 * with a stick held past the bound is taken as genuine until it reconnects: recalibrating while a
 * player holds a stick steady would be worse.
 *
 * Travel: such a clone also uses only part of SDL's -32768..32767 span (its neutral sits in the
 * middle of one half), so reading the axis as -128..127 would reach only half deflection and a
 * full push would never run. Each axis is instead scaled by its own travel: each side of a biased
 * axis' neutral starts from the neutral's magnitude, or the room SDL's range leaves on that side
 * when that is less (a neutral near +20000 has about 12767 toward +32767), and is refined by the
 * largest push seen per direction, so both directions reach full deflection even when the two
 * travels differ by a few percent (that
 * difference is what makes one direction run and the other only walk). A genuine pad keeps the
 * plain -32768..32767 -> -128..127 read. The stick is then converted as shadPS4 does, with an
 * inner/outer dead zone (BB_PAD_DEADZONE, default 5; BB_PAD_DEADZONE_OUTER, default 127) mapping
 * the axis' travel up to full deflection. BB_PAD_CENTER=lx,ly,rx,ry overrides the neutral,
 * BB_PAD_CENTER_CAL=0 disables the measurement. */
#define _GNU_SOURCE
#include "runtime.h"
#include "gpu/bbgpu.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <SDL3/SDL.h>
#include <sys/stat.h>

#define ERR_INVALID_ARG ((int32_t)0x80920001)
#define ERR_INVALID_HANDLE ((int32_t)0x80920003)
#define ERR_ALREADY_OPENED ((int32_t)0x80920004)
#define ERR_NOT_INITIALIZED ((int32_t)0x80920005)
#define PAD_HANDLE 1

enum {
    BTN_L3=0x2, BTN_R3=0x4, BTN_OPTIONS=0x8, BTN_UP=0x10, BTN_RIGHT=0x20, BTN_DOWN=0x40, BTN_LEFT=0x80,
    BTN_L2=0x100, BTN_R2=0x200, BTN_L1=0x400, BTN_R1=0x800, BTN_TRIANGLE=0x1000, BTN_CIRCLE=0x2000,
    BTN_CROSS=0x4000, BTN_SQUARE=0x8000, BTN_TOUCHPAD=0x100000,
};
typedef struct { uint16_t x, y; uint8_t id, reserve[3]; } PadTouch;
typedef struct {
    uint32_t buttons;
    uint8_t left_x, left_y, right_x, right_y;
    uint8_t l2, r2, analog_padding[2];
    float orientation[4], acceleration[3], angular_velocity[3];
    uint8_t touch_count, touch_reserve[3];
    uint32_t touch_held_time;
    PadTouch touches[2];
    uint8_t connected, pad0[3];
    uint64_t timestamp;
    uint8_t extension[16];
    uint8_t connected_count, reserve[2], unique_length, unique[12];
} PadData;
typedef struct {
    float pixel_density; uint16_t resolution_x, resolution_y;
    uint8_t dead_zone_left, dead_zone_right, connection_type, connected_count;
    uint8_t connected, pad[3];
    int32_t device_class;
    uint8_t reserve[8];
} ControllerInfo;
_Static_assert(sizeof(PadData)==120,"OrbisPadData layout");
_Static_assert(sizeof(PadTouch)==8,"OrbisPadTouch layout");
_Static_assert(__builtin_offsetof(PadData,touches)==60,"OrbisPadData touch offset");
_Static_assert(__builtin_offsetof(PadData,timestamp)==80,"OrbisPadData timestamp offset");
_Static_assert(sizeof(ControllerInfo)==28,"OrbisPadControllerInformation layout");

static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static int initialized, opened, sdl_ready;
static SDL_Gamepad *gamepad;
static size_t reads;
static uint8_t connected_count;

/* Per-controller stick neutral (see the file header): the value each axis holds while the
 * sticks are untouched, taken from a quiet window after the pad opens. Works for any pad; a
 * genuine one settles at 0 and the subtraction is a no-op. */
#define PAD_CAL_QUIET 512              /* an axis is quiet when it moved no more than this */
#define PAD_CAL_QUIET_SAMPLES 8
#define PAD_CAL_TIMEOUT_US 3000000u    /* after this, an all-zero reading is accepted as the neutral */
#define PAD_CAL_BIAS 8192              /* |neutral| past this: a biased neutral and a reduced travel */
#define PAD_CAL_BIAS_MAX 22000         /* ...but a stick pushed to its stop (a full diagonal reads ~23170) is no neutral */
#define PAD_CAL_REFINE 90              /* % of the base travel a push must reach to count as the full scale */
static int cal_enabled=-1, pad_deadzone=-1, pad_deadzone_outer=-1, cal_manual;
static int cal_have_center, cal_started, cal_quiet_count;
static int cal_center[4], cal_prev[4], cal_manual_center[4];
static int cal_base[4][2];             /* travel toward -/+ of a biased axis; 0: normal pad */
static int cal_max[4][2];              /* largest push seen per direction on a biased axis */
static uint64_t cal_since;

static uint64_t now_us(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000000u+(uint64_t)t.tv_nsec/1000u; }
static uint8_t trigger(int16_t v) { int x=v>>7; return (uint8_t)(x<0 ? 0 : x>255 ? 255 : x); }
static void cal_read_env(void) {
    const char *v=getenv("BB_PAD_CENTER_CAL"); cal_enabled=!(v && *v=='0');
    /* Inner/outer dead zone, as shadPS4's analog_deadzone: [inner, outer] maps linearly to the
     * full deflection, so an axis with a reduced travel (outer below 127) reaches full. Both are
     * held to the axis' range (inner 0..126, outer 0..127): an outer past 127 would leave every
     * push short of full deflection. */
    v=getenv("BB_PAD_DEADZONE"); pad_deadzone=v && *v ? atoi(v) : 5;
    if (pad_deadzone<0) pad_deadzone=0; else if (pad_deadzone>126) pad_deadzone=126;
    v=getenv("BB_PAD_DEADZONE_OUTER"); pad_deadzone_outer=v && *v ? atoi(v) : 127;
    if (pad_deadzone_outer>127) pad_deadzone_outer=127;
    if (pad_deadzone_outer<=pad_deadzone) pad_deadzone_outer=pad_deadzone<127 ? pad_deadzone+1 : 127;
    v=getenv("BB_PAD_CENTER");
    if (v && *v && sscanf(v,"%d,%d,%d,%d",&cal_manual_center[0],&cal_manual_center[1],
                          &cal_manual_center[2],&cal_manual_center[3])==4) {
        cal_manual=1;
        printf("Runtime: pad center (BB_PAD_CENTER): lx=%d ly=%d rx=%d ry=%d\n",
               cal_manual_center[0],cal_manual_center[1],cal_manual_center[2],cal_manual_center[3]);
    }
}
static void cal_load(void) {
    static int loaded;
    if (loaded) return;
    loaded=1;
    cal_read_env();
}
static void cal_set_base(void) {
    int biased=0;
    for (int i=0;i<4;++i) {
        const int c=cal_center[i], a=abs(c);
        /* A biased axis spans |neutral| to each side, as far as SDL's range reaches: a neutral
         * near an end of the range leaves little travel on that side. */
        const int toward_minus=32768+c, toward_plus=32767-c;
        const int on=a>=PAD_CAL_BIAS;
        cal_base[i][0]=on ? (a<toward_minus ? a : toward_minus) : 0;
        cal_base[i][1]=on ? (a<toward_plus ? a : toward_plus) : 0;
        for (int dir=0;dir<2;++dir) if (on && cal_base[i][dir]<1) cal_base[i][dir]=1;
        cal_max[i][0]=cal_max[i][1]=0;
        biased|=on;
    }
    if (biased)
        printf("Runtime: pad travel (biased neutral, per-side scaling, toward -/+): lx=%d/%d ly=%d/%d rx=%d/%d ry=%d/%d\n",
               cal_base[0][0]?cal_base[0][0]:32768,cal_base[0][1]?cal_base[0][1]:32767,
               cal_base[1][0]?cal_base[1][0]:32768,cal_base[1][1]?cal_base[1][1]:32767,
               cal_base[2][0]?cal_base[2][0]:32768,cal_base[2][1]?cal_base[2][1]:32767,
               cal_base[3][0]?cal_base[3][0]:32768,cal_base[3][1]?cal_base[3][1]:32767);
}
/* Full-scale travel of one direction. A biased axis starts from the room its neutral leaves on
 * that side and is refined by the largest push seen (once that push is close to the base travel),
 * so a direction whose physical travel is a few percent shorter still reaches full deflection. */
static int cal_travel(int axis,int dir) {
    const int base=cal_base[axis][dir];
    if (!base) return 32768;
    return cal_max[axis][dir]*100>=base*PAD_CAL_REFINE ? cal_max[axis][dir] : base;
}
static void cal_reset(void) {
    cal_load();
    cal_started=0; cal_quiet_count=0; cal_since=0; memset(cal_prev,0,sizeof cal_prev);
    if (cal_manual) { memcpy(cal_center,cal_manual_center,sizeof cal_center); cal_have_center=1; }
    else if (!cal_enabled) { memset(cal_center,0,sizeof cal_center); cal_have_center=1; }
    else { cal_have_center=0; memset(cal_center,0,sizeof cal_center); memset(cal_base,0,sizeof cal_base); }
    if (cal_have_center) cal_set_base();
}
/* The value each axis holds while the sticks are untouched (SDL's pre-report zeros are skipped);
 * once it is known, the largest push seen per direction on a biased axis (see cal_travel). */
static void cal_sample(const int16_t raw[4]) {
    if (cal_have_center) {
        for (int i=0;i<4;++i) {
            if (!cal_base[i][0]) continue;
            const int d=raw[i]-cal_center[i], dir=d>=0, travel=dir ? d : -d;
            if (travel>cal_max[i][dir]) cal_max[i][dir]=travel;
        }
        return;
    }
    int allzero=1; for (int i=0;i<4;++i) if (raw[i]) allzero=0;
    int quiet=1; for (int i=0;i<4;++i) if (cal_started && abs(raw[i]-cal_prev[i])>PAD_CAL_QUIET) quiet=0;
    const uint64_t now=now_us();
    if (!cal_started) { cal_started=1; cal_since=now; }
    if (allzero && now-cal_since<PAD_CAL_TIMEOUT_US) quiet=0;
    if (quiet) {
        if (++cal_quiet_count>=PAD_CAL_QUIET_SAMPLES) {
            /* Only the clone pattern (all four axes biased) is taken as the neutral: a genuine pad
             * opened with a stick held steady would otherwise keep that push as its neutral. */
            int biased=0, pushed=0;
            for (int i=0;i<4;++i) { biased+=abs(raw[i])>=PAD_CAL_BIAS; pushed+=abs(raw[i])>PAD_CAL_BIAS_MAX; }
            const int clone=biased==4 && !pushed;
            for (int i=0;i<4;++i) cal_center[i]=clone ? raw[i] : 0;
            cal_have_center=1;
            if (clone)
                printf("Runtime: pad neutral: lx=%d ly=%d rx=%d ry=%d\n",cal_center[0],cal_center[1],cal_center[2],cal_center[3]);
            else if (biased)
                printf("Runtime: pad neutral 0 (%d of 4 axes off-center at open: a stick held, not a biased pad)\n",biased);
            cal_set_base();
        }
    } else cal_quiet_count=0;
    for (int i=0;i<4;++i) cal_prev[i]=raw[i];
}
/* One stick axis in the PS4 0..255 scale. The neutral is subtracted and the axis' own travel
 * (cal_travel) is read as -128..127, so a full push reaches full deflection in both directions
 * whatever part of SDL's range the pad uses; the inner/outer dead zone (BB_PAD_DEADZONE /
 * BB_PAD_DEADZONE_OUTER) then maps that travel to the full deflection, as shadPS4 does. */
static uint8_t stick_axis(int axis,int16_t raw) {
    const int d=raw-cal_center[axis];
    const int travel=cal_travel(axis,d>=0 ? 1 : 0);
    int v=travel>0 ? (int)((long long)d*128/travel) : 0;
    if (v>127) v=127; else if (v<-128) v=-128;
    const int mag=abs(v);
    if (mag<=pad_deadzone || pad_deadzone>=pad_deadzone_outer) v=0;
    else {
        int scaled=(int)(128.0*(mag-pad_deadzone)/(float)(pad_deadzone_outer-pad_deadzone));
        if (scaled>128) scaled=128;
        v = v>=0 ? scaled : -scaled;
    }
    int x=v+128;
    return (uint8_t)(x<0 ? 0 : x>255 ? 255 : x);
}
static uint16_t touch_axis(float v, int max) {
    return (uint16_t)(v<=0.0f ? 0 : v>=1.0f ? max : (int)(v*max+0.5f));
}
static void touch_click(PadData *d, int right) {
    d->buttons|=BTN_TOUCHPAD;
    d->touch_count=1;
    d->touches[0]=(PadTouch){.x=right ? 1440 : 480,.y=471,.id=0};
}

/* BB_GAMEPAD (the launcher's controller choice): its SDL GUID, or part of its name. Issue #15:
 * wheels and other controllers connected for good came first. */
static const char *preferred_gamepad(void) {
    static const char *want; static int read;
    if (!read) { want=getenv("BB_GAMEPAD"); if (want && !*want) want=NULL; read=1; }
    return want;
}
static int is_preferred(SDL_JoystickID id, const char *want) {
    char guid[33];
    SDL_GUIDToString(SDL_GetGamepadGUIDForID(id),guid,sizeof guid);
    const char *name=SDL_GetGamepadNameForID(id);
    return !strcasecmp(guid,want) || (name && strcasestr(name,want));
}
/* The chosen gamepad, else the first while it is not connected (checked again every second, it
 * is taken as soon as it connects); called under lock. */
static SDL_Gamepad *current_gamepad(void) {
    static int on_preferred; static uint64_t last_scan;
    if (!sdl_ready) sdl_ready = SDL_WasInit(SDL_INIT_GAMEPAD) ? 1 : SDL_InitSubSystem(SDL_INIT_GAMEPAD) ? 1 : -1;
    if (sdl_ready<0) return NULL;
    if (gamepad && !SDL_GamepadConnected(gamepad)) { SDL_CloseGamepad(gamepad); gamepad=NULL; }
    const char *want=preferred_gamepad();
    const uint64_t now=now_us();
    if (!gamepad || (want && !on_preferred && now-last_scan>1000000)) {
        last_scan=now;
        int count=0, pick=-1;
        SDL_JoystickID *ids=SDL_GetGamepads(&count);
        for (int i=0; want && ids && i<count && pick<0; ++i) if (is_preferred(ids[i],want)) pick=i;
        if (pick<0 && !gamepad && ids && count>0) pick=0;
        if (pick>=0 && (!gamepad || SDL_GetGamepadID(gamepad)!=ids[pick])) {
            if (gamepad) SDL_CloseGamepad(gamepad);
            gamepad=SDL_OpenGamepad(ids[pick]);
            cal_reset(); /* a new pad has its own neutral */
            on_preferred=want && gamepad && is_preferred(ids[pick],want);
            if (gamepad) {
                ++connected_count;
                printf("Runtime: gamepad connected: %s%s\n",SDL_GetGamepadName(gamepad),
                       !want ? "" : on_preferred ? " (the chosen one)" : " (the chosen one is not connected)");
            }
        }
        SDL_free(ids);
    }
    return gamepad;
}
/* bbport (frame stats): the game's libc heap, read every 5 s from a game thread (it reads the pad)
 * with libc.prx's malloc_stats (export stub at libc.prx+0x1f8c0; malloc_stats_fast returns 1 in this libc, the module at image +0x56e0000):
 * in use now and at most, and what it took from the system. A heap that keeps growing is a leak. */
typedef struct { uint16_t size, version; uint32_t reserved; uint64_t max_system, system, max_in_use, in_use; } MallocManagedSize;
static void report_guest_heap(void) {
    static int enabled=-1; static uint64_t last;
    if (enabled<0) enabled=getenv("BB_FRAME_STATS")!=NULL;
    const uint64_t now=now_us();
    if (!enabled || now-last<5000000) return;
    last=now;
    const uint8_t *stub=(const uint8_t *)(0x800000000ull+0x56e0000+0x1f8c0);
    if (stub[0]!=0xff || stub[1]!=0x25) return; /* another libc */
    ABI int (*stats)(MallocManagedSize *)=(ABI int (*)(MallocManagedSize *))(uintptr_t)stub;
    MallocManagedSize m={.size=sizeof(m),.version=1};
    const int result=stats(&m);
    if (result!=0) { static int told; if (!told++) printf("Guest heap: malloc_stats returned %#x\n",(unsigned)result); return; }
    {
        printf("Guest heap: %.1f MB in use (most %.1f), %.1f MB from the system (most %.1f)\n",
               m.in_use/1048576.0,m.max_in_use/1048576.0,m.system/1048576.0,m.max_system/1048576.0);
    }
}
/* Controls: what each PS4 input is bound to. Defaults below; bbport.ini (BB_CONFIG) lines
 * key.<input>=<SDL key names> and pad.<input>=<SDL gamepad button names>, comma-separated,
 * replace an input's binding (empty: unbound). Inputs: the buttons (cross ... right, touchpad =
 * a left-side click, touchpad_right), and on the keyboard the sticks: move_* (left), look_*
 * (right). Gamepad names as SDL's: a b x y back start leftstick rightstick leftshoulder
 * rightshoulder dpup dpdown dpleft dpright touchpad misc1 paddle1-4, plus lefttrigger and
 * righttrigger. The keyboard works next to a gamepad (the Steam Deck always has one): its buttons
 * add to the gamepad's, a held move/look key moves the stick all the way.
 *
 * Each name in a key.<input>= line is a keyboard key (an SDL scancode name: "E", "Left Ctrl", "Space"),
 * or a mouse input: "Mouse Left", "Mouse Right", "Mouse Middle", "Mouse X1", "Mouse X2" (held while the
 * button is) or "Wheel Up", "Wheel Down" (a 60 ms press per step). Any of them may start with
 * "Shift+", "Ctrl+" or "Alt+" (either side's key): then it counts only while that modifier is held, and a
 * plain input yields to a combination on the same key or button that is held, so "Mouse Left" is R1
 * alone and "Shift+Mouse Left" R2 alone. Other lines of the file: mouse_camera (1), mouse_sensitivity
 * (1.0: 0.022 degrees a count, the scale of Source games), mouse_invert_y (0), mouse_no_auto_rotation
 * (0; 1: while the camera hook is installed the game does not turn the camera by itself as the
 * character walks. Off by default: the hook is installed for every player, and gamepad players
 * keep the game's own camera). */
enum {
    IN_CROSS, IN_CIRCLE, IN_SQUARE, IN_TRIANGLE, IN_L1, IN_R1, IN_L2, IN_R2, IN_L3, IN_R3,
    IN_OPTIONS, IN_TOUCHPAD, IN_TOUCHPAD_RIGHT, IN_UP, IN_DOWN, IN_LEFT, IN_RIGHT,
    IN_MOVE_UP, IN_MOVE_DOWN, IN_MOVE_LEFT, IN_MOVE_RIGHT, IN_LOOK_UP, IN_LOOK_DOWN, IN_LOOK_LEFT,
    IN_LOOK_RIGHT, IN_COUNT
};
static const char *const input_names[IN_COUNT]={
    "cross","circle","square","triangle","l1","r1","l2","r2","l3","r3","options","touchpad",
    "touchpad_right","up","down","left","right","move_up","move_down","move_left","move_right",
    "look_up","look_down","look_left","look_right",
};
static const uint32_t input_buttons[IN_COUNT]={
    BTN_CROSS,BTN_CIRCLE,BTN_SQUARE,BTN_TRIANGLE,BTN_L1,BTN_R1,BTN_L2,BTN_R2,BTN_L3,BTN_R3,
    BTN_OPTIONS,BTN_TOUCHPAD,0,BTN_UP,BTN_DOWN,BTN_LEFT,BTN_RIGHT,
};
#define MAX_BIND 4
enum { PAD_LEFT_TRIGGER=SDL_GAMEPAD_BUTTON_COUNT, PAD_RIGHT_TRIGGER }; /* triggers as buttons */
/* A keyboard key, a mouse button or a wheel step, with the modifiers that must be held. */
enum { KIND_KEY, KIND_MOUSE, KIND_WHEEL };
enum { BIND_SHIFT=1, BIND_CTRL=2, BIND_ALT=4 };
typedef struct { uint8_t kind, mods; int16_t code; } KeyInput; /* SDL scancode, SDL button, wheel step +1 up / -1 down */
typedef struct { int key_count, pad_count; KeyInput keys[MAX_BIND]; int pad[MAX_BIND]; } Binding;
static Binding bindings[IN_COUNT];
static int bindings_ready;
/* The mouse settings of bbport.ini (see the comment above). */
typedef struct { int mouse_camera, invert_y, no_auto_rotation; float sensitivity; } MouseSettings;
static const MouseSettings mouse_defaults={1,0,0,1.0f};
static MouseSettings kbm={1,0,0,1.0f};

/* The Dark Souls III layout of the reference (its docs/KEYBOARD_MOUSE.md, "Default keys"); the launcher's CONTROLS
 * table (launcher/bbport_controls.py) is the same, and a test keeps them equal. The runtime has no walk input, so
 * Left Alt (walk in Dark Souls III) is not bound. */
static void bind_defaults(void) {
#define K(input,key) {input,KIND_KEY,0,SDL_SCANCODE_##key}
#define M(input,mods,button) {input,KIND_MOUSE,mods,SDL_BUTTON_##button}
#define W(input,mods,step) {input,KIND_WHEEL,mods,step}
    static const struct { int input; uint8_t kind, mods; int16_t code; } keys[]={
        K(IN_CROSS,E), K(IN_CROSS,RETURN), K(IN_CIRCLE,SPACE), K(IN_CIRCLE,ESCAPE),
        K(IN_SQUARE,R), K(IN_TRIANGLE,F),
        M(IN_L1,0,RIGHT), M(IN_R1,0,LEFT),
        M(IN_L2,BIND_SHIFT,RIGHT), K(IN_L2,LCTRL), M(IN_R2,BIND_SHIFT,LEFT),
        K(IN_L3,C), K(IN_R3,Q), M(IN_R3,0,MIDDLE),
        K(IN_OPTIONS,TAB), K(IN_TOUCHPAD,G), K(IN_TOUCHPAD_RIGHT,BACKSPACE),
        K(IN_UP,UP), W(IN_UP,0,1), K(IN_DOWN,DOWN), W(IN_DOWN,0,-1),
        K(IN_LEFT,LEFT), W(IN_LEFT,BIND_SHIFT,-1), K(IN_RIGHT,RIGHT), W(IN_RIGHT,BIND_SHIFT,1),
        K(IN_MOVE_UP,W), K(IN_MOVE_DOWN,S), K(IN_MOVE_LEFT,A), K(IN_MOVE_RIGHT,D),
        K(IN_LOOK_UP,I), K(IN_LOOK_DOWN,K), K(IN_LOOK_LEFT,J), K(IN_LOOK_RIGHT,L),
    };
#undef K
#undef M
#undef W
    static const struct { int input, button; } pads[]={
        {IN_CROSS,SDL_GAMEPAD_BUTTON_SOUTH}, {IN_CIRCLE,SDL_GAMEPAD_BUTTON_EAST},
        {IN_SQUARE,SDL_GAMEPAD_BUTTON_WEST}, {IN_TRIANGLE,SDL_GAMEPAD_BUTTON_NORTH},
        {IN_L1,SDL_GAMEPAD_BUTTON_LEFT_SHOULDER}, {IN_R1,SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER},
        {IN_L2,PAD_LEFT_TRIGGER}, {IN_R2,PAD_RIGHT_TRIGGER},
        {IN_L3,SDL_GAMEPAD_BUTTON_LEFT_STICK}, {IN_R3,SDL_GAMEPAD_BUTTON_RIGHT_STICK},
        {IN_OPTIONS,SDL_GAMEPAD_BUTTON_START},
        {IN_TOUCHPAD,SDL_GAMEPAD_BUTTON_BACK}, {IN_TOUCHPAD,SDL_GAMEPAD_BUTTON_TOUCHPAD},
        {IN_UP,SDL_GAMEPAD_BUTTON_DPAD_UP}, {IN_DOWN,SDL_GAMEPAD_BUTTON_DPAD_DOWN},
        {IN_LEFT,SDL_GAMEPAD_BUTTON_DPAD_LEFT}, {IN_RIGHT,SDL_GAMEPAD_BUTTON_DPAD_RIGHT},
    };
    memset(bindings,0,sizeof bindings);
    for (size_t i=0;i<sizeof(keys)/sizeof(*keys);++i) {
        Binding *b=&bindings[keys[i].input];
        b->keys[b->key_count++]=(KeyInput){keys[i].kind,keys[i].mods,keys[i].code};
    }
    kbm=mouse_defaults;
    for (size_t i=0;i<sizeof(pads)/sizeof(*pads);++i) {
        Binding *b=&bindings[pads[i].input]; b->pad[b->pad_count++]=pads[i].button;
    }
}
static int pad_button_from_name(const char *name) {
    if (!SDL_strcasecmp(name,"lefttrigger")) return PAD_LEFT_TRIGGER;
    if (!SDL_strcasecmp(name,"righttrigger")) return PAD_RIGHT_TRIGGER;
    const SDL_GamepadButton b=SDL_GetGamepadButtonFromString(name);
    return b==SDL_GAMEPAD_BUTTON_INVALID ? -1 : (int)b;
}
/* One name of a key.<input>= line: [Shift+][Ctrl+][Alt+] and then a mouse input or an SDL key name. */
static int parse_input(const char *s, KeyInput *in) {
    in->mods=0;
    for (;;) {
        if (!SDL_strncasecmp(s,"shift+",6)) { in->mods|=BIND_SHIFT; s+=6; }
        else if (!SDL_strncasecmp(s,"ctrl+",5)) { in->mods|=BIND_CTRL; s+=5; }
        else if (!SDL_strncasecmp(s,"alt+",4)) { in->mods|=BIND_ALT; s+=4; }
        else break;
    }
    static const struct { const char *name; uint8_t kind; int16_t code; } mouse[]={
        {"Mouse Left",KIND_MOUSE,SDL_BUTTON_LEFT}, {"Mouse Right",KIND_MOUSE,SDL_BUTTON_RIGHT},
        {"Mouse Middle",KIND_MOUSE,SDL_BUTTON_MIDDLE}, {"Mouse X1",KIND_MOUSE,SDL_BUTTON_X1},
        {"Mouse X2",KIND_MOUSE,SDL_BUTTON_X2}, {"Wheel Up",KIND_WHEEL,1}, {"Wheel Down",KIND_WHEEL,-1},
    };
    for (size_t i=0;i<sizeof(mouse)/sizeof(*mouse);++i)
        if (!SDL_strcasecmp(s,mouse[i].name)) { in->kind=mouse[i].kind; in->code=mouse[i].code; return 1; }
    const SDL_Scancode code=SDL_GetScancodeFromName(s);
    if (code==SDL_SCANCODE_UNKNOWN) return 0;
    in->kind=KIND_KEY; in->code=(int16_t)code;
    return 1;
}
/* NaN passes both comparisons, so a value that is not finite is the default (a NaN sensitivity corrupts the turn). */
static float clamp_setting(float v, float low, float high, float fallback) {
    return !isfinite(v) ? fallback : v<low ? low : v>high ? high : v;
}
/* mouse_camera / mouse_sensitivity / mouse_invert_y / mouse_no_auto_rotation. */
static void load_mouse_setting(const char *key, const char *value) {
    const float v=(float)atof(value);
    if (!strcmp(key,"mouse_camera")) kbm.mouse_camera=v!=0;
    else if (!strcmp(key,"mouse_sensitivity")) kbm.sensitivity=clamp_setting(v,0.01f,20.0f,mouse_defaults.sensitivity);
    else if (!strcmp(key,"mouse_invert_y")) kbm.invert_y=v!=0;
    else if (!strcmp(key,"mouse_no_auto_rotation")) kbm.no_auto_rotation=v!=0;
}
/* key.<input>= / pad.<input>= lines of the settings file, and the mouse_* settings. */
static void load_bindings(void) {
    bind_defaults();
    const char *path=getenv("BB_CONFIG");
    FILE *f=path ? fopen(path,"r") : NULL;
    if (!f) return;
    char line[512];
    while (fgets(line,sizeof line,f)) {
        const int keyboard=!strncmp(line,"key.",4), pad=!strncmp(line,"pad.",4);
        char *eq=strchr(line,'=');
        if (!eq) continue;
        if (!keyboard && !pad) {
            *eq=0;
            load_mouse_setting(line,eq+1);
            continue;
        }
        *eq=0;
        int input=-1;
        for (int i=0;i<IN_COUNT;++i) if (!strcmp(line+4,input_names[i])) input=i;
        if (input<0 || (pad && input>=IN_MOVE_UP)) { printf("Runtime: controls: unknown input %s\n",line); continue; }
        Binding *b=&bindings[input];
        if (keyboard) b->key_count=0; else b->pad_count=0;
        for (char *name=strtok(eq+1,",\r\n"); name; name=strtok(NULL,",\r\n")) {
            while (*name==' ') ++name;
            for (char *end=name+strlen(name); end>name && end[-1]==' ';) *--end=0;
            if (!*name) continue;
            if (keyboard) {
                KeyInput in;
                if (!parse_input(name,&in)) printf("Runtime: controls: unknown key \"%s\" for %s\n",name,line+4);
                else if (b->key_count<MAX_BIND) b->keys[b->key_count++]=in;
            } else {
                const int button=pad_button_from_name(name);
                if (button<0) printf("Runtime: controls: unknown gamepad button \"%s\" for %s\n",name,line+4);
                else if (b->pad_count<MAX_BIND) b->pad[b->pad_count++]=button;
            }
        }
    }
    fclose(f);
}
static void ensure_bindings(void) {
    if (bindings_ready) return;
    load_bindings();
    bindings_ready=1;
}
/* A mouse button or the wheel is bound to some input. */
static int mouse_inputs_bound(void) {
    for (int a=0;a<IN_COUNT;++a) for (int i=0;i<bindings[a].key_count;++i)
        if (bindings[a].keys[i].kind!=KIND_KEY) return 1;
    return 0;
}

/* The mouse buttons held or clicked since the last read (SDL_BUTTON_MASK), set by sample_host. */
static uint32_t mouse_buttons;
static int held_mods(const bool *k) {
    if (!k) return 0;
    return (k[SDL_SCANCODE_LSHIFT] || k[SDL_SCANCODE_RSHIFT] ? BIND_SHIFT : 0) |
           (k[SDL_SCANCODE_LCTRL] || k[SDL_SCANCODE_RCTRL] ? BIND_CTRL : 0) |
           (k[SDL_SCANCODE_LALT] || k[SDL_SCANCODE_RALT] ? BIND_ALT : 0);
}
/* The input's modifiers are held and no held combination on the same key or button takes
 * precedence over it: with Shift+Mouse Left bound, a click while Shift is down is not Mouse Left. */
static int mods_match(const KeyInput *in, int mods) {
    if ((in->mods & mods)!=in->mods) return 0;
    for (int a=0;a<IN_COUNT;++a) for (int i=0;i<bindings[a].key_count;++i) {
        const KeyInput *o=&bindings[a].keys[i];
        if (o->kind==in->kind && o->code==in->code && o->mods!=in->mods &&
            (o->mods & in->mods)==in->mods && (o->mods & mods)==o->mods) return 0;
    }
    return 1;
}
/* Wheel steps become short presses of their inputs, queued so that fast scrolling is not lost. */
#define PULSE_ON_US 60000
#define PULSE_GAP_US 50000
#define PULSE_QUEUE 4
static uint8_t pulse_queue[IN_COUNT];
static uint64_t pulse_until[IN_COUNT];
static void wheel_step(int direction, int mods) {
    for (int a=0;a<IN_COUNT;++a) for (int i=0;i<bindings[a].key_count;++i) {
        const KeyInput *in=&bindings[a].keys[i];
        if (in->kind==KIND_WHEEL && in->code==direction && mods_match(in,mods)) {
            if (pulse_queue[a]<PULSE_QUEUE) ++pulse_queue[a];
            break;
        }
    }
}
static int pulse_held(int input, uint64_t now) {
    if (now>=pulse_until[input] && pulse_queue[input]) {
        --pulse_queue[input];
        pulse_until[input]=now+PULSE_ON_US+PULSE_GAP_US;
    }
    return now+PULSE_GAP_US<pulse_until[input];
}
static int key_down(const bool *k, int input, uint64_t now) {
    const int mods=held_mods(k);
    for (int i=0;i<bindings[input].key_count;++i) {
        const KeyInput *in=&bindings[input].keys[i];
        if (in->kind==KIND_WHEEL || !mods_match(in,mods)) continue;
        if (in->kind==KIND_KEY ? k && k[in->code] : (mouse_buttons & SDL_BUTTON_MASK(in->code))!=0) return 1;
    }
    return pulse_held(input,now);
}
/* The bound gamepad buttons' state; triggers as their analog value. */
static int pad_value(SDL_Gamepad *g, int input) {
    int value=0;
    for (int i=0;i<bindings[input].pad_count;++i) {
        const int b=bindings[input].pad[i];
        const int v=b==PAD_LEFT_TRIGGER ? trigger(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_LEFT_TRIGGER))
                  : b==PAD_RIGHT_TRIGGER ? trigger(SDL_GetGamepadAxis(g,SDL_GAMEPAD_AXIS_RIGHT_TRIGGER))
                  : SDL_GetGamepadButton(g,(SDL_GamepadButton)b) ? 255 : 0;
        if (v>value) value=v;
    }
    return value;
}

/* key held for the negative / positive direction: the stick all the way, else the gamepad's. */
static uint8_t key_axis(uint8_t value, int negative, int positive) {
    return negative || positive ? (uint8_t)(128-(negative ? 128 : 0)+(positive ? 127 : 0)) : value;
}
static void apply_keyboard(PadData *d, const bool *k) {
    int held[IN_COUNT];
    const uint64_t now=now_us();
    for (int i=0;i<IN_COUNT;++i) held[i]=key_down(k,i,now);
    for (int i=IN_CROSS;i<=IN_RIGHT;++i)
        if (i!=IN_TOUCHPAD && i!=IN_TOUCHPAD_RIGHT && held[i]) d->buttons|=input_buttons[i];
    if (held[IN_TOUCHPAD]) touch_click(d,0);
    if (held[IN_TOUCHPAD_RIGHT]) touch_click(d,1);
    if (held[IN_L2]) d->l2=255;
    if (held[IN_R2]) d->r2=255;
    d->left_x=key_axis(d->left_x,held[IN_MOVE_LEFT],held[IN_MOVE_RIGHT]);
    d->left_y=key_axis(d->left_y,held[IN_MOVE_UP],held[IN_MOVE_DOWN]);
    d->right_x=key_axis(d->right_x,held[IN_LOOK_LEFT],held[IN_LOOK_RIGHT]);
    d->right_y=key_axis(d->right_y,held[IN_LOOK_UP],held[IN_LOOK_DOWN]);
}

/* The mouse camera. With the hook (runtime_camhook.c) the window thread turns the camera at once,
 * by 0.022 degrees a count x mouse_sensitivity (the scale of Source games, so their sensitivity
 * carries over); the game's pitch grows looking down. */
#define HOOK_RADIANS_PER_COUNT (0.022*3.14159265358979323846/180.0)
static void mouse_turn(float dx, float dy, float *pitch, float *yaw) {
    const double k=kbm.sensitivity*HOOK_RADIANS_PER_COUNT;
    *yaw=(float)(dx*k);
    *pitch=(float)(dy*k*(kbm.invert_y ? -1 : 1));
}
static void mouse_direct_turn(float dx, float dy) {
    float pitch, yaw;
    mouse_turn(dx,dy,&pitch,&yaw);
    runtime_camhook_turn(pitch,yaw);
}
/* The stick fallback, when the camera hook cannot be installed (another game version, not
 * Windows): the mouse as the right stick. The game turns the camera by the tilt, so the tilt
 * follows the mouse speed (MOUSE_FULL_COUNTS_S counts a second at sensitivity 1 tilts it fully),
 * averaged over MOUSE_SMOOTHING_MS because the game and the mouse sample at different rates
 * and divided by the time between pad reads, so the frame rate does not change it. Tilts start
 * at the game's deadzone (MOUSE_DEADZONE) so that the smallest motion still turns the camera. */
#define MOUSE_FULL_COUNTS_S 500.0f
#define MOUSE_SMOOTHING_MS 8.0f
#define MOUSE_DEADZONE 0.25f
typedef struct { float vx, vy; uint64_t last; } MouseStick; /* counts a second, smoothed; time of the last read */
static uint8_t stick_byte(float v) {
    const int x=128+(int)lroundf(v*(v<0 ? 128.0f : 127.0f));
    return (uint8_t)(x<0 ? 0 : x>255 ? 255 : x);
}
static void mouse_stick_step(MouseStick *s, PadData *d, float dx, float dy, uint64_t now) {
    float dt=s->last ? (float)(now-s->last)*1e-6f : 0.016f;
    s->last=now;
    dt=dt<0.0005f ? 0.0005f : dt>0.1f ? 0.1f : dt;
    const float blend=1.0f-expf(-dt*1000.0f/MOUSE_SMOOTHING_MS);
    s->vx+=(dx/dt-s->vx)*blend;
    s->vy+=(dy/dt-s->vy)*blend;
    const float sx=s->vx*kbm.sensitivity/MOUSE_FULL_COUNTS_S;
    const float sy=s->vy*kbm.sensitivity/MOUSE_FULL_COUNTS_S*(kbm.invert_y ? -1.0f : 1.0f);
    const float m=sqrtf(sx*sx+sy*sy);
    if (m<0.02f) return;
    const float tilt=MOUSE_DEADZONE+(1.0f-MOUSE_DEADZONE)*(m>1.0f ? 1.0f : m);
    d->right_x=stick_byte(sx*tilt/m);
    d->right_y=stick_byte(sy*tilt/m);
}
static void mouse_camera_start(void) {
    if (!kbm.mouse_camera) { puts("Runtime: Mouse camera: off (mouse_camera=0)"); return; }
    const char *why="";
    if (runtime_camhook_install(kbm.no_auto_rotation,&why)) {
        bbgpu_mouse_set_direct(mouse_direct_turn,runtime_camhook_drop);
        puts("Runtime: Mouse camera: hook installed");
    } else printf("Runtime: Mouse camera: stick fallback: %s\n",why);
}
/* What the window collected since the last read: the buttons, the wheel as short presses, and
 * the motion that the stick fallback turns into tilt (the hook has had it from the window already). */
static float mouse_dx, mouse_dy;
static int mouse_captured;
static void read_mouse(const bool *k) {
    float wheel=0;
    static float wheel_rest;
    bbgpu_mouse_enable(kbm.mouse_camera || mouse_inputs_bound()); /* the window may open after the pad */
    mouse_captured=bbgpu_mouse_take(&mouse_dx,&mouse_dy,&wheel,&mouse_buttons);
    if (!mouse_captured) mouse_buttons=0;
    wheel_rest=mouse_captured ? wheel_rest+wheel : 0;
    const int mods=held_mods(k);
    for (;wheel_rest>=1.0f;wheel_rest-=1.0f) wheel_step(1,mods);
    for (;wheel_rest<=-1.0f;wheel_rest+=1.0f) wheel_step(-1,mods);
}
static void apply_mouse_stick(PadData *d, uint64_t now) {
    static MouseStick stick;
    if (mouse_captured && kbm.mouse_camera && !runtime_camhook_active()) mouse_stick_step(&stick,d,mouse_dx,mouse_dy,now);
    else stick=(MouseStick){0}; /* the hook gets the motion from the window */
}

static void sample_host(PadData *d) {
    report_guest_heap();
    memset(d,0,sizeof(*d));
    d->left_x=d->left_y=d->right_x=d->right_y=128;
    d->orientation[3]=1.0f;
    d->connected=1; d->connected_count=connected_count ? connected_count : 1;
    d->timestamp=now_us();
    SDL_Gamepad *g=current_gamepad();
    ensure_bindings();
    if (bbgpu_overlay_captures_input()) return; /* settings menu open: neutral input */
    const bool *k=SDL_WasInit(SDL_INIT_VIDEO) ? SDL_GetKeyboardState(NULL) : NULL;
    if (g) {
        int touch_right=0;
        for (int i=IN_CROSS;i<=IN_RIGHT;++i) {
            const int v=pad_value(g,i);
            if (i==IN_L2) d->l2=(uint8_t)v;
            if (i==IN_R2) d->r2=(uint8_t)v;
            if (i==IN_TOUCHPAD_RIGHT) touch_right=v>30;
            else if (v>30) d->buttons|=input_buttons[i];
        }
        static const SDL_GamepadAxis stick_axes[4]={SDL_GAMEPAD_AXIS_LEFTX,SDL_GAMEPAD_AXIS_LEFTY,
                                                    SDL_GAMEPAD_AXIS_RIGHTX,SDL_GAMEPAD_AXIS_RIGHTY};
        int16_t raw[4];
        for (int i=0;i<4;++i) raw[i]=(int16_t)SDL_GetGamepadAxis(g,stick_axes[i]);
        cal_load();
        cal_sample(raw);
        uint8_t *axis_out[4]={&d->left_x,&d->left_y,&d->right_x,&d->right_y};
        for (int i=0;i<4;++i)
            *axis_out[i]=cal_have_center ? stick_axis(i,raw[i]) : 128; /* centred until the neutral is known */
        if (SDL_GetNumGamepadTouchpads(g)>0) {
            const int fingers=SDL_GetNumGamepadTouchpadFingers(g,0);
            for (int finger=0;finger<fingers && d->touch_count<2;++finger) {
                bool down=false;
                float x=0, y=0;
                if (SDL_GetGamepadTouchpadFinger(g,0,finger,&down,&x,&y,NULL) && down) {
                    d->touches[d->touch_count++]=(PadTouch){.x=touch_axis(x,1919),
                        .y=touch_axis(y,942),.id=(uint8_t)finger};
                }
            }
        }
        // Back/Select on pads without a touch surface is a left-side click.
        if ((d->buttons & BTN_TOUCHPAD) && !d->touch_count) touch_click(d,0);
        if (touch_right) touch_click(d,1);
    }
    read_mouse(k);
    if (k) apply_keyboard(d,k);
    apply_mouse_stick(d,d->timestamp);
}

/* BB_PAD_FILE=<file>: scripted input for automated runs. The file holds whitespace-separated
 * tokens, re-read when it changes: button names (cross circle square triangle l1 r1 l2 r2 l3 r3
 * options touchpad touchpad_left touchpad_right up down left right) are held while listed;
 * touchpad defaults to a left-side click; lx= ly= rx= ry= (0..255) override
 * the sticks. An empty file releases everything. */
static struct { uint32_t buttons; int stick[4]; int touch_side; } injected={0,{-1,-1,-1,-1},-1};
static int replay_armed;      /* 1 while a BB_PAD_REPLAY recording plays, 2 once it ended */
static uint64_t replay_start; /* 0: (re)start at the next sample */
static void read_inject(void) {
    static const char *path; static int checked; static uint64_t last_check; static struct timespec mtime;
    if (!checked) { path=getenv("BB_PAD_FILE"); checked=1; }
    if (!path || !*path) return;
    uint64_t now=now_us();
    if (now-last_check<20000) return;
    last_check=now;
    struct stat st;
    if (stat(path,&st)!=0) return;
#ifdef _WIN32
    /* Whole-second mtimes: the size tells two edits within a second apart. */
    if (st.st_mtime==mtime.tv_sec && st.st_size==mtime.tv_nsec) return;
    mtime=(struct timespec){st.st_mtime,(long)st.st_size};
#else
    if (st.st_mtim.tv_sec==mtime.tv_sec && st.st_mtim.tv_nsec==mtime.tv_nsec) return;
    mtime=st.st_mtim;
#endif
    FILE *f=fopen(path,"r");
    if (!f) return;
    static const struct { const char *name; uint32_t ps; } names[]={
        {"cross",BTN_CROSS}, {"circle",BTN_CIRCLE}, {"square",BTN_SQUARE}, {"triangle",BTN_TRIANGLE},
        {"l1",BTN_L1}, {"r1",BTN_R1}, {"l2",BTN_L2}, {"r2",BTN_R2}, {"l3",BTN_L3}, {"r3",BTN_R3},
        {"options",BTN_OPTIONS}, {"touchpad",BTN_TOUCHPAD},
        {"up",BTN_UP}, {"down",BTN_DOWN}, {"left",BTN_LEFT}, {"right",BTN_RIGHT},
    };
    static const char *sticks[]={"lx=","ly=","rx=","ry="};
    injected.buttons=0;
    injected.touch_side=-1;
    for (int i=0;i<4;++i) injected.stick[i]=-1;
    char token[64];
    while (fscanf(f,"%63s",token)==1) {
        if (!strcmp(token,"replay") && replay_armed!=1) { replay_armed=1; replay_start=0; } /* BB_PAD_REPLAY */
        if (!strcmp(token,"touchpad_left") || !strcmp(token,"touchpad_right")) {
            injected.buttons|=BTN_TOUCHPAD;
            injected.touch_side=!strcmp(token,"touchpad_right");
        }
        for (size_t i=0;i<sizeof(names)/sizeof(*names);++i) if (!strcmp(token,names[i].name)) injected.buttons|=names[i].ps;
        for (int i=0;i<4;++i) if (!strncmp(token,sticks[i],3)) { int v=atoi(token+3); injected.stick[i]=v<0 ? 0 : v>255 ? 255 : v; }
    }
    fclose(f);
    printf("Runtime: pad file: buttons 0x%x sticks %d %d %d %d\n",injected.buttons,
           injected.stick[0],injected.stick[1],injected.stick[2],injected.stick[3]);
}
/* BB_PAD_RECORD=<file>: F9 starts and stops recording the pad state (gamepad or keyboard) with
 * the time since F9; BB_PAD_REPLAY=<file> plays such a recording back, started by the token
 * "replay" in BB_PAD_FILE (scripted tests repeat a route the player ran once). Lines: ms buttons
 * lx ly rx ry l2 r2, written when the state changes. */
typedef struct { uint32_t ms, buttons; uint8_t axes[4], l2, r2; } PadSample;
static FILE *record_file;
static uint64_t record_start;
static PadSample record_last;
static void record_sample(const PadData *d) {
    static const char *path; static int checked, f9_was_down;
    if (!checked) { path=getenv("BB_PAD_RECORD"); checked=1; }
    if (!path || !*path || !sdl_ready) return;
    const bool *k=SDL_GetKeyboardState(NULL);
    const int f9=k && k[SDL_SCANCODE_F9];
    if (f9 && !f9_was_down) {
        if (record_file) {
            fclose(record_file); record_file=NULL;
            printf("Runtime: pad recording stopped (%s)\n",path);
        } else if ((record_file=fopen(path,"w"))) {
            record_start=now_us();
            memset(&record_last,0xff,sizeof(record_last));
            printf("Runtime: pad recording started (%s, F9 stops)\n",path);
        }
    }
    f9_was_down=f9;
    if (!record_file) return;
    PadSample s={(uint32_t)((now_us()-record_start)/1000),d->buttons,
                 {d->left_x,d->left_y,d->right_x,d->right_y},d->l2,d->r2};
    if (s.buttons==record_last.buttons && !memcmp(s.axes,record_last.axes,4) &&
        s.l2==record_last.l2 && s.r2==record_last.r2) return;
    record_last=s;
    fprintf(record_file,"%u %u %u %u %u %u %u %u\n",s.ms,s.buttons,s.axes[0],s.axes[1],s.axes[2],
            s.axes[3],s.l2,s.r2);
    fflush(record_file);
}
static PadSample *replay; static size_t replay_count, replay_next;
static void replay_sample(PadData *d) {
    if (!replay_armed) return;
    if (!replay_start) {
        static int loaded;
        if (!loaded) {
            loaded=1;
            const char *path=getenv("BB_PAD_REPLAY");
            FILE *f=path ? fopen(path,"r") : NULL;
            PadSample s; unsigned v[8]; size_t cap=0;
            while (f && fscanf(f,"%u %u %u %u %u %u %u %u",&v[0],&v[1],&v[2],&v[3],&v[4],&v[5],&v[6],&v[7])==8) {
                s=(PadSample){v[0],v[1],{(uint8_t)v[2],(uint8_t)v[3],(uint8_t)v[4],(uint8_t)v[5]},(uint8_t)v[6],(uint8_t)v[7]};
                if (replay_count==cap && !(replay=realloc(replay,(cap=cap ? cap*2 : 1024)*sizeof(*replay)))) break;
                replay[replay_count++]=s;
            }
            if (f) fclose(f);
            printf("Runtime: pad replay of %zu samples from %s\n",replay_count,path ? path : "(unset)");
        }
        replay_start=now_us();
        replay_next=0;
    }
    const uint32_t ms=(uint32_t)((now_us()-replay_start)/1000);
    while (replay_next<replay_count && replay[replay_next].ms<=ms) ++replay_next;
    if (!replay_next) return;
    if (replay_next==replay_count && ms>replay[replay_count-1].ms+500) {
        if (replay_armed==1) { puts("Runtime: pad replay finished"); replay_armed=2; }
        return;
    }
    const PadSample *s=&replay[replay_next-1];
    d->buttons=s->buttons;
    d->left_x=s->axes[0]; d->left_y=s->axes[1]; d->right_x=s->axes[2]; d->right_y=s->axes[3];
    d->l2=s->l2; d->r2=s->r2;
}
/* Touches as the DualShock 4 reports them: every finger that goes down gets a new id (1..127, kept
 * while it stays down) and the time since the first one went down. With id 0 and no hold time
 * the game ignored touchpad presses: no gesture menu from the touchpad, Back or Tab. */
static void touch_ids(PadData *d) {
    static uint8_t next_id=1, ids[2]; static int down[2]; static uint64_t since;
    for (int i=0;i<2;++i) {
        const int now=i<d->touch_count;
        if (now && !down[i]) { ids[i]=next_id; next_id=next_id==127 ? 1 : next_id+1; }
        down[i]=now;
        if (now) d->touches[i].id=ids[i];
    }
    if (!d->touch_count) since=0;
    else if (!since) since=d->timestamp;
    d->touch_held_time=d->touch_count ? (uint32_t)(d->timestamp-since) : 0;
}
/* After the menu or the text dialog closes, buttons still held (the Cross that accepted a
 * name) stay hidden until released: the game would take them as a new press. */
static int hold_after_capture;
static void sample(PadData *d) {
    sample_host(d);
    if (bbgpu_overlay_captures_input()) { hold_after_capture=1; return; }
    record_sample(d);
    read_inject();
    replay_sample(d);
    d->buttons|=injected.buttons;
    if (injected.touch_side>=0) touch_click(d,injected.touch_side);
    else if ((d->buttons & BTN_TOUCHPAD) && !d->touch_count) touch_click(d,0);
    if (injected.buttons & BTN_L2) d->l2=255;
    if (injected.buttons & BTN_R2) d->r2=255;
    uint8_t *axes[4]={&d->left_x,&d->left_y,&d->right_x,&d->right_y};
    for (int i=0;i<4;++i) if (injected.stick[i]>=0) *axes[i]=(uint8_t)injected.stick[i];
    touch_ids(d);
    if (hold_after_capture) {
        if (d->buttons) d->buttons=0;
        else hold_after_capture=0;
    }
}

static ABI int32_t pad_init(void) { pthread_mutex_lock(&lock); initialized=1; pthread_mutex_unlock(&lock); return 0; }
static ABI int32_t pad_open(int32_t user, int32_t type, int32_t index, const void *param) {
    (void)param;
    if (!initialized) return ERR_NOT_INITIALIZED;
    if (user!=1) return ERR_INVALID_ARG;
    if (type!=0 && type!=2) return ERR_INVALID_ARG; /* standard / special port */
    if (index) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    int already=opened; opened=1;
    pthread_mutex_unlock(&lock);
    if (already) return ERR_ALREADY_OPENED;
    pthread_mutex_lock(&lock);
    ensure_bindings();
    pthread_mutex_unlock(&lock);
    mouse_camera_start(); /* before the game's camera first runs */
    puts("Runtime: pad opened for user 1 (SDL gamepad, keyboard and mouse)");
    return PAD_HANDLE;
}
static ABI int32_t pad_close(int32_t handle) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    opened=0; return 0;
}
static ABI int32_t pad_read_state(int32_t handle, PadData *data) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!data) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    sample(data); ++reads;
    pthread_mutex_unlock(&lock);
    return 0;
}
/* Buffered read: the port samples once per call, so one entry is returned. */
static ABI int32_t pad_read(int32_t handle, PadData *data, int32_t count) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!data || count<1 || count>64) return ERR_INVALID_ARG;
    pad_read_state(handle,data);
    return 1;
}
static ABI int32_t pad_info(int32_t handle, ControllerInfo *info) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!info) return ERR_INVALID_ARG;
    memset(info,0,sizeof(*info));
    info->pixel_density=44.86f; info->resolution_x=1920; info->resolution_y=943;
    info->dead_zone_left=info->dead_zone_right=2;
    info->connection_type=0; info->connected=1; info->device_class=0;
    pthread_mutex_lock(&lock);
    current_gamepad();
    info->connected_count=connected_count ? connected_count : 1;
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_vibration(int32_t handle, const uint8_t *param) {
    if (handle!=PAD_HANDLE || !opened) return ERR_INVALID_HANDLE;
    if (!param) return ERR_INVALID_ARG;
    pthread_mutex_lock(&lock);
    SDL_Gamepad *g=current_gamepad();
    if (g) SDL_RumbleGamepad(g,(uint16_t)(param[0]*257),(uint16_t)(param[1]*257),1000);
    pthread_mutex_unlock(&lock);
    return 0;
}
static ABI int32_t pad_ok_handle(int32_t handle) { return handle==PAD_HANDLE && opened ? 0 : ERR_INVALID_HANDLE; }
static ABI int32_t pad_ok_handle_flag(int32_t handle, uint8_t flag) { (void)flag; return pad_ok_handle(handle); }

static const RuntimeExport exports[]={
    {"scePadInit",pad_init}, {"scePadOpen",pad_open}, {"scePadClose",pad_close},
    {"scePadReadState",pad_read_state}, {"scePadRead",pad_read},
    {"scePadGetControllerInformation",pad_info}, {"scePadSetVibration",pad_vibration},
    {"scePadResetOrientation",pad_ok_handle},
    {"scePadSetAngularVelocityDeadbandState",pad_ok_handle_flag}, {"scePadSetTiltCorrectionState",pad_ok_handle_flag},
    {"scePadSetMotionSensorState",pad_ok_handle_flag},
};
uintptr_t runtime_pad_resolve(const char *name) { return RUNTIME_LOOKUP(exports,name); }
void runtime_pad_report(void) { printf("Runtime: pad reads=%zu, gamepad=%s\n",reads,gamepad ? SDL_GetGamepadName(gamepad) : "none"); }
