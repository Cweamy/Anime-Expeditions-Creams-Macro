import threading

from core import runner_bounty
from core.runner_bounty import BountyOps


class _Harness(BountyOps):
    def __init__(self):
        self._get_bounty_settings = lambda: {"enabled": True, "play_mode": "solo", "maps": {}}
        self.logs = []
        self.board_opens = 0
        self.clicks = 0
        self.board_stays_open = False
        self.objective = {
            "kind": "infinite",
            "target_wave": 30,
            "cx": 500,
            "cy": 400,
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

    def _click_ref(self, _hwnd, _x, _y):
        self.clicks += 1

    def _read_bounty_destination_map(self, *_args, **_kwargs):
        return None

    def _wait_ocr_line(self, _hwnd, _stop_event, text, _timeout):
        if self.board_stays_open and text == "Bounty Board":
            return {"text": "Bounty Board"}
        return None

    def _save_debug_screenshot_unconditional(self, *_args, **_kwargs):
        return None

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
    assert sum("Bounty objective click did not register" in line
               for line in runner.logs) == 9
