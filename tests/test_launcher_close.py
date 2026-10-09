"""The game's output and "Close the launcher when the game starts" (issue #103): the game writes
last_run.log itself, so closing the launcher cannot break its output, and the launcher closes only
once the window exists, never during mod preparation."""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher


def stand_in(directory, body):
    path = Path(directory) / 'stand_in.py'
    path.write_text('import sys, time\n\ndef say(text):\n    print(text, flush=True)\n\n' + textwrap.dedent(body),
                    encoding='utf-8')
    return [sys.executable, str(path)]


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class LauncherCloseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / 'user' / 'last_run.log'

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, body):
        return launcher.start_game(stand_in(self.dir, body),
                                    {**os.environ, 'PYTHONIOENCODING': 'utf-8'}, self.log)

    def test_output_goes_to_the_file_with_no_pipe(self):
        process = self.start("say('one'); say('two')\n")
        self.assertIsNone(process.stdout)
        self.assertEqual(process.wait(timeout=30), 0)
        self.assertEqual(self.log.read_text(encoding='utf-8').split(), ['one', 'two'])

    def test_stderr_lands_in_the_same_file_in_order(self):
        process = self.start("say('out'); sys.stderr.write('err\\n'); sys.stderr.flush(); say('after')\n")
        process.wait(timeout=30)
        self.assertEqual(self.log.read_text(encoding='utf-8').split(), ['out', 'err', 'after'])

    def test_nobody_reads_and_the_game_still_logs_everything(self):
        # The launcher has closed: nothing reads. The preparation prints after a pause (it died with
        # Errno 22 on a pipe) and so does the running game.
        process = self.start("""
            say('Mods: preparing'); time.sleep(1.0); say('Mods: ready')
            say('GPU: window and Vulkan presenter ready'); time.sleep(0.5)
            for n in range(3):
                say(f'frame {n}'); time.sleep(0.2)
            """)
        self.assertEqual(process.wait(timeout=60), 0)
        lines = self.log.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines, ['Mods: preparing', 'Mods: ready', 'GPU: window and Vulkan presenter ready',
                                 'frame 0', 'frame 1', 'frame 2'])

    def test_follow_log_reports_every_line_and_the_unfinished_last_one(self):
        process = self.start("""
            say('Zażółć gęślą jaźń · 日本語'); time.sleep(0.4); say('second')
            sys.stdout.write('no newline'); sys.stdout.flush()
            """)
        job = launcher.GameJob(process)
        got = []
        launcher.follow_log(self.log, lambda: launcher.game_over(process, job), got.append, interval=0.02)
        job.close()
        text = ''.join(got)
        self.assertEqual(text.splitlines(), ['Zażółć gęślą jaźń · 日本語', 'second', 'no newline'])
        self.assertEqual(text, self.log.read_bytes().decode('utf-8'))

    def test_the_run_lasts_until_the_restarted_launch_is_over(self):
        # The in-game restart starts a new run.py and the first one ends: the job covers both.
        process = self.start("""
            import subprocess
            subprocess.Popen([sys.executable, '-c',
                              'import time; time.sleep(1.5); print("restarted", flush=True)'],
                             stdout=sys.stdout, stderr=subprocess.STDOUT)
            say('restarting through run.py')
            """)
        job = launcher.GameJob(process)
        process.wait(timeout=30)
        self.assertTrue(job.running())
        self.assertFalse(launcher.game_over(process, job))
        got = []
        launcher.follow_log(self.log, lambda: launcher.game_over(process, job), got.append, interval=0.02)
        self.assertFalse(job.running())
        job.close()
        self.assertEqual(''.join(got).splitlines(), ['restarting through run.py', 'restarted'])

    def test_game_is_up_only_after_the_preparation(self):
        for text in ('Mods: linking 12 files\n', 'Patches: 247 writes, 1250 bytes applied\n',
                     'Output 2560x1440: scene 1920x1080, direct memory 9152 MiB\n'):
            self.assertFalse(launcher.game_is_up(text), text)
        self.assertTrue(launcher.game_is_up('GPU: window and Vulkan presenter ready; SDK 0x02000071\n'))
        self.assertTrue(launcher.game_is_up('Entering original x86-64 code at guest offset 0xa0\n'))


if __name__ == '__main__':
    unittest.main()
