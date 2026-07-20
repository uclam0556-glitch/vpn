const fallbackTelegram = {
  initData: "", initDataUnsafe: {},
  ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {}, setBottomBarColor() {},
  openLink(url) { window.open(url, "_blank", "noopener"); },
  openTelegramLink(url) { window.open(url, "_blank", "noopener"); },
  showAlert(message) { window.alert(message); },
  showConfirm(message, callback) { callback(window.confirm(message)); },
  HapticFeedback: null,
};
const tg = window.Telegram?.WebApp || fallbackTelegram;
const API_BASE = "/api/tma";
const API_TIMEOUT = 15000;
const state = { me: null, plans: [], devices: null, referrals: null, payments: null, view: "home" };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"></use></svg>`;

function setupTelegram() {
  tg.ready(); tg.expand();
  try { tg.setHeaderColor("#070913"); tg.setBackgroundColor("#060811"); tg.setBottomBarColor?.("#080b16"); tg.disableVerticalSwipes?.(); } catch (_) { /* older Telegram */ }
}
function haptic(type = "light") {
  try { tg.HapticFeedback?.impactOccurred(type); } catch (_) { /* unavailable */ }
}
function notify(type = "success") {
  try { tg.HapticFeedback?.notificationOccurred(type); } catch (_) { /* unavailable */ }
}
function toast(message, kind = "") {
  const node = $("#toast"); node.textContent = message; node.className = `toast show ${kind}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { node.className = "toast"; }, 2600);
}

async function api(endpoint, { method = "GET", body } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method, signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": tg.initData || "" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Сервис временно недоступен");
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Сервер отвечает слишком долго. Попробуйте ещё раз.");
    throw error;
  } finally { clearTimeout(timeout); }
}

function showView(name) {
  if (!$( `#view-${name}`)) return;
  state.view = name;
  $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === name));
  $$(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.nav === name));
  $("#content").scrollTop = 0; window.scrollTo(0, 0); haptic();
  if (name === "subscription") loadDevices();
  if (name === "tariffs") { renderPlans(); loadPayments(); }
  if (name === "bonus") loadReferrals();
}

function pluralDays(value) {
  const n = Math.abs(value) % 100; const n1 = n % 10;
  if (n > 10 && n < 20) return "дней";
  if (n1 > 1 && n1 < 5) return "дня";
  if (n1 === 1) return "день";
  return "дней";
}
function daysLeft(timestamp) { return timestamp ? Math.max(0, Math.ceil((timestamp * 1000 - Date.now()) / 86400000)) : 0; }
function dateText(timestamp) { return timestamp ? new Date(timestamp * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Не указано"; }
function shortDate(value) { return value ? new Date(value).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "—"; }
function formatBytes(bytes) {
  if (!bytes) return "0 ГБ";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}
function formatMoney(value) { return `${Number(value || 0).toLocaleString("ru-RU")} ₽`; }

function renderMe() {
  const data = state.me; const active = data.status === "active"; const days = daysLeft(data.expire_at);
  const firstName = (data.full_name || tg.initDataUnsafe?.user?.first_name || "").trim().split(/\s+/)[0];
  $("#welcome-kicker").textContent = firstName ? `Здравствуйте, ${firstName}` : "Добро пожаловать";
  $("#welcome-name").textContent = active ? "Ваш интернет защищён" : "Подключите безопасный интернет";
  const statusPill = $("#status-pill"); statusPill.className = `status-pill ${active ? "active" : "inactive"}`;
  statusPill.innerHTML = `<i></i><span>${active ? "Подписка активна" : "Нет активной подписки"}</span>`;
  $("#days-pill").textContent = active ? `${days} ${pluralDays(days)}` : "Нужен тариф";
  $("#hero-title").textContent = active ? "VPN готов к работе" : "Оформите подписку";
  $("#hero-subtitle").textContent = active ? "Выберите приложение и подключайтесь" : "Доступ активируется сразу после оплаты";
  $("#home-devices").textContent = `${data.active_devices || 0} / ${data.device_limit || 0}`;
  $("#home-traffic").textContent = data.data_limit ? formatBytes(data.data_limit) : "∞";
  $("#home-health").textContent = data.health_status === "healthy" ? "Отлично" : (active ? "Активна" : "—");
  const connect = $("#home-connect"); connect.classList.remove("loading");
  connect.querySelector("span").textContent = active ? "Подключить VPN" : "Выбрать тариф";

  $("#plan-name").textContent = data.plan_name || (active ? "HamaliVPN" : "Тариф не выбран");
  $("#plan-status").textContent = active ? "Активна" : "Неактивна";
  $("#plan-status").style.color = active ? "var(--green)" : "var(--red)";
  $("#plan-expiry").textContent = active ? dateText(data.expire_at) : "Подписки пока нет";
  $("#plan-days").textContent = active ? `${days} ${pluralDays(days)}` : "—";
  $("#device-usage").textContent = `${data.active_devices || 0} из ${data.device_limit || 0}`;
  $("#device-progress").style.width = `${Math.min(100, (data.active_devices || 0) / Math.max(1, data.device_limit || 1) * 100)}%`;
  $("#plan-traffic").textContent = data.data_limit ? formatBytes(data.data_limit) : "Безлимит";
  $("#plan-health").textContent = data.health_status === "healthy" ? "Работает" : (active ? "Доступна" : "—");
  $("#subscription-connect").disabled = !active;
}

function openConnect() {
  if (!state.me || state.me.status !== "active") return showView("tariffs");
  showSheet(`
    <h2 class="sheet-title">Подключить VPN</h2><p class="sheet-subtitle">Выберите удобный способ. Персональная страница сама предложит Happ или Incy для вашего устройства.</p>
    <div class="sheet-options">
      <button class="sheet-option primary" data-sheet-action="connect-page"><span>${icon("bolt")}</span><div><b>Подключить автоматически</b><small>Открыть пошаговую настройку в 1 клик</small></div>${icon("arrow")}</button>
      <button class="sheet-option" data-sheet-action="copy-sub"><span>${icon("copy")}</span><div><b>Скопировать подписку</b><small>Для ручного импорта в VPN-приложение</small></div>${icon("arrow")}</button>
      <button class="sheet-option" data-sheet-action="devices"><span>${icon("phone")}</span><div><b>Управление устройствами</b><small>Посмотреть или отключить старое устройство</small></div>${icon("arrow")}</button>
    </div>`);
}
function showSheet(html) { $("#sheet-content").innerHTML = html; $("#sheet-overlay").classList.add("active"); $("#sheet-overlay").setAttribute("aria-hidden", "false"); haptic("medium"); }
function closeSheet() { $("#sheet-overlay").classList.remove("active"); $("#sheet-overlay").setAttribute("aria-hidden", "true"); }
function openLink(url, telegram = false) { if (!url) return; telegram ? tg.openTelegramLink(url) : tg.openLink(url, { try_instant_view: false }); }
async function copyText(value, message = "Скопировано") {
  try { await navigator.clipboard.writeText(value); toast(message, "success"); notify("success"); }
  catch (_) { toast("Не удалось скопировать", "error"); }
}

function renderPlans() {
  const root = $("#plans-list"); if (!state.plans.length) return;
  root.innerHTML = state.plans.map((plan) => `<article class="tariff-card ${plan.popular ? "popular" : ""}">
    ${plan.popular ? '<span class="popular-label">Выгодно</span>' : ""}
    <div class="tariff-card__top"><div><h3>${esc(plan.name)}</h3><p>${plan.days} ${pluralDays(plan.days)} защищённого интернета</p></div><div class="tariff-price"><strong>${formatMoney(plan.price)}</strong><small>за весь срок</small></div></div>
    <div class="tariff-features"><span>${plan.devices} устр.</span><span>Безлимитный трафик</span><span>Все локации</span></div>
    <button class="buy-button" data-buy-plan="${esc(plan.code)}">Выбрать и оплатить</button></article>`).join("");
}
async function buyPlan(code, button) {
  if (!state.me?.payment_available) return toast("Оплата временно недоступна — напишите в поддержку", "error");
  button.disabled = true; const old = button.textContent; button.textContent = "Создаём платёж…"; haptic("medium");
  try { const result = await api(`/payments/${encodeURIComponent(code)}`, { method: "POST" }); notify("success"); openLink(result.url); setTimeout(() => loadPayments(true), 800); }
  catch (error) { toast(error.message, "error"); notify("error"); }
  finally { button.disabled = false; button.textContent = old; }
}
async function loadPayments(force = false) {
  if (state.payments && !force) return renderPayments();
  try { state.payments = await api("/payments"); renderPayments(); }
  catch (error) { $("#payments-list").innerHTML = `<div class="empty-state compact"><span>${esc(error.message)}</span></div>`; }
}
function renderPayments() {
  const root = $("#payments-list"); const items = state.payments || [];
  if (!items.length) { root.innerHTML = `<div class="empty-state compact">${icon("card")}<span>Платежей пока нет</span></div>`; return; }
  const statuses = { paid: "Оплачено", pending: "Ожидает", cancelled: "Отменено", expired: "Истёк" };
  root.innerHTML = items.map((item) => `<div class="payment-row"><span class="payment-symbol">${icon("card")}</span><div class="payment-row__main"><b>${esc(item.plan_name || "Подписка")}</b><small>${shortDate(item.created_at)} · Platega</small></div><div class="payment-row__amount"><b>${formatMoney(item.amount)}</b><small class="${esc(item.status)}">${statuses[item.status] || esc(item.status)}</small></div></div>`).join("");
}

async function loadDevices(force = false) {
  if (state.devices && !force) return renderDevices();
  $("#devices-list").innerHTML = '<div class="skeleton-row"></div><div class="skeleton-row"></div>';
  try { state.devices = await api("/devices"); renderDevices(); }
  catch (error) { $("#devices-list").innerHTML = `<div class="empty-state compact"><span>${esc(error.message)}</span></div>`; }
}
function renderDevices() {
  const root = $("#devices-list"); const devices = state.devices || [];
  if (!devices.length) { root.innerHTML = `<div class="empty-state compact">${icon("phone")}<span>Активных устройств пока нет</span></div>`; return; }
  root.innerHTML = devices.map((device) => `<div class="device-row"><span class="device-symbol">${icon("phone")}</span><div class="device-row__main"><b>${esc(device.name || device.platform || "Устройство")}</b><small>${device.platform ? esc(device.platform) + " · " : ""}подключено ${device.activated_at ? dateText(device.activated_at) : "недавно"}</small></div><button class="danger-button" data-revoke-device="${esc(device.id)}">Отключить</button></div>`).join("");
}
function confirmAction(message) { return new Promise((resolve) => tg.showConfirm ? tg.showConfirm(message, resolve) : resolve(window.confirm(message))); }
async function revokeDevice(id, button) {
  if (!await confirmAction("Отключить это устройство? Для повторного подключения потребуется импортировать подписку заново.")) return;
  button.disabled = true;
  try { await api(`/devices/${encodeURIComponent(id)}`, { method: "DELETE" }); state.devices = (state.devices || []).filter((item) => String(item.id) !== String(id)); state.me.active_devices = Math.max(0, Number(state.me.active_devices || 0) - 1); renderMe(); renderDevices(); toast("Устройство отключено", "success"); notify("success"); }
  catch (error) { button.disabled = false; toast(error.message, "error"); notify("error"); }
}

async function loadReferrals(force = false) {
  if (state.referrals && !force) return renderReferrals();
  try { state.referrals = await api("/referrals"); renderReferrals(); }
  catch (error) { toast(error.message, "error"); }
}
function renderReferrals() {
  const data = state.referrals; if (!data) return;
  $("#ref-balance").textContent = formatMoney(data.balance); $("#ref-count").textContent = data.total_referrals; $("#ref-earned").textContent = formatMoney(data.total_earned);
  const refLink = `https://t.me/${data.bot_username}?start=ref_${state.me.telegram_id}`; $("#ref-link").textContent = refLink; $("#ref-link").dataset.value = refLink;
  const levels = [{ from: 0, to: 5, name: "Старт" }, { from: 5, to: 15, name: "Партнёр" }, { from: 15, to: 50, name: "Профи" }, { from: 50, to: 100, name: "Амбассадор" }];
  const level = levels.find((item) => data.total_referrals < item.to) || levels.at(-1); const progress = Math.min(100, Math.max(0, (data.total_referrals - level.from) / (level.to - level.from) * 100));
  $("#level-name").textContent = level.name; $("#level-next").textContent = data.total_referrals >= 100 ? "Максимум" : `ещё ${Math.max(0, level.to - data.total_referrals)}`; $("#level-progress").style.width = `${progress}%`;
  const withdraw = $("#withdraw-button"); withdraw.disabled = data.balance < data.minimum_withdrawal || Boolean(data.pending_withdrawal);
  $("#withdraw-hint").textContent = data.pending_withdrawal ? `Заявка на ${formatMoney(data.pending_withdrawal.amount)} уже обрабатывается` : `Минимальная сумма вывода — ${formatMoney(data.minimum_withdrawal)}`;
}
async function withdrawFunds(button) {
  if (!state.referrals) return;
  if (!await confirmAction(`Отправить заявку на вывод ${formatMoney(state.referrals.balance)}?`)) return;
  button.disabled = true;
  try { await api("/withdraw", { method: "POST" }); toast("Заявка на вывод создана", "success"); notify("success"); state.referrals = null; await loadReferrals(true); }
  catch (error) { button.disabled = false; toast(error.message, "error"); notify("error"); }
}

function supportUrl() { return `https://t.me/${String(state.me?.support_username || "Hamali_Support").replace(/^@/, "")}`; }
function showFatal(message) {
  showSheet(`<h2 class="sheet-title">Не удалось загрузить кабинет</h2><p class="sheet-subtitle">${esc(message)}</p><div class="sheet-options"><button class="sheet-option primary" data-sheet-action="retry"><span>${icon("refresh")}</span><div><b>Попробовать снова</b><small>Повторно запросить данные кабинета</small></div>${icon("arrow")}</button><button class="sheet-option" data-sheet-action="support"><span>${icon("chat")}</span><div><b>Написать в поддержку</b><small>Поможем восстановить доступ</small></div>${icon("arrow")}</button></div>`);
}

async function loadApp() {
  try {
    const [me, plans] = await Promise.all([api("/me"), api("/plans")]); state.me = me; state.plans = plans; renderMe(); renderPlans();
  } catch (error) { showFatal(error.message); }
}

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-nav]"); if (nav) return showView(nav.dataset.nav);
  const buy = event.target.closest("[data-buy-plan]"); if (buy) return buyPlan(buy.dataset.buyPlan, buy);
  const revoke = event.target.closest("[data-revoke-device]"); if (revoke) return revokeDevice(revoke.dataset.revokeDevice, revoke);
  const legal = event.target.closest("[data-link]"); if (legal) return openLink(legal.dataset.link);
  const sheetAction = event.target.closest("[data-sheet-action]");
  if (sheetAction) {
    const action = sheetAction.dataset.sheetAction;
    if (action === "connect-page") { closeSheet(); return openLink(state.me?.connect_url); }
    if (action === "copy-sub") { closeSheet(); return copyText(state.me?.raw_url || "", "Ссылка подписки скопирована"); }
    if (action === "devices") { closeSheet(); return showView("subscription"); }
    if (action === "retry") { closeSheet(); return loadApp(); }
    if (action === "support") return openLink(supportUrl(), true);
  }
});
$("#home-connect").addEventListener("click", openConnect); $("#power-button").addEventListener("click", openConnect); $("#subscription-connect").addEventListener("click", openConnect);
$("#refresh-devices").addEventListener("click", () => loadDevices(true));
$("#withdraw-button").addEventListener("click", (event) => withdrawFunds(event.currentTarget));
$("#copy-ref").addEventListener("click", () => copyText($("#ref-link").dataset.value || "", "Реферальная ссылка скопирована"));
$("#share-ref").addEventListener("click", () => { const url = $("#ref-link").dataset.value || ""; openLink(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent("Попробуй HamaliVPN — быстрый и удобный VPN. По моей ссылке тебя ждёт бонус:")}`, true); });
$("#open-support").addEventListener("click", () => openLink(supportUrl(), true)); $("#header-support").addEventListener("click", () => showView("support"));
$("#sheet-close").addEventListener("click", closeSheet); $("#sheet-overlay").addEventListener("click", (event) => { if (event.target.id === "sheet-overlay") closeSheet(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSheet(); });

setupTelegram(); loadApp();
