import main


def test_macro_import_backend_overwrites_and_reads_back_template(monkeypatch, tmp_path):
    from core import templates

    monkeypatch.setattr(templates, "TEMPLATES_DIR", str(tmp_path / "Templates"))
    api = main.Api()
    original = {"prestart": [], "battle": [{"type": "wait_ms", "params": {"ms": 100}}]}
    imported = {"prestart": [], "battle": [{"type": "wait_ms", "params": {"ms": 900}}]}

    assert api.save_template("Farm", original)["ok"]
    assert api.save_template("Farm", imported)["ok"]

    saved = api.load_template("Farm")
    assert saved["name"] == "Farm"
    assert saved["blocks"] == imported
