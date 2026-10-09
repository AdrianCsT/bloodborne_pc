"""Add to Steam: shortcuts.vdf writing and the launcher's button, on a scratch Steam folder. Nothing here
reads or writes the Steam installation of the PC (steam_folder, the registry lookup, is always replaced)."""
import struct
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

import bbport_steam as steam  # noqa: E402

if sys.platform == 'win32':
    import bbport_launcher_win as launcher

EXE = r'C:\Games\bb\Bloodborne.exe'
START = r'C:\Games\bb'


def fields(shortcut):
    return {key: value for _kind, key, value in shortcut}


def read_shortcuts(path):
    root = steam.parse(Path(path).read_bytes())[0]
    return [(key, fields(value)) for _kind, key, value in next(v for _k, k, v in root if k == 'shortcuts')]


def steam_made_file(*games):
    """A shortcuts.vdf as Steam writes it, with games already in it: (name, exe, play time, tags)."""
    entries = []
    for index, (name, exe, played, tags) in enumerate(games):
        item = steam.entry(name, exe, str(Path(exe).parent), exe)
        item = [(kind, key, played if key == 'LastPlayTime' else
                 [(steam.STRING, str(n), tag) for n, tag in enumerate(tags)] if key == 'tags' else value)
                for kind, key, value in item]
        entries.append((steam.MAP, str(index), item))
    return steam.dump([(steam.MAP, 'shortcuts', entries)]) + bytes([steam.END])


class FakeSteam:
    """A scratch Steam folder with userdata/<id>/config for the given account ids."""

    def __init__(self, accounts=('1001',)):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'Steam'
        for account in accounts:
            (self.root / 'userdata' / account / 'config').mkdir(parents=True)
        (self.root / 'userdata' / '0').mkdir(parents=True, exist_ok=True)  # Steam's placeholder account
        (self.root / 'userdata' / 'ac').mkdir()

    def vdf(self, account='1001'):
        return self.root / 'userdata' / account / 'config' / 'shortcuts.vdf'

    def cleanup(self):
        self.tmp.cleanup()


class ShortcutFileTests(unittest.TestCase):
    def setUp(self):
        self.steam = FakeSteam()
        self.addCleanup(self.steam.cleanup)
        self.vdf = self.steam.vdf()

    def add(self, **kwargs):
        return steam.add_shortcut(self.vdf, 'Bloodborne', EXE, START, EXE, **kwargs)

    def test_a_new_account_gets_a_file_with_one_shortcut(self):
        self.assertEqual(self.add(), 'added')
        [(key, entry)] = read_shortcuts(self.vdf)
        self.assertEqual((key, entry['AppName'], entry['Exe'], entry['StartDir']),
                         ('0', 'Bloodborne', f'"{EXE}"', f'"{START}"'))
        self.assertEqual(entry['appid'] & 0xffffffff, steam.shortcut_id(f'"{EXE}"', 'Bloodborne'))
        self.assertTrue(entry['appid'] & 0xffffffff & 0x80000000)
        self.assertFalse(self.vdf.with_name('shortcuts.vdf.bak').exists())  # nothing to back up yet
        self.assertFalse(self.vdf.with_name('shortcuts.vdf.tmp').exists())

    def test_the_shortcut_id_is_steams_crc(self):
        expected = (zlib.crc32(f'"{EXE}"Bloodborne'.encode()) & 0xffffffff) | 0x80000000
        self.assertEqual(steam.shortcut_id(f'"{EXE}"', 'Bloodborne'), expected)

    def test_other_games_are_kept_and_the_old_file_is_backed_up(self):
        before = steam_made_file(('Celeste', r'D:\Celeste\Celeste.exe', 1700000000, ['indie']),
                                 ('Doom', r'D:\Doom\doom.exe', 5, []))
        self.vdf.write_bytes(before)
        self.assertEqual(self.add(options='--play'), 'added')
        names = [(key, entry['AppName']) for key, entry in read_shortcuts(self.vdf)]
        self.assertEqual(names, [('0', 'Celeste'), ('1', 'Doom'), ('2', 'Bloodborne')])
        self.assertEqual(read_shortcuts(self.vdf)[0][1]['LastPlayTime'], 1700000000)
        self.assertEqual(read_shortcuts(self.vdf)[2][1]['LaunchOptions'], '--play')
        self.assertEqual(self.vdf.with_name('shortcuts.vdf.bak').read_bytes(), before)

    def test_adding_twice_updates_in_place_and_keeps_play_time_tags_and_the_first_backup(self):
        before = steam_made_file(('Celeste', r'D:\Celeste\Celeste.exe', 1, []))
        self.vdf.write_bytes(before)
        self.assertEqual(self.add(), 'added')
        # Steam records a play and the player tags the game.
        root = steam.parse(self.vdf.read_bytes())[0]
        game = next(v for _k, k, v in root if k == 'shortcuts')[1][2]
        for index, (kind, key, value) in enumerate(game):
            if key == 'LastPlayTime':
                game[index] = (kind, key, 1800000000)
            if key == 'tags':
                game[index] = (kind, key, [(steam.STRING, '0', 'favorite')])
        self.vdf.write_bytes(steam.dump(root) + bytes([steam.END]))
        self.assertEqual(self.add(options='--play'), 'updated')
        entries = read_shortcuts(self.vdf)
        self.assertEqual([entry['AppName'] for _k, entry in entries], ['Celeste', 'Bloodborne'])
        self.assertEqual((entries[1][1]['LastPlayTime'], entries[1][1]['LaunchOptions']), (1800000000, '--play'))
        tags = next(v for _t, k, v in next(v for _k2, k2, v in steam.parse(self.vdf.read_bytes())[0] if k2 == 'shortcuts')[1][2]
                    if k == 'tags')
        self.assertEqual([tag for _t, _k, tag in tags], ['favorite'])
        self.assertEqual(self.vdf.with_name('shortcuts.vdf.bak').read_bytes(), before)  # the original, not the first add

    def test_a_python_shortcut_of_the_player_is_not_overwritten(self):
        # From a source tree the exe is python(w).exe: another shortcut may start the same interpreter.
        python = r'C:\Python\pythonw.exe'
        other = steam.entry('Other tool', python, r'C:\tools', python, '"C:\\tools\\tool.py"')
        self.vdf.write_bytes(steam.dump([(steam.MAP, 'shortcuts', [(steam.MAP, '0', other)])]) + bytes([steam.END]))
        self.assertEqual(steam.add_shortcut(self.vdf, 'Bloodborne', python, START, python, '"C:\\bb\\launcher.py" --play'),
                         'added')
        apps = read_shortcuts(self.vdf)
        self.assertEqual([entry['AppName'] for _k, entry in apps], ['Other tool', 'Bloodborne'])
        self.assertEqual(apps[0][1]['LaunchOptions'], '"C:\\tools\\tool.py"')
        # the same exe and name again is the same shortcut (Steam's id is made of both): updated in place
        self.assertEqual(steam.add_shortcut(self.vdf, 'Bloodborne', python, START, python, '--play'), 'updated')
        apps = read_shortcuts(self.vdf)
        self.assertEqual([entry['AppName'] for _k, entry in apps], ['Other tool', 'Bloodborne'])
        self.assertEqual(apps[1][1]['LaunchOptions'], '--play')

    def test_a_new_shortcut_takes_the_first_unused_key(self):
        # entries '0' and '2' (another tool or a deleted entry left a gap): the new one must not reuse '2'
        games = [(steam.MAP, key, steam.entry(name, rf'D:\{name}\{name}.exe', rf'D:\{name}', 'x'))
                 for key, name in (('0', 'Celeste'), ('2', 'Doom'))]
        self.vdf.write_bytes(steam.dump([(steam.MAP, 'shortcuts', games)]) + bytes([steam.END]))
        self.assertEqual(self.add(), 'added')
        self.assertEqual([(key, entry['AppName']) for key, entry in read_shortcuts(self.vdf)],
                         [('0', 'Celeste'), ('2', 'Doom'), ('1', 'Bloodborne')])
        self.assertEqual(len({key for key, _e in read_shortcuts(self.vdf)}), 3)

    def test_the_file_round_trips_byte_for_byte(self):
        data = steam_made_file(('Celeste', r'D:\Celeste\Celeste.exe', 7, ['a', 'b']))
        self.assertEqual(steam.dump(steam.parse(data)[0]) + bytes([steam.END]), data)

    def test_a_file_it_cannot_read_is_left_alone(self):
        broken = b'\x00shortcuts\x00\x00' + b'0\x00' + b'\x05odd\x00\x01\x02\x03\x04\x08\x08\x08'
        self.vdf.write_bytes(broken)
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(self.vdf.read_bytes(), broken)
        self.assertFalse(self.vdf.with_name('shortcuts.vdf.bak').exists())

    def test_accounts_are_the_numeric_folders_but_not_zero(self):
        scratch = FakeSteam(accounts=('1001', '2002'))
        self.addCleanup(scratch.cleanup)
        self.assertEqual([c.parent.name for c in steam.user_configs(scratch.root)], ['1001', '2002'])
        self.assertEqual(steam.user_configs(scratch.root / 'missing'), [])

    def test_every_account_gets_it_and_a_broken_one_does_not_stop_the_rest(self):
        scratch = FakeSteam(accounts=('1001', '2002'))
        self.addCleanup(scratch.cleanup)
        scratch.vdf('1001').write_bytes(b'\x00shortcuts\x00\x05bad')
        done, failed = steam.add_to_accounts(scratch.root, 'Bloodborne', EXE, START, EXE)
        self.assertEqual([(path.parent.parent.name, result) for path, result in done], [('2002', 'added')])
        self.assertEqual([path.parent.parent.name for path, _e in failed], ['1001'])
        self.assertEqual(scratch.vdf('1001').read_bytes(), b'\x00shortcuts\x00\x05bad')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process list')
    def test_steam_running_reads_the_process_list(self):
        self.assertIsInstance(steam.steam_running(), bool)


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class LauncherButtonTests(unittest.TestCase):
    """Launcher.add_to_steam on a stand-in window, with Steam's folder and process replaced."""

    def setUp(self):
        self.steam = FakeSteam(accounts=('1001', '2002'))
        self.addCleanup(self.steam.cleanup)

    def press(self, folder, running=False):
        shown = {'error': [], 'info': []}
        window = types.SimpleNamespace(
            collect=lambda: None,
            messagebox=types.SimpleNamespace(showerror=lambda *a: shown['error'].append(a[1]),
                                             showinfo=lambda *a: shown['info'].append(a[1])))
        with mock.patch.object(launcher.bbport_steam, 'steam_folder', return_value=folder), \
                mock.patch.object(launcher.bbport_steam, 'steam_running', return_value=running), \
                mock.patch.object(launcher, 'LANG', 'en'):
            launcher.Launcher.add_to_steam(window)
        return shown

    def test_it_refuses_while_steam_runs_and_writes_nothing(self):
        shown = self.press(self.steam.root, running=True)
        self.assertEqual(len(shown['error']), 1)
        self.assertIn('Steam is running', shown['error'][0])
        self.assertEqual(shown['info'], [])
        self.assertFalse(self.steam.vdf('1001').exists() or self.steam.vdf('2002').exists())

    def test_it_adds_the_game_for_every_account_and_says_what_it_did(self):
        before = steam_made_file(('Celeste', r'D:\Celeste\Celeste.exe', 1, []))
        self.steam.vdf('2002').write_bytes(before)
        shown = self.press(self.steam.root)
        self.assertEqual(shown['error'], [])
        self.assertIn('2 added, 0 updated', shown['info'][0])
        self.assertIn('shortcuts.vdf.bak', shown['info'][0])
        for account in ('1001', '2002'):
            apps = {entry['AppName']: entry for _k, entry in read_shortcuts(self.steam.vdf(account))}
            self.assertIn('Bloodborne', apps)
            self.assertTrue(apps['Bloodborne']['Exe'].strip('"').lower().endswith(('python.exe', 'pythonw.exe', 'bloodborne.exe')))
        self.assertEqual(self.steam.vdf('2002').with_name('shortcuts.vdf.bak').read_bytes(), before)
        again = self.press(self.steam.root)
        self.assertIn('0 added, 2 updated', again['info'][0])

    def test_no_steam_or_no_signed_in_account_is_reported(self):
        for folder in (None, self.steam.root / 'nowhere'):
            shown = self.press(folder)
            self.assertEqual(len(shown['error']), 1)
            self.assertIn('Steam was not found', shown['error'][0])
        empty = FakeSteam(accounts=())
        self.addCleanup(empty.cleanup)
        self.assertIn('Steam was not found', self.press(empty.root)['error'][0])

    def test_the_shortcut_starts_the_packaged_exe(self):
        with mock.patch.object(launcher, 'FROZEN', True), mock.patch.object(launcher, 'PORT_DIR', Path(START)):
            self.press(self.steam.root)
        entry = read_shortcuts(self.steam.vdf('1001'))[0][1]
        self.assertEqual((entry['Exe'], entry['StartDir'], entry['LaunchOptions']), (f'"{EXE}"', f'"{START}"', ''))


if __name__ == '__main__':
    unittest.main()
