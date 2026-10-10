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

    def test_a_texture_pointing_past_the_end_is_refused(self):
        tpf = bytearray(make_tpf(real_layout()))
        struct.pack_into('<I', tpf, 16, len(tpf) - 100)  # the first entry's data offset
        with self.assertRaises(icons.IconError):
            icons.check_layout(bytes(tpf))

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
        self.assertEqual(plan['KG_TP_R'].tokens, [('none',)])  # nothing on the pad opens that half
        self.assertEqual(plan['KG_LS'].tokens, [('stick', 'left')])
        self.assertEqual(plan['KG_RS'].tokens, [('stick', 'right')])
        self.assertEqual(plan['KG_L_UDLR'].tokens, [('pad', 'dpup'), ('pad', 'dpleft'), ('pad', 'dpdown'),
                                                    ('pad', 'dpright')])

    def test_a_saved_pad_binding_moves_the_xbox_icon(self):
        plan = icons.plan('xbox', {'pad.cross': 'y, a', 'pad.options': ''})
        self.assertEqual(plan['KG_Cancel'].tokens, [('pad', 'y')])
        self.assertEqual(plan['KG_Start'].tokens, [('none',)])

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
        self.assertEqual(moved[:2], [('key', (), 'Up'), ('none',)])

    def test_a_saved_key_binding_wins_and_an_empty_one_leaves_the_icon_blank(self):
        plan = icons.plan('keyboard', {'key.cross': 'X, Return', 'key.circle': '', 'key.l3': 'Shift+Wheel Up'})
        self.assertEqual(plan['KG_Cancel'].tokens, [('key', (), 'X')])
        self.assertEqual(plan['KG_OK'].tokens, [('none',)])
        self.assertEqual(plan['KG_L3'].tokens, [('key', ('Shift',), 'Wheel Up')])

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


if __name__ == '__main__':
    unittest.main()
