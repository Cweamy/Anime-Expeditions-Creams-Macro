"""Thread-safe MSS instance lifecycle manager for screen capture operations.

Provides thread-local reuse of mss.MSS objects to eliminate GDI handle thrashing
during continuous polling while ensuring explicit resource cleanup via close()
when worker threads finish or when the application shuts down.
"""
import threading

_mss_lock = threading.Lock()
_active_instances = set()
_thread_local = threading.local()
_factory = None  # Custom factory for testing (e.g. FakeMSS)


def set_mss_factory(factory):
    """Sets a custom MSS factory function (used by unit tests to inject a FakeMSS)."""
    global _factory
    _factory = factory


def get_mss():
    """Returns the thread-local mss.MSS instance, creating one if not present."""
    sct = getattr(_thread_local, "sct", None)
    if sct is None:
        if _factory is not None:
            sct = _factory()
        else:
            import mss
            sct = mss.MSS()
        _thread_local.sct = sct
        with _mss_lock:
            _active_instances.add(sct)
    return sct


def close_mss():
    """Closes and removes the mss.MSS instance for the current thread."""
    sct = getattr(_thread_local, "sct", None)
    if sct is not None:
        _thread_local.sct = None
        with _mss_lock:
            _active_instances.discard(sct)
        try:
            sct.close()
        except Exception:
            pass


def close_all_mss():
    """Closes all active mss.MSS instances across all threads."""
    with _mss_lock:
        instances = list(_active_instances)
        _active_instances.clear()
    _thread_local.sct = None
    for sct in instances:
        try:
            sct.close()
        except Exception:
            pass
