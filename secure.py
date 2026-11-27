"""Token encryption at rest + log/text redaction."""
import base64
import re

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_key(secret_key: str) -> bytes:
    if not secret_key:
        raise ValueError("SECRET_KEY is required")
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"unikpost-v1",
               info=b"token-encryption").derive(secret_key.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class TokenBox:
    def __init__(self, secret_key: str):
        self._f = Fernet(derive_key(secret_key))

    def encrypt(self, value):
        if value is None or value == "":
            return None
        return self._f.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value):
        if not value:
            return None
        try:
            return self._f.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None


_PATTERNS = [
    re.compile(r"(access_token|refresh_token|client_secret|fb_exchange_token|code|token)=([^&\s\"']+)", re.I),
    re.compile(r"(\"(?:access_token|refresh_token|client_secret)\"\s*:\s*\")([^\"]+)(\")", re.I),
    re.compile(r"(Bearer|OAuth)\s+[A-Za-z0-9\-\._~\+/]+=*", re.I),
    re.compile(r"\bEA[A-Za-z0-9]{30,}\b"),       # Meta tokens
    re.compile(r"\bya29\.[A-Za-z0-9\-_\.]+"),    # Google access tokens
    re.compile(r"\b1//[A-Za-z0-9\-_]{20,}"),     # Google refresh tokens
]


def redact(text) -> str:
    if text is None:
        return ""
    s = str(text)
    s = _PATTERNS[0].sub(lambda m: f"{m.group(1)}=***", s)
    s = _PATTERNS[1].sub(lambda m: f"{m.group(1)}***{m.group(3)}", s)
    s = _PATTERNS[2].sub(lambda m: f"{m.group(1)} ***", s)
    for p in _PATTERNS[3:]:
        s = p.sub("***", s)
    return s[:600]
