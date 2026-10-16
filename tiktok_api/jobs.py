from datetime import timedelta
import re
import time
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import accounts
from .db import SessionLocal, utcnow
from .matching import match
from .models import Account, Audit, Campaign, Candidate, Draft, Job, Video
from .providers import experimental
from .providers.official import OfficialClient, ProviderError
from .security import digest

ACTIVE = ("queued", "dispatched", "running", "retry_wait")


def enqueue(db, campaign_id, key):
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if not campaign:
        raise ValueError("campaign_not_found")
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Campaign).where(Campaign.id == campaign_id).values(enabled=Campaign.enabled))
    hashed = digest(campaign_id + ":" + key)
    existing = db.scalar(select(Job).where(Job.idempotency_hash == hashed))
    if existing:
        return existing
    active = db.scalar(select(Job).where(Job.campaign_id == campaign_id, Job.status.in_(ACTIVE)))
    if active:
        return active
    job = Job(campaign_id=campaign_id, idempotency_hash=hashed)
    db.add(job)
    db.flush()
    db.add(Audit(actor="system", action="job.queued", resource_id=job.id))
    return job


def schedule_due():
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(Campaign)
                .where(Campaign.enabled.is_(True), Campaign.next_run_at <= utcnow())
                .with_for_update(skip_locked=True)
            )
        )
        for campaign in due:
            if campaign.interval_minutes:
                enqueue(db, campaign.id, "schedule:" + campaign.next_run_at.isoformat())
                campaign.next_run_at = utcnow() + timedelta(minutes=campaign.interval_minutes)
        db.commit()


def recover_leases():
    with SessionLocal() as db:
        stale = list(
            db.scalars(
                select(Job)
                .where(Job.status.in_(("dispatched", "running")), Job.lease_until < utcnow())
                .with_for_update(skip_locked=True)
            )
        )
        for job in stale:
            job.error_code = "worker_lease_expired"
            job.status = "cancelled" if job.cancel_requested else ("failed" if job.attempts >= 3 else "queued")
            job.lease_until = None
            if job.status in ("cancelled", "failed"):
                job.finished_at = utcnow()
        db.commit()


def dispatch(send):
    """Database is the durable outbox; broker loss leaves recoverable jobs, not lost HTTP work."""
    recover_leases()
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Job.id)
                .where(
                    Job.status.in_(("queued", "retry_wait")),
                    Job.available_at <= utcnow(),
                    Job.cancel_requested.is_(False),
                )
                .order_by(Job.created_at)
                .limit(50)
            )
        )
    for job_id in ids:
        with SessionLocal() as db:
            claimed = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_(("queued", "retry_wait")),
                    Job.available_at <= utcnow(),
                    Job.cancel_requested.is_(False),
                )
                .values(status="dispatched", lease_until=utcnow() + timedelta(seconds=90))
            )
            db.commit()
            if not claimed.rowcount:
                continue
        try:
            send(job_id)
        except Exception:
            with SessionLocal() as db:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "dispatched")
                    .values(status="queued", lease_until=None, error_code="broker_unavailable")
                )
                db.commit()


def video_data(item, source, owner=None):
    external_id = str(item.get("id", ""))
    author = str(item.get("author", ""))
    if not re.fullmatch(r"\d{5,32}", external_id):
        raise ProviderError("invalid_video_id")
    url = item.get("share_url", "")
    if url:
        p = urlsplit(url)
        parsed = re.fullmatch(r"/@([\w.\-]{1,64})/video/(\d{5,32})", p.path, re.ASCII)
        if (
            p.scheme != "https"
            or p.netloc not in ("www.tiktok.com", "tiktok.com")
            or not parsed
            or parsed.group(2) != external_id
        ):
            raise ProviderError("invalid_video_url")
        author = parsed.group(1)
        url = "https://www.tiktok.com" + p.path
    else:
        if not re.fullmatch(r"[\w.\-]{1,64}", author, re.ASCII):
            raise ProviderError("invalid_video_author")
        url = f"https://www.tiktok.com/@{author}/video/{external_id}"
    return dict(
        external_id=external_id,
        url=url,
        author=author,
        description=str(item.get("video_description") or item.get("title") or "")[:10000],
        source=source,
        owner_account_id=owner,
        metadata_verified=source == "display",
    )


def collect(campaign, client):
    if campaign.source == "import":
        with SessionLocal() as db:
            return [
                {
                    "external_id": v.external_id,
                    "url": v.url,
                    "author": v.author,
                    "description": v.description,
                    "source": v.source,
                    "owner_account_id": v.owner_account_id,
                    "metadata_verified": v.metadata_verified,
                }
                for v in db.scalars(
                    select(Video)
                    .where(Video.source == "import")
                    .order_by(Video.created_at.desc())
                    .limit(campaign.max_results)
                )
            ]
    if campaign.source == "display":
        with SessionLocal() as db:
            token = accounts.access_token(db, campaign.account_id, client)
            account = db.get(Account, campaign.account_id)
            if "video.list" not in account.scopes:
                raise ProviderError("video_list_scope_missing", requires_action=True)
            items = client.videos(token, campaign.max_results)
            db.commit()
        return [video_data(v, "display", campaign.account_id) for v in items]
    if campaign.source == "experimental":
        items, seen = [], set()
        deadline = time.monotonic() + 200
        for term in campaign.keywords:
            remaining = campaign.max_results - len(items)
            if remaining <= 0:
                break
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise ProviderError("experimental_time_budget_exceeded", requires_action=True)
            for raw in experimental.search(term, remaining, timeout=min(120, seconds_left)):
                if raw["id"] not in seen:
                    seen.add(raw["id"])
                    items.append(video_data(raw, "experimental"))
        return items
    raise ProviderError("unsupported_source")


def upsert_video(db, data):
    video = db.scalar(select(Video).where(Video.external_id == data["external_id"]))
    if not video:
        # Distinct campaigns can discover the same video concurrently.
        try:
            with db.begin_nested():
                video = Video(**data)
                db.add(video)
                db.flush()
        except IntegrityError:
            video = db.scalar(select(Video).where(Video.external_id == data["external_id"]))
    # Imported data must never overwrite authoritative metadata or claim ownership.
    elif data["metadata_verified"] or not video.metadata_verified:
        for key, value in data.items():
            setattr(video, key, value)
    return video


def process(job_id, client_factory=OfficialClient):
    with SessionLocal() as db:
        claimed = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "dispatched", Job.cancel_requested.is_(False))
            .values(status="running", attempts=Job.attempts + 1, lease_until=utcnow() + timedelta(minutes=10))
        )
        db.commit()
        if not claimed.rowcount:
            return
        job = db.get(Job, job_id)
        attempt = job.attempts
        campaign = db.get(Campaign, job.campaign_id)
        account = db.get(Account, campaign.account_id)
        account_available = account is not None and account.status == "active"
    client = None
    try:
        if not account_available:
            raise ProviderError("account_unavailable", requires_action=True)
        client = client_factory()
        found = collect(campaign, client)
        with SessionLocal() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            # A stale worker must not finish a later delivery of the same job.
            if job.status != "running" or job.attempts != attempt:
                return
            if job.cancel_requested:
                job.status, job.finished_at, job.lease_until = "cancelled", utcnow(), None
                db.commit()
                return
            matched_count = draft_count = 0
            for data in found:
                video = upsert_video(db, data)
                score, terms, reason = match(video.description, campaign.keywords, campaign.exclude_keywords)
                candidate = db.scalar(
                    select(Candidate).where(Candidate.campaign_id == campaign.id, Candidate.video_id == video.id)
                )
                if not candidate:
                    candidate = Candidate(campaign_id=campaign.id, video_id=video.id)
                    db.add(candidate)
                candidate.score, candidate.matched_terms, candidate.reason = score, terms, reason
                if score < campaign.min_score:
                    continue
                matched_count += 1
                if not db.scalar(
                    select(Draft.id).where(Draft.account_id == campaign.account_id, Draft.video_id == video.id)
                ):
                    try:
                        with db.begin_nested():
                            db.add(
                                Draft(
                                    campaign_id=campaign.id,
                                    account_id=campaign.account_id,
                                    video_id=video.id,
                                    text=campaign.comment_text,
                                )
                            )
                            db.flush()
                        draft_count += 1
                    except IntegrityError:
                        pass
            job.status, job.finished_at, job.lease_until = "succeeded", utcnow(), None
            job.error_code = None
            job.result = {
                "found": len(found),
                "matched": matched_count,
                "drafts_created": draft_count,
                "comments_published": 0,
            }
            db.add(Audit(actor="worker", action="job.succeeded", resource_id=job.id, details=job.result))
            db.commit()
    except ProviderError as exc:
        finish_error(job_id, attempt, exc)
    except Exception:
        finish_error(job_id, attempt, ProviderError("internal_error"))
    finally:
        if client:
            client.close()


def finish_error(job_id, attempt, exc):
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.status != "running" or job.attempts != attempt:
            return
        job.error_code = exc.code
        job.lease_until = None
        if job.cancel_requested:
            job.status = "cancelled"
        elif exc.retryable and job.attempts < 3:
            job.status = "retry_wait"
            job.available_at = utcnow() + timedelta(seconds=max(exc.retry_after, 30 * 2 ** (job.attempts - 1)))
        else:
            job.status = "requires_action" if exc.requires_action else "failed"
        if job.status != "retry_wait":
            job.finished_at = utcnow()
        db.add(
            Audit(
                actor="worker",
                action="job." + job.status,
                resource_id=job.id,
                details={"code": exc.code, "attempt": attempt},
            )
        )
        db.commit()


def tick_local():
    schedule_due()
    dispatch(process)
