"""The Controls page of the Windows launcher (issue #5): what it writes to bbport.ini is what
src/runtime_pad.c reads, and what it reads back is what it wrote.

The runtime takes `key.<input>=` / `pad.<input>=` lines of the file BB_CONFIG names, SDL names separated by
commas. Run: python -m pytest -q tests/test_launcher_controls.py"""
import ast
import ctypes
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))

import bbport_controls as controls  # noqa: E402
import bbport_lang  # noqa: E402

if sys.platform == 'win32':
    import bbport_launcher_win as launcher

SDL3 = next((p for p in (Path(os.environ.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin' / 'SDL3.dll',
                         ROOT / 'out' / 'SDL3.dll') if p.is_file()), None)


def sdl():
    """SDL3 itself, the library whose functions the runtime asks (None where it is not installed)."""
    if not SDL3:
        return None
    os.add_dll_directory(str(SDL3.parent))
    lib = ctypes.CDLL(str(SDL3))
    lib.SDL_GetScancodeFromName.argtypes = [ctypes.c_char_p]
    lib.SDL_GetGamepadButtonFromString.argtypes = [ctypes.c_char_p]
    return lib


class TableTests(unittest.TestCase):
    def test_the_inputs_are_the_runtimes_in_the_same_order(self):
        source = (ROOT / 'src' / 'runtime_pad.c').read_text(encoding='utf-8')
        block = re.search(r'input_names\[IN_COUNT\]=\{(.*?)\};', source, re.S).group(1)
        self.assertEqual([row[0] for row in controls.CONTROLS], re.findall(r'"(\w+)"', block))

    def test_only_the_keyboard_inputs_have_no_gamepad_binding(self):
        # runtime_pad.c refuses pad.<input> from move_up on
        keyboard_only = [row[0] for row in controls.CONTROLS if row[3] is None]
        self.assertEqual(keyboard_only, ['move_up', 'move_down', 'move_left', 'move_right',
                                         'look_up', 'look_down', 'look_left', 'look_right'])
        self.assertFalse(controls.has_pad('look_up'))
        self.assertTrue(controls.has_pad('touchpad_right'))

    def test_every_input_has_an_english_label(self):
        self.assertEqual(set(controls.LABELS), {row[0] for row in controls.CONTROLS})

    def test_the_gtk_launcher_uses_the_shared_table(self):
        tree = ast.parse((ROOT / 'launcher' / 'bbport_launcher.py').read_text(encoding='utf-8'))
        defined = [t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
        imported = [a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == 'bbport_controls'
                    for a in n.names]
        self.assertNotIn('CONTROLS', defined)
        self.assertIn('CONTROLS', imported)

    def test_the_defaults_are_names_sdl_knows(self):
        lib = sdl()
        if not lib:
            self.skipTest('SDL3.dll not found')
        for name, _label, keys, pad in controls.CONTROLS:
            for key in controls.split_binding(keys):
                self.assertNotEqual(lib.SDL_GetScancodeFromName(key.encode()), 0, f'{name}: {key}')
            for button in controls.split_binding(pad):
                if button not in ('lefttrigger', 'righttrigger'):  # read as buttons by the runtime itself
                    self.assertGreaterEqual(lib.SDL_GetGamepadButtonFromString(button.encode()), 0, f'{name}: {button}')

    def test_the_gamepad_choices_are_names_sdl_knows(self):
        lib = sdl()
        if not lib:
            self.skipTest('SDL3.dll not found')
        for button, _label in controls.PAD_BUTTONS:
            if button not in ('lefttrigger', 'righttrigger'):
                self.assertGreaterEqual(lib.SDL_GetGamepadButtonFromString(button.encode()), 0, button)


class BindingTextTests(unittest.TestCase):
    def test_split_and_join_follow_the_runtimes_parser(self):
        self.assertEqual(controls.split_binding('back, touchpad'), ['back', 'touchpad'])
        self.assertEqual(controls.split_binding(' Left Shift ,E,, '), ['Left Shift', 'E'])
        self.assertEqual(controls.split_binding(''), [])
        self.assertEqual(controls.split_binding(None), [])
        self.assertEqual(controls.join_binding(['Space', 'E']), 'Space, E')

    def test_no_line_means_the_default_and_an_empty_line_means_nothing(self):
        self.assertEqual(controls.current_binding({}, 'key', 'circle'), ['Left Shift'])
        self.assertEqual(controls.current_binding({}, 'pad', 'touchpad'), ['back', 'touchpad'])
        self.assertEqual(controls.current_binding({}, 'pad', 'touchpad_right'), [])
        self.assertEqual(controls.current_binding({'key.circle': ''}, 'key', 'circle'), [])
        self.assertEqual(controls.current_binding({'key.circle': 'Left Ctrl, X'}, 'key', 'circle'), ['Left Ctrl', 'X'])

    def test_gamepad_text_is_readable(self):
        self.assertEqual(controls.pad_text('back, touchpad'), 'Back, Touchpad')
        self.assertEqual(controls.pad_text('leftshoulder'), 'LB / L1')
        self.assertEqual(controls.pad_text('somethingnew'), 'somethingnew')

    def test_only_the_edited_inputs_are_written(self):
        updates = controls.ini_updates({'key.cross': 'F', 'upscaler': 'fsr4'})
        self.assertEqual(updates['key.cross'], 'F')
        self.assertIsNone(updates['key.circle'])
        self.assertNotIn('upscaler', updates)
        self.assertNotIn('pad.move_up', updates)  # the runtime refuses it
        self.assertEqual(len(updates), 25 + 17)


class KeyNameTests(unittest.TestCase):
    def test_named_keys(self):
        for keysym, name in (('space', 'Space'), ('Return', 'Return'), ('Escape', 'Escape'), ('Tab', 'Tab'),
                             ('BackSpace', 'Backspace'), ('Up', 'Up'), ('Left', 'Left'), ('F1', 'F1'), ('F12', 'F12'),
                             ('Prior', 'PageUp'), ('Next', 'PageDown'), ('Caps_Lock', 'CapsLock'),
                             ('KP_5', 'Keypad 5'), ('KP_Enter', 'Keypad Enter'), ('KP_Add', 'Keypad +')):
            self.assertEqual(controls.tk_key_to_sdl(keysym), name, keysym)

    def test_modifiers_tell_left_from_right(self):
        self.assertEqual(controls.tk_key_to_sdl('Shift_L'), 'Left Shift')
        self.assertEqual(controls.tk_key_to_sdl('Shift_R'), 'Right Shift')
        self.assertEqual(controls.tk_key_to_sdl('Control_L'), 'Left Ctrl')
        self.assertEqual(controls.tk_key_to_sdl('Control_R'), 'Right Ctrl')
        self.assertEqual(controls.tk_key_to_sdl('Alt_L'), 'Left Alt')
        self.assertEqual(controls.tk_key_to_sdl('Alt_R'), 'Right Alt')
        self.assertEqual(controls.tk_key_to_sdl('Win_L'), 'Left GUI')

    def test_the_held_key_decides_the_side_when_tk_says_left_for_both(self):
        held = {0xA1}  # right Shift down
        self.assertEqual(controls.tk_key_to_sdl('Shift_L', down=lambda vk: vk in held), 'Right Shift')
        self.assertEqual(controls.tk_key_to_sdl('Shift_L', down=lambda vk: vk in {0xA0}), 'Left Shift')
        self.assertEqual(controls.tk_key_to_sdl('Control_L', down=lambda vk: vk in {0xA3}), 'Right Ctrl')
        self.assertEqual(controls.tk_key_to_sdl('Alt_L', down=lambda vk: vk in {0xA5}), 'Right Alt')
        # nothing or both held (a stale or unreadable state): Tk's word stands
        self.assertEqual(controls.tk_key_to_sdl('Shift_R', down=lambda vk: False), 'Right Shift')
        self.assertEqual(controls.tk_key_to_sdl('Shift_L', down=lambda vk: vk in {0xA0, 0xA1}), 'Left Shift')

    def test_letters_and_digits_without_a_layout(self):
        self.assertEqual(controls.tk_key_to_sdl('a'), 'A')
        self.assertEqual(controls.tk_key_to_sdl('A'), 'A')  # Shift held
        self.assertEqual(controls.tk_key_to_sdl('7'), '7')
        self.assertEqual(controls.tk_key_to_sdl('minus'), '-')

    def test_letters_follow_the_position_of_the_key(self):
        # French keyboard: the key labelled A is where a US keyboard has Q (scan code 0x10), Windows virtual key 'A'
        self.assertEqual(controls.tk_key_to_sdl('a', 0x41, lambda vk: 0x10), 'Q')
        self.assertEqual(controls.tk_key_to_sdl('z', 0x5A, lambda vk: 0x11), 'W')
        # a Russian layout sends the Cyrillic letter, the position is still the US one
        self.assertEqual(controls.tk_key_to_sdl('Cyrillic_ef', 0x41, lambda vk: 0x1E), 'A')

    def test_shifted_digits_and_symbols_keep_their_key(self):
        self.assertEqual(controls.tk_key_to_sdl('exclam', 0x31, lambda vk: 0x02), '1')
        self.assertEqual(controls.tk_key_to_sdl('ntilde', 0xC0, lambda vk: 0x27), ';')  # Spanish layout
        self.assertEqual(controls.tk_key_to_sdl('plus', 0xBB, lambda vk: 0x0D), '=')

    def test_keys_the_runtime_cannot_bind_are_refused(self):
        self.assertIsNone(controls.tk_key_to_sdl('comma'))  # bindings are split at commas
        self.assertIsNone(controls.tk_key_to_sdl('Insert'))  # it opens the port's menu in the game
        self.assertIsNone(controls.tk_key_to_sdl('comma', 0xBC, lambda vk: 0x33))
        self.assertIsNone(controls.tk_key_to_sdl('ntilde'))  # no layout to place it
        self.assertIsNone(controls.tk_key_to_sdl('ISO_Level3_Shift'))
        self.assertIsNone(controls.tk_key_to_sdl('a', 0x41, lambda vk: 0))  # position unknown
        self.assertIsNone(controls.tk_key_to_sdl('??'))

    def test_every_name_it_gives_is_one_sdl_knows(self):
        lib = sdl()
        if not lib:
            self.skipTest('SDL3.dll not found')
        names = set(controls.KEYSYMS.values()) | set(controls.SCAN_NAMES.values()) | set(controls.KEYSYM_CHARS.values())
        self.assertGreater(len(names), 100)
        for name in sorted(names):
            self.assertNotEqual(lib.SDL_GetScancodeFromName(name.encode()), 0, name)

    def test_the_scan_table_matches_sdl_positions(self):
        # a handful of keys checked against SDL: the name SDL gives the scancode Windows' scan code 0x10 is
        lib = sdl()
        if not lib:
            self.skipTest('SDL3.dll not found')
        self.assertEqual(lib.SDL_GetScancodeFromName(controls.SCAN_NAMES[0x10].encode()), 20)  # SDL_SCANCODE_Q
        self.assertEqual(lib.SDL_GetScancodeFromName(controls.SCAN_NAMES[0x1E].encode()), 4)  # A
        self.assertEqual(lib.SDL_GetScancodeFromName(controls.SCAN_NAMES[0x2C].encode()), 29)  # Z
        self.assertEqual(lib.SDL_GetScancodeFromName(controls.SCAN_NAMES[0x0B].encode()), 39)  # 0


class MouseNameTests(unittest.TestCase):
    """The mouse inputs of key.<input>= lines (issue #5, part 2): the launcher writes the names runtime_pad.c parses."""

    def test_the_names_are_the_runtimes(self):
        source = (ROOT / 'src' / 'runtime_pad.c').read_text(encoding='utf-8')
        runtime = re.findall(r'\{"((?:Mouse|Wheel) [^"]+)",KIND_', source)
        self.assertEqual(sorted(runtime), sorted(name for name, _label in controls.MOUSE_INPUTS))

    def test_tk_buttons_and_wheel_steps_are_named(self):
        self.assertEqual(controls.mouse_button_to_sdl(1), 'Mouse Left')
        self.assertEqual(controls.mouse_button_to_sdl(2), 'Mouse Middle')
        self.assertEqual(controls.mouse_button_to_sdl(3), 'Mouse Right')
        self.assertIsNone(controls.mouse_button_to_sdl(7))
        self.assertEqual(controls.wheel_to_sdl(120), 'Wheel Up')
        self.assertEqual(controls.wheel_to_sdl(-240), 'Wheel Down')
        self.assertIsNone(controls.wheel_to_sdl(0))

    def test_modifiers_go_in_front_in_one_order(self):
        self.assertEqual(controls.with_modifiers('Mouse Left'), 'Mouse Left')
        self.assertEqual(controls.with_modifiers('Mouse Left', shift=True), 'Shift+Mouse Left')
        self.assertEqual(controls.with_modifiers('Wheel Up', shift=True, ctrl=True, alt=True), 'Shift+Ctrl+Alt+Wheel Up')
        self.assertEqual(controls.with_modifiers('Mouse Right', alt=True), 'Alt+Mouse Right')

    def test_held_modifiers_are_read_from_the_keyboard_state(self):
        self.assertEqual(controls.held_modifiers(lambda vk: False), (False, False, False))
        self.assertEqual(controls.held_modifiers(lambda vk: vk == 0xA1), (True, False, False))   # right Shift
        self.assertEqual(controls.held_modifiers(lambda vk: vk in (0xA2, 0xA5)), (False, True, True))

    def test_a_modifier_key_is_told_from_the_rest(self):
        for keysym in ('Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R'):
            self.assertTrue(controls.is_modifier_keysym(keysym), keysym)
        for keysym in ('a', 'space', 'Caps_Lock', 'Win_L', 'F1'):
            self.assertFalse(controls.is_modifier_keysym(keysym), keysym)

    def test_the_settings_defaults_are_the_runtimes(self):
        source = (ROOT / 'src' / 'runtime_pad.c').read_text(encoding='utf-8')
        camera, invert, auto, sensitivity = re.search(
            r'mouse_defaults=\{(\d),(\d),(\d),([\d.]+)f\}', source).groups()
        self.assertEqual(controls.MOUSE_DEFAULTS, {'mouse_camera': camera, 'mouse_sensitivity': f'{float(sensitivity):.2f}',
                                                   'mouse_invert_y': invert, 'mouse_no_auto_rotation': auto})
        self.assertEqual(set(controls.MOUSE_DEFAULTS), {'mouse_camera', 'mouse_sensitivity', 'mouse_invert_y',
                                                        'mouse_no_auto_rotation'})
        low, high = re.search(r'sensitivity=clamp_setting\(v,([\d.]+)f,([\d.]+)f\)', source).groups()
        self.assertEqual(controls.MOUSE_SENSITIVITY_RANGE, (float(low), float(high)))


class Ds3LayoutTests(unittest.TestCase):
    """The Dark Souls III layout of the Controls page: keyboard and mouse only, the gamepad stays."""

    def test_the_layout(self):
        layout = controls.DS3_KEYS
        self.assertEqual({k: layout[k] for k in ('r1', 'r2', 'l1', 'l2', 'circle', 'cross', 'square', 'triangle')},
                         {'r1': 'Mouse Left', 'r2': 'Shift+Mouse Left', 'l1': 'Mouse Right', 'l2': 'Left Ctrl',
                          'circle': 'Space', 'cross': 'E', 'square': 'R', 'triangle': 'F'})
        self.assertEqual({k: layout[k] for k in ('r3', 'l3', 'up', 'down', 'left', 'right')},
                         {'r3': 'Q, Mouse Middle', 'l3': 'C', 'up': 'Up, Wheel Up', 'down': 'Down, Wheel Down',
                          'left': 'Left', 'right': 'Right'})
        self.assertEqual({k: layout[k] for k in ('options', 'touchpad', 'touchpad_right')},
                         {'options': 'Tab', 'touchpad': 'G', 'touchpad_right': 'Backspace'})
        self.assertEqual([layout[k] for k in ('move_up', 'move_down', 'move_left', 'move_right')], list('WSAD'))
        self.assertEqual([layout[k] for k in ('look_up', 'look_down', 'look_left', 'look_right')], list('IKJL'))

    def test_it_covers_every_input_once_without_a_key_used_twice(self):
        self.assertEqual(set(controls.DS3_KEYS), {row[0] for row in controls.CONTROLS})
        used = [part for value in controls.DS3_KEYS.values() for part in controls.split_binding(value)]
        self.assertEqual(len(used), len(set(used)), 'a key or button on two inputs')
        for value in controls.DS3_KEYS.values():
            self.assertLessEqual(len(controls.split_binding(value)), controls.MAX_BIND)

    def test_every_name_is_one_the_runtime_parses(self):
        lib = sdl()
        mouse = {name for name, _label in controls.MOUSE_INPUTS}
        for name, value in controls.DS3_KEYS.items():
            for part in controls.split_binding(value):
                bare = re.sub(r'^((?:shift|ctrl|alt)\+)+', '', part, flags=re.I)
                if bare in mouse:
                    continue
                if lib:
                    self.assertNotEqual(lib.SDL_GetScancodeFromName(bare.encode()), 0, f'{name}: {part}')

    def test_the_updates_are_key_lines_only(self):
        updates = controls.ds3_updates()
        self.assertEqual(set(updates), {f'key.{row[0]}' for row in controls.CONTROLS})
        self.assertFalse(any(key.startswith('pad.') for key in updates))
        self.assertEqual(updates['key.r2'], 'Shift+Mouse Left')

    def test_the_keys_that_match_the_defaults_write_no_line(self):
        ini = controls.apply_ds3({'pad.cross': 'x', 'key.cross': 'F5', 'upscaler': 'fsr4'})
        self.assertEqual(ini['pad.cross'], 'x')                       # the gamepad is left alone
        self.assertEqual(ini['upscaler'], 'fsr4')
        self.assertEqual(ini['key.cross'], 'E')
        self.assertNotIn('key.move_up', ini)                          # W is the default already: no line
        self.assertEqual(ini['key.look_up'], 'I')
        for name in controls.DS3_KEYS:
            self.assertEqual(controls.current_binding(ini, 'key', name), controls.split_binding(controls.DS3_KEYS[name]), name)
        self.assertEqual(controls.current_binding(ini, 'pad', 'cross'), ['x'])


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'bbport.ini'
        patch = mock.patch.dict(os.environ, {'BB_CONFIG': str(self.path)})
        patch.start()
        self.addCleanup(patch.stop)

    def save(self, ini, lines):
        launcher.save_ini(launcher.ini_values(ini), lines)
        return launcher.load_ini()

    def test_a_remap_is_written_in_the_form_the_runtime_reads(self):
        self.path.write_text('# mine\nupscaler=dlss\nmenu_pos=0.2,0.3\n', encoding='utf-8')
        ini, lines = launcher.load_ini()
        ini['key.circle'] = 'Left Ctrl'
        ini['pad.touchpad'] = controls.join_binding(['back', 'touchpad', 'a'])
        ini['key.look_up'] = ''
        ini, lines = self.save(ini, lines)
        text = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(text[:3], ['# mine', 'upscaler=dlss', 'menu_pos=0.2,0.3'])  # the rest is kept
        self.assertIn('key.circle=Left Ctrl', text)
        self.assertIn('pad.touchpad=back, touchpad, a', text)
        self.assertIn('key.look_up=', text)  # unassigned: an empty line, not a missing one
        self.assertEqual(sum(line.startswith(('key.', 'pad.')) for line in text), 3)  # defaults write nothing

    def test_what_was_written_is_read_back(self):
        ini, lines = launcher.load_ini()
        ini['key.cross'] = 'F5'
        ini['pad.cross'] = 'x'
        ini, lines = self.save(ini, lines)
        again, _lines = launcher.load_ini()
        self.assertEqual(controls.current_binding(again, 'key', 'cross'), ['F5'])
        self.assertEqual(controls.current_binding(again, 'pad', 'cross'), ['x'])
        self.assertEqual(controls.current_binding(again, 'key', 'circle'), ['Left Shift'])  # untouched: default

    def test_reset_removes_the_line(self):
        self.path.write_text('key.cross=F5\npad.cross=x\nupscaler=dlss\n', encoding='utf-8')
        ini, lines = launcher.load_ini()
        del ini['key.cross']
        ini, lines = self.save(ini, lines)
        text = self.path.read_text(encoding='utf-8').splitlines()
        self.assertNotIn('key.cross=F5', text)
        self.assertIn('pad.cross=x', text)
        self.assertIn('upscaler=dlss', text)
        self.assertNotIn('key.cross', ini)

    def test_saving_twice_does_not_grow_the_file(self):
        ini, lines = launcher.load_ini()
        ini['key.cross'] = 'F5'
        ini, lines = self.save(ini, lines)
        first = self.path.read_text(encoding='utf-8')
        self.save(ini, lines)
        self.assertEqual(self.path.read_text(encoding='utf-8'), first)

    def test_the_mouse_settings_are_written_as_the_runtime_reads_them(self):
        for key, value in controls.MOUSE_DEFAULTS.items():
            self.assertEqual(launcher.INI_DEFAULTS[key], value, key)
        ini, lines = launcher.load_ini()
        ini['mouse_sensitivity'] = '2.50'
        ini['mouse_invert_y'] = '1'
        ini, lines = self.save(ini, lines)
        text = self.path.read_text(encoding='utf-8').splitlines()
        for line in ('mouse_camera=1', 'mouse_sensitivity=2.50', 'mouse_invert_y=1', 'mouse_no_auto_rotation=0'):
            self.assertIn(line, text)
        self.assertEqual({'mouse_camera', 'mouse_invert_y', 'mouse_no_auto_rotation'} - launcher.INI_FLAGS, set())
        self.assertIn('mouse_sensitivity', launcher.INI_FLOATS)

    def test_the_ds3_layout_is_written_as_key_lines_and_leaves_the_gamepad_alone(self):
        self.path.write_text('pad.cross=x\nkey.cross=F5\n', encoding='utf-8')
        ini, lines = launcher.load_ini()
        ini, lines = self.save(controls.apply_ds3(ini), lines)
        text = self.path.read_text(encoding='utf-8').splitlines()
        for line in ('pad.cross=x', 'key.cross=E', 'key.r1=Mouse Left', 'key.r2=Shift+Mouse Left', 'key.r3=Q, Mouse Middle',
                     'key.up=Up, Wheel Up', 'key.down=Down, Wheel Down', 'key.l2=Left Ctrl', 'key.look_left=J'):
            self.assertIn(line, text)
        self.assertEqual(sum(line.startswith('pad.') for line in text), 1)
        self.assertNotIn('key.move_up=W', text)   # the default needs no line

    def test_a_value_with_an_equals_sign_survives(self):
        ini, lines = launcher.load_ini()
        ini['key.cross'] = '='  # the key SDL calls "="
        ini, lines = self.save(ini, lines)
        again, _lines = launcher.load_ini()
        self.assertEqual(again['key.cross'], '=')


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class ControllerTests(unittest.TestCase):
    def environment(self, **changes):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', **changes}
        with mock.patch.dict(launcher.os.environ):
            launcher.os.environ.pop('BB_GAMEPAD', None)
            return launcher.game_environment(settings)

    def test_automatic_sets_nothing(self):
        self.assertEqual(launcher.APP_DEFAULTS['gamepad'], '')
        self.assertNotIn('BB_GAMEPAD', self.environment())

    def test_the_chosen_controller_reaches_the_game(self):
        self.assertEqual(self.environment(gamepad='030000005e040000')['BB_GAMEPAD'], '030000005e040000')

    def test_the_list_is_what_the_tool_prints(self):
        output = '030000005e0400008e02000000007200\tXbox 360 Controller\r\n\nnot a line\n0300\tPS5 Controller\n'
        self.assertEqual(launcher.parse_gamepads(output),
                         [('030000005e0400008e02000000007200', 'Xbox 360 Controller'), ('0300', 'PS5 Controller')])
        self.assertEqual(launcher.parse_gamepads(''), [])

    def test_automatic_is_first_and_the_saved_one_stays_while_it_is_unplugged(self):
        pads = [('aa', 'Pad A'), ('bb', 'Pad B')]
        with mock.patch.object(launcher, 'LANG', 'en'):
            options = launcher.gamepad_options(pads, '', '')
            self.assertEqual(options, [('', 'Automatic (first connected)'), ('aa', 'Pad A'), ('bb', 'Pad B')])
            self.assertEqual(launcher.gamepad_options(pads, 'bb', 'Pad B'), options)
            gone = launcher.gamepad_options(pads, 'cc', 'Pad C')
            self.assertEqual(gone[-1], ('cc', 'Pad C (not connected)'))
            self.assertEqual(launcher.gamepad_options([], 'cc', '')[-1], ('cc', 'cc (not connected)'))


class TranslationTests(unittest.TestCase):
    LANGUAGES = ('ar', 'es', 'pt', 'fr', 'de', 'it', 'pl', 'tr', 'zh', 'ja', 'ko')

    def test_every_table_matches_the_keys(self):
        for language in self.LANGUAGES:
            bbport_lang.table(language)  # raises on a length mismatch
        self.assertEqual(len(self.LANGUAGES), 11)

    def test_the_controls_texts_are_translated_everywhere(self):
        for text in (*controls.LABELS.values(), 'Controls', 'Controller', 'Keyboard', 'Gamepad'):
            self.assertIn(text, bbport_lang.KEYS, text)
            for language in self.LANGUAGES:
                self.assertTrue(bbport_lang.table(language).get(text), f'{language}: {text}')

    def test_the_mouse_texts_are_translated_everywhere(self):
        texts = ['Mouse', 'Mouse controls the camera', 'Mouse sensitivity', 'Invert vertical look',
                 'Camera turns only by the mouse while walking', 'Dark Souls III layout',
                 'Press Change, then a key, a mouse button or the wheel. Add gives the input a second one. '
                 'Applied when the game starts.',
                 'Replace your keyboard and mouse bindings with the Dark Souls III layout? Gamepad bindings are not changed.',
                 'Dark Souls III layout applied. Reset all brings the defaults back.']
        for text in texts:
            self.assertIn(text, bbport_lang.KEYS, text)
            for language in self.LANGUAGES:
                translated = bbport_lang.table(language).get(text)
                self.assertTrue(translated, f'{language}: {text}')
        # the layout is named the same in every language (a game title), and the old hint is gone
        self.assertNotIn('Press Change, then the key you want. Add gives the input a second key. '
                         'Applied when the game starts.', bbport_lang.KEYS)

    def test_the_file_keeps_its_windows_line_endings(self):
        data = (ROOT / 'launcher' / 'bbport_lang.py').read_bytes()
        self.assertEqual(data.count(b'\n'), data.count(b'\r\n'))

    @unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
    def test_every_text_of_the_page_is_in_the_translations(self):
        tree = ast.parse((ROOT / 'launcher' / 'bbport_launcher_win.py').read_text(encoding='utf-8'))
        names = {'build_controls', 'apply_gamepads', 'show_control', 'start_capture', 'stop_capture',
                 'control_key', 'control_key_release', 'control_mouse', 'poll_side_buttons', 'assign_key',
                 'apply_ds3_layout', 'gamepad_options'}
        pieces = [f for c in ast.walk(tree) if isinstance(c, (ast.ClassDef, ast.Module)) for f in c.body
                  if isinstance(f, ast.FunctionDef) and f.name in names]
        self.assertEqual({f.name for f in pieces}, names)
        texts = [n.args[0].value for f in pieces for n in ast.walk(f) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_' and n.args and isinstance(n.args[0], ast.Constant)]
        self.assertGreater(len(texts), 15)
        for text in texts:
            self.assertIn(text, bbport_lang.KEYS, text)


if __name__ == '__main__':
    unittest.main()
