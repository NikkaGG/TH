/* Lead Hunter: no build step or runtime CDN dependencies. */
'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="vendor/icons.svg#${name}"></use></svg>`;
const num = (n) => n == null ? 'Нет данных' : Number(n).toLocaleString('ru-RU');
const date = (s) => s ? new Date(s).toLocaleString('ru-RU', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}) : 'Не проверено';
const initials = (s) => esc(String(s || 'TT').slice(0, 2));
const LABEL = {active:'Активен',paused:'На паузе',warning:'Есть ограничения',banned:'Заблокирован',new:'Новый',relevant:'Подходит',irrelevant:'Не подходит',replied:'С ответом',draft:'Черновик',pending_post:'Ожидает отправки',sending:'Публикуется',review:'Нужна проверка',sent:'Отправлен',skipped:'Пропущен',queued:'В очереди',running:'Выполняется',done:'Завершено',failed:'Ошибка',cancelled:'Остановлено',search:'Поиск',auto_comment:'Автоотправка',connect_account:'Подключение аккаунта',check_session:'Проверка аккаунта',test_proxy:'Проверка прокси',system:'Приложение',tasks:'Задачи',drafts:'Ответы',accounts:'Аккаунты',INFO:'Событие',WARNING:'Внимание',ERROR:'Ошибка'};
const badge = (s, custom) => `<span class="badge ${['active','relevant','replied','sent','done'].includes(s)?'good':['review','warning','WARNING'].includes(s)?'warn':['banned','failed','ERROR'].includes(s)?'bad':''}">${esc(custom || LABEL[s] || s)}</span>`;
const empty = (title, text, glyph='search', action='', compact=false) => `<div class="empty ${compact?'compact':''}">${icon(glyph)}<h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;
const button = (label, action, glyph='plus', primary=true) => `<button class="button ${primary?'primary':'secondary'}" data-action="${action}">${icon(glyph)}${label}</button>`;
const goto = (label, tab, primary=false) => `<button class="${primary?'button primary':'text-button'}" data-goto="${tab}">${label}${icon('arrow-right')}</button>`;

async function api(path, {method='GET',body}={}) {
  const response = await fetch(path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if (!response.ok) {
    let message = 'Не удалось выполнить действие. Попробуйте ещё раз.';
    try {const data=await response.json();message=typeof data.detail==='string'?data.detail:(data.detail||[]).map(x=>x.msg).join('; ')||message;}catch{}
    throw new Error(message);
  }
  return response.status===204?null:response.json();
}
let toastTimer;
function toast(text,error=false) {const el=$('#toast');el.textContent=text;el.className=`toast ${error?'error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.add('hidden'),4500);}
function formError(id,error) {const el=$(`#${id}-error`);el.textContent=error?.message||error||'';el.classList.toggle('hidden',!error);}
const safe = fn => async (...args) => {try{await fn(...args);}catch(error){toast(error.message,true);}};
async function formAction(form, errorId, action) {
  formError(errorId,null);
  const buttons=$$('button',form);const states=buttons.map(b=>b.disabled);buttons.forEach(b=>b.disabled=true);
  try {await action();}catch(error){formError(errorId,error);}finally{buttons.forEach((b,i)=>b.disabled=states[i]);}
}
function download(url) {const a=document.createElement('a');a.href=url;a.download='';document.body.append(a);a.click();a.remove();}
function dialog(id) {const el=$(`#${id}`);if(!el.open)el.showModal();}
function closeDialog(id) {$(`#${id}`).close();}

let themeChoice='system';try{themeChoice=localStorage.getItem('ttlh-theme')||'system';}catch{}
const colorMedia=window.matchMedia('(prefers-color-scheme: dark)');
function setTheme(choice) {
  themeChoice=choice;
  document.documentElement.dataset.theme=choice==='system'?(colorMedia.matches?'dark':'light'):choice;
  try{localStorage.setItem('ttlh-theme',choice);}catch{}
  $$('[data-theme-choice]').forEach(b=>{b.classList.toggle('active',b.dataset.themeChoice===choice);b.setAttribute('aria-pressed',String(b.dataset.themeChoice===choice));});
  $('#theme-toggle').innerHTML=icon(document.documentElement.dataset.theme==='dark'?'sun':'moon');
}
colorMedia.addEventListener('change',()=>{if(themeChoice==='system')setTheme('system');});
$('#theme-toggle').addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
setTheme(themeChoice);

const PAGES={dashboard:['Обзор','Поиск, отбор и ответы. Всё необходимое для текущей работы.'],accounts:['Аккаунты','Подключения, прокси и дневные лимиты.'],search:['Поиск роликов','Найдите авторов, которым может быть интересно ваше предложение.'],videos:['Ролики','Отберите подходящие ролики и подготовьте ответы.'],drafts:['Очередь ответов','Проверьте текст и отправьте комментарии с нужного аккаунта.'],templates:['Шаблоны','Готовые тексты с вариантами и персональными обращениями.'],logs:['Журнал','История действий и сообщения об ошибках.'],settings:['Настройки','Внешний вид панели и управление данными.']};
let currentTab='dashboard', accounts=[], templates=[], stats={}, allTasks=[], draftItems=[], selectedDraft=null, autoTask=null, connectionTask=null;
let videoOffset=0, pickedVideos=new Set(), detailDirty=false, manualMode=false, pollBusy=false, navVersion=0, queueNeedsRefresh=false;
let lastReplyAccount=null;try{lastReplyAccount=Number(localStorage.getItem('ttlh-last-account'))||null;}catch{}
const loaders={};
const readyAccounts=()=>accounts.filter(a=>a.session_ready&&a.status==='active');
const selectedAccount=()=>readyAccounts().find(a=>a.selected)||readyAccounts()[0];
function options(list, selected, placeholder='Выберите аккаунт') {return list.length?list.map(a=>`<option value="${a.id}" ${a.id===Number(selected)?'selected':''}>@${esc(a.username)} (${a.replies_today}/${a.daily_reply_limit})</option>`).join(''):`<option value="">${placeholder}</option>`;}
function templateOptions(selected,includeNone=false) {return (includeNone?'<option value="">Только найти ролики</option>':'')+templates.map(t=>`<option value="${t.id}" ${t.id===Number(selected)?'selected':''}>${esc(t.name)}</option>`).join('')+(!templates.length&&!includeNone?'<option value="">Сначала создайте шаблон</option>':'');}
function renderHeaderActions() {
  const actions={dashboard:selectedAccount()?goto('Новый поиск','search',true):'',accounts:button('Добавить аккаунт','connect'),search:'',videos:goto('Открыть очередь','drafts'),drafts:button('Автоотправка','auto-dialog','player-play'),templates:button('Создать шаблон','template-new'),logs:'',settings:''};
  $('#page-actions').innerHTML=actions[currentTab];
}
async function showTab(tab,quiet=false) {
  if(!PAGES[tab])tab='dashboard';
  const version=++navVersion;currentTab=tab;
  $$('[data-panel]').forEach(p=>p.classList.toggle('hidden',p.dataset.panel!==tab));
  $$('.tab-btn[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-current',b.dataset.tab===tab?'page':'false');b.setAttribute('aria-label',PAGES[b.dataset.tab][0]);b.title=PAGES[b.dataset.tab][0];});
  $('#page-title').textContent=PAGES[tab][0];$('#breadcrumb-current').textContent=PAGES[tab][0];$('#page-sub').textContent=PAGES[tab][1];
  renderHeaderActions();$('#page-error').classList.add('hidden');
  try{localStorage.setItem('ttlh-tab',tab);}catch{}
  if(location.hash!==`#${tab}`)history.replaceState(null,'',`#${tab}`);
  if(!quiet){document.body.classList.add('is-loading');$('#page-loading').classList.remove('hidden');}
  try{await loaders[tab]();}catch(error){$('#page-error').textContent=error.message;$('#page-error').classList.remove('hidden');}
  finally{if(version===navVersion){document.body.classList.remove('is-loading');$('#page-loading').classList.add('hidden');}}
}
async function globalState() {
  [stats,accounts]=await Promise.all([api('/api/stats'),api('/api/accounts')]);
  $('#nav-accounts').textContent=accounts.filter(a=>a.session_ready).length||'';$('#nav-videos').textContent=stats.videos_total||'';
  $('#badge-drafts').textContent=stats.drafts_pending;$('#badge-drafts').classList.toggle('hidden',!stats.drafts_pending);
  const account=selectedAccount();
  $('#sidebar-account').innerHTML=account?`<span class="avatar">${initials(account.username)}</span><div class="truncate">@${esc(account.username)}<small>Аккаунт для поиска</small></div>`:`${icon('shield-check')}<div>Нет подключения<small>Добавьте аккаунт</small></div>`;
  $('#service-state').innerHTML='<span></span>Панель работает';
}
function friendlyMessage(value) {const text=String(value||'').replaceAll('в «Настройках»','во вкладке «Аккаунты»');if(/net::|Call log:|Page\.|Locator\.|Target page, context/.test(text))return 'Не удалось выполнить действие в TikTok. Проверьте аккаунт и прокси. Подробности в журнале.';return text.length>180?text.slice(0,177)+'…':text;}
function taskRows(tasks,compact=false) {return tasks.length?tasks.map(t=>`<div class="task-row"><span class="task-icon">${icon(t.kind==='search'?'search':t.kind==='connect_account'?'users':'message-circle')}</span><div class="task-content"><h3>${esc(t.kind==='search'?(t.params.queries||[]).join(', '):(LABEL[t.kind]||t.kind))}</h3><p>${esc(friendlyMessage(t.error||t.result||(t.status==='queued'?'Ожидает своей очереди':'Выполняется…')))}</p><time>${date(t.created_at)}</time></div>${badge(t.status)}</div>`).join(''):empty('Поисков пока нет','Начните поиск, чтобы собрать первые ролики.','search','',true);}
function activities(logs) {return logs.length?logs.map(l=>`<div class="activity-row"><p>${esc(friendlyMessage(l.message))}</p><time>${date(l.ts)}</time></div>`).join(''):empty('Событий пока нет','Здесь появится история вашей работы.','history','',true);}

loaders.dashboard=async()=>{
  const [,tasks,logs]=await Promise.all([globalState(),api('/api/tasks?limit=20'),api('/api/logs?limit=5')]);allTasks=tasks;
  const account=selectedAccount();let next;
  if(!account)next={title:'Начнём с подключения аккаунта',text:'Добавьте прокси и войдите в TikTok. После авторизации аккаунт будет готов к поиску и отправке.',action:button('Добавить аккаунт','connect'),stage:0};
  else if(stats.drafts_pending)next={title:`${num(stats.drafts_pending)} ответов ждут обработки`,text:'Откройте очередь, проверьте тексты и выберите аккаунт. Ответы можно отправлять по одному или автоматически.',action:goto('Открыть очередь','drafts',true),stage:2};
  else if(stats.videos_by_status?.relevant)next={title:'Подготовьте ответы для подходящих роликов',text:'Выберите шаблон в роликах и создайте черновики. Каждый ответ можно отредактировать перед отправкой.',action:goto('Подготовить ответы','videos',true),stage:1};
  else if(stats.videos_total)next={title:'Найдите подходящие ролики',text:'Отметьте интересных авторов в результатах поиска. Для отобранных роликов можно подготовить ответы.',action:goto('Отобрать ролики','videos',true),stage:1};
  else next={title:'Найдите первых авторов',text:'Укажите хэштеги или ключевые слова. Ролики будут собраны в одном месте для дальнейшего отбора.',action:goto('Начать поиск','search',true),stage:0};
  const metrics=[['Найдено роликов',stats.videos_total,'По всем запросам'],['Подходят',stats.videos_by_status?.relevant||0,'Готовы к подготовке ответов'],['В очереди',stats.drafts_pending,'Черновики и публикации на проверке'],['Отправлено сегодня',stats.replies_sent_today,`Всего ${num(stats.replies_sent_total)} ответов`]];
  $('#overview').innerHTML=`<div class="overview-stats">${metrics.map(([title,value,hint])=>`<div class="overview-stat"><small>${title}</small><strong>${num(value)}</strong><span>${hint}</span></div>`).join('')}</div>
    <div class="overview-primary"><div class="surface continue-panel"><div class="step-visual"><span class="${next.stage>=0?'done':''}">Поиск</span>${icon('chevron-right')}<span class="${next.stage>=1?'done':''}">Отбор роликов</span>${icon('chevron-right')}<span class="${next.stage>=2?'done':''}">Отправка ответов</span></div><h2>${next.title}</h2><p>${next.text}</p>${next.action}</div><div class="surface account-summary"><div class="section-heading"><h3>Аккаунт для поиска</h3>${badge(account?'active':'paused',account?'Готов к работе':'Не подключён')}</div>${account?`<div class="account-large"><span class="avatar">${initials(account.username)}</span><div><strong>@${esc(account.username)}</strong><small>${esc(account.display_name||'TikTok')}</small></div></div><div class="summary-line"><span>Сегодня / лимит</span><strong>${account.replies_today} / ${account.daily_reply_limit}</strong></div><div class="summary-line"><span>Прокси</span><span class="mono truncate">${esc(proxyHost(account.proxy_label))}</span></div>${goto('Управлять аккаунтами','accounts')}`:`<p class="muted">Для каждого аккаунта сохраняются отдельные прокси и вход в TikTok.</p>${goto('К аккаунтам','accounts')}`}</div></div>
    <div class="overview-bottom"><div class="surface"><div class="section-heading"><h2>Последние поиски</h2>${goto('Все поиски','search')}</div>${taskRows(tasks.filter(t=>t.kind==='search').slice(0,3),true)}</div><div class="surface"><div class="section-heading"><h2>Последние события</h2>${goto('Журнал','logs')}</div>${activities(logs)}</div></div>`;
};
function proxyHost(label) {if(!label)return 'Прокси не настроен';try{return new URL(label).host;}catch{return label;}}
loaders.accounts=async()=>{
  await globalState();
  const latest=await api('/api/accounts/connection/latest');if(latest&&['queued','running'].includes(latest.status))connectionTask=latest;
  $('#accounts-intro').innerHTML=connectionTask&&['queued','running'].includes(connectionTask.status)?`<div class="accounts-intro">${icon('users')}<div><h3>Подключение выполняется</h3><p>${esc(connectionTask.result||'Проверяем прокси и открываем браузер…')}</p></div><button class="text-button push-right" data-action="connect">Продолжить вход</button></div>`:`<div class="accounts-intro">${icon('shield-check')}<div><h3>У каждого аккаунта своё подключение</h3><p>Сначала прокси, затем вход в TikTok. В список попадают только подтверждённые аккаунты.</p></div></div>`;
  const row=a=>`<div class="account-row" data-account-id="${a.id}"><div class="account-identity"><span class="avatar">${initials(a.username)}</span><div class="truncate"><strong>@${esc(a.username)}</strong><small>${esc(a.display_name|| (a.authenticated_at?`Подключён ${date(a.authenticated_at)}`:'Ранее добавлен вручную'))}</small></div></div><div class="account-proxy"><span class="mono">${esc(proxyHost(a.proxy_label))}</span><small>${a.session_ready?badge(a.status,a.status==='active'?'Вход подтверждён':LABEL[a.status]):badge('review','Нужен вход через прокси')}</small></div><div class="account-quota">${a.replies_today} <span class="subtle">/ ${a.daily_reply_limit}</span><small>Ответов сегодня</small></div><div class="account-tools">${a.session_ready?(a.selected?badge('active','Для поиска'):`<button class="button secondary small" data-account-action="select">Для поиска</button>`)+`<button class="icon-button" data-account-action="check" title="Проверить вход" aria-label="Проверить вход">${icon('refresh')}</button>`:`<button class="button secondary small" data-action="connect">Подключить</button>`}<button class="icon-button" data-account-action="edit" title="Настройки аккаунта" aria-label="Настройки аккаунта">${icon('settings')}</button></div></div>`;
  const confirmed=accounts.filter(a=>a.session_ready),legacy=accounts.filter(a=>!a.session_ready);
  $('#accounts-list').innerHTML=(confirmed.length?confirmed.map(row).join(''):empty('Подключите первый аккаунт','Добавьте прокси и войдите в TikTok. Аккаунт появится здесь после подтверждения авторизации.','users'))+(legacy.length?`<details class="legacy-accounts"><summary>Требуют подключения (${legacy.length})</summary><p class="field-help">История и настройки сохранены. Для работы подключите эти аккаунты через прокси.</p>${legacy.map(row).join('')}</details>`:'');
};
function openConnect() {
  formError('connect',null);
  if(connectionTask&&['queued','running'].includes(connectionTask.status))renderConnection(connectionTask);
  else{showProxyStep();$('#connect-sessionid').value='';}
  dialog('connect-dialog');
}
function showProxyStep() {
  $('#connect-form').classList.remove('hidden');$('#session-form').classList.add('hidden');$('#connect-progress').classList.add('hidden');
  $('#connect-steps [data-step="login"]').textContent='sessionid';
  $$('[data-step]').forEach(el=>el.classList.toggle('current',el.dataset.step==='proxy'));
}
function showSessionStep() {
  formError('session',null);$('#connect-form').classList.add('hidden');$('#session-form').classList.remove('hidden');$('#connect-progress').classList.add('hidden');
  $('#connect-steps [data-step="login"]').textContent='sessionid';
  $$('[data-step]').forEach(el=>el.classList.toggle('current',el.dataset.step==='login'));
}
function renderConnection(task) {
  const done=task.status==='done',failed=['failed','cancelled'].includes(task.status),login=(task.result||'').includes('Войдите'),imported=task.params.method==='sessionid';
  if(failed){showSessionStep();formError('session',task.error||'Подключение отменено. Аккаунт не добавлен.');return;}
  $('#connect-form').classList.add('hidden');$('#session-form').classList.add('hidden');$('#connect-progress').classList.remove('hidden');
  $('#connect-steps [data-step="login"]').textContent=imported?'sessionid':'Авторизация';
  $$('[data-step]').forEach(el=>el.classList.toggle('current',el.dataset.step===(done?'done':login||(imported&&(task.result||'').includes('sessionid'))?'login':'proxy')));
  $('#connect-progress-content').innerHTML=`<div class="connection-stage"><div class="stage-icon">${icon(done?'shield-check':login?'brand-tiktok':'world')}</div><h3>${done?'Аккаунт подключён':login?'Войдите в TikTok':imported?'Проверяем подключение':'Подготавливаем подключение'}</h3><p>${esc(task.result||'Задача ожидает свободного браузера…')}</p>${done?'':`<p class="stage-note">${login?'Войдите по телефону или почте. Вход через Google может быть недоступен в управляемом браузере.':'Аккаунт появится в панели только после подтверждения TikTok.'}</p>`}</div>`;
  $('#connect-cancel').classList.toggle('hidden',done);$('#connect-cancel').disabled=!!task.params.stop_requested;
  $('#connect-done').classList.toggle('hidden',!done);
}
async function submitConnection(sessionid) {const f=new FormData($('#connect-form'));const body={proxy:f.get('proxy').trim(),scheme:f.get('scheme'),use_system_chrome:f.get('browser')==='chrome'};if(sessionid!==undefined)body.sessionid=sessionid.trim();connectionTask=await api('/api/accounts/connect',{method:'POST',body});renderConnection(connectionTask);}
$('#connect-form').addEventListener('submit',e=>{e.preventDefault();showSessionStep();});
$('#session-back').addEventListener('click',showProxyStep);
$('#session-form').addEventListener('submit',async e=>{e.preventDefault();await formAction(e.target,'session',()=>submitConnection($('#connect-sessionid').value));});
$('#connect-browser-login').addEventListener('click',async()=>{await formAction($('#session-form'),'session',()=>submitConnection());});
$('#session-reveal').addEventListener('click',()=>{const field=$('#connect-sessionid'),button=$('#session-reveal'),reveal=field.type==='password';field.type=reveal?'text':'password';button.textContent=reveal?'Скрыть':'Показать';button.setAttribute('aria-pressed',String(reveal));});
$('#connect-cancel').addEventListener('click',safe(async()=>{if(connectionTask){connectionTask=await api(`/api/tasks/${connectionTask.id}/stop`,{method:'POST'});renderConnection(connectionTask);}}));
$('#accounts-list').addEventListener('click',safe(async e=>{
  const action=e.target.closest('[data-account-action]')?.dataset.accountAction;if(!action)return;
  const account=accounts.find(a=>a.id===Number(e.target.closest('[data-account-id]').dataset.accountId));
  if(action==='select'){await api(`/api/accounts/${account.id}/select`,{method:'POST'});await loaders.accounts();toast('Аккаунт выбран для поиска');}
  if(action==='check'){await api(`/api/accounts/${account.id}/check`,{method:'POST'});toast('Проверяем аккаунт через его прокси');}
  if(action==='edit'){const f=$('#account-edit-form');for(const k of ['id','display_name','daily_reply_limit','status','notes'])f.elements[k].value=account[k]??'';$('#account-title').textContent=`@${account.username}`;formError('account',null);dialog('account-dialog');}
}));
$('#account-edit-form').addEventListener('submit',async e=>{e.preventDefault();await formAction(e.target,'account',async()=>{const f=new FormData(e.target);await api(`/api/accounts/${f.get('id')}`,{method:'PATCH',body:{display_name:f.get('display_name').trim()||null,daily_reply_limit:Number(f.get('daily_reply_limit')),status:f.get('status'),notes:f.get('notes').trim()||null}});closeDialog('account-dialog');await loaders.accounts();toast('Настройки аккаунта сохранены');});});
$('#account-delete').addEventListener('click',safe(async()=>{const id=$('#account-edit-form').elements.id.value;if(!confirm('Удалить аккаунт и его сохранённый вход? История ответов останется.'))return;try{await api(`/api/accounts/${id}`,{method:'DELETE'});closeDialog('account-dialog');await loaders.accounts();}catch(error){formError('account',error);}}));

loaders.search=async()=>{
  const [,tpl,tasks]=await Promise.all([globalState(),api('/api/templates'),api('/api/tasks?limit=100')]);templates=tpl;allTasks=tasks;
  const current=$('#search-account').value;$('#search-account').innerHTML=options(readyAccounts(),current||selectedAccount()?.id,'Сначала подключите аккаунт');
  const selected=$('#search-template').value;$('#search-template').innerHTML=templateOptions(selected,true);
  $('#search-tasks').innerHTML=taskRows(tasks.filter(t=>t.kind==='search').slice(0,5));
};
$('#search-form').addEventListener('submit',async e=>{e.preventDefault();await formAction(e.target,'search',async()=>{const f=new FormData(e.target);const queries=f.get('queries').split('\n').map(s=>s.trim()).filter(Boolean);if(!queries.length||queries.length>20)throw new Error('Укажите от 1 до 20 запросов, каждый с новой строки');await api('/api/tasks/search',{method:'POST',body:{queries,account_id:Number(f.get('account_id')),limit_per_query:Number(f.get('limit')),template_id:Number(f.get('template'))||null,visible:f.get('visible')==='on'}});toast('Поиск поставлен в очередь');await loaders.search();});});

function videoFilters() {const p=new URLSearchParams();for(const [id,key] of [['v-status','status'],['v-query','query'],['v-q','q']])if($(`#${id}`).value.trim())p.set(key,$(`#${id}`).value.trim());return p;}
function updateSelection(){const n=pickedVideos.size;$('#selection-count').textContent=`Выбрано: ${n}`;$('#video-selection').classList.toggle('hidden',!n);$('#v-all').checked=!!$$('[data-pick]').length&&$$('[data-pick]').every(c=>c.checked);}
loaders.videos=async()=>{
  const filters=videoFilters();filters.set('limit','30');filters.set('offset',videoOffset);
  const [page,queries,tpl]=await Promise.all([api(`/api/videos?${filters}`),api('/api/videos/queries'),api('/api/templates')]);templates=tpl;
  const selected=$('#v-query').value;$('#v-query').innerHTML='<option value="">Все запросы</option>'+queries.map(q=>`<option value="${esc(q)}">${esc(q)}</option>`).join('');$('#v-query').value=queries.includes(selected)?selected:'';
  $('#v-template').innerHTML=templateOptions($('#v-template').value);
  $('#videos-body').innerHTML=page.items.map(v=>`<tr data-id="${v.id}"><td class="check-cell"><input type="checkbox" data-pick="${v.id}" aria-label="Выбрать ролик @${esc(v.author)}" ${pickedVideos.has(v.id)?'checked':''}></td><td><div class="video-title"><span class="avatar">${initials(v.author)}</span><a href="${esc(v.url)}" target="_blank" rel="noopener">@${esc(v.author)}</a></div><details class="video-desc"><summary>${esc(v.description||'Описание отсутствует')}</summary></details><span class="video-query">${esc(v.query)}</span></td><td class="mono">${num(v.views)}</td><td>${badge(v.status)}</td><td><div class="table-actions"><button class="icon-button" data-video-action="relevant" aria-label="Подходит" title="Подходит">${icon('check')}</button><button class="icon-button" data-video-action="irrelevant" aria-label="Не подходит" title="Не подходит">${icon('x')}</button><button class="icon-button" data-video-action="draft" aria-label="Создать черновик" title="Создать черновик">${icon('message-circle')}</button><a class="icon-button" href="${esc(v.url)}" target="_blank" rel="noopener" aria-label="Открыть ролик" title="Открыть ролик">${icon('external-link')}</a></div></td></tr>`).join('');
  $('#videos-empty').innerHTML=page.items.length?'':empty('Ролики не найдены',stats.videos_total?'Измените фильтры или запустите новый поиск.':'Начните поиск по хэштегам или ключевым словам.','video',goto('Начать поиск','search',true));
  $('#v-count').textContent=page.total?`${videoOffset+1}-${Math.min(videoOffset+30,page.total)} из ${num(page.total)} роликов`:'0 роликов';$('#v-prev').disabled=videoOffset===0;$('#v-next').disabled=videoOffset+30>=page.total;updateSelection();
};
let videoSearchTimer;$('#v-q').addEventListener('input',()=>{clearTimeout(videoSearchTimer);videoSearchTimer=setTimeout(()=>{videoOffset=0;pickedVideos.clear();safe(loaders.videos)();},300);});
for(const id of ['v-status','v-query'])$(`#${id}`).addEventListener('change',safe(async()=>{videoOffset=0;pickedVideos.clear();await loaders.videos();}));
$('#v-prev').addEventListener('click',safe(async()=>{videoOffset=Math.max(0,videoOffset-30);await loaders.videos();}));$('#v-next').addEventListener('click',safe(async()=>{videoOffset+=30;await loaders.videos();}));
$('#v-all').addEventListener('change',e=>{$$('[data-pick]').forEach(c=>{c.checked=e.target.checked;e.target.checked?pickedVideos.add(Number(c.dataset.pick)):pickedVideos.delete(Number(c.dataset.pick));});updateSelection();});
$('#videos-body').addEventListener('change',e=>{if(e.target.matches('[data-pick]')){e.target.checked?pickedVideos.add(Number(e.target.dataset.pick)):pickedVideos.delete(Number(e.target.dataset.pick));updateSelection();}});
$('#clear-selection').addEventListener('click',()=>{pickedVideos.clear();$$('[data-pick]').forEach(c=>c.checked=false);updateSelection();});
$('#videos-body').addEventListener('click',safe(async e=>{const action=e.target.closest('[data-video-action]')?.dataset.videoAction;if(!action)return;const id=Number(e.target.closest('tr').dataset.id);if(action==='draft'){const template=Number($('#v-template').value);if(!template)throw new Error('Создайте шаблон во вкладке «Шаблоны»');await api('/api/drafts',{method:'POST',body:{video_id:id,template_id:template}});await api(`/api/videos/${id}`,{method:'PATCH',body:{status:'relevant'}});toast('Черновик добавлен в очередь');await globalState();}else await api(`/api/videos/${id}`,{method:'PATCH',body:{status:action}});await loaders.videos();}));
$$('[data-bulk]').forEach(b=>b.addEventListener('click',safe(async()=>{await api('/api/videos/bulk-status',{method:'POST',body:{ids:[...pickedVideos],status:b.dataset.bulk}});pickedVideos.clear();await loaders.videos();})));
$('#btn-generate').addEventListener('click',safe(async()=>{const template=Number($('#v-template').value);if(!template)throw new Error('Сначала создайте шаблон ответа');const body={template_id:template};if(pickedVideos.size)body.video_ids=[...pickedVideos];const r=await api('/api/drafts/generate',{method:'POST',body});toast(`Создано черновиков: ${r.created}`);await globalState();}));
$('#v-export').addEventListener('click',()=>download(`/api/export/leads.csv?${videoFilters()}`));

loaders.templates=async()=>{templates=await api('/api/templates');$('#templates-list').innerHTML=templates.length?templates.map(t=>`<article class="surface template-card" data-template-id="${t.id}"><header><h3>${esc(t.name)}</h3><button class="icon-button" data-template-action="edit" title="Редактировать" aria-label="Редактировать">${icon('edit')}</button></header><p class="template-body">${esc(t.body)}</p><footer><span>Создан ${date(t.created_at)}</span><button class="text-button text-danger" data-template-action="delete">Удалить</button></footer></article>`).join(''):empty('Создайте первый шаблон','Добавьте текст ответа и варианты приветствия. Шаблон поможет быстрее подготовить комментарии.','template');};
function openTemplate(template) {const f=$('#template-form');f.reset();f.elements.id.value=template?.id||'';f.elements.name.value=template?.name||'';f.elements.body.value=template?.body||'';$('#template-title').textContent=template?'Редактировать шаблон':'Новый шаблон';$('#preview-box').classList.add('hidden');formError('template',null);dialog('template-dialog');}
$('#templates-list').addEventListener('click',safe(async e=>{const a=e.target.closest('[data-template-action]')?.dataset.templateAction;if(!a)return;const t=templates.find(t=>t.id===Number(e.target.closest('[data-template-id]').dataset.templateId));if(a==='edit')openTemplate(t);else if(confirm(`Удалить шаблон «${t.name}»?`)){await api(`/api/templates/${t.id}`,{method:'DELETE'});await loaders.templates();}}));
$('#template-form').addEventListener('submit',async e=>{e.preventDefault();await formAction(e.target,'template',async()=>{const f=new FormData(e.target),id=f.get('id');await api(id?`/api/templates/${id}`:'/api/templates',{method:id?'PUT':'POST',body:{name:f.get('name').trim(),body:f.get('body').trim()}});closeDialog('template-dialog');await loaders.templates();toast('Шаблон сохранён');});});
$$('[data-insert]').forEach(b=>b.addEventListener('click',()=>{const field=$('#template-body');field.setRangeText(b.dataset.insert,field.selectionStart,field.selectionEnd,'end');field.focus();}));
$('#btn-preview').addEventListener('click',safe(async()=>{const body=$('#template-body').value.trim();if(!body)return;const result=await api('/api/templates/preview',{method:'POST',body:{body,count:3}});$('#preview-box').innerHTML=`<small>Возможных сочетаний: ${num(result.total_combinations)}</small>${result.variants.map(s=>`<p>${esc(s)}</p>`).join('')}`;$('#preview-box').classList.remove('hidden');}));

function renderAutoTask(task) {
  autoTask=task;const el=$('#auto-status');el.classList.toggle('hidden',!task);if(!task)return;
  const busy=['queued','running'].includes(task.status);el.className=`notice ${task.error?'danger':''}`;
  el.innerHTML=`${icon(task.error?'alert-circle':'message-circle')}<div>${badge(task.status)} <span>${esc(task.error||task.result||'Ожидает запуска')}</span><small class="subtle"> · подтверждено ${task.found} из ${task.params.draft_ids?.length||0}</small></div>${busy?`<button id="auto-stop" class="button secondary small push-right" ${task.params.stop_requested?'disabled':''}>${task.params.stop_requested?'Останавливается…':'Остановить'}</button>`:task.status==='failed'?`<button class="text-button push-right" data-action="review">Проверить публикации</button>`:''}`;
}
async function startAuto(body) {const t=await api('/api/tasks/auto-comment',{method:'POST',body});renderAutoTask(t);closeDialog('auto-dialog');$('#d-status').value='posting';detailDirty=false;await loaders.drafts();toast('Автоотправка поставлена в очередь');}
async function openAuto() {await globalState();$('#auto-account').innerHTML=options(readyAccounts(),selectedAccount()?.id,'Подключите аккаунт через прокси');formError('auto',null);$('#auto-start').disabled=!!autoTask&&['queued','running'].includes(autoTask.status);dialog('auto-dialog');}
$('#auto-form').addEventListener('submit',async e=>{e.preventDefault();await formAction(e.target,'auto',async()=>{const f=new FormData(e.target);await startAuto({account_id:Number(f.get('account_id')),limit:Number(f.get('limit')),interval_seconds:Number(f.get('interval_seconds')),visible:f.get('visible')==='on'});});});
$('#auto-status').addEventListener('click',safe(async e=>{if(e.target.closest('#auto-stop')&&autoTask)renderAutoTask(await api(`/api/tasks/${autoTask.id}/stop`,{method:'POST'}));}));
function renderDraftList() {
  $('#queue-count').textContent=`${draftItems.length}${draftItems.length===500?'+':''} ответов`;
  $('#drafts-list').innerHTML=draftItems.map(d=>`<button class="draft-row ${d.id===selectedDraft?'selected':''}" data-id="${d.id}" aria-pressed="${d.id===selectedDraft}"><span class="row-top"><span>@${esc(d.video_author)}</span>${badge(d.status)}</span><p>${esc(d.text)}</p><small>${d.account_username?'@'+esc(d.account_username):'Аккаунт ещё не выбран'}</small></button>`).join('');
}
function renderDraftDetail() {
  const d=draftItems.find(d=>d.id===selectedDraft);detailDirty=false;
  if(!d){$('#draft-detail').innerHTML=empty('Здесь пока пусто',$('#d-status').value==='draft'?'Подготовьте черновики из подходящих роликов.':'В выбранной категории нет ответов.','message-circle',$('#d-status').value==='draft'?goto('К роликам','videos',true):'');return;}
  const editable=d.status==='draft',account=d.account_id||(accounts.some(a=>a.id===lastReplyAccount&&a.status==='active')?lastReplyAccount:selectedAccount()?.id);
  const list=accounts.filter(a=>a.status==='active'||a.id===d.account_id);
  $('#draft-detail').innerHTML=`<div data-id="${d.id}"><div class="detail-heading"><div><strong>@${esc(d.video_author)}</strong><p>Черновик #${d.id}${d.sent_at?' · '+date(d.sent_at):''}</p></div><a class="icon-button" href="${esc(d.video_url)}" target="_blank" rel="noopener" title="Открыть ролик" aria-label="Открыть ролик">${icon('external-link')}</a></div>${d.video_description?`<div class="detail-description">${esc(d.video_description)}</div>`:''}<label for="draft-text">Текст комментария</label><textarea id="draft-text" data-text rows="5" ${editable?'':'disabled'}>${esc(d.text)}</textarea><label for="draft-account">Аккаунт для ответа</label><select id="draft-account" data-account ${editable?'':'disabled'}><option value="">Выберите аккаунт</option>${options(list,account)}</select><div id="draft-error" class="form-error hidden" role="alert"></div>${editable?`<div class="detail-actions"><button class="button primary" data-draft-action="auto" data-act="auto" ${autoTask&&['queued','running'].includes(autoTask.status)?'disabled':''}>${icon('player-play')}Отправить</button><button class="button secondary" data-draft-action="save">Сохранить</button><button class="text-button" data-draft-action="manual">${manualMode?'Скрыть ручной режим':'Вручную'}</button></div><div class="manual-controls ${manualMode?'':'hidden'}"><div class="button-group"><button class="button secondary small" data-draft-action="open">${icon('copy')}Открыть и скопировать</button><button class="button secondary small" data-draft-action="sent">Отметить отправленным</button><button class="text-button" data-draft-action="skip">Пропустить</button></div><p>После публикации отметьте результат. Enter: отправлено и следующий. S: пропустить. O: открыть. Esc: выйти.</p></div><div class="detail-actions"><button class="text-button text-danger" data-draft-action="delete">Удалить черновик</button></div>`:''}${d.status==='review'?`<div class="review-controls"><p>Ответ TikTok не получен. Откройте ролик и проверьте, появился ли комментарий. Автоматического повтора не будет.</p><div class="button-group"><button class="button secondary small" data-draft-action="confirm-published" data-act="confirm-published">Комментарий опубликован</button><button class="button secondary small" data-draft-action="confirm-absent" data-act="confirm-absent">Комментария нет</button></div></div>`:''}</div>`;
}
loaders.drafts=async()=>{
  const [items,,task]=await Promise.all([api(`/api/drafts?status=${$('#d-status').value}&limit=500`),globalState(),api('/api/tasks/auto-comment')]);draftItems=items;renderAutoTask(task);
  if(!draftItems.some(d=>d.id===selectedDraft))selectedDraft=draftItems[0]?.id||null;
  renderDraftList();renderDraftDetail();
};
$('#d-status').addEventListener('change',safe(async()=>{if(detailDirty)await syncDraft();selectedDraft=null;await loaders.drafts();}));
$('#drafts-list').addEventListener('click',safe(async e=>{const row=e.target.closest('.draft-row');if(!row)return;if(detailDirty)await syncDraft();selectedDraft=Number(row.dataset.id);renderDraftList();renderDraftDetail();}));
$('#draft-detail').addEventListener('input',()=>{detailDirty=true;});$('#draft-detail').addEventListener('change',()=>{detailDirty=true;});
async function syncDraft() {const d=draftItems.find(d=>d.id===selectedDraft);if(!d||d.status!=='draft')return d;const text=$('#draft-text').value.trim(),accountId=Number($('#draft-account').value)||null;if(!text)throw new Error('Текст комментария не может быть пустым');if(detailDirty||accountId!==d.account_id)Object.assign(d,await api(`/api/drafts/${d.id}`,{method:'PATCH',body:{text,account_id:accountId}}));if(accountId){lastReplyAccount=accountId;try{localStorage.setItem('ttlh-last-account',String(accountId));}catch{}}detailDirty=false;return d;}
let draftActionBusy=false;
async function draftAction(action) {
  if(draftActionBusy)return;draftActionBusy=true;formError('draft',null);
  const d=draftItems.find(d=>d.id===selectedDraft);if(!d){draftActionBusy=false;return;}
  const controls=$$('button',$('#draft-detail'));controls.forEach(b=>b.disabled=true);
  try {
    if(action==='manual'){await syncDraft();manualMode=!manualMode;renderDraftDetail();return;}
    if(action==='open'){await navigator.clipboard.writeText($('#draft-text').value);window.open(d.video_url,'ttlh-video');toast('Текст скопирован, ролик открыт');return;}
    if(action==='save'){await syncDraft();renderDraftList();toast('Черновик сохранён');return;}
    if(action==='auto'){const saved=await syncDraft();if(!saved.account_id)throw new Error('Выберите аккаунт для отправки');const account=accounts.find(a=>a.id===saved.account_id);if(!account?.session_ready)throw new Error('Сначала подключите этот аккаунт через прокси');await startAuto({account_id:saved.account_id,draft_ids:[saved.id],interval_seconds:60,visible:true});return;}
    if(action==='confirm-published'||action==='confirm-absent')await api(`/api/drafts/${d.id}/resolve`,{method:'POST',body:{published:action==='confirm-published'}});
    if(action==='sent'){await syncDraft();await api(`/api/drafts/${d.id}/sent`,{method:'POST'});toast('Отмечено как отправленное');}
    if(action==='skip')await api(`/api/drafts/${d.id}/skip`,{method:'POST'});
    if(action==='delete'){if(!confirm('Удалить этот черновик?'))return;await api(`/api/drafts/${d.id}`,{method:'DELETE'});}
    detailDirty=false;await loaders.drafts();
    if(manualMode&&['sent','skip'].includes(action)&&draftItems.length){const next=draftItems.find(x=>x.id===selectedDraft);if(next){await navigator.clipboard.writeText(next.text);window.open(next.video_url,'ttlh-video');}}
  }catch(error){formError('draft',error);}finally{draftActionBusy=false;controls.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
}
$('#draft-detail').addEventListener('click',e=>{const action=e.target.closest('[data-draft-action]')?.dataset.draftAction;if(action)draftAction(action);});
document.addEventListener('keydown',e=>{if(currentTab!=='drafts'||!manualMode||$('dialog[open]')||e.altKey||e.metaKey)return;const input=e.target.matches('input,textarea,select');if(e.key==='Escape'){if(input){e.target.blur();return;}manualMode=false;safe(async()=>{if(detailDirty)await syncDraft();renderDraftDetail();})();return;}if(input&&!(e.ctrlKey&&e.key==='Enter'))return;const action={Enter:'sent',s:'skip',o:'open',e:'edit'}[e.key];if(!action)return;e.preventDefault();if(action==='edit')$('#draft-text')?.focus();else draftAction(action);});
$('#queue-export').addEventListener('click',()=>{formError('export',null);dialog('export-dialog');});
$('#export-form').addEventListener('submit',e=>{e.preventDefault();const f=new FormData(e.target);if(f.get('from')&&f.get('to')&&f.get('from')>f.get('to')){formError('export','Начальная дата не может быть позже конечной');return;}const p=new URLSearchParams();if(f.get('from'))p.set('date_from',f.get('from'));if(f.get('to'))p.set('date_to',f.get('to'));download(`/api/export/replies.csv?${p}`);closeDialog('export-dialog');});

loaders.logs=async()=>{const p=new URLSearchParams({limit:'100'});if($('#l-level').value)p.set('level',$('#l-level').value);if($('#l-source').value)p.set('source',$('#l-source').value);const logs=await api(`/api/logs?${p}`);$('#logs-list').innerHTML=logs.length?logs.map(l=>`<div class="log-row"><time>${date(l.ts)}</time><span>${badge(l.level)}</span><p>${esc(l.message)}</p></div>`).join(''):empty('Событий не найдено','Попробуйте выбрать другой фильтр.','history');};
for(const id of ['l-level','l-source'])$(`#${id}`).addEventListener('change',safe(loaders.logs));
loaders.settings=async()=>setTheme(themeChoice);
document.addEventListener('click',safe(async e=>{
  const close=e.target.closest('[data-close]');if(close){closeDialog(close.dataset.close);if(close.dataset.close==='connect-dialog'&&connectionTask?.status==='done')await showTab('accounts');return;}
  const tab=e.target.closest('[data-tab]')?.dataset.tab||e.target.closest('[data-goto]')?.dataset.goto;
  if(tab){e.preventDefault();if($('#more-dialog').open)closeDialog('more-dialog');if(currentTab==='drafts'&&detailDirty)await syncDraft();await showTab(tab);return;}
  const theme=e.target.closest('[data-theme-choice]')?.dataset.themeChoice;if(theme){setTheme(theme);return;}
  const action=e.target.closest('[data-action]')?.dataset.action;
  if(action==='connect')openConnect();if(action==='auto-dialog')await openAuto();if(action==='template-new')openTemplate();if(action==='review'){$('#d-status').value='review';await loaders.drafts();}
  if(action==='more')dialog('more-dialog');
}));
window.addEventListener('hashchange',()=>{const tab=location.hash.slice(1);if(PAGES[tab]&&tab!==currentTab)safe(()=>showTab(tab))();});

async function tick() {
  if(pollBusy)return;pollBusy=true;
  try {
    if(connectionTask&&['queued','running'].includes(connectionTask.status)) {
      const latest=await api('/api/accounts/connection/latest');if(latest?.id===connectionTask.id){const previous=connectionTask.status;connectionTask=latest;renderConnection(latest);if(previous!==latest.status&&['done','failed','cancelled'].includes(latest.status)){await globalState();if(currentTab==='accounts')await loaders.accounts();if(latest.status==='done'){$('#connect-proxy').value='';$('#connect-sessionid').value='';$('#connect-sessionid').type='password';$('#session-reveal').textContent='Показать';$('#session-reveal').setAttribute('aria-pressed','false');toast('Аккаунт подключён');}}}
    }
    if(document.hidden)return;
    if(currentTab==='drafts') {
      const task=await api('/api/tasks/auto-comment');if(JSON.stringify(task)!==JSON.stringify(autoTask))queueNeedsRefresh=true;renderAutoTask(task);
      if(queueNeedsRefresh&&!detailDirty&&!$('#draft-detail').contains(document.activeElement)&&!draftActionBusy){await loaders.drafts();queueNeedsRefresh=false;}
    }
    if(currentTab==='dashboard')await loaders.dashboard();
    if(currentTab==='search') {const tasks=await api('/api/tasks?limit=100');$('#search-tasks').innerHTML=taskRows(tasks.filter(t=>t.kind==='search').slice(0,5));}
    if(currentTab==='logs')await loaders.logs();
  }catch{ $('#service-state').innerHTML='<span></span>Нет связи с панелью'; }
  finally{pollBusy=false;}
}
setInterval(tick,3000);
let initial=location.hash.slice(1);if(!PAGES[initial]){try{initial=localStorage.getItem('ttlh-tab')||'dashboard';}catch{initial='dashboard';}}
safe(async()=>{await globalState();connectionTask=await api('/api/accounts/connection/latest');if(connectionTask&&!['queued','running'].includes(connectionTask.status))connectionTask=null;await showTab(initial);})();
