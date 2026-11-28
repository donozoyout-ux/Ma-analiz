"""Cornerflick open-data *read-only* pilot; intentionally not an ingest provider.

Only the published Bundesliga JSON download paths are supported. No scheduler,
registry registration, SQL/R2 writes, odds, result settlement or public API.
Network access is opt-in. Raw JSON is *not* treated as verified match results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time
from typing import Any

import httpx

from app.providers.cornerflick_schema_inspect import _unique_pairs, _reject_constant

from app.providers.base import (
    ProviderAccessRestricted,
    ProviderInvalidResponse,
    ProviderNotConfigured,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)

BASE_URL = "https://cornerflick.com"
DATA_PAGE = "https://cornerflick.com/data"
ATTRIBUTION = "Data by Cornerflick — https://cornerflick.com"
LICENCE = "CC BY 4.0 (Cornerflick compilation; upstream rights separately apply)"
MAX_BYTES = 2 * 1024 * 1024
MATCH_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class CornerflickDocument:
    """Unmapped download with transport provenance, not verified sports facts."""
    dataset: str
    competition: str
    source_url: str
    retrieved_at: datetime
    content_sha256: str
    attribution: str
    licence: str
    payload: dict[str, Any] | list[Any]


class CornerflickOpenData:
    """Strictly bounded manual JSON reads; disabled unless explicitly opted in.

    `enabled=True` is suitable only for an approved read-only probe. Upstream
    data redistribution rights and the JSON field schema are not yet validated.
    """

    name = "cornerflick"
    attribution = ATTRIBUTION
    licence = LICENCE

    def __init__(
        self,
        *,
        enabled: bool = False,
        client: httpx.Client | None = None,
        min_interval_seconds: float = 60.0,
        monotonic=time.monotonic,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("INVALID_INTERVAL")
        self.enabled = enabled
        self._client = client
        self._owns_client = False
        self._min_interval = min_interval_seconds
        self._monotonic = monotonic
        self._last_attempt: float | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
            self._owns_client = False

    def __enter__(self) -> "CornerflickOpenData":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _competition(value: str) -> str:
        if value != "bundesliga":
            raise ValueError("CORNERFLICK_PILOT_BUNDESLIGA_ONLY")
        return value

    def season_results(self, competition: str = "bundesliga") -> CornerflickDocument:
        """Download the site's documented season.json; do not guess its fields."""
        comp = self._competition(competition)
        return self._read(
            f"/football/leagues/{comp}/results/season.json",
            dataset="season_results", competition=comp,
        )

    def match_record(self, match_slug: str, competition: str = "bundesliga") -> CornerflickDocument:
        """Download a match page's data.json by a strict literal slug."""
        comp = self._competition(competition)
        if (not isinstance(match_slug, str) or len(match_slug) > 150
                or not MATCH_SLUG.fullmatch(match_slug)):
            raise ValueError("INVALID_CORNERFLICK_MATCH_SLUG")
        return self._read(
            f"/football/{comp}/matches/{match_slug}/data.json",
            dataset="match_record", competition=comp,
        )

    def _read(self, path: str, *, dataset: str, competition: str) -> CornerflickDocument:
        if not self.enabled:
            raise ProviderNotConfigured("CORNERFLICK_DISABLED: no network, SQL or R2 writes")
        with self._lock:
            now = self._monotonic()
            if self._last_attempt is not None:
                remaining = self._min_interval - (now - self._last_attempt)
                if remaining > 0:
                    raise ProviderRateLimited("CORNERFLICK_LOCAL_COOLDOWN", retry_after=remaining)
            self._last_attempt = now

        client = self._client
        if client is None:
            client = httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False)
            self._client, self._owns_client = client, True

        url = BASE_URL + path
        try:
            with client.stream("GET", url, follow_redirects=False, headers={
                "Accept": "application/json",
                "User-Agent": "MA-ANALIZ/0.1 (read-only research; no automated ingestion)",
            }) as response:
                status = response.status_code
                if status == 429:
                    raise ProviderRateLimited("CORNERFLICK_HTTP_429")
                if status in (401, 403) or 300 <= status < 400:
                    raise ProviderAccessRestricted("CORNERFLICK_ACCESS_OR_REDIRECT_BLOCKED")
                if status == 404:
                    raise ProviderNotFound("CORNERFLICK_DOCUMENT_NOT_FOUND")
                if status != 200:
                    raise ProviderUnavailable("CORNERFLICK_HTTP_UNAVAILABLE")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not (content_type == "application/json" or content_type.endswith("+json")):
                    raise ProviderInvalidResponse("CORNERFLICK_NON_JSON_RESPONSE")
                length_header = response.headers.get("content-length")
                if length_header is not None:
                    try:
                        length = int(length_header)
                    except ValueError as exc:
                        raise ProviderInvalidResponse("CORNERFLICK_BAD_CONTENT_LENGTH") from exc
                    if length < 0 or length > MAX_BYTES:
                        raise ProviderInvalidResponse("CORNERFLICK_DOCUMENT_TOO_LARGE")
                buffer = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(buffer) + len(chunk) > MAX_BYTES:
                        raise ProviderInvalidResponse("CORNERFLICK_DOCUMENT_TOO_LARGE")
                    buffer.extend(chunk)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("CORNERFLICK_NETWORK_UNAVAILABLE") from exc

        try:
            payload = json.loads(buffer, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise ProviderInvalidResponse("CORNERFLICK_INVALID_JSON") from exc
        if type(payload) not in (dict, list):
            raise ProviderInvalidResponse("CORNERFLICK_INVALID_ROOT")
        return CornerflickDocument(
            dataset=dataset,
            competition=competition,
            source_url=url,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=hashlib.sha256(buffer).hexdigest(),
            attribution=ATTRIBUTION,
            licence=LICENCE,
            payload=payload,
        )
