"""key_name_to_vk covers every name the Record block's keyboard hook can
emit (see core.input_record). A name that maps to None is silently skipped
on replay -- "the keyboard didn't play back right" -- so this pins the whole
vocabulary the `keyboard` library reports."""
from core import keys


def test_single_characters_map_to_their_ascii_vk():
    assert keys.key_name_to_vk("w") == ord("W")
    assert keys.key_name_to_vk("1") == ord("1")


def test_function_keys_map():
    assert keys.key_name_to_vk("f1") == keys.VK_F1
    assert keys.key_name_to_vk("f12") == keys.VK_F12


def test_side_specific_modifier_names_all_resolve():
    # These are exactly what the keyboard library's global hook reports for
    # the modifier keys (verified against keyboard.normalize_name).
    assert keys.key_name_to_vk("left shift") == keys.VK_SHIFT
    assert keys.key_name_to_vk("right shift") == keys.VK_SHIFT
    assert keys.key_name_to_vk("shift") == keys.VK_SHIFT
    assert keys.key_name_to_vk("left ctrl") == keys.VK_CONTROL
    assert keys.key_name_to_vk("right ctrl") == keys.VK_CONTROL
    assert keys.key_name_to_vk("left alt") == keys.VK_MENU
    assert keys.key_name_to_vk("right alt") == keys.VK_MENU
    assert keys.key_name_to_vk("alt gr") == keys.VK_MENU


def test_named_specials_the_hook_emits_all_resolve():
    for name in ("space", "esc", "enter", "tab", "delete", "backspace",
                 "up", "down", "left", "right", "page up", "page down",
                 "home", "end", "insert", "caps lock",
                 "pause", "print screen", "num lock", "scroll lock", "menu"):
        assert keys.key_name_to_vk(name) is not None, f"{name!r} maps to None"


def test_unknown_or_empty_name_is_none():
    assert keys.key_name_to_vk("") is None
    assert keys.key_name_to_vk(None) is None
    assert keys.key_name_to_vk("not a real key") is None
