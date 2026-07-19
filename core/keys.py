"""Common Win32 virtual-key codes. Letters/digits map to their ASCII code
(VK_A == ord('A')), so Keyboard.tap(ord('W')) works without a lookup here."""

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
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

_SPECIAL_KEY_NAMES = {
    "space": VK_SPACE, "esc": VK_ESCAPE, "ctrl": VK_CONTROL, "shift": VK_SHIFT, "alt": VK_MENU,
    "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
}
_F_KEY_NAMES = {f"f{i}": globals()[f"VK_F{i}"] for i in range(1, 13)}


def key_name_to_vk(name: str):
    """Reverses ui/app.js's mapKeyName() -- a captured Place Unit/Setting
    block hotkey (see Creation's per-block hotkey capture) is stored as that
    same lowercase string (a single character, 'f1'..'f12', or one of
    space/esc/ctrl/shift/alt/up/down/left/right), and the block runner needs
    to turn it back into a VK code to actually press it. Returns None for an
    empty/unrecognized name rather than raising, so a block with no captured
    hotkey yet is a silent no-op instead of a crash mid-run.
    """
    if not name:
        return None
    name = name.lower()
    if name in _SPECIAL_KEY_NAMES:
        return _SPECIAL_KEY_NAMES[name]
    if name in _F_KEY_NAMES:
        return _F_KEY_NAMES[name]
    if len(name) == 1:
        return ord(name.upper())
    return None
