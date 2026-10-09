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
                mock.patch.object(launcher, 'latest_release', side_effect=body if isinstance(body, Exception) else None,
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

    def test_the_manual_check_says_so_when_up_to_date(self):
        _offers, _errors, infos = self.check('1.6.16', False, launcher.newest_release(RELEASES, False), manual=True)
        self.assertEqual(len(infos), 1)

    def test_a_failed_check_is_quiet_unless_asked_for(self):
        for failure in (OSError('offline'), ValueError('not JSON'), KeyError('tag_name')):
            offers, errors, _infos = self.check('1.6.16', False, failure)
            self.assertEqual((offers, errors), ([], []))
        self.assertEqual(len(self.check('1.6.16', False, OSError('offline'), manual=True)[1]), 1)


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
            result = launcher.latest_release(beta)
        return result, urlopen

    def test_it_asks_the_releases_list_not_the_latest_one(self):
        result, urlopen = self.fetch(json.dumps(RELEASES), beta=True)
        self.assertEqual(result[0], '1.7.0-beta.2')
        self.assertIn('/releases?per_page=20', urlopen.call_args[0][0].full_url)

    def test_bad_json_is_a_value_error_the_check_swallows(self):
        with self.assertRaises(ValueError):
            self.fetch('<html>rate limited</html>', beta=False)


if __name__ == '__main__':
    unittest.main()
