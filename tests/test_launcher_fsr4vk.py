"""The launcher's FSR 4.1.1 (fsr4vk) rules: when the entry can be picked, what the game is told, what happens
to a saved choice that cannot run any more, and the download (urlopen replaced by a local fake)."""
import hashlib
import io
import sys
import tempfile
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
        self.assertEqual(self.state(NO_FILES, True, False), ('no', 'FSR 4.1.1 files are not downloaded'))
        self.assertEqual(self.state(None, True, False), ('no', 'FSR 4.1.1 files are not downloaded'))

    def test_on_with_the_files_can_be_picked(self):
        self.assertEqual(self.state(SUPPORTED, True, True), ('ok', ''))
        self.assertEqual(self.state(None, True, True), ('ok', ''))  # the GPU check could not run
        self.assertEqual(self.state(NO_FILES, True, True), ('ok', ''))  # a check from before the download

    def test_a_gpu_that_cannot_run_it_keeps_its_own_reason(self):
        for experimental in (False, True):
            self.assertEqual(self.state(NO_FEATURE, experimental, True),
                             ('no', 'needs the Vulkan feature shaderInt8'))

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
                         ('fsr3', 'FSR 4.1.1 files are not downloaded'))

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


if __name__ == '__main__':
    unittest.main()
