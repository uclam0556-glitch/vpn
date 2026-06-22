# HamaliVpn — деплой бота и автоматизации

Полный путь: панель Remnawave + бот Bedolaga на одном управляющем сервере, оплата Platega + Telegram Stars, выдача подписок с автоподключением. VPN-ноды подключаем позже.

```
Управляющий VPS
 ├─ Remnawave (панель)         panel.hamalivpn.com
 ├─ Bedolaga (бот + вебадминка) bot.hamalivpn.com  (:8080 за Caddy)
 ├─ PostgreSQL + Redis          (внутренние)
 └─ Caddy (авто-SSL, reverse proxy)
Лендинг (отдельно)             hamalivpn.com → Cloudflare Pages
```

---

## 0. Что нужно подготовить

| Что | Где взять | Секрет? |
|---|---|---|
| Управляющий VPS (2 vCPU / 2–4 ГБ / Ubuntu 22.04+) | любой чистый хостер | — |
| Домен `hamalivpn.com` + поддомены `panel.`, `bot.` | регистратор | — |
| `BOT_TOKEN` | @BotFather | **да** |
| `ADMIN_IDS` (твой Telegram ID) | @userinfobot | нет |
| `@username` бота | @BotFather | нет |
| Platega `MERCHANT_ID` + `SECRET` | кабинет Platega | **да** |

DNS A-записи `panel`, `bot`, `@` → IP управляющего VPS.

---

## 1. Подготовка сервера

```bash
ssh root@SERVER_IP
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh        # Docker + compose plugin
apt -y install git ufw
ufw allow 22,80,443/tcp && ufw --force enable
```

---

## 2. Панель Remnawave

Установить по официальной инструкции (команда инсталлятора — на **docs.rw**, сверить актуальную):

1. Запустить установщик панели, поднять на `panel.hamalivpn.com` (SSL ставится автоматически).
2. Открыть панель → создать **первого админа**.
3. Settings → **API Tokens** → создать токен → это `REMNAWAVE_API_KEY`.
4. `REMNAWAVE_API_URL = https://panel.hamalivpn.com`.

> Ноды пока не добавляем — панель работает и пустая, бот к ней цепляется. Боевые ноды (Cherry/AlexHost) добавим в Фазе 6, и они автоматически попадут в подписки.

---

## 3. Бот Bedolaga

```bash
cd /opt
git clone https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot
cp .env.example .env
```

Перенести значения из нашего шаблона [`bot/.env.hamali.example`](../bot/.env.hamali.example) в `.env`. Сгенерировать секреты:

```bash
openssl rand -hex 32   # для WEBHOOK_SECRET_TOKEN
openssl rand -hex 32   # для WEB_API_DEFAULT_TOKEN
openssl rand -hex 16   # для POSTGRES_PASSWORD
```

Запуск:

```bash
docker compose up -d
docker compose ps
docker compose logs -f bot
```

Сервисы: `bot` (Python, :8080), `postgres` (:5432), `redis` (:6379) в сети `bot_network`.

### Reverse proxy (HTTPS для вебхука)

Caddy перед ботом — маршрут `bot.hamalivpn.com` → `remnawave_bot:8080`. Caddyfile:

```
bot.hamalivpn.com {
    reverse_proxy remnawave_bot:8080
}
```

(Caddy подключить в ту же docker-сеть; SSL он берёт сам через Let's Encrypt.)

---

## 4. Тарифы и лимиты (веб-админка)

Открыть вебадминку Bedolaga → задать тарифы под наш лендинг:

| Тариф | Период | Цена | Устройства |
|---|---|---|---|
| Старт | 30 дн | 349 ₽ | 2 |
| 3 месяца | 90 дн | 899 ₽ | 3 |
| Год | 365 дн | 2 990 ₽ | 5 |

Триал: 3 дня, 20 ГБ, 1 устройство (`TRIAL_*` в env уже выставлены).

---

## 5. Реферальная программа (100 ₽)

В env уже: `REFERRAL_INVITER_BONUS_KOPEKS=10000` → пригласивший получает **100 ₽** за клиента, вывод от 300 ₽. Проверить тумблер в админке.

---

## 6. Платежи

**Telegram Stars** — `TELEGRAM_STARS_ENABLED=true`. Без ИП, юзер платит картой через Telegram, вывод средств — через Fragment/TON.

**Platega (СБП/QR/карты)** — зарегистрироваться в Platega, получить `MERCHANT_ID` + `SECRET`, вписать в `.env`, указать webhook на `https://bot.hamalivpn.com/...` (URL колбэка — из доков Platega/Bedolaga). Перезапустить: `docker compose up -d bot`.

---

## 7. Тест end-to-end

1. Зайти в бота → выбрать тариф → оплатить (Stars — реальный мелкий платёж, или тестовый режим Platega).
2. Проверить: в Remnawave появился пользователь, бот выдал ссылку-подписку + QR.
3. Импортировать в v2rayTun/Hiddify по кнопке автоподключения.
4. Проверить уведомление об окончании и автопродление.

---

## 8. Связать с лендингом

1. В [`landing/index.html`](../landing/index.html) заменить все `https://t.me/HamaliVpnBot` на реальный `@username`.
2. Задеплоить лендинг на Cloudflare Pages / Vercel, привязать `hamalivpn.com`.

---

## 9. Эксплуатация

- Логи: `docker compose logs -f bot`
- Бэкап БД: `docker compose exec postgres pg_dump -U bedolaga bedolaga > backup.sql`
- Обновление бота: `git pull && docker compose up -d --build`
- Мониторинг управляющего VPS + аптайм-бот в Telegram.

---

## Что мне (ассистенту) прислать, чтобы продолжить

- `@username` бота — **впишу во все кнопки лендинга** (не секрет).
- Твой `ADMIN_IDS` (числовой Telegram ID) — не секрет.
- IP/домен управляющего VPS, когда поднимешь.
- `BOT_TOKEN`, ключи Platega — **держи в `.env` на сервере, в чат не вставляй.**
