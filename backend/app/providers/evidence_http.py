"""Bounded, robots-gated evidence GETs. No redirects, credentials, retries or persistence."""
import hashlib
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from datetime import datetime, timezone
import httpx

USER_AGENT = "MaAnalizEvidence/2.0 (+https://github.com/donozoyout-ux/Ma-analiz/issues)"
HOSTS = {"api.openligadb.de", "raw.githubusercontent.com", "football.goaloo.com", "www.goaloo.com"}


class EvidenceFailure(RuntimeError):
    pass


class EvidenceHTTP:
    def __init__(self, *, client=None, sleep=time.sleep, clock=time.monotonic):
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.owns_client = client is None
        self.sleep, self.clock = sleep, clock
        self.requests, self.policies, self.cache, self.last, self.delays = [], {}, {}, {}, {}
        self.data_last = {}

    def close(self):
        if self.owns_client:
            self.client.close()

    def _request(self, url, *, data=False):
        host = urlsplit(url).hostname
        if len(self.requests) >= 12:
            raise EvidenceFailure("REQUEST_BUDGET_EXCEEDED")
        interval = max(self.delays.get(host, 1), 10 if "goaloo" in host else 1)
        previous = self.last.get(host)
        if previous is not None:
            self.sleep(max(0, interval-(self.clock()-previous)))
        if data and host == "api.openligadb.de" and host in self.data_last:
            self.sleep(max(0,60-(self.clock()-self.data_last[host])))
        self.last[host] = self.clock()
        if data:self.data_last[host] = self.clock()
        entry = {"url":url,"observed_at":datetime.now(timezone.utc).isoformat()}
        self.requests.append(entry)
        try:
            response = self.client.get(url,headers={"User-Agent":USER_AGENT},follow_redirects=False)
        except httpx.HTTPError:
            entry["error"]="NETWORK_OR_TLS_FAILURE"
            raise EvidenceFailure("NETWORK_OR_TLS_FAILURE") from None
        entry.update(http_status=response.status_code,bytes=len(response.content),
                     sha256=hashlib.sha256(response.content).hexdigest())
        if len(response.content)>5_000_000:
            raise EvidenceFailure("RESPONSE_TOO_LARGE")
        if response.is_redirect or response.status_code in {401,403,429}:
            raise EvidenceFailure(f"ACCESS_STOP_HTTP_{response.status_code}")
        return response

    def get(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port:
            raise EvidenceFailure("UNAPPROVED_ORIGIN")
        if url in self.cache:return self.cache[url]
        origin=f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.policies:
            response=self._request(origin+"/robots.txt")
            policy=RobotFileParser()
            if response.status_code in {404,410}:
                policy.parse([])
            elif response.status_code==200 and not response.text.lstrip().startswith("<"):
                policy.parse(response.text.splitlines())
                self.requests[-1]["robots_text"]=response.text[:5000]
            else:
                raise EvidenceFailure("ROBOTS_UNVERIFIED")
            delay=policy.crawl_delay("MaAnalizEvidence") or policy.crawl_delay("*") or 0
            rate=policy.request_rate("MaAnalizEvidence") or policy.request_rate("*")
            if rate and rate.requests>0:delay=max(delay,rate.seconds/rate.requests)
            self.delays[parsed.hostname]=delay
            self.policies[origin]=policy
        if not self.policies[origin].can_fetch("MaAnalizEvidence",url):
            raise EvidenceFailure("ROBOTS_DISALLOWED")
        response=self._request(url,data=True)
        if response.status_code!=200:raise EvidenceFailure(f"HTTP_{response.status_code}")
        self.cache[url]=response
        return response

    def json(self,url):
        try:return self.get(url).json()
        except ValueError:raise EvidenceFailure("INVALID_JSON") from None
