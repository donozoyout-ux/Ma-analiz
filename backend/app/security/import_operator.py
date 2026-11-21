"""Manual, single-candidate approval. No token is accepted via argv or saved in JSON."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import hmac
import json
import re

from app.providers.multileague import digest
from app.providers.result_sources import utc


class OperatorDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Operator:
    identity: str
    _token: str = field(repr=False)

    @classmethod
    def authenticate(cls, configured_id, token_sha256, token):
        if (not isinstance(configured_id, str) or not re.fullmatch(r"[\w.@-]{1,80}", configured_id)
                or not isinstance(token_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", token_sha256)):
            raise OperatorDenied("IMPORT_OPERATOR_NOT_CONFIGURED")
        if not isinstance(token, str) or len(token) < 32 or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), token_sha256):
            raise OperatorDenied("IMPORT_OPERATOR_UNAUTHORIZED")
        return cls(configured_id, token)

    def sign(self, payload):
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        return {"payload": deepcopy(payload), "signature": hmac.new(self._token.encode(), body, hashlib.sha256).hexdigest()}

    def verify(self, grant, *, now, match_id, operation):
        try:
            p = grant["payload"]
            expected = self.sign(p)["signature"]
            if not hmac.compare_digest(expected, grant["signature"]):
                raise ValueError()
            created, expires = utc(p["created_at"]), utc(p["expires_at"])
            if (p["operator"] != self.identity or p["match_id"] != str(match_id) or p["operation"] != operation
                    or not created or not expires or not created <= now <= expires
                    or expires - created > timedelta(minutes=10) or p["schema_version"] != 1):
                raise ValueError()
            return deepcopy(p)
        except Exception:
            raise OperatorDenied("IMPORT_APPROVAL_INVALID_EXPIRED_OR_SCOPE_CHANGED") from None

    def approve(self, proof, *, reason, now, sql_scope):
        candidate = proof.candidate(now)
        if candidate["blockers"] or not candidate["technical_eligibility"]:
            raise OperatorDenied("CANDIDATE_NOT_ELIGIBLE")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise OperatorDenied("APPROVAL_REASON_REQUIRED")
        p = {"schema_version": 1, "operation": "FIXTURE_IMPORT", "operator": self.identity,
             "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(), "reason": reason,
             "match_id": candidate["evidence"]["identity"]["source_match_id"], "candidate": candidate,
             "sql_scope": sql_scope}
        return self.sign(p)

    def approve_recovery(self, journal, *, reason, now):
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise OperatorDenied("APPROVAL_REASON_REQUIRED")
        return self.sign({"schema_version": 1, "operation": "ARCHIVE_RECOVERY", "operator": self.identity,
            "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(), "reason": reason,
            "match_id": journal["identity"]["source_match_id"], "source_key": journal["source_key"],
            "journal_snapshot": digest(journal)})
