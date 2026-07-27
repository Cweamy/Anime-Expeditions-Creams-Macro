"""Fetch and convert the wiki's Module:StageData/data Lua table."""
import json
import os
import re
import urllib.parse
import urllib.request

WIKI_API = "https://animeexpeditions.miraheze.org/w/api.php"
DATA_PAGE = "Module:StageData/data"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "stage_data.json")
_HEADERS = {"User-Agent": "CreamsMacro-DataBuilder/1.0"}


def _fetch_wikitext() -> str:
    url = f"{WIKI_API}?action=parse&page={urllib.parse.quote(DATA_PAGE)}&format=json&prop=wikitext"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    return data["parse"]["wikitext"]["*"]


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
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            pos += 1
            continue
        pos = match.end()
        if match.lastgroup != "comment":
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
    return tokens


class _Parser:
    def __init__(self, tokens: list):
        self.tokens, self.i = tokens, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def next(self):
        token = self.peek()
        self.i += 1
        return token

    def parse_value(self):
        kind, value = self.peek()
        if (kind, value) == ("punct", "{"):
            return self.parse_table()
        if kind == "string":
            self.next()
            return json.loads(value)
        if kind == "number":
            self.next()
            return float(value) if "." in value else int(value)
        if kind == "ident":
            self.next()
            return {"true": True, "false": False, "nil": None}.get(value, value)
        raise ValueError(f"Unexpected token {kind!r} {value!r} near position {self.i}")

    def parse_table(self):
        self.next()
        array_items, dict_items, is_array = [], {}, True
        while self.peek() != ("punct", "}"):
            kind, value = self.peek()
            if (kind, value) == ("punct", "["):
                self.next()
                key_kind, key_value = self.next()
                key = json.loads(key_value) if key_kind == "string" else key_value
                self.next()
                self.next()
                dict_items[key] = self.parse_value()
                is_array = False
            elif kind == "ident" and self.i + 1 < len(self.tokens) and self.tokens[self.i + 1] == ("punct", "="):
                self.next()
                self.next()
                dict_items[value] = self.parse_value()
                is_array = False
            else:
                array_items.append(self.parse_value())
            if self.peek() == ("punct", ","):
                self.next()
        self.next()
        if is_array:
            return array_items
        if array_items:
            dict_items["_array"] = array_items
        return dict_items


def lua_table_to_python(text: str):
    text = text.strip()
    if text.startswith("return"):
        text = text[len("return"):]
    return _Parser(_tokenize(text)).parse_value()


def fetch_stage_data() -> dict:
    data = lua_table_to_python(_fetch_wikitext())
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    return data


def main():
    data = fetch_stage_data()
    maps = data.get("Maps", {})
    print(f"Saved stage data for {len(maps)} map(s) to {OUT_PATH}")


if __name__ == "__main__":
    main()
