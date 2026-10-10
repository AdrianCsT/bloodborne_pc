#!/usr/bin/env bash
# Builds dist/bbport-windows/ (and dist/bbport-windows.zip): BLauncher.exe (the launcher, frozen
# with PyInstaller so players need no Python), bb-probe.exe with the MSYS2 CLANG64 DLLs it needs,
# PkgTool (extracts the game from .pkg files), ReShade (optional post-processing, bin/reshade), Intel XeSS
# (libxess.dll, the XeSS upscaler's runtime), the
# preparation scripts and run.py. Run from an MSYS2 CLANG64 shell after `bash build.sh`.
# Freezing uses a Windows Python 3.10+ (python.org; WINPYTHON overrides) and a private venv in
# out/pyenv with PyInstaller. FSR 4 assets in fsr4_shaders/ are included when present.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
source ./msys2-env.sh
[[ -f out/bb-probe.exe && -f out/bb-gpu-capabilities.exe && -f out/bb-play.exe ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }

# A Windows Python (not MSYS2's) for PyInstaller.
python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        [[ -x $candidate ]] && python=$candidate
    done
fi
[[ -n $python ]] || { echo 'Need a Windows Python 3 (python.org) or WINPYTHON=path\to\python.exe' >&2; exit 1; }
# Windows Python needs USERPROFILE (some MSYS2 shells start without it).
export USERPROFILE=${USERPROFILE:-$(cygpath -w "/c/Users/$(id -un)")}
if [[ ! -x out/pyenv/Scripts/python.exe ]]; then
    "$python" -m venv out/pyenv
fi
# Only when missing: pip asks PyPI on every call and hangs for minutes without a connection.
out/pyenv/Scripts/python.exe -c 'import PyInstaller, PIL' 2>/dev/null ||
    out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller pillow
# The scripts run inside BLauncher.exe (--script): the standard modules they import come along.
hidden=()
for module in argparse base64 collections hashlib json re shutil struct tempfile xml.etree.ElementTree \
              urllib.request ctypes.wintypes; do
    hidden+=(--hidden-import "$module")
done
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed \
    --name BLauncher --icon "$(cygpath -w "$PWD/launcher/bloodborne.ico")" --distpath out/pyi-dist \
    --workpath out/pyi-work --specpath out/pyi-work --paths "$(cygpath -w "$PWD/scripts")" \
    --paths "$(cygpath -w "$PWD/tools")" --hidden-import fetch_fsr4vk "${hidden[@]}" \
    "$(cygpath -w "$PWD/launcher/bbport_launcher_win.py")"

# PkgTool (maxton/LibOrbisPkg v0.2, LGPL-3.0, shipped unmodified) extracts the game from the .pkg files the
# launcher's "Install from PKG" takes. The zip is cached in out/pkgtool-cache, so rebuilds work offline.
pkgtool_zip=PkgTool-0.2.231.zip
pkgtool_url=https://github.com/maxton/LibOrbisPkg/releases/download/v0.2/$pkgtool_zip
pkgtool_sha256=c639e591e35c2431f68410d1771541d95f7308863638f9e3a6eedf1818530097
mkdir -p out/pkgtool-cache
if [[ ! -f out/pkgtool-cache/$pkgtool_zip ]]; then
    curl -fsSL --retry 3 -o "out/pkgtool-cache/$pkgtool_zip.part" "$pkgtool_url"
    mv -f "out/pkgtool-cache/$pkgtool_zip.part" "out/pkgtool-cache/$pkgtool_zip"
fi
if ! echo "$pkgtool_sha256 *out/pkgtool-cache/$pkgtool_zip" | sha256sum -c --status -; then
    rm -f "out/pkgtool-cache/$pkgtool_zip"
    echo "$pkgtool_zip does not match its SHA-256 (deleted); run again" >&2
    exit 1
fi
rm -rf out/pkgtool
mkdir -p out/pkgtool
unzip -oq "out/pkgtool-cache/$pkgtool_zip" -d out/pkgtool
[[ -f out/pkgtool/PkgTool.exe && -f out/pkgtool/LibOrbisPkg.dll && -f out/pkgtool/LICENSE.txt ]] ||
    { echo "$pkgtool_zip is missing PkgTool.exe, LibOrbisPkg.dll or LICENSE.txt" >&2; exit 1; }

# ReShade 6.8.0 (crosire/reshade, BSD-3-Clause), the optional post-processing the launcher turns on per
# game run (bin/reshade). The official installer is a stub with a zip appended; ReShade64.dll is read
# from it and checked against a pinned SHA-256. Cached in out/reshade-cache like PkgTool.
reshade_exe=ReShade_Setup_6.8.0.exe
reshade_url=https://reshade.me/downloads/$reshade_exe
reshade_exe_sha256=207aea16205fbf952bc8fe1879966672454cf04002e7ad34237c7990a5b3c0b4
reshade_dll_sha256=b2945c29e7095491a901746b400e58db9b1592ab092bacf2a888ce37f02d08da
mkdir -p out/reshade-cache
if [[ ! -f out/reshade-cache/$reshade_exe ]]; then
    curl -fsSL --retry 3 -o "out/reshade-cache/$reshade_exe.part" "$reshade_url"
    mv -f "out/reshade-cache/$reshade_exe.part" "out/reshade-cache/$reshade_exe"
fi
if ! echo "$reshade_exe_sha256 *out/reshade-cache/$reshade_exe" | sha256sum -c --status -; then
    rm -f "out/reshade-cache/$reshade_exe"
    echo "$reshade_exe does not match its SHA-256 (deleted); run again" >&2
    exit 1
fi
rm -rf out/reshade
mkdir -p out/reshade
unzip -oq "out/reshade-cache/$reshade_exe" ReShade64.dll -d out/reshade || [[ $? == 1 ]]  # 1: warns about the stub
echo "$reshade_dll_sha256 *out/reshade/ReShade64.dll" | sha256sum -c --status - ||
    { echo "ReShade64.dll from $reshade_exe does not match its SHA-256" >&2; exit 1; }

# Intel XeSS SDK 3.0.2 (github.com/intel/xess, Intel Simplified Software License): libxess.dll, the Super
# Resolution the XeSS upscaler loads at run time (it is DP4a based and runs on AMD, Intel and NVIDIA GPUs). The
# license allows redistributing the unmodified binary with its license text; both are shipped untouched.
# The release zip (77 MB) is cached in out/xess-cache like PkgTool and ReShade and pinned by SHA-256.
xess_zip=XeSS_SDK_3.0.2.zip
xess_url=https://github.com/intel/xess/releases/download/v3.0.2/$xess_zip
xess_zip_sha256=88b8a373f30e33f3558a77a93e634f11b8132fc3047ea1a8edeead32b8471990
xess_dll_sha256=251659dd84a3e84de67c886a4186e01f3eca49b00641906fe38bb6b807e5d5b7
mkdir -p out/xess-cache
if [[ ! -f out/xess-cache/$xess_zip ]]; then
    curl -fsSL --retry 3 -o "out/xess-cache/$xess_zip.part" "$xess_url"
    mv -f "out/xess-cache/$xess_zip.part" "out/xess-cache/$xess_zip"
fi
if ! echo "$xess_zip_sha256 *out/xess-cache/$xess_zip" | sha256sum -c --status -; then
    rm -f "out/xess-cache/$xess_zip"
    echo "$xess_zip does not match its SHA-256 (deleted); run again" >&2
    exit 1
fi
rm -rf out/xess
mkdir -p out/xess
unzip -oq "out/xess-cache/$xess_zip" bin/libxess.dll LICENSE.txt third-party-programs.txt -d out/xess
echo "$xess_dll_sha256 *out/xess/bin/libxess.dll" | sha256sum -c --status - ||
    { echo "libxess.dll from $xess_zip does not match its SHA-256" >&2; exit 1; }

# The package is assembled in a fresh staging folder and zipped from there; dist/bbport-windows
# (a playable copy that may hold saves and settings) is only refreshed afterwards.
dest=out/stage/bbport-windows
rm -rf -- out/stage
mkdir -p "$dest/bin" "$dest/launcher"
cp -r out/pyi-dist/BLauncher/. "$dest/"
llvm-strip -o "$dest/Bloodborne.exe" out/bb-play.exe
cp launcher/bloodborne.ico launcher/bloodborne.png "$dest/launcher/"
# The executables without debug information (out/ keeps the symbols for crash reports).
for exe in bb-probe.exe bb-gpu-capabilities.exe; do
    llvm-strip --strip-debug -o "$dest/bin/$exe" "out/$exe"
done
# Every DLL the executables load from the CLANG64 tree (SDL3, FFmpeg, Vulkan loader, ...).
ldd "$dest/bin/bb-probe.exe" "$dest/bin/bb-gpu-capabilities.exe" |
    awk '/\/clang64\/bin\// {print $3}' | sort -u | while read -r dll; do
        cp -u "$dll" "$dest/bin/"
    done
cp -r scripts patches "$dest/"
cp run.py LICENSE README.md packaging/windows/README-Windows.txt "$dest/"
if [[ -d fsr4_shaders ]]; then cp -r fsr4_shaders "$dest/"; fi
# DLSS (NVIDIA RTX): the MSVC-built bridge and NVIDIA's runtime, next to bb-probe.exe
# (packaging/windows/build_dlss.sh). Without them the DLSS option stays unavailable.
if [[ -f out/bbport_dlss.dll && -f out/nvngx_dlss.dll ]]; then
    cp out/bbport_dlss.dll out/nvngx_dlss.dll "$dest/bin/"
    mkdir -p "$dest/licenses" && cp out/NVIDIA-DLSS-LICENSE.txt "$dest/licenses/"
    cp gpu/dlss_bridge/LICENSE.txt "$dest/licenses/bbport_dlss-LICENSE.txt"
else
    echo "DLSS bridge not built (packaging/windows/build_dlss.sh): no DLSS in this package" >&2
fi
# XeSS (Intel): libxess.dll next to bb-probe.exe, unmodified (not stripped), with Intel's license and third-party notices.
mkdir -p "$dest/licenses"
cp out/xess/bin/libxess.dll "$dest/bin/"
cp out/xess/LICENSE.txt "$dest/licenses/Intel-XeSS-LICENSE.txt"
cp out/xess/third-party-programs.txt "$dest/licenses/Intel-XeSS-third-party-programs.txt"
# The AMD FidelityFX SDK (MIT) and FireBurn's FSR-Vulkan port of it (MIT) are linked into the game's GPU
# library, so their licenses ship with it.
fsr_vulkan=gpu/third_party/fsr-vulkan
cp "$fsr_vulkan/upstream/ffx-1.1.4/sdk/LICENSE.txt" "$dest/licenses/AMD-FidelityFX-SDK-LICENSE.txt"
cp "$fsr_vulkan/LICENSE.txt" "$dest/licenses/FireBurn-FSR-Vulkan-LICENSE.txt"
mkdir -p "$dest/bin/pkgtool" "$dest/licenses"
cp out/pkgtool/PkgTool.exe out/pkgtool/LibOrbisPkg.dll "$dest/bin/pkgtool/"
cp out/pkgtool/LICENSE.txt "$dest/licenses/PkgTool-LICENSE.txt"
# ReShade: the DLL, our layer manifest (it names the layer the launcher enables), its settings, only the
# effects the presets use, the presets, and the licence of each. textures and screenshots hold a note
# so the folders exist in the zip (ReShade saves screenshots into one and does not create it).
reshade_src=packaging/windows/reshade
mkdir -p "$dest/bin/reshade/textures" "$dest/bin/reshade/screenshots"
cp out/reshade/ReShade64.dll "$reshade_src/VK_LAYER_bbport_reshade.json" "$reshade_src/ReShade.ini" "$dest/bin/reshade/"
cp -r "$reshade_src/shaders" "$reshade_src/presets" "$dest/bin/reshade/"
echo 'Texture files (.png) that effects load go here.' > "$dest/bin/reshade/textures/README.txt"
echo 'ReShade saves its screenshots here (the key is Print Screen).' > "$dest/bin/reshade/screenshots/README.txt"
cp "$reshade_src"/licenses/*.txt "$dest/licenses/"
find "$dest" -name __pycache__ -prune -exec rm -r {} +
# fsr4vk (GPLv3; experimental FSR 4.1.1) is downloaded by the launcher into <install>\fsr4vk: none of it ships here.
if [[ -n $(find "$dest" \( -type d -iname fsr4vk -o -iname 'amd_fidelityfx_upscaler_vk*' \)) ]]; then
    echo 'an fsr4vk file ended up in the package; it must be downloaded by the launcher' >&2
    exit 1
fi
mkdir -p dist
rm -f dist/bbport-windows.zip
(cd out/stage && powershell -NoProfile -Command \
    "Compress-Archive -Path bbport-windows -DestinationPath ../../dist/bbport-windows.zip")

# Refresh dist/bbport-windows, keeping what players create there (saves, settings, mods), and
# only while nothing runs from it: deleting a running launcher's files breaks it.
play=dist/bbport-windows
running=$(powershell -NoProfile -Command \
    "@(Get-Process | Where-Object { \$_.Path -like '$(cygpath -w "$PWD/$play")\\*' }).Count" | tr -d '\r')
if [[ ${running:-0} != 0 ]]; then
    echo "dist/bbport-windows is in use ($running processes): not refreshed; the zip is ready." >&2
else
    # The ReShade settings and presets of the playable copy survive too (the launcher's updater keeps them).
    rm -rf out/reshade-keep
    if [[ -f $play/bin/reshade/ReShade.ini ]]; then
        mkdir -p out/reshade-keep
        cp -r "$play/bin/reshade/ReShade.ini" "$play/bin/reshade/presets" out/reshade-keep/
    fi
    mkdir -p "$play"
    # The launcher updater's USER_FILES, plus the FSR 4 assets the launcher downloads.
    find "$play" -mindepth 1 -maxdepth 1 ! -name user ! -name out ! -name mods ! -name bbport.ini \
        ! -name mods.json ! -name patches.json ! -name last_run.log ! -name fsr4vk \
        ! -name fsr4_shaders -exec rm -rf {} +
    cp -r "$dest/." "$play/"
    if [[ -d out/reshade-keep ]]; then
        cp out/reshade-keep/ReShade.ini "$play/bin/reshade/"
        cp -r out/reshade-keep/presets/. "$play/bin/reshade/presets/"
        rm -rf out/reshade-keep
    fi
fi
du -sh "$dest" dist/bbport-windows.zip
