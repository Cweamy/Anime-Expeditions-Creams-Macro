"""Bundled example routines, and the Macro Manager picker that uses them.

A blank editor is a hard place to start from when the block semantics are
the thing you are still learning, so the app ships a few working routines.
They live in a SUBFOLDER of Templates/ specifically so list_templates()'s
os.listdir never sees them: an example must not show up in the user's own
Load... list, and must not be saveable or deletable by accident.
"""
import json

import pytest

from core import templates as tpl


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the module at a scratch Templates/ with one example in it."""
    templates = tmp_path / "Templates"
    examples = templates / "examples"
    examples.mkdir(parents=True)
    (examples / "demo.json").write_text(json.dumps({
        "name": "Demo Routine",
        "description": "what it does",
        "blocks": {"prestart": [{"type": "place_unit"}], "battle": [{"type": "wait_wave"}]},
    }), encoding="utf-8")
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(templates))
    monkeypatch.setattr(tpl, "EXAMPLES_DIR", str(examples))
    return templates


def test_examples_are_listed_with_their_description_and_blocks(store):
    examples = tpl.list_examples()

    assert [e["name"] for e in examples] == ["Demo Routine"]
    assert examples[0]["description"] == "what it does"
    assert examples[0]["blocks"]["battle"][0]["type"] == "wait_wave"


def test_examples_never_appear_in_the_users_own_list(store):
    """The whole reason they live in a subfolder. If they leaked into
    list_templates they could be loaded over, renamed or deleted."""
    assert tpl.list_templates() == []


def test_using_an_example_copies_it_into_the_users_templates(store):
    saved = tpl.copy_example("Demo Routine")

    assert saved == "Demo Routine"
    assert tpl.list_templates() == ["Demo Routine"]
    assert tpl.load_template("Demo Routine")["blocks"]["battle"][0]["type"] == "wait_wave"


def test_using_it_twice_does_not_overwrite_the_first_copy(store):
    """save_template's free-slug rule has to apply here too -- someone who
    edited their copy and then clicked Use again must not lose the edits."""
    first = tpl.copy_example("Demo Routine")
    tpl.save_template(first, {"prestart": [{"type": "click"}]})   # user edits it

    second = tpl.copy_example("Demo Routine")

    assert second != first
    assert tpl.load_template(first)["blocks"]["prestart"][0]["type"] == "click"


def test_the_bundled_example_itself_is_never_modified(store):
    tpl.copy_example("Demo Routine")
    tpl.save_template("Demo Routine", {"prestart": [{"type": "click"}]})

    assert tpl.list_examples()[0]["blocks"]["prestart"][0]["type"] == "place_unit"


def test_an_unknown_example_reports_rather_than_creating_one(store):
    assert tpl.copy_example("Nope") == ""
    assert tpl.list_templates() == []


def test_a_corrupt_example_is_skipped_rather_than_breaking_the_picker(store):
    (store / "examples" / "broken.json").write_text("{not json", encoding="utf-8")

    assert [e["name"] for e in tpl.list_examples()] == ["Demo Routine"]


def test_no_examples_folder_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(tpl, "EXAMPLES_DIR", str(tmp_path / "nope"))

    assert tpl.list_examples() == []


# ---------------------------------------------------------------------------
# What actually ships
# ---------------------------------------------------------------------------

def test_the_shipped_examples_are_loadable_and_describe_themselves():
    """Guards the real files, not a fixture -- a bundled example with no
    description or no blocks is a blank row in the picker."""
    for example in tpl.list_examples():
        assert example["name"], "an example needs a name"
        assert example["description"], f"{example['name']} has no description"
        phases = example["blocks"]
        assert any(phases.get(p) for p in ("prestart", "battle", "loop_a", "loop_b")), \
            f"{example['name']} has no blocks in any phase"
