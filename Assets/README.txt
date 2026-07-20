ui/
  Button/screen reference images for core.vision.find_image -- see
  Assets/ui/README.txt for naming rules and what each one's used for.

item_icons/
  Gitignored, regenerated automatically -- reward-item reference art for
  core.rewards.identify_item_name (icon color matching, no OCR). Filled in
  by tools/fetch_item_icons.py, core.rewards._ensure_wiki_icons_for (a
  per-stage fetch of just what's missing), and core.rewards.
  _ensure_icon_reference (saved from a live gameplay capture the first
  time an item's identified with no reference yet). Never committed --
  regenerate locally rather than expecting it to already be there after a
  fresh clone.

maps/ + map/
  Map-name reference crops (core.stage_select) and the map picker's own
  background art.

stage_data.json
  The game's full stage reward/boss/mission table, scraped from the
  wiki's own data (tools/fetch_stage_data.py) -- see core/stage_data.py
  for the lookup helpers built on top of it (expected_rewards,
  expected_item_names, expected_item_amounts).

default_walk_paths.json
  Per-map default Pre Start walk recordings -- see core/paths.py.
