from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _parse_device_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _device_updated_key(device: dict[str, Any]) -> tuple[datetime, datetime]:
    return (
        _parse_device_time(device.get("updatedAt") or device.get("updated_at")),
        _parse_device_time(device.get("createdAt") or device.get("created_at")),
    )


def _device_created_key(device: dict[str, Any]) -> tuple[datetime, datetime]:
    return (
        _parse_device_time(device.get("createdAt") or device.get("created_at")),
        _parse_device_time(device.get("updatedAt") or device.get("updated_at")),
    )


def _device_hwid(device: dict[str, Any]) -> str:
    return str(
        device.get("hwid") or device.get("HWID") or device.get("deviceId") or device.get("id") or ""
    )


async def prune_hwid_devices_to_limit(
    *,
    user_uuid: str,
    device_limit: int,
    list_devices: Callable[[str], Awaitable[list[dict[str, Any]]]],
    delete_device: Callable[[str, str], Awaitable[None]],
    keep: str = "oldest",
) -> dict[str, int]:
    """Remove extra Remnawave HWID slots when a subscription limit is exceeded.

    The default policy is strict commercial licensing: the first activated
    devices keep their slots and later devices are removed. This makes a
    "1 device" tariff behave as a real device lock instead of "who connected
    last wins". For admin-driven migrations, callers may pass keep="newest".
    """

    limit = max(1, int(device_limit or 1))
    try:
        devices = list(await list_devices(user_uuid) or [])
    except Exception:
        logger.exception("Could not list Remnawave HWID devices for pruning")
        return {"before_count": 0, "removed_count": 0, "kept_count": 0}

    before_count = len(devices)
    if before_count <= limit:
        return {"before_count": before_count, "removed_count": 0, "kept_count": before_count}

    if keep == "newest":
        devices.sort(key=_device_updated_key, reverse=True)
    else:
        devices.sort(key=_device_created_key)
    stale_devices = devices[limit:]

    removed_count = 0
    for device in stale_devices:
        hwid = _device_hwid(device)
        if not hwid:
            continue
        try:
            await delete_device(user_uuid, hwid)
            removed_count += 1
        except Exception:
            logger.exception("Could not delete stale Remnawave HWID device")

    return {
        "before_count": before_count,
        "removed_count": removed_count,
        "kept_count": min(before_count, limit),
    }
