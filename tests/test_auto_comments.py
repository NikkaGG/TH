"""Изолированная БД и имитация TikTok: реальные комментарии не отправляются."""
import asyncio
import json
import logging
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

# До импорта приложения: ни сессия пользователя, ни его база не используются.
_data = tempfile.TemporaryDirectory(prefix="ttlh-tests-")
os.environ["TTLH_DATA_DIR"] = _data.name

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import SEARCH_STATE_PATH
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Account, AccountConnection, Draft, DraftStatus, TaskRun, TaskStatus, Video
from app.services import account_sessions, auto_comments, connections, settings, tasks
from app.workers import tiktok_search as ts


def tearDownModule():
    engine.dispose()
    logging.shutdown()
    _data.cleanup()


@asynccontextmanager
async def fake_context(**kwargs):
    yield object()


class AutoCommentTests(unittest.TestCase):
    def test_stop_interrupts_preparation_without_publishing(self):
        task = self.start(draft_ids=[1]).json()
        async def publish(ctx, url, text, **kwargs):
            auto_comments.request_stop(task['id'])
            await asyncio.sleep(10)
            self.fail('Preparation continued after stop')
        result = self.execute(task['id'], publish)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['found'], 0)
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_stop_after_click_preserves_confirmation(self):
        task = self.start(draft_ids=[1]).json()
        async def publish(ctx, url, text, **kwargs):
            kwargs['before_post']()
            auto_comments.request_stop(task['id'])
            await asyncio.sleep(.4)
            return 'confirmed-once'
        result = self.execute(task['id'], publish)
        self.assertEqual(result['found'], 1)
        self.assertEqual(self.statuses()[0], DraftStatus.SENT)

    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        settings.save({"connected_username": "tester"})
        SEARCH_STATE_PATH.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        self.client = TestClient(app)  # Без lifespan: исполнитель подменён, перезапуск тестируется отдельно.
        self.enqueue = patch.object(tasks, "enqueue").start()
        self.addCleanup(patch.stopall)
        with SessionLocal() as db:
            account = Account(username="tester", daily_reply_limit=3, proxy_url="http://proxy.test:8080", authenticated_at=datetime.now())
            db.add(account)
            db.flush()
            self.account = account.id
            state = account_sessions.state_path(account.id)
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            settings.save({"active_account_id": account.id})
            for i in range(1, 5):
                v = Video(video_id=str(i), url=f"https://www.tiktok.com/@author/video/{i}", query="#test", author="author")
                db.add(v)
                db.flush()
                db.add(Draft(video_id=v.id, text=f"Тест {i}"))
            db.commit()

    def start(self, **kwargs):
        return self.client.post("/api/tasks/auto-comment", json={
            "account_id": self.account, "limit": 2, "interval_seconds": 30, **kwargs,
        })

    def statuses(self):
        with SessionLocal() as db:
            return list(db.scalars(select(Draft.status).order_by(Draft.id)))

    def execute(self, task_id, publisher):
        with patch.object(ts, "open_context", fake_context), patch.object(ts, "post_comment_on_video", publisher):
            tasks._execute(task_id)
        return self.client.get("/api/tasks/auto-comment").json()

    def test_reservations_block_duplicate_edit_delete_and_manual_limit(self):
        task = self.start(limit=3)
        self.assertEqual(task.status_code, 202, task.text)
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.client.patch("/api/drafts/1", json={"text": "changed"}).status_code, 409)
        self.assertEqual(self.client.delete("/api/drafts/1").status_code, 409)
        self.assertEqual(self.client.delete("/api/videos/1").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/accounts/{self.account}").status_code, 409)
        self.client.patch("/api/drafts/4", json={"account_id": self.account})
        self.assertEqual(self.client.post("/api/drafts/4/sent").status_code, 409)
        self.assertEqual(len(self.client.get("/api/drafts?status=posting").json()), 3)

    def test_parallel_start_reserves_only_once(self):
        # У каждого запроса собственный клиент; lifespan не запускаем.
        def submit():
            return TestClient(app).post("/api/tasks/auto-comment", json={"account_id": self.account, "limit": 2}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(codes), [202, 409])
        self.assertEqual(self.statuses().count(DraftStatus.PENDING_POST), 2)

    def test_limit_and_wrong_account_and_invalid_settings(self):
        self.assertEqual(self.start(limit=4).status_code, 409)
        self.assertEqual(self.start(interval_seconds=0).status_code, 422)
        with SessionLocal() as db:
            db.get(Account, self.account).authenticated_at = None
            db.commit()
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_failed_enqueue_releases_reservations(self):
        self.enqueue.side_effect = RuntimeError("executor unavailable")
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)
        self.assertEqual(self.client.get("/api/tasks/auto-comment").json()["status"], "failed")

    def test_success_and_stop_between_comments(self):
        task = self.start().json()
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            auto_comments.request_stop(task["id"])
            return "999"
        result = self.execute(task["id"], publish)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["found"], 1)
        self.assertEqual(self.statuses(), [DraftStatus.SENT, DraftStatus.DRAFT, DraftStatus.DRAFT, DraftStatus.DRAFT])
        self.assertEqual(self.client.get("/api/stats").json()["replies_sent_total"], 1)

    def test_single_success(self):
        task = self.start(draft_ids=[1]).json()
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            return "123"
        result = self.execute(task["id"], publish)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["found"], 1)
        self.assertEqual(self.statuses()[0], DraftStatus.SENT)

    def test_stop_before_start_never_opens_browser(self):
        task = self.start().json()
        self.assertEqual(self.client.post(f"/api/tasks/{task['id']}/stop").status_code, 200)
        with patch.object(ts, "open_context") as browser:
            tasks._execute(task["id"])
            browser.assert_not_called()
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_uncertain_is_quarantined_and_requires_resolution(self):
        task = self.start().json()
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            raise ts.PostUncertainError("Ответ потерян")
        result = self.execute(task["id"], publish)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.statuses(), [DraftStatus.REVIEW, DraftStatus.DRAFT, DraftStatus.DRAFT, DraftStatus.DRAFT])
        self.assertEqual(self.start(draft_ids=[1]).status_code, 409)
        self.assertEqual(self.client.post("/api/drafts/1/resolve", json={"published": False}).status_code, 200)
        self.assertEqual(self.statuses()[0], DraftStatus.DRAFT)

    def test_rejection_does_not_mark_sent(self):
        task = self.start().json()
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            raise ts.CommentRejectedError("TikTok отказал")
        self.assertEqual(self.execute(task["id"], publish)["status"], "failed")
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_account_paused_before_click(self):
        task = self.start().json()
        self.client.patch(f"/api/accounts/{self.account}", json={"status": "paused"})
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            self.fail("Отправка с неактивного аккаунта")
        self.assertEqual(self.execute(task["id"], publish)["status"], "failed")
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_lowered_limit_is_checked_before_click(self):
        task = self.start().json()
        self.client.patch(f"/api/accounts/{self.account}", json={"daily_reply_limit": 1})
        async def publish(ctx, url, text, **kwargs):
            kwargs["before_post"]()
            self.fail("Отправка сверх уменьшенного лимита")
        self.assertEqual(self.execute(task["id"], publish)["status"], "failed")
        self.assertEqual(self.statuses(), [DraftStatus.DRAFT] * 4)

    def test_restart_does_not_retry_inflight(self):
        self.start()
        with SessionLocal() as db:
            db.get(Draft, 1).status = DraftStatus.SENDING
            db.commit()
        tasks.recover_interrupted()
        self.assertEqual(self.statuses(), [DraftStatus.REVIEW, DraftStatus.DRAFT, DraftStatus.DRAFT, DraftStatus.DRAFT])
        self.assertEqual(self.client.post("/api/drafts/1/resolve", json={"published": True}).status_code, 200)
        self.assertEqual(self.client.get("/api/stats").json()["replies_sent_total"], 1)


class PublicationBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch()
        self.ctx = await self.browser.new_context()
        self.mode = "success"
        self.requests = 0
        self.comments_mode = 'ready'
        self.html = '''<div data-e2e="comment-input" contenteditable="true"></div>
        <button data-e2e="comment-post" onclick="fetch('/api/comment/publish/?aweme_id=123&text=' +
          encodeURIComponent(document.querySelector('[contenteditable]').innerText), {method:'POST'})">Post</button>'''
        async def handler(route):
            if '/api/comment/list/' in route.request.url:
                if self.comments_mode == 'empty':
                    await route.fulfill(status=200, headers={'content-length':'0'}, body='')
                else:
                    await route.fulfill(status=200, content_type='application/json', body=json.dumps({'status_code':0,'comments':[]}))
                return
            if "/api/comment/publish/" in route.request.url:
                self.requests += 1
                if self.mode == 'empty':
                    await route.fulfill(status=200, content_type='application/json', headers={'content-length':'0'}, body='')
                    return
                body = {"status_code": 0, "comment": {"cid": "999", "aweme_id": "123", "text": "Тест", "user": {"unique_id": "tester"}}}
                if self.mode == "reject":
                    body = {"status_code": 9}
                if self.mode == "uncertain":
                    body = {"status_code": 0}
                if self.mode == "wrong_author":
                    body["comment"]["user"]["unique_id"] = "someone_else"
                if self.mode == "wrong_video":
                    body["comment"]["aweme_id"] = "456"
                if self.mode == "malformed_status":
                    body["status_code"] = "0"
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            else:
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html + "<script>fetch('/api/comment/list/?aweme_id=123')</script>")
        # Все запросы подменяются локально, никакого соединения с TikTok.
        await self.ctx.route("**/*", handler)
        self.info = patch.object(ts, "_account_info", AsyncMock(return_value={"data": {"username": "tester"}})).start()

    async def asyncTearDown(self):
        patch.stopall()
        await self.browser.close()
        await self.pw.stop()

    async def publish(self):
        return await ts.post_comment_on_video(self.ctx, "https://www.tiktok.com/@author/video/123", "Тест", expected_username="tester")

    async def test_real_browser_confirms_response_and_clicks_once(self):
        self.assertEqual(await self.publish(), "999")
        self.assertEqual(self.requests, 1)

    async def test_real_browser_rejection_is_not_success(self):
        self.mode = "reject"
        with self.assertRaises(ts.CommentRejectedError):
            await self.publish()
        self.assertEqual(self.requests, 1)

    async def test_real_browser_unknown_result_is_not_retried(self):
        for mode in ("uncertain", "wrong_author", "wrong_video", "malformed_status"):
            with self.subTest(mode=mode):
                self.mode = mode
                before = self.requests
                with self.assertRaises(ts.PostUncertainError):
                    await self.publish()
                self.assertEqual(self.requests, before + 1)

    async def test_real_browser_wrong_account_never_clicks(self):
        self.info.return_value = {"data": {"username": "someone_else"}}
        with self.assertRaises(ts.SessionError):
            await self.publish()
        self.assertEqual(self.requests, 0)

    async def test_disabled_comments_fail_without_publication(self):
        state = {'__DEFAULT_SCOPE__': {'webapp.video-detail': {'itemInfo': {'itemStruct': {'itemCommentStatus': 3}}}}}
        self.html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">' + json.dumps(state) + '</script><p>Related videos</p>'
        with self.assertRaisesRegex(ts.CommentRejectedError, 'отключил комментарии'):
            await self.publish()
        self.assertEqual(self.requests, 0)

    async def test_cookie_dialog_and_comments_tab_open_before_single_click(self):
        self.html = '''<section id="consent"><button onclick="document.querySelector('#consent').remove()">Отклонить использование дополнительных файлов cookie</button></section>
        <button role="tab" onclick="document.querySelector('#form').hidden=false">Комментарии</button>
        <div id="form" hidden><div data-e2e="comment-input" contenteditable="true"></div>
        <button data-e2e="comment-post" onclick="fetch('/api/comment/publish/?aweme_id=123&text=' + encodeURIComponent(document.querySelector('[contenteditable]').innerText), {method:'POST'})">Post</button></div>'''
        self.assertEqual(await self.publish(), '999')
        self.assertEqual(self.requests, 1)

    async def test_hidden_inbox_tab_does_not_hide_video_comment_button(self):
        self.html = '''<button role="tab" hidden>Комментарии</button>
        <div role="button" data-e2e="comment-icon" aria-label="Прочитать или оставить комментарии" onclick="document.querySelector('#form').hidden=false">3792</div>
        <div id="form" hidden><div data-e2e="comment-input" contenteditable="true"></div>
        <button data-e2e="comment-post" onclick="fetch('/api/comment/publish/?aweme_id=123&text=' + encodeURIComponent(document.querySelector('[contenteditable]').innerText), {method:'POST'})">Post</button></div>'''
        self.assertEqual(await self.publish(), '999')
        self.assertEqual(self.requests, 1)

    async def test_normalized_request_text_still_reads_explicit_rejection(self):
        self.mode = 'reject'
        self.html = '''<div data-e2e="comment-input" contenteditable="true"></div>
        <button data-e2e="comment-post" onclick="fetch('/api/comment/publish/?aweme_id=123&text=normalized', {method:'POST'})">Post</button>'''
        with self.assertRaises(ts.CommentRejectedError):
            await self.publish()
        self.assertEqual(self.requests, 1)

    async def test_empty_publication_response_is_uncertain_and_never_retried(self):
        self.mode = 'empty'
        with self.assertRaisesRegex(ts.PostUncertainError, 'пустой ответ'):
            await self.publish()
        self.assertEqual(self.requests, 1)

    async def test_head_probe_is_not_a_publication_confirmation(self):
        response = SimpleNamespace(url='https://www.tiktok.com/api/comment/publish/?aweme_id=123&text=x',
                                   request=SimpleNamespace(method='HEAD'))
        self.assertFalse(ts._publication_response(response,'123','x'))

    async def test_late_captcha_never_attempts_publication(self):
        self.comments_mode = 'empty'
        with patch.object(ts, '_raise_if_blocked', AsyncMock(side_effect=[None, ts.CaptchaError('не загрузил комментарии: капча')])):
            with self.assertRaisesRegex(ts.CaptchaError, 'не загрузил комментарии'):
                await self.publish()
        self.assertEqual(self.requests, 0)

    async def test_rotation_challenge_detected_without_captcha_css(self):
        self.html = '<p>Передвиньте ползунок, чтобы совместить пазл</p>' + self.html
        with self.assertRaisesRegex(ts.CaptchaError, 'капчу'):
            await self.publish()
        self.assertEqual(self.requests, 0)


class PanelBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_search_selection_draft_and_export(self):
        from playwright.async_api import async_playwright
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        settings.save({})
        with SessionLocal() as db:
            account=Account(username='workflow_user',proxy_url='http://proxy.test:8080',authenticated_at=datetime.now())
            video=Video(video_id='123',url='https://www.tiktok.com/@author/video/123',query='#coffee',author='author',description='Кофейня и кофе',views=1523)
            db.add_all([account,video]);db.commit();account_id=account.id
        state=account_sessions.state_path(account_id);state.parent.mkdir(parents=True,exist_ok=True);state.write_text('{"cookies":[],"origins":[]}',encoding='utf-8')
        settings.save({'active_account_id':account_id})
        client=TestClient(app);errors=[]
        with patch.object(tasks,'enqueue'):
            async with async_playwright() as pw:
                browser=await pw.chromium.launch();page=await browser.new_page()
                page.set_default_timeout(7000)
                page.on('pageerror',lambda e:errors.append(str(e)))
                async def route_app(route):
                    r=route.request
                    if r.url.startswith('http://ttlh.test/'):
                        result=client.request(r.method,r.url.replace('http://ttlh.test',''),content=r.post_data,
                                              headers={'content-type':'application/json'} if r.post_data else {})
                        await route.fulfill(status=result.status_code,headers=dict(result.headers),body=result.content)
                    else:
                        await route.abort()
                await page.route('**/*',route_app)
                await page.goto('http://ttlh.test/#templates')
                await page.locator('[data-action="template-new"]').click()
                await page.locator('#template-name').fill('Первый контакт')
                await page.locator('#template-body').fill('{Привет|Здравствуйте}, [author]!')
                await page.locator('#btn-preview').click()
                await page.locator('#preview-box:not(.hidden)').wait_for()
                await page.locator('#template-form button[type="submit"], #template-form .dialog-actions .primary').click()
                await page.locator('.template-card').wait_for()
                await page.locator('.tab-btn[data-tab="search"]').click()
                await page.locator('#search-queries').fill('#coffee\nкофе Алматы')
                template_id=client.get('/api/templates').json()[0]['id']
                await page.locator('#search-template').select_option(str(template_id))
                await page.locator('#search-form button[type="submit"]').click()
                await page.locator('#search-tasks .task-row').wait_for()
                search=client.get('/api/tasks').json()[0]
                self.assertEqual(search['params']['account_id'],account_id)
                self.assertEqual(search['params']['template_id'],template_id)
                await page.locator('.tab-btn[data-tab="videos"]').click()
                await page.locator('[data-video-action="relevant"]').click()
                await page.wait_for_function("document.querySelector('#videos-body .badge')?.textContent === 'Подходит'")
                await page.locator('[data-pick]').check()
                await page.locator('#btn-generate').click()
                await page.wait_for_function("document.querySelector('#badge-drafts').textContent === '1'")
                # Download navigation bypasses our virtual HTTP origin; check URL and CSV response.
                await page.evaluate("window.download = url => { window.exportURL = url; }")
                await page.locator('#v-export').click()
                exported=client.get(await page.evaluate('window.exportURL'))
                self.assertEqual(exported.status_code,200)
                self.assertIn('leads_',exported.headers['content-disposition'])
                self.assertTrue(exported.content.startswith(b'\xef\xbb\xbf'))
                await page.locator('.tab-btn[data-tab="drafts"]').click()
                await page.locator('#draft-text').fill('Изменённый ответ')
                await page.evaluate('tick()')
                self.assertEqual(await page.locator('#draft-text').input_value(),'Изменённый ответ')
                await page.locator('[data-draft-action="save"]').click()
                await page.wait_for_function("document.querySelector('#toast').textContent === 'Черновик сохранён'")
                self.assertEqual(client.get('/api/drafts').json()[0]['text'],'Изменённый ответ')
                await page.locator('[data-draft-action="manual"]').click()
                await page.locator('[data-draft-action="skip"]').click()
                await page.locator('#draft-detail .empty').wait_for()
                self.assertEqual(len(client.get('/api/drafts?status=skipped').json()),1)
                await page.locator('#queue-export').click()
                await page.locator('#exp-from').fill('2026-10-15')
                await page.locator('#exp-to').fill('2026-10-14')
                await page.locator('#export-form .primary').click()
                await page.locator('#export-error:not(.hidden)').wait_for()
                await page.unroute_all(behavior='wait');await browser.close()
                self.assertEqual(errors,[])

    async def test_sessionid_wizard_registers_only_after_success(self):
        from playwright.async_api import async_playwright
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        settings.save({})
        client = TestClient(app)
        errors = []
        with patch.object(tasks, "enqueue"):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width":1366,"height":860})
                page.set_default_timeout(7000)
                page.on("pageerror",lambda e:errors.append(str(e)))
                async def route_app(route):
                    request=route.request
                    if request.url.startswith("http://ttlh.test/"):
                        response=client.request(request.method,request.url.replace("http://ttlh.test",""),content=request.post_data,
                                                headers={"content-type":"application/json"} if request.post_data else {})
                        await route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
                    else:
                        await route.abort()
                await page.route("**/*",route_app)
                await page.goto("http://ttlh.test/#accounts")
                await page.locator('#accounts-list .empty').wait_for()
                await page.locator('[data-action="connect"]').first.click()
                await page.locator('#connect-proxy').fill('proxy.test:8080:user:secretpass')
                await page.locator('#connect-submit').click()
                await page.locator('#session-form:not(.hidden)').wait_for()
                self.assertEqual(await page.locator('.session-instructions li').count(),4)
                self.assertIsNone(client.get('/api/accounts/connection/latest').json())
                await page.locator('#connect-sessionid').fill('testsessionvalue0123456789abcdef')
                await page.locator('#session-submit').click()
                await page.locator('#connect-progress h3').wait_for()
                self.assertEqual(client.get('/api/accounts').json(),[])
                task=client.get('/api/accounts/connection/latest').json()
                self.assertEqual(task['params']['method'],'sessionid')
                self.assertNotIn('testsessionvalue',json.dumps(task))
                tasks._set(task['id'],status=TaskStatus.RUNNING,result='Проверяем sessionid через прокси…')
                await page.evaluate('tick()')
                await page.get_by_role('heading',name='Проверяем подключение',exact=True).wait_for()
                self.assertEqual(client.get('/api/accounts').json(),[])
                async def login(proxy,state,chrome,on_stage,should_stop):
                    state.write_text('{"cookies":[],"origins":[]}',encoding='utf-8')
                    return 'verified_user'
                with patch.object(ts,'connect_with_session',login):
                    await asyncio.to_thread(tasks._execute,task['id'])
                await page.evaluate('tick()')
                await page.get_by_role('heading',name='Аккаунт подключён',exact=True).wait_for()
                self.assertEqual(await page.locator('#connect-proxy').input_value(),'')
                self.assertEqual(await page.locator('#connect-sessionid').input_value(),'')
                await page.locator('#connect-done').click()
                await page.locator('.account-identity strong').wait_for()
                self.assertEqual(await page.locator('.account-identity strong').inner_text(),'@verified_user')
                await page.locator('[data-action="connect"]').first.click()
                await page.locator('#connect-proxy').fill('bad-format')
                await page.locator('#connect-submit').click()
                await page.locator('#connect-sessionid').fill('testsessionvalue0123456789abcdef')
                await page.locator('#session-submit').click()
                await page.locator('#session-error:not(.hidden)').wait_for()
                self.assertEqual(len(client.get('/api/accounts').json()),1)
                await page.locator('#session-back').click()
                await page.locator('#connect-proxy').fill('proxy.test:8080')
                await page.locator('#connect-submit').click()
                await page.locator('.connection-alternative summary').click()
                await page.locator('#connect-browser-login').click()
                await page.locator('#connect-progress h3').wait_for()
                self.assertEqual(client.get('/api/accounts/connection/latest').json()['params']['method'],'browser')
                await page.unroute_all(behavior='wait')
                await browser.close()
                self.assertEqual(errors,[])

    async def test_panel_start_stop_and_manual_review(self):
        from playwright.async_api import async_playwright
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        settings.save({"connected_username": "tester"})
        SEARCH_STATE_PATH.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        with SessionLocal() as db:
            account = Account(username="tester", proxy_url="http://proxy.test:8080", authenticated_at=datetime.now())
            video = Video(video_id="123", url="https://www.tiktok.com/@author/video/123", query="#test", author="author")
            db.add_all([account, video])
            db.flush()
            state = account_sessions.state_path(account.id)
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            settings.save({"active_account_id": account.id})
            draft = Draft(video_id=video.id, text="Тест")
            db.add(draft)
            db.commit()
        errors = []
        client = TestClient(app)
        with patch.object(tasks, "enqueue"):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width": 1366, "height": 860})
                page.set_default_timeout(7000)
                await page.add_init_script("window.tailwind = {}")
                page.on("pageerror", lambda error: errors.append(str(error)))
                async def route_app(route):
                    request = route.request
                    if request.url.startswith("http://ttlh.test/"):
                        response = client.request(request.method, request.url.replace("http://ttlh.test", ""), content=request.post_data,
                                                  headers={"content-type": "application/json"} if request.post_data else {})
                        await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
                    else:
                        await route.abort()  # Внешние запросы и TikTok запрещены в тесте.
                await page.route("**/*", route_app)
                await page.goto("http://ttlh.test/")
                await page.add_style_tag(content=".hidden {display:none !important}")
                await page.locator('.tab-btn[data-tab="drafts"]').click()
                await page.locator('[data-act="auto"]').wait_for()
                await page.locator('[data-action="auto-dialog"]').click()
                await page.locator('#auto-form [name="limit"]').fill("1")
                await page.locator('#auto-start').click()
                await page.wait_for_function("document.querySelector('#d-status').value === 'posting'")
                await page.locator('[data-id="1"] textarea:disabled').wait_for()
                await page.locator('#auto-stop').click()
                await page.wait_for_function("document.querySelector('#auto-stop').disabled")
                # Имитируем прерывание уже начатой публикации, затем проверяем восстановление в UI.
                with SessionLocal() as db:
                    db.get(Draft, 1).status = DraftStatus.SENDING
                    db.commit()
                tasks.recover_interrupted()
                await page.locator('#d-status').select_option("review")
                await page.locator('[data-act="confirm-absent"]').click()
                await page.locator('#d-status').select_option("draft")
                await page.locator('[data-act="auto"]').wait_for()
                # Проверяем одиночную кнопку с реальным сохранением текста и назначением аккаунта.
                await page.locator('[data-id="1"] textarea').fill("Новый текст")
                await page.locator('[data-act="auto"]').click()
                await page.wait_for_function("document.querySelector('#d-status').value === 'posting'")
                with SessionLocal() as db:
                    self.assertEqual(db.get(Draft, 1).text, "Новый текст")
                    self.assertEqual(db.get(Draft, 1).status, DraftStatus.PENDING_POST)
                await page.unroute_all(behavior="wait")
                await browser.close()
                self.assertEqual(errors, [])


class ConnectionFlowTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        settings.save({})
        self.client = TestClient(app)
        self.enqueue = patch.object(tasks, "enqueue").start()
        self.addCleanup(patch.stopall)

    def connect(self, proxy="proxy.test:8080:login:secretpass"):
        response = self.client.post("/api/accounts/connect", json={"proxy": proxy, "use_system_chrome": False})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn("secretpass", response.text)
        return response.json()

    def finish(self, task, username="tester"):
        async def login(proxy, state, chrome, on_stage, should_stop):
            # До реального подтверждения пользователя аккаунт не зарегистрирован.
            with SessionLocal() as db:
                self.assertIsNone(db.scalars(select(Account).where(Account.username == username)).first())
            state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
            return username
        with patch.object(ts, "connect_account", login):
            tasks._execute(task["id"])
        return self.client.get("/api/accounts/connection/latest").json()

    def test_proxy_required_and_manual_creation_disabled(self):
        self.assertEqual(self.client.post("/api/accounts/connect", json={"proxy": ""}).status_code, 422)
        self.assertEqual(self.client.post("/api/accounts/connect", json={"proxy": "bad"}).status_code, 409)
        self.assertEqual(self.client.post("/api/accounts", json={"username": "tester"}).status_code, 409)
        self.assertEqual(self.client.post("/api/session/import", json={"data": "a" * 32}).status_code, 409)
        self.assertEqual(self.client.get("/api/accounts").json(), [])

    def test_account_only_appears_after_verified_login(self):
        task = self.connect()
        self.assertEqual(self.client.get("/api/accounts").json(), [])
        self.assertEqual(self.finish(task)["status"], "done")
        accounts = self.client.get("/api/accounts").json()
        self.assertEqual(len(accounts), 1)
        self.assertTrue(accounts[0]["session_ready"])
        self.assertNotIn("secretpass", json.dumps(accounts))
        profile = account_sessions.profile(accounts[0]["id"])
        self.assertEqual(profile.proxy_url, "http://login:secretpass@proxy.test:8080")
        self.assertTrue(profile.state_path.exists())
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(AccountConnection))), [])

    def test_accounts_keep_distinct_proxy_and_storage(self):
        self.finish(self.connect("first.test:8001:user:secretpass"), "first_user")
        self.finish(self.connect("second.test:8002:user:secretpass"), "second_user")
        accounts = self.client.get("/api/accounts").json()
        profiles = [account_sessions.profile(a["id"]) for a in accounts]
        self.assertNotEqual(profiles[0].proxy_url, profiles[1].proxy_url)
        self.assertNotEqual(profiles[0].state_path, profiles[1].state_path)
        self.assertEqual(account_sessions.profile().username, "second_user")
        search = self.client.post("/api/tasks/search", json={"queries": ["#test"], "account_id": profiles[0].account_id})
        self.assertEqual(search.status_code, 202)
        self.assertEqual(search.json()["params"]["account_id"], profiles[0].account_id)

    def test_failed_login_keeps_existing_accounts_and_selection(self):
        self.finish(self.connect(), "existing")
        previous = account_sessions.profile()
        task = self.connect("another.test:8081:user:secretpass")
        with patch.object(ts, "connect_account", AsyncMock(side_effect=ts.SessionError("Вход не выполнен"))):
            tasks._execute(task["id"])
        self.assertEqual(len(self.client.get("/api/accounts").json()), 1)
        self.assertEqual(account_sessions.profile().account_id, previous.account_id)
        self.assertEqual(self.client.get("/api/accounts/connection/latest").json()["status"], "failed")

    def test_stop_before_login_never_opens_browser(self):
        task = self.connect()
        self.client.post(f"/api/tasks/{task['id']}/stop")
        with patch.object(ts, "connect_account", AsyncMock()) as login:
            tasks._execute(task["id"])
            login.assert_not_called()
        self.assertEqual(self.client.get("/api/accounts").json(), [])
        self.assertEqual(self.client.get("/api/accounts/connection/latest").json()["status"], "cancelled")

    def test_cancellation_during_login_does_not_register(self):
        task = self.connect()
        with patch.object(ts, "connect_account", AsyncMock(side_effect=ts.ConnectionCancelled())):
            tasks._execute(task["id"])
        self.assertEqual(self.client.get("/api/accounts").json(), [])
        self.assertEqual(self.client.get("/api/accounts/connection/latest").json()["status"], "cancelled")

    def test_enqueue_failure_does_not_block_future_connection(self):
        self.enqueue.side_effect = RuntimeError("queue stopped")
        response = self.client.post("/api/accounts/connect", json={"proxy": "proxy.test:8080"})
        self.assertEqual(response.status_code, 409)
        with SessionLocal() as db:
            self.assertEqual(list(db.scalars(select(AccountConnection))), [])
        self.enqueue.side_effect = None
        self.connect()

    def test_additive_migration_preserves_existing_rows(self):
        from sqlalchemy import create_engine, text, inspect
        from app import database
        legacy = create_engine("sqlite://")
        with legacy.begin() as conn:
            conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, username TEXT)"))
            conn.execute(text("INSERT INTO accounts (username) VALUES ('existing')"))
        with patch.object(database, "engine", legacy):
            database.migrate_accounts()
            database.migrate_accounts()
        self.assertIn("proxy_url", {c["name"] for c in inspect(legacy).get_columns("accounts")})
        with legacy.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT username FROM accounts")).scalar(), "existing")
        legacy.dispose()

    def test_sessionid_kept_out_of_responses_and_only_added_after_check(self):
        sid='testsessionvalue0123456789abcdef'
        response=self.client.post('/api/accounts/connect',json={'proxy':'proxy.test:8080','sessionid':sid})
        self.assertEqual(response.status_code,202,response.text)
        self.assertNotIn(sid,response.text)
        task=response.json()
        self.assertEqual(task['params']['method'],'sessionid')
        self.assertEqual(self.client.get('/api/accounts').json(),[])
        async def verify(proxy,state,chrome,on_stage,should_stop):
            cookies=json.loads(state.read_text(encoding='utf-8'))['cookies']
            self.assertEqual(next(c['value'] for c in cookies if c['name']=='sessionid'),sid)
            return 'imported_user'
        with patch.object(ts,'connect_with_session',verify),patch.object(ts,'connect_account',AsyncMock()) as browser:
            tasks._execute(task['id'])
            browser.assert_not_called()
        accounts=self.client.get('/api/accounts').json()
        self.assertEqual(accounts[0]['username'],'imported_user')
        self.assertTrue(accounts[0]['session_ready'])
        self.assertNotIn(sid,self.client.get('/api/tasks').text)
        self.assertNotIn(sid,self.client.get('/api/logs').text)

    def test_rejected_sessionid_does_not_add_account_and_removes_pending_file(self):
        sid='testsessionvalue0123456789abcdef'
        task=self.client.post('/api/accounts/connect',json={'proxy':'proxy.test:8080','sessionid':sid}).json()
        pending=SEARCH_STATE_PATH.parent/'pending'/f"{task['params']['connection_id']}.json"
        self.assertTrue(pending.exists())
        with patch.object(ts,'connect_with_session',AsyncMock(side_effect=ts.SessionError('TikTok не принял sessionid'))):
            tasks._execute(task['id'])
        self.assertFalse(pending.exists())
        self.assertEqual(self.client.get('/api/accounts').json(),[])

    def test_invalid_sessionid_not_echoed_in_validation_errors(self):
        for sid in ['', 'sessionid=secret-session-value-123', 'secret-session-value-123 with space']:
            with self.subTest(length=len(sid)):
                response=self.client.post('/api/accounts/connect',json={'proxy':'proxy.test:8080','sessionid':sid})
                self.assertEqual(response.status_code,409)
                if sid:self.assertNotIn(sid,response.text)
        self.assertEqual(self.client.get('/api/accounts').json(),[])


class ConnectionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_waits_for_initial_results_before_pagination(self):
        found = {}
        empty = SimpleNamespace(first=SimpleNamespace(is_visible=AsyncMock(return_value=False)))
        page = SimpleNamespace(locator=lambda _: SimpleNamespace(count=AsyncMock(return_value=0)),
                               get_by_text=lambda _: empty, wait_for_timeout=AsyncMock())
        async def initial_results(_):
            found['123'] = ts.FoundVideo('123', 'https://www.tiktok.com/@author/video/123', 'author')
        page.wait_for_timeout.side_effect = initial_results
        with patch.object(ts, '_raise_if_blocked', AsyncMock()), patch.object(ts, '_recover_error_page', AsyncMock()):
            await ts._wait_search_ready(page, found, visible=True, timeout_s=1)
        page.wait_for_timeout.assert_awaited_once()

    async def test_initial_search_wait_checks_for_captcha(self):
        with patch.object(ts, '_raise_if_blocked', AsyncMock(side_effect=ts.CaptchaError('manual challenge'))):
            with self.assertRaises(ts.CaptchaError):
                await ts._wait_search_ready(object(), {}, visible=False, timeout_s=1)

    async def test_successful_search_preserves_session_after_manual_challenge(self):
        context = SimpleNamespace(storage_state=AsyncMock())
        @asynccontextmanager
        async def browser(**kwargs):
            yield context
        selected = SimpleNamespace(account_id=7, state_path=SEARCH_STATE_PATH)
        videos = [ts.FoundVideo('123', 'https://www.tiktok.com/@author/video/123', 'author')]
        results, errors = [], []
        with patch.object(account_sessions, 'profile', return_value=selected), patch.object(ts, 'open_context', browser), patch.object(ts, 'search_videos', AsyncMock(return_value=videos)):
            await ts.search_many(['test'], 10, lambda q, v: results.extend(v), lambda q, e: errors.append(e), visible=True, account_id=7)
        context.storage_state.assert_awaited_once_with(path=str(SEARCH_STATE_PATH))
        self.assertEqual(results, videos)
        self.assertEqual(errors, [])

    async def test_failed_challenge_does_not_replace_saved_session(self):
        context = SimpleNamespace(storage_state=AsyncMock())
        @asynccontextmanager
        async def browser(**kwargs):
            yield context
        selected = SimpleNamespace(account_id=7, state_path=SEARCH_STATE_PATH)
        with patch.object(account_sessions, 'profile', return_value=selected), patch.object(ts, 'open_context', browser), patch.object(ts, 'search_videos', AsyncMock(side_effect=ts.CaptchaError('challenge required'))):
            with self.assertRaises(ts.CaptchaError):
                await ts.search_many(['test'], 10, lambda q, v: None, lambda q, e: None, account_id=7)
        context.storage_state.assert_not_awaited()

    async def test_imported_session_verified_through_proxy_without_login_window(self):
        page=SimpleNamespace(goto=AsyncMock(),inner_text=AsyncMock(return_value='{"ip":"203.0.113.5"}'))
        context=SimpleNamespace(new_page=AsyncMock(return_value=page),storage_state=AsyncMock())
        context_args=[]
        @asynccontextmanager
        async def browser(**kwargs):
            context_args.append(kwargs)
            yield context
        state=SEARCH_STATE_PATH.parent/'import-test.json';state.write_text('{}',encoding='utf-8')
        with patch.object(ts,'open_context',browser),patch.object(ts,'_has_session',AsyncMock(return_value=True)),patch.object(ts,'_account_info',AsyncMock(return_value={'data':{'username':'Imported'}})):
            username=await ts.connect_with_session('http://proxy.test:8080',state,False,lambda _:None,lambda:False)
        self.assertEqual(username,'imported')
        self.assertTrue(context_args[0]['headless'])
        self.assertTrue(context_args[0]['use_state'])
        self.assertEqual(context_args[0]['proxy_url'],'http://proxy.test:8080')
        self.assertEqual(page.goto.await_args_list[1].args[0],'https://www.tiktok.com')
        context.storage_state.assert_awaited_once_with(path=str(state))

    async def test_imported_session_rejected_when_server_does_not_confirm_identity(self):
        page=SimpleNamespace(goto=AsyncMock(),inner_text=AsyncMock(return_value='{"ip":"203.0.113.5"}'))
        context=SimpleNamespace(new_page=AsyncMock(return_value=page),storage_state=AsyncMock())
        @asynccontextmanager
        async def browser(**kwargs):
            yield context
        state=SEARCH_STATE_PATH.parent/'import-test.json';state.write_text('{}',encoding='utf-8')
        for info in [None,{'message':'error','data':{}},{'data':{}}]:
            with self.subTest(info=info),patch.object(ts,'open_context',browser),patch.object(ts,'_has_session',AsyncMock(return_value=True)),patch.object(ts,'_account_info',AsyncMock(return_value=info)):
                with self.assertRaises(ts.SessionError):
                    await ts.connect_with_session('http://proxy.test:8080',state,False,lambda _:None,lambda:False)
        context.storage_state.assert_not_called()

    async def test_proxy_checked_before_login_and_state_saved_after_identity(self):
        page = SimpleNamespace(goto=AsyncMock(), inner_text=AsyncMock(return_value='{"ip":"203.0.113.5"}'), is_closed=lambda:False)
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), storage_state=AsyncMock())
        context_args = []
        @asynccontextmanager
        async def browser(**kwargs):
            context_args.append(kwargs)
            yield context
        with patch.object(ts, "open_context", browser), patch.object(ts, "_has_session", AsyncMock(return_value=True)), patch.object(ts, "_account_info", AsyncMock(return_value={"data":{"username":"Tester"}})):
            state = SEARCH_STATE_PATH.parent / "test-connection.json"
            username = await ts.connect_account("http://proxy.test:8080", state, False, lambda _:None, lambda:False)
        self.assertEqual(username, "tester")
        self.assertEqual(page.goto.await_args_list[0].args[0], "https://api.ipify.org?format=json")
        self.assertEqual(page.goto.await_args_list[1].args[0], "https://www.tiktok.com/login")
        self.assertEqual(context_args[0]["proxy_url"], "http://proxy.test:8080")
        self.assertFalse(context_args[0]["use_state"])
        context.storage_state.assert_awaited_once_with(path=str(state))

    async def test_bad_proxy_never_opens_tiktok(self):
        page = SimpleNamespace(goto=AsyncMock(side_effect=RuntimeError("ERR_PROXY_CONNECTION_FAILED")))
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        @asynccontextmanager
        async def browser(**kwargs):
            yield context
        with patch.object(ts, "open_context", browser):
            with self.assertRaises(RuntimeError):
                await ts.connect_account("http://proxy.test:8080", SEARCH_STATE_PATH, False, lambda _:None, lambda:False)
        self.assertEqual(page.goto.await_count, 1)

    async def test_browser_uses_account_proxy_and_its_own_cookies(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            account=Account(username="isolated",proxy_url="http://user:pass@proxy.test:8080",authenticated_at=datetime.now())
            db.add(account);db.commit();account_id=account.id
        path=account_sessions.state_path(account_id);path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"cookies":[],"origins":[]}',encoding="utf-8")
        context=SimpleNamespace(set_default_timeout=lambda _:None, set_storage_state=AsyncMock(), close=AsyncMock())
        browser=SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
        launch=AsyncMock(return_value=context)
        @asynccontextmanager
        async def playwright():
            yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        with patch.object(ts,"async_playwright",playwright):
            async with ts.open_context(account_id=account_id):
                pass
        proxy = launch.await_args.kwargs["proxy"]
        self.assertTrue(proxy["server"].startswith("http://127.0.0.1:"))
        self.assertNotIn("username", proxy)
        self.assertNotIn("password", proxy)
        context.set_storage_state.assert_awaited_once_with(path)
        self.assertIn(str(account_id), launch.await_args.args[0])
        context.close.assert_awaited_once()
        context.set_storage_state.reset_mock()
        with patch.object(ts,"async_playwright",playwright):
            async with ts.open_context(account_id=account_id):
                pass
        context.set_storage_state.assert_not_awaited()
        original_dir = launch.await_args.args[0]
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            account.proxy_url = 'http://other.test:8080'
            db.commit()
        with patch.object(ts,"async_playwright",playwright):
            async with ts.open_context(account_id=account_id):
                pass
        self.assertNotEqual(original_dir, launch.await_args.args[0])
        context.set_storage_state.assert_awaited_once_with(path)


if __name__ == "__main__":
    unittest.main()
