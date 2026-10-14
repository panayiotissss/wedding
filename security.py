"""
Password hashing for per-event gallery passwords.

Uses PBKDF2-HMAC-SHA256 from the standard library so there's no extra native
dependency.  Format stored in the DB:  pbkdf2$<b64 salt>$<b64 hash>
"""

import base64
import hashlib
import hmac
import os

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, b64salt, b64hash = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.b64decode(b64salt)
        expected = base64.b64decode(b64hash)
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
        return hmac.compare_digest(test, expected)
    except Exception:
        return False
