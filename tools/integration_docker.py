"""Real PostgreSQL/Redis/Celery integration test in disposable, isolated Compose volumes.

Uses explicitly synthetic account/video fixtures; never calls TikTok, never seeds the main stack.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import subprocess
import time

import httpx
from tiktok_api.config import get_settings

root = Path(__file__).resolve().parents[1]
project = "lead-hunter-api-qa-" + secrets.token_hex(4)
command = ["docker", "compose", "-p", project, "-f", "compose.yaml", "-f", "compose.test.yaml"]
report = {"project": project, "synthetic_fixtures": True, "live_tiktok_calls": 0, "main_stack_modified": False}
start = time.monotonic()


def docker(*args, input=None):
    result = subprocess.run([*command, *args], cwd=root, input=input, text=True, capture_output=True, timeout=180)
    if result.returncode:
        # Do not echo arbitrary environment/container output with credentials into the test report.
        raise RuntimeError("Docker command failed: " + args[0])
    return result.stdout


seed = """
import json
from datetime import timedelta
from tiktok_api.db import SessionLocal, utcnow
from tiktok_api.models import Account
from tiktok_api.security import get_vault
with SessionLocal() as db:
    accounts = []
    for index in range(10):
        a = Account(open_id='SYNTHETIC-QA-ACCOUNT-' + str(index), display_name='QA fixture ' + str(index),
                    scopes=['user.info.basic', 'video.list'], access_token=get_vault().encrypt('NOT-A-REAL-TOKEN'),
                    refresh_token=get_vault().encrypt('NOT-A-REAL-REFRESH'),
                    expires_at=utcnow()+timedelta(days=1), refresh_expires_at=utcnow()+timedelta(days=30))
        db.add(a)
        db.flush()
        accounts.append(a.id)
    db.commit()
    print(json.dumps(accounts))
"""
try:
    docker("up", "-d", "--no-build")
    with httpx.Client(
        base_url="http://127.0.0.1:18001",
        timeout=10,
        trust_env=False,
        headers={"Authorization": "Bearer " + get_settings().admin_key.get_secret_value()},
    ) as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise AssertionError("QA stack readiness timed out")
        account_ids = json.loads(docker("exec", "-T", "api", "python", "-", input=seed))
        assert len(account_ids) == 10
        assert (
            client.post(
                "/api/v1/videos/import",
                json={
                    "videos": [
                        {"url": "https://www.tiktok.com/@qa/video/111111111", "description": "coffee review"},
                        {"url": "https://www.tiktok.com/@qa/video/222222222", "description": "coffee advertisement"},
                        {"url": "https://www.tiktok.com/@qa/video/333333333", "description": "unrelated topic"},
                    ]
                },
            ).status_code
            == 201
        )
        campaign_ids = []
        for account_id in account_ids:
            for index in range(2):
                response = client.post(
                    "/api/v1/campaigns",
                    json={
                        "name": "QA parallel " + str(index),
                        "account_id": account_id,
                        "source": "import",
                        "keywords": ["coffee"],
                        "exclude_keywords": ["advertisement"],
                        "comment_text": "QA draft only",
                    },
                )
                assert response.status_code == 201
                campaign_ids.append(response.json()["id"])

        def run(campaign_id):
            key = "qa-idempotency-" + campaign_id
            response = client.post("/api/v1/campaigns/" + campaign_id + "/run", headers={"Idempotency-Key": key})
            assert response.status_code == 202
            repeated = client.post("/api/v1/campaigns/" + campaign_id + "/run", headers={"Idempotency-Key": key})
            assert repeated.json()["id"] == response.json()["id"]
            return response.json()["id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            job_ids = list(pool.map(run, campaign_ids))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job_list = client.get("/api/v1/jobs").json()
            if len(job_list) == 20 and all(j["status"] == "succeeded" for j in job_list):
                break
            assert all(j["status"] not in ("failed", "requires_action") for j in job_list), "QA worker failed"
            time.sleep(1)
        else:
            raise AssertionError("QA jobs did not finish")
        drafts = client.get("/api/v1/drafts").json()
        assert len(drafts) == 10, "Deduplication across campaigns failed"
        assert all(j["result"]["comments_published"] == 0 for j in job_list)
        assert client.post("/api/v1/drafts/" + drafts[0]["id"] + "/publish").status_code == 409
        manual = client.post("/api/v1/drafts/" + drafts[0]["id"] + "/manual-confirm", json={"confirmed": True})
        assert manual.json()["verified_by_tiktok"] is False
        report.update(
            status="passed",
            accounts=10,
            campaigns=20,
            jobs_succeeded=len(job_ids),
            unique_drafts=len(drafts),
            comments_published=0,
            broker="real Redis",
            database="real PostgreSQL",
            workers="real Celery prefork",
            idempotency="passed",
            cross_campaign_deduplication="passed",
        )
except Exception as exc:
    report.update(status="failed", failure_type=type(exc).__name__)
    raise
finally:
    docker("down", "-v", "--remove-orphans")
    report["test_containers_and_volumes_removed"] = True
    report["elapsed_seconds"] = round(time.monotonic() - start, 2)
    (root / "docs/evidence/docker-integration.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
