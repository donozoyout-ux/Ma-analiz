"""Public GET-only collector transport. No application settings, SQL or object store."""
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import random
import re
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from app.providers.evidence_http import EvidenceFailure, USER_AGENT

HOSTS = {"api.openligadb.de", "www.openligadb.de", "raw.githubusercontent.com"}
MAX_BODY = 1_000_000


def now_utc():
    return datetime.now(timezone.utc)


def valid_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password
            or p.port or p.query or p.fragment):
        raise EvidenceFailure("UNAPPROVED_PUBLIC_URL")
    return p


class CollectorHTTP:
    def __init__(self, state, *, client=None, clock=now_utc, sleep=time.sleep, jitter=None):
        self.state = state
        self.client = client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        self.own = client is None
        self.clock, self.sleep = clock, sleep
        self.jitter = jitter or (lambda: random.uniform(0, 2))
        self.requests, self.policies, self.last = [], {}, {}
        self.cache = state.setdefault("http", {})
        self.blocks = state.setdefault("blocks", {})

    def close(self):
        if self.own:
            self.client.close()

    def _stop(self, host, code, until=None):
        self.blocks[host] = {"code": code, "until": until.isoformat() if until else None}
        raise EvidenceFailure(code)

    def _check_block(self, host):
        blocked = self.blocks.get(host)
        if blocked:
            if not blocked["until"] or datetime.fromisoformat(blocked["until"]) > self.clock():
                raise EvidenceFailure("ORIGIN_BLOCKED_REVIEW_OR_COOLDOWN")
            del self.blocks[host]

    def _delay(self, response):
        value = response.headers.get("Retry-After", "")
        if value.isdigit() and len(value) < 10:
            return int(value)
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.utcoffset() is not None:
                return max(0, (stamp - self.clock()).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
        return 0

    def _request(self, key, url, *, robots=False):
        p = valid_url(url)
        host = p.hostname
        self._check_block(host)
        old = self.cache.get(key)
        headers = {"User-Agent": USER_AGENT}
        if old and not robots:
            for source, dest in (("etag", "If-None-Match"), ("modified", "If-Modified-Since")):
                value = old.get(source, "")
                if value and re.fullmatch(r"[\x20-\x7e]{1,256}", value):
                    headers[dest] = value
        for attempt in range(2):
            if len(self.requests) >= 20:
                raise EvidenceFailure("REQUEST_BUDGET_EXCEEDED")
            delay = self.policies.get(host, (None, 1))[1]
            prior = self.last.get(host)
            if prior:
                self.sleep(max(0, delay - (self.clock() - prior).total_seconds()))
            if host == "api.openligadb.de" and not robots:
                prior = self.state.get("last_api_request")
                if prior:
                    elapsed = (self.clock() - datetime.fromisoformat(prior)).total_seconds()
                    if elapsed < -60:
                        raise EvidenceFailure("LOCAL_STATE_CLOCK_INVALID")
                    self.sleep(max(0, 60 - elapsed))
            self.sleep(self.jitter())
            self.last[host] = self.clock()
            if host == "api.openligadb.de" and not robots:
                self.state["last_api_request"] = self.clock().isoformat()
            audit = {"resource": key, "observed_at": self.clock().isoformat(), "attempt": attempt + 1}
            self.requests.append(audit)
            try:
                with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
                    status = response.status_code
                    audit["http_status"] = status
                    if status in {401, 403} or 300 <= status < 400 and status != 304:
                        self._stop(host, "ACCESS_STOP_OR_REDIRECT")
                    if status == 429:
                        self._stop(host, "RATE_LIMIT_COOLDOWN", self.clock() + timedelta(seconds=max(43200, self._delay(response))))
                    parts, size = [], 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > MAX_BODY:
                            raise EvidenceFailure("RESPONSE_TOO_LARGE")
                        parts.append(part)
                    body = b"".join(parts)
            except httpx.TimeoutException:
                audit["error_code"] = "SOURCE_TIMEOUT"
                if attempt == 0:
                    self.sleep(2 + self.jitter())
                    continue
                raise EvidenceFailure("SOURCE_TIMEOUT") from None
            except httpx.HTTPError:
                audit["error_code"] = "NETWORK_OR_TLS_FAILURE"
                self._stop(host, "NETWORK_OR_TLS_FAILURE")
            status = response.status_code
            audit.update(http_status=status, bytes=size, sha256=hashlib.sha256(body).hexdigest())
            if status in {500, 502, 503, 504}:
                wait = max(2 ** (attempt + 1), self._delay(response))
                if attempt == 0 and wait <= 60:
                    self.sleep(wait + self.jitter())
                    continue
                self._stop(host, "SERVER_BACKOFF", self.clock() + timedelta(seconds=max(3600, wait)))
            if robots:
                return status, body, None
            if status == 304:
                if not old:
                    raise EvidenceFailure("CACHE_MISSING_FOR_304")
                body = base64.b64decode(old["body"], validate=True)
                if hashlib.sha256(body).hexdigest() != old["sha256"]:
                    raise EvidenceFailure("CACHE_CHECKSUM_INVALID")
                return status, body, {"sha256": old["sha256"], "received_at": old["received_at"], "cached": True}
            if status != 200:
                raise EvidenceFailure("HTTP_NOT_AVAILABLE")
            stamp = audit["observed_at"]
            self.cache[key] = {"body": base64.b64encode(body).decode(), "sha256": audit["sha256"],
                               "received_at": stamp, "etag": response.headers.get("ETag", ""),
                               "modified": response.headers.get("Last-Modified", "")}
            return status, body, {"sha256": audit["sha256"], "received_at": stamp, "cached": False}

    def get(self, key, url):
        p = valid_url(url)
        self._check_block(p.hostname)
        if p.hostname not in self.policies:
            status, body, _ = self._request("robots:" + p.hostname, f"https://{p.hostname}/robots.txt", robots=True)
            policy = RobotFileParser()
            if status in {404, 410}:
                policy.parse([])
            elif status == 200 and not body.lstrip().startswith(b"<"):
                try:
                    policy.parse(body.decode("utf-8").splitlines())
                except UnicodeDecodeError:
                    self._stop(p.hostname, "ROBOTS_UNVERIFIED")
            else:
                self._stop(p.hostname, "ROBOTS_UNVERIFIED")
            delay = policy.crawl_delay("MaAnalizEvidence") or policy.crawl_delay("*") or 1
            rate = policy.request_rate("MaAnalizEvidence") or policy.request_rate("*")
            if rate and rate.requests:
                delay = max(delay, rate.seconds / rate.requests)
            self.policies[p.hostname] = (policy, delay)
        if not self.policies[p.hostname][0].can_fetch("MaAnalizEvidence", url):
            self._stop(p.hostname, "ROBOTS_DISALLOWED")
        return self._request(key, url)[1:]
