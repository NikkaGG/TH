from datetime import timedelta

from sqlalchemy import select, update

from .db import utcnow
from .models import Account, Audit, Mutex, OAuthState
from .providers.official import ProviderError
from .security import digest, get_vault


def consume_state(db, state, binding):
    # Compare-and-set makes replay prevention work across API replicas and SQLite too.
    result = db.execute(
        update(OAuthState)
        .where(
            OAuthState.state_hash == digest(state),
            OAuthState.binding_hash == digest(binding),
            OAuthState.used.is_(False),
            OAuthState.expires_at > utcnow(),
        )
        .values(used=True)
    )
    db.commit()  # Consume before contacting TikTok; never replay an authorization code.
    return result.rowcount == 1


def save_tokens(account, data):
    required = ("open_id", "access_token", "refresh_token", "scope", "expires_in", "refresh_expires_in")
    if (
        any(k not in data for k in required)
        or data.get("open_id") != account.open_id
        or not isinstance(data.get("scope"), str)
    ):
        raise ProviderError("invalid_token_response", requires_action=True)
    if not all(isinstance(data[k], str) and data[k] for k in required[:3]):
        raise ProviderError("invalid_token_response", requires_action=True)
    try:
        expires, refresh_expires = int(data["expires_in"]), int(data["refresh_expires_in"])
        if not 0 < expires <= 31536000 or not 0 < refresh_expires <= 10 * 31536000:
            raise ValueError()
        expires_at, refresh_at = utcnow() + timedelta(seconds=expires), utcnow() + timedelta(seconds=refresh_expires)
    except (TypeError, ValueError, OverflowError):
        raise ProviderError("invalid_token_response", requires_action=True) from None
    vault = get_vault()
    account.access_token = vault.encrypt(data["access_token"])
    account.refresh_token = vault.encrypt(data["refresh_token"])
    account.expires_at, account.refresh_expires_at = expires_at, refresh_at
    account.scopes = data["scope"].split(",")
    account.status = "active"


def connect(db, code, client, settings):
    data = client.token(grant_type="authorization_code", code=code, redirect_uri=settings.redirect_uri)
    if not isinstance(data.get("open_id"), str) or not data["open_id"]:
        raise ProviderError("invalid_token_response")
    profile = client.profile(data.get("access_token", ""))
    if profile["open_id"] != data["open_id"]:
        raise ProviderError("identity_mismatch")
    # A real UPDATE, not a process lock: serializes account-capacity checks on both databases.
    db.execute(update(Mutex).where(Mutex.name == "accounts").values(version=Mutex.version + 1))
    account = db.scalar(select(Account).where(Account.open_id == data["open_id"]).with_for_update())
    active = list(db.scalars(select(Account.id).where(Account.status != "disconnected")))
    if (account is None or account.status == "disconnected") and len(active) >= settings.max_accounts:
        raise ProviderError("account_capacity_reached")
    if account is None:
        account = Account(open_id=data["open_id"])
        db.add(account)
    save_tokens(account, data)
    account.display_name = str(profile.get("display_name", ""))[:256]
    db.flush()
    db.add(Audit(actor="admin", action="account.connected", resource_id=account.id))
    db.commit()
    return account


def access_token(db, account_id, client):
    # Hold this transaction until the caller's read finishes; refresh and revoke cannot race.
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Account).where(Account.id == account_id).values(status=Account.status))
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if not account or account.status != "active":
        raise ProviderError("account_unavailable", requires_action=True)
    if account.expires_at <= utcnow() + timedelta(minutes=5):
        if account.refresh_expires_at <= utcnow():
            account.status = "reauthorization_required"
            db.commit()
            raise ProviderError("refresh_expired", requires_action=True)
        try:
            data = client.token(grant_type="refresh_token", refresh_token=get_vault().decrypt(account.refresh_token))
            save_tokens(account, data)
            db.commit()  # Persist rotating refresh token immediately, before any further reads.
        except ProviderError as exc:
            db.rollback()
            if exc.requires_action:
                account = db.get(Account, account_id)
                account.status = "reauthorization_required"
                db.commit()
            raise
        # Re-acquire and re-check after the token-persistence commit.
        return _locked_token(db, account_id)
    return get_vault().decrypt(account.access_token)


def _locked_token(db, account_id):
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Account).where(Account.id == account_id).values(status=Account.status))
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if not account or account.status != "active":
        raise ProviderError("account_unavailable", requires_action=True)
    return get_vault().decrypt(account.access_token)
