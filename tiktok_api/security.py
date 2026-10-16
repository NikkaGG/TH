import hashlib
import secrets

from cryptography.fernet import Fernet, MultiFernet
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, keys: str):
        self.cipher = MultiFernet([Fernet(k.strip().encode()) for k in keys.split(",")])

    def encrypt(self, value: str):
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str):
        return self.cipher.decrypt(value.encode()).decode()


def get_vault():
    return Vault(get_settings().encryption_keys.get_secret_value())


bearer = HTTPBearer(auto_error=False)


def principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    supplied = digest(credentials.credentials if credentials else "")
    settings = get_settings()
    selected = None
    for role, secret in (
        ("admin", settings.admin_key),
        ("operator", settings.operator_key),
        ("viewer", settings.viewer_key),
    ):
        raw = secret.get_secret_value()
        matched = secrets.compare_digest(supplied, digest(raw))
        if raw and matched:
            selected = role
    if selected is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return selected


def roles(*allowed):
    def check(role=Depends(principal)):
        if role not in allowed:
            raise HTTPException(403, "Insufficient role")
        return role

    return check
