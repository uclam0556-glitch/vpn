// HamaliVPN partner portal — premium SPA for the existing api.py backend.
// Auth: Bearer portal_access_key (issued by admin). No build step.
const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const KEY_STORE = "hamali_portal_key";
const API_TIMEOUT_MS = 12000;

const state = { me: null, tab: null, cache: {} };

let memoryKey = "";

function markBootReady() {
  if (window.__hamaliBootFallback) {
    clearTimeout(window.__hamaliBootFallback);
    window.__hamaliBootFallback = null;
  }
}

function getStorage() {
  try {
    const storage = window.localStorage;
    const probe = "__hamali_probe__";
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch (err) {
    return null;
  }
}

const safeStorage = getStorage();
const getKey = () => {
  try { return safeStorage ? (safeStorage.getItem(KEY_STORE) || "") : memoryKey; }
  catch (err) { return memoryKey; }
};
const setKey = (k) => {
  memoryKey = k || "";
  try { if (safeStorage) safeStorage.setItem(KEY_STORE, memoryKey); }
  catch (err) { /* memory fallback */ }
};
const clearKey = () => {
  memoryKey = "";
  try { if (safeStorage) safeStorage.removeItem(KEY_STORE); }
  catch (err) { /* memory fallback */ }
};

async function api(path, { method = "GET", body } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        Authorization: `Bearer ${getKey()}`,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error("Портал не получил ответ от сервера за 12 секунд. Отключите проблемный VPN, обновите страницу или откройте app.hamali.ru через другую сеть.");
    }
    throw new Error("Нет соединения с сервером портала. Проверьте сеть/VPN и попробуйте ещё раз.");
  } finally {
    clearTimeout(timeout);
  }
  if (res.status === 401) {
    clearKey();
    renderLogin("Ключ недействителен");
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch (err) { /* no body */ }
  if (res.status === 403 && String((data && data.detail) || "").toLowerCase().includes("заблок")) {
    clearKey();
    renderLogin(data.detail || "Доступ заблокирован");
    throw new Error("unauthorized");
  }
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
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
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
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d) ? "—"
    : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
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
  catch (err) { toast("Не удалось скопировать", "err"); }
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
const closeModal = () => {
  const back = document.querySelector(".modal-backdrop");
  if (back) back.remove();
};

// ── login ────────────────────────────────────────────────────────────────
function renderLogin(error = "") {
  markBootReady();
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
      if (err.message !== "unauthorized") {
        renderLogin(
          err.message && err.message.startsWith("Ошибка 5")
            ? "Портал временно недоступен. Обновите страницу через минуту или напишите в поддержку."
            : "Не удалось проверить ключ. Проверьте подключение и попробуйте ещё раз."
        );
      }
    }
  });
}

function renderFatal(error = "") {
  markBootReady();
  const message = error ? String(error).slice(0, 180) : "Не удалось загрузить кабинет";
  app.innerHTML = `
    <div class="login">
      <div class="login__card">
        <div class="brand">
          <div class="brand__mark">H</div>
          <div><div class="brand__name">HamaliVPN</div>
            <div class="brand__sub">Партнёрский кабинет</div></div>
        </div>
        <h1>Кабинет не загрузился</h1>
        <p class="hint">Обновите страницу. Если ошибка повторится — откройте ссылку в Safari/Chrome или напишите в поддержку.</p>
        <p class="hint" style="color:var(--danger)">${esc(message)}</p>
        <button class="btn btn--primary" type="button" onclick="location.reload()">Обновить</button>
      </div>
    </div>`;
}

// ── shell ────────────────────────────────────────────────────────────────
function tabsFor(role) {
  if (role === "super_admin") {
    return [["admin", "Обзор"], ["resellers", "Реселлеры"], ["subadmins", "Субадмины"], ["tariffs", "Тарифы"], ["referrals", "Рефералы"], ["allkeys", "Ключи"], ["audit", "Аудит"]];
  }
  return [["dashboard", "Обзор"], ["buy", "Купить"], ["clients", "Клиенты"]];
}

function renderShell() {
  markBootReady();
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
    subadmins: viewSubadmins,
    admin: viewAdminDashboard, tariffs: viewTariffs, referrals: viewAdminReferrals, allkeys: viewAllKeys, audit: viewAudit,
  };
  try { await (map[state.tab] || (() => {}))(view); }
  catch (err) { if (err.message !== "unauthorized") view.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

async function viewAdminReferrals(view) {
  const d = await api("/admin/referrals");
  view.innerHTML = `
    <div class="section-title"><h2>Рефералы и статистика</h2></div>
    <div class="card" style="overflow-x:auto">
      <table class="table">
        <thead><tr><th>ID</th><th>Имя / TG</th><th>Баланс</th><th>Рефералов</th></tr></thead>
        <tbody>
          ${d.length ? d.map(r => `
            <tr>
              <td>${r.id} <div class="hint">${r.telegram_id}</div></td>
              <td>${esc(r.full_name || "")} ${r.username ? `<br><span class="pill">@${esc(r.username)}</span>` : ""}</td>
              <td>${rub(r.balance_rub)}</td>
              <td>${r.referrals_count} чел.</td>
            </tr>
          `).join("") : `<tr><td colspan="4" class="empty" style="text-align:center;padding:20px">Нет активных рефералов</td></tr>`}
        </tbody>
      </table>
    </div>`;
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
      <span class="muted">Баланс: ${rub((state.cache.balance != null ? state.cache.balance : 0))}</span></div>
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
      showSubModal(r.connect_url || r.sub_url);
      state.cache.balance = ((state.cache.balance != null ? state.cache.balance : 0)) - t.price_rub;
    } catch (err) {
      btn.disabled = false; btn.textContent = `Списать ${rub(t.price_rub)}`;
      toast(err.message, "err");
    }
  });
}
function showSubModal(url) {
  modal(`
    <h3>Ключ готов 🎉</h3>
    <p class="sub">Отправьте клиенту эту ссылку — откроется красивая страница с установкой приложения и подключением в один клик.</p>
    <p class="hint" style="margin-top:8px">
      Важно: не импортируйте клиентский ключ на своём устройстве. На тарифе «1 устройство»
      первый импорт закрепляет слот за тем телефоном или компьютером, где его открыли.
    </p>
    <div class="field"><label>Ссылка для клиента</label><div class="codebox">${esc(url || "—")}</div></div>
    <div class="modal__actions">
      <button class="btn btn--primary" data-copy="${esc(url || "")}">Скопировать ссылку</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--ghost" data-action="close">Готово</button>
    </div>`);
}

// ── clients (= keys) ───────────────────────────────────────────────────────
async function viewClients(view) {
  const clients = await api("/reseller/clients");
  const q = (state.cache.clientQ || "").toLowerCase();
  const visible = q
    ? clients.filter((c) =>
        `${c.name || ""} ${c.telegram_id || ""} ${c.short_code || ""} ${c.connect_url || ""}`.toLowerCase().includes(q)
      )
    : clients;
  const active = clients.filter((c) => c.sub_status === "active").length;
  const expiring = clients.filter((c) => {
    const d = daysLeft(c.expires_at);
    return c.sub_status === "active" && d !== null && d >= 0 && d <= 5;
  }).length;
  const disabled = clients.length - active;
  view.innerHTML = `
    <div class="grid grid--stats">
      <div class="card stat"><div class="stat__label">Всего клиентов</div><div class="stat__value">${clients.length}</div></div>
      <div class="card stat"><div class="stat__label">Активные</div><div class="stat__value accent">${active}</div></div>
      <div class="card stat"><div class="stat__label">Истекают до 5 дней</div><div class="stat__value">${expiring}</div></div>
      <div class="card stat"><div class="stat__label">Отключены / истекли</div><div class="stat__value">${disabled}</div></div>
    </div>
    <div class="section-title"><h2>Клиенты и ключи</h2><span class="muted">${visible.length}</span></div>
    <div class="field"><input class="input" id="clientSearch" placeholder="Поиск по имени или Telegram ID" value="${esc(state.cache.clientQ || "")}" /></div>
    <div class="rows">
      ${visible.length ? visible.map(clientRow).join("")
        : `<div class="empty">Пока пусто — создайте ключ на вкладке «Купить»</div>`}
    </div>`;
  const search = document.getElementById("clientSearch");
  search.addEventListener("change", () => { state.cache.clientQ = search.value.trim(); renderTab(); });
}
function clientRow(c) {
  const [label, cls] = SUB_STATUS[c.sub_status] || [c.sub_status, "pending"];
  const dl = daysLeft(c.expires_at);
  const left = dl == null ? "" : dl < 0 ? "истёк" : `${dl} дн.`;
  return `<div class="row row--clickable" data-client='${esc(JSON.stringify(c))}' title="Открыть управление ключом">
      <div class="row__main">
      <div class="row__title">${esc(c.name || "Без имени")} <span class="tag tag--${cls}">${label}</span></div>
      <div class="row__meta">
        ${c.short_code ? `код ${esc(c.short_code)} · ` : ""}до ${fmtDate(c.expires_at)}
        ${left ? " · " + left : ""} · 📱 ${c.device_limit}
      </div>
    </div>
    <div class="row__actions">
      ${c.connect_url ? `<button class="btn btn--sm" data-copy="${esc(c.connect_url)}">Ссылка</button>` : ""}
      <button class="btn btn--sm" data-client='${esc(JSON.stringify(c))}'>Открыть</button>
    </div>
  </div>`;
}
function showClientModal(c) {
  const url = c.connect_url || c.sub_url || "";
  const isAdmin = state.me && state.me.role === "super_admin";
  const back = modal(`
    <h3>${esc(c.name || "Клиент")}</h3>
    <p class="sub">
      ${(SUB_STATUS[c.sub_status] || [c.sub_status])[0]} · до ${fmtDate(c.expires_at)} · ${c.device_limit || 0} устр.
      ${c.plan_code ? ` · ${esc(c.plan_code)}` : ""}
      ${c.short_code ? ` · код ${esc(c.short_code)}` : ""}
      ${isAdmin && c.reseller_id ? ` · реселлер #${esc(c.reseller_id)}` : ""}
    </p>
    <p class="hint" style="margin-top:8px">
      Для проверки не импортируйте ссылку в свой VPN-клиент. Если слот уже занят ошибочно —
      откройте «Устройства» и отключите лишнее устройство.
    </p>
    ${c.short_code ? `<div class="field"><label>Короткий код для поиска</label><div class="codebox">${esc(c.short_code)}</div></div>` : ""}
    <div class="field"><label>Ссылка для клиента</label><div class="codebox">${esc(url || "—")}</div></div>
    <div class="modal__actions">
      <button class="btn btn--primary" data-copy="${esc(url)}">Скопировать</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn" id="renewClient">Продлить</button>
      <button class="btn" id="devicesClient">Устройства</button>
      <button class="btn btn--danger" id="revoke">Отозвать ключ</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--ghost" data-action="close">Закрыть</button>
    </div>`);
  const uuid = c.remnawave_uuid;
  back.querySelector("#renewClient").addEventListener("click", () => openRenewModal(c));
  back.querySelector("#devicesClient").addEventListener("click", () => openDevicesModal(c));
  document.getElementById("revoke").addEventListener("click", async () => {
    if (!uuid) return toast("Нет ключа", "err");
    if (!confirm("Отозвать ключ? Клиент потеряет доступ.")) return;
    try {
      await api(`/reseller/clients/${uuid}`, { method: "DELETE" });
      toast("Ключ отозван", "ok"); closeModal(); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}

async function openRenewModal(c) {
  const uuid = c.remnawave_uuid;
  if (!uuid) return toast("Нет ключа для продления", "err");
  const isAdmin = state.me && state.me.role === "super_admin";
  const tariffs = await api("/reseller/tariffs");
  const opts = tariffs.map((t) =>
    `<option value="${t.id}">${esc(t.name)} · ${rub(t.price_rub)} · ${t.duration_days} дн. · ${t.device_limit} устр.</option>`
  ).join("");
  modal(`
    <h3>Продлить клиента</h3>
    <p class="sub">${esc(c.name || "Клиент")} · текущая дата: ${fmtDate(c.expires_at)}. ${isAdmin ? "Админское продление выполняется без списания баланса." : "Списание будет с баланса реселлера."}</p>
    <div class="field"><label>Тариф продления</label><select class="select" id="renewTariff">${opts}</select></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="renewConfirm">${isAdmin ? "Продлить" : "Продлить и списать"}</button>
    </div>`);
  document.getElementById("renewConfirm").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Продлеваем…";
    try {
      const r = await api(`/reseller/clients/${uuid}/renew`, {
        method: "POST",
        body: { tariff_id: Number(document.getElementById("renewTariff").value) },
      });
      state.cache.balance = r.balance;
      toast(`Продлено до ${fmtDate(r.expires_at)}`, "ok");
      closeModal();
      renderTab();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Продлить и списать";
      toast(err.message, "err");
    }
  });
}

function deviceRow(d, uuid) {
  const hwid = d.hwid || d.HWID || d.deviceId || d.id || "";
  const title = [d.deviceModel, d.platform, d.os, d.model].filter(Boolean).join(" · ") || hwid || "Устройство";
  const appName = (d.userAgent || "").split("/")[0] || "";
  const meta = [appName, d.osVersion, d.updatedAt ? fmtDateTime(d.updatedAt) : "", hwid].filter(Boolean).join(" · ");
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(title)}</div>
      <div class="row__meta">${esc(meta)}</div>
    </div>
    <div class="row__actions">
      ${hwid ? `<button class="btn btn--sm btn--danger" data-devdel='${esc(JSON.stringify({ uuid, hwid }))}'>Отключить</button>` : ""}
    </div>
  </div>`;
}

async function renderDeviceManager(uuid) {
  const box = document.getElementById("devBox");
  if (!box) return;
  box.innerHTML = `<div class="empty">Загрузка устройств…</div>`;
  try {
    const d = await api(`/reseller/clients/${uuid}/devices`);
    const over = Number(d.count || 0) > Number(d.device_limit || 0);
    box.innerHTML = `
      <div class="field"><label>Лимит устройств</label>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn btn--sm" id="devMinus" type="button">−</button>
          <input class="input" id="devLimit" type="number" min="1" max="10" value="${d.device_limit || 1}" style="max-width:90px;text-align:center" />
          <button class="btn btn--sm" id="devPlus" type="button">＋</button>
          <button class="btn btn--sm btn--primary" id="devSave" type="button">Сохранить</button>
        </div>
      </div>
      <div class="section-title" style="margin:10px 0 6px">
        <h2 style="font-size:15px">Устройства <span class="muted">${d.count || 0}/${d.device_limit || 0}</span></h2></div>
      <div class="rows">
        ${(d.devices || []).length ? d.devices.map((x) => deviceRow(x, uuid)).join("")
          : `<div class="empty">Активных устройств нет.<br><span class="muted" style="font-size:12px">Hysteria/LTE подключения могут не отображаться как HWID. Основной лимит устройств контролируется через VLESS/Remnawave.</span></div>`}
      </div>
      ${over ? `<p class="hint" style="color:var(--danger);margin-top:8px">Устройств больше лимита — отключите лишние вручную.</p>` : ""}`;
    const inp = document.getElementById("devLimit");
    document.getElementById("devMinus").addEventListener("click", () => { inp.value = Math.max(1, (Number(inp.value) || 1) - 1); });
    document.getElementById("devPlus").addEventListener("click", () => { inp.value = Math.min(10, (Number(inp.value) || 1) + 1); });
    document.getElementById("devSave").addEventListener("click", async () => {
      const v = Math.max(1, Math.min(10, Number(inp.value) || 1));
      try {
        await api(`/reseller/clients/${uuid}`, { method: "PUT", body: { devices_limit: v } });
        toast("Лимит сохранён", "ok");
        renderDeviceManager(uuid);
      } catch (err) { toast(err.message, "err"); }
    });
  } catch (err) { box.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

async function disconnectDevice(uuid, hwid) {
  if (!confirm("Отключить это устройство? Слот освободится, устройство потеряет доступ при следующем подключении.")) return;
  try {
    await api(`/reseller/clients/${uuid}/devices/delete`, { method: "POST", body: { hwid } });
    toast("Устройство отключено", "ok");
    renderDeviceManager(uuid);
  } catch (err) { toast(err.message, "err"); }
}

function openDevicesModal(c) {
  const uuid = c.remnawave_uuid;
  if (!uuid) return toast("Нет ключа", "err");
  modal(`
    <h3>${esc(c.name || "Клиент")}</h3>
    <p class="sub">Лимит и подключённые устройства</p>
    <div id="devBox"><div class="empty">Загрузка…</div></div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--ghost" data-action="close">Закрыть</button>
    </div>`);
  renderDeviceManager(uuid);
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

// ── admin: subadmins ────────────────────────────────────────────────────────
async function viewSubadmins(view) {
  const list = await api("/admin/subadmins");
  view.innerHTML = `
    <div class="section-title"><div><h2>Субадмины</h2>
      <div class="muted" style="font-size:12.5px;margin-top:4px">Операционные аккаунты с отдельными ключами доступа</div></div>
      <button class="btn btn--primary btn--sm" data-action="add-subadmin">+ Создать</button></div>
    <div class="rows">
      ${list.length ? list.map(subadminRow).join("") : `<div class="empty">Субадминов пока нет</div>`}
    </div>`;
}

function subadminRow(item) {
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(item.name || "Без имени")}
        <span class="pill">Субадмин</span>
        ${item.is_blocked ? `<span class="tag tag--disabled">Блок</span>` : `<span class="tag tag--active">Активен</span>`}</div>
      <div class="row__meta">ID ${item.id} · tg ${(item.telegram_id != null ? item.telegram_id : "—")} · отдельный вход в портал</div>
    </div>
    <div class="row__actions"><button class="btn btn--sm" data-subadmin='${esc(JSON.stringify(item))}'>Управление</button></div>
  </div>`;
}

function openCreateSubadminModal() {
  modal(`
    <h3>Новый субадмин</h3>
    <p class="sub">Создайте понятный ключ входа или оставьте поле пустым — тогда система выпустит защищённый случайный ключ.</p>
    <div class="field"><label>Имя *</label><input class="input" id="saName" placeholder="Имя оператора" /></div>
    <div class="field"><label>Telegram ID (необязательно)</label><input class="input" id="saTg" type="number" placeholder="если знаете — привяжем" /></div>
    <div class="field"><label>Свой ключ доступа (необязательно)</label>
      <input class="input" id="saKey" maxlength="64" autocomplete="off" spellcheck="false" placeholder="например: support-team" />
      <div class="muted" style="font-size:12px;margin-top:7px">От 6 до 64 символов: латинские буквы, цифры, точка, дефис и подчёркивание.</div></div>
    <div class="modal__actions"><button class="btn btn--ghost" data-action="close">Отмена</button><button class="btn btn--primary" id="saSave">Создать</button></div>`);
  document.getElementById("saSave").addEventListener("click", async () => {
    const name = document.getElementById("saName").value.trim();
    const telegramId = document.getElementById("saTg").value.trim();
    const customKey = document.getElementById("saKey").value.trim();
    if (!name) return toast("Укажите имя", "err");
    if (customKey && customKey.length < 6) return toast("Ключ минимум 6 символов", "err");
    try {
      const result = await api("/admin/subadmins", { method: "POST", body: {
        name, telegram_id: telegramId ? Number(telegramId) : null, key: customKey || null,
      }});
      renderTab();
      modal(`<h3>Субадмин создан</h3><p class="sub">Передайте этот ключ сотруднику. Позже его можно безопасно заменить.</p>
        <div class="codebox">${esc(result.portal_access_key)}</div>
        <div class="modal__actions"><button class="btn btn--primary" data-copy="${esc(result.portal_access_key)}">Скопировать ключ</button><button class="btn btn--ghost" data-action="close">Готово</button></div>`);
    } catch (err) { toast(err.message, "err"); }
  });
}

function openSubadminManageModal(item) {
  modal(`<h3>${esc(item.name || "Субадмин")}</h3>
    <p class="sub">ID ${item.id} · tg ${(item.telegram_id != null ? item.telegram_id : "—")}</p>
    <div class="field"><label>Ключ доступа</label><div class="codebox">${esc(item.portal_access_key || "не задан")}</div></div>
    <div class="modal__actions">${item.portal_access_key ? `<button class="btn" data-copy="${esc(item.portal_access_key)}">Скопировать</button>` : ""}<button class="btn" id="saRotate">Случайный ключ</button></div>
    <div class="field" style="margin-top:12px"><label>Задать свой ключ</label>
      <input class="input" id="saCustomKey" maxlength="64" autocomplete="off" spellcheck="false" placeholder="например: support-team" />
      <div class="muted" style="font-size:12px;margin-top:7px">Старый ключ перестанет работать сразу после сохранения.</div></div>
    <div class="modal__actions"><button class="btn btn--primary" id="saSetKey">Сохранить свой ключ</button></div>
    <div class="modal__actions"><button class="btn btn--danger" id="saBlock">${item.is_blocked ? "Разблокировать" : "Заблокировать"}</button><button class="btn btn--ghost" data-action="close">Закрыть</button></div>`);
  async function applySubadminKey(key) {
    try {
      const result = await api(`/admin/resellers/${item.id}/key`, { method: "POST", body: { key } });
      renderTab();
      modal(`<h3>Ключ обновлён</h3><p class="sub">Передайте новый ключ субадмину. Старый ключ больше не работает.</p><div class="codebox">${esc(result.portal_access_key)}</div><div class="modal__actions"><button class="btn btn--primary" data-copy="${esc(result.portal_access_key)}">Скопировать ключ</button><button class="btn btn--ghost" data-action="close">Готово</button></div>`);
    } catch (err) { toast(err.message, "err"); }
  }
  document.getElementById("saRotate").addEventListener("click", async () => {
    if (!confirm("Создать новый случайный ключ? Старый ключ сразу перестанет работать.")) return;
    applySubadminKey(null);
  });
  document.getElementById("saSetKey").addEventListener("click", () => {
    const key = document.getElementById("saCustomKey").value.trim();
    if (key.length < 6) return toast("Ключ минимум 6 символов", "err");
    if (!confirm("Сохранить новый ключ? Старый ключ сразу перестанет работать.")) return;
    applySubadminKey(key);
  });
  document.getElementById("saBlock").addEventListener("click", async () => {
    try {
      await api(`/admin/resellers/${item.id}/block`, { method: "POST", body: { blocked: !item.is_blocked } });
      closeModal(); toast(item.is_blocked ? "Субадмин разблокирован" : "Субадмин заблокирован", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
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
        ${r.level ? `<span class="pill">${LEVEL_RU[r.level] || r.level}</span>` : ""}
        ${r.is_blocked ? `<span class="tag tag--disabled">Блок</span>` : ""}</div>
      <div class="row__meta">ID ${r.id} · tg ${(r.telegram_id != null ? r.telegram_id : "—")} · Баланс: ${rub(r.balance)}</div>
    </div>
    <div class="row__actions">
      <button class="btn btn--sm" data-manage='${esc(JSON.stringify(r))}'>Управление</button>
    </div>
  </div>`;
}

function openResellerManageModal(r) {
  const curKey = r.portal_access_key || "";
  modal(`
    <h3>${esc(r.name || "Реселлер")}</h3>
    <p class="sub">ID ${r.id} · tg ${(r.telegram_id != null ? r.telegram_id : "—")} · Баланс: <b>${rub(r.balance)}</b></p>

    <div class="field"><label>Текущий ключ доступа</label>
      <div class="codebox">${esc(curKey || "не задан")}</div></div>
    <div class="modal__actions">
      ${curKey ? `<button class="btn" data-copy="${esc(curKey)}">Скопировать</button>` : ""}
      <button class="btn" id="mGenKey">Случайный ключ</button>
    </div>
    <div class="field" style="margin-top:12px"><label>Задать свой ключ</label>
      <input class="input" id="mCustomKey" placeholder="придумайте ключ (мин. 6 символов)" /></div>
    <div class="modal__actions">
      <button class="btn btn--primary" id="mSetKey">Сохранить ключ</button>
    </div>

    <div class="field" style="margin-top:16px"><label>Уровень</label>
      <select class="select" id="mLevel">
        <option value="1" ${r.level == 1 ? "selected" : ""}>Start</option>
        <option value="2" ${r.level == 2 ? "selected" : ""}>Partner</option>
        <option value="3" ${r.level == 3 ? "selected" : ""}>VIP</option>
      </select></div>
    <div class="field"><label>Изменить баланс, ₽ (минус — списать)</label>
      <input class="input" id="mAmt" type="number" placeholder="напр. 5000 или -1000" /></div>
    <div class="modal__actions">
      <button class="btn btn--danger" id="mBlock">${r.is_blocked ? "Разблокировать" : "Заблокировать"}</button>
      <button class="btn btn--primary" id="mApply">Применить</button>
    </div>
    <div class="modal__actions" style="margin-top:10px">
      <button class="btn btn--ghost" data-action="close">Закрыть</button>
    </div>`);

  async function applyKey(keyVal) {
    try {
      const res = await api(`/admin/resellers/${r.id}/key`, { method: "POST", body: { key: keyVal } });
      renderTab();
      modal(`
        <h3>Ключ доступа обновлён</h3>
        <p class="sub">${esc(r.name || "Реселлер")} — ключ для входа в портал:</p>
        <div class="codebox">${esc(res.portal_access_key)}</div>
        <div class="modal__actions">
          <button class="btn btn--primary" data-copy="${esc(res.portal_access_key)}">Скопировать</button>
          <button class="btn btn--ghost" data-action="close">Готово</button>
        </div>`);
    } catch (err) { toast(err.message, "err"); }
  }
  document.getElementById("mGenKey").addEventListener("click", () => {
    if (!confirm("Сгенерировать новый случайный ключ? Старый перестанет работать.")) return;
    applyKey(null);
  });
  document.getElementById("mSetKey").addEventListener("click", () => {
    const k = document.getElementById("mCustomKey").value.trim();
    if (k.length < 6) return toast("Ключ минимум 6 символов", "err");
    applyKey(k);
  });
  document.getElementById("mApply").addEventListener("click", async () => {
    try {
      const level = Number(document.getElementById("mLevel").value);
      if (level !== r.level) {
        await api(`/admin/resellers/${r.id}/level`, { method: "POST", body: { level } });
      }
      const amt = Math.round(Number(document.getElementById("mAmt").value));
      if (amt) {
        await api(`/admin/resellers/${r.id}/balance`, {
          method: "POST", body: { amount: amt, comment: "Корректировка из админки" },
        });
      }
      closeModal(); toast("Сохранено", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
  document.getElementById("mBlock").addEventListener("click", async () => {
    try {
      await api(`/admin/resellers/${r.id}/block`, { method: "POST", body: { blocked: !r.is_blocked } });
      closeModal(); toast(r.is_blocked ? "Разблокирован" : "Заблокирован", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
}

// ── admin: dashboard ─────────────────────────────────────────────────────────
async function viewAdminDashboard(view) {
  const d = await api("/admin/dashboard");
  view.innerHTML = `
    <div class="grid grid--stats">
      <div class="card stat"><div class="stat__label">Выручка</div>
        <div class="stat__value accent">${rub(d.revenue_rub)}</div>
        <div class="stat__sub">оплачено всего</div></div>
      <div class="card stat"><div class="stat__label">Активные ключи</div>
        <div class="stat__value">${d.active_subs}</div></div>
      <div class="card stat"><div class="stat__label">Реселлеры</div>
        <div class="stat__value">${d.resellers}</div></div>
      <div class="card stat"><div class="stat__label">Клиенты</div>
        <div class="stat__value">${d.clients}</div></div>
      <div class="card stat"><div class="stat__label">Баланс реселлеров</div>
        <div class="stat__value">${rub(d.reseller_balance_rub)}</div></div>
    </div>
    <div class="section-title"><h2>Последние платежи</h2></div>
    <div class="rows">
      ${(d.recent_payments || []).length ? d.recent_payments.map((p) => `<div class="row">
        <div class="row__main"><div class="row__title">${esc(p.provider || "")} · ${esc(p.payload || "")}</div>
          <div class="row__meta">${fmtDate(p.date)}</div></div>
        <div class="amount-pos">+${rub(p.amount)}</div></div>`).join("")
        : `<div class="empty">Платежей пока нет</div>`}
    </div>`;
}

// ── admin: tariffs ───────────────────────────────────────────────────────────
async function viewTariffs(view) {
  const list = await api("/admin/tariffs");
  view.innerHTML = `
    <div class="section-title"><h2>Тарифы реселлеров</h2>
      <button class="btn btn--primary btn--sm" data-action="add-tariff">+ Тариф</button></div>
    <p class="muted" style="margin:0 2px 14px;font-size:13px">Цена = сколько списывается с баланса реселлера за ключ.</p>
    <div class="rows">
      ${list.length ? list.map((t) => `<div class="row">
        <div class="row__main">
          <div class="row__title">${esc(t.name)} ${t.is_active ? "" : '<span class="tag tag--disabled">выкл</span>'}</div>
          <div class="row__meta">${rub(t.price_rub)} · ${t.duration_days} дн. · ${t.device_limit} устр. · ${t.traffic_limit_gb ? t.traffic_limit_gb + " ГБ" : "∞"}</div>
        </div>
        <div class="row__actions">
          <button class="btn btn--sm" data-tariff-edit='${esc(JSON.stringify(t))}'>Изменить</button>
        </div></div>`).join("") : `<div class="empty">Тарифов нет — создайте первый</div>`}
    </div>`;
}
function openTariffModal(t) {
  const e = t || { name: "", price_rub: 100, duration_days: 30, device_limit: 1, traffic_limit_gb: 0, is_active: true };
  modal(`
    <h3>${t ? "Изменить тариф" : "Новый тариф"}</h3>
    <div class="field"><label>Название *</label><input class="input" id="tfName" value="${esc(e.name)}" placeholder="Start · 1 месяц" /></div>
    <div class="field"><label>Цена реселлеру, ₽ *</label><input class="input" id="tfPrice" type="number" value="${e.price_rub}" /></div>
    <div class="field"><label>Срок, дней *</label><input class="input" id="tfDays" type="number" value="${e.duration_days}" /></div>
    <div class="field"><label>Устройств</label><input class="input" id="tfDev" type="number" value="${e.device_limit}" /></div>
    <div class="field"><label>Трафик, ГБ (0 = безлимит)</label><input class="input" id="tfTraf" type="number" value="${e.traffic_limit_gb}" /></div>
    <div class="field"><label style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="tfActive" ${e.is_active ? "checked" : ""}/> Активен</label></div>
    <div class="modal__actions">
      ${t ? `<button class="btn btn--danger" id="tfDel">Удалить</button>` : `<button class="btn btn--ghost" data-action="close">Отмена</button>`}
      <button class="btn btn--primary" id="tfSave">Сохранить</button>
    </div>`);
  document.getElementById("tfSave").addEventListener("click", async () => {
    const body = {
      name: document.getElementById("tfName").value.trim(),
      price_rub: Math.round(Number(document.getElementById("tfPrice").value)) || 0,
      duration_days: Number(document.getElementById("tfDays").value) || 30,
      device_limit: Number(document.getElementById("tfDev").value) || 1,
      traffic_limit_gb: Number(document.getElementById("tfTraf").value) || 0,
      is_active: document.getElementById("tfActive").checked,
    };
    if (!body.name) return toast("Укажите название", "err");
    try {
      if (t) await api(`/admin/tariffs/${t.id}`, { method: "PATCH", body });
      else await api("/admin/tariffs", { method: "POST", body });
      closeModal(); toast("Сохранено", "ok"); renderTab();
    } catch (err) { toast(err.message, "err"); }
  });
  if (t) {
    document.getElementById("tfDel").addEventListener("click", async () => {
      if (!confirm("Удалить тариф?")) return;
      try { await api(`/admin/tariffs/${t.id}`, { method: "DELETE" }); closeModal(); toast("Удалён", "ok"); renderTab(); }
      catch (err) { toast(err.message, "err"); }
    });
  }
}

// ── admin: all keys ──────────────────────────────────────────────────────────
async function viewAllKeys(view) {
  const q = state.cache.keysQ || "";
  const keys = await api(`/admin/keys?q=${encodeURIComponent(q)}`);
  const active = keys.filter((k) => k.sub_status === "active" || k.status === "active").length;
  const expiring = keys.filter((k) => {
    const d = daysLeft(k.expires_at);
    return (k.sub_status === "active" || k.status === "active") && d !== null && d >= 0 && d <= 5;
  }).length;
  const disabled = keys.length - active;
  view.innerHTML = `
    <div class="grid grid--stats">
      <div class="card stat"><div class="stat__label">Всего ключей</div><div class="stat__value">${keys.length}</div></div>
      <div class="card stat"><div class="stat__label">Активные</div><div class="stat__value accent">${active}</div></div>
      <div class="card stat"><div class="stat__label">Истекают до 5 дней</div><div class="stat__value">${expiring}</div></div>
      <div class="card stat"><div class="stat__label">Отключены / истекли</div><div class="stat__value">${disabled}</div></div>
    </div>
    <div class="section-title"><h2>Все ключи <span class="muted">${keys.length}</span></h2>
      <button class="btn btn--primary btn--sm" data-action="add-key">+ Создать ключ</button></div>
    <div class="field"><input class="input" id="keySearch" placeholder="Поиск по имени, Telegram ID, короткому коду или ссылке" value="${esc(q)}" /></div>
    <div class="rows">
      ${keys.length ? keys.map(adminKeyRow).join("") : `<div class="empty">Ключей нет</div>`}
    </div>`;
  const search = document.getElementById("keySearch");
  search.addEventListener("change", () => { state.cache.keysQ = search.value.trim(); renderTab(); });
}

function adminKeyRow(k) {
  const [label, cls] = SUB_STATUS[k.status] || SUB_STATUS[k.sub_status] || [k.status || "none", "pending"];
  const dl = daysLeft(k.expires_at);
  const left = dl == null ? "" : dl < 0 ? "истёк" : `${dl} дн.`;
  const name = k.client || k.name || "Без имени";
  return `<div class="row row--clickable" data-client='${esc(JSON.stringify(k))}' title="Открыть управление ключом">
    <div class="row__main">
      <div class="row__title">${esc(name)} <span class="tag tag--${cls}">${label}</span></div>
      <div class="row__meta">
        ${k.short_code ? `код ${esc(k.short_code)} · ` : ""}tg ${(k.telegram_id != null ? esc(k.telegram_id) : "—")} · до ${fmtDate(k.expires_at)}${left ? " · " + left : ""}
        · 📱 ${k.device_limit || 0}${k.plan_code ? " · " + esc(k.plan_code) : ""}${k.reseller_id ? " · реселлер #" + esc(k.reseller_id) : ""}
      </div>
    </div>
    <div class="row__actions">
      ${k.connect_url ? `<button class="btn btn--sm" data-copy="${esc(k.connect_url)}">Ссылка</button>` : ""}
      <button class="btn btn--sm" data-client='${esc(JSON.stringify(k))}'>Открыть</button>
      ${k.uuid && k.status === "active" ? `<button class="btn btn--sm btn--danger" data-key-disable="${esc(k.uuid)}">Отключить</button>` : ""}
    </div>
  </div>`;
}
async function adminDisableKey(uuid) {
  if (!confirm("Отключить ключ? Клиент потеряет доступ.")) return;
  try { await api(`/admin/keys/${uuid}/disable`, { method: "POST" }); toast("Ключ отключён", "ok"); renderTab(); }
  catch (err) { toast(err.message, "err"); }
}
async function openAdminCreateKeyModal() {
  const tariffs = await api("/admin/tariffs").catch(() => []);
  const opts = tariffs.map((t) =>
    `<option value="${t.id}">${esc(t.name)} (${t.duration_days} дн., ${t.device_limit} устр.)</option>`).join("");
  modal(`
    <h3>Создать ключ</h3>
    <p class="sub">Ключ выдаётся напрямую — без баланса и без лимита.</p>
    <div class="field"><label>Имя / метка (необязательно)</label>
      <input class="input" id="akName" placeholder="напр. Личный" /></div>
    <div class="field"><label>Тариф</label>
      <select class="select" id="akTariff"><option value="">— свой срок —</option>${opts}</select></div>
    <div class="field"><label>Свой срок (если без тарифа): дней / устройств</label>
      <div style="display:flex;gap:8px">
        <input class="input" id="akDays" type="number" placeholder="дней" value="30" />
        <input class="input" id="akDevices" type="number" placeholder="устройств" value="5" />
      </div></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" data-action="close">Отмена</button>
      <button class="btn btn--primary" id="akCreate">Создать ключ</button>
    </div>`);
  document.getElementById("akCreate").addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Создаём…";
    const tariffId = document.getElementById("akTariff").value;
    const body = { client_name: document.getElementById("akName").value.trim() };
    if (tariffId) {
      body.tariff_id = Number(tariffId);
    } else {
      body.days = Number(document.getElementById("akDays").value) || 30;
      body.devices = Number(document.getElementById("akDevices").value) || 1;
    }
    try {
      const r = await api("/admin/keys/create", { method: "POST", body });
      closeModal(); toast("Ключ создан", "ok"); renderTab();
      showSubModal(r.connect_url || r.sub_url);
    } catch (err) {
      btn.disabled = false; btn.textContent = "Создать ключ";
      toast(err.message, "err");
    }
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

// ── admin: audit ─────────────────────────────────────────────────────────────
async function viewAudit(view) {
  const rows = await api("/admin/audit");
  view.innerHTML = `
    <div class="section-title"><h2>Журнал действий</h2><span class="muted">${rows.length}</span></div>
    <p class="muted" style="margin:0 2px 14px;font-size:13px">Финансы, ключи, продления, блокировки и изменения тарифов — без секретов и токенов.</p>
    <div class="rows">
      ${rows.length ? rows.map(auditRow).join("") : `<div class="empty">Аудит пока пуст</div>`}
    </div>`;
}

function auditRow(a) {
  const d = a.details || {};
  const detail = Object.entries(d).slice(0, 6).map(([k, v]) => {
    const value = typeof v === "object" ? JSON.stringify(v) : String(v == null ? "" : v);
    return `${k}: ${value}`;
  }).join(" · ");
  return `<div class="row">
    <div class="row__main">
      <div class="row__title">${esc(a.action)} <span class="tag tag--pending">${esc(a.entity_type || "")}</span></div>
      <div class="row__meta">${fmtDate(a.created_at)} · ${esc(a.actor || "")}${detail ? " · " + esc(detail) : ""}</div>
    </div>
  </div>`;
}

// ── delegation ─────────────────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const t = e.target.closest(
    "[data-tab],[data-action],[data-buy],[data-client],[data-copy],[data-openurl],[data-manage],[data-subadmin],[data-tariff-edit],[data-key-disable],[data-devdel]"
  );
  if (!t) return;
  if (t.dataset.openurl) return window.open(t.dataset.openurl, "_blank");
  if (t.dataset.tab) { state.tab = t.dataset.tab; renderShell(); return; }
  const a = t.dataset.action;
  if (a === "logout") { clearKey(); state.me = null; renderLogin(); return; }
  if (a === "close") return closeModal();
  if (a === "add-reseller") return openCreateResellerModal();
  if (a === "add-subadmin") return openCreateSubadminModal();
  if (a === "add-tariff") return openTariffModal(null);
  if (a === "add-key") return openAdminCreateKeyModal();
  if (t.dataset.copy !== undefined && t.dataset.copy !== "") return copy(t.dataset.copy, "Скопировано");
  if (t.dataset.buy) return openBuyModal(JSON.parse(t.dataset.buy));
  if (t.dataset.client) return showClientModal(JSON.parse(t.dataset.client));
  if (t.dataset.manage) return openResellerManageModal(JSON.parse(t.dataset.manage));
  if (t.dataset.subadmin) return openSubadminManageModal(JSON.parse(t.dataset.subadmin));
  if (t.dataset.tariffEdit) return openTariffModal(JSON.parse(t.dataset.tariffEdit));
  if (t.dataset.keyDisable) return adminDisableKey(t.dataset.keyDisable);
  if (t.dataset.devdel) { const o = JSON.parse(t.dataset.devdel); return disconnectDevice(o.uuid, o.hwid); }
});

// ── boot ───────────────────────────────────────────────────────────────────
async function boot() {
  if (!getKey()) return renderLogin();
  try { state.me = await api("/portal/me"); renderShell(); }
  catch (err) { if (err.message !== "unauthorized") renderFatal(err.message); }
}
window.addEventListener("error", (event) => {
  renderFatal(event.message || "Ошибка интерфейса");
});
window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason && (event.reason.message || event.reason);
  renderFatal(reason || "Ошибка загрузки данных");
});
boot().catch((err) => renderFatal(err && err.message));
