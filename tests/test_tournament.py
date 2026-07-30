import re
from pathlib import Path

from core import runner_constants as rc

APP_JS = Path(__file__).resolve().parent.parent / "ui" / "app.js"


def test_tournament_type_images_and_order_stay_in_sync():
    """_reach_tournament_selected looks a type up in TOURNAMENT_TYPE_IMAGES; the
    Task Builder offers TOURNAMENT_TYPE_ORDER. A type in one but not the other
    means an offered choice with no card to click (or vice versa), so the two
    have to be edited together -- same guard as the Event acts have."""
    assert set(rc.TOURNAMENT_TYPE_IMAGES) == set(rc.TOURNAMENT_TYPE_ORDER)


def test_every_tournament_type_has_at_least_one_candidate_crop():
    for name, images in rc.TOURNAMENT_TYPE_IMAGES.items():
        candidates = (images,) if isinstance(images, str) else images
        assert candidates, f"{name} has no reference crop names"
        assert all(isinstance(n, str) and n for n in candidates), f"{name} has a bad crop name"


def test_task_builder_tournament_types_match_the_runner_constants():
    """The Task Builder's Tournament type picker (TASK_DATA.tournament.maps in
    ui/app.js) is what writes a task's `map`, and the runner looks that string
    up in TOURNAMENT_TYPE_IMAGES to know which card to click. A type offered in
    the UI but missing from the constants would fail navigation at runtime, so
    the two lists have to stay in step -- this catches them drifting."""
    src = APP_JS.read_text(encoding="utf-8")
    block = re.search(r"tournament:\s*\{.*?maps:\s*\[(.*?)\]", src, re.S)
    assert block, "couldn't find TASK_DATA.tournament.maps in ui/app.js"
    ui_types = [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", block.group(1))]
    assert ui_types == rc.TOURNAMENT_TYPE_ORDER
