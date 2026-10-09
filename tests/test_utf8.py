"""The Python scripts read and write text as UTF-8 whatever the system's code page is (#55)."""
from paths import ROOT
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

SCRIPTS = ROOT / 'scripts'

# Written to a file and run with PYTHONUTF8=0 under a code page that is not UTF-8. No backslashes.
CHILD = '''
import json
import locale
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

scripts, ini, patch_config, mods_dir, mods_config = sys.argv[1:6]
sys.path.insert(0, scripts)
import mods
import patches

found = [('extra.xml/Rückblick', None, ET.Element('Metadata')), ('extra.xml/other', None, ET.Element('Metadata'))]
print(json.dumps({
    'encoding': locale.getpreferredencoding(False),
    'settings': patches.read_settings(Path(ini)),
    'external': [key for key, _, _ in patches.external_selection(found, patch_config)],
    'mods': mods.selected(Path(mods_dir), mods_config),
}))
'''


def non_utf8_environment():
    """The environment of a process started under a legacy code page. On Windows that is the
    system's ANSI code page (cp1252, cp936, ...), which only PYTHONUTF8=0 lets through; elsewhere
    the C locale (ASCII) stands in for it."""
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONUTF8', 'PYTHONIOENCODING', 'PYTHONCOERCECLOCALE', 'LC_ALL', 'LANG')}
    env['PYTHONUTF8'] = '0'
    if os.name != 'nt':
        env.update(LC_ALL='C', LANG='C', PYTHONCOERCECLOCALE='0')
    return env


class LegacyCodePageTests(unittest.TestCase):
    def test_config_files_with_non_ascii_text_are_read_as_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ini = root / 'bbport.ini'
            ini.write_bytes('\ufeffnote=Zoë 日本語 Привет\nupscaler=fsr3\n'.encode('utf-8'))  # Notepad's BOM too
            patch_config = root / 'patches.json'
            patch_config.write_text(json.dumps({'enabled': ['extra.xml/Rückblick']}, ensure_ascii=False),
                                    encoding='utf-8')
            mods_dir = root / 'mods'
            for name in ('Mödel', 'Plain'):
                (mods_dir / name / 'dvdroot_ps4').mkdir(parents=True)
            mods_config = root / 'mods.json'
            mods_config.write_text(json.dumps({'disabled': ['Mödel']}, ensure_ascii=False), encoding='utf-8')
            child = root / 'child.py'
            child.write_text(CHILD, encoding='utf-8')
            result = subprocess.run([sys.executable, str(child), str(SCRIPTS), str(ini), str(patch_config),
                                     str(mods_dir), str(mods_config)],
                                    env=non_utf8_environment(), capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            seen = json.loads(result.stdout)
        self.assertNotIn(seen['encoding'].lower().replace('-', ''), ('utf8', 'cp65001'),
                         'the child must run under a legacy code page for this test to mean anything')
        self.assertEqual(seen['settings'], {'note': 'Zoë 日本語 Привет', 'upscaler': 'fsr3'})
        self.assertEqual(seen['external'], ['extra.xml/Rückblick'])
        self.assertEqual(seen['mods'], ['Plain'])

    def test_unencodable_output_is_replaced_not_fatal(self):
        # The title is "Bloodborne" followed by the trademark sign; a console without it must not stop prepare.
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import prepare; '
                'prepare.replace_unencodable_output(); print("Bloodborne" + chr(0x2122))')
        env = non_utf8_environment()
        env['PYTHONIOENCODING'] = 'ascii'
        result = subprocess.run([sys.executable, '-c', code, str(SCRIPTS)], env=env, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertEqual(result.stdout.strip(), b'Bloodborne?')

    def test_run_reads_the_scripts_output_as_utf8(self):
        spec = importlib.util.spec_from_file_location('bbrun', ROOT / 'run.py')
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
        with tempfile.TemporaryDirectory() as directory:
            port = Path(directory)
            (port / 'scripts').mkdir()
            (port / 'scripts' / 'echo.py').write_text(
                'import sys\nprint(sys.argv[1])\n', encoding='utf-8')
            name = 'C:/Users/Zoë 日本語/Bloodborne'
            run.PORT = port
            run.no_console = lambda: 0
            saved = {key: os.environ.pop(key, None) for key in ('PYTHONUTF8', 'PYTHONIOENCODING')}
            os.environ['PYTHONUTF8'] = '0'
            try:
                self.assertEqual(run.run_script('echo.py', name, capture=True).strip(), name)
            finally:
                os.environ.pop('PYTHONUTF8', None)
                for key, value in saved.items():
                    if value is not None:
                        os.environ[key] = value


class ExplicitEncodingTests(unittest.TestCase):
    """Every text file the scripts and run.py touch names its encoding: no locale default."""

    @staticmethod
    def keyword(call, name):
        return next((k.value for k in call.keywords if k.arg == name), None)

    def binary_open(self, call, first_mode_argument):
        mode = self.keyword(call, 'mode')
        if mode is None and len(call.args) > first_mode_argument:
            mode = call.args[first_mode_argument]
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and 'b' in mode.value

    def offenders(self, path, base=ROOT):
        found = []
        tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            text_file = False
            if isinstance(function, ast.Name) and function.id == 'open':
                text_file = not self.binary_open(node, 1)
            elif isinstance(function, ast.Attribute) and function.attr in ('read_text', 'write_text'):
                text_file = True
            elif isinstance(function, ast.Attribute) and function.attr == 'open':
                text_file = not self.binary_open(node, 0)
            text_pipe = any(self.keyword(node, option) is not None and
                            getattr(self.keyword(node, option), 'value', None) is True
                            for option in ('text', 'universal_newlines'))
            if (text_file or text_pipe) and self.keyword(node, 'encoding') is None:
                found.append(f'{path.relative_to(base)}:{node.lineno}')
        return found

    def test_scripts_and_run_name_their_encoding(self):
        files = sorted(SCRIPTS.glob('*.py')) + [ROOT / 'run.py']
        self.assertGreater(len(files), 5)
        self.assertEqual([found for path in files for found in self.offenders(path)], [])

    def test_the_check_sees_a_missing_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / 'sample.py'
            lines = ['from pathlib import Path', 'open("a")', 'open("b", "rb")', 'Path("c").read_text()',
                     'Path("d").open("wb")', 'Path("e").write_text("x", encoding="utf-8")',
                     'import subprocess', 'subprocess.run(["x"], text=True)']
            sample.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
            found = [item.rsplit(':', 1)[1] for item in self.offenders(sample, Path(directory))]
        self.assertEqual(found, ['2', '4', '8'])


if __name__ == '__main__':
    unittest.main()
