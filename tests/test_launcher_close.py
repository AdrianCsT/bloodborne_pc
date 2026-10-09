"""The game's output and "Close the launcher when the game starts" (issue #103): the game writes
last_run.log itself, so closing the launcher cannot break its output, and the launcher closes only
once the window exists, never during mod preparation."""
import os
import queue
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

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

    def test_a_log_that_is_locked_for_a_moment_is_followed_once_it_opens(self):
        # antivirus holds the new file, or it is created a little late: the first opens fail
        def create_late():
            time.sleep(0.3)
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self.log.write_bytes(b'first\nsecond\n')
            done.set()
        done = threading.Event()
        threading.Thread(target=create_late, daemon=True).start()
        got = []
        launcher.follow_log(self.log, done.is_set, got.append, interval=0.02)
        self.assertEqual(''.join(got).splitlines(), ['first', 'second'])

    def test_a_read_error_resumes_where_it_stopped_without_repeating_lines(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes(b'one\ntwo\nthree\n')
        real_open, failures = open, [1]

        class Flaky:
            def __init__(self, stream):
                self.stream, self.reads = stream, 0

            def read(self, size):
                self.reads += 1
                if self.reads == 2 and failures[0]:  # the second read of the file: the file is locked
                    failures[0] -= 1
                    self.reads -= 1
                    raise PermissionError(32, 'in use by another process')
                return self.stream.read(size)

            def __getattr__(self, name):
                return getattr(self.stream, name)

        got = []
        with mock.patch.object(launcher, 'open', create=True, side_effect=lambda *a, **k: Flaky(real_open(*a, **k))):
            launcher.follow_log(self.log, lambda: True, got.append, interval=0.01)
        self.assertEqual(failures, [0])
        self.assertEqual(''.join(got).splitlines(), ['one', 'two', 'three'])

    def test_a_log_that_stays_unreadable_ends_with_an_error_not_a_hang(self):
        with self.assertRaises(OSError):
            launcher.follow_log(self.dir / 'never-created' / 'last_run.log', lambda: False, lambda text: None,
                                interval=0.01, retries=3)

    def test_the_exit_is_reported_even_when_the_log_cannot_be_followed(self):
        process = self.start("say('hello'); time.sleep(0.5)\n")
        job = launcher.GameJob(process)
        window = types.SimpleNamespace(output=queue.Queue())
        unreadable = self.dir / 'a-folder'
        unreadable.mkdir()  # opening a folder as a file fails
        real_sleep = time.sleep
        with mock.patch.object(launcher.time, 'sleep', side_effect=lambda seconds: real_sleep(min(seconds, 0.02))):
            launcher.Launcher.read_output(window, process, job, unreadable)
        job.close()
        items = []
        while not window.output.empty():
            items.append(window.output.get_nowait())
        self.assertEqual(items[-1], (0,))  # the exit code always arrives
        self.assertTrue(any(isinstance(item, str) and 'could not follow' in item for item in items), items)

    def test_without_a_job_object_the_log_is_followed_for_a_while_after_run_py_ends(self):
        # The job object could not be used, so only run.py is watched; the in-game restart ends it while
        # the new launch (not its child) is still running and writing: the launcher must not say "over" yet.
        process = self.start("""
            import subprocess
            subprocess.Popen([sys.executable, '-c',
                              'import time; time.sleep(1.0); print("restarted", flush=True)'],
                             stdout=sys.stdout, stderr=subprocess.STDOUT)
            say('restarting through run.py')
            """)
        job = launcher.GameJob(process)
        job.assigned = False
        notes = []
        job.say = notes.append
        process.wait(timeout=30)
        with mock.patch.object(launcher, 'FALLBACK_GRACE_S', 3.0):
            self.assertFalse(launcher.game_over(process, job))  # run.py has ended, the grace has not
            got = []
            launcher.follow_log(self.log, lambda: launcher.game_over(process, job), got.append, interval=0.02)
        job.close()
        self.assertEqual(''.join(got).splitlines(), ['restarting through run.py', 'restarted'])
        self.assertEqual(len(notes), 1)  # said once, not on every poll
        self.assertIn('restart', notes[0])

    def test_the_grace_ends_and_a_missing_job_means_no_grace(self):
        process = self.start("say('done')\n")
        process.wait(timeout=30)
        job = launcher.GameJob(process)
        job.assigned = False
        with mock.patch.object(launcher, 'FALLBACK_GRACE_S', 0.2):
            self.assertFalse(launcher.game_over(process, job))
            time.sleep(0.3)
            self.assertTrue(launcher.game_over(process, job))
        job.close()
        self.assertTrue(launcher.game_over(process, None))

    def test_game_is_up_only_after_the_preparation(self):
        for text in ('Mods: linking 12 files\n', 'Patches: 247 writes, 1250 bytes applied\n',
                     'Output 2560x1440: scene 1920x1080, direct memory 9152 MiB\n'):
            self.assertFalse(launcher.game_is_up(text), text)
        self.assertTrue(launcher.game_is_up('GPU: window and Vulkan presenter ready; SDK 0x02000071\n'))
        self.assertTrue(launcher.game_is_up('Entering original x86-64 code at guest offset 0xa0\n'))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class CloseOnPlayTests(unittest.TestCase):
    """Launcher.drain_output on a stand-in window: when "Close the launcher when the game starts" closes it."""

    PREPARATION = ['Mods: preparing 12 files\n', 'Patches: 247 writes applied\n', 'Shaders: compiling 3000\n']
    UP = ['GPU: window and Vulkan presenter ready; SDK 0x02000071\n', 'Entering original x86-64 code at guest offset 0xa0\n']

    def drain(self, close_on_play, lines):
        scheduled = []
        window = types.SimpleNamespace(
            ui_calls=queue.Queue(), output=queue.Queue(), app={'close_on_play': close_on_play}, process=object(),
            job=None, close_scheduled=False, drain_output=lambda: None,
            root=types.SimpleNamespace(after=lambda ms, fn: scheduled.append((ms, fn)), destroy=lambda: None),
            status=types.SimpleNamespace(configure=lambda **kwargs: None),
            set_running=lambda *args, **kwargs: None, append=lambda text: None, refresh_status=lambda: None)
        for line in lines:
            window.output.put(line)
        launcher.Launcher.drain_output(window)
        return [ms for ms, fn in scheduled if fn is window.root.destroy]

    def test_it_closes_once_when_the_window_is_up(self):
        self.assertEqual(self.drain(True, self.PREPARATION + self.UP), [launcher.CLOSE_AFTER_UP_MS])

    def test_it_never_closes_during_the_preparation(self):
        self.assertEqual(self.drain(True, self.PREPARATION), [])

    def test_the_game_closing_the_launcher_needs_the_switch(self):
        self.assertEqual(self.drain(False, self.PREPARATION + self.UP), [])

    def test_a_game_that_never_says_it_is_up_leaves_the_launcher_open(self):
        # the old 5 s timer closed it anyway; now the window line decides, and an exit never closes it
        self.assertEqual(self.drain(True, self.PREPARATION + [(1,)]), [])


if __name__ == '__main__':
    unittest.main()
