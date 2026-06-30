import pytest

from hamalivpn.device_limits import prune_hwid_devices_to_limit


@pytest.mark.asyncio
async def test_prune_hwid_devices_keeps_newest_slots() -> None:
    deleted: list[str] = []
    devices = [
        {"hwid": "old", "updatedAt": "2026-06-01T10:00:00.000Z"},
        {"hwid": "new", "updatedAt": "2026-06-03T10:00:00.000Z"},
        {"hwid": "middle", "updatedAt": "2026-06-02T10:00:00.000Z"},
    ]

    async def list_devices(_: str) -> list[dict]:
        return devices

    async def delete_device(_: str, hwid: str) -> None:
        deleted.append(hwid)

    result = await prune_hwid_devices_to_limit(
        user_uuid="user-uuid",
        device_limit=2,
        list_devices=list_devices,
        delete_device=delete_device,
    )

    assert result == {"before_count": 3, "removed_count": 1, "kept_count": 2}
    assert deleted == ["old"]


@pytest.mark.asyncio
async def test_prune_hwid_devices_noops_when_under_limit() -> None:
    deleted: list[str] = []

    async def list_devices(_: str) -> list[dict]:
        return [{"hwid": "only", "updatedAt": "2026-06-01T10:00:00.000Z"}]

    async def delete_device(_: str, hwid: str) -> None:
        deleted.append(hwid)

    result = await prune_hwid_devices_to_limit(
        user_uuid="user-uuid",
        device_limit=2,
        list_devices=list_devices,
        delete_device=delete_device,
    )

    assert result == {"before_count": 1, "removed_count": 0, "kept_count": 1}
    assert deleted == []
