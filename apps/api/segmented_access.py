"""Process-local atomic operator policy replacement; no public administration API."""
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from hmac import compare_digest
from threading import RLock
from pydantic import SecretStr
from apps.api.settings import Settings
from core.paper import segmented_revocations as revocations


class Denied(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__('Operator access denied')


@dataclass(frozen=True, repr=False)
class Policy:
    token: SecretStr | None
    accounts: frozenset
    actions: frozenset


def policy(settings):
    # Revalidate even constructed/mutated settings; never retain caller lists.
    try:
        value = Settings.model_validate(settings.model_dump(warnings=False))
    except Exception:
        raise ValueError('Invalid operator policy') from None
    token = SecretStr(value.paper_operator_token.get_secret_value()) if value.paper_operator_token is not None else None
    return Policy(token, frozenset(value.paper_operator_accounts), frozenset(value.paper_operator_actions))


class Access:
    def __init__(self, settings, *, engine=None):
        self._engine = engine
        self._lock = RLock()
        self._policy = policy(settings)
        self._revision = 0
        self._active = 0

    def replace(self, settings, *, expected_revision):
        candidate = policy(settings)
        with self._lock:
            if self._active:
                raise ValueError('Cannot replace policy inside an admitted operation')
            if type(expected_revision) is not int or expected_revision != self._revision:
                raise ValueError('Stale operator policy revision')
            self._policy = candidate
            self._revision += 1
            return self._revision

    def _authenticate(self, token):
        current = self._policy.token
        if current is None: raise Denied(503)
        if not isinstance(token, str) or not compare_digest(token.encode(), current.get_secret_value().encode()):
            raise Denied(401)

    def authenticate(self, token):
        with self._lock:
            self._authenticate(token)

    @contextmanager
    def operation(self, token, identity, action):
        # Replacement waits for earlier admitted operations; subsequent operations
        # must authenticate and check grants again against the new policy.
        with self._lock:
            self._authenticate(token)
            try:
                with revocations.guard(self._engine,token,identity,action) if self._engine is not None else nullcontext():
                    if identity not in self._policy.accounts or action not in self._policy.actions:
                        raise Denied(403)
                    self._active += 1
                    try:
                        yield self._revision
                    finally:
                        self._active -= 1
            except revocations.Revoked as error:
                raise Denied(error.status) from None

    def revoke(self, command_id, *, ledger_id='*', action='*'):
        with self._lock:
            if self._active or self._engine is None or self._policy.token is None:
                raise ValueError('Durable revocation requires an idle configured database policy')
            return revocations.revoke(self._engine,self._policy.token.get_secret_value(),command_id,
                                      ledger_id=ledger_id,action=action)
