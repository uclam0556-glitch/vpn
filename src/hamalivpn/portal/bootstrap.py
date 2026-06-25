"""Operational bootstrap for the portal.

Usage (run on the control host, inside the control container):

    python -m hamalivpn.portal.bootstrap create-admin "Owner key"
    python -m hamalivpn.portal.bootstrap seed-tariffs

`create-admin` prints a one-time admin secret key — store it immediately, only
its hash is kept. `seed-tariffs` inserts the default reseller tariffs from the
spec (idempotent: existing codes are skipped).
"""

import asyncio
import sys

from sqlalchemy import select

from ..db import SessionFactory, create_schema
from .auth import generate_secret_key, hash_secret_key, key_prefix
from .models import SecretKey, SecretKeyRole, Tariff

DEFAULT_TARIFFS = [
    # code, name, days, price_rub, devices, traffic_gb, sort
    ("start_1m", "Start · 1 месяц", 30, 100, 1, 0, 10),
    ("family_1m", "Family · 1 месяц", 30, 180, 3, 0, 20),
    ("plan_3m", "3 месяца", 90, 270, 1, 0, 30),
    ("plan_6m", "6 месяцев", 180, 500, 1, 0, 40),
]


async def create_admin(label: str) -> None:
    await create_schema()
    token = generate_secret_key()
    async with SessionFactory() as session:
        session.add(
            SecretKey(
                role=SecretKeyRole.admin,
                reseller_id=None,
                key_prefix=key_prefix(token),
                key_hash=hash_secret_key(token),
                label=label or "admin",
            )
        )
        await session.commit()
    print("\n=== ADMIN SECRET KEY (показывается один раз) ===")
    print(token)
    print("=== сохраните его и больше нигде не светите ===\n")


async def seed_tariffs() -> None:
    await create_schema()
    created = 0
    async with SessionFactory() as session:
        for code, name, days, price_rub, devices, traffic, sort in DEFAULT_TARIFFS:
            exists = await session.scalar(select(Tariff).where(Tariff.code == code))
            if exists is not None:
                continue
            session.add(
                Tariff(
                    code=code,
                    name=name,
                    duration_days=days,
                    price_kopecks=int(price_rub * 100),
                    device_limit=devices,
                    traffic_limit_gb=traffic,
                    sort_order=sort,
                )
            )
            created += 1
        await session.commit()
    print(f"Создано тарифов: {created} (существующие пропущены)")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    command = args[0]
    if command == "create-admin":
        asyncio.run(create_admin(args[1] if len(args) > 1 else "admin"))
    elif command == "seed-tariffs":
        asyncio.run(seed_tariffs())
    else:
        print(f"Неизвестная команда: {command}")
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
