"""Offline-only probe tests: no Cornerflick, PostgreSQL or R2 connection."""
import hashlib
import json

import httpx
import pytest

from app.providers.base import (
    ProviderAccessRestricted, ProviderInvalidResponse, ProviderNotConfigured,
    ProviderRateLimited,
)
from app.providers.cornerflick_open_data import (
    ATTRIBUTION, BASE_URL, CornerflickOpenData, MAX_BYTES,
)


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_disabled_by_default_never_reaches_network():
    called = []
    client = client_with(lambda req: called.append(req) or httpx.Response(200, json={}))
    with CornerflickOpenData(client=client) as source:
        with pytest.raises(ProviderNotConfigured, match="CORNERFLICK_DISABLED"):
            source.season_results()
    assert called == []


def test_bundesliga_season_is_raw_unmapped_json_with_provenance():
    sample = [{"fixture": "synthetic-no-schema-claim", "score": None}]
    encoded = json.dumps(sample).encode()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=encoded)

    with CornerflickOpenData(enabled=True, client=client_with(handler)) as source:
        doc = source.season_results()
    assert len(requests) == 1
    assert str(requests[0].url) == BASE_URL + "/football/leagues/bundesliga/results/season.json"
    assert requests[0].method == "GET"
    assert doc.payload == sample
    assert doc.dataset == "season_results" and doc.competition == "bundesliga"
    assert doc.content_sha256 == hashlib.sha256(encoded).hexdigest()
    assert doc.attribution == ATTRIBUTION and doc.retrieved_at.tzinfo is not None


def test_match_slug_is_allowlisted_and_never_accepted_as_a_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"meta": {"last_updated_at": None}})

    with CornerflickOpenData(enabled=True, client=client_with(handler)) as source:
        for slug in ("../etc/passwd", "http://evil.test", "%2f", "match?q=1", "a" * 151, ""):
            with pytest.raises(ValueError, match="INVALID_CORNERFLICK_MATCH_SLUG"):
                source.match_record(slug)
        with pytest.raises(ValueError, match="PILOT_BUNDESLIGA_ONLY"):
            source.season_results("other-league")
        record = source.match_record("bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20")
    assert seen == [BASE_URL + "/football/bundesliga/matches/bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20/data.json"]
    assert record.dataset == "match_record"


@pytest.mark.parametrize("status,exception", [
    (302, ProviderAccessRestricted),
    (403, ProviderAccessRestricted),
    (429, ProviderRateLimited),
])
def test_denied_redirect_and_rate_limit_do_not_retry(status, exception):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://example.com/private"})
    with CornerflickOpenData(enabled=True, client=client_with(handler)) as source:
        with pytest.raises(exception):
            source.season_results()
    assert len(requests) == 1


def test_wrong_content_type_and_invalid_root_fail_closed():
    for response in (
        httpx.Response(200, headers={"content-type": "text/html"}, text="<html>blocked</html>"),
        httpx.Response(200, json="not-an-object-or-array"),
        httpx.Response(200, headers={"content-type": "application/json"}, content=b"{broken"),
    ):
        with CornerflickOpenData(enabled=True, client=client_with(lambda _request: response)) as source:
            with pytest.raises(ProviderInvalidResponse):
                source.season_results()


def test_bounded_response_body_even_without_content_length():
    data = b"[" + b" " * MAX_BYTES + b"]"
    with CornerflickOpenData(
        enabled=True,
        client=client_with(lambda _request: httpx.Response(
            200, headers={"content-type": "application/json"}, content=data)),
    ) as source:
        with pytest.raises(ProviderInvalidResponse, match="TOO_LARGE"):
            source.season_results()


def test_second_read_is_throttled_instead_of_silent_auto_retry():
    count = []
    with CornerflickOpenData(
        enabled=True,
        monotonic=lambda: 10.0,
        client=client_with(lambda request: count.append(request) or httpx.Response(200, json={})),
    ) as source:
        source.season_results()
        with pytest.raises(ProviderRateLimited, match="LOCAL_COOLDOWN"):
            source.season_results()
    assert len(count) == 1


@pytest.mark.parametrize("raw", [
    b'{"score":1,"score":2}',
    b'{"score":NaN}',
    b'{"score":Infinity}',
    b'{"score":-Infinity}',
    b'{"details":{"status":1,"status":2}}',
])
def test_ambiguous_numeric_or_duplicate_json_rejected(raw):
    """Never accept ambiguous source data or non-finite values as real match facts."""
    with CornerflickOpenData(
        enabled=True,
        client=client_with(lambda _request: httpx.Response(
            200, headers={"content-type": "application/json"}, content=raw)),
    ) as source:
        with pytest.raises(ProviderInvalidResponse, match="CORNERFLICK_INVALID_JSON"):
            source.season_results()
