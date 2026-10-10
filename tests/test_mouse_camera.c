/* Mouse support of runtime_pad.c and runtime_camhook.c (issue #5, part 2): the names of the mouse
 * inputs in key.<input>= lines and their modifiers, which inputs are held, wheel steps, the mouse
 * settings of bbport.ini, the stick fallback, the camera hook's turn, and the refusal to patch a
 * game image that is not 1.09. No window, no game: the mouse state is set by hand.
 * After configuring the GPU build: ninja -C out/gpu mouse-camera-test && out/gpu/mouse-camera-test.exe
 * Or from the repository root in an MSYS2 CLANG64 shell:
 * clang -std=c11 -O2 -Wall -Wextra -Werror -UNDEBUG -pthread -I. -Isrc $(pkg-config --cflags sdl3) \
 *   tests/test_mouse_camera.c src/compat_win.c $(pkg-config --libs sdl3) -lbcrypt -o out/mouse-camera-test.exe
 */
#define _GNU_SOURCE
#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include "../src/runtime_pad.c"
#include "../src/runtime_camhook.c"
#ifdef _WIN32
#include <windows.h>
#define SLEEP_MS(ms) Sleep(ms)
#define SETENV(name,value) _putenv_s(name,value)
#else
#include <unistd.h>
#define SLEEP_MS(ms) usleep((ms)*1000)
#define SETENV(name,value) setenv(name,value,1)
#endif

int bbgpu_overlay_captures_input(void) { return 0; }
void bbgpu_mouse_enable(int enabled) { (void)enabled; }
void bbgpu_mouse_set_direct(void (*turn)(float dx, float dy), void (*drop)(void)) { (void)turn; (void)drop; }
int bbgpu_mouse_take(float *dx, float *dy, float *wheel, uint32_t *buttons) {
    *dx=*dy=*wheel=0; *buttons=0;
    return 0;
}
uintptr_t runtime_lookup(const RuntimeExport *table, size_t count, const char *name) {
    (void)table; (void)count; (void)name;
    return 0;
}

#define PI_F 3.14159265358979f
static int close_to(float a, float b, float tolerance) { return fabsf(a-b)<=tolerance; }

static void names(void) {
    KeyInput in;
    assert(parse_input("Mouse Left",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_LEFT && in.mods==0);
    assert(parse_input("mouse right",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_RIGHT);
    assert(parse_input("Mouse Middle",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_MIDDLE);
    assert(parse_input("Mouse X1",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_X1);
    assert(parse_input("Mouse X2",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_X2);
    assert(parse_input("Wheel Up",&in) && in.kind==KIND_WHEEL && in.code==1);
    assert(parse_input("Wheel Down",&in) && in.kind==KIND_WHEEL && in.code==-1);
    assert(parse_input("Shift+Mouse Left",&in) && in.kind==KIND_MOUSE && in.code==SDL_BUTTON_LEFT && in.mods==BIND_SHIFT);
    assert(parse_input("ctrl+alt+Wheel Up",&in) && in.kind==KIND_WHEEL && in.mods==(BIND_CTRL|BIND_ALT));
    assert(parse_input("Left Ctrl",&in) && in.kind==KIND_KEY && in.code==SDL_SCANCODE_LCTRL && in.mods==0);
    assert(parse_input("E",&in) && in.kind==KIND_KEY && in.code==SDL_SCANCODE_E);
    assert(parse_input("Keypad +",&in) && in.kind==KIND_KEY && in.code==SDL_SCANCODE_KP_PLUS && in.mods==0);
    assert(parse_input("shift+R",&in) && in.kind==KIND_KEY && in.code==SDL_SCANCODE_R && in.mods==BIND_SHIFT);
    assert(!parse_input("Mouse Nine",&in));
    assert(!parse_input("Shift+",&in));
    assert(!parse_input("Wheel Sideways",&in));
}

static void write_config(const char *path, const char *text) {
    FILE *f=fopen(path,"wb");
    assert(f);
    fputs(text,f);
    fclose(f);
}

/* The Dark Souls III bindings of the launcher's preset, as bbport.ini holds them. */
static void held_setup(void) {
    static const char config[]="key.r1=Mouse Left\nkey.r2=Shift+Mouse Left\nkey.l1=Mouse Right\nkey.l2=Left Ctrl\n"
                                "key.cross=E\nkey.r3=Q, Mouse Middle\nkey.up=Up, Wheel Up\nkey.down=Down, Wheel Down\n";
    write_config("mouse-camera-test.ini",config);
    SETENV("BB_CONFIG","mouse-camera-test.ini");
    load_bindings();
    remove("mouse-camera-test.ini");
}

/* A held mouse button or wheel step is the button of the input it is bound to. */
static void held(void) {
    bool keys[SDL_SCANCODE_COUNT]={0};
    PadData d;
    mouse_buttons=SDL_BUTTON_MASK(SDL_BUTTON_LEFT);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_R1) && !(d.buttons & (BTN_R2|BTN_L1)));
    mouse_buttons=SDL_BUTTON_MASK(SDL_BUTTON_RIGHT)|SDL_BUTTON_MASK(SDL_BUTTON_MIDDLE);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_L1) && (d.buttons & BTN_R3) && !(d.buttons & BTN_R1));
    /* Shift + left click is R2 alone: the plain click binding yields to the combination. */
    mouse_buttons=SDL_BUTTON_MASK(SDL_BUTTON_LEFT);
    keys[SDL_SCANCODE_LSHIFT]=true;
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_R2) && d.r2==255 && !(d.buttons & BTN_R1));
    keys[SDL_SCANCODE_LSHIFT]=false; keys[SDL_SCANCODE_RSHIFT]=true;
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_R2) && !(d.buttons & BTN_R1));
    keys[SDL_SCANCODE_RSHIFT]=false;
    /* Shift alone is nothing, and Shift + the right button has no combination: the plain binding stays. */
    mouse_buttons=SDL_BUTTON_MASK(SDL_BUTTON_RIGHT);
    keys[SDL_SCANCODE_LSHIFT]=true;
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_L1) && !(d.buttons & BTN_R2));
    keys[SDL_SCANCODE_LSHIFT]=false;
    mouse_buttons=0;
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons==0);
    /* Keys still work; a modifier on a key too. */
    keys[SDL_SCANCODE_E]=true; keys[SDL_SCANCODE_LCTRL]=true;
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert((d.buttons & BTN_CROSS) && (d.buttons & BTN_L2) && d.l2==255);
    keys[SDL_SCANCODE_E]=keys[SDL_SCANCODE_LCTRL]=false;
    /* A wheel step is a short press, and fast scrolling queues steps instead of losing them. */
    wheel_step(1,0);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons & BTN_UP);
    SLEEP_MS(200);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(!(d.buttons & BTN_UP));
    wheel_step(-1,0); wheel_step(-1,0);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons & BTN_DOWN);
    SLEEP_MS(65);                      /* the first press is over, the gap not yet */
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(!(d.buttons & BTN_DOWN));
    SLEEP_MS(60);                      /* gap over: the second step */
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons & BTN_DOWN);
    SLEEP_MS(300);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(!(d.buttons & BTN_DOWN));
    /* A plain wheel binding takes steps with a modifier held too; a Shift+Wheel one only with Shift. */
    wheel_step(1,BIND_ALT);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons & BTN_UP);
    SLEEP_MS(200);
    write_config("mouse-camera-test.ini","key.up=Shift+Wheel Up\n");
    SETENV("BB_CONFIG","mouse-camera-test.ini");
    load_bindings();
    remove("mouse-camera-test.ini");
    wheel_step(1,0);
    SLEEP_MS(100);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(!(d.buttons & BTN_UP));
    wheel_step(1,BIND_SHIFT);
    memset(&d,0,sizeof d); apply_keyboard(&d,keys);
    assert(d.buttons & BTN_UP);
}

/* The mouse lines of bbport.ini, next to key.<input>= lines that use the new names. */
static void settings(void) {
    char path[]="mouse-camera-test.ini";
    write_config(path,"upscaler=fsr3\nkey.r1=Mouse Left, X\nkey.r2=Shift+Mouse Left\nkey.up=Up,Wheel Up\n"
                      "key.l2=Left Ctrl\nkey.square=Nonsense Key\nmouse_camera=0\nmouse_sensitivity=2.5\n"
                      "mouse_invert_y=1\nmouse_no_auto_rotation=0\n");
    SETENV("BB_CONFIG",path);
    load_bindings();
    assert(!kbm.mouse_camera && close_to(kbm.sensitivity,2.5f,0.001f) && kbm.invert_y && !kbm.no_auto_rotation);
    assert(bindings[IN_R1].key_count==2 && bindings[IN_R1].keys[0].kind==KIND_MOUSE &&
           bindings[IN_R1].keys[1].code==SDL_SCANCODE_X);
    assert(bindings[IN_R2].key_count==1 && bindings[IN_R2].keys[0].mods==BIND_SHIFT);
    assert(bindings[IN_UP].key_count==2 && bindings[IN_UP].keys[1].kind==KIND_WHEEL);
    assert(bindings[IN_SQUARE].key_count==0);     /* the unknown name is refused, the line still replaces the default */
    assert(mouse_inputs_bound());
    write_config(path,"mouse_sensitivity=500\n");
    load_bindings();
    assert(kbm.mouse_camera && close_to(kbm.sensitivity,20.0f,0.001f) && !kbm.invert_y && !kbm.no_auto_rotation);
    write_config(path,"mouse_sensitivity=0\n");
    load_bindings();
    assert(close_to(kbm.sensitivity,0.01f,0.0001f));
    /* A value that is not a number would pass both comparisons of a clamp and corrupt the camera turn for good:
     * it falls back to the default sensitivity. */
    static const char *const not_finite[]={"nan","-nan","inf","-inf","infinity"};
    for (size_t i=0;i<sizeof(not_finite)/sizeof(*not_finite);++i) {
        char line[64];
        snprintf(line,sizeof(line),"mouse_sensitivity=%s\n",not_finite[i]);
        write_config(path,line);
        load_bindings();
        assert(isfinite(kbm.sensitivity) && close_to(kbm.sensitivity,1.0f,0.0001f));
    }
    /* No key line: the Dark Souls III layout is the default, mouse buttons and wheel included. */
    write_config(path,"upscaler=fsr3\n");
    load_bindings();
    /* Gamepad players keep the game's own camera: auto-rotation stays unless asked for. */
    assert(close_to(kbm.sensitivity,1.0f,0.0001f) && kbm.mouse_camera && !kbm.invert_y &&
           !kbm.no_auto_rotation && mouse_inputs_bound());
    remove(path);
}

static int has_default(int input, int kind, int mods, int code) {
    for (int i=0;i<bindings[input].key_count;++i) {
        const KeyInput *k=&bindings[input].keys[i];
        if (k->kind==kind && k->mods==mods && k->code==code) return 1;
    }
    return 0;
}

/* The default keyboard and mouse bindings are the Dark Souls III layout (launcher/bbport_controls.py has the same
 * table and a test keeps them equal). R2 is Shift+Mouse Left, L2 Shift+Mouse Right and Left Ctrl. */
static void defaults(void) {
    bind_defaults();
    assert(bindings[IN_R2].key_count==1 && has_default(IN_R2,KIND_MOUSE,BIND_SHIFT,SDL_BUTTON_LEFT));
    assert(bindings[IN_L2].key_count==2 && has_default(IN_L2,KIND_MOUSE,BIND_SHIFT,SDL_BUTTON_RIGHT) &&
           has_default(IN_L2,KIND_KEY,0,SDL_SCANCODE_LCTRL));
    assert(bindings[IN_R1].key_count==1 && has_default(IN_R1,KIND_MOUSE,0,SDL_BUTTON_LEFT));
    assert(bindings[IN_L1].key_count==1 && has_default(IN_L1,KIND_MOUSE,0,SDL_BUTTON_RIGHT));
    assert(bindings[IN_CROSS].key_count==2 && has_default(IN_CROSS,KIND_KEY,0,SDL_SCANCODE_E) &&
           has_default(IN_CROSS,KIND_KEY,0,SDL_SCANCODE_RETURN));
    assert(bindings[IN_CIRCLE].key_count==2 && has_default(IN_CIRCLE,KIND_KEY,0,SDL_SCANCODE_SPACE) &&
           has_default(IN_CIRCLE,KIND_KEY,0,SDL_SCANCODE_ESCAPE));
    assert(has_default(IN_R3,KIND_KEY,0,SDL_SCANCODE_Q) && has_default(IN_R3,KIND_MOUSE,0,SDL_BUTTON_MIDDLE));
    assert(has_default(IN_UP,KIND_WHEEL,0,1) && has_default(IN_DOWN,KIND_WHEEL,0,-1));
    assert(has_default(IN_LEFT,KIND_WHEEL,BIND_SHIFT,-1) && has_default(IN_RIGHT,KIND_WHEEL,BIND_SHIFT,1));
    assert(has_default(IN_MOVE_UP,KIND_KEY,0,SDL_SCANCODE_W) && has_default(IN_LOOK_UP,KIND_KEY,0,SDL_SCANCODE_I) &&
           has_default(IN_LOOK_RIGHT,KIND_KEY,0,SDL_SCANCODE_L));
    /* The gamepad's defaults are unchanged. */
    assert(bindings[IN_R2].pad_count==1 && bindings[IN_R2].pad[0]==PAD_RIGHT_TRIGGER);
    assert(bindings[IN_CROSS].pad_count==1 && bindings[IN_CROSS].pad[0]==SDL_GAMEPAD_BUTTON_SOUTH);
}

/* The stick fallback: mouse speed becomes right-stick tilt. */
static PadData stick_after(float counts_per_s, float dy_per_s, int dt_us, int ticks) {
    MouseStick s={0};
    PadData d;
    uint64_t now=1000000;
    for (int i=0;i<ticks;++i) {
        memset(&d,0,sizeof d);
        d.right_x=d.right_y=128;
        now+=(uint64_t)dt_us;
        mouse_stick_step(&s,&d,counts_per_s*dt_us*1e-6f,dy_per_s*dt_us*1e-6f,now);
    }
    return d;
}
static void stick(void) {
    kbm.sensitivity=1.0f; kbm.invert_y=0;
    PadData d=stick_after(500,0,10000,60);                  /* full tilt at 500 counts a second */
    assert(d.right_x==255 && d.right_y==128);
    d=stick_after(250,0,10000,60);                          /* half the speed: 0.25 + 0.75 * 0.5 */
    assert(d.right_x==207 && d.right_y==128);
    PadData other_rate=stick_after(250,0,4000,150);         /* the same speed at another rate */
    assert(other_rate.right_x==d.right_x);
    d=stick_after(-250,0,10000,60);
    assert(d.right_x==128-80);                              /* left; negative values scale by 128 */
    d=stick_after(0,250,10000,60);
    assert(d.right_y==207 && d.right_x==128);               /* mouse down: the stick down */
    d=stick_after(20,0,10000,60);                           /* slow motion starts above the game's deadzone */
    assert(d.right_x>=128+(int)(0.25f*127) && d.right_x<=168);
    d=stick_after(0,0,10000,60);
    assert(d.right_x==128 && d.right_y==128);
    d=stick_after(1000,0,10000,60);                         /* beyond full: clamped */
    assert(d.right_x==255);
    kbm.sensitivity=2.0f;
    d=stick_after(250,0,10000,60);                          /* sensitivity 2: full tilt at 250 */
    assert(d.right_x==255);
    kbm.sensitivity=0.5f;
    d=stick_after(500,0,10000,60);
    assert(d.right_x==207);                                 /* ...and at 0.5 it takes 1000 for full tilt: 500 is half */
    kbm.sensitivity=1.0f; kbm.invert_y=1;
    d=stick_after(0,250,10000,60);
    assert(d.right_y==128-80 && d.right_x==128);            /* inverted */
    kbm.invert_y=0;
    /* Motion stops: the tilt decays to the centre in a few reads. */
    MouseStick s={0};
    PadData p;
    uint64_t now=1000000;
    for (int i=0;i<40;++i) { memset(&p,0,sizeof p); p.right_x=p.right_y=128; now+=10000; mouse_stick_step(&s,&p,5,0,now); }
    assert(p.right_x==255);
    for (int i=0;i<20;++i) { memset(&p,0,sizeof p); p.right_x=p.right_y=128; now+=10000; mouse_stick_step(&s,&p,0,0,now); }
    assert(p.right_x==128);
}

/* The camera hook: radians of the game's angles from mouse counts. */
static void turn(void) {
    float pitch, yaw;
    kbm.sensitivity=1.0f; kbm.invert_y=0;
    mouse_turn(100,0,&pitch,&yaw);
    assert(close_to(yaw,100*0.022f*PI_F/180.0f,1e-5f) && pitch==0);
    mouse_turn(0,100,&pitch,&yaw);
    assert(close_to(pitch,100*0.022f*PI_F/180.0f,1e-5f) && yaw==0);    /* mouse down: looks down, the game's pitch grows */
    kbm.invert_y=1;
    mouse_turn(0,100,&pitch,&yaw);
    assert(pitch<0);
    kbm.invert_y=0; kbm.sensitivity=2.0f;
    mouse_turn(100,0,&pitch,&yaw);
    assert(close_to(yaw,2*100*0.022f*PI_F/180.0f,1e-5f));
    kbm.sensitivity=1.0f;
}

/* camhook_frame on a camera object laid out as the game's: angles at +0x140/+0x144, the pivot at
 * +0xd0, the drawn position at +0x100, the pitch limits at +0x1ec/+0x1f0. */
static void hook_turn(void) {
    static float camera[0x200/4];
    static uint64_t slot;
    delta=&slot;
    memset(camera,0,sizeof camera);
    float *pivot=camera+0xd0/4, *drawn=camera+0x100/4;
    pivot[0]=10; pivot[1]=1; pivot[2]=-4;
    drawn[0]=10; drawn[1]=1; drawn[2]=-4-5;                  /* five metres behind: yaw 0, pitch 0 */
    camera[0x140/4]=0.1f; camera[0x144/4]=0.5f; camera[0x150/4]=0.1f;
    camera[0x1ec/4]=1.2f; camera[0x1f0/4]=-0.8f;
    assert(camhook_frame((unsigned char *)camera,0.25f)==0.25f);          /* no motion: untouched */
    assert(camera[0x144/4]==0.5f && drawn[2]==-9);
    runtime_camhook_turn(0.2f,0.3f);
    runtime_camhook_turn(0.1f,0.3f);                                      /* motion between two frames adds up */
    const float pitch=camhook_frame((unsigned char *)camera,0.25f);
    assert(close_to(pitch,0.4f,1e-6f) && close_to(camera[0x140/4],0.4f,1e-6f) && close_to(camera[0x150/4],0.4f,1e-6f));
    assert(close_to(camera[0x144/4],1.1f,1e-6f) && slot==0);                  /* taken once */
    const double ox=drawn[0]-pivot[0], oy=drawn[1]-pivot[1], oz=drawn[2]-pivot[2];
    assert(close_to((float)sqrt(ox*ox+oy*oy+oz*oz),5.0f,1e-3f));               /* the distance is kept */
    assert(close_to((float)atan2(-ox,-oz),0.6f,1e-3f));                       /* the drawn position turned by the yaw too */
    assert(close_to((float)asin(oy/5.0),0.3f,1e-3f));
    assert(camhook_frame((unsigned char *)camera,pitch)==pitch);          /* nothing more to take */
    runtime_camhook_turn(5.0f,0);                                         /* into the game's limits */
    assert(close_to(camhook_frame((unsigned char *)camera,0),1.2f,1e-6f));
    runtime_camhook_turn(-9.0f,0);
    assert(close_to(camhook_frame((unsigned char *)camera,0),-0.8f,1e-6f));
    runtime_camhook_drop();                                               /* a switch drops what has not been taken */
    runtime_camhook_turn(0.2f,0.2f);
    runtime_camhook_drop();
    assert(slot==0);
    delta=NULL;
}

/* The hook is installed only over the bytes of game version 1.09. */
static void hook_refusals(void) {
    const char *why=NULL;
    runtime_image_start=0;
    assert(!runtime_camhook_install(1,&why) && why && *why);
    printf("no image: %s\n",why);
    static unsigned char image[0x1500000];                                /* the hook site is at 0x143ce67 */
    runtime_image_start=(uintptr_t)image;
    runtime_image_size=sizeof(image);
    why=NULL;
    assert(!runtime_camhook_install(1,&why) && why && *why);
#ifdef _WIN32
    assert(strstr(why,"version"));
#endif
    printf("other version: %s\n",why);
    assert(!runtime_camhook_active());
    assert(image[0x143ce67]==0);                                          /* nothing was written */
    runtime_image_size=0x1000;
    why=NULL;
    assert(!runtime_camhook_install(1,&why) && why);
    runtime_image_start=0; runtime_image_size=0;
}

int main(void) {
#ifdef _WIN32
    /* A failed assert prints to stderr and aborts: no dialog on the desktop of whoever runs this. */
    _set_error_mode(_OUT_TO_STDERR);
    _set_abort_behavior(0,_WRITE_ABORT_MSG|_CALL_REPORTFAULT);
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
#endif
    names();
    held_setup();
    held();
    settings();
    defaults();
    stick();
    turn();
    hook_turn();
    hook_refusals();
    puts("PASS: mouse input names and modifiers, held buttons, wheel steps, mouse settings, Dark Souls III default layout, stick fallback, camera turn, hook refusals");
}
