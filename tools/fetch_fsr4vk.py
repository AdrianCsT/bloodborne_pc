#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Downloads the fsr4vk provider (experimental FSR 4.1.1 on Windows) into a folder.

    fetch_fsr4vk.py [--dir DIR] [--check]

fsr4vk (https://github.com/dvj5411/fsr4vk, GPLv3; its AMD shader and model data are MIT) is a Vulkan
FFX provider for the INT8 FSR 4.1.1 model. The port loads its DLL at run time (vk_fsr4vk.cpp) and
does not ship it: this script fetches the pinned upstream release zip, checks its SHA-256, extracts
the provider DLL and licenses, and checks the DLL's SHA-256. Resumable (HTTP Range into a .part
file), stdlib only, so the launcher can import `fetch` or run it as a process.

DIR defaults to BB_FSR4VK_DIR, else the `fsr4vk` folder next to this repository's executables.
Exit status 0: the folder holds the verified files. 1: failed (the reason is the last line on stderr).
"""
import argparse
import hashlib
import http.client
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

RELEASE = 'v0.4.3'
SOURCE_URL = 'https://github.com/dvj5411/fsr4vk/tree/7c04e511195bf4420a060d64df84d625a37457e0'
ZIP_URL = 'https://github.com/dvj5411/fsr4vk/releases/download/v0.4.3/fsr4vk-v0.4.3.zip'
ZIP_NAME = 'fsr4vk-v0.4.3.zip'
ZIP_SIZE = 20452070
ZIP_SHA256 = '3dd2fe7a6b14a1d045c23aa51b63cb45a53576d64eba5f96cb0e334e55ce9827'

# (name inside the zip, name in DIR, size, SHA-256)
FILES = (
    ('OptiScaler/amd_fidelityfx_upscaler_vk.dll', 'amd_fidelityfx_upscaler_vk.dll', 18034864,
     '25025f9a6acef5636d49ca9ae8d87ebeb77f8891083d9657ca64deec1a12a440'),
    ('LICENSES/GPL-3.0.txt', 'LICENSES/GPL-3.0.txt', 35149,
     '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'),
    ('LICENSES/AMD-FidelityFX-SDK-MIT.md', 'LICENSES/AMD-FidelityFX-SDK-MIT.md', 1119,
     'be05d8cd5489deff924dbe91e0c5b8c0ff034d43cd11749bf9a67a0c38252b7b'),
    ('LICENSES/Zstandard-BSD.txt', 'LICENSES/Zstandard-BSD.txt', 1549,
     '7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8'),
)
SOURCE_NOTE = (
    'fsr4vk ' + RELEASE + ' (GPLv3 provider, MIT AMD FidelityFX data).\n'
    'Downloaded from ' + ZIP_URL + '\n'
    'Source code of this release: ' + SOURCE_URL + '\n'
    'Licenses: LICENSES/. The Bloodborne port loads the DLL as a separate program and does not '
    'include its code.\n')
CHUNK = 1 << 20


class FetchError(Exception):
    pass


def default_dir() -> Path:
    env = os.environ.get('BB_FSR4VK_DIR')
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / 'fsr4vk'


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def verified(path: Path, size: int, sha256: str) -> bool:
    try:
        return path.stat().st_size == size and sha256_of(path) == sha256
    except OSError:
        return False


def files_ok(target: Path) -> bool:
    """True when every pinned file is in `target` with its pinned size and hash."""
    return all(verified(target / name, size, sha) for _, name, size, sha in FILES)


def _download(part: Path, progress) -> None:
    """Fills `part` with the zip, resuming what an earlier run left; the caller checks the hash."""
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have > ZIP_SIZE:
            part.unlink()
            have = 0
        if have == ZIP_SIZE:
            return
        request = urllib.request.Request(ZIP_URL, headers={'User-Agent': 'bbport-fetch-fsr4vk'})
        if have:
            request.add_header('Range', f'bytes={have}-')
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if have and response.status != 206:
                    have = 0  # the server ignored the range: start again
                with open(part, 'ab' if have else 'wb') as out:
                    for block in iter(lambda: response.read(CHUNK), b''):
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, ZIP_SIZE)
        except urllib.error.HTTPError as error:
            if error.code == 416:  # the part is longer than the file: drop it
                part.unlink(missing_ok=True)
            elif attempt == 3:
                raise FetchError(f'download failed: HTTP {error.code} from {ZIP_URL}')
        except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as error:
            if attempt == 3:
                raise FetchError(f'download failed: {error}')
        if part.exists() and part.stat().st_size == ZIP_SIZE:
            return
    raise FetchError('download failed: the file stayed incomplete')


def fetch(target: Path, progress=None) -> None:
    """Puts the pinned, verified provider files into `target`. `progress(done, total)` is optional.
    Raises FetchError. Does nothing when `target` already holds them."""
    target = Path(target)
    if files_ok(target):
        return
    target.mkdir(parents=True, exist_ok=True)
    work = target / '.download'
    work.mkdir(exist_ok=True)
    part = work / (ZIP_NAME + '.part')
    _download(part, progress)
    if part.stat().st_size != ZIP_SIZE or sha256_of(part) != ZIP_SHA256:
        part.unlink(missing_ok=True)
        raise FetchError('the downloaded zip does not match the pinned SHA-256; it was deleted')
    try:
        with zipfile.ZipFile(part) as archive:
            for member, name, size, sha in FILES:
                data = archive.read(member)
                if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                    raise FetchError(f'{member} in the zip does not match its pinned SHA-256')
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + '.tmp')
                temporary.write_bytes(data)
                os.replace(temporary, destination)
    except zipfile.BadZipFile as error:
        raise FetchError(f'the zip is damaged: {error}')
    (target / 'SOURCE.txt').write_text(SOURCE_NOTE, encoding='utf-8')
    part.unlink(missing_ok=True)
    try:
        work.rmdir()
    except OSError:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', type=Path, default=None, help='target folder (default: BB_FSR4VK_DIR or ./fsr4vk)')
    parser.add_argument('--check', action='store_true', help='only verify the folder; download nothing')
    args = parser.parse_args(argv)
    target = args.dir or default_dir()
    if args.check:
        ok = files_ok(target)
        print(f'fsr4vk {RELEASE}: {"files verified" if ok else "files missing or changed"} in {target}')
        return 0 if ok else 1
    last = [-1]

    def progress(done, total):
        percent = done * 100 // total
        if percent != last[0] and percent % 5 == 0:
            last[0] = percent
            print(f'PROGRESS {done} {total}', flush=True)
    try:
        fetch(target, progress)
    except FetchError as error:
        print(f'fetch_fsr4vk: {error}', file=sys.stderr)
        return 1
    print(f'fsr4vk {RELEASE}: files verified in {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
