"""macOS implementation of the input primitives (see core._input_win for
the contract) -- CGEvent synthesis via pyobjc's Quartz bindings, the mac
equivalent of Win32 SendInput: events go through the real HID event tap
(kCGHIDEventTap), which is what games actually listen to, same reasoning
_sendinput.py documents for SendInput over cursor-only writes.

Coordinates are GLOBAL DISPLAY POINTS (what CGEvent expects and what
core.window_mac's window rects report) -- Retina's 2x pixel density is a
capture-side concern only (see core.vision's capture normalization), never
an input-side one.

REQUIREMENTS (testers, read this first):
  - pip install pyobjc-framework-Quartz (see requirements.txt's darwin
    markers -- a plain `pip install -r requirements.txt` gets it).
  - System Settings > Privacy & Security > Accessibility AND Input
    Monitoring: the terminal/app running this macro must be granted both,
    or every synthesized event is silently dropped by the OS -- the exact
    "clicks visibly do nothing" symptom. macOS prompts on first use, but
    only for Accessibility; Input Monitoring sometimes needs adding by
    hand.

The Win32 virtual-key codes in core.keys stay the app-wide currency
(hotkey storage, recorded walk paths, block hotkeys are all saved in
terms of them -- keeping them portable means a recording made on Windows
replays on a Mac); this module translates VK -> mac keycode at event time
via _VK_TO_MAC below.
"""
import Quartz

# ANSI-layout mac virtual keycodes (Events.h's kVK_ANSI_* values). Only
# what core.keys can actually produce needs mapping; anything unmapped is
# silently ignored at event time (same failure mode as an unrecognized
# hotkey name on Windows) rather than raising mid-run.
_VK_TO_MAC = {
    # Letters (VK == ord('A')..ord('Z'))
    ord("A"): 0x00, ord("B"): 0x0B, ord("C"): 0x08, ord("D"): 0x02, ord("E"): 0x0E,
    ord("F"): 0x03, ord("G"): 0x05, ord("H"): 0x04, ord("I"): 0x22, ord("J"): 0x26,
    ord("K"): 0x28, ord("L"): 0x25, ord("M"): 0x2E, ord("N"): 0x2D, ord("O"): 0x1F,
    ord("P"): 0x23, ord("Q"): 0x0C, ord("R"): 0x0F, ord("S"): 0x01, ord("T"): 0x11,
    ord("U"): 0x20, ord("V"): 0x09, ord("W"): 0x0D, ord("X"): 0x07, ord("Y"): 0x10,
    ord("Z"): 0x06,
    # Digits (VK == ord('0')..ord('9'))
    ord("0"): 0x1D, ord("1"): 0x12, ord("2"): 0x13, ord("3"): 0x14, ord("4"): 0x15,
    ord("5"): 0x17, ord("6"): 0x16, ord("7"): 0x1A, ord("8"): 0x1C, ord("9"): 0x19,
    # Specials (core.keys VK_* constants)
    0x08: 0x33,  # VK_BACK -> mac "delete" (backspace)
    0x09: 0x30,  # VK_TAB
    0x0D: 0x24,  # VK_RETURN
    0x10: 0x38,  # VK_SHIFT
    0x11: 0x3B,  # VK_CONTROL -> control (NOT command -- Roblox mac uses
                 #   the same Ctrl-style shortcuts for this game's UI, and
                 #   the app only ever uses Ctrl+A for select-all in the
                 #   settings search box, which Roblox handles itself)
    0x12: 0x3A,  # VK_MENU (Alt) -> option
    0x14: 0x39,  # VK_CAPITAL
    0x1B: 0x35,  # VK_ESCAPE
    0x20: 0x31,  # VK_SPACE
    0x21: 0x74,  # VK_PRIOR (PgUp)
    0x22: 0x79,  # VK_NEXT (PgDn)
    0x23: 0x77,  # VK_END
    0x24: 0x73,  # VK_HOME
    0x25: 0x7B,  # VK_LEFT
    0x26: 0x7E,  # VK_UP
    0x27: 0x7C,  # VK_RIGHT
    0x28: 0x7D,  # VK_DOWN
    0x2D: 0x72,  # VK_INSERT -> mac "help" (closest physical equivalent; rarely used)
    0x2E: 0x75,  # VK_DELETE -> forward delete
    # F-keys
    0x70: 0x7A, 0x71: 0x78, 0x72: 0x63, 0x73: 0x76, 0x74: 0x60, 0x75: 0x61,
    0x76: 0x62, 0x77: 0x64, 0x78: 0x65, 0x79: 0x6D, 0x7A: 0x67, 0x7B: 0x6F,
    # OEM punctuation (core.keys VK_OEM_*)
    0xBA: 0x29,  # ;:
    0xBB: 0x18,  # =+
    0xBC: 0x2B,  # ,<
    0xBD: 0x1B,  # -_
    0xBE: 0x2F,  # .>
    0xBF: 0x2C,  # /?
    0xC0: 0x32,  # `~
    0xDB: 0x21,  # [{
    0xDC: 0x2A,  # \|
    0xDD: 0x1E,  # ]}
    0xDE: 0x27,  # '"
}

_DOWN_EVENT = {"left": Quartz.kCGEventLeftMouseDown, "right": Quartz.kCGEventRightMouseDown,
               "middle": Quartz.kCGEventOtherMouseDown}
_UP_EVENT = {"left": Quartz.kCGEventLeftMouseUp, "right": Quartz.kCGEventRightMouseUp,
             "middle": Quartz.kCGEventOtherMouseUp}
_DRAG_EVENT = {"left": Quartz.kCGEventLeftMouseDragged, "right": Quartz.kCGEventRightMouseDragged,
               "middle": Quartz.kCGEventOtherMouseDragged}
_CG_BUTTON = {"left": Quartz.kCGMouseButtonLeft, "right": Quartz.kCGMouseButtonRight,
              "middle": Quartz.kCGMouseButtonCenter}

# A move while a button is held must be a *Dragged event, not MouseMoved --
# macOS treats a plain move mid-hold as inconsistent input and apps
# (games included) won't register it as a drag. Mouse.drag() just calls
# move primitives while holding, so the held state is tracked here and the
# right event type chosen per move instead of leaking that macOS quirk up
# into the cross-platform Mouse class.
_held_button = None


def _post(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def cursor_pos():
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return int(loc.x), int(loc.y)


def _move_event_type():
    return _DRAG_EVENT[_held_button] if _held_button else Quartz.kCGEventMouseMoved


def move_abs(x: int, y: int) -> None:
    btn = _CG_BUTTON[_held_button] if _held_button else Quartz.kCGMouseButtonLeft
    _post(Quartz.CGEventCreateMouseEvent(None, _move_event_type(), (x, y), btn))


def move_rel(dx: int, dy: int) -> None:
    # Quartz has no relative-move event -- emit an absolute move to
    # cursor+delta, with the delta fields set so apps reading
    # kCGMouseEventDeltaX/Y (how games detect genuine hover movement, the
    # whole reason Mouse.nudge exists) still see a real relative motion.
    x, y = cursor_pos()
    event = Quartz.CGEventCreateMouseEvent(None, _move_event_type(), (x + dx, y + dy),
                                            _CG_BUTTON[_held_button] if _held_button else Quartz.kCGMouseButtonLeft)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, dx)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, dy)
    _post(event)


def button_down(button: str) -> None:
    global _held_button
    x, y = cursor_pos()
    _post(Quartz.CGEventCreateMouseEvent(None, _DOWN_EVENT[button], (x, y), _CG_BUTTON[button]))
    _held_button = button


def button_up(button: str) -> None:
    global _held_button
    x, y = cursor_pos()
    _post(Quartz.CGEventCreateMouseEvent(None, _UP_EVENT[button], (x, y), _CG_BUTTON[button]))
    _held_button = None


def scroll(amount: int) -> None:
    # `amount` arrives in Windows wheel-delta units (multiples of 120 per
    # notch -- every caller in core.runner/stage_select speaks that unit),
    # translated here to mac scroll lines so callers stay platform-blind.
    lines = int(amount / 120)
    if lines == 0:
        lines = 1 if amount > 0 else -1
    _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines))


def key_down(vk: int) -> None:
    mac_code = _VK_TO_MAC.get(vk)
    if mac_code is None:
        return
    _post(Quartz.CGEventCreateKeyboardEvent(None, mac_code, True))


def key_up(vk: int) -> None:
    mac_code = _VK_TO_MAC.get(vk)
    if mac_code is None:
        return
    _post(Quartz.CGEventCreateKeyboardEvent(None, mac_code, False))


def is_key_down(vk: int) -> bool:
    # CGEventSourceKeyState against the HID system state reads real
    # physical key state without any event tap/hook -- the direct
    # equivalent of GetAsyncKeyState polling core.paths' recorder needs.
    mac_code = _VK_TO_MAC.get(vk)
    if mac_code is None:
        return False
    return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateHIDSystemState, mac_code))


# Layout-independent movement keys -- see _input_win's counterparts for the
# rationale. On macOS this is a non-issue: CGEvent keycodes ARE physical
# positions (kVK_ANSI_W is the physical W key on every layout), so these
# just route the movement key names through the existing VK path.
_MOVE_VKS = {"w": ord("W"), "a": ord("A"), "s": ord("S"), "d": ord("D"),
             "i": ord("I"), "o": ord("O")}


def move_key_down(name: str) -> None:
    vk = _MOVE_VKS.get(name)
    if vk is not None:
        key_down(vk)


def move_key_up(name: str) -> None:
    vk = _MOVE_VKS.get(name)
    if vk is not None:
        key_up(vk)


def is_move_key_down(name: str) -> bool:
    vk = _MOVE_VKS.get(name)
    return is_key_down(vk) if vk is not None else False
