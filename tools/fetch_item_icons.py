"""Fetch reward-item reference icons from the Anime Expeditions wiki."""
import json
import os
import re
import urllib.parse
import urllib.request

WIKI_API = "https://animeexpeditions.miraheze.org/w/api.php"
ITEMS_PAGE = "Items"
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "item_icons")
_HEADERS = {"User-Agent": "CreamsMacro-DataBuilder/1.0"}
_ITEM_TEMPLATES = ("ItemBox", "EquipBox", "AccessoryBox")
_FILENAME_SUFFIX = re.compile(r"[_ ](Icon|Equipment|Accessory|Currency)\.\w+$", re.IGNORECASE)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _fetch_item_names() -> list:
    url = f"{WIKI_API}?action=parse&page={urllib.parse.quote(ITEMS_PAGE)}&format=json&prop=wikitext"
    wikitext = _fetch_json(url)["parse"]["wikitext"]["*"]
    pattern = r"\{\{(?:" + "|".join(_ITEM_TEMPLATES) + r")\|([^}|]+)"
    return [match.strip() for match in re.findall(pattern, wikitext)]


def _fetch_all_images() -> list:
    images, continuation = [], None
    base = f"{WIKI_API}?action=query&list=allimages&format=json&ailimit=500"
    while True:
        url = base + (f"&aicontinue={urllib.parse.quote(continuation)}" if continuation else "")
        data = _fetch_json(url)
        images.extend(data["query"]["allimages"])
        continuation = data.get("continue", {}).get("aicontinue")
        if not continuation:
            return images


def _normalize_filename(name: str) -> str:
    base = _FILENAME_SUFFIX.sub("", name)
    base = re.sub(r"\.\w+$", "", base)
    return base.replace("_", " ").strip().lower()


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 \-']", "", name).strip() + ".png"


def fetch_icons_for(names: list, quiet: bool = True) -> dict:
    wanted = {name.lower(): name for name in names if name}
    if not wanted:
        return {}
    images_by_name = {}
    for image in _fetch_all_images():
        key = _normalize_filename(image["name"])
        if key in wanted:
            images_by_name.setdefault(key, image)
    os.makedirs(ICON_DIR, exist_ok=True)
    result = {}
    for key, name in wanted.items():
        image = images_by_name.get(key)
        if image is None:
            result[name] = False
            if not quiet:
                print(f"no matching image found for {name!r}")
            continue
        req = urllib.request.Request(image["url"], headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        with open(os.path.join(ICON_DIR, _safe_filename(name)), "wb") as file:
            file.write(data)
        result[name] = True
        if not quiet:
            print(f"downloaded icon for {name!r}")
    return result


def main():
    names = _fetch_item_names()
    result = fetch_icons_for(names)
    downloaded = sum(result.values())
    print(f"Downloaded {downloaded}/{len(names)} item icons to {ICON_DIR}")


if __name__ == "__main__":
    main()
