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


def test_type_text_skips_unmappable_characters_without_raising(monkeypatch):
    """It must NOT raise. The only caller (runner._search_and_set_toggle) has
    no try/except and _run_session only wraps _run in try/finally, so an
    exception here would kill the whole run thread over one stray character.
    Skipping degrades into the existing, handled "not found in Settings" path.
    """
    monkeypatch.setattr(keyboard.backend, "key_for_char",
                        lambda ch: (ord(ch.upper()), ()) if ch.isalpha() else None)
    monkeypatch.setattr(keyboard.time, "sleep", lambda _: None)
    monkeypatch.setattr(keyboard.pacing, "action_pause", lambda: None)

    seen = []
    kb = keyboard.Keyboard()
    monkeypatch.setattr(kb, "key_down", lambda vk: seen.append(("down", vk)))
    monkeypatch.setattr(kb, "key_up", lambda vk: seen.append(("up", vk)))

    skipped = kb.type_text("a☃b", delay=0)

    assert skipped == ["☃"], "the unmappable character was not reported"
    # The mappable characters either side of it still got typed, in order.
    assert [vk for kind, vk in seen if kind == "down"] == [ord("A"), ord("B")]


def test_type_text_never_strands_a_modifier_held(monkeypatch):
    """If a tap blows up mid-character, Shift must not stay down for every
    keystroke that follows."""
    monkeypatch.setattr(keyboard.backend, "key_for_char", lambda ch: (ord("D"), (0x10,)))
    monkeypatch.setattr(keyboard.time, "sleep", lambda _: None)
    monkeypatch.setattr(keyboard.pacing, "action_pause", lambda: None)

    seen = []
    kb = keyboard.Keyboard()
    monkeypatch.setattr(kb, "key_down", lambda vk: seen.append(("down", vk)))
    monkeypatch.setattr(kb, "key_up", lambda vk: seen.append(("up", vk)))
    monkeypatch.setattr(kb, "tap", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        kb.type_text("D", delay=0)
    except RuntimeError:
        pass
    assert ("up", 0x10) in seen, "Shift was left held after a failed tap"
