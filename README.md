# HamaliVpn

Премиальный VPN-сервис под Кавказ и Россию: лендинг, Telegram-бот (Bedolaga) и автоматизация на панели Remnawave.

## Структура

- `landing/` — лендинг (статика; деплой на Cloudflare Pages, корневая папка для Pages: `landing`)
- `bot/` — конфиг Bedolaga (`.env.hamali.example`), тарифы (`tariffs.hamali.json`), брендированные тексты
- `docs/` — `DEPLOY.md` (пошаговый деплой) и `BOT_BEHAVIOR.md` (спецификация поведения бота)
- `PLAN.md` — карта проекта и деплоя

## Деплой

- **Лендинг** → Cloudflare Pages, root directory: `landing`.
- **Бот + панель + БД** → один control-VPS (Oracle Always Free), Docker. См. `docs/DEPLOY.md`.
- **VPN-ноды** → отдельные чистые VPS (подключаются к Remnawave позже).

> Секреты (BOT_TOKEN, ключи платёжек, пароли) хранятся только в `.env` на сервере и в репозиторий не попадают.
