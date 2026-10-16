"""Generate local API credentials without printing them or touching legacy data."""

import base64
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; preserved. Edit it manually if needed.")
template = (root / ".env.example").read_text(encoding="utf-8")
for placeholder in (
    "CHANGE_ME_ADMIN_RANDOM_32_PLUS",
    "CHANGE_ME_OPERATOR_RANDOM_32_PLUS",
    "CHANGE_ME_VIEWER_RANDOM_32_PLUS",
):
    template = template.replace(placeholder, secrets.token_urlsafe(40))
template = template.replace("CHANGE_ME_FERNET_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
template = template.replace("CHANGE_ME_DATABASE_PASSWORD", secrets.token_urlsafe(32))
template = template.replace("CHANGE_ME_REDIS_PASSWORD", secrets.token_urlsafe(32))
target.write_text(template, encoding="utf-8")
print("Created .env with random API keys and encryption key. Keep this file private and backed up.")
