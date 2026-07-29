"""Thread-safe MSS instance lifecycle manager for screen capture operations.

Provides thread-local reuse of MSS capture objects to eliminate GDI handle thrashing
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
    """Return the thread-local capture instance, creating one if absent.

    Current MSS releases expose the public constructor as ``mss.mss()``.
    Some older/bundled builds exposed ``MSS()`` instead, so retaining that
    fallback keeps existing packaged installations compatible.
    """
    sct = getattr(_thread_local, "sct", None)
    if sct is None:
        if _factory is not None:
            sct = _factory()
        else:
            import mss
            constructor = getattr(mss, "mss", None) or getattr(mss, "MSS", None)
            if constructor is None:
                raise RuntimeError("Installed mss package has no capture constructor.")
            sct = constructor()
        _thread_local.sct = sct
        with _mss_lock:
            _active_instances.add(sct)
    return sct


def close_mss():
    """Closes and removes the MSS instance for the current thread."""
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
    """Closes all active MSS instances across all threads."""
    with _mss_lock:
        instances = list(_active_instances)
        _active_instances.clear()
    _thread_local.sct = None
    for sct in instances:
        try:
            sct.close()
        except Exception:
            pass
