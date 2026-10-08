#!/usr/bin/env bash
# Builds dist/bbport-windows/ (and dist/bbport-windows.zip): BLauncher.exe (the launcher, frozen
# with PyInstaller so players need no Python), bb-probe.exe with the MSYS2 CLANG64 DLLs it needs,
# PkgTool (extracts the game from .pkg files), the preparation scripts and run.py. Run from an MSYS2 CLANG64 shell after `bash build.sh`.
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
out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller pillow
# The scripts run inside BLauncher.exe (--script): the standard modules they import come along.
hidden=()
for module in argparse base64 collections hashlib json re shutil struct tempfile xml.etree.ElementTree \
              urllib.request ctypes.wintypes; do
    hidden+=(--hidden-import "$module")
done
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed \
    --name BLauncher --icon "$(cygpath -w "$PWD/launcher/bloodborne.ico")" --distpath out/pyi-dist \
    --workpath out/pyi-work --specpath out/pyi-work --paths "$(cygpath -w "$PWD/scripts")" "${hidden[@]}" \
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
mkdir -p "$dest/bin/pkgtool" "$dest/licenses"
cp out/pkgtool/PkgTool.exe out/pkgtool/LibOrbisPkg.dll "$dest/bin/pkgtool/"
cp out/pkgtool/LICENSE.txt "$dest/licenses/PkgTool-LICENSE.txt"
find "$dest" -name __pycache__ -prune -exec rm -r {} +
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
    mkdir -p "$play"
    find "$play" -mindepth 1 -maxdepth 1 ! -name user ! -name mods ! -name bbport.ini \
        ! -name mods.json ! -name patches.json -exec rm -rf {} +
    cp -r "$dest/." "$play/"
fi
du -sh "$dest" dist/bbport-windows.zip
