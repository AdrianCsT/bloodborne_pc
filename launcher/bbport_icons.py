# SPDX-License-Identifier: GPL-2.0-or-later
"""Button icons for the Bloodborne port: the game draws PlayStation glyphs, and this module redraws them as
Xbox buttons or as the keys and mouse buttons the player has bound. It exists because the glyphs are
textures inside menu/common.tpf.dcx of the player's own game copy, which this project never ships: the
launcher calls generate() at Play, from that file, and the result goes into the mods overlay (the original
file stays untouched).

The file is a DCX (DFLT: zlib) wrapping a TPF (platform 4, PS4) of 65 textures. The glyphs are 20 textures
named KG_* of 32x32 pixels, BC7 without a GNF header, one 8x8-block micro tile each (plus two 16 px
trademark marks, left alone). The big atlases (MENU_Common_00091 and 00092) tile as 1D thin: micro tiles in
raster order, Morton order inside each. The pixels are re-encoded as BC7 mode 6 by a pure Python encoder: this
module needs the standard library and Pillow only (no numpy, which the frozen launcher does not carry).
The module has no --selftest: its checks are tests/test_button_icons.py, which need no game files.

Run `python launcher/bbport_icons.py <common.tpf.dcx> <out folder> [xbox|keyboard ...]` to write each variant
under <out folder>/<set>/ with PNGs of its KG_* glyphs (also scaled x4), for a look at the art; the tests
(tests/test_button_icons.py) need no game files."""
import io
import struct
import zlib
from collections import namedtuple

from PIL import Image, ImageChops, ImageDraw

import bbport_controls


class IconError(Exception):
    """The input is not the file this module knows, or a request cannot be met; the text is for the player."""


# --- DCX -----------------------------------------------------------------------------------------------
DCX_HEADER = struct.pack('>4sIIIII', b'DCX\0', 0x10000, 0x18, 0x24, 0x44, 0x4C)
DCX_DCP = struct.pack('>4s4sIIIIII', b'DCP\0', b'DFLT', 0x20, 0x09000000, 0, 0, 0, 0x00010100)
DCX_DATA_AT = 0x4C


def dcx_unpack(data):
    """The payload of a DFLT DCX file."""
    if data[:4] != b'DCX\0' or len(data) < DCX_DATA_AT or data[0x18:0x1C] != b'DCS\0':
        raise IconError('not a DCX file')
    if data[0x24:0x28] != b'DCP\0' or data[0x28:0x2C] != b'DFLT':
        raise IconError('this DCX is not zlib (DFLT) compressed')
    if data[0x44:0x48] != b'DCA\0':
        raise IconError('this DCX has an unexpected header')
    uncompressed, compressed = struct.unpack('>II', data[0x1C:0x24])
    start = 0x44 + struct.unpack('>I', data[0x48:0x4C])[0]
    if start + compressed > len(data):
        raise IconError('the DCX is cut short')
    try:
        payload = zlib.decompress(data[start:start + compressed])
    except zlib.error as error:
        raise IconError(f'the DCX data does not inflate: {error}') from None
    if len(payload) != uncompressed:
        raise IconError('the DCX data has a different size than its header says')
    return payload


def dcx_pack(payload, level=9):
    """The payload as a DFLT DCX file, as the game's own files are written."""
    compressed = zlib.compress(payload, level)
    dcs = struct.pack('>4sII', b'DCS\0', len(payload), len(compressed))
    return DCX_HEADER + dcs + DCX_DCP + struct.pack('>4sI', b'DCA\0', 8) + compressed


# --- TPF -----------------------------------------------------------------------------------------------
Texture = namedtuple('Texture', 'name offset size width height dxgi')
ENTRY_AT, ENTRY_SIZE = 16, 36
DXGI_BC7 = 98

# The 20 glyph textures in the order of the game's file, and the two trademark marks between them.
GLYPH_NAMES = ['KG_Cancel', 'KG_L1', 'KG_L2', 'KG_L3', 'KG_LS', 'KG_L_D', 'KG_L_LR', 'KG_L_U', 'KG_L_UD',
               'KG_L_UDLR', 'KG_OK', 'KG_R1', 'KG_R2', 'KG_R3', 'KG_RS', 'KG_R_L', 'KG_R_U', 'KG_Start',
               'KG_TP_L', 'KG_TP_R']
GLYPH_SIZE = 32
GLYPH_BYTES = 1024  # 8x8 blocks of 16 bytes


def parse_tpf(tpf):
    """[Texture] of a TPF, in file order."""
    if tpf[:4] != b'TPF\0' or len(tpf) < ENTRY_AT:
        raise IconError('not a TPF file')
    count = struct.unpack_from('<I', tpf, 8)[0]
    if ENTRY_AT + ENTRY_SIZE * count > len(tpf):
        raise IconError('the TPF header is cut short')
    textures = []
    for index in range(count):
        at = ENTRY_AT + ENTRY_SIZE * index
        offset, size = struct.unpack_from('<II', tpf, at)
        width, height = struct.unpack_from('<HH', tpf, at + 12)
        name_at = struct.unpack_from('<I', tpf, at + 24)[0]
        dxgi = struct.unpack_from('<I', tpf, at + 32)[0]
        textures.append(Texture(read_name(tpf, name_at, tpf[14]), offset, size, width, height, dxgi))
    return textures


def read_name(tpf, at, encoding):
    """A texture name: UTF-16 when the header says so (flag 1, as the game's file), else a plain string."""
    end = at
    if encoding == 1:
        while end + 2 <= len(tpf) and tpf[end:end + 2] != b'\0\0':
            end += 2
        return tpf[at:end].decode('utf-16-le', 'replace')
    while end < len(tpf) and tpf[end]:
        end += 1
    return tpf[at:end].decode('shift-jis', 'replace')


def check_layout(tpf):
    """{name: Texture} of the glyphs, or IconError saying what differs from the file this module was made for:
    a PS4 TPF whose KG_* textures are 20 glyphs of 32x32 BC7 and two marks, one after the other."""
    textures = parse_tpf(tpf)
    if tpf[12] != 4:
        raise IconError(f'common.tpf is not a PS4 TPF (platform {tpf[12]}): other game versions are not supported')
    by_name = {}
    for texture in textures:
        if texture.name in by_name:
            raise IconError(f'common.tpf has two textures named {texture.name}')
        by_name[texture.name] = texture
    for name in GLYPH_NAMES:
        texture = by_name.get(name)
        if texture is None:
            raise IconError(f'common.tpf has no texture named {name}')
        if (texture.width, texture.height) != (GLYPH_SIZE, GLYPH_SIZE) or texture.size != GLYPH_BYTES \
                or texture.dxgi != DXGI_BC7:
            raise IconError(f'{name} is {texture.width}x{texture.height}, {texture.size} bytes, format '
                            f'{texture.dxgi}: expected 32x32 BC7 of {GLYPH_BYTES} bytes')
    for texture in textures:
        if texture.offset < ENTRY_AT + ENTRY_SIZE * len(textures) or texture.offset + texture.size > len(tpf):
            raise IconError(f'{texture.name} lies outside common.tpf')
    run = sorted((t for t in textures if t.name.startswith('KG_')), key=lambda t: t.offset)
    for before, after in zip(run, run[1:]):
        if before.offset + before.size != after.offset:
            raise IconError(f'{before.name} and {after.name} are not next to each other in common.tpf')
    return {name: by_name[name] for name in GLYPH_NAMES}


def patch_texture(tpf, texture, data):
    """tpf with the bytes of one texture replaced; the file keeps its size."""
    if len(data) != texture.size:
        raise IconError(f'{texture.name}: {len(data)} bytes where the file has {texture.size}')
    return tpf[:texture.offset] + bytes(data) + tpf[texture.offset + texture.size:]


# --- PS4 tiling ----------------------------------------------------------------------------------------
# A BC7 block is 4x4 pixels, so a micro tile of 8x8 blocks is 32x32 pixels. Inside it the k-th block sits at x =
# the even bits of k, y = the odd bits. Textures of several micro tiles keep them in raster order (1D thin).
MORTON_8X8 = [(sum(((k >> (2 * i)) & 1) << i for i in range(3)), sum(((k >> (2 * i + 1)) & 1) << i for i in range(3)))
              for k in range(64)]


def tile_blocks(raster, bw, bh):
    """Blocks in raster order (a list of 16 byte blocks, bw x bh) as the bytes of a PS4 texture."""
    if bw % 8 or bh % 8 or len(raster) != bw * bh:
        raise IconError(f'{bw}x{bh} blocks do not make whole 32x32 micro tiles')
    out = bytearray(bw * bh * 16)
    tiles_x = bw // 8
    for tile in range(tiles_x * (bh // 8)):
        left, top = (tile % tiles_x) * 8, (tile // tiles_x) * 8
        for k, (x, y) in enumerate(MORTON_8X8):
            at = (tile * 64 + k) * 16
            out[at:at + 16] = raster[(top + y) * bw + left + x]
    return bytes(out)


def untile_blocks(data, bw, bh):
    """The inverse of tile_blocks: a list of bw x bh blocks in raster order."""
    if bw % 8 or bh % 8 or len(data) != bw * bh * 16:
        raise IconError(f'{len(data)} bytes are not {bw}x{bh} blocks in whole micro tiles')
    raster = [None] * (bw * bh)
    tiles_x = bw // 8
    for tile in range(tiles_x * (bh // 8)):
        left, top = (tile % tiles_x) * 8, (tile // tiles_x) * 8
        for k, (x, y) in enumerate(MORTON_8X8):
            at = (tile * 64 + k) * 16
            raster[(top + y) * bw + left + x] = data[at:at + 16]
    return raster


# --- BC7 mode 6 ----------------------------------------------------------------------------------------
# One subset, RGBA endpoints of 7 bits plus one shared parity bit each, 4 bit indices (3 for the first pixel,
# whose top bit is implied zero). Bits are little endian: mode (0b1000000), R0 R1 G0 G1 B0 B1 A0 A1, P0 P1,
# then the 16 indices in raster order.
WEIGHTS = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)
MODE6 = 0x40


def bc7_decode_block(block):
    """[(r, g, b, a)] x 16 of a mode 6 block (the only mode this module writes)."""
    bits = int.from_bytes(block, 'little')
    if bits & 0x7F != MODE6:
        raise IconError('only BC7 mode 6 blocks are decoded here')
    fields = [(bits >> (7 + 7 * i)) & 0x7F for i in range(8)]  # R0 R1 G0 G1 B0 B1 A0 A1
    p0, p1 = (bits >> 63) & 1, (bits >> 64) & 1
    e0 = [fields[2 * c] << 1 | p0 for c in range(4)]
    e1 = [fields[2 * c + 1] << 1 | p1 for c in range(4)]
    pixels, at = [], 65
    for i in range(16):
        width = 3 if i == 0 else 4
        w = WEIGHTS[(bits >> at) & ((1 << width) - 1)]
        at += width
        pixels.append(tuple((e0[c] * (64 - w) + e1[c] * w + 32) >> 6 for c in range(4)))
    return pixels


def bc7_pack(q0, q1, p0, p1, indices):
    """The block of 7 bit endpoints q0/q1 (RGBA), their parity bits and 16 indices; the ends are swapped (and
    the indices mirrored) when the first pixel would need the top bit of its 3 bit index."""
    if indices[0] >= 8:
        q0, q1, p0, p1 = q1, q0, p1, p0
        indices = [15 - i for i in indices]
    bits = MODE6
    for c in range(4):
        bits |= q0[c] << (7 + 14 * c) | q1[c] << (14 + 14 * c)
    bits |= p0 << 63 | p1 << 64
    at = 65
    for i, index in enumerate(indices):
        bits |= index << at
        at += 3 if i == 0 else 4
    return bits.to_bytes(16, 'little')


def endpoint_bits(target):
    """(q, p, error): the 7 bit channels and shared parity bit whose 8 bit endpoint is nearest to target."""
    best = None
    for p in (0, 1):
        q = [min(127, max(0, int((v - p) / 2 + 0.5))) for v in target]
        error = sum((v - (2 * c + p)) ** 2 for v, c in zip(target, q))
        if best is None or error < best[2]:
            best = (q, p, error)
    return best


def channel_weights(pixel):
    """How much each channel of a pixel matters to the fit: a stray bit of alpha around a shape is seen at once,
    and the colour of a fully transparent pixel never is."""
    return (0, 0, 0, 4) if pixel[3] == 0 else (1, 1, 1, 2)


def assign_indices(pixels, e0, e1):
    """(indices, weighted squared error) of the palette entry nearest to each pixel, for 8 bit endpoints e0, e1."""
    palette = [tuple((e0[c] * (64 - w) + e1[c] * w + 32) >> 6 for c in range(4)) for w in WEIGHTS]
    axis = [e1[c] - e0[c] for c in range(4)]
    indices, total = [], 0
    for pixel in pixels:
        weight = channel_weights(pixel)
        length = sum(weight[c] * axis[c] ** 2 for c in range(4))
        guess = 0
        if length:
            t = sum(weight[c] * (pixel[c] - e0[c]) * axis[c] for c in range(4)) / length
            guess = min(15, max(0, round(t * 15)))
        best, best_error = guess, None
        for index in range(max(0, guess - 2), min(15, guess + 2) + 1):
            entry = palette[index]
            error = sum(weight[c] * (pixel[c] - entry[c]) ** 2 for c in range(4))
            if best_error is None or error < best_error:
                best, best_error = index, error
        indices.append(best)
        total += best_error
    return indices, total


def principal_axis(pixels):
    """The direction along which the 16 RGBA pixels spread most (None when they are all alike)."""
    mean = [sum(p[c] for p in pixels) / 16 for c in range(4)]
    cov = [[sum((p[i] - mean[i]) * (p[j] - mean[j]) for p in pixels) for j in range(4)] for i in range(4)]
    # Start from the widest pair of colours. A fixed start such as (1, 1, 1, 1) is orthogonal to the spread of red
    # next to green, so the power iteration would stay at zero; the widest pair never is.
    one, two = max(((a, b) for a in pixels for b in pixels), key=lambda ab: sum((ab[0][c] - ab[1][c]) ** 2 for c in range(4)))
    axis = [float(one[c] - two[c]) for c in range(4)]
    length = sum(v * v for v in axis) ** 0.5
    if length < 1e-9:
        return None
    axis = [v / length for v in axis]
    for _ in range(10):
        nxt = [sum(cov[i][j] * axis[j] for j in range(4)) for i in range(4)]
        norm = sum(v * v for v in nxt) ** 0.5
        if norm < 1e-9:
            return None
        axis = [v / norm for v in nxt]
    return axis, mean


def least_squares_ends(pixels, indices):
    """Endpoints (floats) that reproduce the pixels best for the given indices, or None when one index serves all."""
    e0, e1 = [], []
    for ch in range(4):
        a = b = c = d = e = 0.0
        for pixel, index in zip(pixels, indices):
            w = channel_weights(pixel)[ch]
            t = WEIGHTS[index] / 64
            a += w * (1 - t) ** 2
            b += w * (1 - t) * t
            c += w * t * t
            d += w * (1 - t) * pixel[ch]
            e += w * t * pixel[ch]
        det = a * c - b * b
        if det < 1e-6:
            return None
        e0.append((d * c - e * b) / det)
        e1.append((a * e - b * d) / det)
    return e0, e1


def fit_ends(pixels, ends, rounds):
    """(error, q0, q1, p0, p1, indices) of the best of a few rounds: quantize the ends, give each pixel its
    nearest palette entry, refit the ends to those indices."""
    best = None
    for _round in range(rounds):
        q0, p0, _e = endpoint_bits(ends[0])
        q1, p1, _e = endpoint_bits(ends[1])
        indices, error = assign_indices(pixels, [2 * q + p0 for q in q0], [2 * q + p1 for q in q1])
        if best is None or error < best[0]:
            best = (error, q0, q1, p0, p1, indices)
        if error == 0:
            break
        fitted = least_squares_ends(pixels, indices)
        if fitted is None:
            break
        ends = [[min(255.0, max(0.0, v)) for v in end] for end in fitted]
    return best


def bc7_encode_block(pixels):
    """A mode 6 block for 16 RGBA pixels (raster order). The ends start from the pixels' main axis, and from the
    least and most opaque pixels; each start is refined by fitting the ends to the indices they give, and the
    best wins. Pure Python, made for flat-coloured icons: blocks of one or two colours come out within 1 of the
    input (the parity bit is shared by the four channels of an end), blocks of more colours are as close as two
    ends allow."""
    pixels = bleed_transparent([tuple(p) for p in pixels])
    if pixels.count(pixels[0]) == 16:  # one colour: the common case in an atlas, no fitting needed
        q, p, _e = endpoint_bits(pixels[0])
        return bc7_pack(q, q, p, p, [0] * 16)
    starts = [[pixels[0], pixels[0]]]
    axis = principal_axis(pixels)
    if axis:
        direction, mean = axis
        along = [sum((p[c] - mean[c]) * direction[c] for c in range(4)) for p in pixels]
        starts = [[pixels[along.index(min(along))], pixels[along.index(max(along))]]]
        alpha = [p[3] for p in pixels]
        if min(alpha) != max(alpha):
            starts.append([pixels[alpha.index(min(alpha))], pixels[alpha.index(max(alpha))]])
    _error, q0, q1, p0, p1, indices = min((fit_ends(pixels, ends, 4) for ends in starts), key=lambda r: r[0])
    return bc7_pack(q0, q1, p0, p1, indices)


def bleed_transparent(pixels):
    """pixels with the colour of fully transparent ones replaced by the average of the block's visible pixels:
    their colour is never seen, and left black it would drag the fitted ends towards a dark fringe."""
    visible = [p for p in pixels if p[3]]
    if not visible or len(visible) == 16:
        return pixels
    total = sum(p[3] for p in visible)
    mean = tuple(round(sum(p[c] * p[3] for p in visible) / total) for c in range(3))
    return [p if p[3] else (*mean, 0) for p in pixels]


def encode_blocks(image):
    """Raster-ordered mode 6 blocks of an RGBA image whose sides are multiples of 4."""
    width, height = image.size
    if width % 4 or height % 4:
        raise IconError(f'{width}x{height} is not a whole number of 4x4 blocks')
    image = image.convert('RGBA')
    pixels = list(getattr(image, 'get_flattened_data', image.getdata)())
    blocks = []
    for by in range(height // 4):
        for bx in range(width // 4):
            block = [pixels[(by * 4 + y) * width + bx * 4 + x] for y in range(4) for x in range(4)]
            blocks.append(bc7_encode_block(block))
    return blocks


def encode_image(image):
    """The bytes of a PS4 BC7 texture for an RGBA image (sides multiples of 32)."""
    width, height = image.size
    return tile_blocks(encode_blocks(image), width // 4, height // 4)


def decode_raster(blocks, width, height):
    """An RGBA image from raster-ordered BC7 blocks of any mode (Pillow's decoder)."""
    data = b''.join(blocks)
    header = b'DDS ' + struct.pack('<7I', 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, height, width, len(data), 0, 1)
    header += b'\0' * 44 + struct.pack('<2I4s5I', 32, 4, b'DX10', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x1000, 0, 0, 0, 0)
    header += struct.pack('<5I', DXGI_BC7, 3, 0, 1, 0)
    return Image.open(io.BytesIO(header + data)).convert('RGBA')


def decode_image(data, width, height):
    """An RGBA image from the bytes of a PS4 BC7 texture."""
    return decode_raster(untile_blocks(data, width // 4, height // 4), width, height)


# --- what each glyph shows -----------------------------------------------------------------------------
# Every KG_* texture is named by its shape, not by the PlayStation's button names: the engine picks OK or
# Cancel by region, so the cross texture is the cross input whichever of the two the game calls it. The
# direction textures name the d-pad directions they light, in the order up, left, down, right.
SHAPES = {
    'KG_Cancel': ('cross',), 'KG_OK': ('circle',), 'KG_R_L': ('square',), 'KG_R_U': ('triangle',),
    'KG_L1': ('l1',), 'KG_L2': ('l2',), 'KG_L3': ('l3',), 'KG_R1': ('r1',), 'KG_R2': ('r2',), 'KG_R3': ('r3',),
    'KG_LS': ('move_up', 'move_left', 'move_down', 'move_right'),
    'KG_RS': ('look_up', 'look_left', 'look_down', 'look_right'),
    'KG_Start': ('options',), 'KG_TP_L': ('touchpad',), 'KG_TP_R': ('touchpad_right',),
    'KG_L_U': ('up',), 'KG_L_D': ('down',), 'KG_L_LR': ('left', 'right'), 'KG_L_UD': ('up', 'down'),
    'KG_L_UDLR': ('up', 'left', 'down', 'right'),
}
# The part of the 32x32 texture the original art uses, per kind of glyph (measured on the game's file): the
# replacements stay inside it, so the game's layout around them keeps working.
BOXES = {'disc': (3, 3, 29, 29), 'key': (0, 6, 32, 27), 'pill': (9, 2, 23, 30), 'dpad': (0, 0, 32, 32),
         'touch': (0, 1, 32, 27)}
KINDS = {'KG_Cancel': 'disc', 'KG_OK': 'disc', 'KG_R_L': 'disc', 'KG_R_U': 'disc', 'KG_L3': 'disc', 'KG_R3': 'disc',
         'KG_LS': 'disc', 'KG_RS': 'disc', 'KG_L1': 'key', 'KG_L2': 'key', 'KG_R1': 'key', 'KG_R2': 'key',
         'KG_Start': 'pill', 'KG_TP_L': 'touch', 'KG_TP_R': 'touch', 'KG_L_U': 'dpad', 'KG_L_D': 'dpad',
         'KG_L_LR': 'dpad', 'KG_L_UD': 'dpad', 'KG_L_UDLR': 'dpad'}
ICON_SETS = ('playstation', 'xbox', 'keyboard')
MODIFIERS = ('Shift', 'Ctrl', 'Alt')

Plan = namedtuple('Plan', 'kind tokens names')


def parse_key(text):
    """(modifiers, key) of a key.<input>= entry: 'Shift+Mouse Right' is (('Shift',), 'Mouse Right')."""
    mods = []
    while True:
        for prefix in MODIFIERS:
            if text.startswith(prefix + '+'):
                mods.append(prefix)
                text = text[len(prefix) + 1:]
                break
        else:
            return tuple(mods), text


def first_binding(ini, kind, name):
    """The first of the names bound to an input (the saved line, else the default), None when none is."""
    names = bbport_controls.current_binding(ini, kind, name)
    return names[0] if names else None


DASH = ('key', (), '-')  # what an input with no binding at all shows


def key_token(ini, name):
    """The first key bound to an input; with none bound, the default one, else a dash (a glyph is never blank)."""
    first = first_binding(ini, 'key', name) or first_default(bbport_controls.default_binding('key', name))
    if first is None:
        return DASH
    mods, base = parse_key(first)
    return ('key', mods, base)


def first_default(text):
    names = bbport_controls.split_binding(text)
    return names[0] if names else None


def pad_token(ini, name):
    """The first gamepad button bound to an input; with none bound, the default one, the View button for the two
    touchpad halves (a pad has no touchpad), else a dash (a glyph is never blank)."""
    first = first_binding(ini, 'pad', name) or first_default(bbport_controls.default_binding('pad', name))
    if first:
        return ('pad', first)
    return ('pad', 'back') if name in ('touchpad', 'touchpad_right') else DASH


def plan(icon_set, ini):
    """{texture: Plan(kind, tokens, names)}: what to draw in each glyph for an icon set ('xbox' or 'keyboard')
    and the bbport.ini values (key.<input>, pad.<input>, mouse_camera). A token is ('key', modifiers, key),
    ('pad', SDL button), ('stick', side) or ('mouse',). An input with nothing bound shows its default binding, or
    on the Xbox side the View button for a touchpad half, or a dash: no glyph is blank."""
    if icon_set not in ('xbox', 'keyboard'):
        raise IconError(f'no icons are drawn for {icon_set}')
    ini = ini or {}
    plans = {}
    for texture, names in SHAPES.items():
        if icon_set == 'xbox':
            if texture in ('KG_LS', 'KG_RS'):
                tokens = [('stick', 'left' if texture == 'KG_LS' else 'right')]
            else:
                tokens = [pad_token(ini, name) for name in names]
        elif texture == 'KG_RS' and ini.get('mouse_camera', bbport_controls.MOUSE_DEFAULTS['mouse_camera']) == '1':
            tokens = [('mouse',)]
        else:
            tokens = [key_token(ini, name) for name in names]
        plans[texture] = Plan(KINDS[texture], tokens, names)
    return plans


# Short legends for a key cap 26 px wide: what each SDL key name is called on a keycap.
LEGENDS = {
    'Space': 'Spc', 'Left Shift': 'Shft', 'Right Shift': 'Shft', 'Left Ctrl': 'Ctrl', 'Right Ctrl': 'Ctrl',
    'Left Alt': 'Alt', 'Right Alt': 'Alt', 'Escape': 'Esc', 'Return': 'Ent', 'Backspace': 'Bksp',
    'PageUp': 'PgUp', 'PageDown': 'PgDn', 'CapsLock': 'Caps', 'Delete': 'Del', 'Insert': 'Ins',
    'Left GUI': 'Win', 'Right GUI': 'Win', 'Application': 'Menu', 'PrintScreen': 'Prt', 'Numlock': 'Num',
    'ScrollLock': 'ScL', 'Keypad Enter': 'Ent', 'Keypad .': 'N.', 'Keypad +': 'N+', 'Keypad -': 'N-',
    'Keypad *': 'N*', 'Keypad /': 'N/',
}
ARROWS = {'Up': 'up', 'Down': 'down', 'Left': 'left', 'Right': 'right'}


def legend_for(name):
    """The text on the keycap of an SDL key name."""
    if name in LEGENDS:
        return LEGENDS[name]
    if name.startswith('Keypad '):
        return 'N' + name[7:]
    return name if len(name) <= 4 else name[:4]


# Xbox buttons: the legend, and the colour of the letter of the four face buttons.
XBOX_FACE = {'a': ('A', (110, 204, 72)), 'b': ('B', (240, 68, 60)), 'x': ('X', (78, 146, 248)),
             'y': ('Y', (252, 210, 56))}
XBOX_LEGENDS = {'leftshoulder': 'LB', 'rightshoulder': 'RB', 'lefttrigger': 'LT', 'righttrigger': 'RT',
                'leftstick': 'LS', 'rightstick': 'RS', 'guide': 'Gd', 'touchpad': 'TP', 'misc1': 'M1',
                'paddle1': 'P1', 'paddle2': 'P2', 'paddle3': 'P3', 'paddle4': 'P4'}
PAD_ARROWS = {'dpup': 'up', 'dpdown': 'down', 'dpleft': 'left', 'dpright': 'right'}

# --- drawing -------------------------------------------------------------------------------------------
# Everything is drawn 8 times larger and reduced, which anti-aliases the shapes; coordinates are in pixels
# of the final 32x32 glyph.
SS = 8
INK = (238, 228, 198, 255)  # the light symbol, a little warmer than white as the game's own tan
DARK = (46, 46, 46, 255)  # the dark disc and key of the game's art
EDGE = (104, 104, 104, 255)
CAP = (64, 64, 64, 255)  # the face of a key cap
DIM = (78, 78, 78, 255)  # a part that is not lit
GOLD = (244, 178, 62, 255)  # a lit mouse button
SHADE = (30, 30, 30, 255)


def scaled(box):
    return tuple(v * SS for v in box)


_fonts = {}


def font_for(pixels):
    """Pillow's bundled TrueType font at a size in supersampled pixels (always the same on every machine)."""
    from PIL import ImageFont
    pixels = max(4, int(round(pixels)))
    if pixels not in _fonts:
        try:
            _fonts[pixels] = ImageFont.load_default(size=pixels)
        except (TypeError, OSError):  # a Pillow without FreeType: the bitmap font, whatever size it has
            _fonts[pixels] = ImageFont.load_default()
    return _fonts[pixels]


def fit_text(draw, text, width, height, top=12.0):
    """(font, stroke, ink box) of the largest bold text of the given legend that fits width x height pixels."""
    size = top
    while True:
        font = font_for(size * SS)
        stroke = max(1, int(size * SS * 0.06))
        box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if (box[2] - box[0] <= width * SS and box[3] - box[1] <= height * SS) or size <= 4:
            return font, stroke, box
        size -= 0.25


def legend_size(text, width, height, top=12.0):
    """Height in glyph pixels of the ink of the text once fitted into width x height (a legibility measure)."""
    scratch = ImageDraw.Draw(Image.new('L', (4, 4)))
    _font, _stroke, box = fit_text(scratch, text, width, height, top)
    return (box[3] - box[1]) / SS


def put_text(draw, text, box, fill, top=12.0):
    """The text, as large as fits, centred on its ink in box."""
    x0, y0, x1, y1 = box
    font, stroke, ink = fit_text(draw, text, x1 - x0, y1 - y0, top)
    x = (x0 + x1) / 2 * SS - (ink[0] + ink[2]) / 2
    y = (y0 + y1) / 2 * SS - (ink[1] + ink[3]) / 2
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill)


def arrow(draw, direction, box, fill):
    """A solid triangle pointing up, down, left or right inside box."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    points = {'up': [(cx, y0), (x1, y1), (x0, y1)], 'down': [(cx, y1), (x1, y0), (x0, y0)],
              'left': [(x0, cy), (x1, y0), (x1, y1)], 'right': [(x1, cy), (x0, y0), (x0, y1)]}[direction]
    draw.polygon([(x * SS, y * SS) for x, y in points], fill=fill)


def fitted(box, aspect):
    """The largest box of width/height = aspect centred in box."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if width / height > aspect:
        width = height * aspect
    else:
        height = width / aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2


MOUSE_LIT = {'Mouse Left': 'left', 'Mouse Right': 'right', 'Mouse Middle': 'middle', 'Mouse X1': 'x1',
             'Mouse X2': 'x2', 'Wheel Up': 'up', 'Wheel Down': 'down'}


def draw_mouse(image, draw, box, lit=None):
    """A mouse seen from above, the button named by lit (left, right, middle, x1, x2) or the wheel (up, down)
    in gold. x1 (back) and x2 (forward) show as side buttons on its left, the wheel's direction as an arrow."""
    x0, y0, x1, y1 = box
    extra = {'x1': 0.16, 'x2': 0.16, 'up': 0.34, 'down': 0.34}.get(lit, 0.0)  # room beside the body, of its height
    height = min(y1 - y0, (x1 - x0) / (0.6 + extra))
    width = height * 0.6
    left = (x0 + x1) / 2 - (width + height * extra) / 2 + (height * extra if lit in ('x1', 'x2') else 0)
    top = (y0 + y1) / 2 - height / 2
    body = (left, top, left + width, top + height)
    cx = left + width / 2
    line = max(1.0, height / 14)
    split = top + height * 0.46
    wheel = (cx - width * 0.1, top + height * 0.09, cx + width * 0.1, top + height * 0.33)
    regions = {'left': (left, top, cx, split), 'right': (cx, top, body[2], split), 'middle': wheel, 'up': wheel,
               'down': wheel}
    radius = width * 0.48
    draw.rounded_rectangle(scaled(body), radius=radius * SS, fill=DARK)
    if lit in regions:
        shape = Image.new('L', image.size, 0)
        ImageDraw.Draw(shape).rectangle(scaled(regions[lit]), fill=255)
        body_mask = Image.new('L', image.size, 0)
        ImageDraw.Draw(body_mask).rounded_rectangle(scaled(body), radius=radius * SS, fill=255)
        image.paste(Image.new('RGBA', image.size, GOLD), mask=ImageChops.multiply(shape, body_mask))
    draw.rounded_rectangle(scaled(body), radius=radius * SS, outline=INK, width=int(line * SS))
    draw.line(scaled((cx, top + line / 2, cx, split)), fill=INK, width=int(line * SS * 0.8))
    draw.line(scaled((left + line / 2, split, body[2] - line / 2, split)), fill=INK, width=int(line * SS * 0.8))
    draw.rounded_rectangle(scaled(wheel), radius=width * 0.1 * SS, outline=INK, width=int(line * SS * 0.7),
                           fill=GOLD if lit in ('middle', 'up', 'down') else DARK)
    if lit in ('x1', 'x2'):
        tab = height * 0.15
        rows = {'x2': (split - tab * 1.8, split - tab * 0.3), 'x1': (split + tab * 0.3, split + tab * 1.8)}
        for name, (y_from, y_to) in rows.items():
            draw.rectangle(scaled((left - height * 0.13, y_from, left - height * 0.01, y_to)),
                           fill=GOLD if lit == name else INK)
    if lit in ('up', 'down'):
        size = height * 0.26
        arrow(draw, lit, (body[2] + height * 0.08, top + height * 0.21 - size / 2, body[2] + height * 0.08 + size * 0.9,
                          top + height * 0.21 + size / 2), GOLD)


def draw_cap(draw, box, kind, set_name):
    """The container of a glyph: a disc (Xbox), a key cap (keyboard) or a rounded key, in the dark of the game's art."""
    x0, y0, x1, y1 = box
    if kind == 'disc' and set_name == 'xbox':
        draw.ellipse(scaled(box), fill=DARK, outline=EDGE, width=SS)
    elif kind == 'pill':
        draw.rounded_rectangle(scaled(box), radius=(x1 - x0) / 2 * SS, fill=DARK, outline=EDGE, width=SS)
    else:
        radius = 5 if kind == 'disc' else 3
        draw.rounded_rectangle(scaled(box), radius=radius * SS, fill=SHADE, outline=EDGE, width=SS)
        if set_name == 'keyboard':  # the key's top face, set a little up from the foot of the cap
            draw.rounded_rectangle(scaled((x0 + 1.5, y0 + 1.5, x1 - 1.5, y1 - 2.5)), radius=max(1, radius - 1.5) * SS,
                                   fill=CAP)


def inner_box(kind, set_name):
    x0, y0, x1, y1 = BOXES[kind]
    if kind == 'disc' and set_name == 'xbox':
        return x0 + 4.5, y0 + 4.5, x1 - 4.5, y1 - 4.5
    if kind == 'pill':
        return x0 + 2, y0 + 5, x1 - 2, y1 - 5
    if set_name == 'keyboard':  # inside the top face of the cap
        return x0 + 2.5, y0 + 2.5, x1 - 2.5, y1 - 3.5
    return x0 + 3, y0 + 3, x1 - 3, y1 - 3


def draw_key(image, draw, token, box):
    """A key or mouse input inside box: a legend, an arrow, or the mouse with its button lit; a modifier
    ('Shift+Mouse Right') goes beside it in a wide box and above it in a tall one."""
    _kind, mods, base = token
    if mods:
        x0, y0, x1, y1 = box
        legend = '+'.join(legend_for(MODIFIER_NAMES[m]) for m in mods)
        if x1 - x0 >= 1.5 * (y1 - y0):
            split = x0 + (x1 - x0) * 0.52
            put_text(draw, legend, (x0, y0, split - 0.5, y1), INK, top=8.5)
            box = (split + 0.5, y0, x1, y1)
        else:
            split = y0 + (y1 - y0) * 0.38
            put_text(draw, legend, (x0, y0, x1, split), INK, top=7)
            box = (x0, split + 0.5, x1, y1)
    if base in MOUSE_LIT:
        draw_mouse(image, draw, box, MOUSE_LIT[base])
    elif base in ARROWS:
        x0, y0, x1, y1 = fitted(box, 1.0)
        size = min(x1 - x0, y1 - y0) * 0.62
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        arrow(draw, ARROWS[base], (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), INK)
    else:
        put_text(draw, legend_for(base), box, INK)


MODIFIER_NAMES = {'Shift': 'Left Shift', 'Ctrl': 'Left Ctrl', 'Alt': 'Left Alt'}


def draw_pad(draw, button, box):
    """An Xbox button inside box."""
    if button in XBOX_FACE:
        text, color = XBOX_FACE[button]
        put_text(draw, text, box, color + (255,), top=17)
    elif button == 'start':  # Menu: three bars
        x0, y0, x1, y1 = box
        cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, min(4.2, (x1 - x0) / 2)
        for dy in (-4.5, 0, 4.5):
            draw.rounded_rectangle(scaled((cx - half, cy + dy - 0.9, cx + half, cy + dy + 0.9)), radius=0.9 * SS, fill=INK)
    elif button == 'back':  # View: two overlapping windows
        x0, y0, x1, y1 = fitted(box, 1.0)
        cx, cy, unit = (x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0) / 2
        for dx, dy in ((-0.28, -0.28), (0.28, 0.28)):
            draw.rectangle(scaled((cx + dx * unit - 0.6 * unit, cy + dy * unit - 0.5 * unit,
                                   cx + dx * unit + 0.6 * unit, cy + dy * unit + 0.5 * unit)), outline=INK, width=int(SS * 1.1))
    elif button in PAD_ARROWS:
        x0, y0, x1, y1 = fitted(box, 1.0)
        size = (x1 - x0) * 0.6
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        arrow(draw, PAD_ARROWS[button], (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), INK)
    else:
        put_text(draw, XBOX_LEGENDS.get(button) or legend_for(button), box, INK)


def draw_click(draw, label, box):
    """A stick: its label, and under it an arrow when the stick is pressed (L3, R3)."""
    x0, y0, x1, y1 = box
    put_text(draw, label, (x0, y0, x1, y1 - 4), INK, top=11)
    cx = (x0 + x1) / 2
    arrow(draw, 'down', (cx - 2.2, y1 - 3.2, cx + 2.2, y1 - 0.2), INK)


def draw_dpad(draw, lit):
    """Four arrows, the lit ones bright (the Xbox d-pad glyph)."""
    for direction in ('up', 'left', 'down', 'right'):
        cx, cy = {'up': (16, 6.5), 'down': (16, 25.5), 'left': (6.5, 16), 'right': (25.5, 16)}[direction]
        arrow(draw, direction, (cx - 6, cy - 5.5, cx + 6, cy + 5.5) if direction in ('up', 'down') else
              (cx - 5.5, cy - 6, cx + 5.5, cy + 6), INK if direction in lit else DIM)


def key_caps(kind, count):
    """Boxes of the small caps of a keyboard d-pad glyph (up, left, down, right order) for 1, 2 or 4 inputs."""
    if kind == 'KG_L_UDLR':
        return [(11, 0, 21, 10), (0, 11, 10, 21), (11, 22, 21, 32), (22, 11, 32, 21)]
    return [(5, 5, 27, 27)] if count == 1 else None


def draw_small_cap(image, draw, token, box, text=None):
    """A key cap of its own (a d-pad or WASD key, a modifier); text replaces the legend of the token."""
    draw.rounded_rectangle(scaled(box), radius=2 * SS, fill=SHADE)
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(scaled((x0 + 0.6, y0 + 0.6, x1 - 0.6, y1 - 1.2)), radius=1.6 * SS, fill=CAP)
    face = (x0 + 1.2, y0 + 1.2, x1 - 1.2, y1 - 2)
    if text:
        put_text(draw, text, face, INK)
    elif token[0] == 'key':
        draw_key(image, draw, token, face)


def is_mouse(token):
    return token[0] == 'mouse' or (token[0] == 'key' and token[2] in MOUSE_LIT)


def draw_mouse_input(image, draw, token, box):
    """A mouse input: the mouse itself, as large as the glyph's area allows (no cap round it), with the
    modifier held for it, if any, on a small cap beside it (in a wide area) or above it."""
    x0, y0, x1, y1 = box[0] + 0.5, box[1] + 0.5, box[2] - 0.5, box[3] - 0.5
    lit = MOUSE_LIT.get(token[2]) if token[0] == 'key' else None
    mods = token[1] if token[0] == 'key' else ()
    if mods:
        text = '+'.join(legend_for(MODIFIER_NAMES[m]) for m in mods)
        if x1 - x0 >= 1.3 * (y1 - y0):
            middle = (y0 + y1) / 2
            draw_small_cap(image, draw, token, (x0, middle - 7.5, x0 + 17, middle + 7.5), text)
            x0 += 18
        else:
            draw_small_cap(image, draw, token, (x0 + 1, y0, x1 - 1, y0 + 9), text)
            y0 += 10
    draw_mouse(image, draw, (x0, y0, x1, y1), lit)


def draw_cluster(image, draw, tokens, box):
    """Four small caps in the layout of WASD (up above down, left and right beside it) inside box."""
    x0, y0, x1, y1 = box
    size = (x1 - x0 - 2) / 3
    top = (x0 + size + 1, y0, x0 + 2 * size + 1, y0 + size)
    row = y0 + size + 1
    spots = {0: top, 1: (x0, row, x0 + size, row + size), 2: (x0 + size + 1, row, x0 + 2 * size + 1, row + size),
             3: (x0 + 2 * size + 2, row, x1, row + size)}
    for index, token in enumerate(tokens):
        draw_small_cap(image, draw, token, spots[index])


def render_glyph(icon_set, glyph):
    """The 32x32 RGBA image of one glyph (a Plan)."""
    image = Image.new('RGBA', (32 * SS, 32 * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    kind, tokens = glyph.kind, glyph.tokens
    if icon_set == 'keyboard' and kind == 'pill':  # a key cap across the whole width, its legend written level
        kind = 'key'
    if kind == 'dpad':
        if icon_set == 'xbox':
            draw_dpad(draw, set(glyph.names))
        elif len(tokens) == 4:
            for token, box in zip(tokens, key_caps('KG_L_UDLR', 4)):
                draw_small_cap(image, draw, token, box)
        elif len(tokens) == 2:
            horizontal = glyph.names == ('left', 'right')
            boxes = [(1, 9, 15, 23), (17, 9, 31, 23)] if horizontal else [(9, 1, 23, 15), (9, 17, 23, 31)]
            for token, box in zip(tokens, boxes):
                draw_small_cap(image, draw, token, box)
        else:
            draw_small_cap(image, draw, tokens[0], (5, 5, 27, 27))
    else:
        box = BOXES[kind]
        inner = inner_box(kind, icon_set)
        if len(tokens) == 4:  # WASD or IJKL: four small caps, with no cap of their own
            draw_cluster(image, draw, tokens, (0, 5, 32, 27))
        else:
            token = tokens[0]
            if icon_set == 'keyboard' and is_mouse(token):
                draw_mouse_input(image, draw, token, box)
                return image.resize((32, 32), Image.LANCZOS)
            draw_cap(draw, box, kind, icon_set)
            if token[0] == 'key':
                draw_key(image, draw, token, inner)
            elif token[0] == 'stick':
                put_text(draw, 'LS' if token[1] == 'left' else 'RS', inner, INK, top=11)
            elif token[0] == 'pad':
                if token[1] in ('leftstick', 'rightstick'):
                    draw_click(draw, XBOX_LEGENDS[token[1]], inner)
                else:
                    draw_pad(draw, token[1], inner)
    return image.resize((32, 32), Image.LANCZOS)


def build_glyphs(icon_set, ini):
    """{texture name: 32x32 RGBA image} of the 20 glyphs for 'xbox' or 'keyboard' and the bbport.ini values."""
    return {texture: render_glyph(icon_set, glyph) for texture, glyph in plan(icon_set, ini).items()}


# --- the file --------------------------------------------------------------------------------------------
Generated = namedtuple('Generated', 'digest skipped')
COMMON_TPF = 'dvdroot_ps4/menu/common.tpf.dcx'  # where the game keeps the glyphs, under its folder


def patch_glyphs(tpf, glyphs):
    """tpf with the glyph textures replaced by the images {name: RGBA 32x32}; same size, nothing else touched."""
    found = check_layout(tpf)
    out = bytearray(tpf)
    for name, image in glyphs.items():
        data = encode_image(image)
        texture = found[name]
        if len(data) != texture.size:
            raise IconError(f'{name}: {len(data)} bytes where the file has {texture.size}')
        out[texture.offset:texture.offset + texture.size] = data
    return bytes(out)


# Buttons baked into the menu atlases (the prompts that show a circle, a cross, L3 or a triangle): per atlas, the
# whole-block rectangle that holds one, the box of its disc, and the glyph it takes after. Measured on the game's
# file; patch_atlases checks that the art is where this table says before it draws over it.
ATLAS_ICONS = {
    'MENU_Common_00091': [((660, 120, 688, 152), (661, 123, 687, 149), 'KG_OK'),
                          ((692, 120, 720, 152), (694, 123, 720, 149), 'KG_Cancel'),
                          ((724, 120, 752, 152), (726, 123, 752, 149), 'KG_L3')],
    'MENU_Common_00092': [((684, 0, 716, 28), (688, 3, 713, 28), 'KG_OK'),
                          ((660, 108, 688, 136), (663, 108, 688, 133), 'KG_R_U'),
                          ((700, 108, 728, 136), (703, 108, 728, 133), 'KG_Cancel')],
}
DISC_AREA = (3, 3, 29, 29)  # the part of a 32x32 glyph that a disc-shaped button uses


# How atlas_icon_present tells a baked button from other art: alpha up to ATLAS_CLEAR_ALPHA is clear (the faint
# halo of the original discs is below it), above ATLAS_SOLID_ALPHA is opaque, and a disc counts when more than
# ATLAS_MIN_COVER of its box is opaque.
ATLAS_CLEAR_ALPHA, ATLAS_SOLID_ALPHA, ATLAS_MIN_COVER = 24, 128, 0.5


def atlas_icon_present(image, rect, box):
    """True when a baked button is where the table says: the rectangle outside the disc is clear (nothing else is
    drawn there) and the disc is mostly opaque."""
    alpha = image.getchannel('A')
    outside = 0
    for y in range(rect[1], rect[3]):
        for x in range(rect[0], rect[2]):
            if not (box[0] <= x < box[2] and box[1] <= y < box[3]) and alpha.getpixel((x, y)) > ATLAS_CLEAR_ALPHA:
                outside += 1
    inside = sum(alpha.getpixel((x, y)) > ATLAS_SOLID_ALPHA for y in range(box[1], box[3]) for x in range(box[0], box[2]))
    return outside == 0 and inside > ATLAS_MIN_COVER * (box[2] - box[0]) * (box[3] - box[1])


def patch_atlases(tpf, glyphs):
    """Redraws, in place in the bytearray tpf, the buttons baked into the menu atlases with the new glyphs
    {name: 32x32 image}. Only the BC7 blocks of a rectangle are rewritten. Returns [(atlas, rectangle, done)]:
    done is False for a rectangle that does not fit its atlas or whose art is not as expected (left as it is, so one
    bad spot never stops the rest); atlases the file lacks have no entries."""
    report = []
    by_name = {texture.name: texture for texture in parse_tpf(bytes(tpf))}
    for name, entries in ATLAS_ICONS.items():
        texture = by_name.get(name)
        if texture is None:
            continue
        if texture.dxgi != DXGI_BC7 or texture.size != texture.width * texture.height \
                or texture.width % GLYPH_SIZE or texture.height % GLYPH_SIZE:
            report += [(name, rect, False) for rect, _box, _glyph in entries]  # an edition with other atlases
            continue
        bw, bh = texture.width // 4, texture.height // 4
        blocks = untile_blocks(bytes(tpf[texture.offset:texture.offset + texture.size]), bw, bh)
        image = decode_raster(blocks, texture.width, texture.height)
        for rect, box, glyph in entries:
            if rect[2] > texture.width or rect[3] > texture.height or not atlas_icon_present(image, rect, box):
                report.append((name, rect, False))
                continue
            patch = Image.new('RGBA', (rect[2] - rect[0], rect[3] - rect[1]), (0, 0, 0, 0))
            disc = glyphs[glyph].crop(DISC_AREA)
            size = (box[2] - box[0], box[3] - box[1])
            patch.paste(disc.resize(size, Image.LANCZOS) if disc.size != size else disc, (box[0] - rect[0], box[1] - rect[1]))
            new = encode_blocks(patch)
            across = patch.width // 4
            for index, block in enumerate(new):
                blocks[(rect[1] // 4 + index // across) * bw + rect[0] // 4 + index % across] = block
            report.append((name, rect, True))
        tpf[texture.offset:texture.offset + texture.size] = tile_blocks(blocks, bw, bh)
    return report


def source_hash(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def generate(source, destination, icon_set, ini):
    """Writes the game's common.tpf.dcx at `source` to `destination` with its button glyphs redrawn for
    icon_set ('xbox' or 'keyboard') and the bbport.ini values `ini`. The input is checked first (IconError
    says what differs); the source file is only read. Returns Generated(digest, skipped): the SHA-256 of the source and
    the number of baked atlas buttons that kept their PlayStation art (see patch_atlases)."""
    import os
    digest = source_hash(source)
    try:
        with open(source, 'rb') as handle:
            tpf = dcx_unpack(handle.read())
        check_layout(tpf)
    except (struct.error, IndexError, KeyError, ValueError, OverflowError, zlib.error) as error:
        # A damaged file fails in many ways below the format checks; to the caller it is one: not usable.
        raise IconError(f"could not read or check the game's {COMMON_TPF} ({type(error).__name__}: {error})") from None
    try:
        glyphs = build_glyphs(icon_set, ini)
        patched = bytearray(patch_glyphs(tpf, glyphs))
        report = patch_atlases(patched, glyphs)
    except IconError:
        raise
    except Exception as error:  # the file was fine: this is the drawing, not the game's data
        raise IconError(f'could not draw the icons ({type(error).__name__}: {error})') from None
    destination = os.fspath(destination)
    os.makedirs(os.path.dirname(destination) or '.', exist_ok=True)
    temporary = destination + '.tmp'
    with open(temporary, 'wb') as handle:
        handle.write(dcx_pack(bytes(patched)))
    os.replace(temporary, destination)
    return Generated(digest, sum(1 for _name, _rect, done in report if not done))


# --- the cache -------------------------------------------------------------------------------------------
ICONS_VERSION = 1  # part of every cache key: raise it when the drawings change
KEEP_CACHED = 4  # generated files kept in out/icons (each is the size of the game's own, about 9 MB)


def cache_key(source_sha256, icon_set, ini):
    """What makes two generated files the same: the source file, the set, the icons' drawings (their version)
    and what each glyph shows, which is what the bindings amount to."""
    import hashlib
    shown = repr([(name, glyph.tokens) for name, glyph in plan(icon_set, ini).items()])
    return hashlib.sha256(f'{ICONS_VERSION}|{source_sha256}|{icon_set}|{shown}'.encode()).hexdigest()[:16]


def ensure_layer(game_dir, out_dir, icon_set, ini):
    """(folder, built, kept): a folder, under out_dir/icons, that is a mod layer (dvdroot_ps4/menu/common.tpf.dcx) with the
    icons for icon_set and the bindings in ini. A file made before for the same source, set and bindings is reused
    (built False). kept is how many baked atlas buttons kept their PlayStation art. IconError says why the game's
    file cannot be used."""
    import json
    import os
    import shutil
    from pathlib import Path
    source = Path(game_dir) / COMMON_TPF
    if not source.is_file():
        raise IconError(f'{COMMON_TPF} is not in the game folder')
    digest = source_hash(source)
    root = Path(out_dir) / 'icons'
    folder = root / f'{icon_set}-{cache_key(digest, icon_set, ini)}'
    target = folder / COMMON_TPF
    if target.is_file() and (folder / 'icons.json').is_file():
        os.utime(folder)  # the newest are kept
        try:
            kept = int(json.loads((folder / 'icons.json').read_text(encoding='utf-8')).get('atlas_skipped', 0))
        except (OSError, ValueError, TypeError):
            kept = 0
        return folder, False, kept
    shutil.rmtree(folder, ignore_errors=True)
    kept = generate(source, target, icon_set, ini).skipped
    (folder / 'icons.json').write_text(json.dumps({'source_sha256': digest, 'icon_set': icon_set, 'version': ICONS_VERSION,
                                                   'atlas_skipped': kept}, indent=2), encoding='utf-8')
    others = sorted((p for p in root.iterdir() if p.is_dir() and p != folder), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in others[KEEP_CACHED - 1:]:
        shutil.rmtree(old, ignore_errors=True)
    return folder, True, kept


def main(argv):
    import os
    if len(argv) < 3:
        print('usage: bbport_icons.py <common.tpf.dcx> <out folder> [xbox|keyboard ...]')
        return 2
    source, out = argv[1], argv[2]
    for icon_set in argv[3:] or ['xbox', 'keyboard']:
        folder = os.path.join(out, icon_set)
        variant = os.path.join(folder, 'dvdroot_ps4', 'menu', 'common.tpf.dcx')
        try:
            digest = generate(source, variant, icon_set, {}).digest
        except (IconError, OSError) as error:
            print(f'{icon_set}: {error}')
            return 1
        with open(variant, 'rb') as handle:
            tpf = dcx_unpack(handle.read())
        for texture in parse_tpf(tpf):
            if texture.name.startswith('KG_'):
                image = decode_image(tpf[texture.offset:texture.offset + 1024], 32, 32).crop((0, 0, texture.width, texture.height))
                image.save(os.path.join(folder, texture.name + '.png'))
                image.resize((image.width * 4, image.height * 4), Image.NEAREST).save(os.path.join(folder, texture.name + '_x4.png'))
        print(f'{icon_set}: {variant} (source SHA-256 {digest})')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
