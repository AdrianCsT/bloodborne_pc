/* Stick neutral and dead zone of runtime_pad.c (upstream #70/#71), on SDL virtual controllers:
 * a genuine pad, a clone whose sticks rest off-centre, sticks held while the pad connects, and
 * the BB_PAD_REPLAY route, which must come out as recorded whatever pad is connected.
 * After configuring the GPU build: ninja -C out/gpu pad-calibration-test && out/gpu/pad-calibration-test.exe
 * Or from the repository root in an MSYS2 CLANG64 shell:
 * clang -std=c11 -O2 -Wall -Wextra -Werror -UNDEBUG -pthread -I. -Isrc $(pkg-config --cflags sdl3) \
 *   tests/test_pad_calibration.c src/compat_win.c $(pkg-config --libs sdl3) -lbcrypt -o out/pad-calibration-test.exe
 */
#define _GNU_SOURCE
#include <assert.h>
#include "../src/runtime_pad.c"
#ifdef _WIN32
#include <windows.h>
#define SLEEP_MS(ms) Sleep(ms)
#define SETENV(name,value) _putenv_s(name,value)
#else
#include <unistd.h>
#define SLEEP_MS(ms) usleep((ms)*1000)
#define SETENV(name,value) setenv(name,value,1)
#endif

static int capture;
int bbgpu_overlay_captures_input(void) { return capture; }
uintptr_t runtime_lookup(const RuntimeExport *table, size_t count, const char *name) {
    (void)table; (void)count; (void)name;
    return 0;
}

typedef struct { SDL_Joystick *joystick; SDL_JoystickID id; } Virtual;

static Virtual attach(const int16_t axes[4]) {
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type=SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.naxes=SDL_GAMEPAD_AXIS_COUNT;
    desc.nbuttons=SDL_GAMEPAD_BUTTON_COUNT;
    desc.button_mask=(1u<<SDL_GAMEPAD_BUTTON_COUNT)-1;
    desc.axis_mask=(1u<<SDL_GAMEPAD_AXIS_COUNT)-1;
    desc.name="bbport test controller";
    desc.vendor_id=0x1d50;
    desc.product_id=0x6189;
    Virtual v;
    v.id=SDL_AttachVirtualJoystick(&desc);
    assert(v.id!=0);
    v.joystick=SDL_OpenJoystick(v.id);
    assert(v.joystick);
    for (int i=0;i<4;++i) assert(SDL_SetJoystickVirtualAxis(v.joystick,i,axes[i]));
    SDL_UpdateJoysticks();
    SDL_UpdateGamepads();
    return v;
}
static void detach(Virtual v) {
    SDL_CloseJoystick(v.joystick);
    if (gamepad) SDL_CloseGamepad(gamepad);
    gamepad=NULL;
    assert(SDL_DetachVirtualJoystick(v.id));
}
static void move(Virtual v,int axis,int16_t value) {
    assert(SDL_SetJoystickVirtualAxis(v.joystick,axis,value));
    SDL_UpdateJoysticks();
    SDL_UpdateGamepads();
}
/* The game polls the pad every frame: a number of reads lets the neutral settle. */
static PadData settle(int reads) {
    PadData d;
    for (int i=0;i<reads;++i) { assert(pad_read_state(1,&d)==0); SLEEP_MS(2); }
    return d;
}
static PadData read_once(void) { PadData d; assert(pad_read_state(1,&d)==0); return d; }
static int sticks_are(const PadData *d,int lx,int ly,int rx,int ry) {
    return d->left_x==lx && d->left_y==ly && d->right_x==rx && d->right_y==ry;
}
#define EXPECT_STICKS(d,lx,ly,rx,ry) do { \
    if (!sticks_are(&(d),lx,ly,rx,ry)) { \
        printf("line %d: sticks %d %d %d %d, expected %d %d %d %d\n",__LINE__,(d).left_x,(d).left_y,(d).right_x,(d).right_y,lx,ly,rx,ry); \
        abort(); } } while (0)

int main(void) {
    setvbuf(stdout,NULL,_IONBF,0); /* a failed check aborts: keep what was printed before it */
    SETENV("BB_PAD_FILE","bbport-pad-calibration-pad.txt");
    SETENV("BB_PAD_REPLAY","bbport-pad-calibration-replay.txt");
    FILE *f=fopen("bbport-pad-calibration-pad.txt","w"); assert(f); fclose(f);
    f=fopen("bbport-pad-calibration-replay.txt","w");
    assert(f);
    /* ms buttons lx ly rx ry l2 r2: a held walk, then a turn */
    fputs("0 0 10 200 50 250 0 0\n150 16384 255 0 128 128 0 0\n",f);
    fclose(f);
    SETENV("SDL_VIDEODRIVER","dummy");
    SDL_SetHint(SDL_HINT_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT,"0x1d50/0x6189");
    assert(SDL_Init(SDL_INIT_GAMEPAD));
    assert(pad_init()==0 && pad_open(1,0,0,NULL)==1);

    /* 1. A genuine pad: small offsets at rest are inside the dead zone; full pushes reach 0/255. */
    {
        const int16_t rest[4]={120,-80,30,-200};
        Virtual v=attach(rest);
        PadData d=settle(20);
        EXPECT_STICKS(d,128,128,128,128);
        assert(cal_have_center && !cal_center[0] && !cal_center[1] && !cal_center[2] && !cal_center[3]);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,32767); d=read_once(); assert(d.left_x==255);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,-32768); d=read_once(); assert(d.left_x==0);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,120); d=read_once(); assert(d.left_x==128);
        /* The inner dead zone (5 of 127) swallows a small push; a half push is a bit under half. */
        move(v,SDL_GAMEPAD_AXIS_LEFTY,1000); d=read_once(); assert(d.left_y==128);
        move(v,SDL_GAMEPAD_AXIS_LEFTY,16384); d=read_once(); assert(d.left_y>=128+60 && d.left_y<=128+64);
        detach(v);
    }
    /* 2. A clone whose sticks rest at +/-16380: the neutral is measured, rest reads centred, and
     * both directions reach full deflection over the half range the pad uses. */
    {
        const int16_t rest[4]={-16380,16380,-16380,16380};
        Virtual v=attach(rest);
        PadData d=settle(20);
        EXPECT_STICKS(d,128,128,128,128);
        assert(cal_center[0]==-16380 && cal_center[1]==16380 && cal_center[2]==-16380 && cal_center[3]==16380);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,-32768); d=read_once(); assert(d.left_x==0);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,0); d=read_once(); assert(d.left_x==255);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,-16380); d=read_once(); assert(d.left_x==128);
        move(v,SDL_GAMEPAD_AXIS_LEFTY,32767); d=read_once(); assert(d.left_y==255);
        detach(v);
    }
    /* 3. One stick held while the pad connects (the other axes at rest): it is not the neutral. */
    {
        const int16_t rest[4]={-32768,-90,60,10};
        Virtual v=attach(rest);
        PadData d=settle(20);
        EXPECT_STICKS(d,0,128,128,128);
        assert(cal_have_center && !cal_center[0]);
        move(v,SDL_GAMEPAD_AXIS_LEFTX,50); d=read_once(); assert(d.left_x==128); /* released */
        move(v,SDL_GAMEPAD_AXIS_LEFTX,32767); d=read_once(); assert(d.left_x==255);
        detach(v);
    }
    /* 4. Both sticks held at their stops (a full diagonal reads 23170): no neutral either. */
    {
        const int16_t rest[4]={23170,-23170,23170,-23170};
        Virtual v=attach(rest);
        PadData d=settle(20);
        EXPECT_STICKS(d,217,39,217,39); /* 23170 of 32768 less the dead zone, scaled: kept as pushed */
        assert(cal_have_center && !cal_center[0] && !cal_center[1] && !cal_center[2] && !cal_center[3]);
        detach(v);
    }
    /* 5. A stick still moving does not end the quiet window: the neutral waits for it to settle. */
    {
        const int16_t rest[4]={100,100,100,100};
        Virtual v=attach(rest);
        PadData d;
        for (int i=0;i<30;++i) { move(v,SDL_GAMEPAD_AXIS_RIGHTX,(int16_t)((i&1) ? 20000 : -20000)); d=read_once(); }
        assert(!cal_have_center);
        move(v,SDL_GAMEPAD_AXIS_RIGHTX,100);
        d=settle(20);
        EXPECT_STICKS(d,128,128,128,128);
        assert(cal_have_center && !cal_center[2]);
        detach(v);
    }
    /* 6. The BB_PAD_REPLAY route is what the file says, with a clone connected or with no pad,
     * and the pad shows again once the recording ends. */
    {
        const int16_t rest[4]={-16380,16380,-16380,16380};
        Virtual v=attach(rest);
        settle(20);
        f=fopen("bbport-pad-calibration-pad.txt","w"); assert(f);
        fputs("replay\n",f); fclose(f);
        SLEEP_MS(40);
        PadData d=read_once();
        EXPECT_STICKS(d,10,200,50,250);
        SLEEP_MS(170);
        d=read_once();
        assert(d.buttons==16384);
        EXPECT_STICKS(d,255,0,128,128);
        for (int i=0;i<12 && replay_armed==1;++i) { SLEEP_MS(100); d=read_once(); }
        assert(replay_armed==2);
        d=read_once();
        EXPECT_STICKS(d,128,128,128,128); /* the calibrated clone, after the route */
        detach(v);
        /* Same route with no pad connected. */
        f=fopen("bbport-pad-calibration-pad.txt","w"); assert(f);
        fputs("replay replay2\n",f); fclose(f);
        SLEEP_MS(40);
        d=read_once();
        EXPECT_STICKS(d,10,200,50,250);
    }
    SDL_Quit();
    remove("bbport-pad-calibration-pad.txt");
    remove("bbport-pad-calibration-replay.txt");
    puts("PASS: stick neutral of a genuine and a biased pad, held sticks at connect, replay route unchanged");
    return 0;
}
