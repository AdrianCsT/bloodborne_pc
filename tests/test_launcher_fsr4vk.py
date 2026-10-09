"""The launcher's FSR 4.1.1 (fsr4vk) rules: when the entry can be picked, what the game is told, what happens
to a saved choice that cannot run any more, and the download (urlopen replaced by a local fake)."""
import hashlib
import http.client
import importlib.util
import io
import queue
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher

DLL = 'amd_fidelityfx_upscaler_vk.dll'
MEMBERS = {
    'OptiScaler/' + DLL: b'MZ' + bytes(range(256)) * 20,
    'LICENSES/GPL-3.0.txt': b'gpl',
    'LICENSES/AMD-FidelityFX-SDK-MIT.md': b'mit',
    'LICENSES/Zstandard-BSD.txt': b'bsd',
}
SUPPORTED = {'fsr411': (True, ''), 'dlss': (False, 'not an NVIDIA RTX GPU')}
NO_FILES = {'fsr411': (False, 'FSR 4.1.1 files are not downloaded'), 'dlss': (False, 'not an NVIDIA RTX GPU')}
NO_FEATURE = {'fsr411': (False, 'needs the Vulkan feature shaderInt8'), 'dlss': (True, '')}


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class GateTests(unittest.TestCase):
    def state(self, support, experimental, files):
        return launcher.upscaler_state('fsr411', support, False, experimental, files)

    def test_off_by_default_so_the_entry_says_to_turn_experimental_features_on(self):
        for files in (False, True):
            for support in (SUPPORTED, NO_FILES, None):
                state, reason = self.state(support, False, files)
                self.assertEqual((state, reason), ('no', launcher.FSR4VK_EXPERIMENTAL_REASON))
        self.assertIn('Experimental features', launcher.FSR4VK_EXPERIMENTAL_REASON)

    def test_on_without_the_files_says_they_are_not_downloaded(self):
        self.assertEqual(self.state(NO_FILES, True, False), ('no', launcher.FSR4VK_FILES_REASON))
        self.assertEqual(self.state(None, True, False), ('no', launcher.FSR4VK_FILES_REASON))

    def test_on_with_the_files_can_be_picked(self):
        self.assertEqual(self.state(SUPPORTED, True, True), ('ok', ''))
        self.assertEqual(self.state(None, True, True), ('ok', ''))  # the GPU check could not run
        self.assertEqual(self.state(NO_FILES, True, True), ('ok', ''))  # a check from before the download

    def test_a_gpu_that_cannot_run_it_keeps_its_own_reason(self):
        for experimental in (False, True):
            self.assertEqual(self.state(NO_FEATURE, experimental, True),
                             ('no', 'needs the Vulkan feature shaderInt8'))

    def test_a_verdict_made_under_bb_fsr4vk_0_is_not_a_gpu_verdict(self):
        # the gray entry: a check that ran with BB_FSR4VK=0 in its environment answered "switched off", which
        # says nothing about this PC; the launcher has its own switch, so it must not stay gray on that
        switched_off = {'fsr411': (False, 'switched off (BB_FSR4VK=0)')}
        self.assertEqual(self.state(switched_off, True, True), ('ok', ''))
        self.assertEqual(self.state(switched_off, True, False), ('no', launcher.FSR4VK_FILES_REASON))
        self.assertEqual(self.state(switched_off, False, True), ('no', launcher.FSR4VK_EXPERIMENTAL_REASON))

    def test_the_tool_prints_the_switched_off_reason_the_launcher_ignores(self):
        source = (Path(__file__).resolve().parents[1] / 'tools' / 'gpu_capabilities.c').read_text(encoding='utf-8')
        self.assertIn(f'return "{launcher.FSR4VK_OFF_REASON}";', source)

    def test_the_files_missing_reason_is_the_one_the_capability_tool_prints(self):
        # fsr411_state tells "files missing" from a GPU that cannot run it by this exact text, which is
        # written in tools/gpu_capabilities.c: rewording it there must fail here, not hide the entry
        source = (Path(__file__).resolve().parents[1] / 'tools' / 'gpu_capabilities.c').read_text(encoding='utf-8')
        self.assertIn(f'return "{launcher.FSR4VK_FILES_REASON}";', source)
        self.assertEqual(NO_FILES['fsr411'][1], launcher.FSR4VK_FILES_REASON)

    def test_the_other_upscalers_are_untouched(self):
        self.assertEqual(launcher.upscaler_state('fsr3', SUPPORTED, False), ('ok', ''))
        self.assertEqual(launcher.upscaler_state('fsr4', SUPPORTED, True), ('no', 'download the FSR 4 assets in Graphics'))
        self.assertEqual(launcher.upscaler_state('dlss', SUPPORTED, False), ('no', 'not an NVIDIA RTX GPU'))

    def test_the_dropdown_calls_it_experimental(self):
        self.assertEqual(dict(launcher.UPSCALERS)['fsr411'][0], 'FSR 4.1.1 (experimental)')


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class FallbackTests(unittest.TestCase):
    def test_a_saved_fsr411_without_experimental_features_falls_back(self):
        better, reason = launcher.upscaler_fallback('fsr411', SUPPORTED, False, True)
        self.assertEqual((better, reason), ('fsr3', launcher.FSR4VK_EXPERIMENTAL_REASON))

    def test_a_saved_fsr411_whose_files_are_gone_falls_back(self):
        self.assertEqual(launcher.upscaler_fallback('fsr411', SUPPORTED, True, False),
                         ('fsr3', launcher.FSR4VK_FILES_REASON))

    def test_an_rtx_gpu_falls_back_to_dlss(self):
        support = {'fsr411': (True, ''), 'dlss': (True, '')}
        self.assertEqual(launcher.upscaler_fallback('fsr411', support, False, True)[0], 'dlss')

    def test_a_usable_choice_stays(self):
        self.assertIsNone(launcher.upscaler_fallback('fsr411', SUPPORTED, True, True))
        self.assertIsNone(launcher.upscaler_fallback('fsr3', SUPPORTED, False, False))
        self.assertIsNone(launcher.upscaler_fallback('fsr4', None, False, False))  # unknown: the game decides

    def test_the_old_rule_for_the_other_upscalers_holds(self):
        self.assertEqual(launcher.upscaler_fallback('dlss', SUPPORTED, True, True),
                         ('fsr3', 'not an NVIDIA RTX GPU'))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class EnvironmentTests(unittest.TestCase):
    def environment(self, experimental, **extra):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': experimental, **extra}
        with mock.patch.dict(launcher.os.environ), mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            for name in ('BB_FSR4VK', 'BB_FSR4VK_DIR'):
                launcher.os.environ.pop(name, None)
            return launcher.game_environment(settings)

    def test_experimental_off_keeps_fsr4vk_from_loading(self):
        env = self.environment(False)
        self.assertEqual(env['BB_FSR4VK'], '0')
        self.assertEqual(env['BB_FSR4VK_DIR'], str(launcher.fsr4vk_dir()))

    def test_experimental_on_lets_the_game_load_it_from_the_launchers_folder(self):
        env = self.environment(True)
        self.assertNotIn('BB_FSR4VK', env)
        self.assertEqual(env['BB_FSR4VK_DIR'], str(launcher.fsr4vk_dir()))

    def test_the_folder_is_inside_the_install(self):
        self.assertEqual(launcher.fsr4vk_dir(), launcher.PORT_DIR / 'fsr4vk')

    def test_extra_variables_still_win(self):
        self.assertEqual(self.environment(False, extra_env='BB_FSR4VK=1')['BB_FSR4VK'], '1')

    def test_the_capability_check_looks_in_the_same_folder(self):
        with mock.patch.dict(launcher.os.environ):
            launcher.os.environ['BB_FSR4VK_DIR'] = 'somewhere else'
            self.assertEqual(launcher.gpu_tool()[1]['BB_FSR4VK_DIR'], str(launcher.fsr4vk_dir()))

    def test_the_capability_check_ignores_a_bb_fsr4vk_of_the_launchers_own_environment(self):
        # the check asks what this PC can run; the Experimental features switch is the launcher's, not BB_FSR4VK's
        with mock.patch.dict(launcher.os.environ):
            launcher.os.environ['BB_FSR4VK'] = '0'
            self.assertNotIn('BB_FSR4VK', launcher.gpu_tool()[1])

    def test_experimental_on_is_not_undone_by_a_bb_fsr4vk_of_the_launchers_own_environment(self):
        # the dropdown and the game must agree: with the switch on the game gets to load fsr4vk
        with mock.patch.dict(launcher.os.environ):
            launcher.os.environ['BB_FSR4VK'] = '0'
            settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': True}
            with mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
                self.assertNotIn('BB_FSR4VK', launcher.game_environment(settings))
                settings['experimental'] = False
                self.assertEqual(launcher.game_environment(settings)['BB_FSR4VK'], '0')


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class ShownPathTests(unittest.TestCase):
    """The FSR 4.1.1 card names its folder short (it used to print the full path, over two lines)."""

    def test_the_folder_and_its_licenses_are_shown_relative_to_the_install(self):
        self.assertEqual(launcher.fsr4vk_shown(), 'fsr4vk')
        self.assertEqual(launcher.fsr4vk_shown('LICENSES'), 'fsr4vk\\LICENSES')

    def test_a_folder_elsewhere_is_shortened_like_the_other_paths(self):
        far = Path('D:/some/very/long/path/of/a/player/who/keeps/games/deep/in/folders/fsr4vk')
        with mock.patch.object(launcher, 'fsr4vk_dir', return_value=far):
            shown = launcher.fsr4vk_shown('LICENSES')
        self.assertTrue(shown.endswith('LICENSES'), shown)
        self.assertLessEqual(len(shown), 50)
        self.assertIn('…', shown)


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class UpdateTests(unittest.TestCase):
    def test_an_update_keeps_the_downloaded_folder(self):
        self.assertIn('fsr4vk', launcher.USER_FILES)
        skipped = launcher.update_ignore(launcher.PORT_DIR)(str(launcher.PORT_DIR), ['fsr4vk', 'run.py', 'scripts'])
        self.assertEqual(set(skipped), {'fsr4vk'})

    def test_a_real_update_copy_leaves_the_folder_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            new, old = Path(folder, 'new'), Path(folder, 'old')
            (new / 'fsr4vk').mkdir(parents=True)
            (new / 'fsr4vk' / DLL).write_bytes(b'from the package')
            (new / 'run.py').write_text('new')
            (old / 'fsr4vk').mkdir(parents=True)
            (old / 'fsr4vk' / DLL).write_bytes(b'downloaded')
            (old / 'fsr4vk' / 'SOURCE.txt').write_text('note')
            with mock.patch.object(launcher, 'PORT_DIR', new):
                launcher.shutil.copytree(new, old, dirs_exist_ok=True, ignore=launcher.update_ignore(old))
            self.assertEqual((old / 'run.py').read_text(), 'new')
            self.assertEqual((old / 'fsr4vk' / DLL).read_bytes(), b'downloaded')
            self.assertTrue((old / 'fsr4vk' / 'SOURCE.txt').is_file())


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class DownloadTests(unittest.TestCase):
    def setUp(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive:
            for name, data in MEMBERS.items():
                archive.writestr(name, data)
        self.body = buffer.getvalue()
        fetch = launcher.fetch_fsr4vk
        patches = [
            mock.patch.object(fetch, 'ZIP_SIZE', len(self.body)),
            mock.patch.object(fetch, 'ZIP_SHA256', hashlib.sha256(self.body).hexdigest()),
            mock.patch.object(fetch, 'FILES', tuple(
                (name, name.removeprefix('OptiScaler/'), len(data), hashlib.sha256(data).hexdigest())
                for name, data in MEMBERS.items())),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        port = mock.patch.object(launcher, 'PORT_DIR', Path(self.scratch.name))
        port.start()
        self.addCleanup(port.stop)

    def test_the_download_fills_the_installs_folder_and_reports_progress(self):
        seen = []
        with mock.patch.object(launcher.urllib.request, 'urlopen', side_effect=lambda *a, **k: FakeResponse(self.body)) as opened:
            error = launcher.download_fsr4vk(lambda done, total: seen.append((done, total)))
        self.assertEqual(error, '')
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(launcher.fsr4vk_present())
        folder = launcher.fsr4vk_dir()
        self.assertEqual(folder, Path(self.scratch.name) / 'fsr4vk')
        self.assertEqual((folder / DLL).read_bytes(), MEMBERS['OptiScaler/' + DLL])
        self.assertTrue((folder / 'LICENSES' / 'GPL-3.0.txt').is_file())
        self.assertEqual(seen[-1], (len(self.body), len(self.body)))

    def test_a_failed_download_returns_the_reason_and_leaves_nothing_usable(self):
        offline = urllib.error.URLError('offline')
        with mock.patch.object(launcher.urllib.request, 'urlopen', side_effect=offline):
            error = launcher.download_fsr4vk()
        self.assertIn('offline', error)
        self.assertFalse(launcher.fsr4vk_present())

    def test_a_wrong_download_is_refused(self):
        with mock.patch.object(launcher.fetch_fsr4vk, 'ZIP_SHA256', '0' * 64), \
                mock.patch.object(launcher.urllib.request, 'urlopen', side_effect=lambda *a, **k: FakeResponse(self.body)):
            error = launcher.download_fsr4vk()
        self.assertIn('SHA-256', error)
        self.assertFalse(launcher.fsr4vk_present())


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class MissingHelperTests(unittest.TestCase):
    """tools/fetch_fsr4vk.py hidden: only the download card is off, the launcher still loads."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('launcher_without_fetch', Path(launcher.__file__))
        self.hidden = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'fetch_fsr4vk': None}):  # None makes the import raise ImportError
            spec.loader.exec_module(self.hidden)

    def test_the_launcher_still_loads_with_the_download_switched_off(self):
        self.assertIsNone(self.hidden.fetch_fsr4vk)
        self.assertTrue(hasattr(self.hidden, 'Launcher'))
        problem = self.hidden.fsr4vk_download_problem()
        self.assertTrue(problem)
        self.assertEqual(self.hidden.download_fsr4vk(), problem)  # no AttributeError on None
        self.assertEqual(launcher.fsr4vk_download_problem(), '')  # the real module is there

    def test_the_gate_does_not_need_the_helper(self):
        # files put there by hand still make the entry usable
        self.assertEqual(self.hidden.fsr411_state(SUPPORTED, True, True), ('ok', ''))

    def test_the_card_says_why_and_the_button_stays_off_even_with_experimental_features_on(self):
        shown = {}
        window = types.SimpleNamespace(
            downloading_fsr4vk=False, var=lambda key, store: types.SimpleNamespace(get=lambda: True),
            fsr4vk_label=types.SimpleNamespace(configure=lambda **kw: shown.update(label=kw['text'])),
            fsr4vk_progress=types.SimpleNamespace(set=lambda value: shown.update(progress=value)),
            fsr4vk_button=types.SimpleNamespace(configure=lambda **kw: shown.update(button=kw['state'])))
        with mock.patch.object(self.hidden, 'fsr4vk_present', return_value=False), \
                mock.patch.object(self.hidden, 'LANG', 'en'):
            self.hidden.Launcher.refresh_fsr4vk(window)
        self.assertEqual(shown['button'], 'disabled')
        self.assertIn('not available', shown['label'])
        self.assertIn(self.hidden.fsr4vk_download_problem(), shown['label'])


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class SwitchTests(unittest.TestCase):
    """Launcher.experimental_changed on a stand-in window: the dropdown follows the switch, and a stale
    'cannot run' verdict is asked again when the switch goes on."""

    def window(self, support, on=True):
        calls = []
        window = types.SimpleNamespace(
            upscaler_support=support, ui_calls=queue.Queue(), rechecking=False,
            apply_upscaler_support=lambda: calls.append('apply'), refresh_fsr4vk=lambda: calls.append('refresh'),
            var=lambda key, store: types.SimpleNamespace(get=lambda: on))
        window.check_upscalers = lambda tool=None: (calls.append('check'),
                                                    setattr(window, 'upscaler_support', {'fsr411': (True, '')}))
        window.calls = calls
        return window

    def settle(self, window):
        end = time.time() + 5
        while time.time() < end and (window.rechecking or not window.ui_calls.empty()):
            try:
                window.ui_calls.get(timeout=0.05)()
            except queue.Empty:
                pass

    def test_the_switch_going_on_asks_again_when_the_verdict_says_it_cannot_run(self):
        window = self.window({'fsr411': (False, 'needs the Vulkan feature shaderInt8')})
        launcher.Launcher.experimental_changed(window)
        self.assertEqual(window.calls[:2], ['apply', 'refresh'])  # the dropdown follows at once
        self.settle(window)
        self.assertEqual(window.calls, ['apply', 'refresh', 'check', 'apply'])
        self.assertEqual(window.upscaler_support['fsr411'], (True, ''))
        self.assertFalse(window.rechecking)

    def test_a_good_verdict_or_the_switch_going_off_is_not_asked_again(self):
        for support, on in (({'fsr411': (True, '')}, True), ({'fsr411': (False, 'x')}, False), (None, True)):
            window = self.window(support, on)
            launcher.Launcher.experimental_changed(window)
            self.settle(window)
            self.assertEqual(window.calls, ['apply', 'refresh'])


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class GpuCheckTests(unittest.TestCase):
    def test_one_gpu_check_builds_the_tool_and_its_environment_once(self):
        ran = []

        def run(command, **kwargs):
            ran.append(command[1])
            return types.SimpleNamespace(stdout='UPSCALER fsr3 supported\n', stderr='GPU: Test GPU: discrete, 1 MiB\n',
                                         returncode=0)
        window = types.SimpleNamespace(upscaler_support=None, gpu_text='', gpu_checking=True, ui_calls=queue.Queue(),
                                       apply_upscaler_support=lambda: None, refresh_status=lambda: None,
                                       apply_displays=lambda: None)
        window.check_upscalers = lambda tool=None: launcher.Launcher.check_upscalers(window, tool)
        with mock.patch.object(launcher, 'gpu_tool', return_value=('tool.exe', {})) as tool, \
                mock.patch.object(launcher.subprocess, 'run', side_effect=run), \
                mock.patch.object(launcher, 'connected_displays', return_value=[]), \
                mock.patch.object(launcher, 'LANG', 'en'):
            launcher.Launcher.detect_gpu(window)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(ran, ['--upscalers', '--live-resolution'])
        self.assertEqual(window.upscaler_support, {'fsr3': (True, '')})
        self.assertEqual(window.gpu_text, '✓ Graphics card: Test GPU')

    def test_the_check_after_a_download_asks_for_the_tool_itself(self):
        window = types.SimpleNamespace(upscaler_support=None)
        with mock.patch.object(launcher, 'gpu_tool', return_value=('tool.exe', {})) as tool, \
                mock.patch.object(launcher.subprocess, 'run', return_value=types.SimpleNamespace(
                    stdout='UPSCALER fsr411 supported\n', stderr='', returncode=0)):
            launcher.Launcher.check_upscalers(window)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(window.upscaler_support, {'fsr411': (True, '')})


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class WorkerTests(unittest.TestCase):
    """Launcher.start_fsr4vk_download on a stand-in window: whatever goes wrong in the thread, done() is queued."""

    def run_worker(self, download, check=lambda: None):
        shown, progress = [], []
        window = types.SimpleNamespace(
            downloading_fsr4vk=False, ui_calls=queue.Queue(), refresh_fsr4vk=lambda: None,
            apply_upscaler_support=lambda: shown.append('applied'), check_upscalers=check,
            fsr4vk_progress=types.SimpleNamespace(set=progress.append),
            messagebox=types.SimpleNamespace(showerror=lambda *a: shown.append(a[1])))
        with mock.patch.object(launcher, 'download_fsr4vk', side_effect=download), \
                mock.patch.object(launcher, 'LANG', 'en'):
            launcher.Launcher.start_fsr4vk_download(window)
            self.assertTrue(window.downloading_fsr4vk)
            end = time.time() + 10
            while time.time() < end:
                try:
                    window.ui_calls.get(timeout=0.05)()
                except queue.Empty:
                    if not window.downloading_fsr4vk:
                        break
        return window, shown, progress

    def test_an_error_the_fetcher_does_not_wrap_still_ends_the_download_and_is_shown(self):
        for failure in (http.client.IncompleteRead(b'part', 100), zipfile.BadZipFile('File is not a zip file'),
                        ValueError('bad header'), RuntimeError('unexpected')):
            with self.subTest(failure=type(failure).__name__):
                window, shown, _progress = self.run_worker(failure)
                self.assertFalse(window.downloading_fsr4vk)  # the button can be used again
                self.assertIn('applied', shown)
                message = next(item for item in shown if item != 'applied')
                self.assertIn('Download failed', message)
                self.assertIn(type(failure).__name__, message)

    def test_a_capability_check_that_raises_does_not_leave_the_card_downloading(self):
        def check():
            raise RuntimeError('tool crashed')
        window, shown, _progress = self.run_worker(lambda progress=None: '', check)
        self.assertFalse(window.downloading_fsr4vk)
        self.assertTrue(any('tool crashed' in str(item) for item in shown), shown)

    def test_the_card_refresh_before_its_page_exists_does_nothing(self):
        # the Experimental features switch can change before the Graphics page has built the card
        window = types.SimpleNamespace(downloading_fsr4vk=False,
                                       var=lambda key, store: types.SimpleNamespace(get=lambda: True))
        launcher.Launcher.refresh_fsr4vk(window)  # no AttributeError

    def test_a_clean_download_shows_no_error(self):
        window, shown, _progress = self.run_worker(lambda progress=None: '')
        self.assertFalse(window.downloading_fsr4vk)
        self.assertEqual(shown, ['applied'])

    def test_a_total_of_zero_does_not_divide(self):
        def download(progress):
            progress(10, 0)  # a server that gave no length
            progress(50, 100)
            return ''
        window, shown, progress = self.run_worker(download)
        self.assertEqual(progress, [0.5])
        self.assertEqual(shown, ['applied'])


if __name__ == '__main__':
    unittest.main()
