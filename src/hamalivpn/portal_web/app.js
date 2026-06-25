// HamaliVPN partner portal — single-file SPA (no build step).
const app = document.getElementById("app");
const toastEl = document.getElementById("toast");

// ── helpers ──────────────────────────────────────────────────────────────
const state = { identity: null, reseller: null, tab: null, cache: {} };

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) {
    state.identity = null;
    renderLogin();
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || `Ошибка ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : "Ошибка запроса");
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

const rub = (n) => `${Number(n || 0).toLocaleString("ru-RU")} ₽`;
const uuid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}
function daysLeft(iso) {
  if (!iso) return null;
  return Math.ceil((new Date(iso) - new Date()) / 86400000);
}

const STATUS_RU = {
  active: "Активен", expired: "Истёк", disabled: "Отключён",
  suspended: "Заморожен", pending: "Создаётся", error: "Ошибка", deleted: "Удалён",
};
const KIND_RU = {
  topup: "Пополнение", purchase: "Покупка ключа", extend: "Продление",
  adjust: "Корректировка", bonus: "Бонус", penalty: "Штраф", refund: "Возврат",
};
const LEVEL_RU = { start: "Start", partner: "Partner", vip: "VIP" };

async function copy(text, label = "Скопировано") {
  try { await navigator.clipboard.writeText(text); toast(label, "ok"); }
  catch { toast("Не удалось скопировать", "err"); }
}

// ── modal ────────────────────────────────────────────────────────────────
function modal(html) {
  closeModal();
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  back.innerHTML = `<div class="modal">${html}</div>`;
  back.addEventListener("click", (e) => { if (e.target === back) closeModal(); });
  document.body.appendChild(back);
  return back;
}
function closeModal() {
  document.querySelector(".modal-backdrop")?.remove();
}

// ── login ────────────────────────────────────────────────────────────────
function renderLogin(error = "") {
  app.innerHTML = `
    <div class="login">
      <form class="login__card" id="loginForm">
        <div class="brand">
          <div class="brand__mark">H</div>
          <div>
            <div class="brand__name">HamaliVPN</div>
            <div class="brand__sub">Партнёрский кабинет</div>
          </div>
        </div>
        <h1>Вход по ключу</h1>
        <p class="hint">Введите секретный ключ доступа, выданный администратором.</p>
        <div class="field">
          <label for="key">Секретный ключ</label>
          <input class="input" id="key" type="password" autocomplete="off"
                 placeholder="hk_..." spellcheck="false" />
        </div>
        ${error ? `<p class="hint" style="color:var(--danger)">${esc(error)}</p>` : ""}
        <button class="btn btn--primary" type="submit">Войти</button>
      </form>
    </div>`;
  document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = document.getElementById("key").value.trim();
    if (!key) return;
    try {
      await api("/portal/login", { method: "POST", body: { key } });
      await boot();
    } catch (err) {
      renderLogin(err.message === "unauthorized" ? "Неверный ключ" : err.message);
    }
  });
}

// ── shell ────────────────────────────────────────────────────────────────
const RESELLER_TABS = [
  ["dashboard", "Обзор"], ["buy", "Купить"], ["keys", "Ключи"],
  ["clients", "Клиенты"], ["history", "История"],
];
const ADMIN_TABS = [["resellers", "Реселлеры"], ["tariffs", "Тарифы"], ["allkeys", "Ключи"]];

function renderShell() {
  const isAdmin = state.identity.role === "admin";
  const tabs = isAdmin ? ADMIN_TABS : RESELLER_TABS;
  if (!state.tab) state.tab = tabs[0][0];
  const name = isAdmin ? "Администратор" : esc(state.reseller?.name || "Реселлер");
  const sub = isAdmin
    ? ""
    : `<span class="pill">${LEVEL_RU[state.reseller?.level] || ""}</span>`;
  app.innerHTML = `
    <div class="shell">
      <div class="topbar">
        <div class="topbar__id">
          <div class="brand__mark" style="width:36px;height:36px;font-size:18px;border-radius:11px">H</div>
          <div class="topbar__name">${name}</div>
          ${sub}
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

function renderTab() {
  const view = document.getElementById("view");
  const map = {
    dashboard: viewDashboard, buy: viewBuy, keys: viewKeys, clients: viewClients,
    history: viewHistory, resellers: viewResellers, tariffs: viewTariffs, allkeys: viewAllKeys,
  };
  (map[state.tab] || (() => { view.innerHTML = ""; }))(view);
}

// ── reseller: dashboard ──────────────────────────────────────────────────
async function viewDashboard(view) {
  const d = await api("/reseller/dashboard");
  view.innerHTML = `
    <div class="grid grid--stats">
      <div class="card stat">
        <div class="stat__label">Баланс</div>
        <div class="stat__value accent">${rub(d.balance_rub)}</div>
        <div class="stat__sub">доступно для покупок</div>
      </div>
      <div class="card stat"><div class="stat__label">Активные ключи</div>
        <div class="stat__value">${d.active_keys}</div></div>
      <div class="card stat"><div class="stat__label">Клиенты</div>
        <div class="stat__value">${d.clients}</div></div>
      <div class="card stat"><div class="stat__label">Истекают (3 дня)</div>
        <div class="stat__value" style="color:var(--warn)">${d.expiring_soon}</div></div>
    </div>
    <div class="section-title"><h2>Последние операции</h2></div>
    <div class="rows">
      ${d.recent_operations.length
        ? d.recent_operations.map(ledgerRow).join("")
        : `<div class="empty">Операций пока нет</div>`}
    </div>`;
}

function ledgerRow(e) {
  const pos = e.amount_rub >= 0;
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(KIND_RU[e.kind] || e.kind)}</div>
      <div class="row__meta">${esc(e.comment || "")} · ${fmtDate(e.created_at)}</div>
    </div>
    <div class="${pos ? "amount-pos" : "amount-neg"}">${pos ? "+" : ""}${rub(e.amount_rub)}</div>
  </div>`;
}

// ── reseller: buy ────────────────────────────────────────────────────────
async function viewBuy(view) {
  const [tariffs, clients] = await Promise.all([
    api("/reseller/tariffs"),
    api("/reseller/clients").catch(() => []),
  ]);
  state.cache.clients = clients;
  view.innerHTML = `
    <div class="section-title"><h2>Выберите тариф</h2>
      <span class="muted">Баланс: ${rub(state.reseller?.balance_rub ?? 0)}</span></div>
    <div class="grid grid--2">
      ${tariffs.length ? tariffs.map(tariffCard).join("") : `<div class="empty">Тарифы не настроены</div>`}
    </div>`;
}

function tariffCard(t) {
  return `<div class="tariff">
    <div class="tariff__name">${esc(t.name)}</div>
    <div class="tariff__price">${rub(t.price_rub)} <small>/ ${t.duration_days} дн.</small></div>
    <div class="tariff__meta">📱 ${t.device_limit} устр. · ${t.traffic_limit_gb ? t.traffic_limit_gb + " ГБ" : "∞ трафик"}</div>
    <button class="btn btn--primary" data-buy="${esc(t.code)}" data-name="${esc(t.name)}"
            data-price="${t.price_rub}">Создать ключ</button>
  </div>`;
}

function openBuyModal(code, name, price) {
  const clients = state.cache.clients || [];
  const options = clients.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  modal(`
    <h3>Покупка ключа</h3>
    <p class="sub">${esc(name)} · ${rub(price)}</p>
    <div class="field">
      <label>Привязать к клиенту (необязательно)</label>
      <select class="select" id="buyClient"><option value="">— без клиента —</option>${options}</select>
    </div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="buyConfirm">Списать ${rub(price)}</button>
    </div>`);
  document.getElementById("buyConfirm").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = "Создаём…";
    const clientId = document.getElementById("buyClient").value;
    try {
      const key = await api("/reseller/keys/buy", {
        method: "POST",
        body: { tariff_code: code, client_id: clientId ? Number(clientId) : null, idempotency_key: uuid() },
      });
      await refreshIdentity();
      closeModal();
      toast("Ключ создан, баланс обновлён", "ok");
      showKeyModal(key);
    } catch (err) {
      btn.disabled = false; btn.textContent = `Списать ${rub(price)}`;
      toast(err.message, "err");
    }
  });
}

// ── reseller: keys ───────────────────────────────────────────────────────
async function viewKeys(view) {
  const keys = await api("/reseller/keys");
  view.innerHTML = `
    <div class="section-title"><h2>Мои ключи</h2><span class="muted">${keys.length}</span></div>
    <div class="rows">
      ${keys.length ? keys.map(keyRow).join("") : `<div class="empty">Ключей пока нет — создайте на вкладке «Купить»</div>`}
    </div>`;
}

function keyRow(k) {
  const dl = daysLeft(k.expires_at);
  const left = dl == null ? "" : dl < 0 ? "истёк" : `${dl} дн.`;
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(k.tariff_code)} <span class="tag tag--${k.status}">${STATUS_RU[k.status] || k.status}</span></div>
      <div class="row__meta">до ${fmtDate(k.expires_at)} · ${left}</div>
    </div>
    <div class="row__actions">
      <button class="btn btn--sm" data-keyshow='${esc(JSON.stringify(k))}'>Открыть</button>
    </div>
  </div>`;
}

async function showKeyModal(k) {
  const url = k.subscription_url || "";
  const m = modal(`
    <h3>Ключ ${esc(k.tariff_code)}</h3>
    <p class="sub">Статус: ${STATUS_RU[k.status] || k.status} · до ${fmtDate(k.expires_at)}</p>
    <div class="qr" id="qrBox"><div class="muted" style="color:#333">Загрузка QR…</div></div>
    <div class="field" style="margin-top:14px">
      <label>Ссылка подписки</label>
      <div class="codebox" id="subUrl">${esc(url) || "—"}</div>
    </div>
    <div class="modal__actions">
      <button class="btn" data-action="copy-sub">Скопировать</button>
      <button class="btn btn--danger" data-keydisable="${esc(k.id)}">Отключить</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--ghost" data-action="close">Закрыть</button>
    </div>`);
  m.querySelector('[data-action="copy-sub"]').addEventListener("click", () => copy(url, "Ссылка скопирована"));
  m.querySelector("[data-keydisable]").addEventListener("click", async (e) => {
    if (!confirm("Отключить ключ? Клиент потеряет доступ.")) return;
    try { await api(`/reseller/keys/${e.currentTarget.dataset.keydisable}/disable`, { method: "POST" });
      toast("Ключ отключён", "ok"); closeModal(); if (state.tab === "keys") renderTab(); }
    catch (err) { toast(err.message, "err"); }
  });
  if (url) {
    try {
      const { qr } = await api(`/reseller/keys/${k.id}/qr`);
      const box = document.getElementById("qrBox");
      if (box && qr) box.innerHTML = `<img alt="QR" src="${qr}" />`;
    } catch { /* keep placeholder */ }
  } else {
    document.getElementById("qrBox").innerHTML = `<div class="muted" style="color:#333">Нет ссылки</div>`;
  }
}

// ── reseller: clients ────────────────────────────────────────────────────
async function viewClients(view) {
  const clients = await api("/reseller/clients");
  view.innerHTML = `
    <div class="section-title"><h2>Клиенты (CRM)</h2>
      <button class="btn btn--primary btn--sm" data-action="add-client">+ Добавить</button></div>
    <div class="rows">
      ${clients.length ? clients.map(clientRow).join("") : `<div class="empty">Клиентов пока нет</div>`}
    </div>`;
}
function clientRow(c) {
  const meta = [c.telegram && `@${c.telegram.replace(/^@/, "")}`, c.phone].filter(Boolean).join(" · ");
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(c.name)}</div>
      <div class="row__meta">${esc(meta || c.note || "—")}</div>
    </div>
  </div>`;
}
function openClientModal() {
  modal(`
    <h3>Новый клиент</h3>
    <p class="sub">Запись в вашей CRM — другие реселлеры её не видят.</p>
    <div class="field"><label>Имя *</label><input class="input" id="cName" placeholder="Иван" /></div>
    <div class="field"><label>Telegram</label><input class="input" id="cTg" placeholder="username" /></div>
    <div class="field"><label>Телефон</label><input class="input" id="cPhone" placeholder="+7…" /></div>
    <div class="field"><label>Заметка</label><textarea class="input" id="cNote"></textarea></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="cSave">Сохранить</button>
    </div>`);
  document.getElementById("cSave").addEventListener("click", async () => {
    const name = document.getElementById("cName").value.trim();
    if (!name) return toast("Укажите имя", "err");
    try {
      await api("/reseller/clients", { method: "POST", body: {
        name, telegram: document.getElementById("cTg").value.trim() || null,
        phone: document.getElementById("cPhone").value.trim() || null,
        note: document.getElementById("cNote").value.trim() || null,
      }});
      closeModal(); toast("Клиент добавлен", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}

// ── reseller: history ────────────────────────────────────────────────────
async function viewHistory(view) {
  const items = await api("/reseller/ledger");
  view.innerHTML = `
    <div class="section-title"><h2>История операций</h2></div>
    <div class="rows">
      ${items.length ? items.map(ledgerRow).join("") : `<div class="empty">Операций пока нет</div>`}
    </div>`;
}

// ── admin: resellers ─────────────────────────────────────────────────────
async function viewResellers(view) {
  const list = await api("/admin/resellers");
  view.innerHTML = `
    <div class="section-title"><h2>Реселлеры</h2>
      <button class="btn btn--primary btn--sm" data-action="add-reseller">+ Создать</button></div>
    <div class="rows">
      ${list.length ? list.map(resellerRow).join("") : `<div class="empty">Реселлеров пока нет</div>`}
    </div>`;
}
function resellerRow(r) {
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(r.name)} <span class="pill">${LEVEL_RU[r.level] || r.level}</span>
        ${r.is_blocked ? `<span class="tag tag--disabled">Заблокирован</span>` : ""}</div>
      <div class="row__meta">Баланс: ${rub(r.balance_rub)}</div>
    </div>
    <div class="row__actions">
      <button class="btn btn--sm" data-topup="${r.id}" data-name="${esc(r.name)}">Пополнить</button>
      <button class="btn btn--sm" data-issuekey="${r.id}">Ключ доступа</button>
    </div>
  </div>`;
}
function openCreateResellerModal() {
  modal(`
    <h3>Новый реселлер</h3>
    <div class="field"><label>Имя / название *</label><input class="input" id="rName" /></div>
    <div class="field"><label>Уровень</label>
      <select class="select" id="rLevel">
        <option value="start">Start</option><option value="partner">Partner</option><option value="vip">VIP</option>
      </select></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="rSave">Создать</button>
    </div>`);
  document.getElementById("rSave").addEventListener("click", async () => {
    const name = document.getElementById("rName").value.trim();
    if (!name) return toast("Укажите имя", "err");
    try {
      const r = await api("/admin/resellers", { method: "POST", body: {
        name, level: document.getElementById("rLevel").value }});
      closeModal(); toast("Реселлер создан", "ok"); renderTab();
      openIssueKeyModal(r.id);
    } catch (err) { toast(err.message, "err"); }
  });
}
function openTopupModal(id, name) {
  modal(`
    <h3>Пополнить баланс</h3>
    <p class="sub">${esc(name)}</p>
    <div class="field"><label>Сумма, ₽</label><input class="input" id="tAmount" type="number" min="1" /></div>
    <div class="field"><label>Комментарий</label><input class="input" id="tComment" placeholder="напр. оплата СБП" /></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="tSave">Пополнить</button>
    </div>`);
  document.getElementById("tSave").addEventListener("click", async () => {
    const amount = Number(document.getElementById("tAmount").value);
    if (!amount || amount <= 0) return toast("Введите сумму", "err");
    try {
      await api(`/admin/resellers/${id}/topup`, { method: "POST", body: {
        amount_rub: amount, comment: document.getElementById("tComment").value.trim(),
        idempotency_key: uuid() }});
      closeModal(); toast("Баланс пополнен", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}
async function openIssueKeyModal(id) {
  try {
    const r = await api(`/admin/resellers/${id}/secret-keys`, { method: "POST", body: { label: "" } });
    modal(`
      <h3>Ключ доступа создан</h3>
      <p class="sub">Передайте его реселлеру. Показывается один раз — потом только заново.</p>
      <div class="codebox" id="newKey">${esc(r.secret_key)}</div>
      <div class="modal__actions">
        <button class="btn btn--primary" data-action="copy-newkey">Скопировать ключ</button>
        <button class="btn btn--ghost" data-action="close">Готово</button>
      </div>`);
    document.querySelector('[data-action="copy-newkey"]')
      .addEventListener("click", () => copy(r.secret_key, "Ключ скопирован"));
  } catch (err) { toast(err.message, "err"); }
}

// ── admin: tariffs ───────────────────────────────────────────────────────
async function viewTariffs(view) {
  const list = await api("/admin/tariffs");
  view.innerHTML = `
    <div class="section-title"><h2>Тарифы</h2>
      <button class="btn btn--primary btn--sm" data-action="add-tariff">+ Тариф</button></div>
    <div class="grid grid--2">
      ${list.length ? list.map((t) => `
        <div class="tariff">
          <div class="tariff__name">${esc(t.name)} ${t.is_active ? "" : "<span class='tag tag--disabled'>выкл</span>"}</div>
          <div class="tariff__price">${rub(t.price_rub)} <small>/ ${t.duration_days} дн.</small></div>
          <div class="tariff__meta">код: ${esc(t.code)} · ${t.device_limit} устр.</div>
        </div>`).join("") : `<div class="empty">Тарифов нет</div>`}
    </div>`;
}
function openCreateTariffModal() {
  modal(`
    <h3>Новый тариф</h3>
    <div class="field"><label>Код (латиницей) *</label><input class="input" id="tfCode" placeholder="start_1m" /></div>
    <div class="field"><label>Название *</label><input class="input" id="tfName" placeholder="Start · 1 месяц" /></div>
    <div class="field"><label>Срок, дней *</label><input class="input" id="tfDays" type="number" value="30" /></div>
    <div class="field"><label>Цена реселлеру, ₽ *</label><input class="input" id="tfPrice" type="number" value="100" /></div>
    <div class="field"><label>Лимит устройств</label><input class="input" id="tfDev" type="number" value="1" /></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="tfSave">Создать</button>
    </div>`);
  document.getElementById("tfSave").addEventListener("click", async () => {
    const code = document.getElementById("tfCode").value.trim();
    const name = document.getElementById("tfName").value.trim();
    if (!code || !name) return toast("Заполните код и название", "err");
    try {
      await api("/admin/tariffs", { method: "POST", body: {
        code, name,
        duration_days: Number(document.getElementById("tfDays").value) || 30,
        price_rub: Number(document.getElementById("tfPrice").value) || 0,
        device_limit: Number(document.getElementById("tfDev").value) || 1,
      }});
      closeModal(); toast("Тариф создан", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}

// ── admin: all keys ──────────────────────────────────────────────────────
async function viewAllKeys(view) {
  const keys = await api("/admin/keys");
  view.innerHTML = `
    <div class="section-title"><h2>Все ключи</h2><span class="muted">${keys.length}</span></div>
    <div class="rows">
      ${keys.length ? keys.map((k) => `<div class="row">
        <div class="row__main">
          <div class="row__title">${esc(k.tariff_code)}
            <span class="tag tag--${k.status}">${STATUS_RU[k.status] || k.status}</span></div>
          <div class="row__meta">до ${fmtDate(k.expires_at)} · ${esc(k.id.slice(0, 8))}</div>
        </div>
        <div class="row__actions">
          <button class="btn btn--sm btn--danger" data-adminkeydisable="${esc(k.id)}">Отключить</button>
        </div></div>`).join("") : `<div class="empty">Ключей нет</div>`}
    </div>`;
}

// ── global event delegation ──────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-tab],[data-action],[data-buy],[data-keyshow],[data-topup],[data-issuekey],[data-adminkeydisable]");
  if (!t) return;
  if (t.dataset.tab) { state.tab = t.dataset.tab; renderShell(); return; }
  const a = t.dataset.action;
  if (a === "logout") { await api("/portal/logout", { method: "POST" }).catch(() => {}); state.identity = null; renderLogin(); return; }
  if (a === "close") return closeModal();
  if (a === "add-client") return openClientModal();
  if (a === "add-reseller") return openCreateResellerModal();
  if (a === "add-tariff") return openCreateTariffModal();
  if (t.dataset.buy) return openBuyModal(t.dataset.buy, t.dataset.name, Number(t.dataset.price));
  if (t.dataset.keyshow) return showKeyModal(JSON.parse(t.dataset.keyshow));
  if (t.dataset.topup) return openTopupModal(Number(t.dataset.topup), t.dataset.name);
  if (t.dataset.issuekey) return openIssueKeyModal(Number(t.dataset.issuekey));
  if (t.dataset.adminkeydisable) {
    if (!confirm("Отключить ключ?")) return;
    try { await api(`/admin/keys/${t.dataset.adminkeydisable}/disable`, { method: "POST" });
      toast("Отключён", "ok"); renderTab(); } catch (err) { toast(err.message, "err"); }
  }
});

// ── boot ─────────────────────────────────────────────────────────────────
async function refreshIdentity() {
  const me = await api("/portal/me");
  state.identity = { role: me.role };
  state.reseller = me.reseller || null;
}
async function boot() {
  try {
    await refreshIdentity();
    renderShell();
  } catch (err) {
    if (err.message !== "unauthorized") renderLogin(err.message);
  }
}
boot();
