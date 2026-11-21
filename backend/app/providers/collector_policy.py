"""Fingerprint reviewed legal text and links, excluding Blazor transport comments."""
import hashlib
from html.parser import HTMLParser


class LegalText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.roots, self.hidden = 0, 0, 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if "main-card-body" in attrs.get("class", "").split():
                self.roots += 1
                if not self.depth:
                    self.depth = 1
                    return
            if self.depth:
                self.depth += 1
        if self.depth and tag in {"script", "style"}:
            self.hidden += 1
        if self.depth and not self.hidden and tag == "a":
            self.parts.append("LINK:" + attrs.get("href", ""))

    def handle_endtag(self, tag):
        if self.depth and tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, value):
        if self.depth and not self.hidden:
            self.parts.extend(value.split())


def policy_digest(body, mode="raw"):
    if mode == "html-main-card-visible-links-v1":
        parser = LegalText()
        parser.feed(body.decode("utf-8"))
        text = " ".join(parser.parts)
        if parser.roots != 1 or parser.depth != 0 or len(text) < 500:
            raise ValueError("LEGAL_DOCUMENT_STRUCTURE_CHANGED")
        body = text.encode("utf-8")
    elif mode != "raw":
        raise ValueError("UNKNOWN_POLICY_FINGERPRINT_FORMAT")
    return hashlib.sha256(body).hexdigest()
