"""One-off/rerunnable script: downloads reference icon art for every known
item from the game's Miraheze wiki (via its public MediaWiki API -- the
page itself sits behind a bot-check that blocks plain HTTP fetches, but the
API is meant for exactly this kind of programmatic access) into
Assets/item_icons/, so core.rewards.identify_item_name has something to
compare captured reward icons against.

Re-run this whenever the wiki's Items page gains new entries -- it's safe
to run repeatedly, it just re-downloads and overwrites.

fetch_icons_for(names) below is also imported directly by core.rewards for
an on-demand, single-stage-sized fetch (just the handful of items a stage
can actually drop, not the whole wiki) when a stage's expected reward has
no local icon yet -- see core.rewards._ensure_wiki_icons_for.
"""
import json
import os
import re
import urllib.parse
import urllib.request

WIKI_API = "https://animeexpeditions.miraheze.org/w/api.php"
ITEMS_PAGE = "Items"
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "item_icons")
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# The Items page groups entries under these infobox-style templates --
# ItemBox for materials/currencies/EXP, EquipBox for equipment,
# AccessoryBox for cosmetics -- each taking the item's display name as its
# first positional argument.
_ITEM_TEMPLATES = ("ItemBox", "EquipBox", "AccessoryBox")

# Reference icon filenames on the wiki commonly carry one of these suffixes
# (e.g. "Bunny Candy Icon.png", "Calamity's Eye Equipment.png") -- stripped
# so the filename reduces to the bare item name for matching.
_FILENAME_SUFFIX = re.compile(r"[_ ](Icon|Equipment|Accessory|Currency)\.\w+$", re.IGNORECASE)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _fetch_item_names() -> list:
    url = f"{WIKI_API}?action=parse&page={urllib.parse.quote(ITEMS_PAGE)}&format=json&prop=wikitext"
    wikitext = _fetch_json(url)["parse"]["wikitext"]["*"]
    pattern = r"\{\{(?:" + "|".join(_ITEM_TEMPLATES) + r")\|([^}|]+)"
    return [m.strip() for m in re.findall(pattern, wikitext)]


def _fetch_all_images() -> list:
    images = []
    cont = None
    base = f"{WIKI_API}?action=query&list=allimages&format=json&ailimit=500"
    while True:
        url = base + (f"&aicontinue={urllib.parse.quote(cont)}" if cont else "")
        data = _fetch_json(url)
        images.extend(data["query"]["allimages"])
        cont = data.get("continue", {}).get("aicontinue")
        if not cont:
            break
    return images


def _normalize_filename(name: str) -> str:
    base = _FILENAME_SUFFIX.sub("", name)
    base = re.sub(r"\.\w+$", "", base)
    return base.replace("_", " ").strip().lower()


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 \-']", "", name).strip() + ".png"


def fetch_icons_for(names: list, quiet: bool = True) -> dict:
    """Downloads just the given items' icons (not the whole wiki) into
    ICON_DIR. The wiki API has no per-file lookup that's reliable across
    every item's naming quirks (Icon/Equipment/Accessory suffixes, spaces
    vs underscores, ...), so this still has to page through the full
    allimages list once -- but that's one bounded network round-trip
    regardless of how many names are asked for, and it's the same proven
    matching _fetch_all_images/_normalize_filename already do for the full
    scrape below, just applied to a subset. Returns {name: True/False} --
    False for a name with no matching image found on the wiki (a non-icon
    cosmetic, or a name that doesn't match the wiki's own spelling).
    """
    wanted = {n.lower(): n for n in names if n}
    if not wanted:
        return {}

    images_by_name = {}
    for img in _fetch_all_images():
        key = _normalize_filename(img["name"])
        if key in wanted:
            images_by_name.setdefault(key, img)

    os.makedirs(ICON_DIR, exist_ok=True)
    result = {}
    for key, name in wanted.items():
        img = images_by_name.get(key)
        if img is None:
            result[name] = False
            if not quiet:
                print(f"no matching image found for {name!r}")
            continue
        req = urllib.request.Request(img["url"], headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        with open(os.path.join(ICON_DIR, _safe_filename(name)), "wb") as f:
            f.write(data)
        result[name] = True
        if not quiet:
            print(f"downloaded icon for {name!r}")
    return result


def main():
    item_names = _fetch_item_names()
    print(f"{len(item_names)} item names on the wiki's Items page")
    result = fetch_icons_for(item_names, quiet=True)
    downloaded = [n for n, ok in result.items() if ok]
    missing = [n for n, ok in result.items() if not ok]
    print(f"downloaded {len(downloaded)} icons to {ICON_DIR}")
    if missing:
        print(f"no matching image found for {len(missing)} items (likely non-icon cosmetics): {missing}")


if __name__ == "__main__":
    main()
