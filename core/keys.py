"""Common Win32 virtual-key codes. Letters/digits map to their ASCII code
(VK_A == ord('A')), so Keyboard.tap(ord('W')) works without a lookup here.
"""

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_CAPITAL = 0x14  # Caps Lock
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22  # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E

VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B

# Punctuation/OEM keys -- unlike letters/digits, ord() of the printed
# character does NOT line up with these keys' actual VK codes (there's no
# ASCII-code shortcut for them), so each needs its own real Win32 constant.
VK_OEM_1 = 0xBA       # ;:
VK_OEM_PLUS = 0xBB    # =+
VK_OEM_COMMA = 0xBC   # ,<
VK_OEM_MINUS = 0xBD   # -_
VK_OEM_PERIOD = 0xBE  # .>
VK_OEM_2 = 0xBF       # /?
VK_OEM_3 = 0xC0       # `~
VK_OEM_4 = 0xDB       # [{
VK_OEM_5 = 0xDC       # \|
VK_OEM_6 = 0xDD       # ]}
VK_OEM_7 = 0xDE       # '"

_SPECIAL_KEY_NAMES = {
    "space": VK_SPACE, "esc": VK_ESCAPE, "ctrl": VK_CONTROL, "shift": VK_SHIFT, "alt": VK_MENU,
    "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
    # The rest of what ui/app.js's mapKeyName() can actually capture (it
    # falls through to e.key.toLowerCase() for anything not in its own
    # small special-case list, so the JS side was already capturing all of
    # these -- this Python side just never knew how to press them back).
    "tab": VK_TAB, "enter": VK_RETURN, "backspace": VK_BACK, "delete": VK_DELETE,
    "home": VK_HOME, "end": VK_END, "pageup": VK_PRIOR, "pagedown": VK_NEXT,
    "insert": VK_INSERT, "capslock": VK_CAPITAL,
}
_F_KEY_NAMES = {f"f{i}": globals()[f"VK_F{i}"] for i in range(1, 13)}
# Keyed by the literal character JS's KeyboardEvent.key reports for these --
# e.g. pressing the "-" key reports e.key === "-", captured verbatim by
# mapKeyName's fallback.
_OEM_KEY_NAMES = {
    ";": VK_OEM_1, "=": VK_OEM_PLUS, ",": VK_OEM_COMMA, "-": VK_OEM_MINUS, ".": VK_OEM_PERIOD,
    "/": VK_OEM_2, "`": VK_OEM_3, "[": VK_OEM_4, "\\": VK_OEM_5, "]": VK_OEM_6, "'": VK_OEM_7,
}


def key_name_to_vk(name: str):
    """Reverses ui/app.js's mapKeyName() -- a captured Place Unit/Setting
    block hotkey (see Creation's per-block hotkey capture) is stored as
    whatever lowercase string that captured (a single character, 'f1'..
    'f12', or one of the names above), and the block runner needs to turn
    it back into a VK code to actually press it. Returns None for an
    empty/unrecognized name rather than raising, so a block with no
    captured hotkey yet is a silent no-op instead of a crash mid-run.
    """
    if not name:
        return None
    name = name.lower()
    if name in _SPECIAL_KEY_NAMES:
        return _SPECIAL_KEY_NAMES[name]
    if name in _F_KEY_NAMES:
        return _F_KEY_NAMES[name]
    if name in _OEM_KEY_NAMES:
        return _OEM_KEY_NAMES[name]
    if len(name) == 1:
        return ord(name.upper())
    return None
