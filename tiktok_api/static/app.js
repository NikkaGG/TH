"use strict";
let apiKey = "", role = "", timer = null, oauthRedirect = "", busy = false;
const $ = id => document.getElementById(id);
const el = (tag, text, className) => {const node = document.createElement(tag); if(text !== undefined) node.textContent=text; if(className) node.className=className; return node;};
function message(text){$("message").textContent = text;}
async function api(path, options={}) {
  const response = await fetch("/api/v1" + path, { ...options, headers:{Authorization:"Bearer " + apiKey, "Content-Type":"application/json", ...options.headers} });
  const data = await response.json();
  if (path === "/capabilities") oauthRedirect = data.oauth_redirect_uri || "";
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function button(text, fn, secondary=true) {
  const b = el("button",text,secondary?"secondary":""); b.type="button";
  b.onclick=async()=>{b.disabled=true;busy=true;try{await fn();message("");await refresh();}catch(e){message(e.message);}finally{b.disabled=false;busy=false;}};
  return b;
}
function entry(title, status, meta) {
  const n=el("div",undefined,"entry"), heading=el("div");heading.append(el("strong",title),el("span",status,"badge"));n.append(heading,el("div",meta,"meta"));return n;
}
function empty(node){if(!node.childElementCount)node.append(el("p","Записей пока нет.","empty"));}
async function refresh(){
  if (!apiKey) return;
  const [accounts,campaigns,drafts,jobs] = await Promise.all([api("/accounts"),api("/campaigns"),api("/drafts"),api("/jobs")]);
  const accountNames = Object.fromEntries(accounts.map(a=>[a.id,a.display_name || a.open_id]));
  const aList=$("accounts-list");aList.replaceChildren();
  for(const a of accounts){const n=entry(a.display_name||a.open_id,a.status,`ID: ${a.id} · Права: ${a.scopes.join(", ")}`);
    if(role==="admin" && a.status!=="disconnected"){const actions=el("div",undefined,"actions");actions.append(button("Проверить токен",()=>api(`/accounts/${a.id}/refresh`,{method:"POST"})),button("Отключить",()=>api(`/accounts/${a.id}`,{method:"DELETE"})));n.append(actions);}aList.append(n);}empty(aList);
  const select=$("account-select"),current=select.value;select.replaceChildren();
  for(const a of accounts.filter(a=>a.status==="active")){const o=el("option",a.display_name||a.open_id);o.value=a.id;select.append(o);}if([...select.options].some(o=>o.value===current))select.value=current;
  const cList=$("campaigns-list");cList.replaceChildren();
  for(const c of campaigns){const n=entry(c.name,c.source,`${accountNames[c.account_id]||c.account_id} · ${c.keywords.join(", ")} · ${c.enabled?"Каждые "+c.interval_minutes+" мин.":"По запросу"}`);
    if(role!=="viewer"){const actions=el("div",undefined,"actions");actions.append(button("Запустить",()=>api(`/campaigns/${c.id}/run`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()}})));
      if(c.interval_minutes)actions.append(button(c.enabled?"Остановить расписание":"Включить расписание",()=>{const payload={};for(const key of ["name","account_id","source","keywords","exclude_keywords","comment_text","min_score","max_results","interval_minutes"])payload[key]=c[key];payload.enabled=!c.enabled;return api(`/campaigns/${c.id}`,{method:"PUT",body:JSON.stringify(payload)});}));n.append(actions);}cList.append(n);}empty(cList);
  const dList=$("drafts-list");dList.replaceChildren();
  for(const d of drafts){const n=entry(d.video.author||d.video.external_id,d.status,`Аккаунт: ${accountNames[d.account_id]||d.account_id} · ${d.video.description}`);n.append(el("p",d.text));const actions=el("div",undefined,"actions"),link=el("a","Открыть ролик ↗");link.href=d.video.url;link.target="_blank";link.rel="noopener noreferrer";actions.append(link);
    if(role!=="viewer" && d.status==="pending"){actions.append(button("Скопировать текст",()=>navigator.clipboard.writeText(d.text)),button("Я опубликовал вручную",()=>api(`/drafts/${d.id}/manual-confirm`,{method:"POST",body:JSON.stringify({confirmed:true})})),button("Пропустить",()=>api(`/drafts/${d.id}/skip`,{method:"POST"})));}n.append(actions);dList.append(n);}empty(dList);
  const jList=$("jobs-list");jList.replaceChildren();
  for(const j of jobs){const n=entry(campaigns.find(c=>c.id===j.campaign_id)?.name||j.campaign_id,j.status,`ID: ${j.id} · Попыток: ${j.attempts} · ${j.error_code||JSON.stringify(j.result)} · ${j.created_at}`);if(role!=="viewer" && ["queued","dispatched","running","retry_wait"].includes(j.status))n.append(button("Отменить задачу",()=>api(`/jobs/${j.id}/cancel`,{method:"POST"})));jList.append(n);}empty(jList);
}
$("login-form").onsubmit=async event=>{event.preventDefault();apiKey=$("key").value;try{const cap=await api("/capabilities");role=cap.role;$("key").value="";$("login").hidden=true;$("workspace").hidden=false;$("role").textContent=`Роль: ${role} · Режим очереди: ${cap.queue_mode}`;document.querySelectorAll("[data-admin]").forEach(n=>n.hidden=role!=="admin");document.querySelectorAll("[data-operate]").forEach(n=>n.hidden=role==="viewer");await refresh();message("");clearInterval(timer);timer=setInterval(()=>{if(!busy)refresh().catch(e=>message(e.message));},8000);}catch(e){apiKey="";message(e.message);}};
$("logout").onclick=()=>{apiKey="";clearInterval(timer);$("workspace").hidden=true;$("login").hidden=false;message("");};
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tab").forEach(n=>n.hidden=n.id!==b.dataset.tab);document.querySelectorAll("nav button").forEach(n=>n.classList.toggle("selected",n===b));});
$("connect").onclick=async()=>{try{if(location.protocol!=="https:" || location.host!==new URL(oauthRedirect).host)throw new Error("Откройте панель на HTTPS-адресе, указанном в LH_REDIRECT_URI: "+oauthRedirect);const result=await api("/oauth/start",{method:"POST"});location.assign(result.authorization_url);}catch(e){message(e.message);}};
$("campaign-form").onsubmit=async event=>{event.preventDefault();const form=event.target,data=new FormData(form),terms=name=>data.get(name).split(",").map(s=>s.trim()).filter(Boolean),interval=data.get("interval");try{await api("/campaigns",{method:"POST",body:JSON.stringify({name:data.get("name"),account_id:data.get("account_id"),source:data.get("source"),keywords:terms("keywords"),exclude_keywords:terms("exclude_keywords"),comment_text:data.get("comment_text"),interval_minutes:interval?Number(interval):null,enabled:!!interval})});form.reset();await refresh();message("");}catch(e){message(e.message);}};
$("import-form").onsubmit=async event=>{event.preventDefault();const videos=$("import-text").value.split(/\r?\n/).filter(s=>s.trim()).map(line=>{const [url,...text]=line.split("\t");return{url:url.trim(),description:text.join("\t")};});try{await api("/videos/import",{method:"POST",body:JSON.stringify({videos})});$("import-text").value="";message("Ролики импортированы. Запустите сценарий с источником «Импортированные ссылки».");}catch(e){message(e.message);}};
