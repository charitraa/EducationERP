"""The key string: ``erp_<prefix>_<secret>``.

``erp_`` makes a leaked key recognisable to secret scanners; the prefix is
public and finds the row; the secret is 256 random bits.
"""
import hashlib
import hmac
import secrets

PREFIX_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_prefix() -> str:
    return "".join(secrets.choice(PREFIX_CHARS) for _ in range(10))


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def compose(prefix: str, secret: str) -> str:
    return f"erp_{prefix}_{secret}"


def split(raw: str) -> tuple[str, str] | None:
    parts = (raw or "").strip().split("_", 2)
    if len(parts) != 3 or parts[0] != "erp" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def matches(secret: str, stored_hash: str) -> bool:
    return hmac.compare_digest(digest(secret), stored_hash)
