// SPDX-License-Identifier: GPL-2.0-or-later
// bbport (Windows): the Linux-only parts of the GPU library, as inert stand-ins.
//
// bbport_guest_hooks, bbport_free_check, bbport_gnm_hooks and bbport_guest_memory are built on
// mprotect, SIGTRAP and ucontext hooks, memfd and dma-buf exports (the PC memory model, BB_FREE_CHECK,
// BB_GNM_CENSUS). CMakeLists.txt leaves them out of the Windows build and this file answers their
// interfaces: no hook is installed, no direct memory chunk is made (the runtime keeps its
// pagefile-backed section) and the PC memory model stays off, as with BB_GUEST_IN_PLACE=0.
#ifdef _WIN32
#include "bbport_free_check.h"
#include "bbport_gnm_hooks.h"
#include "bbport_guest_hooks.h"
#include "bbport_guest_memory.h"

namespace BbGuestHooks {
void Install(RangeCallback on_gpu_range_allocated) {
    (void)on_gpu_range_allocated;
}
} // namespace BbGuestHooks

namespace BbFreeCheck {
bool Enabled() {
    return false;
}
void Check(std::uint64_t address, std::uint64_t size, const void* data, Source source,
           std::uint64_t seq) {
    (void)address;
    (void)size;
    (void)data;
    (void)source;
    (void)seq;
}
std::uint64_t NextFenceSeq() {
    return 0;
}
void NoteFenceDecoded(std::uint64_t label, std::uint64_t value, const void* packet,
                      const void* buffer, std::uint64_t submit) {
    (void)label;
    (void)value;
    (void)packet;
    (void)buffer;
    (void)submit;
}
void NoteFenceWriting(std::uint64_t label) {
    (void)label;
}
void NoteFenceWritten(std::uint64_t label, std::uint64_t write_ns) {
    (void)label;
    (void)write_ns;
}
bool OnTrapFault(void* ucontext, std::uint64_t address) {
    (void)ucontext;
    (void)address;
    return false;
}
bool OnStaleTrapFault(std::uint64_t address) {
    (void)address;
    return false;
}
void NoteSubmit(std::uint64_t submit, const void* buffer, std::uint64_t size) {
    (void)submit;
    (void)buffer;
    (void)size;
}
void DumpAtFault(std::uint64_t rax, std::uint64_t r14) {
    (void)rax;
    (void)r14;
}
} // namespace BbFreeCheck

namespace BbGnmHooks {
void PatchImage(unsigned char* image, std::uint64_t size) {
    (void)image;
    (void)size;
}
void CheckSubmission(const std::uint32_t* commands, std::uint64_t dwords) {
    (void)commands;
    (void)dwords;
}
DriverWrite::~DriverWrite() = default;
} // namespace BbGnmHooks

namespace BbGuestMemory {
bool Usable(const Vulkan::Instance& instance) {
    (void)instance;
    return false;
}
bool PcModelGpu(const Vulkan::Instance& instance) {
    (void)instance;
    return false;
}
void Install(const Vulkan::Instance& instance) {
    (void)instance;
}
const Chunk* Find(std::uint64_t phys) {
    (void)phys;
    return nullptr;
}
} // namespace BbGuestMemory
#endif
