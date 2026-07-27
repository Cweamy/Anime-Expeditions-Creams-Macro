from core import keyboard


def test_type_text_uses_layout_mapping_for_punctuation(monkeypatch):
    mappings = {
        "D": (ord("D"), (0x10,)),
        "o": (ord("O"), ()),
        "n": (ord("N"), ()),
        "'": (0xDE, ()),
        "t": (ord("T"), ()),
        "-": (0xBD, ()),
    }
    monkeypatch.setattr(keyboard.backend, "key_for_char", mappings.get)
    monkeypatch.setattr(keyboard.time, "sleep", lambda _: None)
    monkeypatch.setattr(keyboard.pacing, "action_pause", lambda: None)

    seen = []
    kb = keyboard.Keyboard()
    monkeypatch.setattr(kb, "key_down", lambda vk: seen.append(("down", vk)))
    monkeypatch.setattr(kb, "key_up", lambda vk: seen.append(("up", vk)))

    kb.type_text("Don't-")

    # Apostrophe/hyphen use their real OEM keys, not ord("'") == VK_RIGHT
    # and ord("-") == VK_INSERT.
    assert ("down", 0xDE) in seen
    assert ("down", 0xBD) in seen
    assert ("down", 0x27) not in seen
    assert ("down", 0x2D) not in seen
    # Uppercase D holds Shift; lowercase letters do not.
    assert seen[:4] == [("down", 0x10), ("down", ord("D")), ("up", ord("D")), ("up", 0x10)]


def test_type_text_rejects_unmappable_character(monkeypatch):
    monkeypatch.setattr(keyboard.backend, "key_for_char", lambda _: None)
    kb = keyboard.Keyboard()
    try:
        kb.type_text("\N{SNOWMAN}", delay=0)
    except ValueError as exc:
        assert "cannot be typed" in str(exc)
    else:
        raise AssertionError("unmappable character was silently sent as an unrelated virtual key")
