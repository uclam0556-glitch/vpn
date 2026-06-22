# HamaliVpn — production deployment

## Состав

OVH VPS-1:

- Remnawave Panel + собственная PostgreSQL/Valkey;
- HamaliVpn Control API и dashboard;
- HamaliVpn Telegram Bot;
- HamaliVpn PostgreSQL;
- Redis;
- maintenance worker;
- Caddy с автоматическим TLS.

VPN-ноды устанавливаются отдельно и подключаются к Remnawave.

## Временные адреса без покупки домена

Для IP `203.0.113.10`:

- Control: `https://203.0.113.10.sslip.io`;
- Dashboard: `https://203.0.113.10.sslip.io/admin`;
- Panel: `https://panel.203.0.113.10.sslip.io`;

`sslip.io` резолвит hostname в указанный IP, а Caddy получает TLS-сертификаты.

## 1. Безопасная подготовка VPS

На Mac:

```bash
scp -i ~/.ssh/hamalivpn_control \
  ~/.ssh/hamalivpn_control.pub \
  infra/bootstrap-control.sh \
  root@SERVER_IP:/root/

ssh root@SERVER_IP 'bash /root/bootstrap-control.sh'
```

Проверить новый доступ:

```bash
ssh -i ~/.ssh/hamalivpn_control hamaliadmin@SERVER_IP
```

После успешной проверки отключить парольный/root-вход:

```bash
scp -i ~/.ssh/hamalivpn_control \
  infra/harden-ssh.sh \
  hamaliadmin@SERVER_IP:/tmp/

ssh -i ~/.ssh/hamalivpn_control hamaliadmin@SERVER_IP \
  'sudo bash /tmp/harden-ssh.sh'
```

## 2. Загрузить код

```bash
sudo install -d -o hamaliadmin -g hamaliadmin /opt/hamalivpn
git clone https://github.com/uclam0556-glitch/vpn.git /opt/hamalivpn
cd /opt/hamalivpn
```

## 3. Remnawave

```bash
sudo bash infra/install-remnawave.sh SERVER_IP
```

Скрипт использует официальный `docker-compose-prod.yml`, генерирует секреты и
настраивает `panel.SERVER_IP.sslip.io`.

## 4. HamaliVpn Control

```bash
cp .env.example .env
nano .env
```

Заполнить на сервере:

- `BOT_TOKEN`;
- `ADMIN_TELEGRAM_IDS`;
- `ADMIN_PASSWORD`;
- `POSTGRES_PASSWORD`;
- `SESSION_SECRET`.

Первый запуск:

```bash
sudo bash infra/deploy-hamalivpn.sh SERVER_IP
```

На первом этапе `REMNAWAVE_MOCK=true`: можно проверить dashboard и поведение
бота, но импортированная подписка не содержит VPN-ноды.

## 5. Связать с Remnawave

1. Открыть `https://panel.SERVER_IP.sslip.io` — создать первого администратора.
2. Settings → API Tokens → создать токен для `HamaliVpn Control`.
3. Запустить скрипт привязки (токен запрашивается без отображения, проверяется
   реальными запросами и записывается в `.env` автоматически):

```bash
bash infra/verify-remnawave-token.sh
```

4. Squad можно добавить сразу или позже (скрипт поддерживает повторный запуск).
   Remnawave Panel → Nodes → Squads → Create Squad `CHECHNYA-LAB` → скопировать UUID.
   При повторном запуске `verify-remnawave-token.sh` ввести тот же токен и UUID.

5. Скрипт сам перезапустит `control`, `bot`, `maintenance`.

## 6. Проверка

```bash
docker compose ps
docker compose logs --tail=100 control
docker compose logs --tail=100 bot
curl -fsS https://SERVER_IP.sslip.io/health
```

В Telegram:

1. `/start`;
2. «Получить тест на 90 минут»;
3. «Подключить устройство»;
4. импорт в Hiddify/v2rayTun;
5. проверить пользователя и лимиты в Remnawave.

## Резервное копирование

Минимум ежедневно:

```bash
docker compose exec -T postgres \
  pg_dump -U hamalivpn hamalivpn \
  | gzip > /opt/hamalivpn/backups/hamalivpn-$(date +%F).sql.gz

docker exec remnawave-db \
  pg_dump -U postgres postgres \
  | gzip > /opt/hamalivpn/backups/remnawave-$(date +%F).sql.gz
```

Копии необходимо отправлять offsite; резерв на том же VPS не защищает от
потери сервера.

## 7. Тестовые VPN-ноды

Для добавления VPN-нод (тест на Gcore на 1 час) — смотрите полный runbook:

```
docs/NODE_TEST_PLAN.md
```

Ключевые скрипты:
- `infra/install-node.sh` — установка ноды на любой Ubuntu VPS одной командой;
- `infra/remove-node.sh` — чистое удаление ноды с чеклистом по Gcore.
