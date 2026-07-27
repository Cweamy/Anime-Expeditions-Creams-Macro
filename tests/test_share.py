import pytest
from core import share


def test_encode_and_decode_single_template():
    single_payload = {
        "kind": "anime-expeditions-template",
        "version": 1,
        "name": "Test Castle",
        "blocks": [
            {"type": "place_unit", "params": {"name": "Goku", "x": 100, "y": 200}, "once": False}
        ],
    }

    code = share.encode_template_code(single_payload)
    assert code.startswith("CREAM:v1:")

    result = share.decode_template_code(code)
    assert result["ok"] is True
    assert result["type"] == "single"
    assert "Test Castle" in result["templates"]
    assert len(result["templates"]["Test Castle"]) == 1
    assert result["templates"]["Test Castle"][0]["params"]["name"] == "Goku"


def test_encode_and_decode_multi_template_pack():
    pack_payload = {
        "kind": "anime-expeditions-template-pack",
        "version": 1,
        "templates": {
            "Map Alpha": [{"type": "walk_path", "params": {}, "once": True}],
            "Map Beta": [{"type": "upgrade_unit", "params": {"index": "1"}, "once": False}],
        },
    }

    code = share.encode_template_code(pack_payload)
    assert code.startswith("CREAM:v1:")

    result = share.decode_template_code(code)
    assert result["ok"] is True
    assert result["type"] == "pack"
    assert "Map Alpha" in result["templates"]
    assert "Map Beta" in result["templates"]
    assert len(result["templates"]["Map Alpha"]) == 1
    assert len(result["templates"]["Map Beta"]) == 1


def test_decode_raw_json_single():
    raw_json = '{"name": "Raw Template", "blocks": [{"type": "test"}]}'
    result = share.decode_template_code(raw_json)
    assert result["ok"] is True
    assert result["type"] == "single"
    assert "Raw Template" in result["templates"]


def test_decode_raw_json_pack():
    raw_json = '{"kind": "anime-expeditions-template-pack", "templates": {"T1": [], "T2": []}}'
    result = share.decode_template_code(raw_json)
    assert result["ok"] is True
    assert result["type"] == "pack"
    assert len(result["templates"]) == 2


def test_decode_invalid_inputs():
    res_empty = share.decode_template_code("")
    assert res_empty["ok"] is False

    res_corrupted = share.decode_template_code("CREAM:v1:InvalidBase64Data!!!")
    assert res_corrupted["ok"] is False

    res_bad_json = share.decode_template_code("{not json}")
    assert res_bad_json["ok"] is False


def test_api_bridge_export_and_import(tmp_path, monkeypatch):
    import main
    from core import templates as tpl

    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tmp_path))

    api = main.Api()

    # Save a test template
    tpl.save_template("ShareTestTpl", [{"type": "place_unit", "params": {"name": "Luffy"}}])

    # Export via ApiBridge
    exp = api.export_template_code("ShareTestTpl")
    assert exp["ok"] is True
    assert exp["code"].startswith("CREAM:v1:")

    # Import via ApiBridge into new template name
    import_payload = share.decode_template_code(exp["code"])
    import_payload["templates"]["ShareTestTplImported"] = import_payload["templates"].pop("ShareTestTpl")
    new_code = share.encode_template_code({
        "kind": "anime-expeditions-template-pack",
        "templates": import_payload["templates"]
    })

    imp = api.import_template_code(new_code)
    assert imp["ok"] is True
    assert imp["count"] == 1
    assert "ShareTestTplImported" in imp["templates"]
    assert "ShareTestTplImported" in tpl.list_templates()


def test_dict_structured_template_blocks():
    dict_payload = {
        "kind": "anime-expeditions-template",
        "version": 1,
        "name": "Dict Template",
        "blocks": {
            "prestart": [{"type": "place_unit"}, {"type": "walk_path"}],
            "battle": [{"type": "upgrade_unit"}],
            "team": "AIO Team",
        },
    }

    code = share.encode_template_code(dict_payload)
    preview = share.preview_template_code(code)

    assert preview["ok"] is True
    assert preview["total_templates"] == 1
    assert preview["items"][0]["blocks_count"] == 3

    decoded = share.decode_template_code(code)
    assert decoded["ok"] is True
    assert isinstance(decoded["templates"]["Dict Template"], dict)
    assert len(decoded["templates"]["Dict Template"]["prestart"]) == 2
    assert len(decoded["templates"]["Dict Template"]["battle"]) == 1


def test_all_real_templates_lossless_roundtrip(tmp_path, monkeypatch):
    import json
    import os
    import main
    from core import templates as tpl

    # 1. Collect all real original template files
    orig_dir = tpl.TEMPLATES_DIR
    orig_files = [f for f in os.listdir(orig_dir) if f.endswith(".json")]
    assert len(orig_files) > 0, "Real Templates directory should contain templates"

    orig_data = {}
    for fname in orig_files:
        with open(os.path.join(orig_dir, fname), "r", encoding="utf-8") as f:
            orig_data[fname] = json.load(f)

    # 2. Export all via Api
    api_orig = main.Api()
    exp_res = api_orig.export_template_code(names=None)
    assert exp_res["ok"] is True
    assert exp_res["count"] == len(orig_files)
    code = exp_res["code"]

    # 3. Switch TEMPLATES_DIR to isolated tmp_path
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tmp_path))
    api_tmp = main.Api()

    # 4. Import full pack into clean tmp_path
    imp_res = api_tmp.import_template_code(code)
    assert imp_res["ok"] is True
    assert imp_res["count"] == len(orig_files)

    # 5. Verify byte-for-byte / data equivalence for every template
    for fname, expected_json in orig_data.items():
        imported_path = os.path.join(str(tmp_path), fname)
        assert os.path.exists(imported_path), f"Imported file {fname} should exist"
        with open(imported_path, "r", encoding="utf-8") as f:
            imported_json = json.load(f)

        assert imported_json == expected_json, f"Mismatch in {fname} after roundtrip!"


def test_export_custom_selected_templates_list(tmp_path, monkeypatch):
    import main
    from core import templates as tpl

    monkeypatch.setattr(tpl, "TEMPLATES_DIR", str(tmp_path))
    api = main.Api()

    tpl.save_template("T1", [{"type": "walk_path"}])
    tpl.save_template("T2", [{"type": "place_unit"}])
    tpl.save_template("T3", [{"type": "upgrade_unit"}])

    # Export specific subset [T1, T3]
    exp = api.export_template_code(names=["T1", "T3"])
    assert exp["ok"] is True
    assert exp["count"] == 2

    # Decode and check contents
    decoded = share.decode_template_code(exp["code"])
    assert decoded["ok"] is True
    assert "T1" in decoded["templates"]
    assert "T3" in decoded["templates"]
    assert "T2" not in decoded["templates"]

