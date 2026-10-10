"""Button icons: the file formats behind launcher/bbport_icons.py (DCX, TPF, PS4 tiling, BC7 mode 6)
and the choice of what each icon shows. Nothing here reads game files: the TPF is built in the test.
Run: python -m pytest -q tests/test_button_icons.py"""
import io
import random
import struct
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))

from PIL import Image  # noqa: E402

import bbport_controls as controls  # noqa: E402
import bbport_icons as icons  # noqa: E402


def make_tpf(textures, platform=4):
    """A TPF as the game's common.tpf: 16 byte header, 36 byte entries, UTF-16 names, then the data.
    textures: [(name, width, height, dxgi, data)]."""
    count = len(textures)
    names_at = 16 + 36 * count
    names = b''
    name_offsets = []
    for name, *_rest in textures:
        name_offsets.append(names_at + len(names))
        names += name.encode('utf-16-le') + b'\0\0'
    data_at = (names_at + len(names) + 15) // 16 * 16
    entries, blob = b'', b''
    for (name, width, height, dxgi, data), name_at in zip(textures, name_offsets):
        entries += struct.pack('<II4B2H5I', data_at + len(blob), len(data), 0x66 if dxgi == 98 else 0, 0, 1, 0,
                               width, height, 1, 0xd, name_at, 0, dxgi)
        blob += data
    header = struct.pack('<4sIIBBBB', b'TPF\0', data_at + len(blob) - data_at, count, platform, 3, 1, 0)
    return header + entries + names + b'\0' * (data_at - names_at - len(names)) + blob


def real_layout(extra=()):
    """The 22 glyph textures, in the order and with the sizes of the game's file, plus the two 16 px marks."""
    glyphs = [(name, 32, 32, 98, bytes([i]) * 1024) for i, name in enumerate(icons.GLYPH_NAMES)]
    marks = [('KG_TM_Registered', 16, 16, 98, b'\xee' * 1024), ('KG_TM_UnRegistered', 16, 16, 98, b'\xdd' * 1024)]
    return glyphs[:18] + marks + glyphs[18:] + list(extra)


class DcxTests(unittest.TestCase):
    def test_round_trip_of_a_tpf(self):
        tpf = make_tpf(real_layout())
        packed = icons.dcx_pack(tpf)
        self.assertEqual(packed[:4], b'DCX\0')
        self.assertEqual(icons.dcx_unpack(packed), tpf)

    def test_the_header_names_the_sizes_and_the_deflate_codec(self):
        payload = bytes(range(256)) * 40
        packed = icons.dcx_pack(payload)
        dcs = packed.index(b'DCS\0')
        uncompressed, compressed = struct.unpack('>II', packed[dcs + 4:dcs + 12])
        self.assertEqual(uncompressed, len(payload))
        self.assertEqual(packed[packed.index(b'DCP\0') + 4:packed.index(b'DCP\0') + 8], b'DFLT')
        dca = packed.index(b'DCA\0')
        start = dca + struct.unpack('>I', packed[dca + 4:dca + 8])[0]
        self.assertEqual(len(packed) - start, compressed)
        self.assertEqual(zlib.decompress(packed[start:]), payload)

    def test_refuses_what_is_not_a_deflate_dcx(self):
        packed = icons.dcx_pack(b'x' * 100)
        for bad in (b'', b'XXXX' + packed[4:], packed.replace(b'DFLT', b'KRAK'), packed[:-3]):
            with self.assertRaises(icons.IconError):
                icons.dcx_unpack(bad)


class TpfTests(unittest.TestCase):
    def test_parse_reads_names_sizes_formats_and_offsets(self):
        tpf = make_tpf(real_layout())
        textures = icons.parse_tpf(tpf)
        self.assertEqual([t.name for t in textures][:3], ['KG_Cancel', 'KG_L1', 'KG_L2'])
        first = textures[0]
        self.assertEqual((first.width, first.height, first.size, first.dxgi), (32, 32, 1024, 98))
        self.assertEqual(tpf[first.offset:first.offset + 4], b'\0\0\0\0')
        self.assertEqual(tpf[textures[1].offset], 1)

    def test_a_matching_layout_is_accepted(self):
        found = icons.check_layout(make_tpf(real_layout()))
        self.assertEqual(set(icons.GLYPH_NAMES) - set(found), set())

    def test_a_layout_that_differs_is_refused_with_the_reason(self):
        def refused(textures, **kwargs):
            with self.assertRaises(icons.IconError) as caught:
                icons.check_layout(make_tpf(textures, **kwargs))
            return str(caught.exception)
        layout = real_layout()
        self.assertIn('KG_OK', refused([t for t in layout if t[0] != 'KG_OK']))
        wrong_size = [(n, 64, 64, d, b'\0' * 4096) if n == 'KG_OK' else (n, w, h, d, data) for n, w, h, d, data in layout]
        self.assertIn('KG_OK', refused(wrong_size))
        wrong_format = [(n, w, h, 71, data[:512]) if n == 'KG_L1' else (n, w, h, d, data) for n, w, h, d, data in layout]
        self.assertIn('KG_L1', refused(wrong_format))
        self.assertIn('PS4', refused(layout, platform=3))

    def test_a_texture_pointing_past_the_end_is_refused_as_outside_the_file(self):
        layout = real_layout([('MENU_Common_00001', 32, 32, 98, bytes(1024))])  # not a glyph: only the bounds apply
        tpf = bytearray(make_tpf(layout))
        struct.pack_into('<I', tpf, 16 + 36 * (len(layout) - 1), len(tpf) - 100)  # its data offset: 1024 bytes will not fit
        with self.assertRaises(icons.IconError) as caught:
            icons.check_layout(bytes(tpf))
        self.assertIn('MENU_Common_00001 lies outside', str(caught.exception))

    def test_patching_a_texture_keeps_the_file_the_same_size_and_the_rest_untouched(self):
        tpf = make_tpf(real_layout())
        found = icons.check_layout(tpf)
        patched = icons.patch_texture(tpf, found['KG_OK'], b'\x5a' * 1024)
        self.assertEqual(len(patched), len(tpf))
        for name in icons.GLYPH_NAMES:
            texture = found[name]
            before, after = tpf[texture.offset:texture.offset + 1024], patched[texture.offset:texture.offset + 1024]
            self.assertEqual(after == b'\x5a' * 1024, name == 'KG_OK', name)
            if name != 'KG_OK':
                self.assertEqual(before, after, name)
        with self.assertRaises(icons.IconError):
            icons.patch_texture(tpf, found['KG_OK'], b'\0' * 1000)


class TilingTests(unittest.TestCase):
    def test_the_micro_tile_is_morton_ordered(self):
        """k-th block of an 8x8 micro tile: x takes the even bits of k, y the odd ones (verified on the game's
        glyphs, whose shapes only come out right this way)."""
        order = icons.MORTON_8X8
        self.assertEqual(len(order), 64)
        self.assertEqual(order[:4], [(0, 0), (1, 0), (0, 1), (1, 1)])
        self.assertEqual(order[4], (2, 0))
        self.assertEqual(order[8], (0, 2))
        self.assertEqual(order[16], (4, 0))
        self.assertEqual(order[32], (0, 4))
        self.assertEqual(order[63], (7, 7))
        self.assertEqual(len(set(order)), 64)

    def test_micro_tiles_follow_each_other_in_raster_order(self):
        """1D thin tiling: a 16x8 block image is two micro tiles, the left one first."""
        bw, bh = 16, 8
        raster = [bytes([x, y]) * 8 for y in range(bh) for x in range(bw)]
        tiled = icons.tile_blocks(raster, bw, bh)
        self.assertEqual(len(tiled), len(raster) * 16)
        self.assertEqual(tiled[:16], bytes([0, 0]) * 8)
        self.assertEqual(tiled[16:32], bytes([1, 0]) * 8)
        self.assertEqual(tiled[32:48], bytes([0, 1]) * 8)
        self.assertEqual(tiled[64 * 16:64 * 16 + 16], bytes([8, 0]) * 8)  # first block of the second tile

    def test_tile_and_untile_are_exact_inverses(self):
        rng = random.Random(1)
        for bw, bh in ((8, 8), (16, 8), (8, 24), (32, 16), (256, 128)):
            raster = [rng.randbytes(16) for _ in range(bw * bh)]
            self.assertEqual(icons.untile_blocks(icons.tile_blocks(raster, bw, bh), bw, bh), raster, (bw, bh))

    def test_sizes_that_are_not_whole_micro_tiles_are_refused(self):
        with self.assertRaises(icons.IconError):
            icons.tile_blocks([b'\0' * 16] * 20, 5, 4)
        with self.assertRaises(icons.IconError):
            icons.untile_blocks(b'\0' * 100, 8, 8)


def pillow_decode(blocks, width, height):
    """What Pillow's own BC7 decoder makes of raster-ordered blocks (the independent oracle)."""
    header = b'DDS ' + struct.pack('<7I', 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, height, width, len(blocks), 0, 1)
    header += b'\0' * 44 + struct.pack('<2I4s5I', 32, 4, b'DX10', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x1000, 0, 0, 0, 0)
    header += struct.pack('<5I', 98, 3, 0, 1, 0)
    return Image.open(io.BytesIO(header + blocks)).convert('RGBA')


def flat(image):
    return list(getattr(image, "get_flattened_data", image.getdata)())


def max_error(a, b):
    """The largest channel difference; the colour of a fully transparent pixel is not compared (it is never seen)."""
    return max(abs(x - y) for p, q in zip(a, b) for x, y in zip(p[3:] if p[3] == 0 else p, q[3:] if p[3] == 0 else q))


def mean_error(a, b):
    return sum(abs(x - y) for p, q in zip(a, b) for x, y in zip(p, q)) / (len(a) * 4)


class Bc7Tests(unittest.TestCase):
    def test_a_flat_block_is_within_one(self):
        """The parity bit of an end is shared by its four channels, so a colour whose channels differ in parity
        (the dark disc, alpha 255) is off by one in some channel; one with equal parities is exact."""
        for color in ((0, 0, 0, 0), (255, 255, 255, 255), (47, 85, 237, 255), (195, 161, 113, 128), (1, 2, 3, 254),
                      (46, 46, 46, 255)):
            block = icons.bc7_encode_block([color] * 16)
            self.assertEqual(len(block), 16)
            self.assertLessEqual(max_error([color] * 16, icons.bc7_decode_block(block)), 1, color)
        for color in ((0, 0, 0, 0), (255, 255, 255, 255), (47, 85, 237, 255)):
            self.assertEqual(icons.bc7_decode_block(icons.bc7_encode_block([color] * 16)), [color] * 16, color)

    def test_a_block_of_two_colours_is_exact(self):
        rng = random.Random(7)
        for _ in range(40):
            one = tuple(rng.randrange(256) for _ in range(4))
            two = tuple(rng.randrange(256) for _ in range(4))
            pixels = [one if rng.random() < 0.5 else two for _ in range(16)]
            decoded = icons.bc7_decode_block(icons.bc7_encode_block(pixels))
            self.assertLessEqual(max_error(pixels, decoded), 1, (one, two))

    def test_a_block_whose_colours_differ_but_whose_channel_sum_is_constant_still_encodes(self):
        """Red next to green: R+G+B+A is the same for both, so the spread is orthogonal to (1, 1, 1, 1), where the
        search for the main axis used to start, and the axis came out empty."""
        rng = random.Random(5)
        red, green = (255, 0, 0, 255), (0, 255, 0, 255)
        for _ in range(20):
            pixels = [red if rng.random() < 0.5 else green for _ in range(16)]
            pixels[0], pixels[1] = red, green
            decoded = icons.bc7_decode_block(icons.bc7_encode_block(pixels))
            self.assertLessEqual(max_error(pixels, decoded), 1)
        ramp = [(round(255 * i / 15), round(255 - 255 * i / 15), 0, 255) for i in range(16)]
        decoded = icons.bc7_decode_block(icons.bc7_encode_block(ramp))
        self.assertLessEqual(max_error(ramp, decoded), 6)

    def test_a_gradient_stays_close(self):
        """A smooth ramp between two colours (a lit edge of a key) is what the 16 palette steps are for."""
        pixels = [(30 + 12 * (x + y), 220 - 20 * (x + y), 90 + 5 * (x + y), 255) for y in range(4) for x in range(4)]
        decoded = icons.bc7_decode_block(icons.bc7_encode_block(pixels))
        self.assertLessEqual(mean_error(pixels, decoded), 1.5)
        self.assertLessEqual(max_error(pixels, decoded), 6)

    def test_the_decoder_agrees_with_pillows(self):
        """Encoded blocks put through the same bits by Pillow's decoder: the layout is the format's."""
        rng = random.Random(3)
        blocks, pixels = [], []
        for _ in range(64):
            base = [rng.randrange(256) for _ in range(4)]
            block = [tuple(min(255, max(0, c + rng.randrange(-30, 31))) for c in base) for _ in range(16)]
            blocks.append(icons.bc7_encode_block(block))
        raster = b''.join(blocks)
        theirs = pillow_decode(raster, 32, 32)
        for index, block in enumerate(blocks):
            ours = icons.bc7_decode_block(block)
            bx, by = (index % 8) * 4, (index // 8) * 4
            for i, pixel in enumerate(ours):
                self.assertEqual(theirs.getpixel((bx + i % 4, by + i // 4)), pixel, (index, i))

    def test_the_first_index_never_needs_its_top_bit(self):
        """The anchor index has 3 bits: a block whose first pixel is the far end is written with the ends swapped."""
        light, dark = (250, 240, 200, 255), (10, 10, 10, 255)
        pixels = [light] + [dark] * 15
        block = icons.bc7_encode_block(pixels)
        decoded = icons.bc7_decode_block(block)
        self.assertLessEqual(max_error(pixels, decoded), 1)
        self.assertLessEqual(max_error([pillow_decode(block, 4, 4).getpixel((0, 0))], [light]), 1)

    def test_an_image_encodes_to_the_blocks_of_its_size(self):
        image = Image.new('RGBA', (32, 32), (46, 46, 46, 255))
        data = icons.encode_image(image)
        self.assertEqual(len(data), 1024)
        round_trip = icons.decode_image(data, 32, 32)
        self.assertEqual(round_trip.size, (32, 32))
        self.assertLessEqual(max_error([round_trip.getpixel((5, 5))], [(46, 46, 46, 255)]), 1)

    def test_a_glyph_survives_the_tiled_round_trip(self):
        image = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        for x in range(8, 24):
            for y in range(8, 24):
                image.putpixel((x, y), (200, 40, 40, 255))
        decoded = icons.decode_image(icons.encode_image(image), 32, 32)
        self.assertLessEqual(max_error([decoded.getpixel((16, 16))], [(200, 40, 40, 255)]), 1)
        self.assertEqual(decoded.getpixel((2, 2))[3], 0)
        self.assertLessEqual(max_error(flat(image), flat(decoded)), 1)


def first_default(name):
    return controls.split_binding(controls.default_binding('key', name))[0]


class PlanTests(unittest.TestCase):
    """What each glyph texture shows: its shape names an input, and the input's first binding is drawn."""

    # Verified by looking at the decoded textures: the cross is blue, the circle red, the square magenta and the
    # triangle green, and the engine picks OK/Cancel by name per region, so every texture is mapped by its shape.
    SHAPES = {'KG_Cancel': 'cross', 'KG_OK': 'circle', 'KG_R_L': 'square', 'KG_R_U': 'triangle',
              'KG_L1': 'l1', 'KG_L2': 'l2', 'KG_L3': 'l3', 'KG_R1': 'r1', 'KG_R2': 'r2', 'KG_R3': 'r3',
              'KG_Start': 'options', 'KG_TP_L': 'touchpad', 'KG_TP_R': 'touchpad_right'}

    def test_the_shape_table_names_the_input_of_every_texture(self):
        self.assertEqual(set(icons.SHAPES), set(icons.GLYPH_NAMES))
        for texture, name in self.SHAPES.items():
            self.assertEqual(icons.SHAPES[texture], (name,), texture)
        self.assertEqual(icons.SHAPES['KG_L_U'], ('up',))
        self.assertEqual(icons.SHAPES['KG_L_D'], ('down',))
        self.assertEqual(icons.SHAPES['KG_L_LR'], ('left', 'right'))
        self.assertEqual(icons.SHAPES['KG_L_UD'], ('up', 'down'))
        self.assertEqual(icons.SHAPES['KG_L_UDLR'], ('up', 'left', 'down', 'right'))
        known = {row[0] for row in controls.CONTROLS}
        for texture, names in icons.SHAPES.items():
            if texture not in ('KG_LS', 'KG_RS'):
                self.assertLessEqual(set(names), known, texture)

    def test_xbox_icons_follow_the_pad_bindings(self):
        plan = icons.plan('xbox', {})
        self.assertEqual({t: plan[t].tokens for t in ('KG_Cancel', 'KG_OK', 'KG_R_L', 'KG_R_U')},
                         {'KG_Cancel': [('pad', 'a')], 'KG_OK': [('pad', 'b')], 'KG_R_L': [('pad', 'x')],
                          'KG_R_U': [('pad', 'y')]})
        for texture, button in (('KG_L1', 'leftshoulder'), ('KG_R1', 'rightshoulder'), ('KG_L2', 'lefttrigger'),
                                ('KG_R2', 'righttrigger'), ('KG_L3', 'leftstick'), ('KG_R3', 'rightstick'),
                                ('KG_Start', 'start'), ('KG_TP_L', 'back'), ('KG_L_U', 'dpup'), ('KG_L_D', 'dpdown')):
            self.assertEqual(plan[texture].tokens, [('pad', button)], texture)
        self.assertEqual(plan['KG_TP_R'].tokens, [('pad', 'back')])  # nothing on the pad opens it: View, as the left half
        self.assertEqual(plan['KG_LS'].tokens, [('stick', 'left')])
        self.assertEqual(plan['KG_RS'].tokens, [('stick', 'right')])
        self.assertEqual(plan['KG_L_UDLR'].tokens, [('pad', 'dpup'), ('pad', 'dpleft'), ('pad', 'dpdown'),
                                                    ('pad', 'dpright')])

    def test_a_saved_pad_binding_moves_the_xbox_icon(self):
        plan = icons.plan('xbox', {'pad.cross': 'y, a', 'pad.options': ''})
        self.assertEqual(plan['KG_Cancel'].tokens, [('pad', 'y')])
        self.assertEqual(plan['KG_Start'].tokens, [('pad', 'start')])  # unbound: the default button, never blank

    def test_keyboard_icons_show_the_first_bound_key(self):
        plan = icons.plan('keyboard', {})
        for texture, name in self.SHAPES.items():
            tokens = plan[texture].tokens
            first = first_default(name)
            mods, base = icons.parse_key(first)
            self.assertEqual(tokens, [('key', mods, base)], texture)
        self.assertEqual(plan['KG_Cancel'].tokens, [('key', (), 'E')])
        self.assertEqual(plan['KG_L2'].tokens, [('key', ('Shift',), 'Mouse Right')])  # the first of two bindings
        self.assertEqual(plan['KG_R1'].tokens, [('key', (), 'Mouse Left')])
        self.assertEqual(plan['KG_L_UDLR'].tokens, [('key', (), 'Up'), ('key', (), 'Left'), ('key', (), 'Down'),
                                                    ('key', (), 'Right')])

    def test_the_left_stick_is_the_move_keys_and_the_right_one_the_mouse_or_the_look_keys(self):
        plan = icons.plan('keyboard', {})
        self.assertEqual(plan['KG_LS'].tokens, [('key', (), 'W'), ('key', (), 'A'), ('key', (), 'S'), ('key', (), 'D')])
        self.assertEqual(plan['KG_RS'].tokens, [('mouse',)])
        look = icons.plan('keyboard', {'mouse_camera': '0'})['KG_RS'].tokens
        self.assertEqual(look, [('key', (), 'I'), ('key', (), 'J'), ('key', (), 'K'), ('key', (), 'L')])
        moved = icons.plan('keyboard', {'key.move_up': 'Up', 'key.move_left': ''})['KG_LS'].tokens
        self.assertEqual(moved[:2], [('key', (), 'Up'), ('key', (), 'A')])  # an unbound one shows its default

    def test_a_saved_key_binding_wins_and_an_empty_one_shows_the_default(self):
        plan = icons.plan('keyboard', {'key.cross': 'X, Return', 'key.circle': '', 'key.l3': 'Shift+Wheel Up'})
        self.assertEqual(plan['KG_Cancel'].tokens, [('key', (), 'X')])
        self.assertEqual(plan['KG_OK'].tokens, [('key', (), 'Space')])
        self.assertEqual(plan['KG_L3'].tokens, [('key', ('Shift',), 'Wheel Up')])

    def test_no_glyph_is_ever_blank(self):
        """The default bindings leave the right touchpad without a pad button, and a player can clear any binding:
        every glyph of both sets still draws something (a fallback, never an empty box)."""
        cleared = {f'{kind}.{name}': '' for name, *_rest in controls.CONTROLS for kind in ('key', 'pad')}
        for icon_set in ('xbox', 'keyboard'):
            for label, ini in (('defaults', {}), ('everything cleared', cleared)):
                for texture, glyph in icons.plan(icon_set, ini).items():
                    self.assertNotIn(('none',), glyph.tokens, (icon_set, label, texture))
                    image = icons.render_glyph(icon_set, glyph)
                    solid = sum(1 for pixel in flat(image) if pixel[3] > 128)
                    bright = sum(1 for pixel in flat(image) if pixel[3] > 128 and max(pixel[:3]) > 150)
                    self.assertGreater(solid, 100, (icon_set, label, texture))
                    self.assertGreater(bright, 6, (icon_set, label, texture, 'no symbol, only a box'))

    def test_the_xbox_right_touchpad_is_the_view_glyph_like_the_left_one(self):
        glyphs = icons.build_glyphs('xbox', {})
        self.assertEqual(flat(glyphs['KG_TP_R']), flat(glyphs['KG_TP_L']))

    def test_the_keyboard_options_key_is_a_level_wide_cap(self):
        """Esc is written across the cap, not turned on its side in the narrow footprint of the original."""
        glyph = icons.build_glyphs('keyboard', {})['KG_Start']
        solid = glyph.getchannel('A').point(lambda v: 255 if v > 128 else 0).getbbox()
        self.assertGreaterEqual(solid[2] - solid[0], 28)
        legend = Image.new('L', glyph.size, 0)
        legend.putdata([255 if p[3] > 128 and max(p[:3]) > 190 else 0 for p in flat(glyph)])
        light = legend.getbbox()
        self.assertIsNotNone(light)
        self.assertGreater(light[2] - light[0], light[3] - light[1])  # the legend is wider than tall

    def test_a_key_binding_is_split_into_its_modifiers_and_the_key(self):
        self.assertEqual(icons.parse_key('Shift+Mouse Right'), (('Shift',), 'Mouse Right'))
        self.assertEqual(icons.parse_key('Ctrl+Alt+Left Shift'), (('Ctrl', 'Alt'), 'Left Shift'))
        self.assertEqual(icons.parse_key('Space'), ((), 'Space'))
        self.assertEqual(icons.parse_key('Wheel Down'), ((), 'Wheel Down'))

    def test_long_key_names_get_short_legends(self):
        legends = {'Space': 'Spc', 'Left Shift': 'Shft', 'Right Shift': 'Shft', 'Left Ctrl': 'Ctrl', 'Left Alt': 'Alt',
                   'Escape': 'Esc', 'Tab': 'Tab', 'Return': 'Ent', 'Backspace': 'Bksp', 'PageDown': 'PgDn',
                   'CapsLock': 'Caps', 'E': 'E', 'F5': 'F5', 'Keypad 5': 'N5', 'Left GUI': 'Win', '/': '/'}
        for name, legend in legends.items():
            self.assertEqual(icons.legend_for(name), legend, name)
        self.assertLessEqual(len(icons.legend_for('Some Unknown Key')), 4)


XBOX_GUID = '030000005e0400008e02000000007200'  # Microsoft, an Xbox 360 pad
DS4_GUID = '030000004c050000cc09000000007200'  # Sony, a DualShock 4
OTHER_GUID = '03000000c82d00000160000000007200'  # 8BitDo


class AutoResolutionTests(unittest.TestCase):
    """The icon set the launcher picks at Play for button_icons=auto: from the pad the game would use."""

    def test_a_choice_that_is_not_auto_is_kept(self):
        for choice in ('playstation', 'xbox', 'keyboard'):
            self.assertEqual(controls.resolve_icon_set(choice, [(XBOX_GUID, 'Xbox')]), choice)
            self.assertEqual(controls.resolve_icon_set(choice, []), choice)

    def test_no_pad_means_keyboard_and_mouse(self):
        self.assertEqual(controls.resolve_icon_set('auto', []), 'keyboard')

    def test_an_xbox_pad_means_xbox_icons_and_a_playstation_pad_playstation_ones(self):
        self.assertEqual(controls.resolve_icon_set('auto', [(XBOX_GUID, 'Xbox 360 Controller')]), 'xbox')
        self.assertEqual(controls.resolve_icon_set('auto', [(DS4_GUID, 'PS4 Controller')]), 'playstation')

    def test_the_pad_is_known_by_its_vendor_or_by_its_name(self):
        self.assertEqual(controls.pad_family(XBOX_GUID, 'Anything'), 'xbox')
        self.assertEqual(controls.pad_family(DS4_GUID, 'Anything'), 'playstation')
        self.assertEqual(controls.pad_family('xinput', 'XInput Controller'), 'xbox')
        for name in ('Wireless Controller', 'DualSense Wireless Controller', 'PS5 Controller', 'Sony Interactive'):
            self.assertEqual(controls.pad_family('', name), 'playstation', name)
        for name in ('Xbox One Controller', 'Controller (XBOX 360 For Windows)', 'Microsoft X-Box pad'):
            self.assertEqual(controls.pad_family('', name), 'xbox', name)

    def test_another_pad_gets_the_xbox_layout_its_buttons_are_named_by(self):
        self.assertEqual(controls.pad_family(OTHER_GUID, '8BitDo Pro 2'), 'xbox')
        self.assertEqual(controls.resolve_icon_set('auto', [(OTHER_GUID, '8BitDo Pro 2')]), 'xbox')

    def test_the_pad_the_player_chose_decides_else_the_first_one(self):
        pads = [(DS4_GUID, 'PS4 Controller'), (XBOX_GUID, 'Xbox 360 Controller')]
        self.assertEqual(controls.resolve_icon_set('auto', pads), 'playstation')
        self.assertEqual(controls.resolve_icon_set('auto', pads, XBOX_GUID), 'xbox')
        self.assertEqual(controls.resolve_icon_set('auto', pads, 'not connected'), 'playstation')

    def test_when_the_pads_cannot_be_listed_the_game_keeps_its_own_icons(self):
        self.assertEqual(controls.resolve_icon_set('auto', None), 'playstation')

    def test_the_choices_are_the_four_the_setting_takes(self):
        self.assertEqual([value for value, _text in controls.ICON_CHOICES], ['auto', 'playstation', 'xbox', 'keyboard'])
        self.assertEqual(controls.ICON_DEFAULT, 'auto')


class LayerTests(unittest.TestCase):
    """The cache of generated files: keyed by the source, the set and the bindings, reused when nothing changed."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'game'
        (self.game / 'dvdroot_ps4/menu').mkdir(parents=True)
        self.source = self.game / icons.COMMON_TPF
        self.source.write_bytes(icons.dcx_pack(make_tpf(real_layout())))
        self.cache = self.root / 'out'

    def layer(self, icon_set='keyboard', ini=None):
        return icons.ensure_layer(self.game, self.cache, icon_set, ini or {})

    def test_the_layer_holds_the_generated_file_under_the_games_own_path(self):
        folder, built = self.layer()
        self.assertTrue(built)
        generated = folder / icons.COMMON_TPF
        self.assertTrue(generated.is_file())
        found = icons.check_layout(icons.dcx_unpack(generated.read_bytes()))
        original = icons.dcx_unpack(self.source.read_bytes())
        patched = icons.dcx_unpack(generated.read_bytes())
        cancel = found['KG_Cancel']
        self.assertNotEqual(patched[cancel.offset:cancel.offset + 1024], original[cancel.offset:cancel.offset + 1024])
        self.assertEqual(len(patched), len(original))

    def test_the_source_file_is_never_written(self):
        before = self.source.read_bytes()
        self.layer()
        self.assertEqual(self.source.read_bytes(), before)

    def test_the_hash_of_the_source_is_recorded(self):
        import hashlib
        import json
        folder, _built = self.layer()
        meta = json.loads((folder / 'icons.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(meta['icon_set'], 'keyboard')

    def test_nothing_changed_reuses_the_file(self):
        folder, built = self.layer()
        mtime = (folder / icons.COMMON_TPF).stat().st_mtime_ns
        again, built_again = self.layer()
        self.assertTrue(built)
        self.assertFalse(built_again)
        self.assertEqual(again, folder)
        self.assertEqual((again / icons.COMMON_TPF).stat().st_mtime_ns, mtime)

    def test_another_set_binding_or_source_makes_another_file(self):
        folder, _ = self.layer()
        self.assertNotEqual(self.layer('xbox')[0], folder)
        self.assertNotEqual(self.layer('keyboard', {'key.cross': 'X'})[0], folder)
        # a binding the icons do not show leaves the file as it is
        self.assertEqual(self.layer('keyboard', {'key.cross': 'E, Return', 'mouse_sensitivity': '2.00'})[0], folder)
        self.source.write_bytes(icons.dcx_pack(make_tpf(real_layout(), platform=4) + b'\0' * 16))
        self.assertNotEqual(self.layer()[0], folder)

    def test_a_cache_with_a_missing_file_is_built_again(self):
        folder, _ = self.layer()
        (folder / icons.COMMON_TPF).unlink()
        again, built = self.layer()
        self.assertTrue(built)
        self.assertTrue((again / icons.COMMON_TPF).is_file())

    def test_old_files_are_pruned(self):
        from unittest import mock
        with mock.patch.object(icons, 'KEEP_CACHED', 2):
            for index in range(4):
                self.layer('keyboard', {'key.cross': chr(ord('A') + index)})
        left = list((self.cache / 'icons').iterdir())
        self.assertEqual(len(left), 2)
        self.assertIn(self.layer('keyboard', {'key.cross': 'D'})[0], left)  # the newest stays

    def test_a_game_that_does_not_match_is_refused_with_the_reason(self):
        self.source.write_bytes(icons.dcx_pack(make_tpf([t for t in real_layout() if t[0] != 'KG_OK'])))
        with self.assertRaises(icons.IconError) as caught:
            self.layer()
        self.assertIn('KG_OK', str(caught.exception))
        self.assertFalse((self.cache / 'icons').exists() and list((self.cache / 'icons').iterdir()))

    def test_a_corrupt_file_is_refused_by_the_real_layer_code_with_an_icon_error(self):
        good = icons.dcx_pack(make_tpf(real_layout()))
        broken = bytearray(good)
        broken[0x60:0x80] = b'\xff' * 32  # inside the zlib data
        for data in (bytes(broken), good[:0x50], b'DCX\0' + b'\0' * 100, icons.dcx_pack(b'TPF\0' + b'\xff' * 40)):
            self.source.write_bytes(data)
            with self.assertRaises(icons.IconError):
                self.layer()
        self.assertFalse((self.cache / 'icons').exists() and list((self.cache / 'icons').iterdir()))

    def test_an_unexpected_failure_while_reading_the_file_is_an_icon_error_too(self):
        from unittest import mock
        for error in (IndexError('x'), KeyError('x'), struct.error('x'), zlib.error('x')):
            with mock.patch.object(icons, 'check_layout', side_effect=error):
                with self.assertRaises(icons.IconError):
                    self.layer()

    def test_a_game_without_the_file_is_refused(self):
        self.source.unlink()
        with self.assertRaises(icons.IconError):
            self.layer()


def atlas_image(entries, size, margin_noise=False):
    """A stand-in for a menu atlas: transparent, a bright block of other art, and a flat disc where the game
    has a baked button (the discs of the given ATLAS_ICONS entries)."""
    from PIL import ImageDraw
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 63, 63), fill=(30, 200, 90, 255))  # some other art, far from every button
    for _rect, box, _glyph in entries:
        draw.ellipse((box[0], box[1], box[2] - 1, box[3] - 1), fill=(60, 60, 60, 255))
    if margin_noise:
        entry = entries[0]
        draw.rectangle((entry[0][0], entry[0][1], entry[0][0] + 3, entry[0][1] + 3), fill=(255, 0, 0, 255))
    return image


class AtlasTests(unittest.TestCase):
    """The buttons baked into the menu atlases are redrawn too."""
    NAME = 'MENU_Common_00091'

    @classmethod
    def setUpClass(cls):
        cls.entries = icons.ATLAS_ICONS[cls.NAME]
        cls.atlas = icons.encode_image(atlas_image(cls.entries, (1024, 512)))  # slow in pure Python: once
        cls.noisy = icons.encode_image(atlas_image(cls.entries, (1024, 512), margin_noise=True))
        cls.blank = icons.encode_image(Image.new('RGBA', (1024, 512), (0, 0, 0, 0)))
        cls.tpf = make_tpf(real_layout([(cls.NAME, 1024, 512, 98, cls.atlas)]))

    def patched(self, icon_set='xbox', tpf=None):
        tpf = tpf or self.tpf
        glyphs = icons.build_glyphs(icon_set, {})
        out = icons.patch_glyphs(tpf, glyphs)
        report = icons.patch_atlases(out, glyphs)
        return report

    def atlas_of(self, tpf):
        texture = next(t for t in icons.parse_tpf(tpf) if t.name == self.NAME)
        return tpf[texture.offset:texture.offset + texture.size]

    def test_the_table_lists_the_baked_buttons_with_their_glyph_and_a_whole_block_rectangle(self):
        for name, entries in icons.ATLAS_ICONS.items():
            self.assertIn(name, ('MENU_Common_00091', 'MENU_Common_00092'))
            for rect, box, glyph in entries:
                self.assertIn(glyph, icons.GLYPH_NAMES)
                self.assertEqual([v % 4 for v in rect], [0, 0, 0, 0], rect)  # whole BC7 blocks
                self.assertTrue(rect[0] <= box[0] and rect[1] <= box[1] and box[2] <= rect[2] and box[3] <= rect[3])
        shown = {glyph for entries in icons.ATLAS_ICONS.values() for _r, _b, glyph in entries}
        self.assertEqual(shown, {'KG_OK', 'KG_Cancel', 'KG_L3', 'KG_R_U'})  # circle, cross, L3, triangle

    def test_the_rectangles_of_one_atlas_do_not_overlap(self):
        for entries in icons.ATLAS_ICONS.values():
            rects = [rect for rect, _box, _glyph in entries]
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b))

    def test_every_baked_button_is_redrawn_and_the_rest_of_the_atlas_is_byte_for_byte_the_same(self):
        glyphs = icons.build_glyphs('xbox', {})
        tpf = icons.dcx_unpack(icons.dcx_pack(self.tpf))
        report = icons.patch_atlases(bytearray(tpf), glyphs)
        self.assertEqual([done for _name, _rect, done in report], [True] * len(self.entries))
        patched = bytearray(tpf)
        icons.patch_atlases(patched, glyphs)
        before = icons.untile_blocks(self.atlas_of(tpf), 256, 128)
        after = icons.untile_blocks(self.atlas_of(bytes(patched)), 256, 128)
        changed = {i for i, (a, b) in enumerate(zip(before, after)) if a != b}
        inside = set()
        for rect, _box, _glyph in self.entries:
            for by in range(rect[1] // 4, rect[3] // 4):
                for bx in range(rect[0] // 4, rect[2] // 4):
                    inside.add(by * 256 + bx)
        self.assertTrue(changed)
        self.assertLessEqual(changed, inside)
        self.assertEqual(len(bytes(patched)), len(tpf))

    def test_the_new_button_is_the_glyph_of_the_set(self):
        glyphs = icons.build_glyphs('xbox', {})
        patched = bytearray(self.tpf)
        icons.patch_atlases(patched, glyphs)
        image = icons.decode_image(self.atlas_of(bytes(patched)), 1024, 512)
        for rect, box, glyph in self.entries:
            expected = glyphs[glyph].crop((3, 3, 29, 29)).resize((box[2] - box[0], box[3] - box[1]))
            centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
            want = expected.getpixel((expected.width // 2, expected.height // 2))
            got = image.getpixel(centre)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(want, got)), 40, (glyph, want, got))
            self.assertEqual(image.getpixel((rect[0], rect[1]))[3], 0)  # the corner of the rectangle is clear

    def test_a_rectangle_that_holds_other_art_is_left_alone(self):
        tpf = make_tpf(real_layout([(self.NAME, 1024, 512, 98, self.noisy)]))
        patched = bytearray(tpf)
        report = icons.patch_atlases(patched, icons.build_glyphs('xbox', {}))
        self.assertEqual([done for _name, _rect, done in report], [False] + [True] * (len(self.entries) - 1))

    def test_an_atlas_that_is_not_a_button_sheet_is_left_alone(self):
        tpf = make_tpf(real_layout([(self.NAME, 1024, 512, 98, self.blank)]))
        patched = bytearray(tpf)
        report = icons.patch_atlases(patched, icons.build_glyphs('keyboard', {}))
        self.assertEqual({done for _n, _r, done in report}, {False})
        self.assertEqual(bytes(patched), tpf)

    def test_a_file_without_the_atlases_is_fine(self):
        patched = bytearray(make_tpf(real_layout()))
        self.assertEqual(icons.patch_atlases(patched, icons.build_glyphs('xbox', {})), [])

    def test_generate_redraws_the_atlas_buttons_too(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'common.tpf.dcx'
            source.write_bytes(icons.dcx_pack(self.tpf))
            destination = Path(folder) / 'out/dvdroot_ps4/menu/common.tpf.dcx'
            icons.generate(source, destination, 'keyboard', {})
            made = icons.dcx_unpack(destination.read_bytes())
        self.assertNotEqual(self.atlas_of(made), self.atlas)


if __name__ == '__main__':
    unittest.main()
