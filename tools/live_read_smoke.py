"""Explicit opt-in live READ test. Never logs tokens or publishes anything."""

import argparse

from tiktok_api.db import SessionLocal
from tiktok_api.models import Account
from tiktok_api.accounts import access_token
from tiktok_api.providers.official import OfficialClient, ProviderError

parser = argparse.ArgumentParser()
parser.add_argument("account_id", help="An account already connected using the app OAuth flow")
args = parser.parse_args()
client = OfficialClient()
try:
    with SessionLocal() as db:
        account = db.get(Account, args.account_id)
        if not account:
            raise SystemExit("Account not found. Connect it using OAuth first.")
        token = access_token(db, account.id, client)
        profile = client.profile(token)
        if profile["open_id"] != account.open_id:
            raise SystemExit("FAILED: TikTok identity mismatch")
        videos = client.videos(token, limit=1)
        db.commit()
    print("PASS: live profile identity checked; public videos returned:", len(videos))
except ProviderError as exc:
    raise SystemExit("FAILED: " + exc.code) from None
finally:
    client.close()
