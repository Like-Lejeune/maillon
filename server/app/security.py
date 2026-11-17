"""Mots de passe et jetons."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # Argon2id, paramètres recommandés par la bibliothèque

# Empreinte factice : sert à dépenser le même temps de calcul quand l'e-mail
# n'existe pas, pour ne pas révéler quels comptes existent.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SESSION_TOKEN_PREFIX = "ms_"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """Empreinte d'un jeton. Seule cette empreinte est stockée côté serveur."""
    return hashlib.sha256(token.encode()).hexdigest()


API_KEY_PREFIX = "mk_live_"
ENROLLMENT_TOKEN_PREFIX = "me_"
AGENT_TOKEN_PREFIX = "ma_"


def new_api_key() -> tuple[str, str]:
    """Renvoie (clé complète, préfixe affichable). La clé n'est montrée qu'une fois."""
    secret = secrets.token_urlsafe(32)
    key = API_KEY_PREFIX + secret
    return key, key[: len(API_KEY_PREFIX) + 8]


def new_enrollment_token() -> str:
    return ENROLLMENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def new_agent_token() -> str:
    return AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
