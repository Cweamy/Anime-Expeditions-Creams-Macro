import threading

from core import runner_bounty
from core.runner_bounty import BountyOps


class _Harness(BountyOps):
    def __init__(self):
        self._get_bounty_settings = lambda: {
            "enabled": True, "play_mode": "solo",
            "summon_banner": "standard", "maps": {},
        }
        self.logs = []
        self.board_opens = 0
        self.board_leaves = 0
        self.clicks = 0
        self.click_details = []
        self.board_stays_open = False
        self.webhook_events = []
        self.keyboard_taps = []
        self.saved_remaining = []
        self._set_bounty_remaining = (
            lambda remaining, total=None:
                self.saved_remaining.append((remaining, total)))
        self._keyboard = type(
            "_Keyboard", (), {
                "tap": lambda keyboard, key, hold=0.03:
                    self.keyboard_taps.append((key, hold)),
            })()
        self.objective = {
            "kind": "infinite",
            "target_wave": 30,
            "cx": 500,
            "cy": 400,
            "h": 9,
            "signature": ("infinite", 30, 12345),
        }

    def _log(self, message):
        self.logs.append(message)

    def _checkpoint(self, _stop_event):
        return False

    def _set_status(self, **_kwargs):
        pass

    def _interruptible_sleep(self, _seconds, _stop_event):
        pass

    def _open_bounty_board(self, _hwnd, _stop_event):
        self.board_opens += 1
        return True

    def _find_next_bounty(self, _hwnd, _stop_event, attempted):
        if self._bounty_was_attempted(self.objective["signature"], attempted):
            return None
        return self.objective

    def _click_ref(self, _hwnd, _x, _y, hold=0.05):
        self.clicks += 1
        self.click_details.append((_x, _y, hold))

    def _read_bounty_destination_map(self, *_args, **_kwargs):
        return None

    def _wait_ocr_line(self, _hwnd, _stop_event, text, _timeout):
        if self.board_stays_open and text == "Bounty Board":
            return {"text": "Bounty Board"}
        return None

    def _save_debug_screenshot_unconditional(self, *_args, **_kwargs):
        return "bounty_reward.png"

    def _send_event_webhook(self, *args, **kwargs):
        self.webhook_events.append((args, kwargs))

    def _recover_to_lobby(self, *_args, **_kwargs):
        return True

    def _ensure_lobby(self, *_args, **_kwargs):
        return True

    def _leave_bounty_board(self, *_args, **_kwargs):
        self.board_leaves += 1
        return True


def test_failed_objective_click_is_retried_before_runner_moves_on(monkeypatch):
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)
    runner = _Harness()

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runner.board_opens == 4
    retry_logs = [line for line in runner.logs if "returning to the board to retry it" in line]
    assert len(retry_logs) == 2
    assert any("giving up on this objective after 3 attempts" in line for line in runner.logs)


def test_incomplete_map_setup_skips_board_entirely():
    runner = _Harness()
    runner._get_bounty_settings = lambda: {
        "enabled": True,
        "setup_ready": False,
        "missing_maps": ["Flower Forest"],
        "invalid_maps": [],
    }

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is False

    assert runner.board_opens == 0
    assert any(
        "every Story map needs a saved Macro Operation" in line
        for line in runner.logs)


def test_missed_click_uses_all_three_attempts_while_board_remains_open(monkeypatch):
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)
    runner = _Harness()
    runner.board_stays_open = True

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runner.clicks == 9
    assert all(detail == (500, 402, 0.1) for detail in runner.click_details)
    assert sum("Bounty objective click did not register" in line
               for line in runner.logs) == 9


def test_find_next_bounty_finishes_current_card_before_later_card(monkeypatch):
    runner = _Harness()
    state = {"first_card_scrolled": False}

    class _Mouse:
        def move_to(self, *_args):
            pass

        def nudge(self):
            pass

        def scroll(self, _amount):
            pass

        def drag(self, x1, _y1, _x2, _y2, duration):
            if x1 == 290:
                state["first_card_scrolled"] = True

    runner._mouse = _Mouse()
    cards = [
        {"x": 290, "from_y": 180, "to_y": 260, "card": (100, 100, 200, 250)},
        {"x": 590, "from_y": 180, "to_y": 260, "card": (400, 100, 200, 250)},
    ]
    first_card_objective = {
        "cx": 200, "cy": 220, "signature": ("infinite", 15, 111)}
    later_card_objective = {
        "cx": 500, "cy": 180, "signature": ("infinite", 30, 222)}

    monkeypatch.setattr(
        runner_bounty.vision, "ref_to_screen", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_card_scrolls", lambda _frame: cards)
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_claim_buttons",
        lambda _frame, _cards=None: [])
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_summon_objectives",
        lambda _frame, _cards=None: [])
    monkeypatch.setattr(
        runner_bounty.bounty,
        "detect_objectives",
        lambda _frame: (
            [first_card_objective, later_card_objective]
            if state["first_card_scrolled"] else [later_card_objective]),
    )

    found = BountyOps._find_next_bounty(
        runner, 123, threading.Event(), attempted=[])

    assert found is first_card_objective


def test_find_next_bounty_does_not_drag_a_card_without_scrollbar(monkeypatch):
    runner = _Harness()
    drag_calls = []

    class _Mouse:
        def move_to(self, *_args):
            pass

        def nudge(self):
            pass

        def scroll(self, _amount):
            pass

        def drag(self, *args, **kwargs):
            drag_calls.append((args, kwargs))

    runner._mouse = _Mouse()
    cards = [
        {
            "x": 290, "from_y": 180, "to_y": 260,
            "card": (100, 100, 200, 250), "has_scrollbar": False,
        },
        {
            "x": 590, "from_y": 180, "to_y": 260,
            "card": (400, 100, 200, 250), "has_scrollbar": False,
        },
    ]
    later_card_objective = {
        "cx": 500, "cy": 180, "signature": ("infinite", 30, 222)}

    monkeypatch.setattr(
        runner_bounty.vision, "ref_to_screen", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_card_scrolls", lambda _frame: cards)
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_claim_buttons",
        lambda _frame, _cards=None: [])
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_summon_objectives",
        lambda _frame, _cards=None: [])
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_objectives",
        lambda _frame: [later_card_objective])

    found = BountyOps._find_next_bounty(
        runner, 123, threading.Event(), attempted=[])

    assert found is later_card_objective
    assert drag_calls == []


def test_find_next_bounty_uses_largest_remaining_summon_amount(monkeypatch):
    runner = _Harness()

    class _Mouse:
        def move_to(self, *_args):
            pass

        def nudge(self):
            pass

        def scroll(self, _amount):
            pass

    runner._mouse = _Mouse()
    cards = [
        {
            "x": 420, "from_y": 180, "to_y": 260,
            "card": (250, 180, 200, 230), "has_scrollbar": False,
        },
        {
            "x": 720, "from_y": 180, "to_y": 260,
            "card": (550, 180, 200, 230), "has_scrollbar": False,
        },
    ]
    summons = {
        250: {
            "kind": "summon", "target_summons": 250,
            "remaining_summons": 200, "signature": ("summon", 250, 0),
        },
        550: {
            "kind": "summon", "target_summons": 500,
            "remaining_summons": 450, "signature": ("summon", 500, 1),
        },
    }
    monkeypatch.setattr(
        runner_bounty.vision, "ref_to_screen", lambda _hwnd, x, y: (x, y))
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_card_scrolls", lambda _frame: cards)
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_claim_buttons",
        lambda _frame, _cards=None: [])
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_objectives", lambda _frame: [])
    monkeypatch.setattr(
        runner_bounty.bounty,
        "detect_summon_objectives",
        lambda _frame, selected=None: (
            [summons[selected[0]["card"][0]]] if selected else []),
    )

    found = BountyOps._find_next_bounty(
        runner, 123, threading.Event(), attempted=[])

    assert found["kind"] == "summon"
    assert found["target_summons"] == 500
    assert found["remaining_summons"] == 450


def test_run_summon_bounty_runs_purchase_and_reward_click_per_batch(monkeypatch):
    runner = _Harness()
    menu = {
        "tabs": {
            "standard": {"cx": 610, "cy": 170},
            "villain": {"cx": 482, "cy": 170},
        },
        "summon_50": {"cx": 870, "cy": 578},
    }
    runner._wait_fuzzy_ocr_line = lambda *_args, **_kwargs: {
        "cx": 110, "cy": 450}
    runner._wait_lobby_summon = lambda *_args, **_kwargs: {
        "cx": 110, "cy": 450}
    runner._wait_summon_menu = lambda *_args, **_kwargs: menu
    runner._capture_summon_menu = lambda _hwnd: menu
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)

    objective = {"remaining_summons": 420}
    assert runner._run_summon_bounty(
        123, threading.Event(), objective,
        {"summon_banner": "standard"}) is True

    assert runner.click_details.count((870, 578, 0.1)) == 18
    assert any("completed all 9" in line for line in runner.logs)


def test_run_summon_bounty_retries_open_menu_key(monkeypatch):
    runner = _Harness()
    menu = {
        "tabs": {
            "standard": {"cx": 610, "cy": 170},
            "villain": {"cx": 482, "cy": 170},
        },
        "summon_50": {"cx": 870, "cy": 578},
    }
    runner._wait_fuzzy_ocr_line = lambda *_args, **_kwargs: {
        "cx": 110, "cy": 450}
    runner._wait_lobby_summon = lambda *_args, **_kwargs: {
        "cx": 110, "cy": 450}
    menu_reads = iter([None, menu, menu, menu])
    runner._wait_summon_menu = lambda *_args, **_kwargs: next(menu_reads)
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)

    assert runner._run_summon_bounty(
        123, threading.Event(), {"remaining_summons": 50},
        {"summon_banner": "standard"}) is True

    assert runner.keyboard_taps[:2] == [
        (ord("E"), 0.12),
        (ord("E"), 0.12),
    ]
    assert any("attempt 1/3" in line for line in runner.logs)


def test_open_bounty_board_is_idempotent_when_board_is_already_visible(
        monkeypatch):
    runner = _Harness()
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty.ocr_windows,
        "ocr_lines",
        lambda _frame: [
            {"text": "Bounty Board", "cx": 500, "cy": 90},
            {"text": "Bounties Left", "cx": 1040, "cy": 85},
        ],
    )
    monkeypatch.setattr(
        runner_bounty.bounty, "read_bounties_left",
        lambda _frame: (1, 10),
    )

    assert BountyOps._open_bounty_board(
        runner, 123, threading.Event()) is True

    assert runner.click_details == []
    assert any("already open" in line for line in runner.logs)


def test_run_bounties_does_not_fall_back_to_smaller_shared_summon_target(
        monkeypatch):
    runner = _Harness()
    objectives = [
        {
            "kind": "summon", "target_summons": 500,
            "remaining_summons": 500, "signature": ("summon", 500, 0),
        },
        {
            "kind": "summon", "target_summons": 500,
            "remaining_summons": 500, "signature": ("summon", 500, 0),
        },
        {
            "kind": "summon", "target_summons": 250,
            "remaining_summons": 250, "signature": ("summon", 250, 1),
        },
        None,
    ]
    runs = []
    runner._find_next_bounty = (
        lambda _hwnd, _stop, _attempted: objectives.pop(0))
    runner._run_summon_bounty = (
        lambda _hwnd, _stop, objective, _settings:
            runs.append((objective["target_summons"],
                         objective["remaining_summons"])) or True)
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: None)

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runs == [(500, 500)]
    assert any("made no progress" in line for line in runner.logs)
    assert any("ignoring the smaller Summon 250" in line
               for line in runner.logs)


def test_run_bounties_retries_only_real_shared_summon_remainder(monkeypatch):
    runner = _Harness()
    objectives = [
        {
            "kind": "summon", "target_summons": 500,
            "remaining_summons": 500, "signature": ("summon", 500, 0),
        },
        {
            "kind": "summon", "target_summons": 500,
            "remaining_summons": 100, "signature": ("summon", 500, 0),
        },
        None,
    ]
    runs = []
    runner._find_next_bounty = (
        lambda _hwnd, _stop, _attempted: objectives.pop(0))
    runner._run_summon_bounty = (
        lambda _hwnd, _stop, objective, _settings:
            runs.append(objective["remaining_summons"]) or True)
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: None)

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runs == [500, 100]


def test_claim_completed_bounty_verifies_button_disappeared(monkeypatch):
    runner = _Harness()
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    reward = {
        "description": "50x Event Coin",
        "close_cx": 576,
        "close_cy": 465,
    }
    monkeypatch.setattr(
        runner_bounty.bounty, "read_reward_overlay",
        lambda _frame: reward if runner.clicks == 1 else None)
    claim = {"cx": 700, "cy": 500}

    assert runner._claim_completed_bounty(
        123, threading.Event(), claim, {"enabled": True}) is True

    assert runner.click_details == [(700, 500, 0.1), (576, 465, 0.1)]
    assert any("reward collected and overlay closed" in line for line in runner.logs)
    assert len(runner.webhook_events) == 1


def test_claim_completed_bounty_accepts_disabled_claim_after_overlay_is_gone(
        monkeypatch):
    runner = _Harness()
    monkeypatch.setattr(runner_bounty.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(runner_bounty, "BOUNTY_NAV_CLICK_VERIFY_TIMEOUT", 0.01)
    monkeypatch.setattr(runner_bounty.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty, "read_reward_overlay", lambda _frame: None)
    monkeypatch.setattr(
        runner_bounty.bounty, "detect_claim_buttons", lambda _frame: [])
    claim = {"cx": 700, "cy": 500}

    assert runner._claim_completed_bounty(
        123, threading.Event(), claim, {"enabled": True}) is True

    assert runner.click_details == [(700, 500, 0.1)]
    assert any("claim control is now disabled" in line for line in runner.logs)


def test_run_bounties_claims_every_visible_card_before_leaving_board():
    runner = _Harness()
    objectives = [
        {"kind": "claim", "cx": 300, "cy": 500},
        {"kind": "claim", "cx": 600, "cy": 500},
        None,
    ]
    claimed = []
    runner._find_next_bounty = (
        lambda _hwnd, _stop, _attempted: objectives.pop(0))
    runner._claim_completed_bounty = (
        lambda _hwnd, _stop, claim, _webhook: claimed.append(claim["cx"]) or True)

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert claimed == [300, 600]
    assert runner.board_opens == 1
    assert runner.board_leaves == 1
    assert any("moving on to Challenge and the Task Queue" in line for line in runner.logs)


def test_run_bounties_reports_unsupported_remaining_count_and_moves_on(monkeypatch):
    runner = _Harness()
    runner._find_next_bounty = lambda _hwnd, _stop, _attempted: None
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty, "read_bounties_left",
        lambda _frame: (2, 10))

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runner.board_leaves == 1
    assert runner.saved_remaining == [(2, 10)]
    assert any(
        "2/10 bounties remain, but none can currently be completed" in line
        for line in runner.logs
    )
    assert any(
        "moving on to Challenge and the Task Queue" in line
        for line in runner.logs
    )


def test_run_bounties_skips_board_when_tracker_is_zero():
    runner = _Harness()
    runner._get_bounty_settings = lambda: {
        "enabled": True, "remaining": 0, "total": 10,
        "play_mode": "solo", "summon_banner": "standard", "maps": {},
    }

    assert runner._run_bounties(
        123, threading.Event(), {}, {}, {}) is True

    assert runner.board_opens == 0
    assert any("0 bounties remain" in line for line in runner.logs)


def test_leave_bounty_board_returns_to_lobby_without_removed_region(monkeypatch):
    runner = _Harness()
    monkeypatch.setattr(
        runner_bounty.vision, "capture_game_bgr", lambda _hwnd: object())
    monkeypatch.setattr(
        runner_bounty.bounty.ocr_windows,
        "ocr_lines",
        lambda _frame: [{"text": "Back", "cx": 80, "cy": 715}],
    )
    monkeypatch.setattr(
        runner_bounty.wm, "activate_window", lambda _hwnd: True)
    monkeypatch.setattr(
        runner_bounty.vision,
        "wait_for_image_any",
        lambda *_args, **_kwargs: ({"score": 1.0}, "nav_play"),
    )

    assert BountyOps._leave_bounty_board(
        runner, 123, threading.Event()) is True

    assert runner.click_details == [(80, 715, 0.05)]
    assert any("Leaving Bounty Board" in line for line in runner.logs)
