"""The launcher's update check with canned GitHub release JSON (no network): version order, the stable
and beta channels, and what a beta user is offered."""
import json
import queue
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher


def release(tag, prerelease=False, draft=False, assets=('Bloodborne-Windows.zip',)):
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft,
            'html_url': f'https://github.com/AdrianCsT/bloodborne_pc/releases/tag/{tag}',
            'assets': [{'name': name, 'browser_download_url': f'https://example.test/{tag}/{name}'}
                       for name in assets]}


# What /releases?per_page=20 returns: newest first, as GitHub lists them.
RELEASES = [
    release('windows-v1.7.0-beta.2', prerelease=True),
    release('windows-v1.7.0-beta.1', prerelease=True),
    release('windows-v1.6.16'),
    release('windows-v1.6.15'),
    release('windows-v1.8.0', draft=True),
    release('v0.5-pre2', prerelease=True),
]


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class VersionOrderTests(unittest.TestCase):
    def test_a_beta_sits_between_the_releases_around_it(self):
        order = ['1.6.15', '1.6.16', '1.7.0-beta.1', '1.7.0-beta.2', '1.7.0-beta.10', '1.7.0-rc.1', '1.7.0', '1.7.1']
        keys = [launcher.version_tuple(v) for v in order]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))

    def test_the_release_tag_prefix_is_accepted(self):
        self.assertEqual(launcher.version_tuple('windows-v1.7.0-beta.1'), launcher.version_tuple('1.7.0-beta.1'))
        self.assertEqual(launcher.version_tuple('windows-v1.6.16'), launcher.version_tuple('v1.6.16'))
        self.assertEqual(launcher.release_version('windows-v1.7.0-beta.1'), '1.7.0-beta.1')

    def test_game_versions_still_compare(self):
        self.assertGreater(launcher.version_tuple('01.09'), launcher.version_tuple('01.07'))
        self.assertLess(launcher.version_tuple('?'), launcher.version_tuple('01.00'))
        self.assertLess(launcher.version_tuple(''), launcher.version_tuple('01.00'))

    def test_pre_release_detection(self):
        self.assertTrue(launcher.is_prerelease('1.7.0-beta.1'))
        self.assertFalse(launcher.is_prerelease('1.6.16'))
        self.assertFalse(launcher.is_prerelease('1.7.0'))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class ChannelTests(unittest.TestCase):
    def test_stable_users_are_offered_stable_releases_only(self):
        self.assertEqual(launcher.newest_release(RELEASES, beta=False)[0], '1.6.16')

    def test_beta_switch_adds_the_newest_pre_release(self):
        version, url, page = launcher.newest_release(RELEASES, beta=True)
        self.assertEqual(version, '1.7.0-beta.2')
        self.assertEqual(url, 'https://example.test/windows-v1.7.0-beta.2/Bloodborne-Windows.zip')
        self.assertTrue(page.endswith('/windows-v1.7.0-beta.2'))

    def test_list_order_does_not_matter(self):
        self.assertEqual(launcher.newest_release(list(reversed(RELEASES)), beta=True)[0], '1.7.0-beta.2')
        self.assertEqual(launcher.newest_release(list(reversed(RELEASES)), beta=False)[0], '1.6.16')

    def test_a_beta_user_moves_to_the_final_release(self):
        final = [release('windows-v1.7.0')] + RELEASES
        for beta in (True, False):  # the final release is offered whatever the switch says
            self.assertEqual(launcher.newest_release(final, beta=beta)[0], '1.7.0')
        newest = launcher.newest_release(final, beta=True)[0]
        self.assertGreater(launcher.version_tuple(newest), launcher.version_tuple('1.7.0-beta.2'))

    def test_the_next_beta_reaches_the_switch_on_only(self):
        listed = [release('windows-v1.7.0'), release('windows-v1.8.0-beta.1', True)]
        self.assertEqual(launcher.newest_release(listed, beta=True)[0], '1.8.0-beta.1')
        self.assertEqual(launcher.newest_release(listed, beta=False)[0], '1.7.0')

    def test_the_prerelease_flag_counts_even_without_a_suffix(self):
        flagged = [release('windows-v1.7.0', prerelease=True), release('windows-v1.6.16')]
        self.assertEqual(launcher.newest_release(flagged, beta=False)[0], '1.6.16')
        self.assertEqual(launcher.newest_release(flagged, beta=True)[0], '1.7.0')

    def test_drafts_other_tags_and_missing_assets(self):
        self.assertIsNone(launcher.newest_release([release('windows-v2.0.0', draft=True), release('v0.5-pre2', True)],
                                                  beta=True))
        self.assertIsNone(launcher.newest_release([], beta=True))
        self.assertIsNone(launcher.newest_release([release('windows-v1.7.0-beta.1', True)], beta=False))
        self.assertIsNone(launcher.newest_release([release('windows-v1.7.0', assets=('notes.txt',))], beta=False)[1])

    def test_entries_that_are_not_releases_are_skipped(self):
        # a list from GitHub with junk in it: newest_release must not raise (check_update lets AttributeError out)
        listed = [None, 'windows-v9.9.9', 7, ['windows-v9.9.9'], {'tag_name': ['windows-v9.9.9']}, {'tag_name': None},
                  {}, release('windows-v1.7.0')]
        self.assertEqual(launcher.newest_release(listed, beta=False)[0], '1.7.0')
        self.assertIsNone(launcher.newest_release([None, 'x', 3, []], beta=True))

    def test_malformed_assets_are_skipped_not_crashed_on(self):
        good = {'name': 'Bloodborne-Windows.zip', 'browser_download_url': 'https://example.test/good.zip'}
        broken = {**release('windows-v1.7.0'), 'assets': [None, 'text', 7, {'name': None}, {'name': 5}, {}, good]}
        self.assertEqual(launcher.newest_release([broken], beta=False)[1], 'https://example.test/good.zip')
        for assets in (None, 'zip', {'name': 'a.zip'}, 5):
            odd = {**release('windows-v1.7.0'), 'assets': assets}
            self.assertEqual(launcher.newest_release([odd], beta=False)[:2], ('1.7.0', None))

    def test_a_release_list_of_the_wrong_shape_is_an_error(self):
        with self.assertRaises(ValueError):
            launcher.newest_release({'message': 'API rate limit exceeded'}, beta=False)

    def test_beta_switch_default_follows_the_running_version(self):
        self.assertEqual(launcher.APP_DEFAULTS['beta_versions'], launcher.is_prerelease(launcher.VERSION))


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class CheckTests(unittest.TestCase):
    """Launcher.check_update on a stand-in window: what gets offered, and silence on a failure."""

    def check(self, running, beta, body, manual=False):
        offers, errors, infos = [], [], []
        window = types.SimpleNamespace(
            app={'beta_versions': beta}, ui_calls=queue.Queue(), offer_update=lambda *release: offers.append(release),
            messagebox=types.SimpleNamespace(showerror=lambda *a: errors.append(a), showinfo=lambda *a: infos.append(a)))
        with mock.patch.object(launcher, 'VERSION', running), \
                mock.patch.object(launcher, 'fetch_update_release', side_effect=body if isinstance(body, Exception) else None,
                                  return_value=None if isinstance(body, Exception) else body):
            launcher.Launcher.check_update(window, manual)
            while not window.ui_calls.empty():
                window.ui_calls.get_nowait()()
        return offers, errors, infos

    def test_a_beta_user_is_offered_the_final_release(self):
        found = launcher.newest_release([release('windows-v1.7.0'), release('windows-v1.7.0-beta.2', True)], True)
        offers, _errors, _infos = self.check('1.7.0-beta.2', True, found)
        self.assertEqual([offer[0] for offer in offers], ['1.7.0'])

    def test_nothing_is_offered_at_the_same_version_or_below(self):
        found = launcher.newest_release(RELEASES, True)
        self.assertEqual(self.check('1.7.0-beta.2', True, found)[0], [])
        self.assertEqual(self.check('1.7.0', True, found)[0], [])

    def test_a_programming_error_is_not_taken_for_a_network_failure(self):
        for manual in (False, True):
            with self.assertRaises(AttributeError):
                self.check('1.6.16', False, AttributeError("'NoneType' object has no attribute 'lower'"), manual=manual)

    def test_the_manual_check_says_so_when_up_to_date(self):
        _offers, _errors, infos = self.check('1.6.16', False, launcher.newest_release(RELEASES, False), manual=True)
        self.assertEqual(len(infos), 1)

    def test_a_failed_check_is_quiet_unless_asked_for(self):
        for failure in (OSError('offline'), ValueError('not JSON'), KeyError('tag_name')):
            offers, errors, _infos = self.check('1.6.16', False, failure)
            self.assertEqual((offers, errors), ([], []))
        self.assertEqual(len(self.check('1.6.16', False, OSError('offline'), manual=True)[1]), 1)


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class ExperimentalTests(unittest.TestCase):
    """The Experimental features switch (kept in this file: launcher settings that reach the game)."""

    def environment(self, experimental, object_motion='1'):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': experimental}
        with mock.patch.dict(launcher.os.environ), \
                mock.patch.object(launcher, 'load_ini', return_value=({'object_motion': object_motion}, [])):
            launcher.os.environ.pop('BB_OBJECT_MOTION_AMD', None)
            return launcher.game_environment(settings)

    def test_it_is_off_by_default(self):
        self.assertIs(launcher.APP_DEFAULTS['experimental'], False)
        self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(launcher.APP_DEFAULTS['experimental']))

    def test_on_with_object_motion_on_lets_the_game_use_it_on_amd(self):
        self.assertEqual(self.environment(True)['BB_OBJECT_MOTION_AMD'], '1')

    def test_on_with_object_motion_off_changes_nothing(self):
        self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(True, object_motion='0'))

    def test_the_note_under_the_option_follows_the_switch(self):
        with mock.patch.object(launcher, 'LANG', 'en'):
            off, on = launcher.object_motion_hint(False), launcher.object_motion_hint(True)
        self.assertIn('Off on AMD graphics cards', off)
        self.assertNotIn('(experimental)', off)
        self.assertIn('(experimental)', on)
        self.assertNotIn('Off on AMD graphics cards', on)


DISPLAYS = '1\tQ27GAZD\t2560x1440\t240\t1\n2\tDELL U2417H\t1920x1080\t60\t0\n'


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class MonitorPickerTests(unittest.TestCase):
    """The monitor list of bb-gpu-capabilities --displays and BB_DISPLAY (same file: launcher settings)."""

    def setUp(self):
        launcher._displays = None
        self.addCleanup(setattr, launcher, '_displays', None)

    def run_tool(self, stdout='', returncode=0, error=None):
        result = types.SimpleNamespace(stdout=stdout, stderr='', returncode=returncode)
        return mock.patch.object(launcher.subprocess, 'run', side_effect=error, return_value=result)

    def test_the_tool_output_becomes_labelled_monitors(self):
        monitors = launcher.parse_displays(DISPLAYS)
        self.assertEqual([m['name'] for m in monitors], ['Q27GAZD', 'DELL U2417H'])
        self.assertEqual([m['primary'] for m in monitors], [True, False])
        self.assertEqual([launcher.display_label(m) for m in monitors],
                         ['1: Q27GAZD, 2560x1440, 240 Hz', '2: DELL U2417H, 1920x1080, 60 Hz'])

    def test_an_unknown_refresh_rate_has_no_hz_part(self):
        self.assertEqual(launcher.display_label(launcher.parse_displays('3\tTV\t3840x2160\t0\t0\n')[0]),
                         '3: TV, 3840x2160')

    def test_empty_output_and_bad_lines_mean_no_monitors(self):
        self.assertEqual(launcher.parse_displays(''), [])
        self.assertEqual(launcher.parse_displays('error: SDL\nx\ty\n1\t\t1x1\t0\t1\n'), [])

    def test_the_tool_runs_once_per_run(self):
        with self.run_tool(DISPLAYS) as run:
            first, second = launcher.connected_displays(), launcher.connected_displays()
        self.assertEqual(run.call_count, 1)
        self.assertIs(first, second)
        self.assertEqual(run.call_args[0][0][1], '--displays')
        self.assertEqual(len(first), 2)

    def test_a_failing_tool_gives_no_monitors_and_is_not_asked_again(self):
        with self.run_tool(returncode=1) as run:
            self.assertEqual(launcher.connected_displays(), [])
            self.assertEqual(launcher.connected_displays(), [])
        self.assertEqual(run.call_count, 1)
        launcher._displays = None
        with self.run_tool(error=OSError('missing')):
            self.assertEqual(launcher.connected_displays(), [])
        launcher._displays = None
        with self.run_tool(error=launcher.subprocess.TimeoutExpired('tool', 30)):
            self.assertEqual(launcher.connected_displays(), [])

    def environment(self, monitor):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'monitor': monitor}
        with mock.patch.dict(launcher.os.environ), mock.patch.object(launcher, 'load_ini', return_value=({}, [])):
            launcher.os.environ.pop('BB_DISPLAY', None)
            return launcher.game_environment(settings)

    def test_the_primary_monitor_writes_no_variable(self):
        self.assertEqual(launcher.APP_DEFAULTS['monitor'], '')
        self.assertNotIn('BB_DISPLAY', self.environment(''))

    def test_a_chosen_monitor_is_passed_by_name(self):
        self.assertEqual(self.environment('DELL U2417H')['BB_DISPLAY'], 'DELL U2417H')

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'out' / 'bb-gpu-capabilities.exe').is_file(),
                         'out/bb-gpu-capabilities.exe is not built')
    def test_the_real_tool_output_parses(self):
        exe, env = launcher.gpu_tool()
        result = launcher.subprocess.run([str(exe), '--displays'], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        monitors = launcher.parse_displays(result.stdout)
        self.assertEqual(len(monitors), len([line for line in result.stdout.splitlines() if line.strip()]))
        self.assertEqual(sum(m['primary'] for m in monitors), 1 if monitors else 0)


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class MonitorRowTests(unittest.TestCase):
    """The Monitor picker of Display & FPS in a real (hidden-input) window: one row, however often the GPU
    check runs (it runs again after the FSR 4.1.1 download)."""

    MONITORS = launcher.parse_displays(DISPLAYS) if sys.platform == 'win32' else []

    def setUp(self):
        import tempfile
        import tkinter
        from tkinter import filedialog, messagebox, ttk
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        scratch = Path(self.tmp.name)
        (scratch / 'config').mkdir()
        (scratch / 'config' / 'settings.json').write_text('{"check_updates": false}', encoding='utf-8')
        for patch in (mock.patch.dict(launcher.os.environ, {'BB_CONFIG': str(scratch / 'bbport.ini'),
                                                            'BB_LAUNCHER_ANIMATIONS': '0'}),
                      mock.patch.object(launcher, 'CONFIG_FILE', scratch / 'config' / 'settings.json'),
                      mock.patch.object(launcher, 'CONFIG_DIR', scratch / 'config'),
                      mock.patch.object(launcher, 'DATA_DIR', scratch),
                      mock.patch.object(launcher, 'connected_displays', return_value=self.MONITORS),
                      mock.patch.object(launcher, 'gpu_tool', return_value=(scratch / 'no-such.exe', {})),
                      mock.patch.object(launcher, 'LANG', 'en')):
            patch.start()
            self.addCleanup(patch.stop)
        import time
        for _attempt in range(5):  # Tk now and then cannot find its scripts right after another window closed
            try:
                self.root = tkinter.Tk()
                break
            except tkinter.TclError:
                time.sleep(0.5)
        else:
            self.skipTest('no display for Tk')
        self.addCleanup(self.root.destroy)
        self.tk = tkinter
        self.app = launcher.Launcher(self.root, tkinter, ttk, filedialog, messagebox)

    def settle(self, seconds=0.4):
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def widgets(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.widgets(child)

    def monitor_rows(self):
        return [w for w in self.widgets(self.root) if isinstance(w, self.tk.Label) and w.cget('text') == 'Monitor']

    def run_gpu_check(self):
        self.app.detect_gpu()  # what the start-up thread and the FSR 4.1.1 download run
        self.settle()

    def test_the_gpu_check_running_twice_leaves_one_monitor_row(self):
        self.settle()
        self.run_gpu_check()
        self.run_gpu_check()
        self.assertEqual(len(self.monitor_rows()), 1)
        comboboxes = [w for w in self.widgets(self.app.monitor_frame) if w.winfo_class() == 'TCombobox']
        self.assertEqual(len(comboboxes), 1)
        self.assertEqual(len(comboboxes[0]['values']), 3)  # primary, Q27GAZD, DELL U2417H

    def test_a_later_check_refreshes_the_note_for_a_monitor_that_is_gone(self):
        self.run_gpu_check()
        self.app.var('monitor', 'app').set('Gone Monitor')
        self.run_gpu_check()
        notes = [w.cget('text') for w in self.widgets(self.app.monitor_frame) if isinstance(w, self.tk.Label)]
        self.assertTrue(any('Gone Monitor' in text for text in notes), notes)
        self.assertEqual(len(self.monitor_rows()), 1)

    def test_one_monitor_has_no_picker(self):
        launcher.connected_displays.return_value = self.MONITORS[:1]
        self.run_gpu_check()
        self.assertEqual(self.monitor_rows(), [])


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class FetchTests(unittest.TestCase):
    def fetch(self, body, beta):
        class Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner, size=-1):
                return body.encode() if isinstance(body, str) else body

        with mock.patch.object(launcher.urllib.request, 'urlopen', return_value=Response()) as urlopen:
            result = launcher.fetch_update_release(beta)
        return result, urlopen

    def test_it_asks_the_releases_list_not_the_latest_one(self):
        result, urlopen = self.fetch(json.dumps(RELEASES), beta=True)
        self.assertEqual(result[0], '1.7.0-beta.2')
        self.assertIn('/releases?per_page=100', urlopen.call_args[0][0].full_url)

    def pages(self, *pages):
        """launcher.fetch_update_release over a GitHub that serves PAGES in order; returns (result, requested URLs)."""
        served, urls = list(pages), []

        class Response:
            def __init__(self_inner, body):
                self_inner.body = body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def read(self_inner, size=-1):
                return json.dumps(self_inner.body).encode()

        def urlopen(request, timeout=None):
            urls.append(request.full_url)
            return Response(served.pop(0) if served else [])

        with mock.patch.object(launcher.urllib.request, 'urlopen', side_effect=urlopen):
            return launcher.fetch_update_release(False), urls

    def other_tags(self, count):
        return [release(f'v0.{n}-pre', True) for n in range(count)]

    def test_a_release_past_the_first_page_is_still_found(self):
        # a hundred newer entries of other tags push windows-v1.7.0 onto page 2
        result, urls = self.pages(self.other_tags(100), [release('windows-v1.7.0')])
        self.assertEqual(result[0], '1.7.0')
        self.assertEqual(len(urls), 2)
        self.assertIn('per_page=100', urls[0])
        self.assertIn('page=1', urls[0])
        self.assertIn('page=2', urls[1])

    def test_it_stops_at_the_first_page_that_has_a_release_or_is_the_last(self):
        result, urls = self.pages([release('windows-v1.6.16')] + self.other_tags(99))
        self.assertEqual((result[0], len(urls)), ('1.6.16', 1))
        result, urls = self.pages(self.other_tags(5))  # a short page is the last one
        self.assertEqual((result, len(urls)), (None, 1))

    def test_it_gives_up_after_a_few_pages(self):
        result, urls = self.pages(*[self.other_tags(100) for _n in range(10)])
        self.assertIsNone(result)
        self.assertEqual(len(urls), launcher.RELEASE_PAGES)
        self.assertLessEqual(launcher.RELEASE_PAGES, 5)

    def test_bad_json_is_a_value_error_the_check_swallows(self):
        with self.assertRaises(ValueError):
            self.fetch('<html>rate limited</html>', beta=False)


if __name__ == '__main__':
    unittest.main()
