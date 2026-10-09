from paths import ROOT
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('bbmods', ROOT / 'scripts/mods.py')
mods = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mods)


class ModTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'CUSA03173'
        self.assets = self.game / 'dvdroot_ps4' / 'chr'
        self.assets.mkdir(parents=True)
        (self.game / 'eboot.bin').write_bytes(b'original executable')
        (self.assets / 'a.dcx').write_bytes(b'original')
        (self.assets / 'b.dcx').write_bytes(b'untouched')
        self.moddir = self.root / 'mods'

    def mod(self, name, contents=b'mod', file='a.dcx'):
        root = self.moddir / name
        folder = root / 'dvdroot_ps4' / 'chr'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / file).write_bytes(contents)
        return root

    def test_merge_replacement_new_file_and_directory_listing(self):
        a = self.mod('A')
        self.mod('A', b'new', 'new.dcx')
        result = mods.build_overlay(self.game, self.root / 'out', [('A', a)])
        folder = result / 'dvdroot_ps4' / 'chr'
        self.assertEqual((folder / 'a.dcx').read_bytes(), b'mod')
        self.assertEqual((folder / 'b.dcx').read_bytes(), b'untouched')
        self.assertEqual((folder / 'new.dcx').read_bytes(), b'new')
        self.assertEqual({p.name for p in folder.iterdir()}, {'a.dcx', 'b.dcx', 'new.dcx'})
        self.assertEqual((self.assets / 'a.dcx').read_bytes(), b'original')
        self.assertFalse((self.assets / 'new.dcx').exists())
        self.assertEqual((result / 'eboot.bin').read_bytes(), b'original executable')

    def test_last_mod_wins_and_reordering_changes_winner(self):
        a, b = self.mod('A', b'A'), self.mod('B', b'B')
        for layers, winner in [([('A', a), ('B', b)], b'B'), ([('B', b), ('A', a)], b'A')]:
            result = mods.build_overlay(self.game, self.root / 'out', layers)
            self.assertEqual((result / 'dvdroot_ps4/chr/a.dcx').read_bytes(), winner)

    def test_selection_disable_and_new_mod(self):
        for name in ['C', 'B', 'A']:
            self.mod(name)
        config = self.root / 'mods.json'
        config.write_text(json.dumps({'order': ['B', 'A', 'deleted'], 'disabled': ['A']}))
        self.assertEqual(mods.selected(self.moddir, config), ['B', 'C'])

    def test_no_mod_returns_original_game(self):
        self.assertEqual(mods.build_overlay(self.game, self.root / 'out', []), self.game)
        self.assertFalse((self.root / 'out').exists())

    def test_directory_conflict_does_not_modify_base(self):
        a = self.mod('A')
        (a / 'dvdroot_ps4/chr/a.dcx').unlink()
        nested = a / 'dvdroot_ps4/chr/b.dcx'
        nested.mkdir()
        (nested / 'child').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            mods.build_overlay(self.game, self.root / 'out', [('A', a)])
        self.assertEqual((self.assets / 'b.dcx').read_bytes(), b'untouched')
        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_mod_symlinks_rejected(self):
        a = self.mod('A')
        (a / 'dvdroot_ps4/escape').symlink_to(self.assets, target_is_directory=True)
        with self.assertRaises(ValueError):
            mods.build_overlay(self.game, self.root / 'out', [('A', a)])

    def test_executable_replacement_rejected(self):
        a = self.mod('A')
        (a / 'eboot.bin').write_bytes(b'unsupported')
        with self.assertRaises(ValueError):
            mods.build_overlay(self.game, self.root / 'out', [('A', a)])

    def test_shadps4_sibling_folder_and_global_disable(self):
        legacy = Path(str(self.game) + '-mods')
        folder = legacy / 'dvdroot_ps4/chr'
        folder.mkdir(parents=True)
        (folder / 'a.dcx').write_bytes(b'legacy')
        for enabled, expected in [('1', b'legacy'), ('0', b'original')]:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/mods.py'), str(self.game),
                '--out', str(self.root / 'out'), '--mods-dir', str(self.moddir), '--enabled', enabled],
                capture_output=True, text=True, check=True)
            self.assertEqual((Path(result.stdout.strip()) / 'dvdroot_ps4/chr/a.dcx').read_bytes(), expected)

    def test_app0_wrapped_mod_discovered(self):
        self.moddir.mkdir()
        (self.moddir / 'Wrapped/app0/dvdroot_ps4').mkdir(parents=True)
        self.assertEqual(mods.discover(self.moddir), ['Wrapped'])

    def test_other_case_replaces_the_game_file(self):
        root = self.moddir / 'Upper'
        (root / 'DVDROOT_PS4/Chr').mkdir(parents=True)
        (root / 'DVDROOT_PS4/Chr/A.DCX').write_bytes(b'upper')
        result = mods.build_overlay(self.game, self.root / 'out', [('Upper', root)])
        folder = result / 'dvdroot_ps4/chr'
        self.assertEqual((folder / 'a.dcx').read_bytes(), b'upper')
        self.assertEqual({p.name for p in folder.iterdir()}, {'a.dcx', 'b.dcx'})
        self.assertEqual({p.name for p in result.iterdir()}, {'dvdroot_ps4', 'eboot.bin'})

    def test_wrapped_and_bare_layouts(self):
        layouts = {'Title': 'CUSA03173/dvdroot_ps4/chr', 'Archive': 'Archive v1.2/dvdroot_ps4/chr',
                   'Bare': 'chr', 'Nested': 'Nested/app0/dvdroot_ps4/chr'}
        for name, path in layouts.items():
            folder = self.moddir / name / path
            folder.mkdir(parents=True)
            (folder / 'a.dcx').write_bytes(name.encode())
        (self.moddir / 'Archive/readme.txt').write_text('notes')
        (self.moddir / 'Junk/stuff').mkdir(parents=True)
        self.assertEqual(mods.discover(self.moddir), ['Archive', 'Bare', 'Nested', 'Title'])
        for name in layouts:
            with self.subTest(name=name):
                result = mods.build_overlay(self.game, self.root / 'out',
                                            [(name, self.moddir / name)])
                self.assertEqual((result / 'dvdroot_ps4/chr/a.dcx').read_bytes(), name.encode())

    def test_invalid_profile_fails(self):
        self.mod('A')
        config = self.root / 'mods.json'
        config.write_text('{"disabled":"A"}')
        with self.assertRaises(ValueError):
            mods.selected(self.moddir, config)

    @unittest.skipIf(os.name == 'nt', 'run.sh is the Linux launcher; Windows starts the game through run.py')
    def test_run_uses_overlay_propagates_exit_and_cleans_view(self):
        self.mod('A')
        python = self.root / 'python'
        python.write_text(f'#!{sys.executable}\nimport subprocess,sys\n'
            'if sys.argv[1] == "scripts/mods.py" or sys.argv[1] == "-c":\n'
            '    sys.exit(subprocess.call([sys.executable,*sys.argv[1:]]))\n')
        python.chmod(0o755)
        probe = self.root / 'probe'
        probe.write_text(f'#!{sys.executable}\nimport json,sys,os\nfrom pathlib import Path\n'
            'game=Path(sys.argv[sys.argv.index("--app0")+1])\n'
            'Path(os.environ["BB_DATA_DIR"],"mounted.json").write_text(json.dumps({\n'
            '"path":str(game),"content":(game/"dvdroot_ps4/chr/a.dcx").read_text()}))\n'
            'sys.exit(7)\n')
        probe.chmod(0o755)
        env = dict(os.environ, BB_PREBUILT='1', BB_PROBE=str(probe), PYTHON=str(python),
            BB_DATA_DIR=str(self.root), BB_GAME_DIR=str(self.game),
            BB_MODS_DIR=str(self.moddir), BB_MODS_ENABLED='1', BB_MODS_CONFIG=str(self.root/'mods.json'))
        result = subprocess.run(['bash', 'run.sh'], cwd=ROOT, env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 7, result.stderr)
        mounted = json.loads((self.root / 'mounted.json').read_text())
        self.assertEqual(mounted['content'], 'mod')
        self.assertFalse(Path(mounted['path']).exists())
        self.assertEqual((self.assets/'a.dcx').read_bytes(), b'original')


class WindowsLinkTests(unittest.TestCase):
    """On Windows the overlay holds junctions and hard links, never symlinks (upstream #102): with
    Developer Mode on, symlinks are created fine and the game then panics reading through them
    (Dantelion2 FileTransferTask.cpp(865), every boot, with any loose-file mod)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'CUSA03173'
        for folder in ('chr', 'map'):
            (self.game / 'dvdroot_ps4' / folder).mkdir(parents=True)
            (self.game / 'dvdroot_ps4' / folder / 'a.dcx').write_bytes(b'original ' + folder.encode())
        (self.game / 'eboot.bin').write_bytes(b'original executable')
        self.mod = self.root / 'mods' / 'A' / 'dvdroot_ps4' / 'chr'
        self.mod.mkdir(parents=True)
        (self.mod / 'a.dcx').write_bytes(b'mod')
        (self.mod / 'new.dcx').write_bytes(b'new')
        self.layers = [('A', self.root / 'mods' / 'A')]

    def build_with_symlinks_recorded(self):
        real = Path.symlink_to
        calls = []

        def record(path, *args, **kwargs):
            calls.append(str(path))
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, 'symlink_to', record):
            overlay = mods.build_overlay(self.game, self.root / 'out', self.layers)
        return overlay, calls

    @unittest.skipUnless(os.name == 'nt', 'junctions and hard links are Windows')
    def test_no_symlink_is_made_or_left_behind_and_the_game_reads_through_the_links(self):
        overlay, calls = self.build_with_symlinks_recorded()
        self.assertEqual(calls, [])
        entries = [overlay, *overlay.rglob('*')]
        # rglob does not enter junctions: the untouched map folder is one, the merged chr folder is real.
        self.assertTrue(entries)
        self.assertFalse([p for p in entries if p.is_symlink()])
        self.assertTrue((overlay / 'dvdroot_ps4' / 'map').is_junction())
        self.assertFalse((overlay / 'dvdroot_ps4' / 'chr').is_junction())
        self.assertEqual((overlay / 'dvdroot_ps4/map/a.dcx').read_bytes(), b'original map')
        self.assertEqual((overlay / 'dvdroot_ps4/chr/a.dcx').read_bytes(), b'mod')
        self.assertEqual((overlay / 'dvdroot_ps4/chr/new.dcx').read_bytes(), b'new')
        self.assertTrue((overlay / 'eboot.bin').samefile(self.game / 'eboot.bin'))  # a hard link
        self.assertTrue((overlay / 'dvdroot_ps4/chr/a.dcx').samefile(self.mod / 'a.dcx'))

    @unittest.skipUnless(os.name == 'nt', 'junctions and hard links are Windows')
    def test_removing_the_overlay_leaves_the_game_and_the_mod_alone(self):
        overlay, _ = self.build_with_symlinks_recorded()
        shutil.rmtree(overlay)
        self.assertFalse(overlay.exists())
        self.assertEqual((self.game / 'dvdroot_ps4/map/a.dcx').read_bytes(), b'original map')
        self.assertEqual((self.game / 'dvdroot_ps4/chr/a.dcx').read_bytes(), b'original chr')
        self.assertEqual((self.game / 'eboot.bin').read_bytes(), b'original executable')
        self.assertEqual((self.mod / 'a.dcx').read_bytes(), b'mod')

    def test_windows_uses_junctions_and_hard_links_even_where_symlinks_succeed(self):
        junctions = []
        links = []
        windows = types.SimpleNamespace(name='nt', link=lambda target, link: links.append((str(target), str(link))))
        winapi = types.SimpleNamespace(CreateJunction=lambda target, link: junctions.append((target, link)))
        real_symlink_to = Path.symlink_to
        symlinks = []

        def symlink_that_works(path, *args, **kwargs):  # Developer Mode on: this succeeds
            symlinks.append(str(path))
            return real_symlink_to(path, *args, **kwargs)
        folder, file = self.game / 'dvdroot_ps4', self.game / 'eboot.bin'
        with mock.patch.object(mods, 'os', windows), mock.patch.dict(sys.modules, {'_winapi': winapi}), \
                mock.patch.object(Path, 'symlink_to', symlink_that_works):
            mods.make_link(self.root / 'folder-link', folder)
            mods.make_link(self.root / 'file-link', file)
        self.assertEqual(symlinks, [])
        self.assertEqual(junctions, [(str(folder), str(self.root / 'folder-link'))])
        self.assertEqual(links, [(str(file), str(self.root / 'file-link'))])
        self.assertEqual(mods.LINKED.pop(str(self.root / 'folder-link')), folder)
        self.assertEqual(mods.LINKED.pop(str(self.root / 'file-link')), file)

    def test_windows_copies_a_file_when_it_cannot_be_hard_linked(self):
        def refuse(target, link):
            raise OSError('cross-device link')
        windows = types.SimpleNamespace(name='nt', link=refuse)
        with mock.patch.object(mods, 'os', windows):
            mods.make_link(self.root / 'copy', self.game / 'eboot.bin')
        self.assertEqual((self.root / 'copy').read_bytes(), b'original executable')
        self.assertFalse((self.root / 'copy').is_symlink())
        mods.LINKED.pop(str(self.root / 'copy'))

    def build_with_game_on_another_drive(self, writable_parent, link_fails=False, need_gb=None, free_gb=None):
        """build_overlay as for a game on another drive than out; returns the overlay and keeps
        stderr in self.log. link_fails: os.link refuses like across volumes (EXDEV). need_gb and
        free_gb stand in for the size of the copy and the free space on out's volume."""
        import contextlib
        import errno
        import io
        real = tempfile.mkdtemp
        gb = 1024 ** 3

        def mkdtemp(*args, dir=None, **kwargs):
            if Path(dir).resolve() == self.game.parent.resolve() and not writable_parent:
                raise PermissionError(13, 'Access is denied', str(dir))
            return real(*args, dir=dir, **kwargs)
        self.log = io.StringIO()
        self.link = mock.Mock(side_effect=OSError(errno.EXDEV, 'Invalid cross-device link'))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(mods, 'beside_game', return_value=True))
            stack.enter_context(mock.patch.object(mods.tempfile, 'mkdtemp', mkdtemp))
            stack.enter_context(contextlib.redirect_stderr(self.log))
            if link_fails:
                stack.enter_context(mock.patch('os.link', self.link))
            if need_gb is not None:
                stack.enter_context(mock.patch.object(mods, 'copy_bytes', return_value=int(need_gb * gb)))
            if free_gb is not None:
                usage = types.SimpleNamespace(total=100 * gb, used=0, free=int(free_gb * gb))
                stack.enter_context(mock.patch.object(mods.shutil, 'disk_usage', return_value=usage))
            overlay = mods.build_overlay(self.game, self.root / 'out', self.layers)
        self.addCleanup(shutil.rmtree, overlay, True)
        return overlay

    def test_game_on_another_drive_gets_its_overlay_beside_it(self):
        overlay = self.build_with_game_on_another_drive(writable_parent=True)
        self.assertEqual(overlay.parent, self.game.parent.resolve())
        self.assertEqual((overlay / 'dvdroot_ps4/chr/a.dcx').read_bytes(), b'mod')
        self.assertNotIn('copies', self.log.getvalue())

    def test_unwritable_game_folder_falls_back_to_out_and_says_so(self):
        overlay = self.build_with_game_on_another_drive(writable_parent=False)
        out = (self.root / 'out').resolve()
        self.assertEqual(overlay.parent, out)
        self.assertEqual((overlay / 'dvdroot_ps4/chr/a.dcx').read_bytes(), b'mod')
        self.assertEqual((overlay / 'dvdroot_ps4/chr/new.dcx').read_bytes(), b'new')
        self.assertEqual((overlay / 'dvdroot_ps4/map/a.dcx').read_bytes(), b'original map')
        self.assertEqual((overlay / 'eboot.bin').read_bytes(), b'original executable')
        notes = [line for line in self.log.getvalue().splitlines() if str(out) in line]
        self.assertEqual(len(notes), 1, self.log.getvalue())
        self.assertTrue(notes[0].startswith('Mods: '), notes[0])
        self.assertRegex(notes[0], r'this build copies \d+\.\d GB')

    @unittest.skipUnless(os.name == 'nt', 'Windows links files with os.link; the others use symlinks')
    def test_files_that_cannot_be_hard_linked_are_copied_never_symlinked(self):
        overlay = self.build_with_game_on_another_drive(writable_parent=False, link_fails=True)
        self.assertTrue(self.link.called)  # the copy path ran: every link attempt was refused
        for name, original in (('eboot.bin', self.game / 'eboot.bin'),
                               ('dvdroot_ps4/chr/a.dcx', self.mod / 'a.dcx'),
                               ('dvdroot_ps4/chr/new.dcx', self.mod / 'new.dcx')):
            with self.subTest(name=name):
                self.assertEqual((overlay / name).read_bytes(), original.read_bytes())
                self.assertFalse((overlay / name).samefile(original))  # a copy, not a link
        self.assertFalse([p for p in [overlay, *overlay.rglob('*')] if p.is_symlink()])

    def test_copy_bytes_counts_the_files_a_cross_volume_overlay_copies(self):
        replacements = list(mods.mod_files(self.root / 'mods' / 'A'))
        # The game's top-level files, its files in the folders the mod touches (chr; map stays a
        # junction), and the mod's own files.
        expected = len(b'original executable') + len(b'original chr') + len(b'mod') + len(b'new')
        self.assertEqual(mods.copy_bytes(self.game.resolve(), replacements), expected)

    def test_a_copy_that_does_not_fit_stops_before_copying_and_says_why(self):
        with self.assertRaises(ValueError) as caught:
            self.build_with_game_on_another_drive(writable_parent=False, need_gb=50, free_gb=20)
        message = str(caught.exception)
        self.assertIn('50.0 GB', message)
        self.assertIn('20.0 GB', message)
        self.assertIn(str(self.game.parent.resolve()), message)  # the folder to make writable
        self.assertIn('same drive', message)
        self.assertEqual(list((self.root / 'out').glob('mod-game-*')), [])

    def test_a_copy_that_fits_says_how_big_it_is(self):
        self.build_with_game_on_another_drive(writable_parent=False, need_gb=5, free_gb=20)
        self.assertIn('this build copies 5.0 GB', self.log.getvalue())

    @unittest.skipIf(os.name == 'nt', 'symlinks are the other systems')
    def test_other_systems_keep_using_symlinks(self):
        overlay, calls = self.build_with_symlinks_recorded()
        self.assertTrue(calls)
        self.assertTrue((overlay / 'dvdroot_ps4' / 'map').is_symlink())
        self.assertEqual((overlay / 'dvdroot_ps4/chr/a.dcx').read_bytes(), b'mod')
