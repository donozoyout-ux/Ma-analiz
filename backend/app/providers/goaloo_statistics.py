"""Parse only the observed match technical-statistics HTML contract."""
from html.parser import HTMLParser
from decimal import Decimal, InvalidOperation

from app.providers.base import ProviderInvalidResponse


class StatisticsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.period = None
        self.seen = False
        self.row = None
        self.field = None
        self.span_depth = 0
        self.output = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "ul" and attrs.get("id") in {"ftstat", "htstat", "otstat"}:
            self.period = attrs["id"]
            self.seen = True
        if self.period and tag == "li":
            self.row = {"values": [], "label": ""}
        if self.row is not None and tag == "span":
            self.span_depth += 1
            classes = attrs.get("class", "").split()
            if "stat-c" in classes:
                self.field = "value"
                self.row["values"].append("")
            elif "stat-title" in classes:
                self.field = "label"

    def handle_data(self, data):
        if self.row is not None:
            if self.field == "value":
                self.row["values"][-1] += data
            elif self.field == "label":
                self.row["label"] += data

    def handle_endtag(self, tag):
        if tag == "span" and self.row is not None:
            self.span_depth -= 1
            self.field = None
        if tag == "li" and self.row is not None:
            label = self.row["label"].strip()
            values = [value.strip() for value in self.row["values"]]
            if not label or len(values) != 2:
                raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo statistic row schema changed")
            def number(raw):
                if raw in {"", "-", "--"}:
                    return None
                try:
                    parsed = Decimal(raw.rstrip("%"))
                    if not parsed.is_finite() or parsed < 0:
                        raise InvalidOperation
                    return float(parsed)
                except InvalidOperation:
                    raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo statistic is not numeric") from None
            self.output.append({"id": f"{self.period}:{label}", "name": label, "period": self.period,
                                "home_value": number(values[0]), "away_value": number(values[1]),
                                "raw_home": values[0], "raw_away": values[1],
                                "unit": "percent" if any("%" in value for value in values) else "source_unspecified"})
            self.row = None
        if tag == "ul" and self.period:
            self.period = None


def parse_statistics(html):
    parser = StatisticsParser()
    parser.feed(html)
    if not parser.seen:
        raise ProviderInvalidResponse("PROVIDER_INVALID_RESPONSE: Goaloo technical statistics section missing")
    return parser.output
