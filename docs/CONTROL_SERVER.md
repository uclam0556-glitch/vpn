# HamaliVpn — заказ управляющего сервера

## Конфигурация

- Provider/product: OVHcloud VPS-1
- Location: Europe
- CPU: 2 vCPU
- RAM: 4 GB
- Storage: 40 GB SSD
- Architecture: x86_64
- OS: Ubuntu 24.04 LTS
- IPv4: 1
- Billing: monthly
- Included: daily backup of the previous 24 hours
- Reference price on June 22, 2026: approximately $4.54/month before tax

Сначала оплачивается один месяц. После проверки стабильности можно выбрать более длинный период, если он даёт реальную скидку.

## Почему 2 vCPU / 4 GB достаточно на старте

Remnawave указывает 2 CPU / 2 GB как минимум и 4 CPU / 4 GB как рекомендуемую конфигурацию. На старте сервер используется только как control-plane:

- HamaliVpn Control API и Telegram-бот;
- веб-кабинет и API;
- PostgreSQL Remnawave;
- PostgreSQL HamaliVpn Control;
- Redis;
- reverse proxy;
- мониторинг и резервное копирование.

VPN-трафик через этот сервер не проходит. Для первых 50–100 клиентов 2 vCPU / 4 GB достаточно при ограничении памяти контейнеров, аккуратной настройке PostgreSQL/Redis и 2 GB swap как страховке от кратких пиков. Если среднее использование RAM превышает 75% или CPU остаётся выше 70%, тариф увеличивается до 4 vCPU / 8 GB.

## Перед созданием

1. Включить двухфакторную аутентификацию в OVHcloud.
2. Сохранить recovery codes в менеджер паролей.
3. Не добавлять root-пароль или приватный SSH-ключ в GitHub и Telegram.
4. Если кабинет предлагает резервное копирование/снимки, сначала оставить ручные snapshots; автоматические offsite-бэкапы настроим отдельно.

## После создания

Нужны:

- публичный IPv4;
- root-доступ только для первичной настройки;
- приватный SSH-ключ на компьютере владельца;
- подтверждение, что ОС — Ubuntu 24.04 x86_64.

Первый запуск выполняется скриптом:

```bash
scp -i ~/.ssh/hamalivpn_control \
  ~/.ssh/hamalivpn_control.pub \
  infra/bootstrap-control.sh \
  root@SERVER_IP:/root/

ssh root@SERVER_IP 'bash /root/bootstrap-control.sh'
```

Если OVH выдаёт пользователя `ubuntu`, используйте `ubuntu@SERVER_IP`, загрузите файлы в `/tmp`, затем выполните bootstrap через `sudo`.

После bootstrap проверить отдельным терминалом:

```bash
ssh -i ~/.ssh/hamalivpn_control hamaliadmin@SERVER_IP
```

Только после успешного входа:

```bash
scp -i ~/.ssh/hamalivpn_control infra/harden-ssh.sh hamaliadmin@SERVER_IP:/tmp/
ssh -i ~/.ssh/hamalivpn_control hamaliadmin@SERVER_IP \
  'sudo bash /tmp/harden-ssh.sh'
```
