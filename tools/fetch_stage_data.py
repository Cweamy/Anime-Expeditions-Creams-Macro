"""One-off/rerunnable script: downloads the game's full stage data (rewards,
bosses, star missions, drops, etc. for every map x act x difficulty) from the
wiki's Module:StageData/data Lua page via the MediaWiki API, converts the Lua
table literal into JSON, and saves it to Assets/stage_data.json.

This is what lets the macro log a stage's "possible reward" for comparison
against what OCR actually read off the Victory screen (see
core.stage_data.expected_rewards), instead of hand-transcribing hundreds of
numbers from the wiki.

Re-run this whenever the wiki's data changes (new maps/acts/rebalanced
rewards) -- safe to run repeatedly, it just re-downloads and overwrites.
"""
import json
import os
import re
import urllib.parse
import urllib.request

WIKI_API = "https://animeexpeditions.miraheze.org/w/api.php"
DATA_PAGE = "Module:StageData/data"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "stage_data.json")
_HEADERS = {"User-Agent": "Mozilla/5.0"}  # the page itself 403s plain fetches; the API doesn't, given a real UA


def _fetch_wikitext() -> str:
    url = f"{WIKI_API}?action=parse&page={urllib.parse.quote(DATA_PAGE)}&format=json&prop=wikitext"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return data["parse"]["wikitext"]["*"]


# ---------------------------------------------------------------------------
# Minimal Lua table-literal parser -- just enough of Lua's grammar to read
# this one data file (nested { }, "quoted"/["bracketed"] keys, bare-word
# keys, numbers, strings) -- not a general-purpose Lua interpreter.
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"""
    \s*(?:
        (?P<comment>--\[\[.*?\]\]|--[^\n]*) |
        (?P<string>"(?:[^"\\]|\\.)*") |
        (?P<number>-?\d+\.\d+|-?\d+) |
        (?P<ident>[A-Za-z_][A-Za-z0-9_]*) |
        (?P<punct>[{}\[\]=,])
    )""", re.VERBOSE | re.DOTALL)


def _tokenize(text: str) -> list:
    pos, tokens = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            pos += 1
            continue
        pos = m.end()
        if m.lastgroup == "comment":
            continue
        tokens.append((m.lastgroup, m.group(m.lastgroup)))
    return tokens


class _Parser:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse_value(self):
        kind, val = self.peek()
        if kind == "punct" and val == "{":
            return self.parse_table()
        if kind == "string":
            self.next()
            return json.loads(val)  # Lua and JSON double-quoted string escaping are close enough here
        if kind == "number":
            self.next()
            return float(val) if "." in val else int(val)
        if kind == "ident":
            self.next()
            if val == "true":
                return True
            if val == "false":
                return False
            if val == "nil":
                return None
            return val
        raise ValueError(f"Unexpected token {kind!r} {val!r} near position {self.i}")

    def parse_table(self):
        self.next()  # consume '{'
        array_items, dict_items, is_array = [], {}, True
        while True:
            kind, val = self.peek()
            if kind == "punct" and val == "}":
                self.next()
                break
            if kind == "punct" and val == "[":
                self.next()
                key_kind, key_val = self.next()
                key = json.loads(key_val) if key_kind == "string" else key_val
                self.next()  # ']'
                self.next()  # '='
                dict_items[key] = self.parse_value()
                is_array = False
            elif kind == "ident" and self.i + 1 < len(self.tokens) and self.tokens[self.i + 1] == ("punct", "="):
                self.next()
                self.next()  # '='
                dict_items[val] = self.parse_value()
                is_array = False
            else:
                array_items.append(self.parse_value())
            kind, val = self.peek()
            if kind == "punct" and val == ",":
                self.next()
        if is_array:
            return array_items
        if array_items:  # a table with both keyed and positional entries -- none expected here, but don't drop data
            dict_items["_array"] = array_items
        return dict_items


def lua_table_to_python(text: str):
    text = text.strip()
    if text.startswith("return"):
        text = text[len("return"):]
    return _Parser(_tokenize(text)).parse_value()


def main():
    wikitext = _fetch_wikitext()
    data = lua_table_to_python(wikitext)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    maps = data.get("Maps", {})
    print(f"Saved stage data for {len(maps)} map(s) to {OUT_PATH}: {', '.join(sorted(maps))}")


if __name__ == "__main__":
    main()
