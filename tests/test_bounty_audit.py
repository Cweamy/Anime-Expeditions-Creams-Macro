import json

import numpy as np

from core import bounty_audit


def test_bounty_audit_writes_events_and_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(bounty_audit.constants, "APP_DIR", str(tmp_path))
    recorder = bounty_audit.BountyAudit()
    path = recorder.frame("card 2 / bottom", np.zeros((12, 16, 3), dtype=np.uint8),
                          card=2, confidence=0.93)
    recorder.event("scroll_check", registered=True, coordinates=(10, 20))
    recorder.close()

    assert path is not None
    assert path.endswith(".jpg")
    with open(recorder.events_path, encoding="utf-8") as stream:
        events = [json.loads(line) for line in stream]
    assert events[0]["event"] == "audit_started"
    assert any(item["event"] == "frame" for item in events)
    scroll = next(item for item in events if item["event"] == "scroll_check")
    assert scroll["coordinates"] == [10, 20]
    assert (tmp_path / "audit" / "bounty").is_dir()
