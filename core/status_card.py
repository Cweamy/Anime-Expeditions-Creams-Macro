"""Renders the match-result "status card" attached to the webhook -- a
compact Current Status + Win/Loss panel drawn with OpenCV (already a
dependency, so no extra install) rather than assembled from Discord embed
fields, which can't do the tile / progress-bar / activity-grid layout.
Colors and layout are our own.

The single entry point is render_status_card_bgr(): it returns a BGR image
array (what OpenCV works in), or None if anything goes wrong -- the webhook
caller composites it under the live game screenshot and falls back to just
the screenshot if this returns None, so a rendering failure never costs the
notification itself.
"""
import cv2
import numpy as np

CARD_W = 1152  # matches the docked game's client width, so the card stacks cleanly under a screenshot

# BGR (OpenCV order), not RGB.
_BG = (24, 20, 18)
_PANEL = (44, 36, 32)
_EMPTY = (52, 44, 40)     # an un-played activity-grid cell
_TEXT = (238, 238, 238)
_MUTED = (150, 146, 142)
_GREEN = (96, 210, 96)
_RED = (86, 86, 232)
_PURPLE = (214, 130, 176)
_PINK = (150, 92, 226)
_GOLD = (72, 198, 236)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_FONT_B = cv2.FONT_HERSHEY_DUPLEX


def _text(img, s, org, scale, color, thickness=1, font=_FONT):
    cv2.putText(img, str(s), org, font, scale, color, thickness, cv2.LINE_AA)


def _panel(img, x, y, w, h, accent):
    cv2.rectangle(img, (x, y), (x + w, y + h), _PANEL, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), accent, 1)


def _status_tile(img, x, y, w, h, accent, label, value, value_scale=0.85):
    """A Current-Status tile: small caps label, one accent value line."""
    _panel(img, x, y, w, h, accent)
    _text(img, label.upper(), (x + 16, y + 28), 0.5, _MUTED, 1)
    _text(img, value, (x + 16, y + 60), value_scale, accent, 2, _FONT_B)


def _stat_tile(img, x, y, w, h, accent, title, value, sub_label, sub_value):
    """A Win/Loss tile: caps title, one big accent number, muted sub-row."""
    _panel(img, x, y, w, h, accent)
    _text(img, title.upper(), (x + 16, y + 30), 0.6, _MUTED, 1)
    _text(img, value, (x + 16, y + 78), 1.35, accent, 3, _FONT_B)
    _text(img, sub_label.upper(), (x + 16, y + h - 26), 0.45, _MUTED, 1)
    _text(img, sub_value, (x + 16, y + h - 9), 0.55, _TEXT, 1)


def _activity_grid(img, x, y, results, max_cells=20, cols=10):
    """GitHub-contribution-style grid of the recent match results -- a small,
    fixed window (the last `max_cells`, laid out `cols` per row, row-major
    left->right / oldest->newest) rather than a wall spanning the whole card.
    win -> green cell, loss -> red, not played yet -> muted empty cell.
    Returns the grid's pixel height."""
    cell, gap = 26, 6
    step = cell + gap
    data = (results or [])[-max_cells:]
    rows = (max_cells + cols - 1) // cols
    for i in range(max_cells):
        col, row = i % cols, i // cols
        cx, cy = x + col * step, y + row * step
        color = (_GREEN if data[i] else _RED) if i < len(data) else _EMPTY
        cv2.rectangle(img, (cx, cy), (cx + cell, cy + cell), color, -1)
    return rows * step - gap


def render_status_card_bgr(*, is_win, action, last_run_duration, last_run_win,
                            time_until_challenge, session_wins, session_losses,
                            all_time_wins, all_time_losses, runs_per_hour="-", results=None):
    try:
        sw, sl = int(session_wins or 0), int(session_losses or 0)
        aw, al = int(all_time_wins or 0), int(all_time_losses or 0)
        s_total, a_total = sw + sl, aw + al
        s_rate = round(sw / s_total * 100) if s_total else 0
        a_rate = round(aw / a_total * 100) if a_total else 0
        tuc = str(time_until_challenge or "-")
        rph = str(runs_per_hour or "-")

        pad, gap = 28, 20
        h = 434
        img = np.full((h, CARD_W, 3), _BG, dtype=np.uint8)

        # ── CURRENT STATUS ──────────────────────────────────────────────
        _text(img, "CURRENT STATUS", (pad, 34), 0.62, _MUTED, 1)
        row_y, row_h = 48, 82
        tw3 = (CARD_W - pad * 2 - gap * 2) // 3
        _status_tile(img, pad, row_y, tw3, row_h, _PINK, "Action", action)
        # Last Run tile carries a W/L badge on the right.
        lr_x = pad + tw3 + gap
        _status_tile(img, lr_x, row_y, tw3, row_h, _GOLD, "Last Run", last_run_duration or "-")
        badge, badge_col = ("W", _GREEN) if last_run_win else ("L", _RED)
        bx = lr_x + tw3 - 56
        cv2.rectangle(img, (bx, row_y + 26), (bx + 40, row_y + 58), badge_col, -1)
        _text(img, badge, (bx + 12, row_y + 50), 0.8, _BG, 2, _FONT_B)
        # Time Until Challenge -- long strings ("No stages enabled") shrink.
        ch_x = pad + (tw3 + gap) * 2
        _status_tile(img, ch_x, row_y, tw3, row_h, _PURPLE, "Time Until Challenge", tuc,
                     value_scale=0.85 if len(tuc) <= 10 else 0.6)

        # ── WIN / LOSS & SESSION STATS ──────────────────────────────────
        wl_y = 164
        _text(img, "WIN / LOSS", (pad, wl_y), 0.62, _MUTED, 1)
        ty, th = wl_y + 12, 130
        tw4 = (CARD_W - pad * 2 - gap * 3) // 4
        _stat_tile(img, pad, ty, tw4, th, _GREEN, "Wins", sw, "All Time", aw)
        _stat_tile(img, pad + (tw4 + gap), ty, tw4, th, _RED, "Losses", sl, "All Time", al)
        _stat_tile(img, pad + (tw4 + gap) * 2, ty, tw4, th, _PURPLE, "Win %", f"{s_rate}%",
                   "All Time", f"{a_rate}%  ({aw}/{a_total})" if a_total else "-")
        _stat_tile(img, pad + (tw4 + gap) * 3, ty, tw4, th, _GOLD, "Runs / h", rph,
                   "Session", f"{s_total} total" if s_total else "-")

        # ── SESSION ACTIVITY (GitHub-style grid) ────────────────────────
        ga_y = ty + th + 26
        _text(img, "SESSION ACTIVITY", (pad, ga_y), 0.62, _MUTED, 1)
        # legend, right-aligned on the same line as the label
        lg_x = CARD_W - pad - 210
        cv2.rectangle(img, (lg_x, ga_y - 12), (lg_x + 13, ga_y + 1), _RED, -1)
        _text(img, "Loss", (lg_x + 20, ga_y), 0.5, _MUTED, 1)
        cv2.rectangle(img, (lg_x + 90, ga_y - 12), (lg_x + 103, ga_y + 1), _GREEN, -1)
        _text(img, "Win", (lg_x + 110, ga_y), 0.5, _MUTED, 1)
        _activity_grid(img, pad, ga_y + 16, results, max_cells=20, cols=10)

        return img
    except Exception:
        return None
