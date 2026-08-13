"""Tests for core/runner_blocks.py (BlockOps mixin)."""
from unittest.mock import MagicMock, patch
import pytest

from core import keys
from core.runner_blocks import BlockOps


class DummyRunner(BlockOps):
    def __init__(self):
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)

    def _strip_auto_upgrade_for_expedition(self, blocks, task):
        return blocks


def test_load_battle_blocks_empty_macro():
    runner = DummyRunner()
    task = {}
    result = runner._load_battle_blocks(task)
    assert result == []


@patch("core.templates.load_template")
def test_load_battle_blocks_dict_format(mock_load):
    mock_load.return_value = {
        "blocks": {
            "battle": [{"type": "upgrade_unit", "slot": 1}]
        }
    }
    runner = DummyRunner()
    task = {"macro": "test_macro"}
    result = runner._load_battle_blocks(task)
    assert len(result) == 1
    assert result[0]["type"] == "upgrade_unit"


@patch("core.templates.load_template")
def test_load_battle_blocks_legacy_flat_list(mock_load):
    mock_load.return_value = {
        "blocks": [{"type": "place_unit"}]
    }
    runner = DummyRunner()
    task = {"macro": "old_macro"}
    result = runner._load_battle_blocks(task)
    assert result == []
    assert any("old format" in log for log in runner.logs)


@patch("core.templates.load_template")
def test_load_battle_blocks_legacy_three_phase(mock_load):
    mock_load.return_value = {
        "blocks": {
            "during": [{"type": "wait", "ms": 1000}],
            "after": [{"type": "walk", "path": "path1"}]
        }
    }
    runner = DummyRunner()
    task = {"macro": "legacy_macro"}
    result = runner._load_battle_blocks(task)
    assert len(result) == 2
    assert any("legacy during/after" in log for log in runner.logs)


def test_walk_block_replays_with_phase_label(monkeypatch):
    """The Walk block replays a recorded path and labels its log by phase --
    so the same block works in Pre Start (multiple allowed) and Battle."""
    from core import runner_blocks

    runner = DummyRunner()
    runner._keyboard = MagicMock()
    runner._set_status = lambda **k: None

    replayed = {}
    monkeypatch.setattr(runner_blocks.walk_paths, "load_path",
                        lambda name: {"events": [("w", "down", 0.0)]})
    monkeypatch.setattr(runner_blocks.walk_paths, "replay_events",
                        lambda events, kb, stop, sprint=False: replayed.setdefault("hit", True))

    import threading
    block = {"type": "walk", "params": {"path": "MyPath"}}
    runner._run_walk_block_tick(threading.Event(), block, 2, phase_label="Pre Start")

    assert replayed.get("hit") is True
    assert any("Pre Start block #2 (Walk)" in m for m in runner.logs)


def test_walk_block_no_path_is_skipped(monkeypatch):
    from core import runner_blocks
    import threading

    runner = DummyRunner()
    runner._keyboard = MagicMock()
    runner._set_status = lambda **k: None
    called = {"replay": False}
    monkeypatch.setattr(runner_blocks.walk_paths, "replay_events",
                        lambda *a, **k: called.__setitem__("replay", True))

    runner._run_walk_block_tick(threading.Event(), {"type": "walk", "params": {"path": ""}}, 1,
                                phase_label="Pre Start")

    assert called["replay"] is False
    assert any("no path selected" in m for m in runner.logs)

# ---------------------------------------------------------------------------
# Placement: the scan box must stay inside the game window
# ---------------------------------------------------------------------------
# Centering a PLACE_SEARCH_BOX_SIZE box on a saved spot within half a box of an
# edge used to capture pixels from outside the game entirely. On Windows the
# docked game sits inside this app's own frame, so those pixels are the macro's
# control panel -- near-white in the Light theme, and therefore accepted as a
# valid placement tile.

class _ScanRunner(BlockOps):
    def __init__(self):
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)


def _record_capture(white_at=None):
    """Window-capture stand-in: records the reference-space region asked for,
    and optionally paints one white pixel at a given WINDOW coordinate."""
    import numpy as np
    seen = {}

    def capture_window(_hwnd, region):
        x, y, w, h = region
        seen.clear()
        seen.update(x=x, y=y, w=w, h=h)
        patch = np.zeros((h, w, 3), np.uint8)
        if white_at is not None:
            px, py = white_at[0] - x, white_at[1] - y
            if 0 <= px < w and 0 <= py < h:
                patch[py, px] = (255, 255, 255)
        return patch

    return capture_window, seen


def _patch_place_capture(monkeypatch, capture):
    """Keep placement geometry tests deterministic on either platform."""
    monkeypatch.setattr("core.runner_blocks.vision.capture_window_region_bgr", capture)
    monkeypatch.setattr(
        "core.ocr.capture_region",
        lambda left, top, width, height: capture(123, (left, top, width, height)),
    )


def test_mac_scan_uses_screen_capture_for_transient_placement_highlight(monkeypatch):
    """macOS's window image can omit Roblox's cursor-driven placement glow."""
    import numpy as np
    from core import ocr, runner_blocks

    calls = []

    def capture_screen(left, top, width, height):
        calls.append((left, top, width, height))
        patch = np.zeros((height, width, 3), np.uint8)
        patch[height // 2, width // 2] = (255, 255, 255)
        return patch

    monkeypatch.setattr(runner_blocks.sys, "platform", "darwin")
    monkeypatch.setattr(ocr, "capture_region", capture_screen)
    monkeypatch.setattr(
        runner_blocks.vision,
        "capture_window_region_bgr",
        lambda *_args: pytest.fail("macOS placement scans must use the composed screen capture"),
    )

    result = _ScanRunner()._scan_place_search_box(123, 100, 200, 400, 400)

    assert result == (0, 0)
    assert calls == [(481, 581, 38, 38)]


@pytest.mark.parametrize("spot", [
    (5, 400), (400, 3), (1149, 400), (400, 753), (2, 2), (1150, 754), (576, 378),
])
def test_scan_box_never_reads_outside_the_window(spot, monkeypatch):
    from core.config import FIXED_WIN_H, FIXED_WIN_W
    capture, seen = _record_capture()
    _patch_place_capture(monkeypatch, capture)

    _ScanRunner()._scan_place_search_box(123, 0, 0, *spot)

    assert seen["x"] >= 0 and seen["y"] >= 0, f"captured off the top/left: {seen}"
    assert seen["x"] + seen["w"] <= FIXED_WIN_W, f"captured past the right edge: {seen}"
    assert seen["y"] + seen["h"] <= FIXED_WIN_H, f"captured past the bottom edge: {seen}"


def test_scan_box_does_not_accept_a_white_pixel_outside_the_window(monkeypatch):
    # x=5 used to capture x=-14..24; a white pixel at x=-6 is the macro's own
    # panel, and was returned as a placement tile at offset (-11, 0).
    capture, _ = _record_capture(white_at=(-6, 400))
    _patch_place_capture(monkeypatch, capture)
    assert _ScanRunner()._scan_place_search_box(123, 0, 0, 5, 400) is None


@pytest.mark.parametrize("spot,white,expected", [
    ((576, 378), (580, 378), (4, 0)),    # no clamping needed
    ((576, 378), (576, 373), (0, -5)),
    ((5, 400), (9, 400), (4, 0)),        # box shifted right by the clamp
    ((4, 6), (7, 9), (3, 3)),            # shifted on both axes
])
def test_scan_box_offset_is_measured_from_the_requested_spot(spot, white, expected, monkeypatch):
    """Clamping moves the box, so the offset has to be relative to the spot the
    caller asked about -- not the middle of whatever region got captured. Get
    this wrong and every placement near an edge lands somewhere else."""
    capture, _ = _record_capture(white_at=white)
    _patch_place_capture(monkeypatch, capture)
    assert _ScanRunner()._scan_place_search_box(123, 0, 0, *spot) == expected


# ---------------------------------------------------------------------------
# Placement: a broken quick-place chain must not leave Shift held
# ---------------------------------------------------------------------------
# Consecutive Place Unit blocks with the same hotkey hold Left Shift so the unit
# stays selected and later placements can skip re-pressing the hotkey. If the
# next member never runs -- marked "Once" on a repeat, or missing its position --
# nothing released Shift, and the FOLLOWING Place Unit block (a different unit)
# took the "Shift is down, same unit still selected" path and placed the
# previous unit on its tile.

class _ShiftRunner(BlockOps):
    def __init__(self):
        self.logs = []
        self.keyboard_events = []
        self._quick_place_shift_down = True      # a chain is in progress
        self._battle_block_index = 0
        self._battle_block_state = {}
        self._last_unit_ordinal = 0
        self._keyboard = MagicMock()
        self._keyboard.key_up = lambda vk: self.keyboard_events.append(("up", vk))

    def _log(self, msg):
        self.logs.append(msg)

    def _set_status(self, **kw):
        pass


def test_once_skipped_battle_block_releases_the_quick_place_shift():
    runner = _ShiftRunner()
    blocks = [{"type": "place_unit", "once": True, "hotkey": "1",
               "params": {"name": "Archer", "x": 100, "y": 100}}]

    runner._run_battle_blocks_tick(1, MagicMock(is_set=lambda: False), blocks, first_repeat=False)

    assert runner._quick_place_shift_down is False, "Shift left held after the chain broke"
    assert ("up", keys.VK_SHIFT) in runner.keyboard_events


def test_place_unit_with_no_position_releases_the_quick_place_shift():
    runner = _ShiftRunner()
    block = {"type": "place_unit", "hotkey": "1", "params": {"name": "Archer"}}   # no x/y

    runner._run_place_unit_block(1, MagicMock(is_set=lambda: False), 0, 0, block,
                                 index=1, macro_name="m", next_is_same_unit=False)

    assert runner._quick_place_shift_down is False, "Shift left held after a no-position skip"
    assert ("up", keys.VK_SHIFT) in runner.keyboard_events


def test_place_unit_with_no_position_keeps_shift_when_the_chain_continues():
    """The guard is conditional on purpose: if the NEXT block is the same unit,
    the chain is still alive and Shift must stay down, exactly as the other
    early returns in this function already do."""
    runner = _ShiftRunner()
    block = {"type": "place_unit", "hotkey": "1", "params": {"name": "Archer"}}

    runner._run_place_unit_block(1, MagicMock(is_set=lambda: False), 0, 0, block,
                                 index=1, macro_name="m", next_is_same_unit=True)

    assert runner._quick_place_shift_down is True


def test_run_target_priority_tick():
    from core.runner_blocks import BlockOps
    from unittest.mock import MagicMock

    class DummyRunner(BlockOps):
        def __init__(self):
            self._placed_unit_positions = {1: (100, 200)}
            self._coords = {"unit_info_reset_x": 10, "unit_info_reset_y": 10}
            self._mouse = MagicMock()
            self._keyboard = MagicMock()
            self.logs = []
        def _log(self, msg):
            self.logs.append(msg)
        def _set_status(self, **kw):
            pass
        def _checkpoint(self, stop_event):
            return False

    runner = DummyRunner()
    block = {"type": "target_priority", "params": {"index": 1, "priority": "Boss"}}
    stop_event = MagicMock(is_set=lambda: False)

    with MagicMock():
        from core import runner_blocks
        original_wm = runner_blocks.wm
        runner_blocks.wm = MagicMock(get_window_rect_screen=lambda hwnd: (0, 0, 800, 600))
        try:
            done = runner._run_target_priority_tick(123, stop_event, block, 1)
        finally:
            runner_blocks.wm = original_wm

    assert done is True
    assert any("pressing R to set target priority to Boss" in log for log in runner.logs)
    assert runner._keyboard.tap.called


class _AutoUpgradeRunner(BlockOps):
    def __init__(self, hotkey="g"):
        self._placed_unit_positions = {1: (100, 200)}
        self._coords = {"unit_info_reset_x": 10, "unit_info_reset_y": 20}
        self._mouse = MagicMock()
        self._keyboard = MagicMock()
        self._get_hotkeys = lambda: {"game_auto_upgrade": hotkey}
        self.logs = []

    def _log(self, msg):
        self.logs.append(msg)

    def _set_status(self, **kw):
        pass

    def _checkpoint(self, stop_event):
        return False

    def _debug_save(self, *args):
        return None


def test_auto_upgrade_hotkey_cycles_to_selected_priority(monkeypatch):
    """Hotkey mode selects the unit, presses the configured game key once per
    priority step, and never depends on finding the small info-panel button."""
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("g")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (50, 60, 1202, 816))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        runner_blocks.vision,
        "find_image_any",
        lambda *a, **k: pytest.fail("hotkey mode should not search for priority_upgrade"),
    )

    block = {
        "type": "auto_upgrade_unit",
        "params": {"index": 1, "priority": 3, "input": "hotkey"},
    }
    assert runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 2) is True

    assert [item.args for item in runner._mouse.click.call_args_list] == [
        (150, 260),
        (60, 80),
    ]
    assert [item.args for item in runner._keyboard.tap.call_args_list] == [
        (ord("G"),),
        (ord("G"),),
        (ord("G"),),
    ]
    assert any("hotkey G 3x" in message for message in runner.logs)


def test_auto_upgrade_hotkey_none_holds_to_clear(monkeypatch):
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("f8")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)

    block = {
        "type": "auto_upgrade_unit",
        "params": {"index": 1, "priority": "None", "input": "hotkey"},
    }
    runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 1)

    runner._keyboard.tap.assert_not_called()
    runner._keyboard.key_down.assert_called_once_with(keys.VK_F8)
    runner._keyboard.key_up.assert_called_once_with(keys.VK_F8)
    assert any("clear it back to off" in message for message in runner.logs)


def test_auto_upgrade_hotkey_unbound_is_actionable_and_safe(monkeypatch):
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)

    block = {
        "type": "auto_upgrade_unit",
        "params": {"index": 1, "priority": 1, "input": "hotkey"},
    }
    assert runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 1) is True

    runner._keyboard.tap.assert_not_called()
    assert any("Settings > Hotkeys" in message for message in runner.logs)


def test_legacy_auto_upgrade_block_still_uses_click_mode(monkeypatch):
    """An existing template has no input field, so missing must continue to
    mean click rather than silently changing behavior after an update."""
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("g")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        runner_blocks.vision,
        "find_image_any",
        lambda *a, **k: (
            {"cx": 300, "cy": 400, "score": 0.99},
            "priority_upgrade",
        ),
    )

    block = {"type": "auto_upgrade_unit", "params": {"index": 1, "priority": 2}}
    runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 1)

    runner._keyboard.tap.assert_not_called()
    assert [item.args for item in runner._mouse.click.call_args_list] == [
        (100, 200),
        (300, 400),
        (300, 400),
        (10, 20),
    ]


def test_auto_upgrade_click_waits_for_a_slow_info_panel(monkeypatch):
    """The panel can still be rendering AUTO_UPGRADE_CLICK_SETTLE after the
    unit is clicked -- a unit placed while a wave spawns was reported missing
    that one check and logging "not found" against a panel that showed up right
    afterwards. Keep looking until it does."""
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("g")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)

    attempts = []

    def slow_panel(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            return (None, None)
        return ({"cx": 300, "cy": 400, "score": 0.99}, "priority_upgrade")

    monkeypatch.setattr(runner_blocks.vision, "find_image_any", slow_panel)

    block = {
        "type": "auto_upgrade_unit",
        "params": {"index": 1, "priority": 2, "input": "click"},
    }
    assert runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 1) is True

    assert len(attempts) == 3, "gave up instead of waiting for the panel"
    assert [item.args for item in runner._mouse.click.call_args_list] == [
        (100, 200),   # select the unit
        (300, 400),   # cycle to priority 2
        (300, 400),
        (10, 20),     # close the info panel
    ]
    assert not any("not found" in message for message in runner.logs)


def test_auto_upgrade_click_gives_up_when_the_panel_never_opens(monkeypatch):
    """Waiting must be bounded -- a panel that genuinely never opens still has
    to end the block rather than poll forever, and must not cycle anything."""
    from core import runner_blocks
    import threading

    runner = _AutoUpgradeRunner("g")
    monkeypatch.setattr(runner_blocks.wm, "get_window_rect_screen",
                        lambda hwnd: (0, 0, 1152, 756))
    monkeypatch.setattr(runner_blocks.time, "sleep", lambda seconds: None)
    ticks = iter([step * 0.5 for step in range(200)])
    monkeypatch.setattr(runner_blocks.time, "time", lambda: next(ticks))
    monkeypatch.setattr(runner_blocks.vision, "find_image_any",
                        lambda *a, **k: (None, None))

    block = {
        "type": "auto_upgrade_unit",
        "params": {"index": 1, "priority": 2, "input": "click"},
    }
    assert runner._run_auto_upgrade_unit_tick(123, threading.Event(), block, 1) is True

    assert any("not found on the info panel" in message for message in runner.logs)
    assert [item.args for item in runner._mouse.click.call_args_list] == [(100, 200)]


# ---------------------------------------------------------------------------
# Navigation recovery: gamemode card search widening
# ---------------------------------------------------------------------------

class _CardRunner:
    """Just enough runner for _find_gamemode_card."""

    def __init__(self, boxed, wide):
        self._boxed, self._wide = boxed, wide
        self.searches = []
        self.logs = []

    _find_gamemode_card = None  # bound below

    def _log(self, msg):
        self.logs.append(msg)


def _make_card_runner(boxed, wide):
    from core.runner import MacroRunner
    r = _CardRunner(boxed, wide)
    r._find_gamemode_card = MacroRunner._find_gamemode_card.__get__(r, _CardRunner)
    return r


def _patch_card_search(monkeypatch, runner):
    from core import runner as runner_mod

    def wait_for_image_any(hwnd, names, region=None, timeout=None, stop_event=None):
        runner.searches.append("boxed" if region else "wide")
        # Mirrors the real helper: (None, None) when nothing matched.
        found = runner._boxed if region else runner._wide
        return (found, "expedition") if found is not None else (None, None)

    monkeypatch.setattr(runner_mod.vision, "wait_for_image_any", wait_for_image_any)


def test_gamemode_card_found_in_the_panel_never_widens(monkeypatch):
    """The boxed search exists to keep the 3D viewport out of the search --
    a hit there must not trigger a second, wider scan."""
    import threading

    runner = _make_card_runner(boxed={"cx": 700, "cy": 200, "score": 0.98}, wide=None)
    _patch_card_search(monkeypatch, runner)

    match, _ = runner._find_gamemode_card(1, threading.Event(), ("expedition",), "Expedition")
    assert match is not None
    assert runner.searches == ["boxed"]


def test_gamemode_card_outside_the_panel_is_still_found(monkeypatch):
    """The menu has gained cards (Tower, Event), so a mode can render outside
    GAMEMODE_CARD_REGION. That used to fail the whole task."""
    import threading

    runner = _make_card_runner(boxed=None, wide={"cx": 120, "cy": 640, "score": 0.95})
    _patch_card_search(monkeypatch, runner)

    match, _ = runner._find_gamemode_card(1, threading.Event(), ("expedition",), "Expedition")
    assert match is not None, "a card outside the box must still be found"
    assert runner.searches == ["boxed", "wide"]
    assert any("widening" in m for m in runner.logs)


def test_gamemode_card_genuinely_absent_reports_nothing(monkeypatch):
    import threading

    runner = _make_card_runner(boxed=None, wide=None)
    _patch_card_search(monkeypatch, runner)

    match, name = runner._find_gamemode_card(1, threading.Event(), ("expedition",), "Expedition")
    assert (match, name) == (None, None)
    assert runner.searches == ["boxed", "wide"]


def test_gamemode_card_does_not_widen_after_a_stop(monkeypatch):
    """Stop must not be followed by another 5s scan."""
    import threading

    runner = _make_card_runner(boxed=None, wide={"cx": 1, "cy": 1, "score": 1.0})
    _patch_card_search(monkeypatch, runner)

    stop = threading.Event()
    stop.set()
    runner._find_gamemode_card(1, stop, ("expedition",), "Expedition")
    assert runner.searches == ["boxed"]


# ---------------------------------------------------------------------------
# AFK Chamber: click out instead of polling a dead screen
# ---------------------------------------------------------------------------

class _AfkRunner:
    def __init__(self, match=None, raises=False):
        self._match, self._raises = match, raises
        self._mouse = MagicMock()
        self.logs = []
        self.searches = 0

    _dismiss_afk_chamber = None  # bound below

    def _log(self, msg):
        self.logs.append(msg)

    def _set_status(self, **kw):
        pass


def _make_afk_runner(match=None, raises=False, monkeypatch=None):
    from core import runner as runner_mod
    from core.runner import MacroRunner

    r = _AfkRunner(match, raises)
    r._dismiss_afk_chamber = MacroRunner._dismiss_afk_chamber.__get__(r, _AfkRunner)

    def find_image(hwnd, name, region=None, **kw):
        r.searches += 1
        if r._raises:
            raise runner_mod.vision.TemplateNotFound(name)
        return r._match

    monkeypatch.setattr(runner_mod.vision, "find_image", find_image)
    monkeypatch.setattr(runner_mod.wm, "get_window_rect_screen", lambda hwnd: (10, 20, 1152, 756))
    return r


def test_afk_chamber_is_clicked_out_of(monkeypatch):
    from core.runner_constants import AFK_CHAMBER_EXIT_CLICK

    r = _make_afk_runner(match={"cx": 576, "cy": 44, "score": 0.99}, monkeypatch=monkeypatch)
    at = r._dismiss_afk_chamber(1, 0.0)

    assert r._mouse.click.call_args.args == (10 + AFK_CHAMBER_EXIT_CLICK[0],
                                             20 + AFK_CHAMBER_EXIT_CLICK[1])
    assert at > 0.0, "the click time must be returned so the cooldown can start"
    assert any("AFK Chamber" in m for m in r.logs)


def test_afk_chamber_absent_does_nothing(monkeypatch):
    r = _make_afk_runner(match=None, monkeypatch=monkeypatch)
    assert r._dismiss_afk_chamber(1, 0.0) == 0.0
    r._mouse.click.assert_not_called()


def test_afk_chamber_without_a_reference_image_is_skipped(monkeypatch):
    """Optional check: a missing afk_chamber.png must not break a run."""
    r = _make_afk_runner(raises=True, monkeypatch=monkeypatch)
    assert r._dismiss_afk_chamber(1, 0.0) == 0.0
    r._mouse.click.assert_not_called()


def test_afk_chamber_is_not_reclicked_inside_the_cooldown(monkeypatch):
    """The banner lingers while the exit animates -- clicking every poll would
    fight the transition the first click started."""
    import time as _time

    r = _make_afk_runner(match={"cx": 576, "cy": 44, "score": 0.99}, monkeypatch=monkeypatch)
    just_now = _time.time()
    assert r._dismiss_afk_chamber(1, just_now) == just_now
    r._mouse.click.assert_not_called()
    assert r.searches == 0, "the cooldown should short-circuit before searching"
