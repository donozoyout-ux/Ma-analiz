"""Session-local write capability; never enabled by a Boolean/environment flag."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from app.providers.result_sources import utc

_CURRENT = ContextVar("controlled_import_permission", default=None)


@dataclass
class Permission:
    session: object
    store: object
    deadline: object
    clock: object
    candidate: dict

    def check(self, session, store=None):
        from app.storage.import_review import ReviewBlocked
        if session is not self.session or store is not None and store is not self.store or self.clock() > self.deadline:
            raise ReviewBlocked("IMPORT_SESSION_PERMISSION_INVALID_OR_EXPIRED")


def check_permission(session, store=None):
    permission = _CURRENT.get()
    if permission is None:
        return False
    permission.check(session, store)
    return True


@contextmanager
def authorized_scope(session, store, *, operator, grant, proof, match_id, clock, recovery=None):
    """No serialized report can open this scope: validator-issued proof + signed approval required."""
    from app.storage.import_review import ReviewBlocked
    operation = "ARCHIVE_RECOVERY" if recovery else "FIXTURE_IMPORT"
    payload = operator.verify(grant, now=clock(), match_id=match_id, operation=operation)
    if recovery:
        from app.providers.multileague import digest
        identity = recovery["identity"]
        if (digest(recovery) != payload["journal_snapshot"] or identity["source_match_id"] != str(match_id)
                or identity["provider"] != "openligadb" or identity["league"] != "de.bl1"
                or str(match_id) == "83237"
                or payload["source_key"] != digest([identity["provider"], identity["source_match_id"]])):
            raise ReviewBlocked("RECOVERY_SNAPSHOT_CHANGED")
        candidate = {"source_key": payload["source_key"]}
    else:
        if proof is None or session.get_bind().dialect.name == "postgresql" and not proof.live:
            raise ReviewBlocked("TRUSTED_LIVE_PROOF_REQUIRED")
        candidate = proof.candidate(clock())
        original = payload["candidate"]
        from app.providers.multileague import digest
        if (digest(original["evidence"]) != original["evidence_sha256"]
                or candidate["evidence"]["identity"]["source_match_id"] != str(match_id)
                or candidate["blockers"] or not candidate["technical_eligibility"]
                or candidate["source_key"] != original["source_key"]
                or candidate["evidence"]["source_version"] != original["evidence"]["source_version"]):
            raise ReviewBlocked("APPROVED_SOURCE_VERSION_CHANGED")
        # Enforce fresh original reviewed evidence as well as the new source response.
        from app.analysis.import_review import fresh
        if not fresh(original["evidence"]["observation"]["response_received_at"], clock()):
            raise ReviewBlocked("APPROVAL_EVIDENCE_EXPIRED")
    permission = Permission(session, store, utc(payload["expires_at"]), clock, candidate)
    token = _CURRENT.set(permission)
    try:
        yield candidate, payload
    finally:
        _CURRENT.reset(token)


class ImmutableImportStore:
    """Narrow wrapper: only one computed immutable part can be written; never delete/overwrite."""
    def __init__(self, session, store):
        self.session, self.root = session, store
        self.keys = set()

    def get_object(self, key): return self.root.get_object(key)
    def head_object(self, key): return self.root.head_object(key)

    def put_object_if_absent(self, key, body, **kwargs):
        from app.storage.import_review import ReviewBlocked
        if key not in self.keys or not check_permission(self.session, self):
            raise ReviewBlocked("IMMUTABLE_IMPORT_SCOPE_REQUIRED")
        return self.root.put_object_if_absent(key, body, **kwargs)

    def put_object(self, *args, **kwargs):
        from app.storage.import_review import ReviewBlocked
        raise ReviewBlocked("R2_OVERWRITE_FORBIDDEN")

    def delete_object(self, *args, **kwargs):
        from app.storage.import_review import ReviewBlocked
        raise ReviewBlocked("R2_DELETE_FORBIDDEN")
