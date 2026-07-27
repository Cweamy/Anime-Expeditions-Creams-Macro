from tools import fetch_item_icons, fetch_stage_data


def test_stage_data_lua_parser_handles_nested_tables():
    parsed = fetch_stage_data.lua_table_to_python(
        'return { Maps = { ["School Grounds"] = { Story = { Normal = { ["Act 1"] = {'
        ' Rewards = { Normal = { Gold = 125, Every = true } } } } } } } }')
    stage = parsed["Maps"]["School Grounds"]["Story"]["Normal"]["Act 1"]
    assert stage["Rewards"]["Normal"] == {"Gold": 125, "Every": True}


def test_icon_filename_normalization_matches_wiki_suffixes():
    assert fetch_item_icons._normalize_filename("Bunny_Candy_Icon.png") == "bunny candy"
    assert fetch_item_icons._normalize_filename("Calamity's Eye Equipment.png") == "calamity's eye"
