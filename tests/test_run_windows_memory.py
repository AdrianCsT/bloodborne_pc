"""Run the real run.py with preparation stand-ins; no game required."""
from paths import ROOT
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('bbrun', ROOT / 'run.py')
run_py = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_py)

# What the stand-in patches.py does: answer `--print-scaled` with STUB_SCALED ("RENDER OUTPUT" or
# nothing), and stop at the real patch compilation after saving its environment.
PATCHES = '''
import json, os, sys
from pathlib import Path

def game_app_version(game):
    return os.environ.get("STUB_VERSION") or None

def game_is_patched(version, env=os.environ):
    return version in (None, "01.09") or bool(env.get("BB_FORCE_PATCHES"))

if __name__ == "__main__":  # run.py also imports this file for game_app_version
    if "--print-scaled" in sys.argv:
        print(os.environ.get("STUB_SCALED", ""))
    else:
        observed = {key: value for key, value in os.environ.items() if key.startswith("BB_")}
        Path("observed.json").write_text(json.dumps(observed))
        print("patches.py stopped here")
        sys.exit(73)  # before the probe can be started
'''


class RenderSizeTests(unittest.TestCase):
    def test_pixels_of_an_explicit_size(self):
        for size, pixels in (('2560x1440', 3686400), ('3840X2160', 8294400), (' 1280 x 720 ', 921600),
                             ('', 0), (None, 0), ('abc', 0), ('1920x', 0), ('-1x5', 0)):
            with self.subTest(size=size):
                self.assertEqual(run_py.render_pixels(size), pixels)


@unittest.skipUnless(os.name == 'nt', 'run.py needs Windows')
class ResolutionMemoryTests(unittest.TestCase):
    def environment_at_patch_compilation(self, resolution, memory=None, **extra):
        with tempfile.TemporaryDirectory(prefix='bb memory ') as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'run.py', root / 'run.py')
            (root / 'eboot.bin').touch()
            (root / 'bb-probe.exe').touch()
            scripts = root / 'scripts'
            scripts.mkdir()
            (scripts / 'mods.py').write_text('import sys\nprint(sys.argv[1])\n')
            for name in ('prepare', 'link_libc', 'link_modules', 'content_profile'):
                (scripts / f'{name}.py').write_text('pass\n')
            (scripts / 'patches.py').write_text(PATCHES)
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('BB_', 'STUB_'))}
            env.update(BB_GAME_DIR=str(root), BB_DATA_DIR=str(root), BB_PROBE=str(root / 'bb-probe.exe'), **extra)
            if resolution is not None:
                env['BB_RENDER_RES'] = resolution
            if memory is not None:
                env['BB_DMEM_MB'] = memory
            run = subprocess.run([sys.executable, 'run.py'], cwd=root, env=env, capture_output=True,
                                 text=True, encoding='utf-8', timeout=60)
            self.assertEqual(run.returncode, 73, run.stdout + run.stderr)
            self.assertTrue((root / 'observed.json').exists(), run.stdout + run.stderr)
            return json.loads((root / 'observed.json').read_text(encoding='utf-8')), run.stdout

    def memory(self, resolution, memory=None, **extra):
        return self.environment_at_patch_compilation(resolution, memory, **extra)[0].get('BB_DMEM_MB')

    def test_explicit_above_1080p_gets_extra_memory(self):
        for resolution in ('2560x1440', '3840x2160', '3840X2160', '2560x1080'):
            with self.subTest(resolution=resolution):
                self.assertEqual(self.memory(resolution), '9152')

    def test_default_and_lower_resolutions_keep_default_pool(self):
        for resolution in (None, '1280x720', '1920x1080', '1080x1920', 'garbage'):
            with self.subTest(resolution=resolution):
                self.assertIsNone(self.memory(resolution))

    def test_explicit_memory_budget_is_preserved(self):
        self.assertEqual(self.memory('3840x2160', '12000'), '12000')

    def test_the_log_says_where_the_memory_comes_from(self):
        _, log = self.environment_at_patch_compilation('2560x1440')
        self.assertIn('Render 2560x1440: direct memory 9152 MiB', log)

    def test_outputs_other_than_1080p_still_get_it_from_the_scaled_path_only_once(self):
        env, log = self.environment_at_patch_compilation(None, STUB_SCALED='2560x1440 3840x2160')
        self.assertEqual((env['BB_RENDER_RES'], env['BB_OUTPUT_RES'], env['BB_DMEM_MB']),
                         ('2560x1440', '3840x2160', '9152'))
        self.assertEqual(log.count('direct memory'), 1)

    def test_a_game_without_the_patches_keeps_its_memory(self):
        self.assertIsNone(self.memory('3840x2160', STUB_VERSION='01.00'))


if __name__ == '__main__':
    unittest.main()
