from paths import ROOT
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import patches

from patches import (EBOOT_BASE, OUTPUT_SIZE, RESOLUTION_TEMPLATE, SCENE_HEIGHT,
                     SCENE_WIDTH, UI_HEIGHT, UI_WIDTH, compile_patches,
                     render_size, resolution_writes, scaled_sizes, effect_patches,
                     validate_patch_requirements, external_patches, external_selection,
                     compile_external)

XML = ROOT / 'patches/Bloodborne.xml'
SEGMENTS = [(0, 0x6000000)]


def immediate(writes, address):
    # The XML splits a 9-byte MOV imm32 + NOP into 4+4+1 byte writes.
    data = bytearray(9)
    for offset, value in writes:
        if address <= offset < address + 9:
            data[offset-address:offset-address+len(value)] = value
    assert data[5:] == b'\x0f\x1f\x40\x00'
    return data[0], struct.unpack_from('<I', data, 1)[0]


class NativeUiTests(unittest.TestCase):
    def test_presets_keep_ui_native(self):
        for preset, expected in [(1, (1280, 720)), (2, (1130, 636)),
                                 (3, (960, 540)), (4, (640, 360))]:
            with self.subTest(preset=preset):
                size = render_size({'upscaler': 'fsr3', 'preset': str(preset)})
                self.assertEqual(size, expected)
                writes = resolution_writes(XML, size, '01.09', SEGMENTS)
                self.assertEqual(immediate(writes, SCENE_WIDTH), (0xB8, size[0]))
                self.assertEqual(immediate(writes, SCENE_HEIGHT), (0xB8, size[1]))
                self.assertEqual(immediate(writes, UI_WIDTH), (0xB8, OUTPUT_SIZE[0]))
                self.assertEqual(immediate(writes, UI_HEIGHT), (0xB9, OUTPUT_SIZE[1]))

    def test_explicit_scene_resolution_keeps_native_ui(self):
        writes = resolution_writes(XML, (800, 450), '01.09', SEGMENTS)
        self.assertEqual(immediate(writes, SCENE_WIDTH)[1], 800)
        self.assertEqual(immediate(writes, UI_WIDTH)[1], 1920)
        self.assertEqual(immediate(writes, UI_HEIGHT)[1], 1080)

    def test_coordinate_and_aspect_fixes_are_preserved(self):
        original = compile_patches(XML, [RESOLUTION_TEMPLATE], '01.09', SEGMENTS)
        modified = resolution_writes(XML, (960, 540), '01.09', SEGMENTS)
        self.assertEqual(len(original), len(modified))
        changed = {SCENE_WIDTH, SCENE_HEIGHT, UI_WIDTH, UI_HEIGHT}
        self.assertEqual([w for w in original if w[0] not in changed],
                         [w for w in modified if w[0] not in changed])

    def test_unexpected_or_missing_ui_instruction_is_rejected(self):
        for remove in (False, True):
            tree = ET.parse(XML)
            for meta in tree.getroot().iter('Metadata'):
                if meta.get('Name') == RESOLUTION_TEMPLATE and meta.get('AppVer') == '01.09':
                    patch_list = meta.find('PatchList')
                    for line in list(patch_list):
                        if int(line.get('Address'), 0) == UI_WIDTH + EBOOT_BASE:
                            if remove:
                                patch_list.remove(line)
                            else:
                                line.set('Value', '0x000500B9')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'patch.xml'
                tree.write(path)
                with self.assertRaises(ValueError):
                    resolution_writes(path, (960, 540), '01.09', SEGMENTS)

    def test_native_and_disabled_upscaler_need_no_resolution_patch(self):
        self.assertIsNone(render_size({'preset': '0'}))
        self.assertIsNone(render_size({'upscaler': 'off', 'preset': '3'}))

    def test_output_other_than_1080p_scales_the_scene(self):
        self.assertIsNone(scaled_sizes({'output_res': '1920x1080', 'preset': '2'}))
        # Steam Deck: below 1080p the scene is still the preset's fraction of the output.
        for preset, expected in [(0, (1280, 720)), (2, (752, 424)), (4, (426, 240))]:
            with self.subTest(preset=preset):
                self.assertEqual(scaled_sizes({'output_res': '1280x720', 'preset': str(preset)}),
                                 (expected, (1280, 720)))
        self.assertEqual(scaled_sizes({'output_res': '3840x2160', 'preset': '3'}),
                         ((1916, 1078), (3840, 2160)))
        # TAA is native-only and uses the live host targets.
        self.assertIsNone(scaled_sizes({'output_res': '1280x720', 'upscaler': 'taa', 'preset': '3'}))


class FpsListTests(unittest.TestCase):
    """The FPS++ lists follow shadps4-emu/ps4_cheats of 2026-10-02 (timesteps, foliage wind)."""

    def writes(self, preset):
        return compile_patches(XML, patches.FPS_PRESETS[preset], '01.09', SEGMENTS)

    def test_lists_only_layer_a_float_over_its_own_immediate(self):
        # A 9-byte "mov dword [rsp+18h], imm32" followed by a 4-byte write of the immediate is the
        # lists' own layering. Any other overlap is two patches fighting over the same bytes
        # (upstream's call at 0x02715D71 over the one at 0x02715D75 was one).
        for preset in ('60', '90', 'uncap'):
            with self.subTest(preset=preset):
                spans = sorted((offset, offset + len(data)) for offset, data in self.writes(preset))
                for (start, end), (next_start, next_end) in zip(spans, spans[1:]):
                    if next_start < end:
                        self.assertEqual((end - start, next_start - start, next_end - next_start), (9, 5, 4))

    def test_foliage_wind_goes_to_the_frame_timer_in_every_list(self):
        for preset in ('60', '90', 'uncap'):
            with self.subTest(preset=preset):
                found = {offset + EBOOT_BASE: data for offset, data in self.writes(preset)}
                self.assertEqual(found[0x02713D91], bytes.fromhex('736B0403'))
                self.assertIn(0x02418E3D, found)
                self.assertIn(0x02418E9C, found)
                self.assertNotIn(0x02715D71, found)

    def test_lists_do_not_write_the_stale_physics_float(self):
        # 0x011383CA sits inside a NOP of the game: the old lists' write there never did anything.
        for preset in ('60', '90', 'uncap'):
            with self.subTest(preset=preset):
                for offset, data in self.writes(preset):
                    self.assertFalse(offset <= 0x011383CA - EBOOT_BASE < offset + len(data))

    def test_sprint_fix_stays_with_the_high_frame_rates(self):
        self.assertNotIn('Sprint Fix (High FPS)', patches.FPS_PRESETS['60'])
        for preset in ('90', 'uncap'):
            self.assertIn('Sprint Fix (High FPS)', patches.FPS_PRESETS[preset])
        self.assertEqual(len(self.writes('uncap')), 329)


class DebugPatchTests(unittest.TestCase):
    def test_camera_patch_is_optional_and_compatible_with_fps_and_debug_menu(self):
        self.assertEqual(effect_patches({'debug_camera': '0', 'debug_menu': '0'}), [])
        camera = effect_patches({'debug_camera': '1'})
        writes = compile_patches(XML, camera, '01.09', SEGMENTS)
        self.assertGreater(len(writes), 0)
        camera_bytes = {offset+i: byte for offset, data in writes for i, byte in enumerate(data)}
        for patch in ('Uncap FPS++', '60 FPS++', '90 FPS++', 'Restore Debug Menu (READ NOTES)'):
            for offset, data in compile_patches(XML, [patch], '01.09', SEGMENTS):
                for i, byte in enumerate(data):
                    if offset+i in camera_bytes:
                        self.assertEqual(camera_bytes[offset+i], byte, patch)

    def test_debug_menu_checks_both_fonts_but_camera_does_not_need_them(self):
        names = effect_patches({'debug_menu': '1'})
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            validate_patch_requirements(['Restore Debug Camera'], game)
            with self.assertRaisesRegex(ValueError, 'DbgFont14h.ccm.*DbgFont14h.tpf'):
                validate_patch_requirements(names, game)
            font = game / 'dvdroot_ps4/font'
            font.mkdir(parents=True)
            (font / 'DbgFont14h.ccm').write_bytes(b'test')
            (font / 'DbgFont14h.tpf').touch()
            with self.assertRaisesRegex(ValueError, 'DbgFont14h.tpf'):
                validate_patch_requirements(names, game)
            (font / 'DbgFont14h.tpf').write_bytes(b'test')
            validate_patch_requirements(names, game)

    def test_conflicting_enemy_control_patch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'conflicts with Enemy Control'):
            validate_patch_requirements(['Enemy Control', 'Restore Debug Camera'], Path('.'))


EXTERNAL = """<?xml version="1.0"?>
<Patch>
  <TitleID><ID>CUSA03173</ID><ID>CUSA00207</ID></TitleID>
  <Metadata Title="Bloodborne" Name="On" Author="x" PatchVer="1.0" AppVer="01.09" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="bytes" Address="0x00401000" Value="9090"/></PatchList>
  </Metadata>
  <Metadata Title="Bloodborne" Name="Off" Author="x" PatchVer="1.0" AppVer="01.09" AppElf="eboot.bin">
    <PatchList><Line Type="bytes32" Address="0x00402000" Value="0x12345678"/></PatchList>
  </Metadata>
  <Metadata Title="Bloodborne" Name="Mask" Author="x" PatchVer="1.0" AppVer="01.09" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="mask" Value="90 ?? 90" Offset="0"/></PatchList>
  </Metadata>
  <Metadata Title="Bloodborne" Name="Old" Author="x" PatchVer="1.0" AppVer="01.00" AppElf="eboot.bin" isEnabled="true">
    <PatchList><Line Type="bytes" Address="0x00403000" Value="90"/></PatchList>
  </Metadata>
</Patch>"""


class ExternalPatchTests(unittest.TestCase):
    def test_selection_and_unsupported_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'extra.xml').write_text(EXTERNAL)
            (folder / 'other.xml').write_text(EXTERNAL.replace('CUSA03173', 'CUSA99999')
                                              .replace('CUSA00207', 'CUSA99998'))
            (folder / 'broken.xml').write_text('<Patch>')
            found = external_patches(folder)
            self.assertEqual([key for key, _, _ in found],
                             ['extra.xml/On', 'extra.xml/Off', 'extra.xml/Mask'])
            # The file's isEnabled; the mask patch is skipped as a whole.
            writes = compile_external(external_selection(found, None), SEGMENTS)
            self.assertEqual(writes, [(0x1000, bytes.fromhex('9090'))])
            config = folder / 'patches.json'
            config.write_text('{"enabled": ["extra.xml/Off"], "disabled": ["extra.xml/On"]}')
            writes = compile_external(external_selection(found, config), SEGMENTS)
            self.assertEqual(writes, [(0x2000, (0x12345678).to_bytes(4, 'little'))])

    def test_built_in_file_is_not_external(self):
        self.assertEqual(external_patches(XML.parent), [])


class SkipNetworkChoiceTests(unittest.TestCase):
    """The title's PLAY ONLINE / PLAY OFFLINE dialog is skipped by default."""
    ADDRESS = 0x01F39030

    def test_on_unless_the_environment_turns_it_off(self):
        self.assertTrue(patches.skip_network_choice({}))
        self.assertTrue(patches.skip_network_choice({'BB_SKIP_NETWORK_CHOICE': '1'}))
        self.assertFalse(patches.skip_network_choice({'BB_SKIP_NETWORK_CHOICE': '0'}))

    def test_patch_is_one_function_body_and_not_enabled_by_the_file(self):
        meta = [m for m in ET.parse(XML).getroot().iter('Metadata')
                if m.get('Name') == patches.SKIP_NETWORK_CHOICE and m.get('AppVer') == '01.09']
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0].get('isEnabled'), 'false')  # only patches.py's switch turns it on
        writes = compile_patches(XML, [patches.SKIP_NETWORK_CHOICE], '01.09', SEGMENTS)
        self.assertEqual([(offset + EBOOT_BASE, len(data)) for offset, data in writes],
                         [(self.ADDRESS, 46)])
        self.assertEqual(writes[0][1][:4].hex(), '554889e5')  # push rbp; mov rbp, rsp
        self.assertEqual(writes[0][1][-1:], b'\xc3')  # ret

    def compile(self, env):
        """patches.py as run.py runs it, on an ELF with one segment; returns (log, writes)."""
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            header = bytearray(64)
            header[0:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<Q', header, 0x20, 64)
            struct.pack_into('<HH', header, 0x36, 56, 1)
            (out / 'eboot.elf').write_bytes(bytes(header) + struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, 0x6000000, 0x6000000, 0x1000))
            settings = {key: value for key, value in os.environ.items() if key != 'BB_SKIP_NETWORK_CHOICE'}
            settings.update(env)
            run = subprocess.run([sys.executable, str(ROOT / 'scripts/patches.py'), '--out', str(out),
                                  '--fps', '60', '--settings', str(out / 'none.ini'),
                                  '--game-dir', str(out), '--xml', str(XML)],
                                 env=settings, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stderr)
            blob = (out / 'patches.bin').read_bytes()
        count, = struct.unpack_from('<Q', blob, 16)
        position, writes = 24, {}
        for _ in range(count):
            offset, length = struct.unpack_from('<QQ', blob, position)
            writes[offset] = blob[position + 16:position + 16 + length]
            position += 16 + length
        return run.stdout, writes

    def test_main_applies_it_by_default_and_not_with_the_switch_off(self):
        log, writes = self.compile({})
        self.assertIn(patches.SKIP_NETWORK_CHOICE, log)
        self.assertEqual(len(writes[self.ADDRESS - EBOOT_BASE]), 46)
        log, writes = self.compile({'BB_SKIP_NETWORK_CHOICE': '0'})
        self.assertNotIn(patches.SKIP_NETWORK_CHOICE, log)
        self.assertNotIn(self.ADDRESS - EBOOT_BASE, writes)


class IntelTonemapTests(unittest.TestCase):
    def cpuinfo(self, vendor):
        path = Path(tempfile.mkdtemp()) / 'cpuinfo'
        path.write_text(f'processor\t: 0\nvendor_id\t: {vendor}\nmodel name\t: x\n')
        return str(path)

    def test_on_for_intel_off_for_amd(self):
        self.assertTrue(patches.intel_tonemap_fix({}, self.cpuinfo('GenuineIntel')))
        self.assertFalse(patches.intel_tonemap_fix({}, self.cpuinfo('AuthenticAMD')))

    def test_environment_forces_it(self):
        amd, intel = self.cpuinfo('AuthenticAMD'), self.cpuinfo('GenuineIntel')
        self.assertTrue(patches.intel_tonemap_fix({'BB_INTEL_TONEMAP_FIX': '1'}, amd))
        self.assertFalse(patches.intel_tonemap_fix({'BB_INTEL_TONEMAP_FIX': '0'}, intel))

    def test_patch_exists_for_109(self):
        xml = ET.parse(ROOT / 'patches/Bloodborne.xml')
        names = [m.get('Name') for m in xml.iter('Metadata')]
        self.assertIn(patches.INTEL_TONEMAP, names)


if __name__ == '__main__':
    unittest.main()
