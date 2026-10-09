"""Object motion vectors on AMD cards are their own experimental choice: Experimental features alone, or a
saved object_motion=1, must not turn them on (they cost FPS and broke vases under frame generation, 1.6.15)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))

if sys.platform == 'win32':
    import bbport_launcher_win as launcher


@unittest.skipUnless(sys.platform == 'win32', 'the Windows launcher')
class ObjectMotionOnAmdTests(unittest.TestCase):
    def environment(self, experimental, object_motion='1', amd_switch=None):
        settings = {**launcher.APP_DEFAULTS, 'game_dir': 'G', 'experimental': experimental}
        if amd_switch is not None:
            settings['object_motion_amd'] = amd_switch
        with mock.patch.dict(launcher.os.environ), \
                mock.patch.object(launcher, 'load_ini', return_value=({'object_motion': object_motion}, [])):
            launcher.os.environ.pop('BB_OBJECT_MOTION_AMD', None)
            return launcher.game_environment(settings)

    def test_the_experimental_switch_alone_does_not_enable_it(self):
        # object_motion is 1 for most players: this used to give BB_OBJECT_MOTION_AMD=1 on every AMD card
        self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(True, object_motion='1'))

    def test_a_saved_object_motion_alone_does_not_enable_it(self):
        for experimental in (False, True):
            self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(experimental, object_motion='1'))

    def test_it_is_off_by_default(self):
        self.assertIs(launcher.APP_DEFAULTS['object_motion_amd'], False)

    def test_the_players_own_choice_with_experimental_features_on_enables_it(self):
        self.assertEqual(self.environment(True, '1', amd_switch=True)['BB_OBJECT_MOTION_AMD'], '1')

    def test_the_choice_does_nothing_without_experimental_features(self):
        self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(False, '1', amd_switch=True))

    def test_the_choice_does_nothing_while_object_motion_is_off(self):
        self.assertNotIn('BB_OBJECT_MOTION_AMD', self.environment(True, '0', amd_switch=True))

    def test_the_note_under_object_motion_no_longer_says_the_switch_turns_it_on(self):
        with mock.patch.object(launcher, 'LANG', 'en'):
            note = launcher.object_motion_hint(True)
        self.assertNotIn('Switched on by Experimental features', note)
        self.assertIn('stays off', note)


if __name__ == '__main__':
    unittest.main()
