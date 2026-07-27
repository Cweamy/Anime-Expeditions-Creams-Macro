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
    assert code.startswith(share.HEADER_PREFIX_V2)

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
    assert code.startswith(share.HEADER_PREFIX_V2)

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
    assert exp["code"].startswith(share.HEADER_PREFIX_V2)

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
    """Every template's name + blocks must survive an export -> import roundtrip
    byte-for-byte. Runs against an isolated, seeded TEMPLATES_DIR (dict-phase,
    battle-only, and legacy flat-list forms) rather than the developer's real
    folder, so it is deterministic and does not choke on stray non-template
    JSON (pack/export artifacts) that a real Templates/ folder can contain.
    """
    import json
    import os

    import main
    from core import templates as tpl

    # 1. Seed an isolated source dir with representative templates
    source_dir = str(tmp_path / "source_templates")
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", source_dir)
    for name, blocks in {
        "Story Alpha": {"team": "Alpha", "prestart": [{"type": "walk_path"}], "battle": [{"type": "upgrade_unit"}]},
        "Battle Only": {"prestart": [], "battle": [{"type": "place_unit"}, {"type": "sell_unit"}]},
        "Legacy List": [{"type": "walk_path"}, {"type": "place_unit"}],
    }.items():
        tpl.save_template(name, blocks)

    orig_files = [f for f in os.listdir(source_dir) if f.endswith(".json")]
    orig_data = {}
    for fname in orig_files:
        with open(os.path.join(source_dir, fname), "r", encoding="utf-8") as f:
            orig_data[fname] = json.load(f)

    # 2. Export all via Api
    api_orig = main.Api()
    exp_res = api_orig.export_template_code(names=None)
    assert exp_res["ok"] is True
    assert exp_res["count"] == len(orig_files)
    code = exp_res["code"]

    # 3. Switch TEMPLATES_DIR to isolated target_dir
    target_dir = str(tmp_path / "target_templates")
    monkeypatch.setattr(tpl, "TEMPLATES_DIR", target_dir)
    api_tmp = main.Api()

    # 4. Import full pack into clean target_dir
    imp_res = api_tmp.import_template_code(code)
    assert imp_res["ok"] is True
    assert imp_res["count"] == len(orig_files)

    # 5. Verify data equivalence for every template
    for fname, expected_json in orig_data.items():
        imported_path = os.path.join(target_dir, fname)
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



def test_decode_rejects_decompression_bomb():
    """A tiny CREAM code that expands past the size cap is rejected, not OOM'd --
    checked on both the v2 (dict) and legacy v1 containers."""
    import base64
    import zlib

    from core import share

    payload = b"A" * (share.MAX_DECOMPRESSED_SIZE + 1024)

    # v2: raw deflate + preset dictionary
    co = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, share._ZDICT)
    v2 = share.HEADER_PREFIX_V2 + base64.urlsafe_b64encode(co.compress(payload) + co.flush()).decode("ascii").rstrip("=")
    res = share.decode_template_code(v2)
    assert res["ok"] is False and "limit" in res["reason"].lower()

    # v1: standard zlib
    v1 = share.HEADER_PREFIX_V1 + base64.urlsafe_b64encode(zlib.compress(payload, 9)).decode("ascii").rstrip("=")
    res = share.decode_template_code(v1)
    assert res["ok"] is False and "limit" in res["reason"].lower()


def test_legacy_v1_code_still_decodes():
    """Codes shared before the v2 dictionary format must still import."""
    import base64
    import json
    import zlib

    from core import share

    payload = {"kind": "anime-expeditions-template", "name": "Old Code", "blocks": [{"type": "walk_path"}]}
    jb = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    v1 = share.HEADER_PREFIX_V1 + base64.urlsafe_b64encode(zlib.compress(jb, 9)).decode("ascii").rstrip("=")

    res = share.decode_template_code(v1)
    assert res["ok"] is True
    assert res["type"] == "single"
    assert "Old Code" in res["templates"]


def test_v2_is_shorter_than_v1_for_a_single_template():
    """The dictionary format must actually be smaller than the old one."""
    import base64
    import json
    import zlib

    from core import share

    payload = {
        "kind": "anime-expeditions-template",
        "name": "Act3 Villian",
        "blocks": {
            "prestart": [{"type": "walk_path", "params": {}, "once": True}],
            "battle": [
                {"type": "place_unit", "params": {"name": "Goku", "x": 120, "y": 340}, "once": False},
                {"type": "upgrade_unit", "params": {"index": "1"}, "once": False},
                {"type": "sell_unit", "params": {"index": "1"}, "once": False},
            ],
        },
    }
    jb = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    v1_len = len(share.HEADER_PREFIX_V1 + base64.urlsafe_b64encode(zlib.compress(jb, 9)).decode("ascii").rstrip("="))
    v2_len = len(share.encode_template_code(payload))
    assert v2_len < v1_len, f"v2 ({v2_len}) should be shorter than v1 ({v1_len})"
