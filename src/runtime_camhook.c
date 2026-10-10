/* Native PC mouse camera: a hook in the game's camera update (Bloodborne 1.09).
 *
 * Ported from Ryansousa10/bloodborne_windows_mouse_and_keyboard (src/runtime_camhook.c, commit
 * c650c2e, GPL-2.0-or-later, the same license as this file). The hook site, the camera object's
 * field offsets, the machine-code stub and the four auto-rotation sites are that project's; the
 * install checks, the reasons it gives for refusing, runtime_camhook_drop and the layout of the
 * code differ.
 *
 * The camera object's update (image+0x143a000..0x1440200) turns the camera from the right stick
 * with an acceleration ramp, levels the pitch while the stick turns it and falls back to
 * automatic camera paths when the stick is still. All its paths for a free camera meet at
 * image+0x143ce67, after the frame's angles are written to the object (pitch +0x140, yaw +0x144,
 * radians) and before the camera is built from them; the current pitch then follows the target
 * pitch (+0x150). The camera's ideal position (+0xf0) is placed around the pivot (+0xd0) from
 * those angles at once, but the position it is drawn from (+0x100) follows the ideal one with a
 * spring, which made a mouse turn arrive over ~100 ms.
 *
 * A 6-byte jmp at the meeting point runs `stub`: when the mouse moved it saves every register the
 * game may hold, calls camhook_frame on the game's camera thread with the camera object (r13),
 * restores them, runs the instruction the jmp replaced and returns to the game. camhook_frame
 * adds the mouse turn (taken atomically from the stub's `delta` slot, written by the window
 * thread) to both pitches, inside the game's own limits (+0x1f0..+0x1ec), and to the yaw, and
 * turns the drawn position around the pivot by the same angles so that the spring has nothing to
 * catch up with. The mouse thus turns the camera by exact angles in the frame it moved, as in PC
 * games; the spring still smooths distance changes and collisions, and the stick, lock-on and the
 * rest stay the game's. Disassembly of the stub (llvm-objdump, Intel syntax):
 *   +0x00 cmp qword [delta],0 ; je +0x1ad            nothing to do
 *   +0x0e push rbx ; mov rbx,rsp ; and rsp,-16 ; sub rsp,0x180
 *         xmm0-15 and rax rcx rdx rsi rdi r8-r11 stored at rsp+0x00..0x140
 *   +0xdc mov rdi,r13 ; call [camhook_frame]          xmm0 = the pitch the game had, returned
 *         the registers are loaded back, xmm0 holds the returned pitch; mov rsp,rbx ; pop rbx
 *   +0x1ad mov eax,[rbp-0x3c4] ; jmp [back]           the replaced instruction, then the game
 */
#define _GNU_SOURCE
#include "runtime.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

/* The game image, set by probe.c before the game runs. */
uintptr_t runtime_image_start;
uint64_t runtime_image_size;

#define HOOK_SITE 0x143ce67
static const unsigned char original[10]={0x8b,0x85,0x3c,0xfc,0xff,0xff, 0x84,0xc0, 0x74,0x26};
/* delta at 0x1c0 (pitch, yaw floats), return address at 0x1c8, camhook_frame at 0x1d0. */
static const unsigned char stub[0x1d8]={
    0x48, 0x83, 0x3d, 0xb8, 0x01, 0x00, 0x00, 0x00, 0x0f, 0x84, 0x9f, 0x01, 0x00, 0x00, 0x53, 0x48,
    0x89, 0xe3, 0x48, 0x83, 0xe4, 0xf0, 0x48, 0x81, 0xec, 0x80, 0x01, 0x00, 0x00, 0xc5, 0xf8, 0x11,
    0x04, 0x24, 0xc5, 0xf8, 0x11, 0x4c, 0x24, 0x10, 0xc5, 0xf8, 0x11, 0x54, 0x24, 0x20, 0xc5, 0xf8,
    0x11, 0x5c, 0x24, 0x30, 0xc5, 0xf8, 0x11, 0x64, 0x24, 0x40, 0xc5, 0xf8, 0x11, 0x6c, 0x24, 0x50,
    0xc5, 0xf8, 0x11, 0x74, 0x24, 0x60, 0xc5, 0xf8, 0x11, 0x7c, 0x24, 0x70, 0xc5, 0x78, 0x11, 0x84,
    0x24, 0x80, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x11, 0x8c, 0x24, 0x90, 0x00, 0x00, 0x00, 0xc5, 0x78,
    0x11, 0x94, 0x24, 0xa0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x11, 0x9c, 0x24, 0xb0, 0x00, 0x00, 0x00,
    0xc5, 0x78, 0x11, 0xa4, 0x24, 0xc0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x11, 0xac, 0x24, 0xd0, 0x00,
    0x00, 0x00, 0xc5, 0x78, 0x11, 0xb4, 0x24, 0xe0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x11, 0xbc, 0x24,
    0xf0, 0x00, 0x00, 0x00, 0x48, 0x89, 0x84, 0x24, 0x00, 0x01, 0x00, 0x00, 0x48, 0x89, 0x8c, 0x24,
    0x08, 0x01, 0x00, 0x00, 0x48, 0x89, 0x94, 0x24, 0x10, 0x01, 0x00, 0x00, 0x48, 0x89, 0xb4, 0x24,
    0x18, 0x01, 0x00, 0x00, 0x48, 0x89, 0xbc, 0x24, 0x20, 0x01, 0x00, 0x00, 0x4c, 0x89, 0x84, 0x24,
    0x28, 0x01, 0x00, 0x00, 0x4c, 0x89, 0x8c, 0x24, 0x30, 0x01, 0x00, 0x00, 0x4c, 0x89, 0x94, 0x24,
    0x38, 0x01, 0x00, 0x00, 0x4c, 0x89, 0x9c, 0x24, 0x40, 0x01, 0x00, 0x00, 0x4c, 0x89, 0xef, 0xff,
    0x15, 0xeb, 0x00, 0x00, 0x00, 0xc5, 0xfa, 0x11, 0x04, 0x24, 0xc5, 0xf8, 0x10, 0x04, 0x24, 0xc5,
    0xf8, 0x10, 0x4c, 0x24, 0x10, 0xc5, 0xf8, 0x10, 0x54, 0x24, 0x20, 0xc5, 0xf8, 0x10, 0x5c, 0x24,
    0x30, 0xc5, 0xf8, 0x10, 0x64, 0x24, 0x40, 0xc5, 0xf8, 0x10, 0x6c, 0x24, 0x50, 0xc5, 0xf8, 0x10,
    0x74, 0x24, 0x60, 0xc5, 0xf8, 0x10, 0x7c, 0x24, 0x70, 0xc5, 0x78, 0x10, 0x84, 0x24, 0x80, 0x00,
    0x00, 0x00, 0xc5, 0x78, 0x10, 0x8c, 0x24, 0x90, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x10, 0x94, 0x24,
    0xa0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x10, 0x9c, 0x24, 0xb0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x10,
    0xa4, 0x24, 0xc0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x10, 0xac, 0x24, 0xd0, 0x00, 0x00, 0x00, 0xc5,
    0x78, 0x10, 0xb4, 0x24, 0xe0, 0x00, 0x00, 0x00, 0xc5, 0x78, 0x10, 0xbc, 0x24, 0xf0, 0x00, 0x00,
    0x00, 0x48, 0x8b, 0x84, 0x24, 0x00, 0x01, 0x00, 0x00, 0x48, 0x8b, 0x8c, 0x24, 0x08, 0x01, 0x00,
    0x00, 0x48, 0x8b, 0x94, 0x24, 0x10, 0x01, 0x00, 0x00, 0x48, 0x8b, 0xb4, 0x24, 0x18, 0x01, 0x00,
    0x00, 0x48, 0x8b, 0xbc, 0x24, 0x20, 0x01, 0x00, 0x00, 0x4c, 0x8b, 0x84, 0x24, 0x28, 0x01, 0x00,
    0x00, 0x4c, 0x8b, 0x8c, 0x24, 0x30, 0x01, 0x00, 0x00, 0x4c, 0x8b, 0x94, 0x24, 0x38, 0x01, 0x00,
    0x00, 0x4c, 0x8b, 0x9c, 0x24, 0x40, 0x01, 0x00, 0x00, 0x48, 0x89, 0xdc, 0x5b, 0x8b, 0x85, 0x3c,
    0xfc, 0xff, 0xff, 0xff, 0x25, 0x0f, 0x00, 0x00, 0x00, 0x0f, 0x1f, 0x80, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};
#define STUB_DELTA 0x1c0
#define STUB_BACK 0x1c8
#define STUB_FRAME 0x1d0

static volatile uint64_t *delta;

/* The camera object's fields (floats). */
enum { CAM_PITCH=0x140, CAM_YAW=0x144, CAM_TARGET_PITCH=0x150, CAM_PITCH_MAX=0x1ec, CAM_PITCH_MIN=0x1f0,
       CAM_PIVOT=0xd0, CAM_DRAWN=0x100 };
static float *field(unsigned char *camera, unsigned offset) { return (float *)(camera+offset); }
/* On the game's camera thread, from the stub (System V ABI, like the game). */
static __attribute__((unused)) ABI float camhook_frame(unsigned char *camera, float pitch_in) {
    const uint64_t taken=__atomic_exchange_n(delta,0,__ATOMIC_ACQ_REL);
    float turn[2];
    memcpy(turn,&taken,8);
    if (!taken) return pitch_in;
    const float pitch_old=*field(camera,CAM_PITCH), yaw_old=*field(camera,CAM_YAW);
    float pitch=pitch_old+turn[0];
    if (pitch<*field(camera,CAM_PITCH_MIN)) pitch=*field(camera,CAM_PITCH_MIN);
    if (pitch>*field(camera,CAM_PITCH_MAX)) pitch=*field(camera,CAM_PITCH_MAX);
    *field(camera,CAM_PITCH)=pitch;
    *field(camera,CAM_TARGET_PITCH)=pitch;
    *field(camera,CAM_YAW)=yaw_old+turn[1];
    /* The drawn position sits at pivot - direction * r, direction = (sin y cos p, -sin p,
     * cos y cos p): turned by the same yaw and pitch. */
    const float *pivot=field(camera,CAM_PIVOT);
    float *drawn=field(camera,CAM_DRAWN);
    const double ox=drawn[0]-pivot[0], oy=drawn[1]-pivot[1], oz=drawn[2]-pivot[2];
    const double r=sqrt(ox*ox+oy*oy+oz*oz);
    if (r>1e-3 && r<100) {
        double s=oy/r;
        s=s<-1 ? -1 : s>1 ? 1 : s;
        const double p=asin(s)+(pitch-pitch_old), y=atan2(-ox,-oz)+turn[1];
        drawn[0]=(float)(pivot[0]-sin(y)*cos(p)*r);
        drawn[1]=(float)(pivot[1]+sin(p)*r);
        drawn[2]=(float)(pivot[2]-cos(y)*cos(p)*r);
    }
    return pitch;
}

#ifdef _WIN32
#include <windows.h>
/* An executable page within a rel32 jump of the hook site. */
static unsigned char *near_page(uintptr_t site) {
    for (int64_t step=1;step<0x7000;++step) for (int dir=-1;dir<=1;dir+=2) {
        const uintptr_t at=((site&~(uintptr_t)0xffff)+(uintptr_t)(dir*step*0x10000));
        void *p=VirtualAlloc((void *)at,0x1000,MEM_RESERVE|MEM_COMMIT,PAGE_EXECUTE_READWRITE);
        if (p) return p;
    }
    return NULL;
}
/* The camera's automatic turning while the character moves ("Disable Camera Auto Rotation via
 * Movement", Imedved and Kyo, patches/Bloodborne.xml): its yaw writes, nopped. A mouse camera
 * turns only by the mouse (or the stick). */
static const struct { uint32_t at; unsigned char original[9]; } auto_rotation[]={
    {0x143c6e8,{0xc4,0xc1,0x7a,0x11,0x85,0x44,0x01,0x00,0x00}}, {0x143c984,{0xc4,0xc1,0x7a,0x11,0x8d,0x30,0x01,0x00,0x00}},
    {0x143dde6,{0xc4,0xc1,0x7a,0x11,0x85,0x94,0x02,0x00,0x00}}, {0x143c870,{0xc4,0xc1,0x7a,0x11,0xad,0x44,0x01,0x00,0x00}},
};
static int write_code(unsigned char *at, const void *bytes, size_t size) {
    DWORD old;
    if (!VirtualProtect(at,size,PAGE_EXECUTE_READWRITE,&old)) return 0;
    memcpy(at,bytes,size);
    VirtualProtect(at,size,old,&old);
    FlushInstructionCache(GetCurrentProcess(),at,size);
    return 1;
}
static void disable_auto_rotation(void) {
    static const unsigned char nops[9]={0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90};
    int done=0;
    for (size_t i=0;i<sizeof(auto_rotation)/sizeof(*auto_rotation);++i) {
        unsigned char *at=(unsigned char *)runtime_image_start+auto_rotation[i].at;
        if (!memcmp(at,nops,9)) { ++done; continue; } /* the patch is on already */
        if (memcmp(at,auto_rotation[i].original,9)) continue;
        if (write_code(at,nops,9)) ++done;
    }
    printf("Runtime: camera auto-rotation off (%d of 4 sites)\n",done);
}
int runtime_camhook_install(int no_auto_rotation, const char **why) {
    static char reason[96];
    *why="";
    if (delta) return 1;
    if (!runtime_image_start || HOOK_SITE+sizeof(original)>runtime_image_size) {
        *why="no game image to hook";
        return 0;
    }
    unsigned char *site=(unsigned char *)runtime_image_start+HOOK_SITE;
    if (memcmp(site,original,sizeof(original))) {
        *why="unknown game version (the camera code differs from 1.09)";
        return 0;
    }
    unsigned char *page=near_page((uintptr_t)site);
    if (!page) { *why="no memory near the game code"; return 0; }
    memcpy(page,stub,sizeof(stub));
    const uint64_t back=(uintptr_t)site+6;
    const uint64_t frame=(uintptr_t)camhook_frame;
    memcpy(page+STUB_FRAME,&frame,8);
    memcpy(page+STUB_BACK,&back,8);
    const int64_t rel=(int64_t)((uintptr_t)page-((uintptr_t)site+5));
    if (rel<INT32_MIN || rel>INT32_MAX) { VirtualFree(page,0,MEM_RELEASE); *why="no memory near the game code"; return 0; }
    const int32_t rel32=(int32_t)rel;
    unsigned char jump[6]={0xe9,0,0,0,0,0x90};
    memcpy(jump+1,&rel32,4);
    if (!write_code(site,jump,6)) {
        VirtualFree(page,0,MEM_RELEASE);
        snprintf(reason,sizeof(reason),"the game code cannot be written (error %lu)",(unsigned long)GetLastError());
        *why=reason;
        return 0;
    }
    delta=(volatile uint64_t *)(page+STUB_DELTA);
    printf("Runtime: PC mouse camera hooked into the game's camera (image+0x%x)\n",HOOK_SITE);
    if (no_auto_rotation) disable_auto_rotation();
    return 1;
}
#else
int runtime_camhook_install(int no_auto_rotation, const char **why) {
    (void)no_auto_rotation;
    *why="the camera hook is Windows only";
    return 0;
}
#endif

int runtime_camhook_active(void) { return delta!=NULL; }

/* Adds a turn (radians, as the game's angles: pitch +0x140, yaw +0x144) for the next frame. */
void runtime_camhook_turn(float pitch, float yaw) {
    if (!delta || (pitch==0 && yaw==0)) return;
    uint64_t seen=*delta, next;
    do {
        float pair[2];
        memcpy(pair,&seen,8);
        pair[0]+=pitch; pair[1]+=yaw;
        memcpy(&next,pair,8);
    } while (!__atomic_compare_exchange_n(delta,&seen,next,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST));
}

/* Forgets the turn the camera has not taken yet (the mouse was released). */
void runtime_camhook_drop(void) {
    if (delta) __atomic_store_n(delta,0,__ATOMIC_RELEASE);
}
