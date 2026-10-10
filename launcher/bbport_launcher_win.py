#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bloodborne launcher for Windows (Tkinter; launcher/bbport_launcher.py is the Linux one).

Every setting of the port in one window: the game folder and saves, bbport.ini (upscaler,
preset, output, effects), start-up options passed to run.py as environment variables
(frame rate, presentation, HDR, ...), mods, third-party patches and the FSR 4 assets.
Launcher options live in %APPDATA%/bbport-launcher/settings.json. The window has a Simple view
(Play and the few settings a player needs) and an Advanced one (every tab); BB_LAUNCHER_ANIMATIONS=0
turns the motion off.

Frozen with PyInstaller (packaging/windows/package.sh) the same BLauncher.exe also runs the
game without the window (`--play`), run.py (`--run`) and the preparation scripts (`--script`),
so a packaged port needs no Python installation.
"""
import collections
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import random
import re
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

FROZEN = getattr(sys, 'frozen', False)
PORT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PORT_DIR / 'scripts'))
sys.path.insert(0, str(PORT_DIR / 'tools'))  # fetch_fsr4vk.py in a source tree; the frozen launcher bundles it
try:
    import fetch_fsr4vk  # noqa: E402
    FETCH_FSR4VK_ERROR = ''
except Exception as _error:  # a missing or broken helper turns off the FSR 4.1.1 download only
    fetch_fsr4vk, FETCH_FSR4VK_ERROR = None, f'{type(_error).__name__}: {_error}'
DATA_DIR = Path(os.environ.get('BB_DATA_DIR', PORT_DIR))
CONFIG_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'bbport-launcher'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
PATCH_VERSION = '01.09'
MAX_LOG_LINES = 6000
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# This build; GitHub release tags are windows-v<VERSION>.
VERSION = '1.7.0-beta.2'
# GitHub's /releases list leaves nothing out (pre-releases included); /releases/latest hides them.
RELEASES_PER_PAGE = 100
RELEASE_PAGES = 3  # newest first; more than 300 newer entries of other tags would be a repository gone wrong
# By repository id, not owner/name: the id survives a rename of the owner, and whoever later takes an old
# owner name (AdrianCsT was renamed to 0xCydral) cannot serve releases to this launcher.
RELEASES_API = f'https://api.github.com/repositories/1409457490/releases?per_page={RELEASES_PER_PAGE}'
RELEASES_PAGE = 'https://github.com/0xCydral/bloodborne_pc/releases/latest'
PACKAGE_ASSET = 'bbport-windows.zip'  # a release can carry other zips too (the fsr4vk build and its source)
TAG_PREFIX = 'windows-v'


def version_tuple(text):
    """A sort key for '1.6.16', '1.7.0-beta.1', 'windows-v1.7.0' or a game version such as '01.09': the
    numbers, then 1 for a final release and 0 for a pre-release (so 1.7.0 is above 1.7.0-beta.2), then
    the pre-release parts (beta.10 above beta.2, rc above beta)."""
    match = re.match(r'\D*?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?', text or '')
    numbers = tuple(int(part) for part in match.group(1).split('.')) if match else ()
    numbers += (0,) * (4 - len(numbers))
    parts = tuple((0, int(part), '') if part.isdigit() else (1, 0, part)
                  for part in ((match and match.group(2)) or '').split('.') if part)
    return numbers, 0 if parts else 1, parts


def is_prerelease(version):
    return version_tuple(version)[1] == 0


def release_version(tag):
    """'1.7.0-beta.1' from the tag 'windows-v1.7.0-beta.1'."""
    return re.sub(r'^(?:windows-)?v', '', tag or '')


def newest_release(releases, beta):
    """(version, zip URL, page URL) of the newest release in RELEASES (GitHub's /releases list) that
    this launcher may offer: stable ones, and pre-releases only when BETA. None when there is none.
    Only windows-v<version> tags count; drafts never do."""
    if not isinstance(releases, list):
        raise ValueError('GitHub did not return a list of releases')
    best = None
    for release in releases:
        tag = release.get('tag_name') if isinstance(release, dict) else None
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX) or release.get('draft'):
            continue
        version = release_version(tag)
        if (release.get('prerelease') or is_prerelease(version)) and not beta:
            continue
        if best is None or version_tuple(version) > version_tuple(best[0]):
            assets = release.get('assets')
            package = next((asset for asset in assets if isinstance(asset, dict) and isinstance(asset.get('name'), str)
                            and asset['name'].lower() == PACKAGE_ASSET), None) if isinstance(assets, list) else None
            # no package, no URL: install_update then opens the release page instead of installing another zip
            best = (version, package.get('browser_download_url') if package else None, release.get('html_url') or RELEASES_PAGE)
    return best


def fetch_update_release(beta=False):
    """newest_release() of the releases GitHub lists now (raises OSError or ValueError on a failure).
    The list holds every tag, newest first: it is read page by page until a page has a release this
    launcher may offer, so a run of newer entries of other tags cannot hide it."""
    for page in range(1, RELEASE_PAGES + 1):
        request = urllib.request.Request(f'{RELEASES_API}&page={page}',
                                         headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'bbport-launcher'})
        with urllib.request.urlopen(request, timeout=15) as response:
            releases = json.load(response)
        found = newest_release(releases, beta)
        if found or len(releases) < RELEASES_PER_PAGE:
            return found
    return None
UPDATE_DIR = Path(tempfile.gettempdir()) / 'bbport-update'
# Never copied over an installation by an update (the package does not hold them either). fsr4vk is the
# experimental FSR 4.1.1 download (fsr4vk_dir()).
USER_FILES = ('user', 'out', 'mods', 'bbport.ini', 'mods.json', 'patches.json', 'last_run.log', 'fsr4vk')
# ReShade (packaging/windows/package.sh): turned on per game run through the Vulkan loader's layer
# variables, see game_environment().
RESHADE_DIR = PORT_DIR / 'bin' / 'reshade'
RESHADE_LAYER = 'VK_LAYER_bbport_reshade'
RESHADE_PRESET_PREFIX = 'Bloodborne - '  # the shipped presets; the dropdowns show the rest of the name


# ---------------------------------------------------------------------------------------------
# Command line roles of the frozen executable.

def attach_stdio():
    """A windowed executable starts without sys.stdout; inherited pipes or a console still exist."""
    import msvcrt
    for name, std in (('stdout', -11), ('stderr', -12)):
        if getattr(sys, name) is not None:
            continue
        stream = None
        handle = ctypes.windll.kernel32.GetStdHandle(std)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                stream = open(msvcrt.open_osfhandle(handle, os.O_WRONLY), 'w', encoding='utf-8',
                              errors='replace', buffering=1)
            except OSError:
                stream = None
        setattr(sys, name, stream or open(os.devnull, 'w'))


def run_role(argv):
    """--run [args]: run.py; --script <file> [args]: a preparation script. Returns an exit code."""
    attach_stdio()
    for stream in (sys.stdout, sys.stderr):  # keep messages in order with the game's output
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    if argv[0] == '--script':
        path, sys.argv = argv[1], argv[1:]
    else:
        path = str(PORT_DIR / 'run.py')
        sys.argv = [path, *argv[1:]]
    try:
        runpy.run_path(path, run_name='__main__')
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return 0


def run_command():
    """The command that starts run.py: this executable when frozen, else Python."""
    return [sys.executable, '--run'] if FROZEN else [sys.executable, str(PORT_DIR / 'run.py')]


# ---------------------------------------------------------------------------------------------
# Languages: every text is written in English with the Russian next to it; the other
# languages are in bbport_lang.py, keyed by the English text.

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bbport_lang  # noqa: E402
import bbport_controls  # noqa: E402
import bbport_steam  # noqa: E402

LANG = 'en'


WARN = '  ⚠'  # marks a risky choice; the text before it is translated as usual


def _(en, ru=None):
    if en.endswith(WARN):
        return _(en[:-len(WARN)], ru and ru.removesuffix(WARN)) + WARN
    if LANG == 'ru':
        return ru or en
    return bbport_lang.table(LANG).get(en) or en


def windows_language():
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff
    except (AttributeError, OSError):
        return 'en'
    return bbport_lang.WINDOWS_LANGUAGES.get(primary, 'en')


# ---------------------------------------------------------------------------------------------
# Settings. bbport.ini keys (the game reads them, the in-game menu edits them) and the
# launcher's own settings.json (passed to run.py as environment variables).

EFFECTS = [
    ('effect_chromatic_aberration', ('Chromatic aberration', 'Хроматическая аберрация'), True),
    ('effect_dof', ('Depth of field', 'Глубина резкости (DoF)'), True),
    ('effect_motion_blur', ('Motion blur', 'Размытие в движении'), True),
    ('effect_ssao', ('Ambient occlusion (SSAO)', 'Затенение SSAO'), True),
    ('effect_game_aa', ("The game's own anti-aliasing", 'Собственное сглаживание игры'), True),
    ('effect_dynamic_shadows', ('Shadows of dynamic lights', 'Тени от динамических источников'), True),
    ('effect_ssr', ('Screen-space reflections (not in the original)', 'Отражения SSR (не было в игре)'), False),
]
EXTRAS = [
    ('skip_intro', ('Skip the intro logos and movie', 'Пропуск заставок при запуске'), False),
    ('debug_camera', ('Free camera (hold Cross + L3; keyboard E + C)', 'Свободная камера (Cross + L3 / E + C)'), False),
    ('debug_menu', ('Game debug menu (left touchpad / Tab; needs the debug fonts)',
                    'Debug menu (левый touchpad / Tab; нужны шрифты)'), False),
]
# Cheats and gameplay tweaks: game patches for 1.09 (patches.py EFFECTS), off by default.
CHEATS = [
    ('cheat_no_death', ('Never die (health stops at 1 HP)', 'Бессмертие (здоровье не ниже 1 HP)'), False),
    ('cheat_stealth', ('Enemies do not see you (unless attacked)', 'Враги не видят вас (пока не атакованы)'), False),
    ('cheat_silent', ('Enemies do not hear you', 'Враги не слышат вас'), False),
    ('cheat_rally_no_decay', ('Rally never fades', 'Rally не угасает'), False),
    ('cheat_enemy_control', ('Control the targeted enemy (R3; L3 to go back; not with the free camera)',
                             'Управление выбранным врагом (R3; L3 — назад; не вместе со свободной камерой)'), False),
]
TWEAKS = [
    ('tweak_no_rally', ('No Rally (hits do not give health back)', 'Без Rally (удары не возвращают здоровье)'), False),
    ('tweak_camera_distance', ('Camera further from the character', 'Камера дальше от персонажа'), False),
    ('tweak_no_camera_rotation', ('No camera auto-rotation while moving', 'Без автоповорота камеры при движении'), False),
    ('tweak_easy_run', ('Run with less stick tilt (about 70%)', 'Бег при меньшем наклоне стика (около 70%)'), False),
    ('tweak_ragdoll', ('Dark Souls-style ragdoll physics (corpses fly further)',
                       'Физика тел как в Dark Souls (тела отлетают дальше)'), False),
]
INI_FLAGS = {'sharpen', 'object_motion', 'frame_generation', 'show_fps', 'mouse_camera', 'mouse_invert_y',
             'mouse_no_auto_rotation', *(k for k, _t, _o in EFFECTS + EXTRAS + CHEATS + TWEAKS)}
INI_FLOATS = {'sharpness', 'mouse_sensitivity'}  # written with two decimals
INI_DEFAULTS = {'upscaler': 'fsr4', 'preset': '1', 'sharpen': '1', 'sharpness': '0.50',
                'object_motion': '1', 'frame_generation': '0', 'show_fps': '1',
                'output_res': '1920x1080', 'model_lod': '0',
                'live_resolution': 'auto', 'button_icons': bbport_controls.ICON_DEFAULT, **bbport_controls.MOUSE_DEFAULTS,
                **{key: '1' if on else '0' for key, _t, on in EFFECTS + EXTRAS + CHEATS + TWEAKS}}
INI_FLOAT_RANGES = {'sharpness': (0.0, 2.0), 'mouse_sensitivity': bbport_controls.MOUSE_SENSITIVITY_RANGE}


def ini_float(key, value):
    """A saved float setting as a number: the default when it is not a finite number, else clamped to the range
    the runtime accepts (nan passes both comparisons of a clamp)."""
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        return float(INI_DEFAULTS[key])
    low, high = INI_FLOAT_RANGES[key]
    return min(max(number, low), high)


APP_DEFAULTS = {'ui_language': '', 'game_dir': str(PORT_DIR.parent / 'CUSA03173'), 'user_dir': '',
                'mods_dir': '', 'mods_enabled': True, 'patches_dir': '', 'language': '1',
                'player_name': '', 'fullscreen': False, 'hdr': False, 'present_mode': 'Mailbox',
                'fps_mode': 'uncap', 'frame_cap': '', 'draw_pipe': '', 'readbacks': '',
                'frames_ahead': '', 'frame_stats': False, 'gpu_profile': False,
                'vk_validation': False, 'extra_env': '', 'close_on_play': False,
                'check_updates': True, 'ui_advanced': False, 'animations': True,
                'addcont': '', 'pkg_dir': '', 'pkg_src': '', 'reshade': False,
                'beta_versions': is_prerelease(VERSION),  # a beta user keeps getting betas unless they say no
                'experimental': False, 'object_motion_amd': False, 'monitor': '',  # monitor: a name from --displays; '' is the primary
                'gamepad': '', 'gamepad_name': ''}  # gamepad: a GUID from --gamepads (BB_GAMEPAD); '' takes the first one

SIDE_BUTTONS = [(0x05, 'Mouse X1'), (0x06, 'Mouse X2')]  # virtual keys of the mouse's side buttons

UPSCALERS = [('dlss', ('DLSS (NVIDIA GeForce RTX)',)),
             ('xess', ('XeSS (Intel, any recent GPU)', 'XeSS (Intel, любая современная видеокарта)')),
             ('fsr4', ('FSR 4 (best quality)', 'FSR 4 (лучшее качество)')),
             ('fsr411', ('FSR 4.1.1 (experimental)', 'FSR 4.1.1 (экспериментально)')),
             ('fsr3', ('FSR 3.1 (every GPU)', 'FSR 3.1 (любая видеокарта)')),
             ('taa', ('TAA (native resolution anti-aliasing)', 'TAA (нативное сглаживание)')),
             ('off', ('Off', 'Выключен'))]
PRESETS = [('0', ('Native AA (×1.0)',)), ('1', ('Quality (×1.5)',)), ('2', ('Balanced (×1.7)',)),
           ('3', ('Performance (×2)',)), ('4', ('Ultra Performance (×3)',))]
OUTPUTS = [('1280x720', ('1280 × 720 (Steam Deck)',)), ('1920x1080', ('1920 × 1080',)),
           ('2560x1440', ('2560 × 1440',)), ('3840x2160', ('3840 × 2160 (4K)',))]
LIVE = [('auto', ('Auto (by graphics card)', 'Авто (по видеокарте)')), ('0', ('Off (faster)', 'Выключена (быстрее)')),
        ('1', ('On (change without restarting)', 'Включена (без перезапуска)'))]
LODS = [('0', ('As in the game', 'Как в игре')), ('-2', ('Highest (−2)', 'Максимальная (−2)')),
        ('1', ('Lower (1)', 'Ниже (1)')), ('2', ('Lowest (2)', 'Минимальная (2)'))]
FPS_MODES = [('uncap', ('Unlocked (frame-time patch)', 'Без ограничения (патч)')), ('60', ('60 FPS',)),
             ('90', ('90 FPS',)), ('30', ('30 FPS (as on PS4)', '30 FPS (как на PS4)'))]
PRESENT_MODES = [('Mailbox', ('Mailbox (low latency, no tearing)', 'Mailbox (без разрывов)')),
                 ('Fifo', ('FIFO (VSync)',)), ('FifoRelaxed', ('FIFO Relaxed',)),
                 ('Immediate', ('Immediate (tearing)', 'Immediate (с разрывами)'))]
LANGUAGES = [('1', ('English', 'Английский')), ('8', ('Russian', 'Русский')), ('0', ('Japanese', 'Японский')),
             ('2', ('French', 'Французский')), ('3', ('Spanish', 'Испанский')), ('4', ('German', 'Немецкий')),
             ('5', ('Italian', 'Итальянский'))]
DRAW_PIPE = [('', ('Auto (8+ threads)', 'Авто (8+ потоков)')), ('1', ('On', 'Включён')),
             ('0', ('Off (more stable)', 'Выключен (стабильнее)'))]
READBACKS = [('', ('Relaxed (default)', 'Relaxed (по умолчанию)')), ('0', ('Off', 'Выключены')),
             ('2', ('Precise',))]
# Frame cap of the unlocked mode (BB_FPS_LIMIT), Advanced only. '' is Auto: no cap while frame
# generation is off, half the display refresh (at most 120) while it is on (see game_environment).
# The caps above 120 carry the warning mark: the game's movement timing breaks above about 120 FPS.
FRAME_CAPS = [('', ('Auto', 'Авто')),
              ('60', ('60 FPS',)), ('90', ('90 FPS',)), ('120', ('120 FPS',)), ('144', ('144 FPS  ⚠',)),
              ('165', ('165 FPS  ⚠',)), ('240', ('240 FPS  ⚠',)), ('0', ('No limit  ⚠', 'Без ограничения  ⚠'))]


_refresh_hz = None


def display_refresh():
    """Current refresh rate (Hz) of the primary display from Win32, 0 when unknown. Read once per run."""
    global _refresh_hz
    if _refresh_hz is None:
        _refresh_hz = 0
        try:
            from ctypes import wintypes

            class DEVMODEW(ctypes.Structure):
                _fields_ = [('dmDeviceName', wintypes.WCHAR * 32), ('dmSpecVersion', wintypes.WORD),
                            ('dmDriverVersion', wintypes.WORD), ('dmSize', wintypes.WORD),
                            ('dmDriverExtra', wintypes.WORD), ('dmFields', wintypes.DWORD),
                            ('dmPosition', ctypes.c_long * 2), ('dmDisplayOrientation', wintypes.DWORD),
                            ('dmDisplayFixedOutput', wintypes.DWORD), ('dmColor', ctypes.c_short),
                            ('dmDuplex', ctypes.c_short), ('dmYResolution', ctypes.c_short),
                            ('dmTTOption', ctypes.c_short), ('dmCollate', ctypes.c_short),
                            ('dmFormName', wintypes.WCHAR * 32), ('dmLogPixels', wintypes.WORD),
                            ('dmBitsPerPel', wintypes.DWORD), ('dmPelsWidth', wintypes.DWORD),
                            ('dmPelsHeight', wintypes.DWORD), ('dmDisplayFlags', wintypes.DWORD),
                            ('dmDisplayFrequency', wintypes.DWORD), ('dmICMMethod', wintypes.DWORD),
                            ('dmICMIntent', wintypes.DWORD), ('dmMediaType', wintypes.DWORD),
                            ('dmDitherType', wintypes.DWORD), ('dmReserved1', wintypes.DWORD),
                            ('dmReserved2', wintypes.DWORD), ('dmPanningWidth', wintypes.DWORD),
                            ('dmPanningHeight', wintypes.DWORD)]
            mode = DEVMODEW()
            mode.dmSize = ctypes.sizeof(DEVMODEW)
            if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)):  # ENUM_CURRENT_SETTINGS
                hz = int(mode.dmDisplayFrequency)
                _refresh_hz = hz if hz > 1 else 0  # 0 and 1 mean "the hardware default"
        except (AttributeError, OSError, ImportError):
            pass
    return _refresh_hz


def work_area(screen=(1280, 720)):
    """(left, top, right, bottom) of the primary monitor's work area: the screen without the taskbar."""
    try:
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError, ImportError):
        pass
    return 0, 0, screen[0], screen[1]


def auto_cap(refresh):
    """The Auto frame cap while frame generation is on: half the refresh rate, 30 to 120 (60 when the
    rate is unknown). At or below half the refresh the game keeps generation on all the time."""
    return min(120, max(30, refresh // 2)) if refresh else 60


def cap_fits(cap, refresh):
    """True when twice the cap fits the display (a rate that is unknown is taken to fit)."""
    return not refresh or 2 * cap <= refresh + 2


def frame_cap_value(cap, frame_generation, refresh):
    """BB_FPS_LIMIT for a saved frame cap, '' for none. A saved 'half' (v1.6.13) is Auto."""
    cap = '' if cap == 'half' else str(cap or '')
    return cap or (str(auto_cap(refresh)) if frame_generation else '')


def simple_cap_line(cap, refresh):
    """The Simple page line under the frame generation switch (it is on): what the game and the screen show."""
    cap = '' if cap == 'half' else str(cap or '')
    if cap == '0':
        return _('With no frame cap, frame generation can pause: set Auto in Advanced.',
                 'Без ограничения FPS генерация кадров может приостанавливаться: выберите «Авто» в режиме «Дополнительно».')
    if cap and not cap_fits(int(cap), refresh):
        return _('Your frame cap of {} FPS is too high to double on this screen: set Auto in Advanced.',
                 'Ограничение в {} FPS слишком велико, чтобы удвоить кадры на этом экране: выберите «Авто» '
                 'в режиме «Дополнительно».').format(cap)
    if not cap and refresh and refresh < 100:
        return _('Your screen is {} Hz: frame generation helps little here, it is better left off.',
                 'Ваш экран работает на {} Гц: генерация кадров почти не поможет, лучше её выключить.').format(refresh)
    fps = int(cap) if cap else auto_cap(refresh)
    if refresh:
        return _('Up to {} game FPS, {} on your {} Hz screen.',
                 'До {} FPS в игре, {} на вашем экране в {} Гц.').format(fps, 2 * fps, refresh)
    return _('Up to {} game FPS, {} on screen.', 'До {} FPS в игре, {} на экране.').format(fps, 2 * fps)


def advanced_cap_hint(cap, frame_generation, refresh):
    """The line under the Advanced frame cap dropdown, in plain words for the current cap and switch."""
    cap = '' if cap == 'half' else str(cap or '')
    if not frame_generation:
        return (_('No cap.', 'Без ограничения.') if cap in ('', '0') else
                _('Caps the game at {} FPS.', 'Ограничивает игру до {} FPS.').format(cap))
    if cap == '0':
        return _("No frame limit: frame generation pauses whenever the game runs faster than half your "
                 "screen's rate. Choose Auto.",
                 'Без ограничения: генерация кадров приостанавливается, когда игра работает быстрее половины '
                 'частоты экрана. Выберите «Авто».')
    if cap and not cap_fits(int(cap), refresh):
        return _('Too high to double on your {} Hz screen, so frame generation pauses. Choose Auto.',
                 'Слишком много, чтобы удвоить на вашем экране в {} Гц, поэтому генерация кадров '
                 'приостанавливается. Выберите «Авто».').format(refresh)
    fps = int(cap) if cap else auto_cap(refresh)
    return _('With frame generation: {} game FPS, {} on screen.',
             'С генерацией кадров: {} FPS в игре, {} на экране.').format(fps, 2 * fps)


FRAMES_AHEAD = [('', ('1 (default)', '1 (по умолчанию)')), ('2', ('2',)), ('0', ('Unbounded', 'Без ограничения'))]
UI_LANGUAGES = bbport_lang.LANGUAGE_NAMES

FSR4_COMMIT = 'ae8d628fae208813172446d1e49ed94150b04658'
FSR4_BASE = f'https://raw.githubusercontent.com/FireBurn/Q2RTX/{FSR4_COMMIT}/baseq2/fsr4_shaders'


def fsr4_files():
    """The FSR 4 v07 asset set of tools/fetch_fsr4_assets.sh (1080 and 2160 tiers)."""
    files = ['LICENSE-FSR4-v07.txt', 'rcas.spv', 'spd_auto_exposure.spv']
    for model in ('native', 'quality', 'balanced', 'performance', 'ultraperf', 'drs'):
        files += [f'fsr4_model_v07_i8_{model}_initializers.bin', f'fsr4_model_v07_i8_{model}_pre_weights.bin',
                  f'fsr4_model_v07_i8_{model}_shader_manifest.json']
        for tier in ('1080', '2160'):
            files += [f'fsr4_model_v07_i8_{model}_{tier}_pre.spv', f'fsr4_model_v07_i8_{model}_{tier}_post.spv']
            files += [f'fsr4_model_v07_i8_{model}_{tier}_pass{n}.spv' for n in range(1, 13)]
    return files


def fsr4_missing():
    folder = PORT_DIR / 'fsr4_shaders'
    return [name for name in fsr4_files() if not (folder / name).is_file() or not (folder / name).stat().st_size]


# FSR 4.1.1 on Windows (experimental): the game loads fsr4vk's DLL at run time (tools/fetch_fsr4vk.py
# downloads it). It is not in the package; it lives in a folder of the install that updates keep.
FSR4VK_FILES_REASON = 'FSR 4.1.1 files are not downloaded'  # also what bb-gpu-capabilities --upscalers says
FSR4VK_OFF_REASON = 'switched off (BB_FSR4VK=0)'  # likewise: the tool obeys BB_FSR4VK, which says nothing of the PC
FSR4VK_EXPERIMENTAL_REASON = 'turn on Experimental features in Advanced'


def fsr4vk_dir():
    return PORT_DIR / 'fsr4vk'


def fsr4vk_shown(*more):
    """fsr4vk_dir() (and what is below it) as the FSR 4.1.1 card shows it: 'fsr4vk\\LICENSES', relative to
    the install; short_path() when the folder is elsewhere."""
    path = fsr4vk_dir().joinpath(*more)
    try:
        return str(path.relative_to(PORT_DIR))
    except ValueError:
        return short_path(path)


def fsr4vk_present():
    """True when the provider DLL is in fsr4vk_dir(); the game and bb-gpu-capabilities check no more
    than that (the download itself is verified by SHA-256)."""
    return (fsr4vk_dir() / 'amd_fidelityfx_upscaler_vk.dll').is_file()


FSR4VK_VERDICT = None  # (stamp of the pinned files, files_ok()): a 17.6 MB hash is not repeated for the same files


def fsr4vk_stamp():
    """Name, size and mtime of every pinned file; None when one is missing."""
    stamp = []
    for _member, name, _size, _sha in fetch_fsr4vk.FILES:
        try:
            info = (fsr4vk_dir() / name).stat()
        except OSError:
            return None
        stamp.append((str(fsr4vk_dir() / name), info.st_size, info.st_mtime_ns))
    return tuple(stamp)


def fsr4vk_outdated(wait=True):
    """True when the provider DLL is in fsr4vk_dir() but the folder is not the pinned build (the original
    upstream DLL, which fails on AMD's driver; the game keeps using it). Hashes the files once per change;
    with wait=False it returns None instead of hashing a stamp it has not seen. Hashing takes tens of ms."""
    global FSR4VK_VERDICT
    if fetch_fsr4vk is None or not fsr4vk_present():
        return False
    stamp = fsr4vk_stamp()
    if stamp is None:
        return True
    if not FSR4VK_VERDICT or FSR4VK_VERDICT[0] != stamp:
        if not wait:
            return None
        FSR4VK_VERDICT = (stamp, fetch_fsr4vk.files_ok(fsr4vk_dir()))
    return not FSR4VK_VERDICT[1]


def fsr4vk_download_problem():
    """Why the download cannot run in this build ('' when it can): tools/fetch_fsr4vk.py did not import."""
    return f'the download helper did not load ({FETCH_FSR4VK_ERROR})' if fetch_fsr4vk is None else ''


def download_fsr4vk(progress=None):
    """Downloads the pinned fsr4vk release into fsr4vk_dir(); progress(done, total) is optional. Returns
    '' or the reason it failed. Run it off the window's thread."""
    if fsr4vk_download_problem():
        return fsr4vk_download_problem()
    try:
        fetch_fsr4vk.fetch(fsr4vk_dir(), progress)
    except (fetch_fsr4vk.FetchError, OSError) as error:
        return str(error)
    return ''


UPSCALER_LINE = re.compile(r'^UPSCALER (\w+) (supported|unsupported)(?:: (.*))?$')


def parse_upscaler_support(output):
    """{name: (supported, note)} from the UPSCALER lines of `bb-gpu-capabilities --upscalers`; the note
    is the reason when unsupported, a warning (FSR 4 on an RDNA2 GPU) when supported."""
    support = {}
    for line in output.splitlines():
        found = UPSCALER_LINE.match(line.strip())
        if found:
            support[found[1]] = (found[2] == 'supported', found[3] or '')
    return support


def gpu_tool():
    """(bb-gpu-capabilities.exe, the environment to run it with): the packaged copy, else out/."""
    exe = PORT_DIR / 'bin' / 'bb-gpu-capabilities.exe'
    if not exe.is_file():
        exe = PORT_DIR / 'out' / 'bb-gpu-capabilities.exe'
    env = dict(os.environ)
    env['BB_FSR4VK_DIR'] = str(fsr4vk_dir())  # where the check looks for the experimental FSR 4.1.1 files
    env.pop('BB_FSR4VK', None)  # the check asks what the PC can run; the launcher's own switch decides the rest
    if not (exe.parent / 'SDL3.dll').is_file():
        clang64 = Path(os.environ.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin'
        env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
    return exe, env


def parse_displays(output):
    """The monitors of `bb-gpu-capabilities --displays`: one tab-separated line each (number from 1,
    name, WxH, refresh Hz or 0, primary 1/0). Lines that do not fit are skipped."""
    monitors = []
    for line in output.splitlines():
        fields = line.rstrip('\r\n').split('\t')
        if len(fields) == 5 and fields[0].isdigit() and fields[3].isdigit() and fields[1].strip():
            monitors.append({'number': int(fields[0]), 'name': fields[1].strip(), 'size': fields[2],
                             'hz': int(fields[3]), 'primary': fields[4] == '1'})
    return monitors


def display_label(monitor):
    """'2: DELL U2417H, 1920x1080, 60 Hz' (no Hz part when the refresh rate is unknown)."""
    parts = [f"{monitor['number']}: {monitor['name']}", monitor['size']]
    if monitor['hz']:
        parts.append(f"{monitor['hz']} Hz")
    return ', '.join(parts)


_displays = None


def connected_displays():
    """The monitors the game would see, asked of bb-gpu-capabilities once per run ([] when it fails).
    Run it off the window's thread."""
    global _displays
    if _displays is None:
        _displays = []
        exe, env = gpu_tool()
        try:
            result = subprocess.run([str(exe), '--displays'], capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=30, env=env, creationflags=NO_WINDOW)
            if result.returncode == 0:
                _displays = parse_displays(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _displays


def parse_gamepads(output):
    """[(GUID, name)] from `bb-gpu-capabilities --gamepads`: one "GUID<TAB>name" line each; others are skipped."""
    pads = []
    for line in output.splitlines():
        guid, tab, name = line.rstrip('\r\n').partition('\t')
        if tab and guid.strip() and name.strip():
            pads.append((guid.strip(), name.strip()))
    return pads


def list_gamepads():
    """The gamepads the game would see (SDL asks, as the game does), or None when the tool cannot run.
    Run it off the window's thread."""
    exe, env = gpu_tool()
    if not exe.is_file():
        return None
    try:
        result = subprocess.run([str(exe), '--gamepads'], capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=30, env=env, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_gamepads(result.stdout) if result.returncode == 0 else None


def gamepad_options(pads, saved, saved_name):
    """[(value, text)] of the controller dropdown: Automatic first, the connected gamepads, and the saved
    choice while it is not connected (it is used as soon as it connects)."""
    options = [('', _('Automatic (first connected)', 'Авто (первый подключённый)'))] + list(pads)
    if saved and saved not in [value for value, _text in options]:
        options.append((saved, _('{} (not connected)', '{} (не подключён)').format(saved_name or saved)))
    return options


def fsr411_state(support, experimental, files_present):
    """('ok' | 'no', reason) of the experimental FSR 4.1.1: a GPU that cannot run it keeps its own reason,
    then it needs Experimental features, then the downloaded files (also when the check ran before them).
    A verdict that only says the files are missing, or that BB_FSR4VK=0 switched it off, describes the
    moment and environment of that check and not the GPU, so it is not held against it."""
    verdict = (support or {}).get('fsr411')
    if verdict and not verdict[0] and verdict[1] not in (FSR4VK_FILES_REASON, FSR4VK_OFF_REASON):
        return 'no', verdict[1]
    if not experimental:
        return 'no', FSR4VK_EXPERIMENTAL_REASON
    if not files_present:
        return 'no', FSR4VK_FILES_REASON
    return 'ok', ''


def upscaler_state(name, support, assets_missing, experimental=False, fsr4vk_files=False):
    """('ok' | 'slow' | 'no', reason) of one upscaler. support is None until the GPU check has run (and
    when it could not): then only the missing FSR 4 assets are known."""
    if name == 'fsr411':
        return fsr411_state(support, experimental, fsr4vk_files)
    verdict = (support or {}).get(name)
    if verdict and not verdict[0]:
        return 'no', verdict[1]
    if name == 'fsr4' and assets_missing:
        return 'no', 'download the FSR 4 assets in Graphics'
    if verdict and verdict[1]:
        return 'slow', verdict[1]
    return 'ok', ''


UPSCALER_REASONS_RU = {
    FSR4VK_FILES_REASON: 'файлы FSR 4.1.1 не скачаны',
    FSR4VK_EXPERIMENTAL_REASON: 'включите «Экспериментальные функции» в «Дополнительно»',
    'download the FSR 4 assets in Graphics': 'скачайте ассеты FSR 4 на вкладке «Графика»',
    'may be slow on this GPU': 'на этой видеокарте может быть медленно',
    'the GPU or driver lacks the INT8 features FSR 4 needs': 'видеокарте или драйверу не хватает INT8, нужного FSR 4',
    'needs a Vulkan extension that no Windows driver is known to support yet':
        'нужно расширение Vulkan, которое пока не поддерживает ни один известный драйвер Windows',
    'not an NVIDIA RTX GPU': 'не видеокарта NVIDIA RTX',
    'bbport_dlss.dll and nvngx_dlss.dll are not installed': 'bbport_dlss.dll и nvngx_dlss.dll не установлены',
    'this GPU or driver does not support DLSS (GeForce RTX needed)':
        'эта видеокарта или драйвер не поддерживает DLSS (нужна GeForce RTX)',
    'libxess.dll is not installed': 'libxess.dll не установлена',
    'this GPU or driver does not support XeSS (DP4a needed)':
        'эта видеокарта или драйвер не поддерживает XeSS (нужен DP4a)',
}


def reason_text(reason):
    """A reason of the GPU check in the launcher's language (unknown ones stay in English)."""
    return _(reason, UPSCALER_REASONS_RU.get(reason))


def object_motion_hint(experimental):
    """The note under 'Object motion vectors'. On AMD graphics cards the game keeps it off, unless
    Experimental features (Advanced) is on and the player turns on the AMD switch below it."""
    note = (_('(experimental) On AMD graphics cards it cost frame rate and drew some objects wrong with frame '
              'generation, so it stays off there unless you turn on the switch below.',
              '(экспериментально) На видеокартах AMD это снижало FPS и неправильно рисовало некоторые объекты с '
              'генерацией кадров, поэтому там оно выключено, пока вы не включите переключатель ниже.') if experimental else
            _('Off on AMD graphics cards: there it cost frame rate and drew some objects wrong with frame generation.',
              'На видеокартах AMD выключено: там снижало FPS и неправильно рисовало некоторые объекты с генерацией кадров.'))
    return _('Less ghosting on characters, cloth and weapons; costs about 10% FPS.',
             'Меньше гостинга на персонажах и одежде; стоит около 10% FPS.') + ' ' + note


def best_upscaler(support):
    """The upscaler a PC falls back to when the saved one cannot run: DLSS on an RTX GPU, else FSR 3.1."""
    return 'dlss' if (support or {}).get('dlss', (False, ''))[0] else 'fsr3'


def upscaler_fallback(current, support, experimental, fsr4vk_files):
    """(better, reason) when the saved upscaler cannot run on this PC, else None. Only what the GPU
    check or the FSR 4.1.1 rules rule out counts: a missing FSR 4 download is left to the game."""
    if current == 'fsr411':
        state, reason = fsr411_state(support, experimental, fsr4vk_files)
    else:
        verdict = (support or {}).get(current)
        state, reason = ('no', verdict[1]) if verdict and not verdict[0] else ('ok', '')
    return (best_upscaler(support), reason) if state == 'no' else None


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def ini_path():
    return Path(os.environ.get('BB_CONFIG', DATA_DIR / 'bbport.ini'))


def load_ini():
    values, lines = dict(INI_DEFAULTS), []
    try:
        lines = ini_path().read_text(encoding='utf-8').splitlines()
    except OSError:
        pass
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values, lines


def save_ini(values, lines):
    """Rewrites the edited keys in place, appends missing ones, keeps comments and other keys. A key
    whose value is None is removed (a control back to its default)."""
    written, out = set(), []
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key = line.split('=', 1)[0].strip()
            if key in values:
                if values[key] is not None:
                    out.append(f'{key}={values[key]}')
                written.add(key)
                continue
        out.append(line)
    if not lines:
        out.append('# bbport settings (in-game menu: Insert / L3+R3)')
    out += [f'{key}={value}' for key, value in values.items() if key not in written and value is not None]
    ini_path().write_text('\n'.join(out) + '\n', encoding='utf-8')


def ini_values(ini):
    """What collect() saves: the launcher's own bbport.ini keys and the controls (key.* / pad.*)."""
    return {**{key: ini[key] for key in INI_DEFAULTS}, **bbport_controls.ini_updates(ini)}


def game_info(folder):
    """(title, app version) of a game folder, or None without eboot.bin."""
    folder = Path(folder or '.')
    if not (folder / 'eboot.bin').is_file():
        return None
    try:
        from prepare import sfo
        values = sfo((folder / 'sce_sys/param.sfo').read_bytes())
        return values.get('TITLE', 'Bloodborne').replace('™', '').strip(), values.get('APP_VER', '?')
    except (OSError, ValueError, ImportError):
        return 'Bloodborne', '?'


def reshade_ready():
    """True when this build ships ReShade (a source tree has no bin/reshade)."""
    return all((RESHADE_DIR / name).is_file() for name in ('ReShade64.dll', RESHADE_LAYER + '.json', 'ReShade.ini'))


def reshade_presets():
    """Names (file stems) of the presets in bin/reshade/presets."""
    return sorted((path.stem for path in (RESHADE_DIR / 'presets').glob('*.ini')), key=str.casefold)


def reshade_preset_label(name):
    """The dropdown text of a preset: "Bloodborne - Natural" shows as Natural (translated)."""
    text = name.removeprefix(RESHADE_PRESET_PREFIX)
    return _(text, {'Natural': 'Естественный', 'Vivid': 'Яркий'}.get(text))


def get_reshade_preset():
    """The preset ReShade starts with: PresetPath of bin/reshade/ReShade.ini, without folder and extension."""
    try:
        lines = (RESHADE_DIR / 'ReShade.ini').read_text(encoding='utf-8').splitlines()
    except OSError:
        return ''
    for line in lines:
        key, _sep, value = line.partition('=')
        if key.strip() == 'PresetPath':
            return Path(value.strip().replace('\\', '/')).stem
    return ''


def set_reshade_preset(name):
    """Writes PresetPath in the [GENERAL] section of bin/reshade/ReShade.ini, keeping the rest of the file."""
    path = RESHADE_DIR / 'ReShade.ini'
    line = f'PresetPath=.\\presets\\{name}.ini'
    lines, section, done = path.read_text(encoding='utf-8').splitlines(), None, False
    for i, text in enumerate(lines):
        if text.strip().startswith('['):
            section = text.strip().lower()
        elif section == '[general]' and text.partition('=')[0].strip() == 'PresetPath':
            lines[i], done = line, True
    if not done:
        general = next((i for i, text in enumerate(lines) if text.strip().lower() == '[general]'), None)
        if general is None:
            lines[:0] = ['[GENERAL]', line, '']
        else:
            lines.insert(general + 1, line)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def icons_environment(s, ini):
    """The environment entries of the button icons for one Play: BB_ICONS_DIR (a folder run.py puts under the mods,
    holding a common.tpf.dcx generated from the player's own copy, see bbport_icons.py) and BB_ICONS_NOTE (a line for
    the log). The icon set is resolved now, from the controller connected: it stays until the next start. {} when the
    game keeps its own icons (PlayStation, or no game file to redraw)."""
    choice = ini.get('button_icons', INI_DEFAULTS['button_icons'])
    if choice not in [value for value, _text in bbport_controls.ICON_CHOICES]:
        choice = bbport_controls.ICON_DEFAULT
    game = Path(s['game_dir'])
    if choice == 'playstation' or not (game / 'dvdroot_ps4' / 'menu' / 'common.tpf.dcx').is_file():
        return {}
    pads = list_gamepads() if choice == 'auto' else []
    icon_set = bbport_controls.resolve_icon_set(choice, pads, str(s.get('gamepad', '')).strip())
    if icon_set == 'playstation':
        return {}
    try:
        import bbport_icons
        import mods
    except ImportError as error:  # a source tree without Pillow
        return {'BB_ICONS_NOTE': f'Button icons are off for this run: {error}'}
    try:
        note = mods.icons_note(mods.user_layers(game, s['mods_dir'] or DATA_DIR / 'mods', DATA_DIR / 'mods.json',
                                                '1' if s['mods_enabled'] else '0'))
        if note:
            return {'BB_ICONS_NOTE': note}
        folder, built = bbport_icons.ensure_layer(game, DATA_DIR / 'out', icon_set, ini)
    except (bbport_icons.IconError, OSError, ValueError) as error:
        return {'BB_ICONS_NOTE': f'Button icons are off for this run: {error}'}
    return {'BB_ICONS_DIR': str(folder), 'BB_ICONS_NOTE': f'Button icons: {icon_set} ({"made" if built else "from the cache"})'}


def game_environment(s, frame_generation=None):
    """The environment of the game. Frame generation is read from bbport.ini unless the caller knows it."""
    ini = load_ini()[0]
    if frame_generation is None:
        frame_generation = ini.get('frame_generation') == '1'
    # Auto caps the game only while frame generation can run: the game turns it off with the
    # upscaler off or with HDR output, and the cap would then only cost frames.
    frame_generation = (frame_generation and not s['hdr']
                        and ini.get('upscaler', INI_DEFAULTS['upscaler']) != 'off')
    env = dict(os.environ)
    env['BB_GAME_DIR'] = s['game_dir']
    if s['user_dir']:
        env['BB_USER_DIR'] = s['user_dir']
    env['BB_MODS_DIR'] = s['mods_dir'] or str(DATA_DIR / 'mods')
    env['BB_MODS_CONFIG'] = str(DATA_DIR / 'mods.json')
    env['BB_MODS_ENABLED'] = '1' if s['mods_enabled'] else '0'
    env.pop('BB_ICONS_DIR', None)  # only this launch's choice counts, not one inherited from an earlier run
    env.pop('BB_ICONS_NOTE', None)
    env.update(icons_environment(s, ini))
    env['BB_PATCHES_DIR'] = s['patches_dir'] or str(DATA_DIR / 'patches')
    env['BB_PATCHES_CONFIG'] = str(DATA_DIR / 'patches.json')
    env['BB_LANGUAGE'] = s['language']
    if str(s['player_name']).strip():
        env['BB_USER_NAME'] = str(s['player_name']).strip()
    if str(s.get('addcont', '')).strip():
        env['BB_ADDCONT'] = str(s['addcont']).strip()
    env['BB_FULLSCREEN'] = '1' if s['fullscreen'] else '0'
    if str(s.get('monitor', '')).strip():  # the game opens on the primary monitor when this one is gone
        env['BB_DISPLAY'] = str(s['monitor']).strip()
    env['BB_PRESENT_MODE'] = s['present_mode']
    if str(s.get('gamepad', '')).strip():  # runtime_pad.c takes this one of the connected gamepads (GUID or part of the name)
        env['BB_GAMEPAD'] = str(s['gamepad']).strip()
    if s['hdr']:
        env['BB_HDR'] = '1'
    env['BB_FPS'] = s['fps_mode']
    cap = frame_cap_value(s.get('frame_cap', ''), frame_generation, display_refresh())
    if cap:
        env['BB_FPS_LIMIT'] = cap
    for key, name in (('draw_pipe', 'BB_DRAW_PIPE'), ('readbacks', 'BB_READBACKS'), ('frames_ahead', 'BB_FRAMES_AHEAD')):
        if s[key]:
            env[name] = s[key]
    for key, name in (('frame_stats', 'BB_FRAME_STATS'), ('gpu_profile', 'BB_GPU_PROFILE'),
                      ('vk_validation', 'BB_VK_VALIDATION')):
        if s[key]:
            env[name] = '1'
    # The game forces object motion vectors off on AMD under Windows (they cost FPS and broke objects with
    # frame generation) unless it is told otherwise; it reads this only on AMD, so the variable does nothing
    # on other GPUs. The player asks for it with its own switch, which needs Experimental features: the
    # Experimental switch alone, or a saved object_motion=1, never turns it on.
    if s.get('experimental') and s.get('object_motion_amd') \
            and ini.get('object_motion', INI_DEFAULTS['object_motion']) == '1':
        env['BB_OBJECT_MOTION_AMD'] = '1'
    # FSR 4.1.1 (fsr4vk) is experimental: its DLL sits in the install's fsr4vk folder and is loaded only
    # while the switch is on.
    env['BB_FSR4VK_DIR'] = str(fsr4vk_dir())
    if not s.get('experimental'):
        env['BB_FSR4VK'] = '0'
    else:
        env.pop('BB_FSR4VK', None)  # the switch decides: an inherited 0 would leave the dropdown on and the game off
    for item in str(s['extra_env']).split():
        if '=' in item:
            key, value = item.split('=', 1)
            env[key] = value
    if s.get('reshade') and reshade_ready():
        # An explicit layer for this run only; its files, settings and log stay in bin/reshade.
        for name, value in (('VK_ADD_LAYER_PATH', str(RESHADE_DIR)), ('VK_INSTANCE_LAYERS', RESHADE_LAYER)):
            env[name] = os.pathsep.join(filter(None, (env.get(name), value)))
        env['RESHADE_BASE_PATH_OVERRIDE'] = str(RESHADE_DIR)
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


# ---------------------------------------------------------------------------------------------
# PlayStation 4 .pkg files. A package is classified from its header and its param.sfo (the way
# the bloodborne-kit's prepare.ps1 does) and extracted with PkgTool (maxton/LibOrbisPkg, LGPL-3.0).

BB_TITLE_IDS = ('CUSA03173', 'CUSA00900', 'CUSA00207', 'CUSA01363', 'CUSA03023')  # every Bloodborne release
PKG_MAGIC = b'\x7fCNT'
PKG_ENTRY_PARAM_SFO = 0x1000  # a plain entry of the package, outside the extracted files
PKG_SPACE_FACTOR = 1.1


class PkgError(Exception):
    """A problem with the chosen packages or with PkgTool; the message is meant for the player."""


def kind_name(category):
    return {'gd': _('Game package', 'Пакет игры'), 'gp': _('Update package', 'Пакет обновления'),
            'ac': _('DLC package', 'Пакет DLC')}[category]


def size_text(size):
    return f'{size / 2 ** 30:.1f} GB' if size >= 2 ** 30 else f'{size / 2 ** 20:.0f} MB'


def pkg_param_sfo(stream, head):
    """The bytes of the package's param.sfo, or None."""
    count, table = struct.unpack('>I', head[0x10:0x14])[0], struct.unpack('>I', head[0x18:0x1c])[0]
    for index in range(min(count, 1024)):
        stream.seek(table + 32 * index)
        entry_id, _name, _flags1, _flags2, offset, size = struct.unpack('>6I', stream.read(24))
        if entry_id == PKG_ENTRY_PARAM_SFO and 0 < size <= 1 << 20:
            stream.seek(offset)
            return stream.read(size)
    return None


def read_pkg(path):
    """Header and param.sfo of one .pkg as a dict: category 'gd' (game), 'gp' (update) or 'ac' (add-on),
    title_id, app_ver, content_id, label (the add-on's entitlement label), has_data (a file system
    image to extract). Raises PkgError."""
    path = Path(path)
    try:
        with open(path, 'rb') as stream:
            head = stream.read(0x420)
            if len(head) < 0x420 or head[:4] != PKG_MAGIC:
                raise PkgError(_('{} is not a PlayStation 4 .pkg file.', '{} не является .pkg файлом PlayStation 4.')
                               .format(path.name))
            raw = pkg_param_sfo(stream, head)
        size = path.stat().st_size
    except (OSError, struct.error) as failure:
        raise PkgError(_('Could not read {}: {}', 'Не удалось прочитать {}: {}').format(path.name, failure))
    content_id = head[0x40:0x64].split(b'\0')[0].decode('ascii', 'replace')
    drm, content_type, flags = struct.unpack('>3I', head[0x70:0x7c])
    try:
        from prepare import sfo
        values = sfo(raw) if raw else {}
    except (ValueError, IndexError, struct.error, ImportError):
        values = {}
    category = values.get('CATEGORY')
    if category not in ('gd', 'gp', 'ac') and content_type in (0x1b, 0x1c):  # add-on types
        category = 'ac'
    if category not in ('gd', 'gp', 'ac'):
        raise PkgError(_('{} is not a game, update or DLC package.', '{} не является пакетом игры, обновления или DLC.')
                       .format(path.name))
    return {'path': path, 'size': size, 'content_id': content_id, 'category': category,
            'title_id': values.get('TITLE_ID') or content_id[7:16], 'title': values.get('TITLE', '').replace('™', '').strip(),
            'app_ver': values.get('APP_VER') or values.get('VERSION', ''), 'sfo': raw, 'drm': drm,
            'content_type': content_type, 'flags': flags, 'label': content_id.split('-')[-1],
            'has_data': struct.unpack('>I', head[0x404:0x408])[0] > 0 and struct.unpack('>Q', head[0x418:0x420])[0] > 0}


def check_bloodborne(info):
    if info['title_id'] not in BB_TITLE_IDS:
        raise PkgError(_('{} is not Bloodborne (it is {}). Only Bloodborne packages can be installed.',
                         '{} — не Bloodborne (это {}). Можно установить только пакеты Bloodborne.').format(
            info['path'].name, f"{info['title']} ({info['title_id']})" if info['title'] else info['title_id']))


def classify_pkgs(paths):
    """check_pkgs of the Bloodborne packages in these files; raises PkgError for any file that is not one."""
    infos = [read_pkg(path) for path in paths]
    for info in infos:
        check_bloodborne(info)
    return check_pkgs(infos)


def pkg_description(info):
    return {'gd': _('Game v{}', 'Игра v{}'), 'gp': _('Update v{}', 'Обновление v{}'),
            'ac': _('DLC {}', 'DLC {}')}[info['category']].format(info['label'] if info['category'] == 'ac' else info['app_ver'])


def scan_pkg_files(folder, depth=3, limit=200):
    """The .pkg files in a folder and its subfolders down to this depth."""
    folder, found = Path(folder), []
    for here, dirs, files in os.walk(folder):
        dirs.sort()
        found += [Path(here) / name for name in sorted(files) if name.lower().endswith('.pkg')]
        if len(Path(here).relative_to(folder).parts) >= depth or len(found) >= limit:
            dirs.clear()
    return found[:limit]


def read_candidates(paths):
    """One row per file for the install window: {'path', 'info' (a package dict) or None, 'error'}, usable
    Bloodborne packages first (game, update, DLC)."""
    rows = []
    for path in paths:
        try:
            info = read_pkg(path)
            check_bloodborne(info)
            rows.append({'path': Path(path), 'info': info, 'error': None})
        except PkgError as problem:
            rows.append({'path': Path(path), 'info': None, 'error': str(problem)})
    order = {'gd': 0, 'gp': 1, 'ac': 2}
    return sorted(rows, key=lambda row: order[row['info']['category']] if row['info'] else 3)


def preselect(rows):
    """The paths ticked at first: the biggest game package, the highest update of its region, each DLC once."""
    infos = [row['info'] for row in rows if row['info']]
    base = max((i for i in infos if i['category'] == 'gd'), key=lambda i: i['size'], default=None)
    update = max((i for i in infos if i['category'] == 'gp' and (not base or i['title_id'] == base['title_id'])),
                 key=lambda i: version_tuple(i['app_ver']), default=None)
    chosen, labels = [info for info in (base, update) if info], set()
    for info in infos:
        if info['category'] == 'ac' and info['label'] not in labels:
            labels.add(info['label'])
            chosen.append(info)
    return {info['path'] for info in chosen}


def free_space(path):
    """Free bytes on the drive of a folder that may not exist yet, or None."""
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def check_pkgs(infos):
    """([package dicts: game, update, DLCs], [warnings]). Raises PkgError for a set that does not belong
    together (two games, two updates, different regions)."""
    chosen = {}
    for info in infos:
        if info['category'] == 'ac':
            continue
        if info['category'] in chosen:
            raise PkgError(_('Choose only one {}: {} and {} are the same kind.', 'Выберите только один пакет ({}): {} и {} '
                             'одного вида.').format(kind_name(info['category']), chosen[info['category']]['path'].name,
                                                   info['path'].name))
        chosen[info['category']] = info
    base, update = chosen.get('gd'), chosen.get('gp')
    if base and update and base['title_id'] != update['title_id']:
        raise PkgError(_('The game and the update are from different regions ({} and {}).',
                         'Игра и обновление из разных регионов ({} и {}).').format(base['title_id'], update['title_id']))
    warnings = []
    if update and update['app_ver'] != PATCH_VERSION:
        warnings.append(_('Update version {}: the community patches (60+ FPS, effects) need 01.09, so the game would '
                          'run at 30 FPS.', 'Версия обновления {}: патчам сообщества (60+ FPS, эффекты) нужна 01.09, '
                          'поэтому игра будет работать в 30 FPS.').format(update['app_ver'] or '?'))
    if update and not base:
        warnings.append(_('Without the game package, the update alone is not playable.',
                          'Без пакета игры одно обновление играть не позволяет.'))
    order = {'gd': 0, 'gp': 1, 'ac': 2}
    return sorted(infos, key=lambda info: order[info['category']]), warnings


def pkgtool_path():
    """PkgTool.exe: BB_PKGTOOL, the package's bin/pkgtool, or out/pkgtool of a source tree."""
    candidates = [PORT_DIR / 'bin' / 'pkgtool' / 'PkgTool.exe', PORT_DIR / 'out' / 'pkgtool' / 'PkgTool.exe']
    if os.environ.get('BB_PKGTOOL'):
        candidates.insert(0, Path(os.environ['BB_PKGTOOL']))
    return next((path for path in candidates if path.is_file()), None)


def find_game_folder(dest):
    """The folder with eboot.bin inside an extraction target (uroot for the game, Image0 for some dumps)."""
    dest = Path(dest)
    for guess in (dest / 'uroot', dest / 'Image0', dest):
        if (guess / 'eboot.bin').is_file():
            return guess
    for folder, dirs, files in os.walk(dest):
        if 'eboot.bin' in files:
            return Path(folder)
        if len(Path(folder).relative_to(dest).parts) >= 3:
            dirs.clear()
        dirs.sort()
    return None


def write_param_sfo(game, info):
    """The package's param.sfo into <game>/sce_sys when that file is missing or older (PkgTool does not extract
    it); returns True when written."""
    target = Path(game) / 'sce_sys' / 'param.sfo'
    try:
        from prepare import sfo
        current = sfo(target.read_bytes()).get('APP_VER')
    except (OSError, ValueError, IndexError, struct.error, ImportError):
        current = None
    if not info['sfo'] or (current and version_tuple(current) >= version_tuple(info['app_ver'])):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(info['sfo'])
    return True


def error_lines(lines):
    """The line that names the exception or error and the last lines PkgTool printed, each cut at 160 characters."""
    lead = next((line for line in lines if re.search(r'exce|error', line, re.I)), None)
    keep = lines[-3:]
    return '\n'.join(line.strip()[:160] for line in ([lead] if lead and lead not in keep else []) + keep)


def console_encoding():
    try:
        return f'cp{ctypes.windll.kernel32.GetOEMCP()}'
    except (AttributeError, OSError):
        return 'utf-8'


# ---------------------------------------------------------------------------------------------
# The window. Near-black stage, warm parchment text, one accent (blood red). Everything that
# moves goes through Motion; BB_LAUNCHER_ANIMATIONS=0 (or the Animations switch) turns it off.

BG, PANEL, CARD, FIELD = '#0b0a0c', '#0f0d0f', '#171315', '#0d0b0c'
LINE, LINE_HI = '#2a2224', '#4a3a36'
TEXT, MUTED, DIM, GOLD = '#e8dfcf', '#a09282', '#6b6054', '#c8a96a'
BLOOD, BLOOD_HI, BLOOD_DEEP = '#8e1a1f', '#b8282e', '#5c1014'
OK, CAUTION, BAD = '#8fae7e', '#d9a441', '#d9695c'
GLYPH_COLORS = {'✓': OK, '⚠': CAUTION, '✗': BAD, '•': DIM}


def short_path(path, limit=44):
    """A path for display: %LOCALAPPDATA% & co. for the usual roots, the middle cut out when still long."""
    text = str(path)
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
        base = os.environ.get(name)
        if base and text.lower().startswith(base.lower()):
            text = f'%{name}%' + text[len(base):]
            break
    parts = text.split('\\')
    if len(text) > limit and len(parts) > 3:
        text = '\\'.join([parts[0], '…', *parts[-2:]])
    return text


def mix(a, b, t):
    """Colour a moved a fraction t towards colour b ('#rrggbb')."""
    t = max(0.0, min(1.0, t))
    return '#%02x%02x%02x' % tuple(round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)
                                   for i in (1, 3, 5))


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return t * t * (3 - 2 * t)


def linear(t):
    return t


class Motion:
    """after()-driven tweens and loops on one ticker: time based (60 fps target, any speed of
    machine plays the same 150-400 ms), cancelable by key, never blocking. Disabled, a tween
    jumps to its end value and a loop never starts."""

    def __init__(self, root, tcl_error, enabled):
        self.root, self.tcl_error, self.enabled = root, tcl_error, enabled
        self.tweens, self.loops, self.job = {}, {}, None
        self.fine_timer = False
        if enabled:  # 1 ms timer resolution for this process: Tk's after() is 15.6 ms otherwise
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)
                self.fine_timer = True
            except (AttributeError, OSError):
                pass

    def tween(self, key, ms, start, end, step, done=None, ease=ease_out):
        self.tweens.pop(key, None)
        if not self.enabled or ms <= 0:
            step(end)
            if done:
                done()
            return
        step(start)
        self.tweens[key] = (time.perf_counter(), ms / 1000, start, end, step, done, ease)
        self.kick()

    def cancel(self, key):
        self.tweens.pop(key, None)

    def loop(self, key, ms, fn):
        if self.enabled:
            self.loops[key] = [ms / 1000, 0.0, fn]
            self.kick()

    def stop(self, key):
        self.loops.pop(key, None)

    def set_enabled(self, enabled):
        if not enabled:
            for key, (_t0, _d, _a, end, step, done, _e) in list(self.tweens.items()):
                self.tweens.pop(key, None)
                try:
                    step(end)
                    if done:
                        done()
                except self.tcl_error:
                    pass
            self.loops.clear()
        self.enabled = enabled

    def kick(self):
        if self.job is None:
            self.job = self.root.after(1, self.tick)

    def tick(self):
        self.job = None
        began = time.perf_counter()
        for key, entry in list(self.tweens.items()):
            if self.tweens.get(key) is not entry:
                continue
            t0, duration, start, end, step, done, ease = entry
            progress = min(1.0, (began - t0) / duration)
            try:
                step(start + (end - start) * ease(progress))
                if progress >= 1.0:
                    if self.tweens.get(key) is entry:
                        del self.tweens[key]
                    if done:
                        done()
            except self.tcl_error:  # the widget was destroyed under the tween
                self.tweens.pop(key, None)
        if self.visible():
            for key, entry in list(self.loops.items()):
                if began - entry[1] >= entry[0]:
                    entry[1] = began
                    try:
                        entry[2](began)
                    except self.tcl_error:
                        self.loops.pop(key, None)
        if self.tweens:
            delay = max(1, 16 - int((time.perf_counter() - began) * 1000))
        elif self.loops:
            delay = 24 if self.visible() else 300  # paused while minimised or hidden
        else:
            return
        self.job = self.root.after(delay, self.tick)

    def visible(self):
        try:
            return bool(self.root.winfo_viewable()) and self.root.state() != 'iconic'
        except self.tcl_error:
            return False

    def close(self):
        if self.fine_timer:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except (AttributeError, OSError):
                pass
            self.fine_timer = False


class Widget:
    """A hand-drawn Canvas control. Geometry and binding methods (pack, grid, place, bind,
    destroy ...) go straight to the canvas."""

    def __init__(self, ui, parent, width, height, bg, cursor='hand2', takefocus=False):
        self.ui = ui
        self.hover = 0.0
        self.canvas = ui.tk.Canvas(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0,
                                   cursor=cursor, takefocus=takefocus)

    def __getattr__(self, name):
        if name == 'canvas':
            raise AttributeError(name)
        return getattr(self.canvas, name)

    def fade_hover(self, target, ms=140):
        self.ui.motion.tween((id(self), 'hover'), ms, self.hover, target, self.set_hover)

    def set_hover(self, value):
        self.hover = value
        self.paint()

    def paint(self):
        pass


class FlatButton(Widget):
    """A flat button: kind 'ghost' (quiet) or 'primary' (accent)."""

    def __init__(self, ui, parent, text, command, kind='ghost', bg=CARD, width=None, font='body'):
        px = ui.px
        self.text, self.command, self.kind, self.bg, self.state = text, command, kind, bg, 'normal'
        self.font, self.pressed, self.focused = ui.fonts[font], False, False
        self.fixed_width = width
        w = width or ui.measure(self.font, text) + px(32)
        super().__init__(ui, parent, w, px(34), bg, takefocus=True)
        c = self.canvas
        self.rect = c.create_rectangle(0, 0, w - 1, px(34) - 1, width=1)
        self.label = c.create_text(w // 2, px(34) // 2, text=text, font=self.font)
        c.bind('<Enter>', lambda _e: self.state == 'normal' and self.fade_hover(1.0))
        c.bind('<Leave>', lambda _e: self.leave())
        c.bind('<ButtonPress-1>', self.press)
        c.bind('<ButtonRelease-1>', self.release)
        c.bind('<FocusIn>', lambda _e: self.focus(True))
        c.bind('<FocusOut>', lambda _e: self.focus(False))
        c.bind('<Return>', lambda _e: self.invoke())
        c.bind('<space>', lambda _e: self.invoke())
        self.paint()

    def leave(self):
        self.pressed = False
        self.fade_hover(0.0, 200)

    def focus(self, on):
        self.focused = on
        self.paint()

    def press(self, _event):
        if self.state == 'normal':
            self.pressed = True
            self.canvas.focus_set()
            self.paint()

    def release(self, event):
        was, self.pressed = self.pressed, False
        self.paint()
        if was and 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height():
            self.invoke()

    def invoke(self):
        if self.state == 'normal':
            self.command()

    def configure(self, **options):
        if 'text' in options:
            self.text = options['text']
            self.canvas.itemconfigure(self.label, text=self.text)
            if not self.fixed_width:
                w = self.ui.measure(self.font, self.text) + self.ui.px(32)
                self.canvas.configure(width=w)
                self.canvas.coords(self.rect, 0, 0, w - 1, self.ui.px(34) - 1)
                self.canvas.coords(self.label, w // 2, self.ui.px(34) // 2)
        if 'state' in options:
            self.state = options['state']
            self.canvas.configure(cursor='hand2' if self.state == 'normal' else 'arrow')
            if self.state != 'normal':
                self.hover = 0.0
        self.paint()

    def paint(self):
        c, t, bg = self.canvas, self.hover, self.bg
        if self.state != 'normal':
            fill, line, ink = bg, mix(bg, TEXT, .08), DIM
        elif self.kind == 'primary':
            fill, line, ink = mix(BLOOD_DEEP, BLOOD, t), mix(BLOOD, BLOOD_HI, t), TEXT
        else:
            fill = mix(mix(bg, TEXT, .05), mix(bg, TEXT, .13), t)
            line, ink = mix(mix(bg, TEXT, .14), mix(bg, TEXT, .34), t), TEXT
        if self.pressed:
            fill = mix(fill, BG, .35)
        c.itemconfigure(self.rect, fill=fill, outline=MUTED if self.focused else line)
        c.itemconfigure(self.label, fill=ink)


class PlayButton(Widget):
    """The big primary button: chamfered corners, hover glow, a slow idle pulse, pressed state."""

    def __init__(self, ui, parent, text, command):
        px = ui.px
        self.pad, self.w, self.h = px(12), px(228), px(58)
        self.text, self.command, self.state, self.pressed, self.pulse = text, command, 'normal', False, 0.5
        super().__init__(ui, parent, self.w + 2 * self.pad, self.h + 2 * self.pad, BG, takefocus=True)
        c, pad, cut = self.canvas, self.pad, px(10)
        self.cut = cut
        self.rings = [c.create_polygon(self.outline(pad - px(2) * i, cut + px(2) * i), fill='', width=px(2))
                      for i in range(5, 0, -1)]
        self.body = c.create_polygon(self.outline(pad, cut), width=1)
        self.sheen = c.create_polygon(self.sheen_points(), outline='')
        self.label = c.create_text(pad + self.w // 2, pad + self.h // 2, text=text, font=ui.fonts['play'])
        c.bind('<Enter>', lambda _e: self.state == 'normal' and self.fade_hover(1.0, 200))
        c.bind('<Leave>', lambda _e: self.leave())
        c.bind('<ButtonPress-1>', self.press)
        c.bind('<ButtonRelease-1>', self.release)
        c.bind('<Return>', lambda _e: self.invoke())
        c.bind('<space>', lambda _e: self.invoke())
        self.animate(True)
        self.paint()

    def outline(self, inset_x0, cut):
        """The chamfered rectangle grown to start at x = inset_x0 (the same margin on all sides)."""
        grow = self.pad - inset_x0
        x0, y0, x1, y1 = self.pad - grow, self.pad - grow, self.pad + self.w + grow, self.pad + self.h + grow
        return [x0 + cut, y0, x1 - cut, y0, x1, y0 + cut, x1, y1 - cut, x1 - cut, y1, x0 + cut, y1, x0, y1 - cut,
                x0, y0 + cut]

    def sheen_points(self):
        x0, y0, x1, ym = self.pad + 1, self.pad + 1, self.pad + self.w - 1, self.pad + self.h // 2
        c = self.cut
        return [x0 + c, y0, x1 - c, y0, x1, y0 + c, x1, ym, x0, ym, x0, y0 + c]

    def animate(self, on):
        if on:
            self.ui.motion.loop((id(self), 'pulse'), 40, self.beat)
        else:
            self.ui.motion.stop((id(self), 'pulse'))
            self.pulse = 0.5
            self.paint()

    def beat(self, now):
        if self.state == 'normal' and self.hover < 0.99:
            self.pulse = 0.5 + 0.5 * math.sin(now * 2 * math.pi / 2.8)
            self.paint()

    def leave(self):
        self.pressed = False
        self.fade_hover(0.0, 260)

    def press(self, _event):
        if self.state == 'normal':
            self.pressed = True
            self.canvas.focus_set()
            self.paint()

    def release(self, event):
        was, self.pressed = self.pressed, False
        self.paint()
        if was and 0 <= event.x < self.canvas.winfo_width() and 0 <= event.y < self.canvas.winfo_height():
            self.invoke()

    def invoke(self):
        if self.state == 'normal':
            self.command()

    def configure(self, **options):
        if 'text' in options:
            self.text = options['text']
            self.canvas.itemconfigure(self.label, text=self.text)
        if 'state' in options:
            self.state = options['state']
            self.canvas.configure(cursor='hand2' if self.state == 'normal' else 'arrow')
            if self.state != 'normal':
                self.hover = 0.0
        self.paint()

    def paint(self):
        c, t = self.canvas, self.hover
        if self.state != 'normal':
            glow, base, edge, ink = 0.0, '#2a1a1c', '#3a2a2c', DIM
        else:
            glow = 0.9 * t + (1 - t) * (0.12 + 0.34 * self.pulse)
            base = mix(BLOOD_DEEP, BLOOD, 0.35 + 0.65 * t)
            edge, ink = mix(BLOOD, '#e0a690', 0.18 + 0.5 * t), TEXT
            if self.pressed:
                base, glow = mix(base, BG, .35), 0.35
        for i, ring in enumerate(self.rings):  # outermost first
            c.itemconfigure(ring, outline=mix(BG, BLOOD_HI, glow * (i / len(self.rings)) ** 1.6 * 0.8))
        c.itemconfigure(self.body, fill=base, outline=edge)
        c.itemconfigure(self.sheen, fill=mix(base, '#ffffff', 0.045 if self.state == 'normal' else 0.0))
        c.itemconfigure(self.label, fill=ink)
        shift = self.ui.px(1) if self.pressed else 0
        c.coords(self.label, self.pad + self.w // 2, self.pad + self.h // 2 + shift)


class TabBar(Widget):
    """Text tabs with an accent underline that slides to the selected one."""

    def __init__(self, ui, parent, tabs, command):
        px = ui.px
        self.tabs, self.command, self.active = tabs, command, None
        self.h = px(48)
        super().__init__(ui, parent, 10, self.h, BG, cursor='arrow')
        c = self.canvas
        self.font = ui.fonts['tab']
        self.base_line = c.create_line(0, self.h - 1, 10, self.h - 1, fill=LINE)
        self.slots, self.hv, self.items, x = {}, {}, {}, px(20)
        for name, title in tabs:
            w = ui.measure(self.font, title) + px(32)
            self.slots[name] = (x, w)
            self.hv[name] = 0.0
            self.items[name] = c.create_text(x + w // 2, self.h // 2 - px(1), text=title, font=self.font, fill=MUTED)
            x += w
        self.mark = c.create_rectangle(0, self.h - px(3), 0, self.h - 1, fill=BLOOD_HI, outline='')
        self.mark_x, self.mark_w, self.aim, self.shown = 0.0, 0.0, {}, 0
        c.bind('<Configure>', lambda e: c.coords(self.base_line, 0, self.h - 1, e.width, self.h - 1))
        c.bind('<Motion>', self.moved)
        c.bind('<Leave>', lambda _e: self.hot(None))
        c.bind('<Button-1>', lambda e: self.pick(e.x))

    def set_height(self, h):
        self.shown = max(1, int(h))
        self.canvas.configure(height=self.shown)

    def at(self, x):
        return next((name for name, (x0, w) in self.slots.items() if x0 <= x < x0 + w), None)

    def pick(self, x):
        name = self.at(x)
        if name:
            self.command(name)

    def moved(self, event):
        self.hot(self.at(event.x))

    def hot(self, name):
        for tab in self.slots:
            target = 1.0 if tab == name else 0.0
            if self.aim.get(tab, 0.0) != target:
                self.aim[tab] = target
                self.ui.motion.tween((id(self), tab), 140, self.hv[tab], target, lambda v, t=tab: self.shade(t, v))
        self.canvas.configure(cursor='hand2' if name else 'arrow')

    def shade(self, tab, value):
        self.hv[tab] = value
        self.colour(tab)

    def colour(self, tab):
        self.canvas.itemconfigure(self.items[tab], fill=TEXT if tab == self.active else mix(MUTED, TEXT, self.hv[tab]))

    def select(self, name, animate=True):
        old, self.active = self.active, name
        x1, w1 = self.slots[name]
        x0, w0 = (self.mark_x, self.mark_w) if old else (x1, 0.0)

        def move(p):
            self.mark_x, self.mark_w = x0 + (x1 - x0) * p, w0 + (w1 - w0) * p
            self.canvas.coords(self.mark, self.mark_x + self.ui.px(10), self.h - self.ui.px(3),
                               self.mark_x + self.mark_w - self.ui.px(10), self.h - 1)
        self.ui.motion.tween((id(self), 'mark'), 260 if animate else 0, 0.0, 1.0, move)
        for tab in self.slots:
            self.colour(tab)


class ModeToggle(Widget):
    """Simple | Advanced: a segmented switch whose highlight slides."""

    def __init__(self, ui, parent, labels, command):
        px = ui.px
        self.command, self.labels, self.font = command, labels, ui.fonts['small_bold']
        seg = max(ui.measure(self.font, text) for text in labels) + px(34)
        self.seg, self.h = seg, px(32)
        super().__init__(ui, parent, 2 * seg, self.h, FIELD)
        c = self.canvas
        c.create_rectangle(0, 0, 2 * seg - 1, self.h - 1, fill=FIELD, outline=LINE_HI)
        self.slide = c.create_rectangle(1, 1, seg, self.h - 2, fill=BLOOD, outline=BLOOD_HI)
        self.texts = [c.create_text(seg * i + seg // 2, self.h // 2, text=t, font=self.font) for i, t in enumerate(labels)]
        self.pos, self.advanced, self.hv = 0.0, False, [0.0, 0.0]
        c.bind('<Button-1>', lambda e: self.command(e.x >= seg))
        c.bind('<Motion>', lambda e: self.over(int(e.x >= seg)))
        c.bind('<Leave>', lambda _e: self.over(None))
        self.paint()

    def over(self, index):
        for i in (0, 1):
            self.ui.motion.tween((id(self), i), 140, self.hv[i], 1.0 if i == index else 0.0,
                                 lambda v, i=i: self.shade(i, v))

    def shade(self, index, value):
        self.hv[index] = value
        self.paint()

    def select(self, advanced, animate=True):
        self.advanced = advanced
        self.ui.motion.tween((id(self), 'slide'), 240 if animate else 0, self.pos, 1.0 if advanced else 0.0, self.move)

    def move(self, pos):
        self.pos = pos
        x = 1 + pos * self.seg
        self.canvas.coords(self.slide, x, 1, x + self.seg - 1, self.h - 2)
        self.paint()

    def paint(self):
        for i, item in enumerate(self.texts):
            on = abs(self.pos - i)  # 0 when the highlight is under this label
            self.canvas.itemconfigure(item, fill=mix(mix(MUTED, TEXT, self.hv[i]), TEXT, 1 - min(1.0, on)))


class Switch(Widget):
    """An on/off switch bound to a BooleanVar; the knob slides when the variable changes."""

    def __init__(self, ui, parent, var, bg=CARD):
        px = ui.px
        self.w, self.h, self.var, self.pos = px(42), px(22), var, 1.0 if var.get() else 0.0
        self.enabled = True
        super().__init__(ui, parent, self.w, self.h, bg, takefocus=True)
        c = self.canvas
        self.track = c.create_rectangle(0, 0, self.w - 1, self.h - 1, width=1)
        self.knob = c.create_rectangle(0, 0, 0, 0, width=0)
        self.focused = False
        c.bind('<Button-1>', lambda _e: self.toggle())
        c.bind('<space>', lambda _e: self.toggle())
        c.bind('<Enter>', lambda _e: self.fade_hover(1.0))
        c.bind('<Leave>', lambda _e: self.fade_hover(0.0, 200))
        c.bind('<FocusIn>', lambda _e: self.focus(True))
        c.bind('<FocusOut>', lambda _e: self.focus(False))
        var.trace_add('write', lambda *_a: self.sync())
        self.paint()

    def focus(self, on):
        self.focused = on
        self.paint()

    def toggle(self):
        if self.enabled:
            self.var.set(not self.var.get())

    def set_enabled(self, enabled):
        """A greyed switch keeps its value and ignores clicks."""
        self.enabled = enabled
        self.canvas.configure(cursor='hand2' if enabled else 'arrow')
        self.paint()

    def sync(self):
        self.ui.motion.tween((id(self), 'pos'), 170, self.pos, 1.0 if self.var.get() else 0.0, self.move)

    def move(self, pos):
        self.pos = pos
        self.paint()

    def paint(self):
        c, p, px = self.canvas, self.pos, self.ui.px
        pad = px(3)
        x = pad + p * (self.w - self.h)
        c.coords(self.knob, x, pad, x + self.h - 2 * pad, self.h - pad - 1)
        if not self.enabled:
            c.itemconfigure(self.track, fill=mix('#221c1e', DIM, .2 + .2 * p), outline=mix(LINE_HI, '#221c1e', .5))
            c.itemconfigure(self.knob, fill=mix(DIM, '#221c1e', .35))
            return
        c.itemconfigure(self.track, fill=mix('#221c1e', BLOOD, p), outline=MUTED if self.focused else
                        mix(LINE_HI, BLOOD_HI, p))
        c.itemconfigure(self.knob, fill=mix(mix(DIM, MUTED, self.hover), TEXT, p))


class Slider(Widget):
    """A 0..max slider bound to a DoubleVar (drag or click)."""

    def __init__(self, ui, parent, var, low, high, width=300, bg=CARD):
        px = ui.px
        self.var, self.low, self.high, self.w, self.h = var, low, high, px(width), px(24)
        super().__init__(ui, parent, self.w, self.h, bg)
        c = self.canvas
        mid = self.h // 2
        self.trough = c.create_rectangle(px(7), mid - 2, self.w - px(7), mid + 2, fill=FIELD, outline=LINE)
        self.fill = c.create_rectangle(px(7), mid - 2, px(7), mid + 2, fill=BLOOD, outline='')
        self.knob = c.create_rectangle(0, 0, 0, 0, fill=TEXT, outline='')
        c.bind('<Button-1>', self.drag)
        c.bind('<B1-Motion>', self.drag)
        c.bind('<Enter>', lambda _e: self.fade_hover(1.0))
        c.bind('<Leave>', lambda _e: self.fade_hover(0.0, 200))
        var.trace_add('write', lambda *_a: self.paint())
        self.paint()

    def drag(self, event):
        edge = self.ui.px(7)
        fraction = max(0.0, min(1.0, (event.x - edge) / max(1, self.w - 2 * edge)))
        self.var.set(round(self.low + fraction * (self.high - self.low), 2))

    def paint(self):
        edge, mid = self.ui.px(7), self.h // 2
        try:
            fraction = (float(self.var.get()) - self.low) / (self.high - self.low)
        except self.ui.tk.TclError:
            fraction = 0.0
        x = edge + max(0.0, min(1.0, fraction)) * (self.w - 2 * edge)
        half = self.ui.px(5) + round(self.hover * self.ui.px(1))
        self.canvas.coords(self.fill, edge, mid - 2, x, mid + 2)
        self.canvas.coords(self.knob, x - half, mid - half - 1, x + half, mid + half + 1)
        self.canvas.itemconfigure(self.knob, fill=mix(MUTED, TEXT, 0.5 + self.hover * 0.5))


class Bar(Widget):
    """A progress bar: set(fraction) eases to the value, a light band runs across the fill."""

    def __init__(self, ui, parent, width=280, bg=CARD):
        px = ui.px
        self.w, self.h, self.frac, self.shown = px(width), px(8), 0.0, 0.0
        super().__init__(ui, parent, self.w, self.h, bg, cursor='arrow')
        c = self.canvas
        c.create_rectangle(0, 0, self.w - 1, self.h - 1, fill=FIELD, outline=LINE)
        self.fill = c.create_rectangle(1, 1, 1, self.h - 2, fill=BLOOD_HI, outline='')
        self.band = c.create_rectangle(0, 1, 0, self.h - 2, fill=mix(BLOOD_HI, TEXT, .45), outline='', state='hidden')
        self.running = False

    def set(self, fraction):
        self.frac = max(0.0, min(1.0, fraction))
        self.ui.motion.tween((id(self), 'value'), 220, self.shown, self.frac, self.draw)
        if 0.0 < self.frac < 1.0 and not self.running:
            self.running = True
            self.ui.motion.loop((id(self), 'band'), 30, self.sweep)
        elif self.frac in (0.0, 1.0):
            self.running = False
            self.ui.motion.stop((id(self), 'band'))
            self.canvas.itemconfigure(self.band, state='hidden')

    def draw(self, value):
        self.shown = value
        self.canvas.coords(self.fill, 1, 1, 1 + value * (self.w - 3), self.h - 2)

    def sweep(self, now):
        edge, fill = 1 + self.shown * (self.w - 3), self.ui.px(40)
        x = (now * self.ui.px(160)) % (edge + fill) - fill
        self.canvas.coords(self.band, max(1, x), 1, min(edge, x + fill), self.h - 2)
        self.canvas.itemconfigure(self.band, state='normal' if edge > 2 and x + fill > 1 else 'hidden')


class Spinner(Widget):
    """A small rotating arc for work of unknown length."""

    def __init__(self, ui, parent, size=18, bg=BG):
        s = ui.px(size)
        super().__init__(ui, parent, s, s, bg, cursor='arrow')
        w = ui.px(2)
        self.canvas.create_oval(w, w, s - w, s - w, outline=LINE_HI, width=w)
        self.arc = self.canvas.create_arc(w, w, s - w, s - w, start=90, extent=100, style='arc', outline=BLOOD_HI, width=w)
        self.on = False

    def start(self):
        if not self.on:
            self.on = True
            self.ui.motion.loop((id(self), 'spin'), 30, lambda now: self.canvas.itemconfigure(
                self.arc, start=-(now * 300) % 360))

    def stop(self):
        self.on = False
        self.ui.motion.stop((id(self), 'spin'))


class StatusLine:
    """One line of the ready check: a coloured glyph (or a spinner while it is working) and text."""

    def __init__(self, ui, parent, bg=CARD):
        px = ui.px
        self.ui, self.frame = ui, ui.tk.Frame(parent, bg=bg)
        slot = ui.tk.Frame(self.frame, bg=bg, width=px(24), height=px(22))
        slot.pack(side='left', anchor='n')
        slot.pack_propagate(False)
        self.glyph = ui.tk.Label(slot, bg=bg, fg=DIM, font=ui.fonts['body_bold'])
        self.spinner = Spinner(ui, slot, 16, bg)
        self.glyph.pack()
        self.text = ui.tk.Label(self.frame, bg=bg, fg=TEXT, font=ui.fonts['body'], justify='left', anchor='w',
                                wraplength=px(280))
        self.text.pack(side='left', fill='x', expand=True, anchor='n')

    def set(self, text, busy=False):
        glyph, rest = (text[0], text[1:].strip()) if text[:1] in GLYPH_COLORS else ('', text)
        self.text.configure(text=rest, fg=MUTED if glyph == '•' else TEXT)
        if busy:
            self.glyph.pack_forget()
            self.spinner.pack(pady=(self.ui.px(2), 0))
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.pack_forget()
            self.glyph.pack()
            self.glyph.configure(text=glyph or '•', fg=GLYPH_COLORS.get(glyph, DIM))


class Hero:
    """The banner: cover art under a dark gradient (or a blood moon without art), the title
    that writes itself in, drifting embers, and a height that eases between the two views."""

    EMBERS = 36

    def __init__(self, ui, parent, tall, short):
        self.ui, self.tall, self.short, self.h, self.w = ui, tall, short, tall, 0
        c = self.canvas = ui.tk.Canvas(parent, height=tall, bg=BG, highlightthickness=0, bd=0)
        self.art_path, self.art_size, self.images, self.reveal = None, 0, {}, 1.0
        self.art = c.create_image(0, 0, anchor='nw', tags='art')
        self.vignette = c.create_image(0, 0, anchor='nw', tags='art')
        self.halo = [c.create_oval(0, 0, 0, 0, outline='', tags='moon') for _i in range(18)]
        self.disc = c.create_oval(0, 0, 0, 0, fill='#8a1a1f', outline='#b8383a', tags='moon')
        self.craters = [c.create_oval(0, 0, 0, 0, fill='#771519', outline='', tags='moon') for _i in range(3)]
        self.embers = []
        self.scrim_b = c.create_image(0, 0, anchor='nw', tags='scrim')
        self.scrim_l = c.create_image(0, 0, anchor='nw', tags='scrim')
        self.strips = [c.create_rectangle(0, 0, 0, 0, fill=BG, outline='', stipple=s, tags='scrim')
                       for s in ('gray12', 'gray25', 'gray50', 'gray75', '')]
        self.rule = c.create_line(0, 0, 0, 0, fill=LINE, tags='txt')
        self.accent = c.create_rectangle(0, 0, 0, 0, fill=BLOOD_HI, outline='', tags='txt')
        font = ui.fonts['title']
        self.letters, self.xs, x = [], [], 0
        for char in 'BLOODBORNE':
            self.letters.append(c.create_text(0, 0, text=char, anchor='sw', font=font, fill=TEXT, tags='txt'))
            self.xs.append(x)
            x += ui.measure(font, char) + ui.px(5)
        self.sub = c.create_text(0, 0, text='', anchor='sw', font=ui.fonts['small'], fill=MUTED, tags='txt')
        self.info = c.create_text(0, 0, text='', anchor='sw', font=ui.fonts['small_bold'], fill=GOLD, tags='txt')
        self.pending = None
        c.bind('<Configure>', self.resized)

    # ---- content ----------------------------------------------------------------------------
    def set_texts(self, sub, info):
        self.canvas.itemconfigure(self.sub, text=sub)
        self.canvas.itemconfigure(self.info, text=info)

    def set_art(self, path):
        path = Path(path)
        self.art_path = path if path.is_file() else None
        self.art_size = 0
        self.rebuild()

    def resized(self, event):
        if abs(event.width - self.w) > 2:
            self.w = event.width
            if self.pending:
                self.canvas.after_cancel(self.pending)
            self.pending = self.canvas.after(90, self.rebuild)

    def flush(self):
        """Builds the pending images now (the first frame must not appear without the art)."""
        if self.pending:
            self.canvas.after_cancel(self.pending)
        self.w = self.w if self.w > 100 else self.canvas.winfo_width()
        self.rebuild()

    def rebuild(self):
        """Pillow: art crop, vignette and scrims in one go; plain Tk images and stipple without it."""
        self.pending = None
        w, tall, px, c = self.w, self.tall, self.ui.px, self.canvas
        if w < 100:
            return
        has_art = bool(self.art_path)
        for item in self.halo + [self.disc] + self.craters:
            c.itemconfigure(item, state='hidden' if has_art else 'normal')
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.rebuild_plain(w, has_art)
            return
        rgb = tuple(int(BG[i:i + 2], 16) for i in (1, 3, 5))
        resample = Image.Resampling.LANCZOS
        art = None
        if has_art:
            try:
                with Image.open(self.art_path) as source:
                    source = source.convert('RGB')
                scale = max(w / source.width, tall / source.height)
                size = (max(w, round(source.width * scale)), max(tall, round(source.height * scale)))
                source = source.resize(size, resample, reducing_gap=2.0)
                top = int(min(max(0.36 * size[1] - tall / 2, 0), size[1] - tall))
                left = (size[0] - w) // 2
                art = source.crop((left, top, left + w, top + tall))
                art = Image.blend(art, Image.new('RGB', art.size, rgb), 0.34)
            except (OSError, ValueError):
                art = None
        if art is None:  # a faint red haze around the moon, as the backdrop
            art = Image.new('RGB', (w, tall), rgb)
            glow = Image.radial_gradient('L').resize((int(w * 0.9), int(tall * 3.0)), Image.Resampling.BICUBIC)
            glow = glow.point(lambda v: int(max(0, 255 - v * 1.35) * 0.30))
            cx, cy = self.moon_center(tall)
            art.paste(Image.new('RGB', glow.size, (58, 14, 18)), (int(cx - glow.width / 2), int(cy - glow.height / 2)), glow)
        edge = Image.radial_gradient('L').resize((w, tall), Image.Resampling.BICUBIC)
        edge = edge.point(lambda v: 0 if v < 125 else min(255, int((v - 125) * 2.3 * 0.9)))
        vignette = Image.new('RGBA', (w, tall), rgb + (255,))
        vignette.putalpha(edge)
        down = Image.linear_gradient('L')
        scrim_h = px(190)
        bottom = Image.new('RGBA', (w, scrim_h), rgb + (255,))
        bottom.putalpha(down.resize((w, scrim_h), Image.Resampling.BICUBIC).point(lambda v: int(255 * (v / 255) ** 1.5 * 0.97)))
        scrim_w = px(640)
        left = Image.new('RGBA', (scrim_w, tall), rgb + (255,))
        left.putalpha(down.transpose(Image.Transpose.ROTATE_270).resize((scrim_w, tall), Image.Resampling.BICUBIC)
                      .point(lambda v: int(255 * (v / 255) ** 1.7 * 0.78)))
        self.images = {'art': ImageTk.PhotoImage(art), 'vig': ImageTk.PhotoImage(vignette),
                       'sb': ImageTk.PhotoImage(bottom), 'sl': ImageTk.PhotoImage(left)}
        self.scrim_h, self.art_off = scrim_h, (0, 0)
        c.itemconfigure(self.art, image=self.images['art'])
        c.itemconfigure(self.vignette, image=self.images['vig'])
        c.itemconfigure(self.scrim_b, image=self.images['sb'])
        c.itemconfigure(self.scrim_l, image=self.images['sl'])
        for strip in self.strips:
            c.itemconfigure(strip, state='hidden')
        self.layout()

    def rebuild_plain(self, w, has_art):
        c = self.canvas
        self.images, self.art_off = {}, (0, 0)
        if has_art:
            try:
                image = self.ui.tk.PhotoImage(file=str(self.art_path))
                factor = max(1, image.width() // max(w, 1))  # Tk can only drop whole pixels: keep it wider, crop
                self.images['art'] = image.subsample(factor) if factor > 1 else image
                art = self.images['art']
                self.art_off = ((w - art.width()) // 2, -int(min(max(0.36 * art.height() - self.tall / 2, 0),
                                                                max(0, art.height() - self.tall))))
            except (self.ui.tk.TclError, OSError):
                pass
        c.itemconfigure(self.art, image=self.images.get('art', ''))
        for item in (self.vignette, self.scrim_b, self.scrim_l):
            c.itemconfigure(item, image='')
        for strip in self.strips:
            c.itemconfigure(strip, state='normal')
        self.scrim_h = self.ui.px(90)
        self.layout()

    # ---- geometry ---------------------------------------------------------------------------
    def moon_center(self, h):
        return self.w * 0.76, h * 0.48

    def set_height(self, h):
        self.h = int(h)
        self.canvas.configure(height=self.h)
        self.layout()

    def layout(self):
        c, h, w, px = self.canvas, self.h, max(self.w, 1), self.ui.px
        ox, oy = getattr(self, 'art_off', (0, 0))
        c.coords(self.art, ox, oy - int((self.tall - h) * 0.45))
        c.coords(self.vignette, 0, -int((self.tall - h) * 0.45))
        c.coords(self.scrim_b, 0, h - getattr(self, 'scrim_h', px(120)))
        c.coords(self.scrim_l, 0, -int((self.tall - h) * 0.45))
        band = getattr(self, 'scrim_h', px(120)) // 5
        for i, strip in enumerate(self.strips):
            c.coords(strip, 0, h - band * (5 - i), w, h - band * (4 - i) if i < 4 else h)
        c.coords(self.rule, 0, h - 1, w, h - 1)
        base = h - px(70)
        for i, item in enumerate(self.letters):
            c.coords(item, px(36) + self.xs[i], base + round((1 - self.letter_alpha(i)) * px(14)))
        c.coords(self.accent, px(36), h - px(60), px(36) + px(44), h - px(58))
        c.coords(self.sub, px(36), h - px(36))
        c.coords(self.info, px(36), h - px(16))
        cx, cy = self.moon_center(h)
        radius = px(46)
        c.coords(self.disc, cx - radius, cy - radius, cx + radius, cy + radius)
        for item, (dx, dy, r) in zip(self.craters, ((-14, -8, 12), (10, 14, 9), (16, -18, 6))):
            c.coords(item, cx + px(dx) - px(r), cy + px(dy) - px(r), cx + px(dx) + px(r), cy + px(dy) + px(r))
        for i, item in enumerate(self.halo):
            r = radius * (1 + 3.4 * (len(self.halo) - i) / len(self.halo))
            c.coords(item, cx - r, cy - r, cx + r, cy + r)
        self.breathe(0.5)
        self.raise_text()

    def raise_text(self):
        self.canvas.tag_raise('scrim')
        self.canvas.tag_raise('txt')

    def breathe(self, b):
        n = len(self.halo)
        for i, item in enumerate(self.halo):  # outermost first
            level = (i / n) ** 2.4 * (0.55 + 0.45 * b)
            self.canvas.itemconfigure(item, fill=mix(BG, '#3c1013', level) if i else '')

    # ---- motion -----------------------------------------------------------------------------
    def letter_alpha(self, i):
        return max(0.0, min(1.0, (self.reveal * 1.5 - i * 0.07) / 0.6))

    def set_reveal(self, p):
        self.reveal = p
        c = self.canvas
        for i, item in enumerate(self.letters):
            a = ease_out(self.letter_alpha(i))
            c.itemconfigure(item, fill=mix(BG, TEXT, a))
        late = max(0.0, min(1.0, (p - 0.55) / 0.45))
        c.itemconfigure(self.sub, fill=mix(BG, MUTED, late))
        c.itemconfigure(self.info, fill=mix(BG, GOLD, late))
        c.itemconfigure(self.accent, fill=mix(BG, BLOOD_HI, late))
        base = self.h - self.ui.px(70)
        for i, item in enumerate(self.letters):
            c.coords(item, self.ui.px(36) + self.xs[i], base + round((1 - ease_out(self.letter_alpha(i))) * self.ui.px(14)))

    def intro(self):
        self.ui.motion.tween('reveal', 1100, 0.0, 1.0, self.set_reveal, ease=linear)

    def animate(self, on):
        motion = self.ui.motion
        if on:
            motion.loop('moon', 70, lambda now: self.breathe(0.5 + 0.5 * math.sin(now * 2 * math.pi / 7.0)))
            motion.loop('embers', 33, self.drift)
        else:
            motion.stop('moon')
            motion.stop('embers')
            for item, *_rest in self.embers:
                self.canvas.delete(item)
            self.embers = []
            self.breathe(0.5)

    def spawn(self, first=False):
        r, w, h = random.Random(), max(self.w, 400), self.h
        size = r.uniform(1.2, 2.6) * self.ui.dpi
        return [None, r.uniform(0, w), r.uniform(0, h) if first else h + r.uniform(0, 30), size,
                r.uniform(14, 34) * self.ui.dpi, r.uniform(-8, 8) * self.ui.dpi, r.uniform(0, 6.28), r.uniform(6, 13), 0.0]

    def drift(self, now):
        c = self.canvas
        if not self.embers:
            for _i in range(self.EMBERS):
                ember = self.spawn(first=True)
                ember[8] = random.random() * ember[7]
                ember[0] = c.create_oval(0, 0, 0, 0, outline='', fill=BG, tags='ember')
                self.embers.append(ember)
            c.tag_raise('ember', 'moon')
            self.raise_text()
            self.last = now
        dt = min(0.1, now - self.last)
        self.last = now
        for ember in self.embers:
            item, x, y, size, rise, sway, phase, life, age = ember
            age += dt
            if age >= life or y < -10:
                ember[1:] = self.spawn()[1:]
                age = 0.0
                x, y, size, rise, sway, phase, life = ember[1], ember[2], ember[3], ember[4], ember[5], ember[6], ember[7]
            y -= rise * dt
            ember[2], ember[8] = y, age
            fraction = age / life
            fade = min(1.0, fraction * 5) * (1 - fraction) ** 1.3
            px = x + math.sin(now * 0.9 + phase) * sway * 3
            r = size * (0.5 + 0.7 * fade)
            c.coords(item, px - r, y - r, px + r, y + r)
            c.itemconfigure(item, fill=mix(BG, mix('#f0a050', '#8a2a1a', fraction), fade * 1.1))


class Launcher:
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        from tkinter import font as tkfont
        self.tk, self.ttk, self.filedialog, self.messagebox, self.tkfont = tk, ttk, filedialog, messagebox, tkfont
        self.root = root
        self.app = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
        self.ini, self.ini_lines = load_ini()
        self.vars = {}
        # The ReShade preset lives in bin/reshade/ReShade.ini (the game's menu changes it too), not in settings.json.
        self.reshade_preset = tk.StringVar(value=get_reshade_preset() if reshade_ready() else '')
        self.reshade_started, self.reshade_refreshers = self.reshade_preset.get(), []
        self.process = self.job = None
        self.downloading = self.downloading_fsr4vk = self.fsr4vk_hashing = self.rechecking = False
        self.close_scheduled = False  # "Close the launcher when the game starts" is armed once per run
        self.installing, self.install_proc, self.install_cancel = False, None, threading.Event()
        self.install_card = self.install_hide = self.pkg_dialog = None
        self.output = queue.Queue()
        self.gpu_text = _('• Checking the graphics card…', '• Проверка видеокарты…')
        self.gpu_checking = True
        # bb-gpu-capabilities --upscalers: None until it has run; the dropdowns of the Simple and
        # Advanced views; the one-time line that says the saved upscaler was changed.
        self.upscaler_support, self.upscaler_boxes, self.upscaler_notice = None, [], ''
        self.ui_calls = queue.Queue()  # work for the Tk thread from helper threads
        self.mod_order, self.mod_vars, self.patch_vars = [], {}, {}
        self.cards, self.hot_card, self.measures = set(), None, {}
        # Controls page: the row waiting for a key, the rows' widgets, the gamepads SDL listed (None: not asked; False:
        # the list could not be read), and the message under the card.
        self.capturing, self.control_rows, self.gamepads, self.listing_gamepads = None, {}, None, False
        # A Shift, Ctrl or Alt pressed while an input waits: bound when it is let go, unless a mouse input comes
        # first (then it is the combination's modifier); the side buttons held when the wait began.
        self.pending_modifier, self.side_buttons_down, self.side_poll = None, set(), None
        self.controls_message = None
        root.title('Bloodborne — bbport')
        root.configure(bg=BG)
        self.dpi = root.winfo_fpixels('1i') / 96.0
        self.motion = Motion(root, tk.TclError, os.environ.get('BB_LAUNCHER_ANIMATIONS', '1') != '0'
                             and bool(self.app.get('animations', True)))
        if self.motion.enabled:
            try:
                root.attributes('-alpha', 0.0)  # faded in once the first frame is drawn
            except tk.TclError:
                pass
        # The width is fixed here; the height starts at 780 and fit_window grows it to whatever the page
        # being shown needs, within the work area of the screen (the screen minus the taskbar).
        left, top, right, bottom = work_area((root.winfo_screenwidth(), root.winfo_screenheight()))
        size = (min(self.px(1120), right - left - self.px(40)), min(self.px(780), bottom - top - self.frame_extra()))
        origin = (left + (right - left - size[0]) // 2, top + max(0, (bottom - top - size[1] - self.frame_extra()) // 3))
        root.geometry(f'{size[0]}x{size[1]}+{origin[0]}+{origin[1]}')
        self.planned = (*size, *origin)  # what was asked of the window manager, until the window is on screen
        root.minsize(min(self.px(980), size[0]), min(self.px(660), size[1]))
        # The banner: tall in the Simple view, short in the Advanced one; fit_window lowers them (never
        # under hero_min) when the work area is too short for the page.
        self.hero_tall, self.hero_short, self.hero_min = self.px(142), self.px(140), self.px(120)
        self.fit_pending = False
        self.set_icon()
        self.pick_fonts()
        self.style()
        self.build()
        self.set_mode(bool(self.var('ui_advanced', 'app').get()), animate=False)
        self.show('play', animate=False)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain_output)
        root.after(30, self.start_motion)
        threading.Thread(target=self.detect_gpu, daemon=True).start()
        if self.app.get('check_updates', True):
            threading.Thread(target=self.check_update, daemon=True).start()

    def px(self, size):
        return int(size * self.dpi)

    def set_icon(self):
        try:
            self.root.iconbitmap(default=str(PORT_DIR / 'launcher' / 'bloodborne.ico'))
            return
        except self.tk.TclError:
            pass
        for icon in (PORT_DIR / 'launcher' / 'bloodborne.png', Path(self.app['game_dir']) / 'sce_sys' / 'icon0.png'):
            try:
                self.icon = self.tk.PhotoImage(file=str(icon))
                if self.icon.width() > 128:
                    self.icon = self.icon.subsample(self.icon.width() // 64)
                self.root.iconphoto(True, self.icon)
                return
            except (self.tk.TclError, OSError):
                continue

    # ---- look --------------------------------------------------------------------------------
    def pick_fonts(self):
        """Cinzel when installed, else Palatino or Georgia for the titles; Segoe UI for the rest."""
        families = set(self.tkfont.families(self.root))
        serif = next((f for f in ('Cinzel', 'Palatino Linotype', 'Georgia') if f in families), 'Times New Roman')
        sans = 'Segoe UI' if 'Segoe UI' in families else 'Arial'
        self.fonts = {'body': (sans, 10), 'body_bold': (sans, 10, 'bold'), 'small': (sans, 9),
                      'small_bold': (sans, 9, 'bold'), 'tab': (sans, 11), 'h1': (serif, 20), 'h2': (serif, 13),
                      'title': (serif, 34), 'play': (serif, 16, 'bold'), 'mono': ('Consolas', 9)}

    def measure(self, spec, text):
        if spec not in self.measures:
            self.measures[spec] = self.tkfont.Font(root=self.root, font=spec)
        return self.measures[spec].measure(text)

    def style(self):
        ttk, px, f = self.ttk, self.px, self.fonts
        s = ttk.Style(self.root)
        s.theme_use('clam')
        for option, value in (('background', FIELD), ('foreground', TEXT), ('selectBackground', BLOOD),
                              ('selectForeground', TEXT), ('font', f['body']), ('borderWidth', 0),
                              ('highlightThickness', 0), ('relief', 'flat')):
            self.root.option_add(f'*TCombobox*Listbox.{option}', value)
        s.configure('.', background=CARD, foreground=TEXT, fieldbackground=FIELD, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, troughcolor=FIELD, focuscolor=CARD, font=f['body'])
        s.configure('TCombobox', arrowcolor=MUTED, foreground=TEXT, background=CARD, padding=(8, 6), arrowsize=px(15),
                    selectbackground=FIELD, selectforeground=TEXT, bordercolor=LINE_HI, lightcolor=FIELD, darkcolor=FIELD)
        s.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', FIELD)], selectforeground=[('readonly', TEXT)],
              background=[('active', LINE), ('pressed', LINE)], arrowcolor=[('active', TEXT), ('pressed', TEXT)],
              bordercolor=[('focus', MUTED), ('active', MUTED)], lightcolor=[('focus', FIELD)],
              darkcolor=[('focus', FIELD)])
        s.configure('TEntry', foreground=TEXT, insertcolor=TEXT, padding=(8, 6), bordercolor=LINE_HI, lightcolor=FIELD,
                    darkcolor=FIELD, selectbackground=BLOOD, selectforeground=TEXT)
        s.map('TEntry', bordercolor=[('focus', MUTED)], lightcolor=[('focus', FIELD)], darkcolor=[('focus', FIELD)])
        s.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        s.configure('Vertical.TScrollbar', background=LINE_HI, troughcolor=PANEL, bordercolor=PANEL, lightcolor=LINE_HI,
                    darkcolor=LINE_HI, gripcount=0, width=px(9))
        s.map('Vertical.TScrollbar', background=[('active', MUTED), ('pressed', MUTED)],
              lightcolor=[('active', MUTED)], darkcolor=[('active', MUTED)])

    # ---- widget helpers ----------------------------------------------------------------------
    def var(self, key, store):
        """The Tk variable of a setting (one per setting, shared by every widget that shows it)."""
        tk = self.tk
        if key not in self.vars:
            if store == 'ini':
                value = self.ini.get(key, INI_DEFAULTS[key])
                if key in INI_FLAGS:
                    v = tk.BooleanVar(value=value == '1')
                elif key in INI_FLOATS:
                    v = tk.DoubleVar(value=ini_float(key, value))
                else:
                    v = tk.StringVar(value=value)
            else:
                value = self.app.get(key, APP_DEFAULTS[key])
                if isinstance(APP_DEFAULTS[key], bool):
                    v = tk.BooleanVar(value=bool(value))
                else:
                    v = tk.StringVar(value=str(value))
            v.store = store
            self.vars[key] = v
        return self.vars[key]

    def choice(self, parent, key, store, options, width=None):
        """A combobox over (value, (english, russian)) pairs, kept in sync with its variable."""
        var = self.var(key, store)
        values = [v for v, _t in options]
        texts = [_(*text) for _v, text in options]
        box = self.ttk.Combobox(parent, values=texts, state='readonly',
                                width=width or min(62, max(30, max(map(len, texts)) + 3)))
        if var.get() not in values:
            var.set(values[0])

        def show(*_args):
            current = var.get()
            box.current(values.index(current) if current in values else 0)
        show()
        box.bind('<<ComboboxSelected>>', lambda _e: var.set(values[box.current()]))
        var.trace_add('write', show)
        return box

    def upscaler_states(self):
        """{upscaler: (state, reason)} for the dropdown, from the GPU check, the FSR 4 assets and, for
        the experimental FSR 4.1.1, the Experimental features switch and its downloaded files."""
        missing, experimental, files = bool(fsr4_missing()), bool(self.var('experimental', 'app').get()), fsr4vk_present()
        return {value: upscaler_state(value, self.upscaler_support, missing, experimental, files)
                for value, _t in UPSCALERS}

    def upscaler_choice(self, parent, width=None):
        """The Upscaler dropdown. What this PC cannot run reads 'FSR 4.1.1 (not available: reason)', is
        greyed in the open list and refuses to be picked; the Simple and Advanced views share it."""
        var = self.var('upscaler', 'ini')
        values = [v for v, _t in UPSCALERS]
        style = f'Upscaler{len(self.upscaler_boxes)}.TCombobox'  # one per dropdown: each has its own width
        box = self.ttk.Combobox(parent, state='readonly', width=width or 44, style=style)
        ttk_style = self.ttk.Style(self.root)
        if var.get() not in values:
            var.set(values[0])

        def fill(*_args):
            texts = []
            for value, (state, reason) in zip(values, self.upscaler_states().values()):
                label = _(*dict(UPSCALERS)[value])
                if state != 'ok':
                    note = _('not available: {}', 'недоступно: {}').format(reason_text(reason)) if state == 'no' \
                        else reason_text(reason)
                    label = f"{label.split(' (')[0]} ({note})"
                texts.append(label)
            box.configure(values=texts)
            box.current(values.index(var.get()) if var.get() in values else 0)
            # The open list is as wide as the field; the long 'not available' lines need more.
            need = max(self.measure(self.fonts['body'], text) for text in texts) + self.px(48)
            ttk_style.configure(style, postoffset=(0, 0, max(0, need - box.winfo_reqwidth()), 0))

        def picked(_event):
            value = values[box.current()]
            if value == var.get() or self.upscaler_states()[value][0] != 'no':
                var.set(value)
            else:
                fill()  # refused: the list still shows the old choice

        def grey():
            """The popdown listbox is filled after -postcommand runs, hence the short delay."""
            states = self.upscaler_states()
            listbox = f"{self.root.tk.call('ttk::combobox::PopdownWindow', box)}.f.l"
            try:
                for index, value in enumerate(values):
                    if states[value][0] == 'no':
                        for option in ('-foreground', '-selectforeground'):
                            self.root.tk.call(listbox, 'itemconfigure', index, option, MUTED)
            except self.tk.TclError:
                pass

        box.refresh = fill
        box.configure(postcommand=lambda: box.after(1, grey))
        box.bind('<<ComboboxSelected>>', picked)
        var.trace_add('write', fill)
        self.upscaler_boxes.append(box)
        fill()
        return box

    def apply_upscaler_support(self):
        """After the GPU check, the FSR 4 download, or a change of Experimental features: re-mark the
        dropdowns; a saved upscaler this PC cannot run (the GPU, or FSR 4.1.1 without Experimental features
        or its files) becomes the best one that works (DLSS on an RTX GPU, else FSR 3.1), said once."""
        current = self.var('upscaler', 'ini').get()
        fallback = upscaler_fallback(current, self.upscaler_support, bool(self.var('experimental', 'app').get()),
                                     fsr4vk_present())
        if fallback:
            better, reason = fallback
            name = lambda value: _(*dict(UPSCALERS)[value]).split(' (')[0]
            self.upscaler_notice = _('⚠ {} is not available on this PC ({}). The upscaler is now {}.',
                                     '⚠ {} недоступен на этом ПК ({}). Теперь выбран {}.').format(
                name(current), reason_text(reason), name(better))
            self.var('upscaler', 'ini').set(better)
        for box in self.upscaler_boxes:
            box.refresh()
        if self.upscaler_notice:
            self.refresh_status()

    def button(self, parent, text, command, kind='ghost', bg=CARD, **options):
        return FlatButton(self, parent, text, command, kind, bg, **options)

    def label(self, parent, text, style='body', fg=TEXT, bg=CARD, **options):
        return self.tk.Label(parent, text=text, bg=bg, fg=fg, font=self.fonts[style], **options)

    def card(self, parent, title=None, grid=None, pad=24):
        """A bordered panel (it lights up under the pointer); returns the frame to put rows in."""
        tk, px = self.tk, self.px
        frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        if grid:
            frame.grid(sticky='nsew', **grid)
        else:
            frame.pack(fill='x', padx=px(32), pady=(0, px(16)))
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill='both', expand=True, padx=px(pad), pady=px(pad - 4))
        body.columnconfigure(0, minsize=px(210))
        body.columnconfigure(1, weight=1)
        self.cards.add(frame)
        body.frame, body.hovered = frame, 0.0
        if title:
            head = tk.Frame(body, bg=CARD)
            head.grid(row=0, column=0, columnspan=2, sticky='we')
            self.label(head, title, 'h2').pack(anchor='w')
            tk.Frame(head, bg=LINE, height=1).pack(fill='x', pady=(px(8), px(4)))
        return body

    def pointer_moved(self, event):
        """Lights the card under the pointer (border eases to the accent and back)."""
        widget, hot = event.widget, None
        while widget is not None and not isinstance(widget, str):
            if widget in self.cards:
                hot = widget
                break
            widget = getattr(widget, 'master', None)
        if hot is self.hot_card:
            return
        for card, target in ((self.hot_card, 0.0), (hot, 1.0)):
            if card is not None:
                self.motion.tween((id(card), 'glow'), 180, getattr(card, 'glow', 1.0 - target), target,
                                  lambda v, c=card: self.light(c, v))
        self.hot_card = hot

    def light(self, card, value):
        card.glow = value
        card.configure(highlightbackground=mix(LINE, BLOOD, value * 0.75))

    def next_row(self, parent):
        return parent.grid_size()[1]

    def row(self, parent, title, widget, hint=None, fill=False):
        px = self.px
        r = self.next_row(parent)
        self.label(parent, title, wraplength=px(200), justify='left').grid(row=r, column=0, sticky='nw', padx=(0, px(16)),
                                                                           pady=(px(14), 0))
        widget.grid(row=r, column=1, sticky='we' if fill else 'w', pady=(px(10), 0))
        if hint:
            self.label(parent, hint, 'small', MUTED, wraplength=px(560), justify='left').grid(
                row=r + 1, column=1, sticky='w', pady=(px(4), 0))
        return widget

    def switch(self, parent, var, text, wrap=0):
        frame = self.tk.Frame(parent, bg=CARD)
        knob = frame.knob = Switch(self, frame, var)
        knob.pack(side='left')
        label = self.label(frame, text, cursor='hand2', justify='left', anchor='w', wraplength=wrap)
        label.pack(side='left', padx=(self.px(12), 0))
        frame.caption = label
        label.bind('<Button-1>', lambda _e: knob.toggle())
        return frame

    def check(self, parent, key, store, title, hint=None):
        """A switch row with an optional note under it; returns (switch frame, note label or None)."""
        px = self.px
        r = self.next_row(parent)
        row = self.switch(parent, self.var(key, store), title, px(560))
        row.grid(row=r, column=0, columnspan=2, sticky='w', pady=(px(12), 0))
        note = None
        if hint:
            note = self.label(parent, hint, 'small', MUTED, wraplength=px(560), justify='left')
            note.grid(row=r + 1, column=0, columnspan=2, sticky='w', padx=(px(54), 0), pady=(px(2), 0))
        return row, note

    def grey_with_upscaler(self, row, *notes):
        """Frame generation works on top of an upscaler: the switch is greyed while the upscaler is Off
        (its value is kept)."""
        upscaler = self.var('upscaler', 'ini')

        def apply(*_args):
            on = upscaler.get() != 'off'
            row.knob.set_enabled(on)
            row.caption.configure(fg=TEXT if on else DIM)
            for note in notes:
                if note is not None:
                    note.configure(fg=MUTED if on else DIM)
        upscaler.trace_add('write', apply)
        apply()

    def note(self, parent, text, top=12):
        self.label(parent, text, 'small', MUTED, wraplength=self.px(680), justify='left').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(self.px(top), 0))

    def folder(self, parent, key, title, prompt, hint=None, on_change=None):
        px = self.px
        var = self.var(key, 'app')
        holder = self.tk.Frame(parent, bg=CARD)
        entry = self.ttk.Entry(holder, textvariable=var, width=40)
        entry.pack(side='left', fill='x', expand=True)

        def browse():
            chosen = self.filedialog.askdirectory(title=prompt, initialdir=var.get() or str(PORT_DIR))
            if chosen:
                var.set(str(Path(chosen)))
        self.button(holder, _('Browse…', 'Обзор…'), browse).pack(side='left', padx=(px(8), 0))
        self.button(holder, _('Open', 'Открыть'), lambda: self.open_path(var.get(), key)).pack(side='left', padx=(px(8), 0))
        if on_change:
            var.trace_add('write', lambda *_a: on_change())
        return self.row(parent, title, holder, hint, fill=True)

    def scrolled_page(self, name, title, subtitle):
        """A page: a heading (none for the Play page), then cards in a vertically scrolling area; the
        scrollbar shows only when the cards are taller than the window. outer.inner holds the cards."""
        tk, ttk, px = self.tk, self.ttk, self.px
        outer = tk.Frame(self.content, bg=PANEL)
        if title:
            head = tk.Frame(outer, bg=PANEL)
            head.pack(fill='x', padx=px(32), pady=(px(18), px(10)))
            self.label(head, title, 'h1', bg=PANEL).pack(anchor='w')
            self.label(head, subtitle, 'small', MUTED, PANEL, justify='left', wraplength=px(860)).pack(anchor='w', pady=(px(2), 0))
        body = tk.Frame(outer, bg=PANEL)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, bg=PANEL, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg=PANEL)
        window = canvas.create_window(0, 0, window=inner, anchor='nw')

        def fit(_event=None):
            canvas.configure(scrollregion=(0, 0, 1, inner.winfo_reqheight() + px(8)))
            if inner.winfo_reqheight() + px(8) > canvas.winfo_height():
                bar.pack(side='right', fill='y', padx=(0, px(4)), before=canvas)
            else:
                bar.pack_forget()
        inner.bind('<Configure>', fit)
        canvas.bind('<Configure>', lambda e: (canvas.itemconfigure(window, width=e.width), fit()))
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)

        def scroll(units):
            top, bottom = canvas.yview()
            if bottom - top >= 1:
                return
            target = max(0.0, min(1.0 - (bottom - top), top + units * px(64) / max(1, inner.winfo_height())))
            self.motion.tween(('scroll', name), 140, top, target, canvas.yview_moveto)
        outer.scroll, outer.inner = scroll, inner
        self.pages[name] = outer
        return inner

    # ---- layout ------------------------------------------------------------------------------
    def build(self):
        tk, px = self.tk, self.px
        root = self.root
        # Bottom bar: what is happening on the left, Stop and the big PLAY on the right.
        footer = tk.Frame(root, bg=BG)
        footer.pack(side='bottom', fill='x')
        tk.Frame(footer, bg=LINE, height=1).pack(fill='x')
        bar = tk.Frame(footer, bg=BG, height=px(88))
        bar.pack(fill='x')
        bar.pack_propagate(False)
        self.play_button = PlayButton(self, bar, _('PLAY', 'ИГРАТЬ'), self.play)
        self.play_button.pack(side='right', padx=(px(8), px(24)))
        self.stop_button = FlatButton(self, bar, _('Stop', 'Остановить'), self.stop, bg=BG)
        self.busy = Spinner(self, bar, 18, BG)
        self.status = tk.Label(bar, text='', bg=BG, fg=MUTED, font=self.fonts['body'], anchor='w', justify='left')
        self.status.pack(side='left', padx=(px(36), 0))
        self.footer = footer
        self.hero = Hero(self, root, self.hero_tall, self.hero_short)
        self.hero.canvas.pack(side='top', fill='x')
        self.tab_names = (('play', _('Play', 'Играть')), ('graphics', _('Graphics', 'Графика')),
                          ('display', _('Display & FPS', 'Экран и FPS')), ('controls', _('Controls', 'Управление')),
                          ('game', _('Game & effects', 'Игра и эффекты')),
                          ('cheats', _('Cheats', 'Читы')), ('mods', _('Mods & patches', 'Моды и патчи')),
                          ('advanced', _('Advanced', 'Дополнительно')), ('log', _('Log', 'Журнал')))
        self.tabbar = TabBar(self, root, self.tab_names, self.show)
        self.content = tk.Frame(root, bg=PANEL)
        self.content.pack(side='top', fill='both', expand=True)
        self.toggle = ModeToggle(self, self.hero.canvas, (_('Simple', 'Простой'), _('Advanced', 'Дополнительно')),
                                 self.set_mode)
        self.toggle.place(relx=1.0, x=-px(24), y=px(20), anchor='ne')
        self.hero.set_texts(_('native port · Windows', 'нативный порт · Windows') + f'  ·  v{VERSION}', '')
        if self.motion.enabled:
            self.hero.set_reveal(0.0)
        self.update_box = None
        self.current_page = None
        self.pages = {}
        self.build_play()
        self.build_graphics()
        self.build_display()
        self.build_controls()
        self.build_game()
        self.build_cheats()
        self.build_mods()
        self.build_advanced()
        self.build_log()
        self.root.bind_all('<MouseWheel>', self.wheel)
        self.root.bind_all('<Motion>', self.pointer_moved, add='+')
        self.refresh_hero()
        self.refresh_status()

    def start_motion(self):
        """First frame is up: fade the window in, write the title, start the ember drift."""
        self.root.update_idletasks()
        self.hero.flush()
        if self.motion.enabled:
            self.hero.set_reveal(0.0)
            self.motion.tween('fade', 280, 0.0, 1.0, lambda a: self.root.attributes('-alpha', a))
            self.hero.intro()
            self.hero.animate(True)

    def set_running(self, running, preparing=False):
        """The footer while the game runs: Stop appears, PLAY rests, a spinner shows during start-up."""
        px = self.px
        if running:
            self.stop_button.pack(side='right', padx=(0, px(8)))
            self.play_button.configure(state='disabled')
        else:
            self.stop_button.pack_forget()
        if preparing:
            self.busy.pack(side='left', padx=(px(12), 0))
            self.busy.start()
        else:
            self.busy.stop()
            self.busy.pack_forget()

    def wheel(self, event):
        page = self.pages.get(self.current_page)
        if hasattr(page, 'scroll') and not isinstance(event.widget, (self.tk.Text, self.ttk.Combobox)) \
                and 'popdown' not in str(event.widget):
            page.scroll(int(-event.delta / 120))

    def set_mode(self, advanced, animate=True):
        """Simple: the hero and the Play page. Advanced: every tab (the hero shrinks to make room)."""
        advanced = bool(advanced)
        if animate and advanced == self.var('ui_advanced', 'app').get():
            return
        self.var('ui_advanced', 'app').set(advanced)
        self.fit_window(set_hero=False)  # the banner eases to its new height below
        self.toggle.select(advanced, animate)
        ms = 320 if animate else 0
        if advanced:
            if not self.tabbar.canvas.winfo_manager():
                self.tabbar.set_height(1)
                self.tabbar.pack(side='top', fill='x', before=self.content)
            self.motion.tween('tabs', ms, self.tabbar.shown, self.tabbar.h, self.tabbar.set_height, ease=ease_in_out)
        else:
            if self.current_page not in (None, 'play'):
                self.show('play', animate)
            self.motion.tween('tabs', ms, self.tabbar.shown, 1, self.tabbar.set_height, self.tabbar.pack_forget,
                              ease_in_out)
        target = self.hero.short if advanced else self.hero.tall
        self.motion.tween('hero', ms, self.hero.h, target, self.hero.set_height, ease=ease_in_out)

    def show(self, name, animate=True):
        self.stop_capture()
        old, new = self.pages.get(self.current_page), self.pages[name]
        changed = name != self.current_page
        self.current_page = name
        self.tabbar.select(name, animate and changed)
        if changed:
            if old:
                old.place_forget()
            new.place(x=0, y=0, relwidth=1, relheight=1)
            self.motion.tween('slide', 260 if animate else 0, self.px(40), 0, lambda x: new.place_configure(x=int(x)))
        if name == 'mods':
            self.refresh_lists()
        elif name == 'controls' and self.gamepads is None:
            self.refresh_gamepads()
        elif name == 'graphics':
            self.refresh_fsr4()
            self.refresh_fsr4vk()
            self.refresh_reshade()
        elif name == 'play':
            self.refresh_status()
            self.refresh_reshade()
            self.fit_window()

    def frame_extra(self):
        """Height of the title bar and the borders around the window's client area."""
        user32 = ctypes.windll.user32
        try:
            if self.root.winfo_ismapped():
                from ctypes import wintypes
                rect = wintypes.RECT()
                user32.GetWindowRect(int(self.root.wm_frame(), 16), ctypes.byref(rect))
                return max(0, rect.bottom - rect.top - self.root.winfo_height())
            return user32.GetSystemMetrics(4) + 2 * (user32.GetSystemMetrics(33) + user32.GetSystemMetrics(92))
        except (AttributeError, OSError, ValueError, self.tk.TclError):
            return self.px(40)

    def schedule_fit(self):
        """Content of the Play page changed (a hint wrapped, the check card filled): fit once, when idle."""
        if not self.fit_pending:
            self.fit_pending = True
            self.root.after_idle(self.fit_window)

    def fit_window(self, set_hero=True):
        """Sizes the window to the Play page, measured: the page's requested height plus the banner, the tab
        bar and the footer. It only grows (a taller window set by hand stays), up to the work area of the
        screen. When even that is too short the banner shrinks first, down to hero_min; what still does not
        fit scrolls (the Play page is a scrolling area)."""
        self.fit_pending = False
        if self.current_page != 'play':
            return
        root, px = self.root, self.px
        root.update_idletasks()
        advanced = bool(self.var('ui_advanced', 'app').get())
        left, top, right, bottom = work_area((root.winfo_screenwidth(), root.winfo_screenheight()))
        extra = self.frame_extra()
        room = bottom - top - extra
        page = self.pages['play'].inner.winfo_reqheight() + px(8)
        fixed = self.footer.winfo_reqheight() + (self.tabbar.h if advanced else 0)
        want = self.hero_short if advanced else self.hero_tall
        hero = max(self.hero_min, min(want, room - fixed - page))
        if advanced:
            self.hero.short = hero
        elif hero != self.hero.tall:
            self.hero.tall = hero
            self.hero.rebuild()
        if set_hero and abs(self.hero.h - hero) > 1 and 'hero' not in self.motion.tweens:
            self.hero.set_height(hero)
        width, height, x, y = ((root.winfo_width(), root.winfo_height(), root.winfo_x(), root.winfo_y())
                               if root.winfo_ismapped() else self.planned)
        height = min(max(height, hero + fixed + page), room)
        y = max(top, min(y, bottom - height - extra))
        root.minsize(min(px(980), width), min(px(660), height))
        root.geometry(f'{width}x{height}+{x}+{y}')
        self.planned = (width, height, x, y)

    def cell(self, parent, row, column, title, span=1):
        """A settings field of the Play page: a small caption above, the control returned in a frame."""
        px = self.px
        holder = self.tk.Frame(parent, bg=CARD)
        holder.grid(row=row, column=column, columnspan=span, sticky='we', pady=(px(10), 0),
                    padx=(0, px(12)) if column == 0 and span == 1 else 0)
        if title:
            self.label(holder, title, 'small', MUTED).pack(anchor='w', pady=(0, px(4)))
        return holder

    def build_play(self):
        tk, px = self.tk, self.px
        page = self.scrolled_page('play', None, None)
        page.bind('<Configure>', lambda _e: self.schedule_fit(), add='+')
        page.columnconfigure(0, weight=2, uniform='play', minsize=px(300))
        page.columnconfigure(1, weight=3, uniform='play')
        info = self.card(page, _('Ready check', 'Проверка'), grid={'row': 0, 'column': 0, 'padx': (px(32), px(8)),
                                                                   'pady': px(24)})
        self.checks = {}
        for key in ('game', 'saves', 'gpu', 'fsr4', 'upscaler'):
            self.checks[key] = StatusLine(self, info)
            self.checks[key].frame.grid(row=self.next_row(info), column=0, columnspan=2, sticky='we', pady=(px(10), 0))
        holder = tk.Frame(info, bg=CARD)
        holder.grid(row=self.next_row(info), column=0, columnspan=2, sticky='w', pady=(px(14), 0))
        self.button(holder, _('Open folder', 'Открыть папку'),
                    lambda: self.open_path(self.var('user_dir', 'app').get(), 'user_dir')).pack(side='left')
        self.button(holder, _('Add to Steam', 'Добавить в Steam'), self.add_to_steam).pack(side='left', padx=(px(8), 0))
        self.note(info, _("In the game: Insert or L3+R3\nopens the port's menu.", 'В игре: Insert или L3+R3\nоткрывает меню порта.'),
                  top=14)
        # Only shown after the saved upscaler was changed; hidden last so the rows below keep their place.
        self.checks['upscaler'].frame.grid_remove()
        quick = self.card(page, _('Quick settings', 'Основное'), grid={'row': 0, 'column': 1, 'padx': (px(8), px(32)),
                                                                       'pady': px(24)})
        quick.columnconfigure(0, weight=1, uniform='quick', minsize=0)
        quick.columnconfigure(1, weight=1, uniform='quick', minsize=0)
        r = 1
        cell = self.cell(quick, r, 0, _('Game folder', 'Папка игры'), span=2)
        var = self.var('game_dir', 'app')
        holder = tk.Frame(cell, bg=CARD)
        holder.pack(fill='x')
        self.ttk.Entry(holder, textvariable=var, width=30).pack(side='left', fill='x', expand=True)
        self.button(holder, _('Browse…', 'Обзор…'), lambda: self.browse_game(var)).pack(side='left', padx=(px(8), 0))
        self.pkg_button(cell).pack(anchor='w', pady=(px(8), 0))
        for r, pairs in enumerate((((_('Output', 'Разрешение'), 'output_res', 'ini', OUTPUTS),
                                    (_('Frame rate', 'Частота кадров'), 'fps_mode', 'app', FPS_MODES)),
                                   ((_('Upscaler', 'Апскейлер'), 'upscaler', 'ini', UPSCALERS),
                                    (_('Preset', 'Пресет'), 'preset', 'ini', PRESETS)),
                                   ((_('Game language', 'Язык игры'), 'language', 'app', LANGUAGES),
                                    (_('Launcher language', 'Язык лаунчера'), 'ui_language', 'app', UI_LANGUAGES))), 2):
            for column, (title, key, store, options) in enumerate(pairs):
                holder = self.cell(quick, r, column, title)
                (self.upscaler_choice(holder, 16) if key == 'upscaler' else
                 self.choice(holder, key, store, options, 16)).pack(fill='x')
        if reshade_ready():
            self.reshade_box(self.cell(quick, 5, 0, _('ReShade look', 'Стиль ReShade')), simple=True).pack(fill='x')
            holder = self.cell(quick, 5, 1, ' ')
        else:
            holder = self.cell(quick, 5, 0, '', span=2)
        self.switch(holder, self.var('fullscreen', 'app'), _('Fullscreen', 'Полный экран')).pack(anchor='w')
        holder = self.cell(quick, 6, 0, '', span=2)
        row = self.switch(holder, self.var('frame_generation', 'ini'),
                          _('Frame generation (FSR 3.1)', 'Генерация кадров (FSR 3.1)'))
        row.pack(anchor='w')
        note = self.label(holder, '', 'small', MUTED, wraplength=px(420), justify='left')
        note.pack(anchor='w', padx=(px(54), 0), pady=(px(2), 0))
        self.grey_with_upscaler(row, note)
        generation, cap = self.var('frame_generation', 'ini'), self.var('frame_cap', 'app')

        def show_note(*_args):
            """Off: what the switch does. On: what the frame cap (set in Advanced) gives on this screen."""
            note.configure(text=simple_cap_line(cap.get(), display_refresh()) if generation.get() else
                           _('Doubles the frame rate; adds a little input lag. Best with at least 60 FPS.',
                             'Удваивает частоту кадров; добавляет немного задержки ввода. Лучше всего от 60 FPS.'))
        for var in (generation, cap):
            var.trace_add('write', show_note)
        show_note()
        for key in ('fps_mode', 'upscaler', 'output_res'):
            self.vars[key].trace_add('write', lambda *_a: self.refresh_status())

    def browse_game(self, var):
        chosen = self.filedialog.askdirectory(title=_('Choose the folder with eboot.bin', 'Выберите папку с eboot.bin'),
                                              initialdir=var.get() or str(PORT_DIR))
        if chosen:
            var.set(str(Path(chosen)))

    def refresh_hero(self):
        """Cover art, game line and the cache of both for the selected game folder."""
        folder = Path(self.var('game_dir', 'app').get() or '.')
        self.hero.set_art(folder / 'sce_sys' / 'pic1.png')
        info = game_info(folder)
        self.hero.canvas.itemconfigure(self.hero.info, text=(
            _('CUSA03173 · game version {}', 'CUSA03173 · версия игры {}').format(info[1]) if info
            else _('Game folder not set', 'Папка игры не выбрана')))

    def build_graphics(self):
        px = self.px
        page = self.scrolled_page('graphics', _('Graphics', 'Графика'),
                                  _('Stored in bbport.ini; the in-game menu (Insert or L3+R3) changes the same values.',
                                    'Хранится в bbport.ini; в игре меняется через меню (Insert или L3+R3).'))
        f = self.card(page, _('Upscaling', 'Апскейлинг'))
        self.row(f, _('Upscaler', 'Апскейлер'), self.upscaler_choice(f),
                 _("Temporal upscaling with the game's own motion vectors. FSR 4 needs its assets (below) and "
                   'a GPU with INT8 dot products; otherwise the game falls back to FSR 3.1 by itself.',
                   'Временной апскейлинг с векторами движения игры. FSR 4 нужны ассеты (ниже) и GPU с INT8; '
                   'иначе игра сама переключится на FSR 3.1.'))
        self.row(f, _('Quality preset', 'Пресет'), self.choice(f, 'preset', 'ini', PRESETS),
                 _('Render scale per axis: Quality renders at 1/1.5 of the output size.',
                   'Масштаб рендера по каждой оси: Quality рисует в 1/1.5 размера вывода.'))
        self.row(f, _('Output resolution', 'Разрешение вывода'), self.choice(f, 'output_res', 'ini', OUTPUTS),
                 _('What the upscaler produces; the HUD is drawn at this size too.',
                   'Что выдаёт апскейлер; интерфейс рисуется в этом же размере.'))
        self.row(f, _('Live resolution changes', 'Смена разрешения на лету'), self.choice(f, 'live_resolution', 'ini', LIVE),
                 _('Off: outputs other than 1080p are set by a patch at start (fastest; changing them in the '
                   'game restarts it). On: change output and preset in the game without a restart, at a cost.',
                   'Выкл.: разрешения кроме 1080p задаются патчем при запуске (быстрее). Вкл.: менять в игре '
                   'без перезапуска, но медленнее.'))
        self.check(f, 'sharpen', 'ini', _('Sharpening (RCAS)', 'Резкость (RCAS)'))
        holder = self.tk.Frame(f, bg=CARD)
        Slider(self, holder, self.var('sharpness', 'ini'), 0.0, 2.0, 300).pack(side='left')
        value = self.label(holder, '', width=5)
        value.pack(side='left', padx=px(10))
        show = lambda *_a: value.configure(text=f'{self.vars["sharpness"].get():.2f}')
        self.vars['sharpness'].trace_add('write', show)
        show()
        self.row(f, _('Sharpness', 'Сила резкости'), holder)
        experimental = self.var('experimental', 'app')
        motion_note = self.check(f, 'object_motion', 'ini', _('Object motion vectors', 'Векторы движения объектов'),
                                 object_motion_hint(experimental.get()))[1]
        experimental.trace_add('write', lambda *_a: motion_note.configure(text=object_motion_hint(experimental.get())))
        # On AMD cards object motion is its own choice, usable only with Experimental features on.
        amd_motion = self.check(f, 'object_motion_amd', 'app',
                                _('Object motion vectors on AMD cards (experimental)',
                                  'Векторы движения объектов на видеокартах AMD (экспериментально)'))[0]

        def grey_amd_motion(*_args):
            on = bool(experimental.get())
            amd_motion.knob.set_enabled(on)
            amd_motion.caption.configure(fg=TEXT if on else DIM)
        experimental.trace_add('write', grey_amd_motion)
        grey_amd_motion()
        experimental.trace_add('write', lambda *_a: self.experimental_changed())  # FSR 4.1.1 needs it
        self.grey_with_upscaler(*self.check(
            f, 'frame_generation', 'ini', _('Frame generation (FSR 3.1)', 'Генерация кадров (FSR 3.1)'),
            _('Doubles the frame rate; adds a little input lag. Best with at least 60 FPS.',
              'Удваивает частоту кадров; добавляет немного задержки ввода. Лучше всего от 60 FPS.')))
        self.build_reshade(page)
        f = self.card(page, _('FSR 4 assets', 'Ассеты FSR 4'))
        self.fsr4_label = self.label(f, '', wraplength=px(640), justify='left')
        self.fsr4_label.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(10), 0))
        holder = self.tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(12), 0))
        self.fsr4_button = self.button(holder, _('Download FSR 4 assets', 'Скачать ассеты FSR 4'), self.download_fsr4,
                                       'primary')
        self.fsr4_button.pack(side='left')
        self.fsr4_progress = Bar(self, holder, 280)
        self.fsr4_progress.pack(side='left', padx=px(16))
        self.note(f, _("From FireBurn/Q2RTX on GitHub (built from AMD's MIT-licensed FidelityFX source), "
                       'about 30 MB, into the fsr4_shaders folder of the port.',
                       'С GitHub FireBurn/Q2RTX (собраны из MIT-исходников AMD FidelityFX), около 30 МБ, '
                       'в папку fsr4_shaders порта.'), top=10)
        f = self.card(page, _('FSR 4.1.1 (experimental)', 'FSR 4.1.1 (экспериментально)'))
        self.fsr4vk_label = self.label(f, '', wraplength=px(640), justify='left')
        self.fsr4vk_label.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(10), 0))
        holder = self.tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(12), 0))
        self.fsr4vk_button = self.button(holder, _('Download FSR 4.1.1 (about 14 MB)', 'Скачать FSR 4.1.1 (около 14 МБ)'),
                                         self.start_fsr4vk_download, 'primary')
        self.fsr4vk_button.pack(side='left')
        self.fsr4vk_progress = Bar(self, holder, 280)
        self.fsr4vk_progress.pack(side='left', padx=px(16))
        self.note(f, _("Downloaded from this port's release on GitHub: fsr4vk v0.4.3 with a fix for AMD cards "
                       '(GPL-3.0; the licenses and SOURCE.txt, which links the source, are in {}). '
                       'It needs Experimental features in Advanced.',
                       'Скачивается из релиза этого порта на GitHub: fsr4vk v0.4.3 с исправлением для карт AMD '
                       '(GPL-3.0; лицензии и SOURCE.txt со ссылкой на исходники лежат в {}). '
                       'Нужны «Экспериментальные функции» в «Дополнительно».').format(fsr4vk_shown()), top=10)
        self.refresh_fsr4vk()
        f = self.card(page, _('Detail', 'Детализация'))
        self.row(f, _('Model detail (LOD)', 'Детализация моделей'), self.choice(f, 'model_lod', 'ini', LODS),
                 _('A game patch (game version 1.09).', 'Патч игры (версия 1.09).'))
        self.check(f, 'show_fps', 'ini', _('Show the FPS counter', 'Показывать FPS'))

    def build_reshade(self, page):
        px = self.px
        f = self.card(page, _('ReShade', 'ReShade'))
        if not reshade_ready():
            self.note(f, _('ReShade is not included in this build.', 'ReShade не входит в эту сборку.'), top=10)
            return
        self.check(f, 'reshade', 'app', _('Enable ReShade', 'Включить ReShade'),
                   _('Sharpening, contrast and colour filters on top of the game. Applied when the game starts; '
                     'costs a few percent of FPS.',
                     'Фильтры резкости, контраста и цвета поверх игры. Применяется при запуске игры; '
                     'стоит несколько процентов FPS.'))
        self.row(f, _('Preset', 'Пресет'), self.reshade_box(f, simple=False, width=36),
                 _("Make more in the game's ReShade menu, or copy .ini presets into the presets folder.",
                   'Новые пресеты создаются в меню ReShade в игре или копируются как .ini в папку пресетов.'))
        holder = self.tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(12), 0))
        self.button(holder, _('Open presets folder', 'Открыть папку пресетов'),
                    lambda: self.open_path(RESHADE_DIR / 'presets')).pack(side='left')
        self.note(f, _("In the game press Home to open ReShade's menu.", 'В игре нажмите Home, чтобы открыть меню ReShade.'),
                  top=10)

    def reshade_box(self, parent, simple, width=16):
        """A dropdown of the ReShade presets. The Simple one also holds Off: it is the ReShade switch and
        the preset in one."""
        on, preset = self.var('reshade', 'app'), self.reshade_preset
        box = self.ttk.Combobox(parent, state='readonly', width=width)

        def show(*_args):
            found = [(name, reshade_preset_label(name)) for name in reshade_presets()]
            box.options = ([('', _('Off', 'Выключен'))] if simple else []) + found
            current = '' if simple and not on.get() else preset.get()
            box.configure(values=[text for _value, text in box.options])
            box.set(next((text for value, text in box.options if value == current),
                         reshade_preset_label(current) if current else ''))

        def pick(_event):
            value = box.options[box.current()][0]
            if simple:
                on.set(bool(value))
            if value:
                preset.set(value)
        box.bind('<<ComboboxSelected>>', pick)
        on.trace_add('write', show)
        preset.trace_add('write', show)
        self.reshade_refreshers.append(show)
        show()
        return box

    def refresh_reshade(self):
        """The presets folder may have changed since the dropdowns were filled."""
        for show in self.reshade_refreshers:
            show()

    def build_display(self):
        page = self.scrolled_page('display', _('Display & FPS', 'Экран и FPS'),
                                  _('Applied when the game starts.', 'Применяется при запуске игры.'))
        f = self.card(page, _('Frame rate', 'Частота кадров'))
        self.version_warning(f)
        self.row(f, _('Frame rate', 'Режим'), self.choice(f, 'fps_mode', 'app', FPS_MODES),
                 _("Community patches for game version 1.09. Unlocked makes the game use the real frame time. "
                   'Other game versions always run at 30 FPS (the patches would corrupt them).',
                   'Патчи сообщества для версии 1.09. «Без ограничения» — игра использует реальное время кадра. '
                   'Другие версии всегда работают в 30 FPS.'))
        holder = self.tk.Frame(f, bg=CARD)
        self.choice(holder, 'frame_cap', 'app', FRAME_CAPS, 30).pack(anchor='w')
        hint = self.label(holder, '', 'small', MUTED, wraplength=self.px(560), justify='left')
        hint.pack(anchor='w', pady=(self.px(6), 0))
        generation, cap = self.var('frame_generation', 'ini'), self.var('frame_cap', 'app')

        def show_hint(*_args):
            hint.configure(text=advanced_cap_hint(cap.get(), generation.get(), display_refresh()))
        for var in (generation, cap):
            var.trace_add('write', show_hint)
        show_hint()
        self.row(f, _('Frame cap (unlocked mode)', 'Ограничение FPS (режим без ограничения)'), holder,
                 _("Above about 120 FPS the game's movement timing breaks (running and rolling get slower, "
                   'physics and animations can glitch): higher caps are at your own risk.',
                   'Выше ~120 FPS ломается тайминг движения игры (бег и перекаты замедляются, возможны '
                   'сбои физики и анимаций): более высокие значения — на ваш риск.'))
        self.row(f, _('Frames ahead of the GPU', 'Кадров впереди GPU'), self.choice(f, 'frames_ahead', 'app', FRAMES_AHEAD),
                 _('1 keeps frame pacing even; more can raise FPS when the graphics card is the limit.',
                   '1 — ровная подача кадров; больше может поднять FPS, если упирается в видеокарту.'))
        f = self.card(page, _('Window', 'Окно'))
        self.check(f, 'fullscreen', 'app', _('Fullscreen', 'Полноэкранный режим'))
        self.row(f, _('Presentation', 'Режим показа кадров'), self.choice(f, 'present_mode', 'app', PRESENT_MODES))
        self.check(f, 'hdr', 'app', _('Allow HDR output', 'Разрешить HDR'),
                   _('When HDR is on in Windows and the display supports it.',
                     'Если HDR включён в Windows и монитор его поддерживает.'))
        # The Monitor row lives in a frame of its own (empty, so it takes no room, until a PC with several
        # monitors is detected); apply_displays fills it once and only refreshes it on later GPU checks.
        self.monitor_frame = self.tk.Frame(f, bg=CARD)
        self.monitor_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we')
        self.monitor_frame.columnconfigure(0, minsize=self.px(210))  # as the card's own columns
        self.monitor_frame.columnconfigure(1, weight=1)
        self.monitor_note = None

    def build_controls(self):
        """Controls (README: "Controls > Controller"): the gamepad the game uses (BB_GAMEPAD) and the keyboard keys
        and gamepad buttons of every input, written as the key.* / pad.* lines of bbport.ini that runtime_pad.c reads."""
        px, tk = self.px, self.tk
        page = self.scrolled_page('controls', _('Controls', 'Управление'),
                                  _('Applied when the game starts.', 'Применяется при запуске игры.'))
        f = self.card(page, _('Controller', 'Контроллер'))
        holder = tk.Frame(f, bg=CARD)
        self.gamepad_box = self.ttk.Combobox(holder, state='readonly', width=48)
        self.gamepad_box.pack(side='left')
        self.gamepad_box.bind('<<ComboboxSelected>>', lambda _e: self.gamepad_picked())
        self.button(holder, _('Refresh', 'Обновить'), self.refresh_gamepads).pack(side='left', padx=(px(8), 0))
        r = self.next_row(f)
        self.row(f, _('Controller', 'Контроллер'), holder)
        self.gamepad_note = self.label(f, '', 'small', MUTED, wraplength=px(560), justify='left')
        self.gamepad_note.grid(row=r + 1, column=1, sticky='w', pady=(px(4), 0))
        self.gamepad_values = ['']  # the value of each entry of the dropdown
        self.apply_gamepads(None)

        f = self.card(page, _('Button icons', 'Значки кнопок'))
        self.row(f, _('Button icons', 'Значки кнопок'), self.choice(f, 'button_icons', 'ini', bbport_controls.ICON_CHOICES))
        self.note(f, _('The buttons the game shows on screen. Automatic follows the controller connected when the game '
                       'starts: Xbox icons for an Xbox or other controller, PlayStation icons for a PlayStation one, '
                       'your keys and mouse buttons when none is connected. Changing controllers in the game keeps '
                       'the icons until the next start. A mod with its own menu/common.tpf.dcx keeps priority.',
                       'Кнопки, которые игра показывает на экране. «Автоматически» следует за контроллером, '
                       'подключённым при запуске игры: значки Xbox для контроллера Xbox или другого, значки '
                       'PlayStation для контроллера PlayStation, ваши клавиши и кнопки мыши, если контроллера нет. '
                       'Смена контроллера в игре сохраняет значки до следующего запуска. Мод со своим '
                       'menu/common.tpf.dcx имеет приоритет.'), top=4)

        f = self.card(page, _('Mouse', 'Мышь'))
        self.check(f, 'mouse_camera', 'ini', _('Mouse controls the camera', 'Мышь управляет камерой'),
                   _('While the game window has focus, the mouse turns the camera and its buttons and wheel can be '
                     'bound below. Insert (the port menu) or Alt+Tab lets go of the mouse.',
                     'Пока окно игры в фокусе, мышь поворачивает камеру, а её кнопки и колесо можно назначить ниже. '
                     'Insert (меню порта) или Alt+Tab освобождают мышь.'))
        holder = tk.Frame(f, bg=CARD)
        Slider(self, holder, self.var('mouse_sensitivity', 'ini'), 0.1, 5.0, 300).pack(side='left')
        value = self.label(holder, '', width=5)
        value.pack(side='left', padx=px(10))
        show = lambda *_a: value.configure(text=self.mouse_sensitivity_text())
        self.vars['mouse_sensitivity'].trace_add('write', show)
        show()
        self.row(f, _('Mouse sensitivity', 'Чувствительность мыши'), holder)
        self.check(f, 'mouse_invert_y', 'ini', _('Invert vertical look', 'Инвертировать вертикаль'))
        self.check(f, 'mouse_no_auto_rotation', 'ini',
                   _('Camera turns only by the mouse while walking', 'Камера поворачивается только мышью при ходьбе'),
                   _('The game stops turning the camera by itself as the character walks. Leave it off to keep the '
                     'gamepad camera as it was.',
                     'Игра перестаёт сама поворачивать камеру при ходьбе. Оставьте выключенным, чтобы камера '
                     'геймпада работала как раньше.'))

        f = self.card(page, _('Button assignments', 'Назначение кнопок'))
        self.controls_hint = _('Press Change, then a key, a mouse button or the wheel. Add gives the input a second '
                               'one. Applied when the game starts.',
                               'Нажмите «Изменить», затем клавишу, кнопку мыши или колесо. «Добавить» назначает входу '
                               'второе. Применяется при запуске игры.')
        self.controls_message = self.label(f, self.controls_hint, 'small', MUTED, wraplength=px(680), justify='left')
        self.controls_message.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(8), 0))
        self.controls_after = None
        self.button(f, _('Dark Souls III layout', 'Раскладка Dark Souls III'), self.apply_ds3_layout).grid(
            row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(12), 0))
        self.note(f, _('Fills the keyboard and mouse bindings with the Dark Souls III layout: WASD to move, the mouse '
                       'to look, left button R1, Shift + left button R2, right button L1, Left Ctrl L2, Space dodge, '
                       'E interact. Gamepad bindings are not changed.',
                       'Заполняет назначения клавиатуры и мыши раскладкой Dark Souls III: WASD — движение, мышь — '
                       'камера, левая кнопка R1, Shift + левая кнопка R2, правая кнопка L1, левый Ctrl L2, пробел — '
                       'уклонение, E — действие. Назначения геймпада не меняются.'), top=4)
        grid = tk.Frame(f, bg=CARD)  # one grid for every row, so the columns line up
        grid.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(px(4), 0))
        self.label(grid, _('Keyboard', 'Клавиатура'), 'small', MUTED).grid(row=0, column=1, columnspan=3, sticky='w')
        self.label(grid, _('Gamepad', 'Геймпад'), 'small', MUTED).grid(row=0, column=4, sticky='w', padx=(px(16), 0))
        none_text = _('Not assigned', 'Не назначено')
        for index, (name, russian, _keys, pad) in enumerate(bbport_controls.CONTROLS, 1):
            top = (px(8), 0)
            self.label(grid, _(bbport_controls.LABELS[name], russian), wraplength=px(190), justify='left').grid(
                row=index, column=0, sticky='w', padx=(0, px(16)), pady=top)
            row = self.control_rows[name] = {}
            row['keys'] = self.label(grid, '', width=22, anchor='w')
            row['keys'].grid(row=index, column=1, sticky='w', pady=top)
            row['change'] = self.button(grid, _('Change', 'Изменить'), lambda n=name: self.start_capture(n, False))
            row['change'].grid(row=index, column=2, padx=(px(8), 0), pady=top)
            row['add'] = self.button(grid, _('Add', 'Добавить'), lambda n=name: self.start_capture(n, True))
            row['add'].grid(row=index, column=3, padx=(px(8), 0), pady=top)
            if pad is not None:  # the movement and camera keys have no gamepad side
                row['pad'] = self.ttk.Combobox(grid, state='readonly', width=24)
                row['pad'].grid(row=index, column=4, padx=(px(16), 0), pady=top)
                row['pad'].bind('<<ComboboxSelected>>', lambda _e, n=name: self.pad_picked(n))
            row['reset'] = self.button(grid, _('Reset', 'Сбросить'), lambda n=name: self.reset_control(n))
            row['reset'].grid(row=index, column=5, padx=(px(16), 0), pady=top)
            self.show_control(name)
        self.button(f, _('Reset all', 'Сбросить всё'), self.reset_all_controls).grid(
            row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(16), 0))
        self.root.bind('<KeyPress>', self.control_key, add='+')
        self.root.bind('<KeyRelease>', self.control_key_release, add='+')
        self.root.bind('<ButtonPress>', self.control_mouse, add='+')
        self.root.bind('<MouseWheel>', self.control_mouse, add='+')

    def apply_gamepads(self, pads):
        """The controller dropdown, from the gamepads SDL listed (None: not asked yet; False: the list could not be read)."""
        self.gamepads = pads
        saved, name = self.var('gamepad', 'app').get(), self.var('gamepad_name', 'app').get()
        options = gamepad_options(pads or [], saved, name)
        self.gamepad_values = [value for value, _text in options]
        self.gamepad_box.configure(values=[text for _value, text in options])
        self.gamepad_box.current(self.gamepad_values.index(saved) if saved in self.gamepad_values else 0)
        self.gamepad_note.configure(text=_(
            'The list of controllers could not be read; Automatic uses the first one.',
            'Не удалось прочитать список контроллеров; «Авто» берёт первый.') if pads is False else _(
            'The chosen controller is used as soon as it connects.',
            'Выбранный контроллер используется, как только подключится.'))

    def refresh_gamepads(self):
        if self.listing_gamepads:
            return
        self.listing_gamepads = True

        def work():
            try:
                pads = list_gamepads()
            except Exception:  # whatever the tool raises, the flag must be cleared, or the list never refreshes
                pads = None

            def done():
                self.listing_gamepads = False
                self.apply_gamepads(False if pads is None else pads)
            self.ui_calls.put(done)
        threading.Thread(target=work, daemon=True).start()

    def gamepad_picked(self):
        value = self.gamepad_values[self.gamepad_box.current()]
        name, previous = self.var('gamepad_name', 'app'), self.var('gamepad', 'app')
        name.set(dict(self.gamepads or []).get(value) or (name.get() if value == previous.get() else ''))
        previous.set(value)

    def show_control(self, name):
        """Shows what is in force for an input: the saved line or the default, with the default marked."""
        row = self.control_rows[name]
        parts = bbport_controls.current_binding(self.ini, 'key', name)
        text = bbport_controls.join_binding(parts) or _('Not assigned', 'Не назначено')
        custom = f'key.{name}' in self.ini
        row['keys'].configure(text=text if custom else _('{} (default)', '{} (по умолчанию)').format(text),
                              fg=TEXT if custom else MUTED)
        if 'pad' in row:
            value = bbport_controls.join_binding(bbport_controls.current_binding(self.ini, 'pad', name))
            values = [''] + [button for button, _label in bbport_controls.PAD_BUTTONS]
            if value not in values:  # several buttons, as the touchpad's Back + Touchpad
                values.append(value)
            row['pad_values'] = values
            row['pad'].configure(values=[bbport_controls.pad_text(v) or _('Not assigned', 'Не назначено')
                                         for v in values])
            row['pad'].current(values.index(value))

    def write_control(self, kind, name, parts):
        """Sets key.<name> / pad.<name>; the default is no line at all."""
        key = f'{kind}.{name}'
        if parts == bbport_controls.split_binding(bbport_controls.default_binding(kind, name)):
            self.ini.pop(key, None)
        else:
            self.ini[key] = bbport_controls.join_binding(parts)
        self.show_control(name)

    def pad_picked(self, name):
        row = self.control_rows[name]
        self.write_control('pad', name, bbport_controls.split_binding(row['pad_values'][row['pad'].current()]))

    def reset_control(self, name):
        self.stop_capture()
        for kind in ('key', 'pad'):
            self.ini.pop(f'{kind}.{name}', None)
        self.show_control(name)

    def reset_all_controls(self):
        for name in self.control_rows:
            self.reset_control(name)

    def say_controls(self, text, color=MUTED):
        """A message in place of the hint under the card; the hint is back after a few seconds."""
        if self.controls_after:
            self.root.after_cancel(self.controls_after)
        self.controls_message.configure(text=text, fg=color)
        self.controls_after = self.root.after(4000, lambda: self.controls_message.configure(
            text=self.controls_hint, fg=MUTED))

    def start_capture(self, name, add):
        """Change / Add: the next key pressed is taken (the button turns into Cancel; pressing it again cancels)."""
        again = self.capturing == (name, add)
        self.stop_capture()
        if again:
            return
        row = self.control_rows[name]
        row['add' if add else 'change'].configure(text=_('Cancel', 'Отмена'))
        row['keys'].configure(text=_('Press a key…', 'Нажмите клавишу…'), fg=GOLD)
        self.capturing = (name, add)
        self.pending_modifier = None
        self.side_buttons_down = {button for vk, button in SIDE_BUTTONS if bbport_controls.windows_key_down(vk)}
        self.say_controls(_('Press a key, a mouse button or the wheel; hold Shift, Ctrl or Alt first for a combination. '
                            'For the left button, click the highlighted text.',
                            'Нажмите клавишу, кнопку мыши или крутите колесо; для сочетания сначала зажмите Shift, '
                            'Ctrl или Alt. Для левой кнопки щёлкните по выделенному тексту.'))
        self.root.focus_set()  # the key goes to the window, not to a button that would act on Space
        self.side_poll = self.root.after(50, self.poll_side_buttons)

    def stop_capture(self):
        if not self.capturing:
            return
        if self.side_poll:  # one poll chain per capture: a restart within 50 ms must not leave the old one running
            self.root.after_cancel(self.side_poll)
            self.side_poll = None
        name, _add = self.capturing
        self.capturing = self.pending_modifier = None
        row = self.control_rows[name]
        row['change'].configure(text=_('Change', 'Изменить'))
        row['add'].configure(text=_('Add', 'Добавить'))
        self.show_control(name)

    def control_key(self, event):
        """The key pressed while an input waits for one."""
        if not self.capturing:
            return None
        name, add = self.capturing
        key = bbport_controls.tk_key_to_sdl(event.keysym, event.keycode, bbport_controls.windows_scan,
                                           bbport_controls.windows_key_down)
        if key is None:
            self.say_controls(_("Insert opens the port's menu in the game; choose another key.",
                                'Insert открывает меню порта в игре; выберите другую клавишу.')
                              if event.keysym == 'Insert' else
                              _('That key cannot be used in the game. Press another one.',
                                'Игра не может использовать эту клавишу. Нажмите другую.'), BAD)
            return 'break'
        if bbport_controls.is_modifier_keysym(event.keysym):
            self.pending_modifier = key  # bound when let go; a mouse input first makes it the combination's modifier
            return 'break'
        self.assign_key(name, add, key)
        return 'break'

    def control_key_release(self, event):
        """A Shift, Ctrl or Alt let go with nothing pressed meanwhile: that key is the binding."""
        if self.capturing and self.pending_modifier and bbport_controls.is_modifier_keysym(event.keysym):
            name, add = self.capturing
            self.assign_key(name, add, self.pending_modifier)
            return 'break'
        return None

    def control_mouse(self, event):
        """A mouse button or wheel step while an input waits. The left button counts only when it hits the text of
        the waiting row (it also presses the page's own buttons); the others and the wheel count anywhere."""
        if not self.capturing:
            return None
        name, add = self.capturing
        if event.type == self.tk.EventType.MouseWheel:
            button = bbport_controls.wheel_to_sdl(event.delta)
        else:
            button = bbport_controls.mouse_button_to_sdl(event.num)
            if button == 'Mouse Left' and event.widget is not self.control_rows[name]['keys']:
                return None
        if button is None:
            return None
        self.assign_key(name, add, bbport_controls.with_modifiers(
            button, *bbport_controls.held_modifiers(bbport_controls.windows_key_down)))
        return 'break'

    def poll_side_buttons(self):
        """Tk 8.6 reports no side buttons: while an input waits, the mouse's own state is read for them."""
        if not self.capturing:
            return
        down = {button for vk, button in SIDE_BUTTONS if bbport_controls.windows_key_down(vk)}
        pressed = down - self.side_buttons_down
        self.side_buttons_down = down
        if pressed and self.root.focus_displayof() is not None:
            name, add = self.capturing
            self.assign_key(name, add, bbport_controls.with_modifiers(
                sorted(pressed)[0], *bbport_controls.held_modifiers(bbport_controls.windows_key_down)))
            return
        self.side_poll = self.root.after(50, self.poll_side_buttons)

    def assign_key(self, name, add, key):
        """Gives the waiting input the captured key, button or wheel step (Add keeps the ones it has)."""
        parts = bbport_controls.current_binding(self.ini, 'key', name) if add else []
        if key not in parts:
            if len(parts) >= bbport_controls.MAX_BIND:
                self.say_controls(_('At most {} keys per input.', 'Не больше {} клавиш на вход.').format(
                    bbport_controls.MAX_BIND), BAD)
            else:
                self.write_control('key', name, parts + [key])
        self.stop_capture()

    def apply_ds3_layout(self):
        """Dark Souls III layout: after a confirmation every keyboard and mouse binding is replaced, the gamepad's stay."""
        self.stop_capture()
        if not self.messagebox.askokcancel('Bloodborne', _(
                'Replace your keyboard and mouse bindings with the Dark Souls III layout? Gamepad bindings are not '
                'changed.', 'Заменить ваши назначения клавиатуры и мыши раскладкой Dark Souls III? Назначения '
                'геймпада не изменятся.')):
            return
        self.ini = bbport_controls.apply_ds3(self.ini)
        for name in self.control_rows:
            self.show_control(name)
        self.say_controls(_('Dark Souls III layout applied. Reset all brings the defaults back.',
                            'Раскладка Dark Souls III применена. «Сбросить всё» возвращает стандартные назначения.'))

    def mouse_sensitivity_text(self):
        try:
            return f'{self.vars["mouse_sensitivity"].get():.2f}'
        except self.tk.TclError:
            return ''

    def build_game(self):
        page = self.scrolled_page('game', _('Game & effects', 'Игра и эффекты'),
                                  _('Your game dump, saves and the game patches.', 'Дамп игры, сохранения и патчи игры.'))
        f = self.card(page, _('Game', 'Игра'))
        self.folder(f, 'game_dir', _('Game folder', 'Папка игры'),
                    _('Choose the folder with eboot.bin', 'Выберите папку с eboot.bin'),
                    _('Your own dump of CUSA03173 (eboot.bin, sce_module, sce_sys, dvdroot_ps4); version 1.09 '
                      'for the community patches.', 'Ваш дамп CUSA03173 (eboot.bin, sce_module, sce_sys, '
                      'dvdroot_ps4); версия 1.09 для патчей сообщества.'), on_change=self.game_changed)
        self.row(f, _('PlayStation 4 packages', 'Пакеты PlayStation 4'), self.pkg_button(f),
                 _('Pick the game .pkg, the v1.09 update and the DLC. They are extracted for you and the game folder '
                   'is filled in.', 'Выберите .pkg игры, обновление 1.09 и DLC. Они распакуются сами, а папка игры '
                   'заполнится.'))
        self.row(f, _('DLC (The Old Hunters)', 'DLC (The Old Hunters)'), self.dlc_field(f),
                 _('Reported to the game as an installed add-on. The Old Hunters areas ship with the v1.09 data; '
                   'install the DLC .pkg to switch it on.', 'Передаётся игре как установленное дополнение. Области '
                   'The Old Hunters входят в данные 1.09; установите .pkg с DLC, чтобы включить.'))
        self.folder(f, 'user_dir', _('Saves folder', 'Папка сохранений'),
                    _('Choose the saves folder', 'Выберите папку сохранений'),
                    _('Empty: {} (shader caches are kept there too).',
                      'Пусто: {} (там же кэш шейдеров).').format(short_path(DATA_DIR / 'user')), on_change=self.refresh_status)
        self.row(f, _('Game language', 'Язык игры'), self.choice(f, 'language', 'app', LANGUAGES))
        self.row(f, _('Player name', 'Имя игрока'), self.ttk.Entry(f, textvariable=self.var('player_name', 'app'), width=30),
                 _('Where the game shows the PSN name; empty: the default.', 'Где игра показывает имя PSN; пусто — по умолчанию.'))
        f = self.card(page, _('Effects', 'Эффекты'))
        self.version_warning(f)
        for key, title, _on in EFFECTS:
            self.check(f, key, 'ini', _(*title))
        f = self.card(page, _('Extras', 'Дополнительно'))
        for key, title, _on in EXTRAS:
            self.check(f, key, 'ini', _(*title))
        self.note(f, _('Effects and extras are game patches for version 1.09, applied at start.',
                       'Эффекты и дополнения — патчи игры для версии 1.09, применяются при запуске.'))

    def build_cheats(self):
        page = self.scrolled_page('cheats', _('Cheats', 'Читы'),
                                  _('Game patches for version 1.09, applied at start. Leave them off for a normal '
                                    'play-through.', 'Патчи игры для версии 1.09, применяются при запуске. Для обычного '
                                    'прохождения оставьте их выключенными.'))
        f = self.card(page, _('Cheats', 'Читы'))
        self.version_warning(f)
        for key, title, _on in CHEATS:
            self.check(f, key, 'ini', _(*title))
        f = self.card(page, _('Gameplay tweaks', 'Изменения игрового процесса'))
        for key, title, _on in TWEAKS:
            self.check(f, key, 'ini', _(*title))
        # Enemy control and the free camera share their buttons: one at a time.
        control, camera = self.var('cheat_enemy_control', 'ini'), self.var('debug_camera', 'ini')
        control.trace_add('write', lambda *_a: control.get() and camera.set(False))
        camera.trace_add('write', lambda *_a: camera.get() and control.set(False))

    def build_mods(self):
        px = self.px
        page = self.scrolled_page('mods', _('Mods & patches', 'Моды и патчи'),
                                  _('The game files are never changed: mods are layered over them at start.',
                                    'Файлы игры не меняются: моды накладываются при запуске.'))
        f = self.card(page, _('Mods', 'Моды'))
        self.check(f, 'mods_enabled', 'app', _('Load mods', 'Загружать моды'),
                   _('Loose-file mods, each in its own folder (with dvdroot_ps4, or chr\\, parts\\ … directly).',
                     'Моды из файлов, каждый в своей папке (с dvdroot_ps4 или chr\\, parts\\ … напрямую).'))
        self.folder(f, 'mods_dir', _('Mods folder', 'Папка модов'), _('Choose the mods folder', 'Выберите папку модов'),
                    _('Empty: {}', 'Пусто: {}').format(short_path(DATA_DIR / 'mods')), on_change=self.refresh_lists)
        self.mods_frame = self.tk.Frame(f, bg=CARD)
        self.mods_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(px(12), 0))
        f = self.card(page, _('Third-party patches', 'Сторонние патчи'))
        self.folder(f, 'patches_dir', _('Patches folder', 'Папка патчей'), _('Choose the patches folder', 'Выберите папку патчей'),
                    _('shadPS4/GoldHEN XML patch files for version 1.09. Empty: {}',
                      'XML-патчи shadPS4/GoldHEN для версии 1.09. Пусто: {}').format(short_path(DATA_DIR / 'patches')),
                    on_change=self.refresh_lists)
        self.patches_frame = self.tk.Frame(f, bg=CARD)
        self.patches_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(px(12), 0))
        self.button(f, _('Refresh', 'Обновить'), self.refresh_lists).grid(
            row=self.next_row(f), column=0, sticky='w', pady=(px(16), 0))

    def build_advanced(self):
        px, tk = self.px, self.tk
        page = self.scrolled_page('advanced', _('Advanced', 'Дополнительно'),
                                  _('Launcher options, performance switches and diagnostics.',
                                    'Настройки лаунчера, производительность и диагностика.'))
        f = self.card(page, _('Launcher', 'Лаунчер'))
        self.row(f, _('Launcher language', 'Язык лаунчера'), self.choice(f, 'ui_language', 'app', UI_LANGUAGES),
                 _('Applies when the launcher opens again.', 'Применится при следующем открытии лаунчера.'))
        self.check(f, 'close_on_play', 'app', _('Close the launcher when the game starts', 'Закрывать лаунчер при запуске игры'))
        self.check(f, 'check_updates', 'app', _('Check for updates when the launcher opens',
                                                'Проверять обновления при открытии лаунчера'))
        self.check(f, 'beta_versions', 'app', _('Beta versions', 'Бета-версии'),
                   _('Also offer beta releases when checking for updates. They are tested less than stable ones.',
                     'Предлагать и бета-версии при проверке обновлений. Они проверены меньше, чем стабильные.'))
        self.check(f, 'animations', 'app', _('Animations', 'Анимации'))
        self.vars['animations'].trace_add('write', lambda *_a: self.animations_changed())
        holder = tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(16), 0))
        self.button(holder, _('Desktop shortcut', 'Ярлык на рабочем столе'), self.shortcut).pack(side='left')
        self.button(holder, _('Port folder', 'Папка порта'), lambda: self.open_path(DATA_DIR)).pack(side='left', padx=px(8))
        self.button(holder, 'bbport.ini', lambda: self.open_path(ini_path())).pack(side='left')
        holder = tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(10), 0))
        self.button(holder, _('Check for updates', 'Проверить обновления'),
                    lambda: threading.Thread(target=self.check_update, daemon=True,
                                             args=(True, bool(self.var('beta_versions', 'app').get()))).start()
                    ).pack(side='left')
        self.label(holder, f'v{VERSION}', 'small', MUTED).pack(side='left', padx=px(12))
        holder = tk.Frame(f, bg=CARD)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(px(10), 0))
        self.button(holder, _('Clear shader cache', 'Очистить кэш шейдеров'), self.clear_cache).pack(side='left')
        self.label(holder, _('If the game only shows a black screen, this usually helps.',
                             'Если игра показывает только чёрный экран, обычно это помогает.'), 'small', MUTED).pack(
            side='left', padx=px(12))
        f = self.card(page, _('Performance', 'Производительность'))
        self.check(f, 'experimental', 'app', _('Experimental features', 'Экспериментальные функции'),
                   _('Unlocks options that are still being tested and marked (experimental). For now: FSR 4.1.1, and '
                     'object motion vectors on AMD graphics cards, which can lower the frame rate or draw some objects wrong.',
                     'Открывает функции, которые ещё проверяются и помечены (экспериментально). Пока это FSR 4.1.1 и '
                     'векторы движения объектов на видеокартах AMD: они могут снижать FPS или неправильно рисовать объекты.'))
        self.row(f, _('Two-stage GPU pipeline', 'Двухстадийный конвейер GPU'), self.choice(f, 'draw_pipe', 'app', DRAW_PIPE),
                 _('20–30% faster; switch it off if the game is unstable.', 'Быстрее на 20–30%; при нестабильности выключите.'))
        self.row(f, _('GPU readbacks', 'Чтение данных GPU'), self.choice(f, 'readbacks', 'app', READBACKS),
                 _('How exactly data the GPU writes is copied back for the game.',
                   'Насколько точно данные, записанные GPU, возвращаются игре.'))
        f = self.card(page, _('Diagnostics', 'Для разработчика'))
        self.check(f, 'frame_stats', 'app', _('Frame statistics in the log (every 5 s)', 'Статистика кадров в журнале (раз в 5 с)'))
        self.check(f, 'gpu_profile', 'app', _('GPU time per pass in the log', 'Профиль GPU в журнале'))
        self.check(f, 'vk_validation', 'app', _('Vulkan validation layers (needs the Vulkan SDK; much slower)',
                                                'Слои валидации Vulkan (нужен Vulkan SDK; сильно замедляет)'))
        self.row(f, _('Extra variables', 'Доп. переменные'), self.ttk.Entry(f, textvariable=self.var('extra_env', 'app'), width=58),
                 _('NAME=value pairs separated by spaces (README lists them).', 'Пары ИМЯ=значение через пробел (список в README).'),
                 fill=True)

    def animations_changed(self):
        """The Animations switch: motion stops at once, or starts again."""
        on = bool(self.vars['animations'].get()) and os.environ.get('BB_LAUNCHER_ANIMATIONS', '1') != '0'
        self.motion.set_enabled(on)
        self.hero.animate(on)
        self.play_button.animate(on)

    def build_log(self):
        tk, px = self.tk, self.px
        page = tk.Frame(self.content, bg=PANEL)
        self.pages['log'] = page
        top = tk.Frame(page, bg=PANEL)
        top.pack(fill='x', padx=px(32), pady=(px(18), px(10)))
        self.label(top, _('Log', 'Журнал'), 'h1', bg=PANEL).pack(side='left')
        self.button(top, _('Copy', 'Копировать'), self.copy_log, bg=PANEL).pack(side='right')
        self.button(top, _('Clear', 'Очистить'), lambda: self.set_log(''), bg=PANEL).pack(side='right', padx=px(8))
        frame = tk.Frame(page, bg=FIELD, highlightthickness=1, highlightbackground=LINE)
        frame.pack(fill='both', expand=True, padx=px(32), pady=(0, px(20)))
        self.log = tk.Text(frame, wrap='none', bg=FIELD, fg='#cfc6b8', insertbackground=TEXT, relief='flat',
                           font=self.fonts['mono'], padx=px(10), pady=px(8), state='disabled', highlightthickness=0,
                           selectbackground=BLOOD)
        bar = self.ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)

    def version_warning(self, parent):
        """A banner shown while the selected game is not version 1.09 (refresh_status)."""
        px = self.px
        label = self.tk.Label(parent, bg='#2a1d12', fg='#e3b25a', font=self.fonts['small'], padx=px(12), pady=px(8),
                              wraplength=px(640), justify='left', text=_(
            'Your game is version {}: these options are patches for 1.09 and are not applied; the game runs '
            'at 30 FPS. Update the dump to 1.09 to use them.',
            'Ваша игра версии {}: эти настройки — патчи для 1.09 и не применяются; игра работает в 30 FPS. '
            'Обновите дамп до 1.09, чтобы их использовать.'))
        label.grid(row=self.next_row(parent), column=0, columnspan=2, sticky='we', pady=(px(12), 0))
        label.template = label.cget('text')
        self.warnings = getattr(self, 'warnings', []) + [label]

    # ---- state -------------------------------------------------------------------------------
    def game_changed(self):
        self.refresh_hero()
        self.set_icon()
        self.refresh_status()

    def refresh_status(self):
        info = game_info(self.var('game_dir', 'app').get())
        if not info:
            game = _('✗ No eboot.bin in the game folder (Game & effects)', '✗ В папке игры нет eboot.bin («Игра и эффекты»)')
        elif info[1] == PATCH_VERSION:
            game = _('✓ Game version {}: every patch available', '✓ Версия игры {}: доступны все патчи').format(info[1])
        else:
            game = _('⚠ Game version {}: runs at 30 FPS without the community patches (they are for 1.09)',
                     '⚠ Версия игры {}: 30 FPS без патчей сообщества (они для 1.09)').format(info[1])
        user = Path(self.var('user_dir', 'app').get() or DATA_DIR / 'user')
        saves = list((user / 'savedata').glob('*/*/SPRJ*')) if (user / 'savedata').is_dir() else []
        save = (_('✓ Saves found in {}', '✓ Найдены сохранения в {}').format(short_path(user)) if saves
                else _('• No saves yet: the game creates them in {}', '• Сохранений пока нет: игра создаст их в {}').format(short_path(user)))
        missing = fsr4_missing()
        fsr4 = (_('✓ FSR 4 assets installed', '✓ Ассеты FSR 4 установлены') if not missing else
                _('• FSR 4 assets missing (Graphics); FSR 3.1 is used meanwhile',
                  '• Нет ассетов FSR 4 («Графика»); пока используется FSR 3.1'))
        for key, text in (('game', game), ('saves', save), ('gpu', self.gpu_text), ('fsr4', fsr4)):
            self.checks[key].set(text, busy=key == 'gpu' and self.gpu_checking)
        if self.upscaler_notice:
            self.checks['upscaler'].set(self.upscaler_notice)
            self.checks['upscaler'].frame.grid()
        for label in getattr(self, 'warnings', []):
            if info and info[1] != PATCH_VERSION:
                label.configure(text=label.template.format(info[1]))
                label.grid()
            else:
                label.grid_remove()
        if not self.process and not self.installing:
            self.play_button.configure(state='normal' if info else 'disabled')
            self.status.configure(text=self.summary() if info else _('Choose the game folder first.',
                                                                     'Сначала выберите папку игры.'), fg=MUTED)

    def summary(self):
        fps = dict(FPS_MODES).get(self.var('fps_mode', 'app').get(), ('?',))
        info = game_info(self.var('game_dir', 'app').get())
        if info and info[1] != PATCH_VERSION:
            fps = ('30 FPS',)
        upscaler = dict(UPSCALERS).get(self.var('upscaler', 'ini').get(), ('?',))[0].split(' (')[0]
        output = self.var('output_res', 'ini').get().replace('x', ' × ')
        return f'{_(*fps)}   ·   {upscaler}   ·   {output}'

    def check_upscalers(self, tool=None):
        """What each upscaler needs of this PC (bb-gpu-capabilities --upscalers); unknown (every upscaler
        offered) when this fails. TOOL is gpu_tool()'s answer when the caller has it already. Run it off
        the window's thread."""
        exe, env = tool or gpu_tool()
        try:
            check = subprocess.run([str(exe), '--upscalers'], capture_output=True, text=True, timeout=30,
                                   env=env, creationflags=NO_WINDOW)
            self.upscaler_support = parse_upscaler_support(check.stdout) or None
        except (OSError, subprocess.TimeoutExpired):
            pass

    def detect_gpu(self):
        tool = gpu_tool()
        exe, env = tool
        text = _('• Graphics card: not checked', '• Видеокарта: не проверена')
        self.check_upscalers(tool)
        try:
            result = subprocess.run([str(exe), '--live-resolution'], capture_output=True, text=True, timeout=30,
                                    env=env, creationflags=NO_WINDOW)
            names = [line[5:].split(':')[0] for line in result.stderr.splitlines() if line.startswith('GPU: ')]
            if names:
                text = _('✓ Graphics card: {}', '✓ Видеокарта: {}').format(names[0])
            elif result.returncode:
                text = _('✗ No Vulkan 1.3 graphics card found (update the driver)',
                         '✗ Не найдена видеокарта с Vulkan 1.3 (обновите драйвер)')
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.gpu_text, self.gpu_checking = text, False
        self.ui_calls.put(self.apply_upscaler_support)
        self.ui_calls.put(self.refresh_status)
        connected_displays()  # asked here, off the window's thread; the picker is filled from the cache
        self.ui_calls.put(self.apply_displays)

    def apply_displays(self):
        """The monitor picker of Display & FPS: shown only when the PC has more than one monitor."""
        shown = connected_displays()
        if len(shown) < 2:
            return
        saved = str(self.var('monitor', 'app').get())
        names = [monitor['name'] for monitor in shown]
        note = (_('The saved monitor "{}" is not connected; the primary monitor is used.',
                  'Сохранённый монитор «{}» не подключён; используется основной.').format(saved)
                if saved and saved not in names else None)
        # The game takes the first monitor whose name contains the saved one: twins of one model share an entry.
        options = [('', ('Primary monitor', 'Основной монитор'))] + [
            (monitor['name'], (display_label(monitor),)) for index, monitor in enumerate(shown)
            if monitor['name'] not in names[:index]]
        hint = note or _('Which monitor the game opens on. The primary monitor is used when the chosen one is '
                         'not connected.', 'На каком мониторе откроется игра. Если выбранный не подключён, '
                         'используется основной.')
        if self.monitor_note is not None:  # a later GPU check: the row is there, only its note can change
            self.monitor_note.configure(text=hint)
            return
        self.row(self.monitor_frame, _('Monitor', 'Монитор'), self.choice(self.monitor_frame, 'monitor', 'app', options))
        self.monitor_note = self.label(self.monitor_frame, hint, 'small', MUTED, wraplength=self.px(560), justify='left')
        self.monitor_note.grid(row=1, column=1, sticky='w', pady=(self.px(4), 0))

    def refresh_fsr4(self):
        total, missing = len(fsr4_files()), len(fsr4_missing())
        self.fsr4_progress.set((total - missing) / total)
        self.fsr4_label.configure(
            text=_('Installed: {} files in {}.', 'Установлены: {} файлов в {}.').format(total, PORT_DIR / 'fsr4_shaders')
            if not missing else _('{} of {} files missing in {}.', 'Нет {} из {} файлов в {}.').format(
                missing, total, PORT_DIR / 'fsr4_shaders'))
        self.fsr4_button.configure(state='normal' if missing and not self.downloading else 'disabled')
        for box in self.upscaler_boxes:  # FSR 4 becomes selectable once its assets are there
            box.refresh()

    def download_fsr4(self):
        self.downloading = True
        self.fsr4_button.configure(state='disabled')
        folder = PORT_DIR / 'fsr4_shaders'

        def work():
            error = None
            try:
                folder.mkdir(parents=True, exist_ok=True)
                for name in fsr4_missing():
                    part = folder / (name + '.part')
                    with urllib.request.urlopen(f'{FSR4_BASE}/{name}', timeout=60) as response:
                        part.write_bytes(response.read())
                    part.replace(folder / name)
                    self.ui_calls.put(self.refresh_fsr4)
            except OSError as failure:
                error = failure

            def done():
                self.downloading = False
                self.refresh_fsr4()
                self.refresh_status()
                if error:
                    self.messagebox.showerror('FSR 4', _('Download failed: {}', 'Не удалось скачать: {}').format(error))
            self.ui_calls.put(done)
        threading.Thread(target=work, daemon=True).start()

    def experimental_changed(self):
        """The Experimental features switch moved: the dropdown and the FSR 4.1.1 card follow at once. When
        it went on and the last GPU check said FSR 4.1.1 cannot run, that check is asked again in the
        background (a verdict from another moment must not keep the entry gray until a restart)."""
        self.apply_upscaler_support()
        self.refresh_fsr4vk()
        verdict = (self.upscaler_support or {}).get('fsr411')
        if not (self.var('experimental', 'app').get() and verdict and not verdict[0]) or self.rechecking:
            return
        self.rechecking = True

        def work():
            try:
                self.check_upscalers()
            finally:
                self.ui_calls.put(self.apply_upscaler_support)
                self.rechecking = False  # after the apply is queued: a watcher never sees both down
        threading.Thread(target=work, daemon=True).start()

    def refresh_fsr4vk(self):
        """The FSR 4.1.1 card: what is downloaded, and the button (only while Experimental features is on)."""
        if not hasattr(self, 'fsr4vk_button'):  # the Graphics page builds the card; a switch can change before it exists
            return
        ready, problem = fsr4vk_present(), fsr4vk_download_problem()
        outdated = fsr4vk_outdated(wait=False) if ready else False
        if outdated is None:  # files not hashed yet: hash them off this thread, then draw the card again
            outdated = False
            if not self.fsr4vk_hashing:
                self.fsr4vk_hashing = True

                def hash_files():
                    global FSR4VK_VERDICT
                    try:
                        fsr4vk_outdated()
                    except Exception:  # files that cannot be read (a locked DLL): not outdated, and not hashed again
                        FSR4VK_VERDICT = (fsr4vk_stamp(), True)
                    finally:
                        self.fsr4vk_hashing = False
                        self.ui_calls.put(self.refresh_fsr4vk)
                threading.Thread(target=hash_files, daemon=True).start()
        self.fsr4vk_label.configure(text=_('An update is available that makes FSR 4.1.1 work on AMD cards. '
                                           'The version in {} is used until you update.',
                                           'Доступно обновление, с которым FSR 4.1.1 работает на картах AMD. '
                                           'Пока вы не обновите, используется версия из {}.').format(fsr4vk_shown())
                                    if outdated
                                    else _('Installed in {}.', 'Установлено в {}.').format(fsr4vk_shown()) if ready
                                    else _('The download is not available in this build: {}',
                                           'В этой сборке скачивание недоступно: {}').format(problem) if problem
                                    else _('Not downloaded.', 'Не скачано.'))
        if not self.downloading_fsr4vk:
            self.fsr4vk_progress.set(1.0 if ready and not outdated else 0.0)
        usable = ((outdated or not ready) and not problem and not self.downloading_fsr4vk
                  and bool(self.var('experimental', 'app').get()))
        self.fsr4vk_button.configure(
            text=_('Update FSR 4.1.1', 'Обновить FSR 4.1.1') if outdated
            else _('Download FSR 4.1.1 (about 14 MB)', 'Скачать FSR 4.1.1 (около 14 МБ)'),
            state='normal' if usable else 'disabled')

    def start_fsr4vk_download(self):
        """Downloads the fsr4vk files in the background, then re-runs the capability check so the
        dropdown entry can be picked."""
        if self.process and fsr4vk_present():  # an update: the running game has the old DLL loaded, and Windows refuses the swap
            self.messagebox.showinfo('FSR 4.1.1', _('Close the game before updating.', 'Закройте игру перед обновлением.'))
            return
        self.downloading_fsr4vk = True
        self.refresh_fsr4vk()

        def progress(done, total):
            if total > 0:  # a server that sends no length has nothing to show
                self.ui_calls.put(lambda: self.fsr4vk_progress.set(done / total))

        def work():
            try:
                error = download_fsr4vk(progress)
                if not error:
                    self.check_upscalers()
            except Exception as failure:  # nothing may end this thread before done() is queued
                error = f'{type(failure).__name__}: {failure}'

            def done():
                self.downloading_fsr4vk = False
                self.refresh_fsr4vk()
                self.apply_upscaler_support()
                if error:
                    self.messagebox.showerror('FSR 4.1.1', _('Download failed: {}', 'Не удалось скачать: {}').format(error))
            self.ui_calls.put(done)
        threading.Thread(target=work, daemon=True).start()

    def refresh_lists(self):
        if not hasattr(self, 'patches_frame'):
            return
        tk, px = self.tk, self.px
        from mods import discover
        from patches import external_patches
        for holder in (self.mods_frame, self.patches_frame):
            for widget in holder.winfo_children():
                widget.destroy()
        root = Path(self.var('mods_dir', 'app').get() or DATA_DIR / 'mods')
        profile = load_json(DATA_DIR / 'mods.json', {})
        available = discover(root)
        if set(self.mod_order) != set(available):
            known = [n for n in profile.get('order', []) if n in available]
            self.mod_order = known + [n for n in available if n not in known]
        old = {n: v.get() for n, v in self.mod_vars.items()}
        self.mod_vars = {}
        if not available:
            self.label(self.mods_frame, _('No mods in {} yet.', 'В {} пока нет модов.').format(short_path(root)), 'small', MUTED).pack(
                anchor='w')
        for index, name in enumerate(self.mod_order):
            line = tk.Frame(self.mods_frame, bg=CARD)
            line.pack(fill='x', pady=px(3))
            var = tk.BooleanVar(value=old.get(name, name not in profile.get('disabled', [])))
            self.mod_vars[name] = var
            self.button(line, '▲', lambda i=index: self.move_mod(i, -1), width=px(32)).pack(side='left')
            self.button(line, '▼', lambda i=index: self.move_mod(i, 1), width=px(32)).pack(side='left', padx=(px(4), px(14)))
            self.switch(line, var, f'{index + 1}.  {name}').pack(side='left')
        if len(self.mod_order) > 1:
            self.label(self.mods_frame, _('Lower in the list loads later and wins conflicts.',
                                          'Ниже в списке — загружается позже и перекрывает.'), 'small', MUTED).pack(
                anchor='w', pady=(px(6), 0))
        chosen = load_json(DATA_DIR / 'patches.json', {})
        found = external_patches(Path(self.var('patches_dir', 'app').get() or DATA_DIR / 'patches'), PATCH_VERSION)
        old = {n: v.get() for n, v in self.patch_vars.items()}
        self.patch_vars = {}
        if not found:
            self.label(self.patches_frame, _('No patch files for version 1.09 yet.', 'Пока нет патчей для версии 1.09.'),
                       'small', MUTED).pack(anchor='w')
        for key, _path, meta in found:
            on = key in chosen.get('enabled', []) or (key not in chosen.get('disabled', []) and
                                                      meta.get('isEnabled', 'false').lower() == 'true')
            var = tk.BooleanVar(value=old.get(key, on))
            self.patch_vars[key] = var
            author = meta.get('Author')
            self.switch(self.patches_frame, var, key + (f'  —  {author}' if author else '')).pack(anchor='w', pady=px(3))

    def move_mod(self, index, step):
        other = index + step
        if 0 <= other < len(self.mod_order):
            self.mod_order[index], self.mod_order[other] = self.mod_order[other], self.mod_order[index]
            self.refresh_lists()

    def collect(self):
        """Writes settings.json, bbport.ini, mods.json and patches.json."""
        for key, var in self.vars.items():
            try:
                value = var.get()
            except self.tk.TclError:  # an unfinished number in a spinbox
                value = APP_DEFAULTS.get(key, INI_DEFAULTS.get(key))
            if var.store == 'ini':
                self.ini[key] = ('1' if value else '0') if key in INI_FLAGS else \
                    f'{float(value):.2f}' if key in INI_FLOATS else str(value)
            else:
                self.app[key] = value
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.app, indent=2, ensure_ascii=False), encoding='utf-8')
        save_ini(ini_values(self.ini), self.ini_lines)
        self.ini, self.ini_lines = load_ini()
        preset = self.reshade_preset.get()
        if reshade_ready() and preset and preset != self.reshade_started:  # not undoing a change made in the game
            set_reshade_preset(preset)
            self.reshade_started = preset
        if self.mod_order:
            (DATA_DIR / 'mods.json').write_text(json.dumps(
                {'order': self.mod_order, 'disabled': [n for n, v in self.mod_vars.items() if not v.get()]},
                indent=2), encoding='utf-8')
        if self.patch_vars:
            chosen = load_json(DATA_DIR / 'patches.json', {})
            shown = set(self.patch_vars)
            enabled = {k for k in chosen.get('enabled', []) if k not in shown} | {k for k, v in self.patch_vars.items() if v.get()}
            disabled = {k for k in chosen.get('disabled', []) if k not in shown} | {k for k, v in self.patch_vars.items() if not v.get()}
            (DATA_DIR / 'patches.json').write_text(json.dumps(
                {'enabled': sorted(enabled), 'disabled': sorted(disabled)}, indent=2), encoding='utf-8')

    # ---- game process ------------------------------------------------------------------------
    def play(self):
        if self.process or self.installing:
            return
        self.collect()
        if not game_info(self.app['game_dir']):
            self.messagebox.showerror('Bloodborne', _('Choose the game folder with eboot.bin (CUSA03173).',
                                                      'Выберите папку игры с eboot.bin (CUSA03173).'))
            self.show('game' if self.var('ui_advanced', 'app').get() else 'play')
            return
        self.set_log('')
        log_path = Path(self.app.get('user_dir') or DATA_DIR / 'user') / 'last_run.log'
        try:
            self.process = start_game(run_command(), game_environment(self.app), log_path)
        except OSError as error:
            self.append(_('Could not start: {}', 'Не удалось запустить: {}').format(error) + '\n')
            self.status.configure(text=_('Could not start: {}', 'Не удалось запустить: {}').format(error), fg=BAD)
            self.process = None
            return
        self.job = GameJob(self.process)
        threading.Thread(target=self.read_output, args=(self.process, self.job, log_path), daemon=True).start()
        self.set_running(True, preparing=True)
        self.status.configure(text=_('Preparing the game; it opens in its own window…',
                                     'Подготовка игры; она откроется в своём окне…'), fg=GOLD)

    def read_output(self, process, job, log_path):
        # The game writes last_run.log itself (start_game), so a crash report survives the launcher
        # being closed and scripts (amd-motion-test) find it in one place; this only follows the file.
        code = -1
        try:
            if job is not None:  # game_over accepts no job; nothing before the finally may raise
                job.say = self.output.put
            try:
                follow_log(log_path, lambda: game_over(process, job), self.output.put)
            except OSError as error:  # the log cannot be read: say so, and still wait for the game to end
                self.output.put('\n' + _('— could not follow the game log: {} —', '— не удалось читать журнал игры: {} —'
                                         ).format(error) + '\n')
                while not game_over(process, job):
                    time.sleep(0.2)
            code = process.wait()
            try:
                with open(log_path, 'a', encoding='utf-8') as log:
                    log.write(f'\n-- the game exited (code {code}) --\n')
            except OSError:
                pass
        finally:
            self.output.put((code,))  # the window learns the game ended whatever went wrong above

    def drain_output(self):
        while not self.ui_calls.empty():
            self.ui_calls.get_nowait()()
        try:
            for _i in range(500):
                item = self.output.get_nowait()
                if isinstance(item, tuple):
                    self.append(_('\n— the game exited (code {}) —\n', '\n— игра завершилась (код {}) —\n').format(item[0]))
                    self.process = None
                    if self.job:
                        self.job.close()
                    self.set_running(False)
                    self.refresh_status()
                else:
                    if game_is_up(item):  # mods, patches and shaders are prepared; the window exists
                        self.set_running(True)
                        self.status.configure(text=_('The game is running.', 'Игра запущена.'), fg=GOLD)
                        if self.app.get('close_on_play') and not self.close_scheduled:  # both GAME_UP_LINES arrive
                            self.close_scheduled = True
                            self.root.after(CLOSE_AFTER_UP_MS, self.root.destroy)  # the game keeps running
                    elif 'restarting through run.py' in item:
                        self.set_running(True, preparing=True)
                        self.status.configure(text=_('Restarting with the new settings…', 'Перезапуск с новыми настройками…'), fg=GOLD)
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_output)

    def stop(self):
        if self.installing:  # Stop also ends a package installation
            self.install_cancel.set()
            if self.install_proc and self.install_proc.poll() is None:
                self.install_proc.kill()
        elif self.job:
            self.job.terminate()

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end'))

    def open_path(self, path, key=None):
        if key == 'user_dir' and not path:
            path = DATA_DIR / 'user'
        elif key == 'mods_dir' and not path:
            path = DATA_DIR / 'mods'
        elif key == 'patches_dir' and not path:
            path = DATA_DIR / 'patches'
        path = Path(path or DATA_DIR)
        if path.suffix == '.ini':
            self.collect()
        elif not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    # ---- install from PKG ------------------------------------------------------------------------
    def pkg_button(self, parent):
        return self.button(parent, _('Install from PKG…', 'Установить из PKG…'), self.install_from_pkg)

    def dlc_field(self, parent):
        """The DLC entitlement label that goes to the game as BB_ADDCONT, and a button to forget it."""
        holder = self.tk.Frame(parent, bg=CARD)
        var = self.var('addcont', 'app')
        text = self.label(holder, '')
        text.pack(side='left')
        clear = self.button(holder, _('Clear', 'Сбросить'), lambda: var.set(''))
        clear.pack(side='left', padx=(self.px(14), 0))

        def show(*_args):
            label = var.get().strip()
            text.configure(text=label or _('Not installed', 'Не установлено'), fg=GOLD if label else DIM)
            clear.configure(state='normal' if label else 'disabled')
        var.trace_add('write', show)
        show()
        return holder

    def add_addcont(self, label):
        var = self.var('addcont', 'app')
        labels = [item for item in var.get().replace(' ', '').split(',') if item]
        if label not in labels:
            var.set(','.join([*labels, label][:8]))  # the game reads at most 8 labels

    def install_from_pkg(self):
        """The button: opens the install window (choose files or scan a folder, pick the destination, Install)."""
        if self.installing:
            self.messagebox.showinfo('Bloodborne', _('An installation is already running.',
                                                     'Установка уже выполняется.'))
            return
        if self.process:
            self.messagebox.showinfo('Bloodborne', _('Close the game before installing.', 'Закройте игру перед установкой.'))
            return
        if self.pkg_dialog and self.pkg_dialog.winfo_exists():
            self.pkg_dialog.lift()
            return
        self.build_pkg_dialog()

    def pkg_start_dir(self):
        """Where the file and folder dialogs open: the last folder used, else Downloads, else the home folder."""
        for folder in (self.app.get('pkg_src'), Path.home() / 'Downloads', Path.home()):
            if folder and Path(folder).is_dir():
                return str(folder)

    def build_pkg_dialog(self):
        """One window for the whole flow: choose files or scan a folder, see what each file is (ticked: the
        best game, the highest update, every DLC), pick the destination, Install."""
        tk, px = self.tk, self.px
        win = self.pkg_dialog = tk.Toplevel(self.root)
        win.title(_('Install from PKG', 'Установка из PKG'))
        win.configure(bg=PANEL)
        win.transient(self.root)
        body = tk.Frame(win, bg=PANEL)
        body.pack(fill='both', expand=True, padx=px(26), pady=px(22))
        self.label(body, _('Install from PKG', 'Установка из PKG'), 'h2', bg=PANEL).pack(anchor='w')
        self.label(body, _('Choose the game .pkg, the update and the DLC, or scan a folder. The best files are ticked '
                           'for you.', 'Выберите .pkg игры, обновление и DLC или просканируйте папку. Лучшие файлы '
                           'отмечаются сами.'), 'small', MUTED, PANEL, wraplength=px(620), justify='left').pack(
            anchor='w', pady=(px(4), px(14)))
        buttons = tk.Frame(body, bg=PANEL)
        buttons.pack(anchor='w')
        listing = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        listing.pack(fill='x', pady=(px(14), 0))
        note = self.label(body, '', 'small', MUTED, PANEL, wraplength=px(620), justify='left')
        note.pack(anchor='w', pady=(px(10), 0))
        where = tk.Frame(body, bg=PANEL)
        where.pack(fill='x', pady=(px(14), 0))
        self.label(where, _('Install into', 'Установить в'), 'body', MUTED, PANEL).pack(side='left', padx=(0, px(12)))
        dest = tk.StringVar(value=str(Path(self.app.get('pkg_dir') or PORT_DIR / 'game')))
        self.ttk.Entry(where, textvariable=dest, width=48).pack(side='left', fill='x', expand=True)
        rows, ticks = [], {}

        def selected():
            return [row['info'] for row in rows if row['info'] and ticks[row['path']].get()]

        def refresh(*_args):
            chosen, text, colour, ready = selected(), '', MUTED, False
            if rows and not any(row['info'] for row in rows):
                text, colour = _('Tick the packages to install.', 'Отметьте пакеты для установки.'), BAD
            elif rows and not chosen:
                text = _('Tick the packages to install.', 'Отметьте пакеты для установки.')
            elif chosen:
                try:
                    _sorted, warnings = check_pkgs(chosen)
                    lines = []
                    if any(info['category'] != 'ac' for info in chosen):
                        need = int(sum(info['size'] for info in chosen) * PKG_SPACE_FACTOR)
                        if not dest.get().strip():
                            raise PkgError(_('Choose where to install the game', 'Выберите, куда установить игру'))
                        free = free_space(dest.get())
                        if free is not None and free < need:
                            raise PkgError(_('Not enough free space on {}: about {} needed, {} free.',
                                             'Недостаточно места на {}: нужно около {}, свободно {}.').format(
                                Path(dest.get()).anchor or dest.get(), size_text(need), size_text(free)))
                        lines.append(_('Needs about {} free; {} is free. The game takes 15 to 30 minutes to extract and '
                                       'the launcher stays usable.', 'Нужно около {} свободного места, свободно {}. '
                                       'Распаковка игры занимает 15–30 минут, лаунчер остаётся доступным.').format(
                            size_text(need), size_text(free or 0)))
                    text, ready = '\n'.join(lines + [f'⚠ {warning}' for warning in warnings]), True
                    colour = CAUTION if warnings else MUTED
                except PkgError as problem:
                    text, colour = f'✗ {problem}', BAD
            note.configure(text=text, fg=colour)
            install.configure(state='normal' if ready else 'disabled')

        def fill(found, where_from=None):
            rows[:] = read_candidates(found)
            for widget in listing.winfo_children():
                widget.destroy()
            ticks.clear()
            first = preselect(rows)
            for row in rows:
                ticks[row['path']] = var = tk.BooleanVar(value=row['path'] in first)
                var.trace_add('write', refresh)
            for row in rows[:10]:
                line = tk.Frame(listing, bg=CARD)
                line.pack(fill='x', padx=px(14), pady=px(6))
                if row['info']:
                    Switch(self, line, ticks[row['path']]).pack(side='left')
                else:
                    self.label(line, '✗', 'body_bold', BAD).pack(side='left', padx=(px(6), px(8)))
                text = tk.Frame(line, bg=CARD)
                text.pack(side='left', padx=(px(12), 0), fill='x', expand=True)
                self.label(text, row['path'].name, 'body').pack(anchor='w')
                detail = (f"{pkg_description(row['info'])}  ·  {size_text(row['info']['size'])}" if row['info']
                          else row['error'])
                if where_from and row['path'].parent != where_from:
                    detail += f"  ·  {short_path(row['path'].parent, 60)}"
                self.label(text, detail, 'small', MUTED if row['info'] else BAD, wraplength=px(520), justify='left').pack(
                    anchor='w')
            if len(rows) > 10:
                self.label(listing, _('{} more files are not shown.', 'Ещё файлов не показано: {}.').format(len(rows) - 10),
                           'small', MUTED).pack(anchor='w', padx=px(14), pady=(0, px(8)))
            refresh()

        def choose():
            picked = self.filedialog.askopenfilenames(
                parent=win, title=_('Choose the Bloodborne .pkg files', 'Выберите .pkg файлы Bloodborne'),
                initialdir=self.pkg_start_dir(),
                filetypes=[(_('PlayStation 4 packages', 'Пакеты PlayStation 4'), '*.pkg'), (_('All files', 'Все файлы'), '*.*')])
            if picked:
                self.app['pkg_src'] = str(Path(picked[0]).parent)
                fill([Path(path) for path in picked])

        def scan():
            folder = self.filedialog.askdirectory(parent=win, initialdir=self.pkg_start_dir(), title=_(
                'Choose the folder to scan for .pkg files', 'Выберите папку для поиска .pkg файлов'))
            if not folder:
                return
            self.app['pkg_src'] = str(Path(folder))
            found = scan_pkg_files(folder)
            fill(found, Path(folder))
            if not found:
                note.configure(text=_('No .pkg files found in {}.', 'В {} нет .pkg файлов.').format(Path(folder)), fg=BAD)

        def browse():
            start = Path(dest.get() or PORT_DIR)
            while not start.is_dir() and start != start.parent:
                start = start.parent
            chosen = self.filedialog.askdirectory(parent=win, initialdir=str(start), title=_(
                'Choose where to install the game', 'Выберите, куда установить игру'))
            if chosen:
                dest.set(str(Path(chosen)))

        def go():
            infos, _warnings = check_pkgs(selected())
            folder = Path(dest.get().strip()) if any(info['category'] != 'ac' for info in infos) else None
            win.destroy()
            self.start_install(infos, folder)

        self.button(where, _('Browse…', 'Обзор…'), browse, bg=PANEL).pack(side='left', padx=(px(8), 0))
        dest.trace_add('write', refresh)
        choose_button = self.button(buttons, _('Choose PKG files…', 'Выбрать файлы PKG…'), choose, 'primary', bg=PANEL)
        choose_button.pack(side='left')
        scan_button = self.button(buttons, _('Scan a folder…', 'Найти в папке…'), scan, bg=PANEL)
        scan_button.pack(side='left', padx=(px(8), 0))
        foot = tk.Frame(body, bg=PANEL)
        foot.pack(fill='x', pady=(px(18), 0))
        install = self.button(foot, _('Install', 'Установить'), go, 'primary', bg=PANEL)
        install.pack(side='right')
        install.configure(state='disabled')
        self.button(foot, _('Cancel', 'Отмена'), win.destroy, bg=PANEL).pack(side='right', padx=(0, px(8)))
        win.bind('<Escape>', lambda _e: win.destroy())
        win.parts = {'choose': choose_button, 'scan': scan_button, 'install': install, 'rows': rows, 'ticks': ticks, 'dest': dest}
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry(f'+{x}+{y}')
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def start_install(self, infos, dest):
        """Checks PkgTool and the free space, then extracts on a helper thread."""
        steps = [info for info in infos if info['category'] != 'ac']
        tool = None
        if steps:
            tool = pkgtool_path()
            if not tool:
                self.messagebox.showerror('Bloodborne', _('PkgTool.exe was not found. Unpack the whole package again.',
                                                          'PkgTool.exe не найден. Распакуйте архив целиком заново.'))
                return
            need = int(sum(info['size'] for info in infos) * PKG_SPACE_FACTOR)
            try:
                dest = Path(dest)
                dest.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(dest).free
            except OSError as failure:
                self.messagebox.showerror('Bloodborne', str(failure))
                return
            if free < need:
                self.messagebox.showerror('Bloodborne', _('Not enough free space on {}: about {} needed, {} free.',
                                                          'Недостаточно места на {}: нужно около {}, свободно {}.').format(
                    dest.anchor or dest, size_text(need), size_text(free)))
                return
        self.installing = True
        self.install_cancel.clear()
        if steps:
            self.app['pkg_dir'] = str(dest)
        self.set_running(True, preparing=True)
        self.status.configure(text=_('Installing from PKG… {}%', 'Установка из PKG… {}%').format(0), fg=GOLD)
        self.show_install_card(_('Installing from PKG', 'Установка из PKG'))
        threading.Thread(target=self.install_work, args=(infos, dest, tool), daemon=True).start()

    def install_work(self, infos, dest, tool):
        """Helper thread: PkgTool for the game and then the update (into the same folder, the update over the
        game), the package's param.sfo, and the DLC labels. Everything the window shows goes through ui_calls."""
        ui = self.ui_calls.put
        steps = [info for info in infos if info['category'] != 'ac']
        game, error, number = None, None, 0
        try:
            if steps:
                total, free_at_start = max(1, sum(info['size'] for info in steps)), shutil.disk_usage(dest).free

                def progress(name):
                    done = max(0.0, min(0.99, (free_at_start - shutil.disk_usage(dest).free) / total))
                    ui(lambda: self.install_progress(done, name))
            for info in infos:
                name = info['path'].name
                if info['category'] == 'ac':
                    ui(lambda label=info['label']: (self.add_addcont(label), self.append(
                        _('DLC license {} saved.', 'Лицензия DLC {} сохранена.').format(label) + '\n')))
                    continue
                number += 1
                ui(lambda n=name, i=number: self.install_step(
                    _('Extracting {} ({} of {})…', 'Распаковка {} ({} из {})…').format(n, i, len(steps)),
                    _('Extracting {} to {}', 'Распаковка {} в {}').format(n, dest)))
                code, tail = self.run_pkgtool(tool, info['path'], dest, progress)
                if self.install_cancel.is_set():
                    raise PkgError(_('Installation stopped.', 'Установка остановлена.'))
                if code != 0:
                    text = error_lines(tail)
                    lines = [_('PkgTool stopped (code {}).', 'PkgTool остановился (код {}).').format(code)]
                    if re.search(r'passcode|\bkeys?\b|superblock|magic|crypt|signature', '\n'.join(tail), re.I):
                        lines.append(_('This package looks encrypted or is not supported by PkgTool, so it cannot be '
                                       'installed here. Try another copy of the game or an already extracted game folder.',
                                       'Пакет похож на зашифрованный или не поддерживается PkgTool, поэтому установить '
                                       'его здесь нельзя. Попробуйте другую копию игры или уже распакованную папку игры.'))
                    raise PkgError('\n'.join(lines + [text]))
                game = find_game_folder(dest)
                if not game:
                    raise PkgError(_('Extraction finished, but there is no eboot.bin in {}.',
                                     'Распаковка завершена, но в {} нет eboot.bin.').format(dest))
                if write_param_sfo(game, info):
                    ui(lambda v=info['app_ver'], g=game: self.append(
                        _('param.sfo (version {}) written to {}', 'param.sfo (версия {}) записан в {}').format(v, g) + '\n'))
        except (PkgError, OSError) as failure:
            error = str(failure)
        ui(lambda: self.install_done(game, error))

    def run_pkgtool(self, tool, pkg, dest, progress):
        """PkgTool pkg_extract; returns (exit code, its last lines that are not file names). Calls
        progress(current file) twice a second while it runs; kills it when the installation is cancelled."""
        process = subprocess.Popen([str(tool), 'pkg_extract', '--verbose', str(pkg), str(dest)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        self.install_proc = process
        tail, current, encoding = collections.deque(maxlen=40), [''], console_encoding()

        def pump():
            for raw in iter(process.stdout.readline, b''):
                line = raw.decode(encoding, errors='replace').rstrip()
                if ' -> ' in line:  # --verbose prints "<path in the package> -> <extracted file>"
                    current[0] = line.split(' -> ')[0]
                elif line:
                    tail.append(line)
                    self.ui_calls.put(lambda text=line: self.append(f'  {text}\n'))
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        while process.poll() is None:
            if self.install_cancel.is_set():
                process.kill()
                break
            progress(current[0])
            time.sleep(0.5)
        code = process.wait()
        reader.join(5)
        return code, list(tail)

    def show_install_card(self, title, detail=''):
        """The card above the footer that follows the installation (it also shows how it ended)."""
        tk, px = self.tk, self.px
        if self.install_hide:
            self.root.after_cancel(self.install_hide)
            self.install_hide = None
        if not self.install_card:
            self.install_card = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=BLOOD)
            body = tk.Frame(self.install_card, bg=CARD)
            body.pack(padx=px(20), pady=px(14))
            head = tk.Frame(body, bg=CARD)
            head.pack(fill='x')
            self.install_title = self.label(head, '', 'body_bold')
            self.install_title.pack(side='left')
            self.install_spin = Spinner(self, head, 16, CARD)
            self.install_detail = self.label(body, '', 'small', MUTED, wraplength=px(520), justify='left', anchor='w')
            self.install_detail.pack(fill='x', pady=(px(6), px(10)))
            self.install_bar = Bar(self, body, 520)
            self.install_close = self.button(body, _('Close', 'Закрыть'), self.hide_install_card)
        self.install_close.pack_forget()
        self.install_card.configure(highlightbackground=BLOOD)
        self.install_title.configure(text=title, fg=TEXT)
        self.install_detail.configure(text=detail)
        self.install_spin.pack(side='right', padx=(px(16), 0))
        self.install_spin.start()
        self.install_bar.frac = self.install_bar.shown = 0.0
        self.install_bar.draw(0.0)
        self.install_bar.pack(anchor='w')
        self.install_card.place(relx=0.5, rely=1.0, y=-px(104), anchor='s')
        self.install_card.lift()

    def hide_install_card(self):
        if self.install_card:
            self.install_card.place_forget()

    def install_step(self, text, log):
        self.install_step_text = text
        self.install_detail.configure(text=text)
        self.append(log + '\n')

    def install_progress(self, fraction, name):
        if not self.installing:
            return
        self.install_bar.set(fraction)
        self.install_detail.configure(text=f'{self.install_step_text}\n{name[-70:]}' if name else self.install_step_text)
        self.status.configure(text=_('Installing from PKG… {}%', 'Установка из PKG… {}%').format(int(fraction * 100)), fg=GOLD)

    def install_done(self, game, error):
        """On the Tk thread when the helper thread ends: the game folder is set (and the ready check
        refreshed) after a success; a failure keeps PkgTool's last lines in the card and the log."""
        self.installing, self.install_proc = False, None
        self.set_running(False)
        if error:
            title, detail, ok = _('Installation failed', 'Установка не удалась'), error, False
            self.append(f'{title}: {error}\n')
        else:
            if game:
                self.var('game_dir', 'app').set(str(game))
                title, detail = _('Game ready', 'Игра готова'), _('{} is now the game folder.', '{} теперь папка игры.').format(game)
            else:
                title = _('Installation finished', 'Установка завершена')
                detail = _('DLC license {} saved.', 'Лицензия DLC {} сохранена.').format(self.var('addcont', 'app').get())
            ok = True
            self.append(f'{title}: {detail}\n')
            self.collect()
        self.install_bar.set(1.0 if ok else self.install_bar.frac)
        self.install_spin.stop()
        self.install_spin.pack_forget()
        self.install_bar.pack_forget()
        self.install_title.configure(text=('✓ ' if ok else '✗ ') + title, fg=OK if ok else BAD)
        self.install_detail.configure(text=detail)
        self.install_card.configure(highlightbackground=OK if ok else BAD)
        self.install_close.pack(anchor='e', pady=(self.px(10), 0))
        if ok:
            self.install_hide = self.root.after(15000, self.hide_install_card)
        self.refresh_status()

    # ---- updates -------------------------------------------------------------------------------
    def check_update(self, manual=False, beta=None):
        """Helper thread: asks GitHub for the newest release (a pre-release only with the Beta versions
        switch on; BETA is its value, read on the window's thread) and offers it when it is newer."""
        try:
            release = fetch_update_release(self.app.get('beta_versions', False) if beta is None else beta)
        except (OSError, ValueError, KeyError) as failure:
            if manual:
                self.ui_calls.put(lambda error=failure: self.messagebox.showerror(
                    'Bloodborne', _('Could not check for updates: {}', 'Не удалось проверить обновления: {}').format(error)))
            return

        def show():
            if release and version_tuple(release[0]) > version_tuple(VERSION):
                self.offer_update(*release)
            elif manual:
                self.messagebox.showinfo('Bloodborne', _('You have the latest version ({}).',
                                                         'У вас последняя версия ({}).').format(VERSION))
        self.ui_calls.put(show)

    def offer_update(self, version, url, page):
        """A card in the banner (it slides in below the view switch) offering the new release."""
        if self.update_box:
            return
        tk, px = self.tk, self.px
        text = _('Version {} is available.', 'Доступна версия {}.').format(version)
        box = tk.Frame(self.hero.canvas, bg=CARD, highlightthickness=1, highlightbackground=BLOOD)
        row = tk.Frame(box, bg=CARD)
        row.pack(padx=px(14), pady=px(8))
        self.update_label = self.label(row, text, 'body_bold')
        self.update_label.pack(side='left')
        self.update_bar = Bar(self, row, 120)
        self.update_button = self.button(row, _('Update', 'Обновить'), lambda: self.install_update(version, url, page),
                                         'primary')
        self.update_button.pack(side='left', padx=(px(14), 0))
        self.button(row, _("What's new", 'Что нового'), lambda: webbrowser.open(page)).pack(side='left', padx=(px(8), 0))
        box.place(relx=1.0, x=-px(24), y=-px(80), anchor='ne')
        self.motion.tween('update-card', 380, -px(80), px(62), lambda y: box.place_configure(y=int(y)))
        self.update_box = box
        if not self.process:
            self.status.configure(text=text, fg=GOLD)

    def install_update(self, version, url, page):
        if self.process:
            self.messagebox.showinfo('Bloodborne', _('Close the game before updating.', 'Закройте игру перед обновлением.'))
            return
        if not FROZEN or not url:  # a source tree updates with git
            webbrowser.open(page)
            return
        if not self.messagebox.askyesno('Bloodborne', _(
                'Install version {} now? The launcher closes, installs it and opens again. Saves and settings are kept.',
                'Установить версию {} сейчас? Лаунчер закроется, установит её и откроется снова. Сохранения и '
                'настройки останутся.').format(version)):
            return
        self.update_button.configure(state='disabled')
        self.update_bar.pack(side='left', padx=(self.px(12), 0), before=self.update_button.canvas)

        def progress(percent):
            self.update_label.configure(text=_('Downloading version {}… {}%', 'Загрузка версии {}… {}%').format(
                version, percent))
            self.update_bar.set(percent / 100)

        def work():
            try:
                shutil.rmtree(UPDATE_DIR, ignore_errors=True)
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                archive = UPDATE_DIR / 'update.zip'
                request = urllib.request.Request(url, headers={'User-Agent': 'bbport-launcher'})
                with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
                    total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        percent = done * 100 // total if total else 0
                        if percent != shown:
                            shown = percent
                            self.ui_calls.put(lambda p=percent: progress(p))
                with zipfile.ZipFile(archive) as package:
                    package.extractall(UPDATE_DIR / 'new')
                archive.unlink()
                new = next((p.parent for p in (UPDATE_DIR / 'new').rglob('BLauncher.exe')), None)
                if not new:
                    raise OSError('BLauncher.exe is missing from the download')
                # The new launcher copies itself over this installation once this one has closed.
                subprocess.Popen([str(new / 'BLauncher.exe'), '--install-update', str(PORT_DIR), str(os.getpid())],
                                 cwd=str(new), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                self.ui_calls.put(self.root.destroy)
            except (OSError, zipfile.BadZipFile) as failure:
                def failed(error=failure):
                    self.update_button.configure(state='normal')
                    self.update_bar.pack_forget()
                    self.update_label.configure(text=_('Version {} is available.', 'Доступна версия {}.').format(version))
                    self.messagebox.showerror('Bloodborne', _('Update failed: {}', 'Не удалось обновить: {}').format(error))
                self.ui_calls.put(failed)
        threading.Thread(target=work, daemon=True).start()

    def clear_cache(self):
        """Shader and pipeline caches; they are rebuilt while playing."""
        if self.process:
            return
        import shutil
        cache = Path(self.app['user_dir'] or DATA_DIR / 'user') / 'cache'
        shutil.rmtree(cache, ignore_errors=True)
        self.messagebox.showinfo('Bloodborne', _('Shader cache cleared. The next start stutters for a few minutes while '
                                                 'it is rebuilt.', 'Кэш шейдеров очищен. Следующий запуск несколько '
                                                 'минут будет подтормаживать, пока кэш собирается заново.'))

    def shortcut(self):
        """Bloodborne.lnk on the desktop: Bloodborne.exe, which starts the game (or this launcher
        while no game folder is chosen)."""
        if FROZEN:
            target, arguments, icon = str(PORT_DIR / 'Bloodborne.exe'), '', f'{sys.executable},0'
        else:  # a source tree: the launcher script with the windowless Python
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            target = str(pythonw if pythonw.exists() else sys.executable)
            arguments, icon = Path(__file__).resolve(), PORT_DIR / 'launcher' / 'bloodborne.ico'
        script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath("Desktop")'
                  '+"\\Bloodborne.lnk");'
                  f'$s.TargetPath="{target}";$s.WorkingDirectory="{PORT_DIR}";$s.IconLocation="{icon}";'
                  + (f"$s.Arguments='\"{arguments}\"';" if arguments else '') + '$s.Save()')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                                creationflags=NO_WINDOW)
        if result.returncode == 0:
            self.messagebox.showinfo('Bloodborne', _('Shortcut created on the desktop.', 'Ярлык создан на рабочем столе.'))
        else:
            self.messagebox.showerror('Bloodborne', result.stderr.decode(errors='replace')[:400])

    def add_to_steam(self):
        """Bloodborne in the Steam library: a non-Steam game that starts Bloodborne.exe (in a source
        tree, this script with --play). Steam rewrites shortcuts.vdf as it exits, so nothing is written
        while it runs; the player closes it and presses the button again."""
        steam = bbport_steam.steam_folder()
        if not steam or not bbport_steam.user_configs(steam):
            self.messagebox.showerror('Bloodborne', _('Steam was not found on this PC, or nobody has signed in to it yet.',
                                                      'Steam не найден на этом ПК или в нём ещё никто не входил.'))
            return
        running = bbport_steam.steam_running()
        if running is None:  # not knowing is not a yes: a running Steam would undo what is written
            self.messagebox.showerror('Bloodborne', _('Could not check whether Steam is running, so nothing was written. '
                                                      'Close Steam completely and press Add to Steam again.',
                                                      'Не удалось проверить, запущен ли Steam, поэтому ничего не записано. '
                                                      'Закройте Steam полностью и нажмите «Добавить в Steam» снова.'))
            return
        if running:
            self.messagebox.showerror('Bloodborne', _('Steam is running. Close Steam completely, also from its tray '
                                                      'icon, and press Add to Steam again.',
                                                      'Steam запущен. Закройте Steam полностью, в том числе в области '
                                                      'уведомлений, и нажмите «Добавить в Steam» снова.'))
            return
        if FROZEN:
            exe, options = PORT_DIR / 'Bloodborne.exe', ''
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            exe, options = pythonw if pythonw.exists() else Path(sys.executable), f'"{Path(__file__).resolve()}" --play'
        self.collect()  # Bloodborne.exe starts the game with the saved settings
        done, failed = bbport_steam.add_to_accounts(steam, 'Bloodborne', str(exe), str(PORT_DIR), str(exe), options)
        if failed:
            text = _('Could not add the game to Steam: {}', 'Не удалось добавить игру в Steam: {}').format(
                '; '.join(f'{path}: {error}' for path, error in failed))
            if done:  # the other accounts were written: say so, and where
                text += '\n\n' + _('It was added or updated in: {}. An earlier shortcuts.vdf is kept as shortcuts.vdf.bak.',
                                   'Добавлено или обновлено в: {}. Прежний shortcuts.vdf сохранён как shortcuts.vdf.bak.'
                                   ).format('; '.join(str(path) for path, _result in done))
            self.messagebox.showerror('Bloodborne', text)
            return
        added = sum(result == 'added' for _path, result in done)
        self.messagebox.showinfo('Bloodborne', _('Bloodborne is in your Steam library: {} added, {} updated (one shortcut per Steam '
                             'account on this PC). It starts with the settings saved in this launcher. An existing '
                             'shortcuts.vdf is kept as shortcuts.vdf.bak.',
                             'Bloodborne в библиотеке Steam: добавлено {}, обновлено {} (по ярлыку на каждый аккаунт '
                             'Steam на этом ПК). Игра запускается с настройками, сохранёнными в лаунчере. Прежний '
                             'shortcuts.vdf сохранён как shortcuts.vdf.bak.').format(added, len(done) - added))

    def close(self):
        try:
            self.collect()
        except Exception:  # never keep the window open over a settings problem
            pass
        if self.installing:
            self.stop()
        self.motion.close()
        self.root.destroy()


# Lines of the game's output that say the window exists and the preparation (mods, patches,
# shaders) is over. "Close the launcher when the game starts" waits for the first of them; a game that
# never prints one (or exits first) leaves the launcher open, which is safer than closing it too early.
GAME_UP_LINES = ('GPU: window and Vulkan presenter ready', 'Entering original x86-64 code')
# The window line is printed as the game window is created, not when it is on screen and has the focus.
# This pause keeps the launcher window from vanishing before the game window has taken over, and gives the
# reader thread time to pass on the lines written just after it. A margin, not a measured value.
CLOSE_AFTER_UP_MS = 1500


def game_is_up(text):
    return any(line in text for line in GAME_UP_LINES)


def start_game(command, env, log_path):
    """Starts the game with its output going straight to LOG_PATH (stdout and stderr). A pipe to the
    launcher would break when the launcher closes: the preparation fails with Errno 22 on its next
    message and the game's log is lost. A file keeps filling, in-game restarts included."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'wb') as log:
        return subprocess.Popen(command, cwd=PORT_DIR, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=NO_WINDOW)


def follow_log(path, finished, emit, interval=0.05, retries=5):
    """Calls EMIT(text) for each complete line added to PATH, and for a last unfinished one, until
    FINISHED() is true and the file is read to its end. A file that cannot be opened or read (antivirus
    holding it, not created yet, removed) is retried with a growing pause, from where it stopped; after
    RETRIES failures in a row the OSError is raised."""
    pending, position, failures, log = b'', 0, 0, None
    try:
        while True:
            over = finished()  # asked first: what was written before it returned is still read below
            try:
                if log is None:
                    log = open(path, 'rb')
                    log.seek(position)
                data = log.read(1 << 16)
                failures = 0
            except OSError:
                if log is not None:
                    log.close()
                    log = None
                failures += 1
                if failures > retries:
                    raise
                time.sleep(interval * 2 ** failures)
                continue
            position += len(data)
            if data:
                pending += data
                cut = pending.rfind(b'\n') + 1
                if cut:
                    emit(pending[:cut].decode('utf-8', errors='replace'))
                    pending = pending[cut:]
            elif over:
                if pending:
                    emit(pending.decode('utf-8', errors='replace'))
                return
            else:
                time.sleep(interval)
    finally:
        if log is not None:
            log.close()


FALLBACK_GRACE_S = 10  # how long the log is still followed after run.py ends, when no job object tells more


def game_over(process, job):
    """True when run.py and everything it started (the in-game restart's launch too) has ended.
    Without a usable job object only run.py is known, and the in-game restart ends it while the new
    launch goes on writing the log: then the end is reported FALLBACK_GRACE_S seconds after run.py's,
    and job.say (when set) is told once that this fallback is in use."""
    alive = job.running() if job else None
    if alive is not None:
        return not alive
    if job is not None and not job.fallback_said:
        job.fallback_said = True
        if job.say:
            job.say('\n' + _('— the launcher could not watch the game processes; after an in-game restart it may '
                             'notice the end up to {} s late —',
                             '— лаунчер не смог отслеживать процессы игры; после перезапуска из игры он может '
                             'заметить завершение с задержкой до {} с —').format(FALLBACK_GRACE_S) + '\n')
    if process.poll() is None:
        return False
    if job is None:
        return True
    if job.ended_at is None:
        job.ended_at = time.monotonic()
    return time.monotonic() - job.ended_at >= FALLBACK_GRACE_S


class JobAccounting(ctypes.Structure):
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION."""
    _fields_ = [('TotalUserTime', ctypes.c_int64), ('TotalKernelTime', ctypes.c_int64),
                ('ThisPeriodTotalUserTime', ctypes.c_int64), ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                ('TotalPageFaultCount', ctypes.c_uint32), ('TotalProcesses', ctypes.c_uint32),
                ('ActiveProcesses', ctypes.c_uint32), ('TotalTerminatedProcesses', ctypes.c_uint32)]


class GameJob:
    """A Windows job holding run.py and everything it starts: bb-probe.exe and the launches made
    by the in-game restart (no longer descendants of the first process)."""

    def __init__(self, process):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.handle = kernel32.CreateJobObjectW(None, None)
        self.assigned = False
        self.say = None  # called with a line for the log when game_over has to do without the job
        self.fallback_said, self.ended_at = False, None
        if self.handle:
            self.assigned = bool(kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(int(process._handle))))

    def running(self):
        """Whether a process of the job is alive; None when the job could not be used."""
        if not (self.handle and self.assigned):
            return None
        info = JobAccounting()
        if not ctypes.windll.kernel32.QueryInformationJobObject(ctypes.c_void_p(self.handle), 1,
                                                                ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return info.ActiveProcesses > 0

    def terminate(self):
        if self.handle:
            ctypes.windll.kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), 1)

    def close(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def play_without_window(settings):
    """--play: the game with the saved settings (shortcuts, Steam). Output goes to the console
    when there is one and to <saves folder>/last_run.log."""
    attach_stdio()
    log_dir = Path(settings.get('user_dir') or DATA_DIR / 'user')
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / 'last_run.log', 'w', encoding='utf-8', buffering=1) as log:
        process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=game_environment(settings),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        for raw in iter(process.stdout.readline, b''):
            text = raw.decode('utf-8', errors='replace')
            log.write(text)
            try:
                sys.stdout.write(text)
            except (OSError, ValueError):
                pass
        return process.wait()


def update_ignore(target):
    """What copying an update over TARGET skips: USER_FILES everywhere and, in bin/reshade, what is
    already installed of ReShade.ini and the presets (players edit them; new presets still arrive)."""
    plain = shutil.ignore_patterns(*USER_FILES)

    def ignore(folder, names):
        skipped = set(plain(folder, names))
        relative = Path(folder).relative_to(PORT_DIR).as_posix()
        if relative in ('bin/reshade', 'bin/reshade/presets'):
            kept = ('ReShade.ini',) if relative == 'bin/reshade' else names
            skipped |= {name for name in kept if name in names and (Path(target) / relative / name).exists()}
        return skipped
    return ignore


def install_update(target, wait_pid):
    """--install-update TARGET PID, run by the downloaded version from its temporary folder:
    waits for the old launcher to close, copies this version over TARGET (never the saves,
    settings, mods or ReShade presets) and starts it."""
    target = Path(target)
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, int(wait_pid))  # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, 60000)
        kernel.CloseHandle(handle)
    ignore = update_ignore(target)
    for attempt in range(30):
        try:
            shutil.copytree(PORT_DIR, target, dirs_exist_ok=True, ignore=ignore)
            break
        except OSError:  # a file still in use: the old launcher is closing
            time.sleep(1)
    else:
        ctypes.windll.user32.MessageBoxW(None, _(
            'Could not install the update. Download it from the releases page.',
            'Не удалось установить обновление. Скачайте его со страницы релизов.'), 'Bloodborne', 0x10)
        webbrowser.open(RELEASES_PAGE)
        return 1
    subprocess.Popen([str(target / 'BLauncher.exe')], cwd=str(target))
    return 0


def main():
    global LANG
    args = sys.argv[1:]
    if args and args[0] in ('--run', '--script'):
        sys.exit(run_role(args))
    settings = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
    LANG = settings.get('ui_language') or windows_language()
    if args[:1] == ['--install-update'] and len(args) == 3:
        sys.exit(install_update(args[1], args[2]))
    if FROZEN and UPDATE_DIR not in PORT_DIR.parents:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)  # what a finished update left behind
    # Without a usable game folder there is nothing to play yet: open the launcher instead.
    if '--play' in args and (Path(settings['game_dir'] or '.') / 'eboot.bin').is_file():
        sys.exit(play_without_window(settings))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
    except (AttributeError, OSError):
        pass
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    Launcher(root, tk, ttk, filedialog, messagebox)
    root.mainloop()


if __name__ == '__main__':
    main()
