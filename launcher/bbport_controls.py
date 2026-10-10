# SPDX-License-Identifier: GPL-2.0-or-later
"""What both launchers know about the controls page: the table of inputs and their defaults, the way a
binding is written in bbport.ini, and the names the runtime understands.

src/runtime_pad.c reads, from the file BB_CONFIG names, one line per input: `key.<input>=` with SDL keyboard
names (SDL_GetScancodeFromName) and `pad.<input>=` with SDL gamepad button names, several separated by
commas (at most four). No line keeps the default, an empty value leaves the input unassigned.
GUI-free so the tests can import it anywhere.

Two ideas of the Controls page of Ryansousa10/bloodborne_windows_mouse_and_keyboard (launcher/bbport_controls.py,
commit c650c2e, GPL-2.0-or-later) are used here: Tk's key code names the physical key whatever Shift does, and
Insert, which opens the port's menu in the game, is refused as a binding. Its keybinds.ini is a different runtime
format and is not ported. Mouse buttons and the wheel are names in the same key.<input>= lines ("Mouse Left",
"Wheel Up", see parse_input in src/runtime_pad.c), each optionally after "Shift+", "Ctrl+" or "Alt+"; the Dark
Souls III layout of the reference (its docs/KEYBOARD_MOUSE.md) is DS3_KEYS, and the mouse_* lines of bbport.ini
are MOUSE_DEFAULTS."""

# input, label (Russian: the key of the GTK launcher's translations), default keyboard keys, default
# gamepad buttons (SDL names; "" none, None: the input has no gamepad binding). bbport.ini
# key.<input>= / pad.<input>= replace a default; no line keeps it.
CONTROLS = [
    ("cross", "Крест", "Space", "a"),
    ("circle", "Круг", "Left Shift", "b"),
    ("square", "Квадрат", "E", "x"),
    ("triangle", "Треугольник", "Q", "y"),
    ("l1", "L1", "1", "leftshoulder"),
    ("r1", "R1", "3", "rightshoulder"),
    ("l2", "L2", "R", "lefttrigger"),
    ("r2", "R2", "F", "righttrigger"),
    ("l3", "L3", "Z", "leftstick"),
    ("r3", "R3", "C", "rightstick"),
    ("options", "Options", "Return", "start"),
    ("touchpad", "Тачпад, левая половина (жесты)", "Tab", "back, touchpad"),
    ("touchpad_right", "Тачпад, правая половина (личные вещи)", "Backspace", ""),
    ("up", "Крестовина вверх", "I", "dpup"),
    ("down", "Крестовина вниз", "K", "dpdown"),
    ("left", "Крестовина влево", "J", "dpleft"),
    ("right", "Крестовина вправо", "L", "dpright"),
    ("move_up", "Движение вперёд", "W", None),
    ("move_down", "Движение назад", "S", None),
    ("move_left", "Движение влево", "A", None),
    ("move_right", "Движение вправо", "D", None),
    ("look_up", "Камера вверх", "Up", None),
    ("look_down", "Камера вниз", "Down", None),
    ("look_left", "Камера влево", "Left", None),
    ("look_right", "Камера вправо", "Right", None),
]

# The English text of each label: the key of the Windows launcher's translations.
LABELS = {
    "cross": "Cross", "circle": "Circle", "square": "Square", "triangle": "Triangle",
    "l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3", "options": "Options",
    "touchpad": "Touchpad, left half (gestures)", "touchpad_right": "Touchpad, right half (key items)",
    "up": "D-pad up", "down": "D-pad down", "left": "D-pad left", "right": "D-pad right",
    "move_up": "Move forward", "move_down": "Move back", "move_left": "Move left", "move_right": "Move right",
    "look_up": "Camera up", "look_down": "Camera down", "look_left": "Camera left", "look_right": "Camera right",
}

MAX_BIND = 4  # keys (or buttons) one input takes; the runtime ignores the rest

# SDL gamepad button names (SDL_GetGamepadButtonFromString, plus the two triggers the runtime reads as
# buttons) and the text the launcher shows for them.
PAD_BUTTONS = [
    ("a", "A"), ("b", "B"), ("x", "X"), ("y", "Y"),
    ("leftshoulder", "LB / L1"), ("rightshoulder", "RB / R1"),
    ("lefttrigger", "LT / L2"), ("righttrigger", "RT / R2"),
    ("leftstick", "L3 (left stick)"), ("rightstick", "R3 (right stick)"),
    ("start", "Start"), ("back", "Back"), ("guide", "Guide"), ("touchpad", "Touchpad"),
    ("dpup", "D-pad ↑"), ("dpdown", "D-pad ↓"), ("dpleft", "D-pad ←"), ("dpright", "D-pad →"),
    ("misc1", "Misc 1"), ("paddle1", "Paddle 1"), ("paddle2", "Paddle 2"),
    ("paddle3", "Paddle 3"), ("paddle4", "Paddle 4"),
]
PAD_LABELS = dict(PAD_BUTTONS)


def has_pad(name):
    """True when the input can take gamepad buttons (the movement and camera keys cannot)."""
    return next(pad for n, _l, _k, pad in CONTROLS if n == name) is not None


def default_binding(kind, name):
    """The default text of key.<name> ("key") or pad.<name> ("pad")."""
    _n, _label, keys, pad = next(row for row in CONTROLS if row[0] == name)
    return keys if kind == "key" else pad or ""


def binding_keys():
    """Every bbport.ini key a controls page edits."""
    return [f"{kind}.{name}" for name, _l, _k, pad in CONTROLS for kind in ("key", "pad")
            if kind == "key" or pad is not None]


def ini_updates(ini):
    """{bbport.ini key: value}, None where the input keeps its default (save_ini then drops the line)."""
    return {key: ini.get(key) for key in binding_keys()}


def split_binding(text):
    """['back', 'touchpad'] from "back, touchpad", as the runtime splits it."""
    return [part for part in (piece.strip() for piece in (text or "").split(",")) if part]


def join_binding(parts):
    return ", ".join(parts)


def current_binding(ini, kind, name):
    """The list of names in force: the saved line, else the default."""
    value = ini.get(f"{kind}.{name}")
    return split_binding(default_binding(kind, name) if value is None else value)


def pad_text(value):
    """'Back, Touchpad' for "back, touchpad" (names without a label stay as they are)."""
    return ", ".join(PAD_LABELS.get(part, part) for part in split_binding(value))


# --- keyboard names ----------------------------------------------------------------------------
# Tk keysyms -> SDL scancode names. Letters, digits and punctuation are not here: they follow the
# position of the key, see below.
KEYSYMS = {
    "space": "Space", "Return": "Return", "Escape": "Escape", "Tab": "Tab", "ISO_Left_Tab": "Tab",
    "BackSpace": "Backspace", "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
    "Delete": "Delete", "Home": "Home", "End": "End", "Prior": "PageUp",
    "Next": "PageDown", "Caps_Lock": "CapsLock", "Num_Lock": "Numlock", "Scroll_Lock": "ScrollLock",
    "Print": "PrintScreen", "Pause": "Pause", "App": "Application",
    "Shift_L": "Left Shift", "Shift_R": "Right Shift", "Control_L": "Left Ctrl", "Control_R": "Right Ctrl",
    "Alt_L": "Left Alt", "Alt_R": "Right Alt", "Meta_L": "Left Alt", "Meta_R": "Right Alt",
    "Win_L": "Left GUI", "Win_R": "Right GUI", "Super_L": "Left GUI", "Super_R": "Right GUI",
    "KP_Enter": "Keypad Enter", "KP_Add": "Keypad +", "KP_Subtract": "Keypad -", "KP_Multiply": "Keypad *",
    "KP_Divide": "Keypad /", "KP_Decimal": "Keypad .",
    **{f"F{n}": f"F{n}" for n in range(1, 25)},
    **{f"KP_{n}": f"Keypad {n}" for n in range(10)},
    # With Num Lock off the keypad sends its navigation keysyms.
    "KP_Insert": "Keypad 0", "KP_End": "Keypad 1", "KP_Down": "Keypad 2", "KP_Next": "Keypad 3",
    "KP_Left": "Keypad 4", "KP_Begin": "Keypad 5", "KP_Right": "Keypad 6", "KP_Home": "Keypad 7",
    "KP_Up": "Keypad 8", "KP_Prior": "Keypad 9", "KP_Delete": "Keypad .",
}

# The typing block by position (SDL scancodes are positions on a US keyboard): set 1 scan codes.
_ROWS = ((0x02, "1234567890-="), (0x10, "QWERTYUIOP[]"), (0x1E, "ASDFGHJKL;'`"), (0x2B, "\\ZXCVBNM,./"))
# No comma: the runtime splits a binding at commas, so the key could not be written.
SCAN_NAMES = {start + i: char for start, row in _ROWS for i, char in enumerate(row) if char != ","}
# Windows virtual keys of that block: digits, letters, and the OEM keys (; = , - . / ` [ \ ] ').
_TYPING_KEYS = (set(range(0x30, 0x3A)) | set(range(0x41, 0x5B)) | {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xC0,
                                                                    0xDB, 0xDC, 0xDD, 0xDE})
KEYSYM_CHARS = {
    "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
    "semicolon": ";", "apostrophe": "'", "grave": "`", "period": ".", "slash": "/",
}  # "comma" is missing on purpose: the runtime splits a binding at commas


def windows_scan(virtual_key):
    """The scan code of the key that sends this virtual key in the layout in use (0 when unknown)."""
    try:
        import ctypes
        return ctypes.windll.user32.MapVirtualKeyW(virtual_key, 0)  # MAPVK_VK_TO_VSC
    except (AttributeError, OSError):
        return 0


def windows_key_down(virtual_key):
    """True while the physical key is held (GetAsyncKeyState)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
    except (AttributeError, OSError):
        return False


# Virtual keys of the left and right Shift, Ctrl and Alt: Tk reports Shift_L for both Shifts on some builds.
_SIDES = {"Shift": (0xA0, 0xA1), "Control": (0xA2, 0xA3), "Alt": (0xA4, 0xA5)}


def held_side(keysym, down):
    """Shift_L/Shift_R (and Control, Alt) as the keys actually held say, else as Tk named it."""
    base, _sep, side = keysym.partition("_")
    if down and base in _SIDES and side in ("L", "R"):
        left, right = (down(vk) for vk in _SIDES[base])
        if right and not left:
            return f"{base}_R"
        if left and not right:
            return f"{base}_L"
    return keysym


def tk_key_to_sdl(keysym, keycode=0, scan_of=None, down=None):
    """The SDL name of a Tk key press, or None when the runtime cannot bind it.

    keysym, keycode: the event's. scan_of: virtual key -> scan code (windows_scan). down: virtual key -> held
    (windows_key_down), which tells the left modifier from the right one. Letters, digits and punctuation
    are named by the position of the key, as the game reads them: the key labelled A on a French keyboard
    sits where a US keyboard has Q, and the game must see Q. Without scan_of (or for a key it does not
    know) the character of the keysym stands in, which is right on a US layout."""
    keysym = held_side(keysym, down)
    if keysym in KEYSYMS:
        return KEYSYMS[keysym]
    if scan_of and keycode in _TYPING_KEYS:
        return SCAN_NAMES.get(scan_of(keycode))
    if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
        return keysym.upper()
    return KEYSYM_CHARS.get(keysym)


# --- the mouse -----------------------------------------------------------------------------------------
# What a key.<input>= line takes besides keys: the names parse_input in src/runtime_pad.c knows, and the
# text the launcher shows for them.
MOUSE_INPUTS = [
    ("Mouse Left", "Left button"), ("Mouse Right", "Right button"), ("Mouse Middle", "Wheel click"),
    ("Mouse X1", "Side button 1"), ("Mouse X2", "Side button 2"), ("Wheel Up", "Wheel up"), ("Wheel Down", "Wheel down"),
]
_TK_BUTTONS = {1: "Mouse Left", 2: "Mouse Middle", 3: "Mouse Right"}


def mouse_button_to_sdl(number):
    """The runtime's name of Tk's mouse button 1 (left), 2 (middle) or 3 (right); None for others."""
    return _TK_BUTTONS.get(number)


def wheel_to_sdl(delta):
    """'Wheel Up' / 'Wheel Down' for a Tk <MouseWheel> delta (positive: away from the user), None for 0."""
    return "Wheel Up" if delta > 0 else "Wheel Down" if delta < 0 else None


def with_modifiers(name, shift=False, ctrl=False, alt=False):
    """'Shift+Mouse Left': the runtime counts such an input only while that modifier is held."""
    return "".join(prefix for prefix, held in (("Shift+", shift), ("Ctrl+", ctrl), ("Alt+", alt)) if held) + name


def held_modifiers(down):
    """(shift, ctrl, alt) from down(virtual key), either side of each: GetAsyncKeyState's view of the keyboard."""
    return tuple(any(down(vk) for vk in _SIDES[base]) for base in ("Shift", "Control", "Alt"))


def is_modifier_keysym(keysym):
    """True for the Shift, Ctrl and Alt keys: pressed first for a combination, so a capture waits for their release."""
    return keysym.partition("_")[0] in _SIDES and keysym[-2:] in ("_L", "_R")


# bbport.ini lines of the mouse camera (runtime_pad.c reads them; no line keeps the default, but the launcher
# writes them like its other settings). The sensitivity is 0.022 degrees of turn per mouse count x the value.
MOUSE_DEFAULTS = {"mouse_camera": "1", "mouse_sensitivity": "1.00", "mouse_invert_y": "0", "mouse_no_auto_rotation": "0"}
MOUSE_SENSITIVITY_RANGE = (0.01, 20.0)  # what the runtime accepts; the launcher's slider covers 0.1 to 5

# The Dark Souls III keyboard and mouse layout (the reference's docs/KEYBOARD_MOUSE.md). The runtime has no walk
# input, so Left Alt (walk in Dark Souls III) is not bound.
DS3_KEYS = {
    "cross": "E", "circle": "Space", "square": "R", "triangle": "F",
    "l1": "Mouse Right", "r1": "Mouse Left", "l2": "Left Ctrl", "r2": "Shift+Mouse Left",
    "l3": "C", "r3": "Q, Mouse Middle", "options": "Tab", "touchpad": "G", "touchpad_right": "Backspace",
    "up": "Up, Wheel Up", "down": "Down, Wheel Down", "left": "Left", "right": "Right",
    "move_up": "W", "move_down": "S", "move_left": "A", "move_right": "D",
    "look_up": "I", "look_down": "K", "look_left": "J", "look_right": "L",
}


def ds3_updates():
    """{bbport.ini key: value} of the layout: key lines only, the gamepad has none."""
    return {f"key.{name}": value for name, value in DS3_KEYS.items()}


def apply_ds3(ini):
    """A copy of ini with the layout's keyboard and mouse bindings; a binding equal to the default needs no line."""
    ini = dict(ini)
    for name, value in DS3_KEYS.items():
        if split_binding(value) == split_binding(default_binding("key", name)):
            ini.pop(f"key.{name}", None)
        else:
            ini[f"key.{name}"] = value
    return ini
