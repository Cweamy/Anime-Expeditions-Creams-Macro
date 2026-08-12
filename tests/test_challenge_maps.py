"""Guards for the Story-map list that Auto Challenge and Auto Bounty share.

The same list of Story maps is written out in four places: main.py (the
settings/API layer), core/runner_constants.py (what the runner recognizes
after teleporting in), ui/app.js's CHALLENGE_STORY_MAPS (what renders the
Story Map Setup rows), and ui/app.js's TASK_DATA.story.maps (the Task
Builder's own picker, which is the closest thing the repo has to "the maps
the game actually has").

Dropping a new map into only some of them fails quietly and in a way that is
hard to trace back: Story Map Setup shows no row for it, so setup_ready goes
green while that destination has no macro assigned, and Auto Challenge is
allowed to start -- then a Challenge that rotates onto it enters a battle
with no Pre Start blocks and no units. Exactly that happened when East Town
shipped in 0.19.0. These tests make the next map fail loudly instead.
"""
import re
from pathlib import Path

import main
from core import bounty
from core import runner_constants as rc

REPO = Path(__file__).resolve().parent.parent
APP_JS = REPO / "ui" / "app.js"


def _js_list(name):
    """Pull a top-level `const NAME = [...]` string list out of ui/app.js."""
    src = APP_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name}\s*=\s*\[(.*?)\];", src, re.S)
    assert match, f"couldn't find {name} in ui/app.js"
    return [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(1))]


def _js_task_data_story_maps():
    src = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"story:\s*\{.*?maps:\s*\[(.*?)\]", src, re.S)
    assert match, "couldn't find TASK_DATA.story.maps in ui/app.js"
    return [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(1))]


def test_backend_challenge_map_lists_match():
    """main.py serves the settings; runner_constants drives the post-teleport
    "which map did it land on" search. A map in one but not the other is either
    a map you can configure but the runner cannot recognize, or one it
    recognizes with nowhere to have configured a macro."""
    assert main.CHALLENGE_STORY_MAPS == rc.CHALLENGE_STORY_MAPS


def test_story_map_setup_ui_offers_every_backend_map():
    """CHALLENGE_STORY_MAPS in ui/app.js renders the Story Map Setup rows and
    is also validated server-side by set_challenge_map_macro, so a map only the
    backend knows about can never be given a Macro Operation through the UI."""
    assert _js_list("CHALLENGE_STORY_MAPS") == main.CHALLENGE_STORY_MAPS


def test_challenge_maps_cover_every_story_map_the_task_builder_offers():
    """TASK_DATA.story.maps is the map list the game actually has. Regular
    Challenge can rotate onto any of them, so the Challenge list has to keep up
    with it -- this is the check that East Town needed and did not have."""
    assert sorted(_js_task_data_story_maps()) == sorted(main.CHALLENGE_STORY_MAPS)


def test_bounty_shares_the_same_story_map_list():
    """core.bounty.STORY_MAPS is what read_destination_map matches an OCRed
    bounty destination against; BOUNTY_STORY_MAPS is what the Bounty Story Map
    Setup offers. A destination readable but not assignable means the same
    unit-less battle Challenge hits."""
    assert sorted(bounty.STORY_MAPS) == sorted(main.BOUNTY_STORY_MAPS)


def test_every_challenge_map_has_a_reference_crop():
    """_detect_current_challenge_map searches Assets/ui/<map> for each name.
    A name with no crop raises TemplateNotFound and drops the whole search to
    the OCR fallback, so the map is effectively unrecognizable."""
    for name in rc.CHALLENGE_STORY_MAPS:
        direct = REPO / "Assets" / "ui" / f"{name}.png"
        folder = REPO / "Assets" / "ui" / name
        variants = sorted(folder.glob("*.png")) if folder.is_dir() else []
        assert direct.is_file() or variants, f"{name} has no crop under Assets/ui"


def test_daily_challenge_ocr_aliases_cover_every_map():
    """The Daily Challenge map label is too small for the image search, so
    _detect_challenge_map_ocr reads it instead and needs one alias per map.
    A missing alias means that map is never named by the fallback."""
    assert sorted(rc.CHALLENGE_MAP_OCR_ALIASES) == sorted(rc.CHALLENGE_STORY_MAPS)
    assert all(alias.isalpha() and alias.islower()
               for alias in rc.CHALLENGE_MAP_OCR_ALIASES.values())


def test_ocr_aliases_are_distinct_and_not_dropped_as_boilerplate():
    """Two maps sharing an alias, or an alias that the stopword filter strips
    before scoring, both leave a map that can never win the match."""
    aliases = list(rc.CHALLENGE_MAP_OCR_ALIASES.values())
    assert len(set(aliases)) == len(aliases), "two maps share an OCR alias"
    assert not set(aliases) & set(rc.CHALLENGE_MAP_OCR_STOPWORDS)
