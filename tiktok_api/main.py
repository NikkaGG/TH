from contextlib import asynccontextmanager
from datetime import timedelta
import logging
from pathlib import Path
import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import redis
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from . import accounts, jobs
from .config import get_settings
from .db import get_db, utcnow
from .limits import allowance
from .models import Account, Audit, Campaign, Candidate, Draft, Job, OAuthState, Video
from .providers.official import OfficialClient, ProviderError
from .schemas import CampaignIn, DraftEdit, ManualConfirmation, ResearchQuery, VideoBatch
from .security import digest, get_vault, principal, roles

logger = logging.getLogger("lead_hunter")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)
logger.propagate = False
STATIC = Path(__file__).parent / "static"
read = Depends(principal)
operate = Depends(roles("admin", "operator"))
admin = Depends(roles("admin"))


@asynccontextmanager
async def lifespan(_app):
    # Settings are validated at startup. Schema creation belongs to migrations/CLI, never API replicas.
    get_settings()
    yield


app = FastAPI(
    title="Lead Hunter API",
    version="1.0.0",
    lifespan=lifespan,
    description="Official OAuth/Display integrations, discovery and manual comment queue. "
    "Posting comments on arbitrary third-party videos is unsupported.",
)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 1024 * 1024:
                return await JSONResponse({"detail": "Request body exceeds 1 MiB"}, 413)(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)


app.add_middleware(BodyLimit)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = secrets.token_hex(12)
    if request.url.path.startswith("/api/v1/") and request.url.path != "/api/v1/oauth/callback":
        raw = request.headers.get("authorization", "")
        bucket = digest(raw) if raw else "unauthenticated"
        try:
            allowed, wait = allowance("api:" + bucket, get_settings().api_requests_per_minute)
        except redis.RedisError:
            return JSONResponse({"detail": "Rate limiter unavailable"}, 503)
        if not allowed:
            return JSONResponse({"detail": "API rate limit exceeded"}, 429, headers={"Retry-After": str(wait)})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
    logger.info(
        '{"request_id":"%s","method":"%s","path":"%s","status":%s}',
        request_id,
        request.method,
        request.scope.get("route", {}).path if hasattr(request.scope.get("route"), "path") else "unmatched",
        response.status_code,
    )
    return response


@app.exception_handler(ProviderError)
async def provider_error(_request, exc):
    status = 429 if exc.code in ("rate_limit_exceeded", "local_rate_limit") else (409 if exc.requires_action else 502)
    return JSONResponse(
        {"detail": {"code": exc.code, "requires_action": exc.requires_action}},
        status,
        headers={"Retry-After": str(exc.retry_after)} if status == 429 else {},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    return JSONResponse({"detail": [{"loc": e["loc"], "type": e["type"], "msg": e["msg"]} for e in exc.errors()]}, 422)


def required(db, model, identity):
    entity = db.get(model, identity)
    if not entity:
        raise HTTPException(404, "Resource not found")
    return entity


def public(entity, fields):
    result = {f: getattr(entity, f) for f in fields.split()}
    # All externally returned timestamps explicitly carry UTC.
    for key, value in result.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat() + "Z"
    return result


ACCOUNT_FIELDS = "id open_id display_name scopes status expires_at refresh_expires_at created_at"
CAMPAIGN_FIELDS = "id name account_id source keywords exclude_keywords comment_text min_score max_results interval_minutes enabled next_run_at created_at"
JOB_FIELDS = (
    "id campaign_id status attempts available_at lease_until cancel_requested error_code result created_at finished_at"
)
VIDEO_FIELDS = "id external_id url author description source owner_account_id metadata_verified created_at"
DRAFT_FIELDS = "id campaign_id account_id video_id text status comment_id confirmed_at created_at"


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db.scalar(select(func.count(Account.id)))
        if get_settings().queue_mode == "celery":
            redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, 503)
    return {"status": "ready"}


@app.get("/api/v1/capabilities")
def capabilities(role=read):
    settings = get_settings()
    return {
        "role": role,
        "oauth_configured": bool(settings.client_key and settings.client_secret.get_secret_value()),
        "oauth_redirect_uri": settings.redirect_uri,
        "max_accounts": settings.max_accounts,
        "queue_mode": settings.queue_mode,
        "oauth_display": {"status": "available_with_app_approval", "scope": "own_public_videos"},
        "experimental_search": {"enabled": settings.experimental_search_enabled, "status": "unverified_live"},
        "research_search": {"enabled": settings.research_enabled, "scope": "approved_noncommercial_project_only"},
        "comment_publish_arbitrary_video": {
            "supported": False,
            "reason": "No supported public endpoint or OAuth scope verified",
        },
        "business_owned_video_comments": {
            "status": "documented_not_integrated",
            "requires": "Separate Business API access",
        },
        "manual_comment_queue": True,
        "semantic_video_analysis": False,
    }


@app.post("/api/v1/oauth/start")
def oauth_start(request: Request, response: Response, role=admin, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.client_key or not settings.client_secret.get_secret_value():
        raise HTTPException(503, "Configure TikTok client key, client secret and registered HTTPS callback first")
    if request.headers.get("host") != urlsplit(settings.redirect_uri).netloc:
        raise HTTPException(409, "Open this panel on the same HTTPS host and port as LH_REDIRECT_URI before OAuth")
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        OAuthState(state_hash=digest(state), binding_hash=digest(binding), expires_at=utcnow() + timedelta(minutes=10))
    )
    db.commit()
    response.set_cookie(
        "lh_oauth_binding",
        binding,
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/v1/oauth/callback",
    )
    params = {
        "client_key": settings.client_key,
        "response_type": "code",
        "scope": "user.info.basic,video.list",
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "disable_auto_auth": 1,
    }
    return {"authorization_url": "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params)}


@app.get("/api/v1/oauth/callback")
def oauth_callback(
    request: Request,
    state: str = Query(max_length=200),
    code: str | None = Query(None, max_length=2048),
    error: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
):
    binding = request.cookies.get("lh_oauth_binding", "")
    if not state or not binding or not accounts.consume_state(db, state, binding):
        raise HTTPException(400, "Invalid, expired, unbound or already used OAuth state")
    if error or not code:
        raise HTTPException(400, "TikTok authorization was not granted")
    client = OfficialClient()
    try:
        accounts.connect(db, code, client, get_settings())
    finally:
        client.close()
    response = RedirectResponse("/?connected=1", status_code=303)
    response.delete_cookie(
        "lh_oauth_binding", path="/api/v1/oauth/callback", secure=True, httponly=True, samesite="lax"
    )
    return response


@app.get("/api/v1/accounts")
def list_accounts(role=read, db: Session = Depends(get_db)):
    return [public(a, ACCOUNT_FIELDS) for a in db.scalars(select(Account).order_by(Account.created_at))]


@app.post("/api/v1/accounts/{account_id}/refresh")
def refresh(account_id: str, role=admin, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    client = OfficialClient()
    try:
        accounts.access_token(db, account_id, client)
        db.add(Audit(actor=role, action="account.token_checked", resource_id=account_id))
        db.commit()
    finally:
        client.close()
    return public(db.get(Account, account_id), ACCOUNT_FIELDS)


@app.delete("/api/v1/accounts/{account_id}")
def disconnect(account_id: str, role=admin, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Resource not found")
    if account.status == "disconnected":
        return {"status": "disconnected"}
    account.status = "revocation_pending"
    db.execute(update(Campaign).where(Campaign.account_id == account_id).values(enabled=False, next_run_at=None))
    client = OfficialClient()
    try:
        client.revoke(get_vault().decrypt(account.access_token))
    except ProviderError:
        # Persist a locally blocked account while retaining encrypted material for explicit revoke retry.
        db.commit()
        raise
    finally:
        client.close()
    account.access_token = account.refresh_token = ""
    account.status = "disconnected"
    db.add(Audit(actor=role, action="account.disconnected", resource_id=account_id))
    db.commit()
    return {"status": "disconnected"}


def validate_campaign(db, data):
    account = required(db, Account, data.account_id)
    if account.status != "active":
        raise HTTPException(409, "Account is not active")
    if data.source == "display" and "video.list" not in account.scopes:
        raise HTTPException(409, "Account did not authorize video.list")
    if data.source == "experimental" and not get_settings().experimental_search_enabled:
        raise HTTPException(409, "Experimental public search is disabled; live compatibility unverified")


@app.post("/api/v1/campaigns", status_code=201)
def create_campaign(data: CampaignIn, role=operate, db: Session = Depends(get_db)):
    validate_campaign(db, data)
    campaign = Campaign(**data.model_dump(), next_run_at=utcnow() if data.enabled else None)
    db.add(campaign)
    db.flush()
    db.add(Audit(actor=role, action="campaign.created", resource_id=campaign.id))
    db.commit()
    return public(campaign, CAMPAIGN_FIELDS)


@app.get("/api/v1/campaigns")
def list_campaigns(
    role=read, db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return [
        public(c, CAMPAIGN_FIELDS)
        for c in db.scalars(select(Campaign).order_by(Campaign.created_at.desc()).offset(offset).limit(limit))
    ]


@app.put("/api/v1/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, data: CampaignIn, role=operate, db: Session = Depends(get_db)):
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if not campaign:
        raise HTTPException(404, "Resource not found")
    validate_campaign(db, data)
    if db.scalar(select(Job.id).where(Job.campaign_id == campaign_id, Job.status.in_(jobs.ACTIVE))):
        raise HTTPException(409, "Wait for or cancel the active job before editing the campaign")
    for key, value in data.model_dump().items():
        setattr(campaign, key, value)
    campaign.next_run_at = utcnow() if campaign.enabled else None
    db.add(Audit(actor=role, action="campaign.updated", resource_id=campaign.id))
    db.commit()
    return public(campaign, CAMPAIGN_FIELDS)


@app.post("/api/v1/campaigns/{campaign_id}/run", status_code=202)
def run_campaign(campaign_id: str, request: Request, role=operate, db: Session = Depends(get_db)):
    campaign = required(db, Campaign, campaign_id)
    if required(db, Account, campaign.account_id).status != "active":
        raise HTTPException(409, "Account is not active")
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 128:
        raise HTTPException(400, "Provide an Idempotency-Key header of 8..128 characters")
    job = jobs.enqueue(db, campaign_id, key)
    db.commit()
    return public(job, JOB_FIELDS)


@app.post("/api/v1/videos/import", status_code=201)
def import_videos(data: VideoBatch, role=operate, db: Session = Depends(get_db)):
    imported = []
    for item in data.videos:
        path = item.url.split("/@", 1)[1]
        author, external_id = path.split("/video/")
        video = jobs.upsert_video(
            db,
            dict(
                external_id=external_id,
                url=item.url,
                author=author,
                description=item.description,
                source="import",
                owner_account_id=None,
                metadata_verified=False,
            ),
        )
        imported.append(video.id)
    db.add(Audit(actor=role, action="videos.imported", resource_id="batch", details={"count": len(imported)}))
    db.commit()
    return {"video_ids": imported, "metadata_verified": False}


@app.get("/api/v1/videos")
def list_videos(
    role=read, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return [
        public(v, VIDEO_FIELDS)
        for v in db.scalars(select(Video).order_by(Video.created_at.desc()).offset(offset).limit(limit))
    ]


@app.get("/api/v1/campaigns/{campaign_id}/candidates")
def candidates(
    campaign_id: str,
    role=read,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    required(db, Campaign, campaign_id)
    query = (
        select(Candidate, Video)
        .join(Video, Candidate.video_id == Video.id)
        .where(Candidate.campaign_id == campaign_id)
        .order_by(Candidate.score.desc(), Candidate.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        {**public(c, "id campaign_id score matched_terms reason"), "video": public(v, VIDEO_FIELDS)}
        for c, v in db.execute(query)
    ]


@app.get("/api/v1/jobs")
def list_jobs(
    role=read, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return [
        public(j, JOB_FIELDS)
        for j in db.scalars(select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit))
    ]


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, role=read, db: Session = Depends(get_db)):
    return public(required(db, Job, job_id), JOB_FIELDS)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, role=operate, db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "Resource not found")
    if job.status in jobs.ACTIVE:
        job.cancel_requested = True
        if job.status != "running":
            job.status, job.finished_at, job.lease_until = "cancelled", utcnow(), None
        db.add(Audit(actor=role, action="job.cancel_requested", resource_id=job.id))
        db.commit()
    return public(job, JOB_FIELDS)


@app.get("/api/v1/drafts")
def list_drafts(
    role=read,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, max_length=32),
):
    query = select(Draft, Video).join(Video, Draft.video_id == Video.id)
    if status:
        query = query.where(Draft.status == status)
    return [
        {
            **public(d, DRAFT_FIELDS),
            "video": public(v, VIDEO_FIELDS),
            "publication_evidence": "operator_self_report" if d.status == "manual_confirmed" else "none",
        }
        for d, v in db.execute(query.order_by(Draft.created_at.desc()).offset(offset).limit(limit))
    ]


@app.patch("/api/v1/drafts/{draft_id}")
def edit_draft(draft_id: str, data: DraftEdit, role=operate, db: Session = Depends(get_db)):
    draft = required(db, Draft, draft_id)
    if draft.status != "pending":
        raise HTTPException(409, "Only pending drafts can be edited")
    draft.text = data.text
    db.add(Audit(actor=role, action="draft.edited", resource_id=draft.id))
    db.commit()
    return public(draft, DRAFT_FIELDS)


@app.post("/api/v1/drafts/{draft_id}/publish", status_code=409)
def publish(draft_id: str, role=operate, db: Session = Depends(get_db)):
    required(db, Draft, draft_id)
    return JSONResponse(
        {
            "detail": {
                "code": "unsupported_comment_publication",
                "message": "No supported endpoint for comments on arbitrary third-party videos was verified.",
                "alternative": "Open the video in TikTok and publish manually using the intended account.",
            }
        },
        409,
    )


@app.post("/api/v1/drafts/{draft_id}/manual-confirm")
def confirm_draft(draft_id: str, data: ManualConfirmation, role=operate, db: Session = Depends(get_db)):
    draft = required(db, Draft, draft_id)
    if draft.status not in ("pending", "manual_confirmed"):
        raise HTTPException(409, "Draft cannot be confirmed")
    if draft.status == "pending":
        draft.status, draft.confirmed_at, draft.comment_id = "manual_confirmed", utcnow(), data.comment_id
        db.add(
            Audit(
                actor=role,
                action="draft.manually_confirmed",
                resource_id=draft.id,
                details={"evidence": "operator_self_report"},
            )
        )
        db.commit()
    return {**public(draft, DRAFT_FIELDS), "publication_evidence": "operator_self_report", "verified_by_tiktok": False}


@app.post("/api/v1/drafts/{draft_id}/skip")
def skip_draft(draft_id: str, role=operate, db: Session = Depends(get_db)):
    draft = required(db, Draft, draft_id)
    if draft.status != "pending":
        raise HTTPException(409, "Only pending drafts can be skipped")
    draft.status = "skipped"
    db.add(Audit(actor=role, action="draft.skipped", resource_id=draft.id))
    db.commit()
    return public(draft, DRAFT_FIELDS)


@app.post("/api/v1/research/videos/query")
def research(data: ResearchQuery, role=admin, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.research_enabled:
        raise HTTPException(409, "Research API requires separate approval for a noncommercial research project")
    try:
        allowed, wait = allowance("research:daily:" + digest(settings.research_client_key), 900, seconds=86400)
    except redis.RedisError:
        raise HTTPException(503, "Rate limiter unavailable") from None
    if not allowed:
        raise HTTPException(429, "Local research budget reached", headers={"Retry-After": str(wait)})
    client = OfficialClient()
    try:
        result = client.research(data)
    finally:
        client.close()
    db.add(
        Audit(
            actor=role,
            action="research.queried",
            resource_id="research",
            details={"project": settings.research_project},
        )
    )
    db.commit()
    return {"data": result, "purpose": "approved_noncommercial_research_only", "imported_to_lead_campaign": False}


@app.get("/api/v1/audit")
def audit(
    role=admin, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return [
        public(a, "id actor action resource_id details created_at")
        for a in db.scalars(select(Audit).order_by(Audit.id.desc()).offset(offset).limit(limit))
    ]


@app.get("/metrics", response_class=Response)
def metrics(role=read, db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    allowed = (*jobs.ACTIVE, "succeeded", "failed", "cancelled", "requires_action")
    lines = ["# HELP lead_hunter_jobs Number of persisted jobs by status", "# TYPE lead_hunter_jobs gauge"]
    lines += [f'lead_hunter_jobs{{status="{s}"}} {counts.get(s, 0)}' for s in allowed]
    lines += ["# TYPE lead_hunter_accounts gauge", f"lead_hunter_accounts {db.scalar(select(func.count(Account.id)))}"]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
