# SPDX-License-Identifier: GPL-2.0-or-later
"""Adds the game to Steam as a non-Steam shortcut (Steam's userdata/<id>/config/shortcuts.vdf).

shortcuts.vdf is Valve's binary KeyValues: a type byte (0 = map, 1 = string, 2 = int32,
7 = uint64), a NUL-terminated key, then the value; 8 ends a map. The file holds one map,
"shortcuts", whose entries are maps named "0", "1", ...

Based on launcher/bbport_steam.py of Supermedo/bloodborne_pc (commit e076119, GPL-2.0-or-later);
Steam has to be closed (it rewrites the file as it exits), the first backup is kept, and the file is
replaced in one step.
"""
import ctypes
import os
from pathlib import Path
import struct
import zlib

MAP, STRING, INT32, UINT64, END = 0, 1, 2, 7, 8


def _cstring(data, at):
    end = data.index(b'\0', at)
    return data[at:end].decode('utf-8', errors='replace'), end + 1


def parse(data, at=0):
    """A map as a list of (type, key, value); nested maps are lists too. Returns (items, next)."""
    items = []
    while at < len(data):
        kind = data[at]
        at += 1
        if kind == END:
            return items, at
        key, at = _cstring(data, at)
        if kind == MAP:
            value, at = parse(data, at)
        elif kind == STRING:
            value, at = _cstring(data, at)
        elif kind == INT32:
            value, at = struct.unpack_from('<i', data, at)[0], at + 4
        elif kind == UINT64:
            value, at = struct.unpack_from('<Q', data, at)[0], at + 8
        else:
            raise ValueError(f'unknown field type {kind} in shortcuts.vdf')
        items.append((kind, key, value))
    return items, at


def dump(items):
    out = bytearray()
    for kind, key, value in items:
        out += bytes([kind]) + key.encode('utf-8') + b'\0'
        if kind == MAP:
            out += dump(value) + bytes([END])
        elif kind == STRING:
            out += str(value).encode('utf-8') + b'\0'
        elif kind == INT32:
            out += struct.pack('<i', value)
        elif kind == UINT64:
            out += struct.pack('<Q', value)
    return bytes(out)


def shortcut_id(exe, name):
    """The id Steam gives a non-Steam game: CRC32 of the quoted exe and the name, top bit set."""
    return (zlib.crc32((exe + name).encode('utf-8')) & 0xffffffff) | 0x80000000


def entry(name, exe, start_dir, icon, options=''):
    quoted = f'"{exe}"'
    appid = shortcut_id(quoted, name)
    return [
        (INT32, 'appid', struct.unpack('<i', struct.pack('<I', appid))[0]),
        (STRING, 'AppName', name), (STRING, 'Exe', quoted), (STRING, 'StartDir', f'"{start_dir}"'),
        (STRING, 'icon', icon), (STRING, 'ShortcutPath', ''), (STRING, 'LaunchOptions', options),
        (INT32, 'IsHidden', 0), (INT32, 'AllowDesktopConfig', 1), (INT32, 'AllowOverlay', 1),
        (INT32, 'OpenVR', 0), (INT32, 'Devkit', 0), (STRING, 'DevkitGameID', ''),
        (INT32, 'DevkitOverrideAppID', 0), (INT32, 'LastPlayTime', 0), (STRING, 'FlatpakAppID', ''),
        (MAP, 'tags', []),
    ]


def add_shortcut(vdf_path, name, exe, start_dir, icon, options=''):
    """Adds the shortcut to one shortcuts.vdf, or updates the entry that starts the same exe.
    Returns 'added' or 'updated'. The file as it was before the first change is kept as
    shortcuts.vdf.bak (a later change does not replace it). Raises OSError or ValueError, and then
    leaves the file as it was."""
    vdf_path = Path(vdf_path)
    data = vdf_path.read_bytes() if vdf_path.exists() else b''
    root = parse(data)[0] if data else []
    shortcuts = next((value for kind, key, value in root if kind == MAP and key.lower() == 'shortcuts'), None)
    if shortcuts is None:
        shortcuts = []
        root.append((MAP, 'shortcuts', shortcuts))
    new = entry(name, exe, start_dir, icon, options)
    target = f'"{exe}"'.lower()
    for index, (kind, key, value) in enumerate(shortcuts):
        fields = {k.lower(): v for _t, k, v in value} if kind == MAP else {}
        if str(fields.get('exe', '')).lower() == target:
            # Keep what Steam or the player added (play time, tags).
            kept = {'lastplaytime', 'tags'}
            old = {k.lower(): (t, k, v) for t, k, v in value}
            shortcuts[index] = (MAP, key, [old[k.lower()] if k.lower() in kept and k.lower() in old else (t, k, v)
                                            for t, k, v in new])
            result = 'updated'
            break
    else:
        shortcuts.append((MAP, str(len(shortcuts)), new))
        result = 'added'
    backup = vdf_path.with_name(vdf_path.name + '.bak')
    if data and not backup.exists():
        backup.write_bytes(data)
    vdf_path.parent.mkdir(parents=True, exist_ok=True)
    partial = vdf_path.with_name(vdf_path.name + '.tmp')
    partial.write_bytes(dump(root) + bytes([END]))
    os.replace(partial, vdf_path)
    return result


def steam_folder():
    """Steam's install folder from the registry, or None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            path = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
    except OSError:
        return None
    return path if path.is_dir() else None


def user_configs(steam):
    """userdata/<account>/config folders of the accounts that used Steam on this PC."""
    users = Path(steam) / 'userdata'
    return [d / 'config' for d in sorted(users.iterdir()) if d.is_dir() and d.name.isdigit() and d.name != '0'] \
        if users.is_dir() else []


def steam_running():
    """Whether steam.exe is in the process list (read from a snapshot, no helper program started)."""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', wintypes.WCHAR * 260)]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return False
    try:
        found = ProcessEntry()
        found.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(found))
        while more:
            if found.szExeFile.lower() == 'steam.exe':
                return True
            more = kernel32.Process32NextW(snapshot, ctypes.byref(found))
        return False
    finally:
        kernel32.CloseHandle(snapshot)


def add_to_accounts(steam, name, exe, start_dir, icon, options=''):
    """Adds the shortcut for every Steam account of STEAM (its install folder). Returns (done, failed):
    done lists (shortcuts.vdf path, 'added' or 'updated'), failed lists (path, the error); a file that
    cannot be written is left as it was and does not stop the others."""
    done, failed = [], []
    for config in user_configs(steam):
        path = config / 'shortcuts.vdf'
        try:
            done.append((path, add_shortcut(path, name, exe, start_dir, icon, options)))
        except (OSError, ValueError) as error:
            failed.append((path, error))
    return done, failed
