// HamaliVPN partner portal — premium SPA for the existing api.py backend.
// Auth: Bearer portal_access_key (issued by admin). No build step.
const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const KEY_STORE = "hamali_portal_key";

const state = { me: null, tab: null, cache: {} };

const getKey = () => localStorage.getItem(KEY_STORE) || "";
const setKey = (k) => localStorage.setItem(KEY_STORE, k);
const clearKey = () => localStorage.removeItem(KEY_STORE);

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${getKey()}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    clearKey();
    renderLogin("Ключ недействителен");
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const d = data && (data.detail || data.message);
    throw new Error(typeof d === "string" ? d : `Ошибка ${res.status}`);
  }
  return data;
}

function toast(msg, kind = "") {
  toastEl.textContent = msg;
  toastEl.className = `toast show ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toastEl.className = "toast"), 2600);
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rub = (n) => `${Math.round(Number(n || 0)).toLocaleString("ru-RU")} ₽`;

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d) ? "—"
    : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}
function daysLeft(iso) {
  if (!iso) return null;
  return Math.ceil((new Date(iso) - new Date()) / 86400000);
}

const SUB_STATUS = {
  active: ["Активен", "active"], expired: ["Истёк", "expired"],
  disabled: ["Отключён", "disabled"], revoked: ["Отозван", "disabled"],
  pending: ["Создаётся", "pending"], none: ["Нет ключа", "disabled"], inactive: ["Неактивен", "disabled"],
};
const TX_RU = { topup: "Пополнение", purchase: "Покупка", refund: "Возврат", bonus: "Бонус", penalty: "Штраф" };
const LEVEL_RU = {
  1: "Start", 2: "Partner", 3: "VIP",
  start: "Start", partner: "Partner", vip: "VIP", "": "",
};

async function copy(text, label = "Скопировано") {
  try { await navigator.clipboard.writeText(text); toast(label, "ok"); }
  catch { toast("Не удалось скопировать", "err"); }
}

function modal(html) {
  closeModal();
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  back.innerHTML = `<div class="modal">${html}</div>`;
  back.addEventListener("click", (e) => { if (e.target === back) closeModal(); });
  document.body.appendChild(back);
  return back;
}
const closeModal = () => document.querySelector(".modal-backdrop")?.remove();

// ── login ────────────────────────────────────────────────────────────────
function renderLogin(error = "") {
  app.innerHTML = `
    <div class="login">
      <form class="login__card" id="loginForm">
        <div class="brand">
          <div class="brand__mark">H</div>
          <div><div class="brand__name">HamaliVPN</div>
            <div class="brand__sub">Партнёрский кабинет</div></div>
        </div>
        <h1>Вход по ключу</h1>
        <p class="hint">Введите секретный ключ доступа, выданный администратором.</p>
        <div class="field">
          <label for="key">Секретный ключ</label>
          <input class="input" id="key" type="password" autocomplete="off" spellcheck="false" />
        </div>
        ${error ? `<p class="hint" style="color:var(--danger)">${esc(error)}</p>` : ""}
        <button class="btn btn--primary" type="submit">Войти</button>
      </form>
    </div>`;
  document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = document.getElementById("key").value.trim();
    if (!key) return;
    setKey(key);
    try {
      state.me = await api("/portal/me");
      state.tab = null;
      renderShell();
    } catch (err) {
      clearKey();
      if (err.message !== "unauthorized") renderLogin("Неверный ключ");
    }
  });
}

// ── shell ────────────────────────────────────────────────────────────────
function tabsFor(role) {
  const base = [["dashboard", "Обзор"], ["buy", "Купить"], ["clients", "Клиенты"]];
  return role === "super_admin" ? [...base, ["resellers", "Реселлеры"]] : base;
}

function renderShell() {
  const role = state.me.role;
  const tabs = tabsFor(role);
  if (!state.tab) state.tab = tabs[0][0];
  const isAdmin = role === "super_admin";
  app.innerHTML = `
    <div class="shell">
      <div class="topbar">
        <div class="topbar__id">
          <div class="brand__mark" style="width:36px;height:36px;font-size:18px;border-radius:11px">H</div>
          <div class="topbar__name">${esc(state.me.name || "Партнёр")}</div>
          ${isAdmin ? `<span class="pill">Админ</span>`
            : (state.me.level ? `<span class="pill">${LEVEL_RU[state.me.level] || ""}</span>` : "")}
        </div>
        <button class="btn btn--ghost btn--sm" data-action="logout">Выйти</button>
      </div>
      <div class="tabs">
        ${tabs.map(([id, label]) =>
          `<button class="tab ${state.tab === id ? "is-active" : ""}" data-tab="${id}">${label}</button>`
        ).join("")}
      </div>
      <div id="view"><div class="empty">Загрузка…</div></div>
    </div>`;
  renderTab();
}

async function renderTab() {
  const view = document.getElementById("view");
  const map = {
    dashboard: viewDashboard, buy: viewBuy, clients: viewClients, resellers: viewResellers,
  };
  try { await (map[state.tab] || (() => {}))(view); }
  catch (err) { if (err.message !== "unauthorized") view.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

// ── dashboard ──────────────────────────────────────────────────────────────
async function viewDashboard(view) {
  const d = await api("/reseller/dashboard");
  state.cache.balance = d.balance;
  view.innerHTML = `
    <div class="grid grid--stats">
      <div class="card stat"><div class="stat__label">Баланс</div>
        <div class="stat__value accent">${rub(d.balance)}</div>
        <div class="stat__sub">доступно для покупок</div></div>
      <div class="card stat"><div class="stat__label">Клиенты</div>
        <div class="stat__value">${d.clients_count}</div></div>
    </div>
    <div class="section-title"><h2>Последние операции</h2></div>
    <div class="rows">
      ${(d.transactions || []).length
        ? d.transactions.map(txRow).join("")
        : `<div class="empty">Операций пока нет</div>`}
    </div>`;
}
function txRow(t) {
  const pos = Number(t.amount) >= 0;
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(TX_RU[t.type] || t.type)}</div>
      <div class="row__meta">${esc(t.desc || "")} · ${fmtDate(t.date)}</div>
    </div>
    <div class="${pos ? "amount-pos" : "amount-neg"}">${pos ? "+" : ""}${rub(t.amount)}</div>
  </div>`;
}

// ── buy ────────────────────────────────────────────────────────────────────
async function viewBuy(view) {
  const tariffs = await api("/reseller/tariffs");
  view.innerHTML = `
    <div class="section-title"><h2>Выберите тариф</h2>
      <span class="muted">Баланс: ${rub(state.cache.balance ?? 0)}</span></div>
    <div class="grid grid--2">
      ${tariffs.length ? tariffs.map(tariffCard).join("") : `<div class="empty">Тарифы не настроены</div>`}
    </div>`;
}
function tariffCard(t) {
  return `<div class="tariff">
    <div class="tariff__name">${esc(t.name)}</div>
    <div class="tariff__price">${rub(t.price_rub)} <small>/ ${t.duration_days} дн.</small></div>
    <div class="tariff__meta">📱 ${t.device_limit} устр. · ${t.traffic_limit_gb ? t.traffic_limit_gb + " ГБ" : "∞ трафик"}</div>
    <button class="btn btn--primary" data-buy='${esc(JSON.stringify(t))}'>Создать ключ</button>
  </div>`;
}
function openBuyModal(t) {
  modal(`
    <h3>Новый ключ</h3>
    <p class="sub">${esc(t.name)} · ${rub(t.price_rub)} · ${t.duration_days} дн.</p>
    <div class="field"><label>Имя клиента</label><input class="input" id="bName" placeholder="напр. Иван" /></div>
    <div class="field"><label>Telegram клиента</label><input class="input" id="bTg" placeholder="username" /></div>
    <div class="field"><label>Телефон</label><input class="input" id="bPhone" placeholder="+7…" /></div>
    <div class="field"><label>Заметка</label><input class="input" id="bNote" /></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="bConfirm">Списать ${rub(t.price_rub)}</button>
    </div>`);
  document.getElementById("bConfirm").addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Создаём…";
    try {
      const r = await api("/reseller/keys/buy", { method: "POST", body: {
        tariff_id: t.id,
        client_name: document.getElementById("bName").value.trim(),
        client_telegram: document.getElementById("bTg").value.trim(),
        client_phone: document.getElementById("bPhone").value.trim(),
        note: document.getElementById("bNote").value.trim(),
      }});
      closeModal();
      toast("Ключ создан, баланс обновлён", "ok");
      showSubModal(r.sub_url);
      state.cache.balance = (state.cache.balance ?? 0) - t.price_rub;
    } catch (err) {
      btn.disabled = false; btn.textContent = `Списать ${rub(t.price_rub)}`;
      toast(err.message, "err");
    }
  });
}
function showSubModal(url) {
  modal(`
    <h3>Ключ готов</h3>
    <p class="sub">Передайте ссылку клиенту — он импортирует её в приложение.</p>
    <div class="field"><label>Ссылка подписки</label><div class="codebox">${esc(url || "—")}</div></div>
    <div class="modal__actions">
      <button class="btn btn--primary" data-copy="${esc(url || "")}">Скопировать</button>
      <button class="btn btn--ghost" data-action="close">Готово</button>
    </div>`);
}

// ── clients (= keys) ───────────────────────────────────────────────────────
async function viewClients(view) {
  const clients = await api("/reseller/clients");
  view.innerHTML = `
    <div class="section-title"><h2>Клиенты и ключи</h2><span class="muted">${clients.length}</span></div>
    <div class="rows">
      ${clients.length ? clients.map(clientRow).join("")
        : `<div class="empty">Пока пусто — создайте ключ на вкладке «Купить»</div>`}
    </div>`;
}
function clientRow(c) {
  const [label, cls] = SUB_STATUS[c.sub_status] || [c.sub_status, "pending"];
  const dl = daysLeft(c.expires_at);
  const left = dl == null ? "" : dl < 0 ? "истёк" : `${dl} дн.`;
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(c.name || "Без имени")} <span class="tag tag--${cls}">${label}</span></div>
      <div class="row__meta">до ${fmtDate(c.expires_at)} ${left ? "· " + left : ""} · 📱 ${c.device_limit}</div>
    </div>
    <div class="row__actions">
      <button class="btn btn--sm" data-client='${esc(JSON.stringify(c))}'>Открыть</button>
    </div>
  </div>`;
}
function showClientModal(c) {
  const url = c.sub_url || "";
  modal(`
    <h3>${esc(c.name || "Клиент")}</h3>
    <p class="sub">${(SUB_STATUS[c.sub_status] || [c.sub_status])[0]} · до ${fmtDate(c.expires_at)}</p>
    <div class="field"><label>Ссылка подписки</label><div class="codebox">${esc(url || "—")}</div></div>
    <div class="field"><label>Лимит устройств</label>
      <input class="input" id="devLimit" type="number" min="1" max="20" value="${c.device_limit || 1}" /></div>
    <div class="modal__actions">
      <button class="btn btn--primary" data-copy="${esc(url)}">Скопировать ссылку</button>
      <button class="btn" id="saveDev">Сохранить лимит</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--danger" id="revoke">Отозвать ключ</button>
      <button class="btn btn--ghost" data-action="close">Закрыть</button>
    </div>`);
  const uuid = c.remnawave_uuid;
  document.getElementById("saveDev").addEventListener("click", async () => {
    if (!uuid) return toast("Нет ключа для изменения", "err");
    try {
      await api(`/reseller/clients/${uuid}`, { method: "PUT", body: {
        devices_limit: Number(document.getElementById("devLimit").value) || 1 }});
      toast("Лимит обновлён", "ok");
    } catch (err) { toast(err.message, "err"); }
  });
  document.getElementById("revoke").addEventListener("click", async () => {
    if (!uuid) return toast("Нет ключа", "err");
    if (!confirm("Отозвать ключ? Клиент потеряет доступ.")) return;
    try {
      await api(`/reseller/clients/${uuid}`, { method: "DELETE" });
      toast("Ключ отозван", "ok"); closeModal(); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}

// ── admin: resellers ───────────────────────────────────────────────────────
async function viewResellers(view) {
  const list = await api("/admin/resellers");
  view.innerHTML = `
    <div class="section-title"><h2>Реселлеры</h2>
      <button class="btn btn--primary btn--sm" data-action="add-reseller">+ Создать</button></div>
    <div class="rows">
      ${list.length ? list.map(resellerRow).join("") : `<div class="empty">Реселлеров нет</div>`}
    </div>`;
}

function openCreateResellerModal() {
  modal(`
    <h3>Новый реселлер</h3>
    <div class="field"><label>Имя / название *</label><input class="input" id="rName" placeholder="напр. Магомед" /></div>
    <div class="field"><label>Telegram ID (необязательно)</label>
      <input class="input" id="rTg" type="number" placeholder="если знаете — привяжем" /></div>
    <div class="field"><label>Уровень</label>
      <select class="select" id="rLevel">
        <option value="1">Start</option><option value="2">Partner</option><option value="3">VIP</option>
      </select></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="rSave">Создать</button>
    </div>`);
  document.getElementById("rSave").addEventListener("click", async () => {
    const name = document.getElementById("rName").value.trim();
    if (!name) return toast("Укажите имя", "err");
    const tg = document.getElementById("rTg").value.trim();
    try {
      const r = await api("/admin/resellers", { method: "POST", body: {
        name,
        telegram_id: tg ? Number(tg) : null,
        level: Number(document.getElementById("rLevel").value) || 1,
      }});
      toast("Реселлер создан", "ok");
      renderTab();
      modal(`
        <h3>Реселлер создан</h3>
        <p class="sub">${esc(r.name)} — ключ доступа (передайте реселлеру, показывается один раз):</p>
        <div class="codebox">${esc(r.portal_access_key)}</div>
        <div class="modal__actions">
          <button class="btn btn--primary" data-copy="${esc(r.portal_access_key)}">Скопировать ключ</button>
          <button class="btn btn--ghost" data-action="close">Готово</button>
        </div>`);
    } catch (err) { toast(err.message, "err"); }
  });
}
function resellerRow(r) {
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(r.name || "Без имени")}
        ${r.level ? `<span class="pill">${LEVEL_RU[r.level] || r.level}</span>` : ""}</div>
      <div class="row__meta">ID ${r.id} · tg ${r.telegram_id} · Баланс: ${rub(r.balance)}</div>
    </div>
    <div class="row__actions">
      <button class="btn btn--sm" data-topup="${r.id}" data-name="${esc(r.name || "")}">Пополнить</button>
      <button class="btn btn--sm" data-issuekey="${r.id}">Ключ доступа</button>
    </div>
  </div>`;
}
function openTopupModal(id, name) {
  modal(`
    <h3>Пополнить баланс</h3>
    <p class="sub">${esc(name)} (ID ${id})</p>
    <div class="field"><label>Сумма, ₽</label><input class="input" id="tAmt" type="number" min="1" /></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="tGo">Пополнить</button>
    </div>`);
  document.getElementById("tGo").addEventListener("click", async () => {
    const amount = Math.round(Number(document.getElementById("tAmt").value));
    if (!amount || amount <= 0) return toast("Введите сумму", "err");
    try {
      const r = await api(`/admin/resellers/${id}/topup`, { method: "POST", body: { amount } });
      closeModal(); toast(`Баланс: ${rub(r.new_balance)}`, "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}
async function openIssueKeyModal(id) {
  try {
    const r = await api(`/admin/resellers/${id}/key`, { method: "POST" });
    modal(`
      <h3>Ключ доступа создан</h3>
      <p class="sub">Передайте его реселлеру. Старый ключ перестаёт работать.</p>
      <div class="codebox">${esc(r.portal_access_key)}</div>
      <div class="modal__actions">
        <button class="btn btn--primary" data-copy="${esc(r.portal_access_key)}">Скопировать</button>
        <button class="btn btn--ghost" data-action="close">Готово</button>
      </div>`);
  } catch (err) { toast(err.message, "err"); }
}

// ── delegation ─────────────────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-tab],[data-action],[data-buy],[data-client],[data-topup],[data-issuekey],[data-copy]");
  if (!t) return;
  if (t.dataset.tab) { state.tab = t.dataset.tab; renderShell(); return; }
  const a = t.dataset.action;
  if (a === "logout") { clearKey(); state.me = null; renderLogin(); return; }
  if (a === "close") return closeModal();
  if (t.dataset.copy !== undefined && t.dataset.copy !== "") return copy(t.dataset.copy, "Ссылка скопирована");
  if (t.dataset.buy) return openBuyModal(JSON.parse(t.dataset.buy));
  if (t.dataset.client) return showClientModal(JSON.parse(t.dataset.client));
  if (t.dataset.topup) return openTopupModal(Number(t.dataset.topup), t.dataset.name);
  if (t.dataset.issuekey) return openIssueKeyModal(Number(t.dataset.issuekey));
});

// ── boot ───────────────────────────────────────────────────────────────────
async function boot() {
  if (!getKey()) return renderLogin();
  try { state.me = await api("/portal/me"); renderShell(); }
  catch (err) { if (err.message !== "unauthorized") renderLogin(); }
}
boot();
