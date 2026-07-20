import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeatureFlag


@dataclass(frozen=True)
class FlagDefinition:
    key: str
    description: str


FLAG_DEFINITIONS = (
    FlagDefinition(
        "subscription_output_v2",
        "Новый формат выдачи подписок Happ/Incy для тестовой группы",
    ),
    FlagDefinition(
        "integration_sync_v2",
        "Новая синхронизация и дедупликация интегрированных подписок",
    ),
)


def rollout_bucket(flag_key: str, subject: str) -> int:
    """Stable bucket in [0, 99], independent of process and Python hash seed."""
    digest = hashlib.sha256(f"{flag_key}:{subject}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def flag_is_active(flag: FeatureFlag | None, subject: str) -> bool:
    if not flag or not flag.enabled:
        return False
    config = flag.config or {}
    forced = {str(item) for item in config.get("allow_subjects", [])}
    if subject and subject in forced:
        return True
    rollout_percent = int(flag.rollout_percent or 0)
    return bool(subject) and rollout_percent > 0 and rollout_bucket(flag.key, subject) < rollout_percent


async def feature_flag_rows(db: AsyncSession) -> list[dict[str, Any]]:
    existing = {
        row.key: row
        for row in (await db.execute(select(FeatureFlag))).scalars().all()
    }
    result: list[dict[str, Any]] = []
    for definition in FLAG_DEFINITIONS:
        row = existing.get(definition.key)
        result.append(
            {
                "key": definition.key,
                "description": definition.description,
                "enabled": bool(row.enabled) if row else False,
                "rollout_percent": int(row.rollout_percent) if row else 0,
                "config": dict(row.config or {}) if row else {},
                "updated_by": row.updated_by if row else None,
                "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            }
        )
    return result


async def feature_enabled(db: AsyncSession, key: str, subject: str) -> bool:
    row = await db.get(FeatureFlag, key)
    return flag_is_active(row, subject)
