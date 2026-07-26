import threading
import numpy as np
import pytest

from core import mss_manager


class FakeScreenShot:
    def __init__(self, width=10, height=10):
        self.width = width
        self.height = height
        self.raw = np.full((height, width, 4), 128, dtype=np.uint8).tobytes()


class FakeMSS:
    def __init__(self, raise_on_grab=False):
        self.closed = False
        self.grab_count = 0
        self.raise_on_grab = raise_on_grab

    def grab(self, rect):
        if self.closed:
            raise RuntimeError("Cannot grab on a closed MSS instance!")
        if self.raise_on_grab:
            raise RuntimeError("Simulated MSS capture error")
        self.grab_count += 1
        w = rect.get("width", 10)
        h = rect.get("height", 10)
        return FakeScreenShot(width=w, height=h)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_mss_manager():
    """Resets MSS manager state before and after each test."""
    mss_manager.close_all_mss()
    mss_manager.set_mss_factory(None)
    yield
    mss_manager.close_all_mss()
    mss_manager.set_mss_factory(None)


def test_reuse_within_same_thread():
    """Multiple calls in the same thread should return the exact same MSS instance."""
    created = []

    def factory():
        inst = FakeMSS()
        created.append(inst)
        return inst

    mss_manager.set_mss_factory(factory)

    inst1 = mss_manager.get_mss()
    inst2 = mss_manager.get_mss()

    assert inst1 is inst2
    assert len(created) == 1
    assert not inst1.closed


def test_isolation_between_threads():
    """Different threads must receive distinct MSS instances."""
    thread_instances = {}

    def factory():
        return FakeMSS()

    mss_manager.set_mss_factory(factory)

    def worker(name):
        thread_instances[name] = mss_manager.get_mss()

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert thread_instances["t1"] is not thread_instances["t2"]
    assert len(mss_manager._active_instances) == 2


def test_explicit_close_and_recreation():
    """close_mss must close the instance, and a subsequent get_mss must create a fresh one."""
    created = []

    def factory():
        inst = FakeMSS()
        created.append(inst)
        return inst

    mss_manager.set_mss_factory(factory)

    inst1 = mss_manager.get_mss()
    assert len(created) == 1

    mss_manager.close_mss()
    assert inst1.closed
    assert getattr(mss_manager._thread_local, "sct", None) is None

    inst2 = mss_manager.get_mss()
    assert len(created) == 2
    assert inst2 is not inst1
    assert not inst2.closed


def test_close_all_mss_cleans_every_thread():
    """close_all_mss must close all tracked active instances."""
    instances = []

    def factory():
        inst = FakeMSS()
        instances.append(inst)
        return inst

    mss_manager.set_mss_factory(factory)

    def worker():
        mss_manager.get_mss()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(instances) == 3
    mss_manager.close_all_mss()
    assert all(inst.closed for inst in instances)
    assert len(mss_manager._active_instances) == 0


def test_capture_error_handling():
    """When a capture error occurs, close_mss resets the thread state so next call recreates instance."""
    created = []

    def factory():
        # First instance fails on grab, second instance succeeds
        fail = len(created) == 0
        inst = FakeMSS(raise_on_grab=fail)
        created.append(inst)
        return inst

    mss_manager.set_mss_factory(factory)

    # First call gets failing instance
    inst1 = mss_manager.get_mss()
    with pytest.raises(RuntimeError, match="Simulated MSS capture error"):
        try:
            inst1.grab({"left": 0, "top": 0, "width": 10, "height": 10})
        except Exception:
            mss_manager.close_mss()
            raise

    assert inst1.closed
    assert getattr(mss_manager._thread_local, "sct", None) is None

    # Next get_mss call creates a fresh working instance
    inst2 = mss_manager.get_mss()
    assert inst2 is not inst1
    shot = inst2.grab({"left": 0, "top": 0, "width": 10, "height": 10})
    assert shot is not None
    assert inst2.grab_count == 1
