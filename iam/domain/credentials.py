"""Domain value object for device API credentials.

The Edge generates every API key with 256 random bits, so a fast SHA-256
digest is enough to keep the key out of the database: a slow, salted hash only
protects guessable secrets, and on the Raspberry Pi it costs every request of
every node (ADR-0016). Verifiers stored with PBKDF2 before keep working until
the key of the node is rotated.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

_SHA256_SCHEME = "sha256"
_PBKDF2_SCHEME = "pbkdf2_sha256"
_MIN_ITERATIONS = 100_000
_MAX_ITERATIONS = 1_000_000
_GENERATED_SECRET_BYTES = 32


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _sha256(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


@dataclass(frozen=True, repr=False)
class ApiCredential:
    """A one-way verifier for a device API key.

    New verifiers are a SHA-256 digest of the key. A verifier stored with
    PBKDF2 (``_salt`` and ``_iterations`` set) is still accepted.
    """

    _digest: bytes = field(repr=False)
    _salt: bytes | None = field(default=None, repr=False)
    _iterations: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if len(self._digest) != 32:
            raise ValueError("Invalid API key verifier material")
        if self._salt is None and self._iterations is None:
            return
        if self._salt is None or len(self._salt) != 16:
            raise ValueError("Invalid API key verifier material")
        if self._iterations is None or not _MIN_ITERATIONS <= self._iterations <= _MAX_ITERATIONS:
            raise ValueError("Invalid API key verifier work factor")

    @staticmethod
    def generate_secret() -> str:
        """Return a new random URL-safe API key for a device."""
        return secrets.token_urlsafe(_GENERATED_SECRET_BYTES)

    @classmethod
    def create(cls, secret: str) -> "ApiCredential":
        """Create the SHA-256 verifier of a non-empty device secret."""
        if not isinstance(secret, str) or not secret:
            raise ValueError("API key cannot be empty")
        return cls(_sha256(secret))

    @classmethod
    def from_encoded(cls, encoded: str) -> "ApiCredential":
        """Reconstitute a credential verifier from its persisted form."""
        try:
            parts = encoded.split("$")
            if parts[0] == _SHA256_SCHEME and len(parts) == 2:
                return cls(_b64decode(parts[1]))
            if parts[0] == _PBKDF2_SCHEME and len(parts) == 4:
                return cls(_b64decode(parts[3]), _b64decode(parts[2]), int(parts[1]))
            raise ValueError
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Invalid stored API key verifier") from error

    def matches(self, secret: str) -> bool:
        """Compare a presented API key with this verifier in constant time."""
        if not isinstance(secret, str) or not secret:
            return False
        if self._salt is None:
            candidate = _sha256(secret)
        else:
            candidate = hashlib.pbkdf2_hmac(
                "sha256", secret.encode("utf-8"), self._salt, self._iterations
            )
        return hmac.compare_digest(candidate, self._digest)

    def encode(self) -> str:
        """Return the versioned verifier representation for persistence."""
        if self._salt is None:
            return f"{_SHA256_SCHEME}${_b64encode(self._digest)}"
        return f"{_PBKDF2_SCHEME}${self._iterations}${_b64encode(self._salt)}${_b64encode(self._digest)}"
