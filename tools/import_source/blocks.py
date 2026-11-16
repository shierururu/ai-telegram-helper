"""Turn supported HTML blocks into inert text and explicit link candidates."""

import json
from html.parser import HTMLParser
from urllib.parse import urlsplit

SKIP = {"script", "style", "iframe", "svg", "noscript", "template"}
BOUNDARY = {"div", "p", "br", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4"}


class BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: set[str] = set()
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in BOUNDARY:
            self.parts.append(" ")
        if tag == "a":
            hrefs = [value for name, value in attrs if name == "href"]
            if len(hrefs) == 1 and hrefs[0]:
                self.links.add(hrefs[0])

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
        elif tag in BOUNDARY:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    @property
    def text(self) -> str:
        words = "".join(self.parts).split()
        return " ".join(word for word in words if "://" not in word and "@" not in word)


def parse_block(value: str | None) -> BlockParser:
    parser = BlockParser()
    parser.feed(value or "")
    parser.close()
    return parser


def matched_route(url: str) -> tuple[str, str] | None:
    if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc != "kurs.alfabank.ru" or "?" in url or "#" in url:
        return None
    segments = parsed.path.removesuffix("/").split("/")
    if len(segments) != 3 or segments[1] not in ("articles", "courses"):
        return None
    slug = segments[2]
    if not slug or not all(c.isascii() and (c.isalnum() or c in "-_") for c in slug):
        return None
    return segments[1].title(), slug


def params_link(block_type: str | None, raw: str | None) -> str | None:
    """Read only the observed text-link and material-card JSON structures."""
    if block_type not in ("0", "8") or not raw:
        return None
    try:
        params = json.loads(raw)
    except ValueError, RecursionError:
        return None
    if not isinstance(params, dict):
        return None
    if block_type == "8":
        params = params.get("item")
        if not isinstance(params, dict):
            return None
        url = params.get("utm")
    else:
        url = params.get("link")
    return url if isinstance(url, str) else None


def params_text(block_type: str | None, raw: str | None) -> list[str]:
    """Reviewed snapshot shapes only; not a claim about SEARCH_INDEX_FIELDS.

    Never recursively traverse strings: images, files, private and unknown keys stay out.
    """
    if block_type not in ("0", "2", "4") or not raw:
        return []
    try:
        params = json.loads(raw)
    except ValueError, RecursionError:
        return []
    if not isinstance(params, dict):
        return []
    keys = {"0": ("text",), "2": ("title", "description"), "4": ("title",)}[block_type]
    result = [params[key] for key in keys if isinstance(params.get(key), str)]
    if block_type == "4":
        items = params.get("items")
        if isinstance(items, list) and all(isinstance(item, str) for item in items):
            result.extend(items)
    return result
