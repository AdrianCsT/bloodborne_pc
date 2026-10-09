// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone CPU checks of the shader cache's consistency rules: no game, Vulkan device or window.
//   ninja -C out/gpu cache-consistency-test && out/gpu/cache-consistency-test.exe
// The same file with BB_CACHE_STORAGE_TEST also checks the blobs on disk (Storage::DataBase), which
// needs the port's GPU library for the file helpers and the log:
//   ninja -C out/gpu cache-storage-test && out/gpu/cache-storage-test.exe
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
#include "video_core/renderer_vulkan/vk_cache_consistency.h"
#ifdef BB_CACHE_STORAGE_TEST
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <thread>
#include "common/elf_info.h"
#include "common/path_util.h"
#include "video_core/cache_storage.h"

// ElfInfo's fields are private to Core::Emulator; the port fills them in shim/bbgpu.cpp, here the
// test names the game.
namespace Core {
class Emulator {
public:
    static void SetSerial(const char* serial) {
        auto& info = Common::ElfInfo::Instance();
        info.initialized = true;
        info.game_serial = serial;
    }
};
} // namespace Core
#endif

using namespace Vulkan::CacheCheck;
using Shader::Backend::Bindings;

namespace {

// Stage numbers as in Shader::SwStage.
constexpr size_t Fragment = 0, TessControl = 1, TessEval = 2, Vertex = 3;

StageBindings Stage(Bindings start, Bindings size, bool has_resources = true) {
    return {.present = true, .has_resources = has_resources, .start = start, .size = size};
}

#ifdef BB_CACHE_STORAGE_TEST
namespace fs = std::filesystem;

// Saves are queued to the cache's own thread: wait until the file shows up.
bool Appears(const fs::path& path) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!fs::exists(path)) {
        if (std::chrono::steady_clock::now() > deadline) {
            return false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return true;
}

// Always evaluated: most calls below have effects.
#define CHECK(cond) \
    do { \
        if (!(cond)) { \
            std::printf("FAIL line %d: %s\n", __LINE__, #cond); \
            std::abort(); \
        } \
    } while (0)
// What WarmUp relies on in the cache on disk: ForEachBlob hands out each blob under its file
// name without the extension (the name Save and Remove take), and Remove deletes that blob and
// nothing else. The port always runs with a cache directory (EmulatorSettings::
// IsPipelineCacheArchived() is fixed to false in shim/core/emulator_settings.h), so the
// "Remove does nothing in an archive" branch cannot be reached from a test.
void CheckBlobsOnDisk() {
    using Storage::BlobType;
    const auto root = fs::temp_directory_path() /
                      ("bbport-cache-test-" +
                       std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(root);
    Common::FS::SetUserPath(Common::FS::PathType::CacheDir, root);
    Core::Emulator::SetSerial("CUSATEST0");
    auto& db = Storage::DataBase::Instance();
    CHECK(!db.Remove(BlobType::PipelineKey, "0x0000000000000001")); // not open
    db.Open();
    const auto dir = root / "CUSATEST0";
    CHECK(fs::is_directory(dir));

    const std::string first = "0x0000000000000001", second = "0x0000000000000002";
    CHECK(db.Save(BlobType::PipelineKey, first, std::vector<u8>{1, 2, 3}));
    CHECK(db.Save(BlobType::PipelineKey, second, std::vector<u8>{4}));
    CHECK(db.Save(BlobType::ShaderMeta, first, std::vector<u8>{9, 9}));
    CHECK(Appears(dir / (first + ".key")) && Appears(dir / (second + ".key")) &&
           Appears(dir / (first + ".meta")));
    // A write cut short (its temporary name) and the driver's cache are no blobs.
    std::ofstream{dir / ("0x0000000000000003.key.tmp"), std::ios::binary} << "half";
    CHECK(db.SaveDriverCache(std::vector<u8>{7}));

    std::map<std::string, std::vector<u8>> seen;
    db.ForEachBlob(BlobType::PipelineKey, [&](const std::string& name, std::vector<u8>&& data) {
        seen[name] = std::move(data);
    });
    CHECK(seen.size() == 2);
    CHECK(seen.at(first) == (std::vector<u8>{1, 2, 3}) && seen.at(second) == std::vector<u8>{4});
    std::map<std::string, std::vector<u8>> metas;
    db.ForEachBlob(BlobType::ShaderMeta, [&](const std::string& name, std::vector<u8>&& data) {
        metas[name] = std::move(data);
    });
    CHECK(metas.size() == 1 && metas.at(first) == (std::vector<u8>{9, 9}));

    // Remove: that blob of that type, once.
    CHECK(db.Remove(BlobType::PipelineKey, first));
    CHECK(!fs::exists(dir / (first + ".key")) && fs::exists(dir / (first + ".meta")) &&
           fs::exists(dir / (second + ".key")));
    CHECK(!db.Remove(BlobType::PipelineKey, first));
    // The names ForEachBlob gave are the ones Remove takes, as in WarmUp's sweep of unused entries.
    std::vector<std::string> names;
    db.ForEachBlob(BlobType::PipelineKey,
                   [&](const std::string& name, std::vector<u8>&&) { names.push_back(name); });
    CHECK(names == std::vector<std::string>{second});
    for (const auto& name : names) {
        CHECK(db.Remove(BlobType::PipelineKey, name));
    }
    db.ForEachBlob(BlobType::PipelineKey, [&](const std::string&, std::vector<u8>&&) { CHECK(false); });
    CHECK(fs::exists(dir / "0x0000000000000003.key.tmp") && fs::exists(dir / (first + ".meta")));

    db.Close();
    CHECK(!db.Remove(BlobType::ShaderMeta, first)); // closed again
    CHECK(fs::exists(dir / (first + ".meta")));
    std::error_code ec;
    fs::remove_all(root, ec);
    std::puts("  blobs on disk: ForEachBlob names, Remove: PASS");
}
#endif

} // namespace

int main() {
    std::array<StageBindings, 6> stages{};

    // A fragment shader of 19 descriptors (7 buffers) and the vertex shader compiled behind it.
    stages[Fragment] = Stage({0, 0, 0}, {19, 7, 2});
    stages[Vertex] = Stage({19, 7, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // The same vertex shader read back from a cache in which a later session wrote another
    // permutation under its name: it was compiled behind a fragment shader of 17 descriptors. The
    // layout built from the fragment shader gives binding 17 to a fragment sampler (the logged
    // VUID-VkGraphicsPipelineCreateInfo-layout-07988 of the shipped build).
    stages[Vertex] = Stage({17, 7, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // Each of the three counters is checked.
    stages[Vertex] = Stage({19, 6, 2}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));
    stages[Vertex] = Stage({19, 7, 1}, {5, 5, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // A vertex-only pipeline whose vertex shader was compiled behind a fragment shader.
    stages = {};
    stages[Vertex] = Stage({3, 1, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));
    stages[Vertex] = Stage({0, 0, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // A stage without resources keeps whatever start it was compiled with: it names no descriptor.
    stages = {};
    stages[Fragment] = Stage({0, 0, 0}, {4, 2, 1});
    stages[Vertex] = Stage({9, 9, 9}, {0, 0, 0}, false);
    assert(FirstMisplacedStage(stages) == -1);
    // ... but it still adds its own size for the stages after it.
    stages = {};
    stages[Fragment] = Stage({0, 0, 0}, {4, 2, 1});
    stages[TessControl] = Stage({9, 9, 9}, {2, 0, 3}, false);
    stages[TessEval] = Stage({6, 2, 4}, {1, 1, 0});
    stages[Vertex] = Stage({7, 3, 4}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);
    stages[Vertex] = Stage({6, 3, 4}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == int(Vertex));

    // Absent stages add nothing, and no stage at all is consistent.
    stages = {};
    assert(FirstMisplacedStage(stages) == -1);
    stages[Fragment] = Stage({0, 0, 0}, {2, 1, 0});
    stages[Vertex] = Stage({2, 1, 0}, {1, 1, 0});
    assert(FirstMisplacedStage(stages) == -1);

    // A compute pipeline is one stage that starts at zero.
    const std::array<StageBindings, 1> compute{Stage({0, 0, 0}, {8, 4, 1})};
    assert(FirstMisplacedStage(compute) == -1);
    const std::array<StageBindings, 1> late_compute{Stage({1, 0, 0}, {8, 4, 1})};
    assert(FirstMisplacedStage(late_compute) == 0);

    // The profile blob: versions first, then the profile, and nothing else.
    constexpr size_t profile_size = 24;
    const std::array<u32, 4> versions{0x42425043u, 10, 7, 6};
    std::vector<u8> blob(sizeof(versions) + profile_size, 0xAB);
    std::memcpy(blob.data(), versions.data(), sizeof(versions));
    assert(HeaderMatches(blob, versions, profile_size));
    const std::array<u32, 4> bumped{0x42425043u, 11, 7, 6};
    assert(!HeaderMatches(blob, bumped, profile_size));
    // A blob of an older build holds the profile alone.
    assert(!HeaderMatches(std::vector<u8>(profile_size, 0xAB), versions, profile_size));
    blob.push_back(0);
    assert(!HeaderMatches(blob, versions, profile_size));
    assert(!HeaderMatches(std::vector<u8>{}, versions, profile_size));

#ifdef BB_CACHE_STORAGE_TEST
    CheckBlobsOnDisk();
#endif
    std::puts("PASS");
    return 0;
}
