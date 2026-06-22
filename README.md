# HamaliVpn

Собственный control-plane VPN-сервиса:

- Telegram-бот `@HamaliVpn_bot`;
- автоматическая выдача тестовых подписок;
- интеграция с официальным Remnawave API;
- лимиты HWID-устройств, срока и трафика;
- защищённый admin dashboard;
- страница подключения с QR и deeplink для Hiddify/v2rayTun;
- PostgreSQL, Redis, Caddy и автоматическое отключение истёкших подписок;
- WebGL-лендинг на Cloudflare.

## Структура

- `src/hamalivpn/` — backend, Telegram-бот, Remnawave adapter и UI;
- `tests/` — тесты выдачи доступа, лимитов, deeplink и API-контракта;
- `compose.yaml` — PostgreSQL, Redis, backend, bot, maintenance и Caddy;
- `infra/` — bootstrap, hardening и установка Remnawave;
- `landing/` — публичный лендинг;
- `docs/` — эксплуатационная документация.

## Локальная проверка

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
mkdir -p data
.venv/bin/pytest -q
.venv/bin/ruff check src tests

REMNAWAVE_MOCK=true \
ADMIN_PASSWORD=test-password \
SESSION_SECRET=test-session \
.venv/bin/uvicorn hamalivpn.app:app --host 127.0.0.1 --port 8080
```

Открыть:

- `http://127.0.0.1:8080/admin/login`;
- `http://127.0.0.1:8080/health`.

## Сервер

Порядок развёртывания на OVH:

1. `infra/bootstrap-control.sh`;
2. `infra/harden-ssh.sh`;
3. `infra/install-remnawave.sh SERVER_IP`;
4. заполнить `.env`;
5. `infra/deploy-hamalivpn.sh SERVER_IP`;
6. создать API token и squad в Remnawave;
7. переключить `REMNAWAVE_MOCK=false`;
8. запустить Telegram worker.

Секреты хранятся только в `.env` на сервере.
