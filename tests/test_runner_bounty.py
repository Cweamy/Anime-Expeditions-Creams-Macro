import threading

from core import runner_bounty
from core.runner_bounty import BountyOps


class _Harness(BountyOps):
    def __init__(self):
        self._get_bounty_settings = lambda: {"enabled": True, "play_mode": "solo", "maps": {}}
        self.logs = []
        self.board_opens = 0
        self.clicks = 0
        self.click_details = []
        self.board_stays_open = False
        self.webhook_events = []
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

    def _leave_bounty_board(self, *_args, **_kwargs):
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
        runner_bounty.bounty,
        "detect_objectives",
        lambda _frame: (
            [first_card_objective, later_card_objective]
            if state["first_card_scrolled"] else [later_card_objective]),
    )

    found = BountyOps._find_next_bounty(
        runner, 123, threading.Event(), attempted=[])

    assert found is first_card_objective


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
