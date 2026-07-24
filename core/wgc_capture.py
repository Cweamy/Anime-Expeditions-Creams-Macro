"""Windows.Graphics.Capture (WGC) backend for reading the Roblox window.

WHY THIS EXISTS: on hardware-accelerated / flip-model Roblox, the stock
capture paths (mss BitBlt screen grab, PrintWindow window capture) come back
BLACK once the game is the foreground window, so every image search fails
even though the game is clearly on screen. WGC reads the window's composed
frames straight from DWM and returns real pixels regardless of occlusion or
flip-model presentation -- it's what OBS's "Windows Graphics Capture" and
Xbox Game Bar use.

OPT-IN (default off, see set_enabled): most setups never hit the black-frame
problem, and WGC needs the game to stay a top-level window (cutout mode) plus
runs a continuous background capture session, so it's gated behind the
"use_wgc_capture" setting rather than always-on. While disabled, get_grabber
is never even reached from vision.capture_game_gray, so no session starts.

When enabled, runs ONE background capture session on the top-level window
titled "Roblox" and caches the latest frame; vision.capture_game_gray reads
it on demand. Falls back silently (returns None) if WGC or the window isn't
available, so the original capture paths still run.
"""
import threading
import time

WINDOW_NAME = "Roblox"
STALE_SECS = 2.0        # a frame older than this = session likely dead (relaunch)
RESTART_COOLDOWN = 3.0  # don't try to (re)start the session more often than this

# Off until main.Api enables it from the "use_wgc_capture" setting -- keeps
# the background capture session (and its per-frame copy) from ever starting
# on the vast majority of setups that don't need it.
_enabled = False
_grabber = None
_grabber_lock = threading.Lock()


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def is_enabled() -> bool:
    return _enabled


def get_grabber():
    global _grabber
    with _grabber_lock:
        if _grabber is None:
            _grabber = _WGCGrabber(WINDOW_NAME)
        return _grabber


class _WGCGrabber:
    def __init__(self, window_name):
        self.window_name = window_name
        self._latest = None          # latest BGR numpy frame (H, W, 3)
        self._latest_ts = 0.0
        self._lock = threading.Lock()
        self._control = None
        self._last_restart = 0.0

    def _start_session(self):
        try:
            from windows_capture import WindowsCapture
        except Exception:
            return
        try:
            cap = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                monitor_index=None,
                window_name=self.window_name,
            )

            @cap.event
            def on_frame_arrived(frame, capture_control):
                buf = frame.frame_buffer          # H x W x 4, BGRA
                with self._lock:
                    self._latest = buf[:, :, :3].copy()
                    self._latest_ts = time.time()

            @cap.event
            def on_closed():
                pass

            self._control = cap.start_free_threaded()
        except Exception:
            # window not found yet / WGC unavailable -- caller falls back
            self._control = None

    def _restart(self):
        now = time.time()
        if now - self._last_restart < RESTART_COOLDOWN:
            return
        self._last_restart = now
        try:
            if self._control is not None:
                self._control.stop()
        except Exception:
            pass
        self._control = None
        self._start_session()
        # give the fresh session a moment to land its first frame
        deadline = time.time() + 1.5
        while time.time() < deadline:
            with self._lock:
                if self._latest is not None and (time.time() - self._latest_ts) < STALE_SECS:
                    return
            time.sleep(0.05)

    def frame(self):
        """Latest BGR frame as a numpy array, or None if none is fresh.

        Lazily starts the session on first use and restarts it (rate-limited)
        whenever frames go stale -- e.g. after a rejoin relaunches Roblox under
        a new window handle."""
        now = time.time()
        with self._lock:
            if self._latest is not None and (now - self._latest_ts) < STALE_SECS:
                return self._latest
        self._restart()
        with self._lock:
            if self._latest is not None and (time.time() - self._latest_ts) < STALE_SECS:
                return self._latest
        return None
