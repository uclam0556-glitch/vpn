# HamaliVPN operations platform

## Portal sessions and Redis protection

The portal exchanges a long-lived access key for a four-hour opaque session.
The session identifier is kept only in an `HttpOnly`, `SameSite=Strict` cookie;
Redis stores the server-side session and shared login counters.

Existing browsers are migrated automatically from the old `localStorage` key.
Bearer-key access remains available for one compatibility release and can then
be disabled:

```env
PORTAL_SESSION_TTL_SECONDS=14400
PORTAL_AUTH_ATTEMPTS=10
PORTAL_AUTH_WINDOW_SECONDS=300
PORTAL_LEGACY_BEARER_ENABLED=false
```

Rotating or blocking a reseller/subadmin invalidates that account's active
sessions. A Redis outage falls back to bounded process memory so the portal
does not fail open or become unavailable.

## Feature flags and canary

The owner portal has a **Релизы** tab. Both production flags start disabled at
0%:

- `subscription_output_v2` — subscription rendering changes;
- `integration_sync_v2` — imported subscription synchronization changes.

Rollout assignment is deterministic from `SHA-256(flag + subject)`, so a user
does not jump between control and canary on every request. The internal API is:

```text
GET /api/internal/features/{flag}?subject={stable-user-or-subscription-id}
```

Caddy blocks `/api/internal/*` from the public internet. New subscription or
import implementations must check this flag at their entry point and preserve
the current branch as the control path. Recommended rollout: forced test IDs,
then 1%, 5%, 20%, 50%, 100%, with error/latency comparison at every stage.

## Encrypted offsite backups

Backups never write plain SQL to disk. `pg_dump` is piped through gzip and
`age`, uploaded with rclone and verified by checksum.

Install `age` and `rclone`, configure an S3/B2/R2 remote, then create root-only
`/etc/hamalivpn/operations.env`:

```env
BACKUP_AGE_RECIPIENT=age1publicrecipient...
BACKUP_RCLONE_REMOTE=hamali-offsite:encrypted-backups
HAMALI_BACKUP_KEEP_DAYS=14
```

Keep the corresponding age identity **off the production server**. Enable the
daily timer on the control server:

```bash
sudo install -m 0644 infra/systemd/hamali-offsite-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hamali-offsite-backup.timer
sudo systemctl start hamali-offsite-backup.service
```

The restore drill belongs on a separate trusted restore host. Its root-only
configuration additionally contains:

```env
BACKUP_AGE_IDENTITY_FILE=/etc/hamalivpn/backup-age-identity.txt
BACKUP_SOURCE_HOST=production-hostname
```

Install and enable `hamali-restore-drill.timer` there. It downloads the newest
pair, decrypts into pipes, restores both databases into disposable PostgreSQL
containers and checks that the restored schemas contain tables.

## External uptime monitoring

`infra/external-uptime-monitor.py` must run outside the production VPS. It
checks public health endpoints, keeps a small state file and sends Telegram
messages only on a changed failure set and on recovery.

On the external host create `/etc/hamalivpn/uptime.env`:

```env
HAMALI_UPTIME_ENDPOINTS=https://portal.hamali.ru/api/health,https://app.hamali.ru/api/health
HAMALI_TECH_BOT_TOKEN=telegram-bot-token
HAMALI_TECH_CHAT_ID=technical-chat-id
HAMALI_UPTIME_TIMEOUT=10
```

Create an unprivileged `hamali-uptime` user, copy the script to
`/opt/hamali-uptime/`, install the external service/timer files and enable the
timer. Do not deploy this timer on the HamaliVPN control VPS: a monitor on the
same host cannot detect a host or routing outage.
