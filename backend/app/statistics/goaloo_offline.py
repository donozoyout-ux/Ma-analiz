"""Offline Goaloo HTML parser adapted from Bet-app213; no network access.

Source match and league identities are mandatory. Missing values remain None.
"""
from html.parser import HTMLParser
import re

FIELDS = {
    "Corner Kicks": "corners", "Corner Kicks(HT)": "corners_ht",
    "Yellow Cards": "yellow_cards", "Red Cards": "red_cards",
    "Shots": "shots", "Shots On Goal": "shots_on_target", "Fouls": "fouls",
    "Offsides": "offsides", "Possession": "possession",
}


class Node:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def walk(self, tag):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.walk(tag)

    def text(self):
        return " ".join("".join(child.text() if isinstance(child, Node) else child
                                for child in self.children).split())


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"img", "br", "meta", "link", "input", "hr", "source", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def by_id(self, name):
        return next((node for node in self.root.walk(None) if node.attrs.get("id") == name), None)


def value(text, possession=False):
    text = text.strip().removesuffix("%").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    if possession:
        return number if 0 <= number <= 100 else None
    return int(number) if number.is_integer() and number <= 2147483647 else None


def parse_statistics(source: str, match_id: int, league_id: int) -> dict:
    for key, expected in (("sId", match_id), ("sclassId", league_id)):
        match = re.search(r"\b" + key + r"\s*:\s*parseInt\(['\"](-?\d+)['\"]\)", source)
        if not match or int(match.group(1)) != expected:
            raise ValueError("SOURCE_IDENTITY_MISMATCH")
    page = Page(source)
    stats = {f"{side}_{name}": None for name in FIELDS.values() for side in ("home", "away")}
    for block_id in ("ftstat", "hf1stat"):
        block = page.by_id(block_id)
        if not block:
            continue
        for row in block.walk("li"):
            title = next((node.text() for node in row.walk("span")
                          if "stat-title" in node.attrs.get("class", "").split()), None)
            cells = [node.text() for node in row.walk("span")
                     if "stat-c" in node.attrs.get("class", "").split()]
            if len(cells) != 2 or title not in FIELDS:
                continue
            field = "corners_ht" if block_id == "hf1stat" and title == "Corner Kicks" else FIELDS[title]
            if block_id == "hf1stat" and field != "corners_ht":
                continue
            for side, text in zip(("home", "away"), cells):
                parsed = value(text, field == "possession")
                key = f"{side}_{field}"
                if stats[key] is not None and parsed is not None and stats[key] != parsed:
                    raise ValueError("CONFLICTING_STATISTIC")
                if parsed is not None:
                    stats[key] = parsed
    return stats
