# HamaliVpn — поведение Telegram-бота

## Первый тест

1. `/start` показывает краткое описание и три действия:
   - получить тест на 90 минут;
   - открыть текущую подписку;
   - прочитать инструкцию.
2. Тест можно получить один раз на Telegram ID.
3. HamaliVpn Control создаёт пользователя в Remnawave:
   - `telegramId`;
   - срок 90 минут;
   - 30 ГБ;
   - `hwidDeviceLimit=1`;
   - squad `CHECHNYA-LAB`.
4. Remnawave возвращает `subscriptionUrl`.
5. Бот выдаёт приватную HamaliVpn connect page:
   - Hiddify deeplink;
   - v2rayTun deeplink;
   - QR;
   - ручное копирование ссылки;
   - срок, трафик и лимит устройств.
6. Maintenance worker раз в минуту ищет истёкшие подписки и отключает
   пользователя в Remnawave.

## Защита

- access token страницы подключения генерируется через `secrets.token_urlsafe(32)`;
- повторный триал блокируется в PostgreSQL;
- панель использует HTTP-only SameSite cookie и CSRF;
- пароли, токен Telegram и Remnawave API token находятся только в `.env`;
- PostgreSQL и Redis не публикуются наружу;
- действия администратора и системы пишутся в `audit_logs`.

## После проверки нод

Добавляются:

- платные тарифы;
- Telegram Stars/эквайринг;
- продление и напоминания;
- реферальный баланс;
- промокоды;
- поддержка дополнительных устройств.
