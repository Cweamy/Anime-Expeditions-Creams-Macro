This whole folder ships LOOSE beside the exe (never bundled inside it --
see core/constants.py's ASSETS_DIR and build_pyinstaller.py) so everything
here stays user-editable: open a crop, replace it, add extra variants, all
without rebuilding anything. App updates only ever ADD files that are new
in a release; they never overwrite something already on disk (see
core/updater.py's Assets section).

ui/
  Button/screen reference images for core.vision.find_image -- one FOLDER
  per searched name, every .png inside tried as an interchangeable
  variant. See Assets/ui/README.txt for naming rules, how to add your own
  crops (Settings > General > Image Manager does it in-app), and what each
  name's used for.

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
  Map-name reference crops (core.stage_select -- same folder-per-name
  layout as ui/, see maps/README.txt) and the map picker's own background
  art (map/, plain images, not searched).

stage_data.json
  The game's full stage reward/boss/mission table, scraped from the
  wiki's own data (tools/fetch_stage_data.py) -- see core/stage_data.py
  for the lookup helpers built on top of it (expected_rewards,
  expected_item_names, expected_item_amounts).

default_walk_paths.json
  Per-map default Pre Start walk recordings -- see core/paths.py.
