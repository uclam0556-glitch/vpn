# HamaliVpn — Runbook: Часовой тест VPN-ноды на Gcore

> **Цель**: Арендовать VM на Gcore, за 10 минут поднять ноду,
> провести тест через Telegram-бот, зафиксировать результаты
> и **полностью удалить все ресурсы** без скрытых списаний.

---

## Перед первым тестом (один раз)

### Убедитесь, что управляющий сервер работает

```bash
# С вашего Mac:
ssh -i ~/.ssh/hamalivpn_control hamaliadmin@OVH_IP

# На OVH-сервере:
cd /opt/hamalivpn
docker compose ps
curl -fsS https://OVH_IP.sslip.io/health
```

Все контейнеры должны иметь статус `running`. Если `REMNAWAVE_MOCK=true` — сначала выполните привязку токена:

```bash
bash infra/verify-remnawave-token.sh
```

### Убедитесь, что в Remnawave создан Squad

1. Открыть `https://panel.OVH_IP.sslip.io`
2. **Nodes → Squads → Create Squad**
3. Имя: `CHECHNYA-LAB`
4. Скопировать UUID squad
5. Вставить UUID в `.env` (`REMNAWAVE_INTERNAL_SQUADS=UUID`) и перезапустить:

```bash
docker compose up -d control bot maintenance
```

---

## Сценарий часового теста

### 00:00 — Создание VPS на Gcore

1. Перейдите на **gcore.com** → авторизуйтесь
2. **Cloud → Instances → Create Instance**
3. Параметры:

| Поле | Значение |
|---|---|
| **Регион** | Frankfurt (для первого теста) |
| **Тариф** | Basic · 2 vCPU · 4 GB RAM |
| **Диск** | 30 GB SSD (системный) |
| **ОС** | Ubuntu 24.04 LTS x86_64 |
| **Сеть** | Публичный IPv4 |
| **SSH-ключ** | Загрузите свой публичный ключ |
| **Биллинг** | **PAYG (per-minute)** — важно! |

> [!CAUTION]
> При создании Gcore предложит создать Floating IP (плавающий IP).
> Если не планируете постоянное использование — **откажитесь** или сразу освободите после теста.
> Floating IP списывается даже когда VM удалена.

4. Нажмите **Create** → дождитесь IP адреса (обычно 1–2 минуты)
5. Запишите IP ноды: `NODE_IP=___________`

---

### 00:05 — Установка ноды (одна команда)

**В Remnawave Panel создайте новую ноду:**

1. `https://panel.OVH_IP.sslip.io` → **Nodes → Add Node**
2. Имя: `Gcore-Frankfurt-Test`
3. Address: `NODE_IP` (IP вашей Gcore VM)
4. Скопируйте **Node Token** (секретный токен для подключения)

**На Gcore VM выполните:**

```bash
ssh root@NODE_IP

bash <(curl -fsSL https://raw.githubusercontent.com/uclam0556-glitch/vpn/main/infra/install-node.sh) \
  --panel-url https://panel.OVH_IP.sslip.io \
  --node-token ВАШ_NODE_TOKEN
```

Скрипт сам:
- установит Docker (если нет)
- настроит файрвол (SSH + 443/tcp + 443/udp + 2095)
- запустит `remnawave/node:latest`

---

### 00:10 — Подтверждение подключения ноды

В Remnawave Panel → **Nodes** — статус ноды должен стать **Online** 🟢.

Если нода не появилась в течение 2 минут:

```bash
# На Gcore VM:
cd /opt/remnawave-node
docker compose logs -f
```

Затем настройте Inbounds:

1. Nodes → нажмите на ноду → **Configure Inbounds**
2. Добавьте **VLESS + Reality** (порт 443)
3. Добавьте **Hysteria2** (порт 443 UDP)
4. Сохраните → убедитесь, что статус **Active**

---

### 00:15 — Тест через Telegram-бот

1. Откройте Telegram → `@HamaliVpn_bot`
2. `/start` → **«Получить тест на 90 минут»**
3. **«Подключить устройство»** — откроется страница подключения
4. Импортируйте ссылку в **Hiddify** или **v2rayTun**

> [!IMPORTANT]
> Если подписка выдаётся, но в приложении нет серверов — значит Squad не привязан к ноде.
> Remnawave Panel → Nodes → ваша нода → Squads → добавьте `CHECHNYA-LAB`.

---

### 00:15 – 00:45 — Замеры (фиксируем в таблицу ниже)

Тестируем с реального телефона (МТС / Мегафон / Билайн / Tele2 Чечня или симки):

| Метрика | VLESS Reality | Hysteria2 |
|---|---|---|
| Ping до IP (мс) | | |
| Задержка в приложении (мс) | | |
| Download (Мбит/с) | | |
| Upload (Мбит/с) | | |
| Packet loss (%) | | |
| Jitter (мс) | | |
| YouTube 4K | ✓ / ✗ | ✓ / ✗ |
| Telegram видеозвонок | ✓ / ✗ | ✓ / ✗ |
| Wi-Fi ↔ LTE переключение | стабильно / рвётся | стабильно / рвётся |

**Итог**: `reject` / `retest` / `production candidate`

---

### 00:55 — Удаление ноды с Gcore VM

**Шаг 1** — удалить ноду в Remnawave Panel:

- Nodes → ваша нода → **Delete**

**Шаг 2** — удалить контейнер на VM:

```bash
ssh root@NODE_IP
bash <(curl -fsSL https://raw.githubusercontent.com/uclam0556-glitch/vpn/main/infra/remove-node.sh)
```

---

### 01:00 — Удаление VM и ресурсов в Gcore

> [!CAUTION]
> **Это самый важный шаг.** Gcore не удаляет диски и IP автоматически.
> Невыполнение = скрытые списания!

Чеклист Gcore (выполнять в этом порядке):

- [ ] **Cloud → Instances** → найдите VM → нажмите **Delete** → подтвердите
- [ ] **Cloud → Volumes** → убедитесь, что диск (30 GB) тоже исчез (или удалите вручную)
- [ ] **Cloud → Networking → Floating IPs** → освободите все IP с этого сервера
- [ ] **Cloud → Networking → Reserved IPs** → убедитесь, что список пуст
- [ ] **Billing → Usage** → проверьте, что ресурсы больше не фигурируют

---

## Раунд 2 — Istanbul (если Frankfurt прошёл)

Повторите весь сценарий, изменив только регион:

| Поле | Значение |
|---|---|
| Регион | Istanbul |
| Остальное | то же самое |

Сравните таблицы замеров. Побеждает минимальный `latency + jitter + packet_loss`.

---

## Команды для быстрого мониторинга во время теста

```bash
# На Gcore VM — смотреть трафик в реальном времени
docker stats remnawave-node

# Логи ноды
cd /opt/remnawave-node && docker compose logs -f --tail=50

# Проверить, что нода отвечает
curl -s http://localhost:2095/health
```

---

## Стоимость теста

| Ресурс | Gcore PAYG | Час теста |
|---|---|---|
| Basic VM 2vCPU/4GB | ~$0.02–0.04/час | < $0.05 |
| Floating IP (если взяли) | ~$0.005/час | < $0.01 |
| Исходящий трафик | первые GB бесплатно | — |
| **Итого** | | **< $0.10 за тест** |

> [!TIP]
> Если передумали тестировать после создания VM — удалите её **немедленно**.
> Gcore считает по-минутам, но диски и IP тарифицируются, пока не удалены.
